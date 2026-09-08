#!/usr/bin/env python3
"""Reconstruct a coloured 3D point cloud from a 360 (equirectangular) video.

This is the reference implementation of the pipeline that also runs in the
browser (web/src/pipeline/*). Use it for long clips, high resolutions, or
batch processing, where a desktop CPU beats a WebGL context.

Stages
  1. decode equirectangular keyframes                       (ffmpeg)
  2. rotation between keyframes                             (band SAD, yaw+pitch)
  3. translation direction on the sphere                    (sparse flow -> epipolar null space)
  4. relative baselines                                     (triangulated depth ratios)
  5. dense depth per keyframe                               (spherical plane sweep)
  6. filtering and voxel fusion                             (confidence, texture, consistency)
  7. metric scale and levelling                             (floor plane + known camera height)

    python3 tools/reconstruct.py --video samples/walkthrough_360.webm \
        --out samples/walkthrough_cloud.ply

Scale note: a single moving camera recovers geometry only up to one global
scale factor. It is fixed here by the height of the camera above the floor
(--camera-height), which for a hand-held or helmet-mounted 360 camera is the
one dimension the operator reliably knows.
"""

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from spherical import (  # noqa: E402
    directions_to_uv,
    equirect_directions,
    estimate_yaw_pitch,
    estimate_floor,
    high_pass,
    pose_residuals,
    refine_pose,
    sample_bilinear,
    to_gray,
    track_points,
    triangulate_depths,
    warp_equirect,
    yaw_matrix,
)


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise SystemExit("ffmpeg not found. Install ffmpeg or `pip install imageio-ffmpeg`.")


def probe_size(ffmpeg, path):
    out = subprocess.run(
        [ffmpeg, "-i", path], capture_output=True, text=True
    ).stderr
    for line in out.splitlines():
        if "Video:" in line:
            for token in line.split(","):
                token = token.strip().split(" ")[0]
                if "x" in token:
                    try:
                        w, h = token.split("x")
                        return int(w), int(h)
                    except ValueError:
                        continue
    raise SystemExit(f"could not read video dimensions from {path}")


def decode_keyframes(ffmpeg, path, width, stride, start=None, duration=None):
    """Decode every `stride`-th frame, scaled to `width` x `width/2`."""
    height = width // 2
    cmd = [ffmpeg, "-loglevel", "error"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", path]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += [
        "-vf", f"select='not(mod(n\\,{stride}))',scale={width}:{height}",
        "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    frame_bytes = width * height * 3
    frames = []
    while True:
        buf = proc.stdout.read(frame_bytes)
        if len(buf) < frame_bytes:
            break
        frames.append(
            np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3).astype(np.float32) / 255.0
        )
    proc.stdout.close()
    proc.wait()
    if not frames:
        raise SystemExit("no frames decoded; check the input video")
    return frames


def resize(img, width):
    """Area-average resize to `width` x `width/2` (equirect keeps 2:1)."""
    height = width // 2
    h, w = img.shape[:2]
    if (w, h) == (width, height):
        return img
    fy, fx = h / height, w / width
    if fx >= 1 and abs(fx - round(fx)) < 1e-6 and abs(fy - round(fy)) < 1e-6:
        fx, fy = int(round(fx)), int(round(fy))
        if img.ndim == 3:
            return img.reshape(height, fy, width, fx, img.shape[2]).mean(axis=(1, 3))
        return img.reshape(height, fy, width, fx).mean(axis=(1, 3))
    ys = (np.arange(height) + 0.5) * fy - 0.5
    xs = (np.arange(width) + 0.5) * fx - 0.5
    xx, yy = np.meshgrid(xs, ys)
    return sample_bilinear(img, xx.astype(np.float32), yy.astype(np.float32))


def estimate_motion(frames, work_width, flow_grid=(96, 36), verbose=True):
    """Rotation, translation direction and relative baseline for each pair."""
    n = len(frames)
    grays = [to_gray(resize(f, work_width)) for f in frames]
    rot_width = min(work_width, 320)
    rot_grays = [to_gray(resize(f, rot_width)) for f in frames]
    h, w = grays[0].shape

    rel_rotations = []
    directions = []
    median_depths = []

    dirs_full = equirect_directions(w, h)
    gx, gy = flow_grid
    xs = np.linspace(0, w, gx, endpoint=False, dtype=np.float32) + w / (2 * gx)
    # Sample a band around the horizon: the poles are the selfie stick and the
    # sky, which carry little usable structure.
    ys = np.linspace(h * 0.22, h * 0.80, gy, dtype=np.float32)
    pts = np.stack(np.meshgrid(xs, ys, indexing="xy"), axis=-1).reshape(-1, 2)

    hp_frames = [high_pass(g, 5) for g in grays]

    for i in range(n - 1):
        t0 = time.time()
        dx, _ = estimate_yaw_pitch(rot_grays[i], rot_grays[i + 1])
        # A horizontal shift of dx pixels is a yaw of -dx * 2pi / width: the
        # rotation has to undo the shift to carry frame B onto frame A.
        rot_init = yaw_matrix(-(dx * w / rot_width) / w * 2.0 * np.pi)

        rot_rel, t_dir, p, q = solve_pair(
            hp_frames[i], hp_frames[i + 1], dirs_full, pts, rot_init
        )

        q_rot = q @ rot_rel.T
        depths = triangulate_depths(p, q_rot, t_dir)
        valid = np.isfinite(depths)
        if valid.sum() and np.nanmedian(depths[valid]) < 0:
            t_dir = -t_dir
            depths = -depths
        positive = valid & (depths > 0)
        med = float(np.nanmedian(depths[positive])) if positive.any() else 10.0
        if not np.isfinite(med) or med <= 0:
            med = 10.0

        rel_rotations.append(rot_rel)
        directions.append(t_dir)
        median_depths.append(med)
        if verbose:
            yaw_deg = np.degrees(np.arctan2(rot_rel[0, 2], rot_rel[2, 2]))
            print(
                f"  pair {i:3d}->{i+1:3d}  yaw {yaw_deg:+6.2f} deg  "
                f"dir [{t_dir[0]:+.2f} {t_dir[1]:+.2f} {t_dir[2]:+.2f}]  "
                f"median depth {med:5.2f} b  ({time.time()-t0:.1f}s)",
                flush=True,
            )

    return rel_rotations, np.array(directions), np.array(median_depths)


def solve_pair(ref_hp, next_hp, dirs_full, pts, rot_init, passes=2, hypotheses=(0, 6, -6, 13, -13)):
    """Relative rotation and translation direction for one keyframe pair.

    Each pass de-rotates the second frame by the current rotation estimate,
    re-tracks, and re-solves; the first pass tries several yaw hypotheses and
    keeps whichever explains the correspondences best. The coarse shift search
    occasionally locks onto repeating architecture, and a single bad pair would
    otherwise bend the whole trajectory.
    """
    rotation = np.asarray(rot_init, dtype=np.float64)
    p_out = q_out = None
    t_dir = np.array([0.0, 0.0, 1.0], dtype=np.float32)

    for step in range(passes):
        warped = warp_equirect(next_hp, rotation)
        matched, cost = track_points(ref_hp, warped, pts)
        good = np.isfinite(cost) & (cost < np.percentile(cost, 80))
        p = sample_dirs(dirs_full, pts)[good]
        # A match at warped pixel x came from raw direction R * d(x).
        q = sample_dirs(dirs_full, matched)[good] @ rotation.T
        q /= np.linalg.norm(q, axis=1, keepdims=True)

        candidates = hypotheses if step == 0 else (0,)
        best = None
        for extra_deg in candidates:
            start = rotation @ yaw_matrix(np.deg2rad(extra_deg))
            r_try, t_try, _ = refine_pose(p, q, start)
            residuals = pose_residuals(p, q, r_try, t_try)
            score = int(np.count_nonzero(residuals < 0.004))
            if best is None or score > best[0]:
                best = (score, r_try, t_try)
        _, rotation, t_dir = best
        rotation = np.asarray(rotation, dtype=np.float64)
        p_out, q_out = p, q

    return rotation.astype(np.float32), t_dir, p_out, q_out


def sample_dirs(dirs_full, pts):
    """Unit directions for sub-pixel image positions."""
    d = sample_bilinear(dirs_full, pts[:, 0], pts[:, 1])
    return d / np.linalg.norm(d, axis=1, keepdims=True)


def baselines_from_depths(median_depths, smooth=5):
    """Relative baseline per pair.

    Depth is recovered in units of that pair's baseline, so if the scene depth
    varies slowly between neighbouring keyframes, b_{i+1}/b_i = m_i/m_{i+1}.
    A constant walking pace gives constant baselines; this tracks the slow
    changes around it and is clamped so a bad pair cannot wreck the chain.
    """
    m = np.asarray(median_depths, dtype=np.float64)
    if len(m) == 0:
        return np.array([])
    k = max(1, min(smooth, len(m)))
    kernel = np.ones(k) / k
    m_s = np.convolve(np.pad(m, (k // 2, k // 2), mode="edge"), kernel, mode="valid")[: len(m)]
    b = np.ones(len(m))
    for i in range(1, len(m)):
        ratio = np.clip(m_s[i - 1] / max(m_s[i], 1e-6), 0.6, 1.6)
        b[i] = b[i - 1] * ratio
    return b / np.median(b)


def build_poses(rel_rotations, directions, baselines, lock_horizon=True):
    """Chain relative poses into world rotations and camera centres.

    Insta360 X-series footage is exported horizon locked, so the accumulated
    rotation is projected back onto its yaw component by default. Without it,
    a few tenths of a degree of tilt error per pair compounds until floors bend
    and the metric floor fit has nothing planar to lock onto.
    """
    rotations = [np.eye(3, dtype=np.float32)]
    centres = [np.zeros(3, dtype=np.float32)]
    for i, rel in enumerate(rel_rotations):
        step = rotations[-1] @ (directions[i] * baselines[i])
        centres.append(centres[-1] + step.astype(np.float32))
        nxt = rotations[-1] @ rel
        if lock_horizon:
            nxt = yaw_matrix(np.arctan2(nxt[0, 2], nxt[2, 2]))
        rotations.append(nxt.astype(np.float32))
    return np.array(centres), np.array(rotations)


def plane_sweep(
    frames_rgb,
    grays_hp,
    index,
    centres,
    rotations,
    neighbours,
    layers,
    min_depth,
    max_depth,
    agg_radius=1,
):
    """Spherical plane sweep for one keyframe.

    Hypotheses are uniform in inverse depth (constant disparity steps), each
    neighbour is sampled directly in equirectangular space, and the per-pixel
    cost is the mean of the two best neighbours so an occlusion in one view
    does not destroy the estimate.
    """
    ref_hp = grays_hp[index]
    h, w = ref_hp.shape
    dirs = equirect_directions(w, h)
    world_dirs = dirs.reshape(-1, 3) @ rotations[index].T
    world_dirs = world_dirs.reshape(h, w, 3)
    centre = centres[index]

    nb = [j for j in neighbours if 0 <= j < len(grays_hp) and j != index]
    if len(nb) < 2:
        return None

    inv_far, inv_near = 1.0 / max_depth, 1.0 / min_depth
    inv_depths = np.linspace(inv_far, inv_near, layers, dtype=np.float32)

    best = np.full((h, w), np.inf, dtype=np.float32)
    best_k = np.full((h, w), -1, dtype=np.int32)
    second = np.full((h, w), np.inf, dtype=np.float32)
    cost_prev = np.full((h, w), np.inf, dtype=np.float32)
    cost_prev2 = np.full((h, w), np.inf, dtype=np.float32)
    cost_at_best_minus1 = np.full((h, w), np.inf, dtype=np.float32)
    cost_at_best_plus1 = np.full((h, w), np.inf, dtype=np.float32)

    per_nb = np.empty((len(nb), h, w), dtype=np.float32)
    for k, inv_d in enumerate(inv_depths):
        pt = centre[None, None, :] + world_dirs * (1.0 / inv_d)
        for m, j in enumerate(nb):
            local = (pt - centres[j][None, None, :]) @ rotations[j]
            norm = np.linalg.norm(local, axis=2, keepdims=True)
            u, v = directions_to_uv(local / np.maximum(norm, 1e-9), w, h)
            per_nb[m] = np.abs(ref_hp - sample_bilinear(grays_hp[j], u, v))

        if len(nb) > 2:
            part = np.partition(per_nb, 1, axis=0)[:2]
            cost = part.mean(axis=0)
        else:
            cost = per_nb.mean(axis=0)
        cost = _box(cost, agg_radius)

        improved = cost < best
        # The runner-up has to be a competing *local* minimum. Layers adjacent
        # to the winner always cost nearly the same, so counting them would
        # drive every confidence value to zero.
        is_local_min = (cost_prev < cost_prev2) & (cost_prev < cost) & (best_k != k - 1)
        second = np.where(improved, np.minimum(second, best), second)
        second = np.where(is_local_min, np.minimum(second, cost_prev), second)

        cost_at_best_minus1 = np.where(improved, cost_prev, cost_at_best_minus1)
        cost_at_best_plus1 = np.where(best_k == k - 1, cost, cost_at_best_plus1)
        best = np.where(improved, cost, best)
        best_k = np.where(improved, k, best_k)
        cost_prev2 = cost_prev
        cost_prev = cost

    # Sub-layer refinement in inverse-depth space.
    c0, c1, c2 = cost_at_best_minus1, best, cost_at_best_plus1
    finite = np.isfinite(c0) & np.isfinite(c2)
    denom = np.where(finite, c0 - 2 * c1 + c2, 1.0)
    sub = np.where(np.abs(denom) > 1e-9, 0.5 * (c0 - c2) / denom, 0.0)
    sub = np.clip(np.where(finite, sub, 0.0), -1.0, 1.0)
    step = inv_depths[1] - inv_depths[0]
    inv_best = inv_depths[best_k] + sub * step
    depth = 1.0 / np.maximum(inv_best, 1e-6)

    confidence = np.where(best > 1e-6, (second - best) / (best + 1e-3), 0.0)
    return depth.astype(np.float32), confidence.astype(np.float32), best_k, world_dirs


def _box(img, radius):
    if radius <= 0:
        return img
    k = 2 * radius + 1
    pad = np.pad(img, radius, mode="edge")
    cs = np.cumsum(pad, axis=0, dtype=np.float32)
    cs = np.concatenate([np.zeros((1, cs.shape[1]), np.float32), cs], axis=0)
    tmp = (cs[k:, :] - cs[:-k, :]) / k
    cs = np.cumsum(tmp, axis=1, dtype=np.float32)
    cs = np.concatenate([np.zeros((cs.shape[0], 1), np.float32), cs], axis=1)
    return (cs[:, k:] - cs[:, :-k]) / k


def voxel_fuse(points, colors, voxel):
    """Average points and colours inside each voxel."""
    keys = np.floor(points / voxel).astype(np.int64)
    keys -= keys.min(axis=0)
    dims = keys.max(axis=0) + 1
    flat = (keys[:, 0] * dims[1] + keys[:, 1]) * dims[2] + keys[:, 2]
    order = np.argsort(flat, kind="stable")
    flat = flat[order]
    pts = points[order]
    cols = colors[order]
    starts = np.concatenate([[0], np.nonzero(np.diff(flat))[0] + 1])
    counts = np.diff(np.concatenate([starts, [len(flat)]]))
    sums_p = np.add.reduceat(pts, starts, axis=0)
    sums_c = np.add.reduceat(cols, starts, axis=0)
    return sums_p / counts[:, None], sums_c / counts[:, None]


def floor_candidates(points, centres, radius=1.5, margin=0.3, limit=60000):
    """Points that could plausibly be floor.

    Preference goes to what lies directly beneath the walked path: that strip is
    seen from many keyframes at close range, so it is the best reconstructed
    surface in the model, and it is unambiguously the floor the operator was
    standing on. Only if too little of it survives does this fall back to the
    lowest slice of the whole cloud.
    """
    rng = np.random.default_rng(0)
    sample = points[rng.choice(len(points), min(limit, len(points)), replace=False)]

    horizontal = np.linalg.norm(
        sample[:, None, [0, 2]] - centres[None, :, [0, 2]], axis=2
    )
    nearest = np.argmin(horizontal, axis=1)
    under = (horizontal[np.arange(len(sample)), nearest] < radius) & (
        sample[:, 1] < centres[nearest, 1] - margin
    )
    if int(under.sum()) >= 900:
        return sample[under]
    return sample[sample[:, 1] < np.percentile(sample[:, 1], 30)]


def remove_sparse(points, colors, cell, min_count):
    """Drop points sitting in near-empty cells of a coarse grid.

    Real surfaces are seen by several keyframes and land thousands of points in
    the same neighbourhood; a mismatched pixel lands one somewhere else. This
    removes the speckle that otherwise dominates the bounding box.
    """
    keys = np.floor(points / cell).astype(np.int64)
    keys -= keys.min(axis=0)
    dims = keys.max(axis=0) + 1
    flat = (keys[:, 0] * dims[1] + keys[:, 1]) * dims[2] + keys[:, 2]
    _, inverse, counts = np.unique(flat, return_inverse=True, return_counts=True)
    keep = counts[inverse] >= min_count
    return points[keep], colors[keep], int(keep.sum())


def write_ply(path, points, colors):
    n = len(points)
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {n}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "end_header\n"
    )
    rgb = np.clip(colors * 255.0 + 0.5, 0, 255).astype(np.uint8)
    with open(path, "wb") as fh:
        fh.write(header.encode("ascii"))
        buf = np.empty(n, dtype=[("xyz", "<f4", 3), ("rgb", "u1", 3)])
        buf["xyz"] = points.astype("<f4")
        buf["rgb"] = rgb
        fh.write(buf.tobytes())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True, help="output .ply path")
    ap.add_argument("--stride", type=int, default=4, help="keyframe stride in video frames")
    ap.add_argument("--work-width", type=int, default=512, help="width used for motion estimation")
    ap.add_argument("--sweep-width", type=int, default=768, help="width used for dense depth")
    ap.add_argument("--layers", type=int, default=64)
    ap.add_argument("--min-depth", type=float, default=0.6, help="in baseline units")
    ap.add_argument("--max-depth", type=float, default=40.0, help="in baseline units")
    ap.add_argument("--neighbours", type=int, default=3, help="neighbours on each side")
    ap.add_argument("--agg-radius", type=int, default=2, help="cost aggregation window radius")
    ap.add_argument("--min-confidence", type=float, default=0.06)
    ap.add_argument("--min-texture", type=float, default=0.012)
    ap.add_argument("--max-latitude", type=float, default=72.0, help="degrees; masks zenith/nadir")
    ap.add_argument("--camera-height", type=float, default=1.60, help="metres above the floor")
    ap.add_argument("--no-metric-scale", action="store_true")
    ap.add_argument("--max-points", type=int, default=3_000_000)
    ap.add_argument("--min-cluster", type=int, default=8,
                    help="drop points in grid cells holding fewer than this many; 0 disables")
    ap.add_argument("--start", type=float, default=None)
    ap.add_argument("--duration", type=float, default=None)
    args = ap.parse_args()

    ffmpeg = find_ffmpeg()
    src_w, src_h = probe_size(ffmpeg, args.video)
    if abs(src_w / src_h - 2.0) > 0.05:
        print(
            f"warning: {src_w}x{src_h} is not 2:1. Export equirectangular video "
            "from Insta360 Studio before reconstructing.",
            file=sys.stderr,
        )

    print(f"decoding {args.video} ({src_w}x{src_h}) every {args.stride} frames ...")
    frames = decode_keyframes(
        ffmpeg, args.video, max(args.sweep_width, args.work_width), args.stride,
        args.start, args.duration,
    )
    print(f"  {len(frames)} keyframes")
    if len(frames) < 4:
        raise SystemExit("need at least 4 keyframes; lower --stride")

    print("estimating camera motion ...")
    rel_rotations, dirs, med_depths = estimate_motion(frames, args.work_width)
    baselines = baselines_from_depths(med_depths)
    centres, rotations = build_poses(rel_rotations, dirs, baselines)

    sweep_frames = [resize(f, args.sweep_width) for f in frames]
    grays_hp = [high_pass(to_gray(f), 6) for f in sweep_frames]
    h, w = grays_hp[0].shape
    lat = np.abs(np.pi / 2.0 - (np.arange(h, dtype=np.float32) + 0.5) / h * np.pi)
    lat_mask = (np.degrees(lat) < args.max_latitude)[:, None]

    all_pts, all_cols = [], []
    print(f"sweeping {len(frames)} keyframes at {w}x{h}, {args.layers} layers ...")
    for i in range(len(frames)):
        t0 = time.time()
        nb = [i + d for d in range(-args.neighbours, args.neighbours + 1) if d != 0]
        result = plane_sweep(
            sweep_frames, grays_hp, i, centres, rotations, nb,
            args.layers, args.min_depth, args.max_depth, args.agg_radius,
        )
        if result is None:
            continue
        depth, conf, best_k, world_dirs = result

        texture = _box(np.abs(grays_hp[i]), 2)
        keep = (
            (conf > args.min_confidence)
            & (texture > args.min_texture)
            & (best_k > 0)
            & (best_k < args.layers - 1)
            & lat_mask
        )
        idx = np.nonzero(keep.ravel())[0]
        if len(idx) == 0:
            continue
        pts = (centres[i][None, :] + world_dirs.reshape(-1, 3)[idx] * depth.ravel()[idx, None])
        cols = sweep_frames[i].reshape(-1, 3)[idx]
        all_pts.append(pts.astype(np.float32))
        all_cols.append(cols.astype(np.float32))
        print(
            f"  keyframe {i:3d}: {len(idx):7d} points  ({time.time()-t0:.1f}s)",
            flush=True,
        )

    if not all_pts:
        raise SystemExit("no points survived filtering; try lowering --min-confidence")
    points = np.concatenate(all_pts)
    colors = np.concatenate(all_cols)
    print(f"raw points: {len(points):,}")

    median_range = float(np.median(np.linalg.norm(points - centres.mean(axis=0), axis=1)))
    voxel = max(median_range / 500.0, 1e-4)
    points, colors = voxel_fuse(points, colors, voxel)
    print(f"after voxel fusion ({voxel:.4f} units): {len(points):,}")

    if args.min_cluster > 0:
        before = len(points)
        points, colors, kept = remove_sparse(points, colors, voxel * 4.0, args.min_cluster)
        print(f"after sparse-cell filter: {kept:,} ({100 * kept / max(before, 1):.1f}% kept)")

    scale = 1.0
    transform = np.eye(4, dtype=np.float64)
    if not args.no_metric_scale:
        sample = floor_candidates(points, centres)
        plane = estimate_floor(sample, median_range)
        if plane is None:
            print("floor plane not found; leaving the cloud in relative units", file=sys.stderr)
        else:
            normal, d, count = plane
            heights = centres @ normal + d
            rel_height = float(np.median(np.abs(heights)))
            if rel_height > 1e-6:
                scale = args.camera_height / rel_height
                print(
                    f"floor plane from {count:,} points; camera {rel_height:.3f} units "
                    f"above it -> scale {scale:.4f} (camera height {args.camera_height} m)"
                )
                # Rotating the floor normal onto +Y makes a floor point's height
                # equal n.x = -d, so adding d * scale drops the floor onto y = 0.
                rot = _align_to_up(normal)
                points = (points @ rot.T) * scale
                centres_w = (centres @ rot.T) * scale
                offset = float(d) * scale
                points[:, 1] += offset
                centres_w[:, 1] += offset
                centres = centres_w
                transform[:3, :3] = rot * scale
                transform[1, 3] = offset

    if len(points) > args.max_points:
        sel = np.random.default_rng(0).choice(len(points), args.max_points, replace=False)
        points, colors = points[sel], colors[sel]
        print(f"subsampled to {len(points):,} points")

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    write_ply(out, points, colors)
    meta = {
        "source_video": os.path.basename(args.video),
        "keyframes": len(frames),
        "points": int(len(points)),
        "voxel_size_metres": float(voxel * scale),
        "metric_scale_applied": scale != 1.0,
        "camera_height_metres": args.camera_height,
        "trajectory": np.asarray(centres, dtype=float).round(4).tolist(),
        "settings": {
            "stride": args.stride,
            "sweep_width": args.sweep_width,
            "layers": args.layers,
            "neighbours": args.neighbours,
            "min_confidence": args.min_confidence,
        },
    }
    with open(os.path.splitext(out)[0] + ".json", "w") as fh:
        json.dump(meta, fh, indent=2)
    print(f"wrote {out} ({len(points):,} points)")


def _align_to_up(normal):
    """Rotation taking `normal` onto +Y, keeping the yaw of the walk intact."""
    n = normal / np.linalg.norm(normal)
    up = np.array([0.0, 1.0, 0.0])
    v = np.cross(n, up)
    s = np.linalg.norm(v)
    if s < 1e-8:
        return np.eye(3)
    c = float(n @ up)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * ((1 - c) / (s ** 2))


if __name__ == "__main__":
    main()
