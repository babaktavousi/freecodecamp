#!/usr/bin/env python3
"""Solve camera poses with COLMAP, to replace this project's chained estimates.

Why bother: reconstruct.py derives each pose from the one before it, so small
errors compound and the walk slowly bends. COLMAP instead solves every camera
and every tracked point together — bundle adjustment — so there is nothing to
accumulate. That is the single biggest difference between this and commercial
photogrammetry, and it is the part worth borrowing.

COLMAP cannot read equirectangular images, so each keyframe is rendered as a
ring of overlapping pinhole views. COLMAP recovers a pose per view; because the
views of one keyframe are a rigid rig with known relative orientation, each one
votes for the same 360 pose and the votes are averaged.

    python3 tools/colmap_poses.py --video walk.mp4 --stride 12 --out poses.json
    python3 tools/reconstruct.py --video walk.mp4 --stride 12 --poses poses.json \
        --out cloud.ply

Dense stereo is deliberately not used: COLMAP's is CUDA-only, and the plane
sweep in reconstruct.py already does that job on the CPU. This takes the poses
and nothing else.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reconstruct import decode_keyframes, find_ffmpeg, probe_size  # noqa: E402
from spherical import sample_bilinear  # noqa: E402


def perspective_view(equirect, yaw, pitch, fov_deg, size):
    """Render one pinhole view out of an equirectangular frame."""
    h, w = equirect.shape[:2]
    f = (size / 2.0) / np.tan(np.radians(fov_deg) / 2.0)
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)
    x = (xs - size / 2.0 + 0.5) / f
    y = (ys - size / 2.0 + 0.5) / f
    dirs = np.stack([x, -y, np.ones_like(x)], axis=-1)
    dirs /= np.linalg.norm(dirs, axis=-1, keepdims=True)

    cy, sy = np.cos(yaw), np.sin(yaw)
    cp, sp = np.cos(pitch), np.sin(pitch)
    r_yaw = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float32)
    r_pitch = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]], dtype=np.float32)
    world = dirs @ (r_yaw @ r_pitch).T

    lon = np.arctan2(world[..., 0], world[..., 2])
    lat = np.arcsin(np.clip(world[..., 1], -1.0, 1.0))
    u = (lon / (2.0 * np.pi) + 0.5) * w - 0.5
    v = (0.5 - lat / np.pi) * h - 0.5
    return sample_bilinear(equirect, u.astype(np.float32), v.astype(np.float32))


def quat_to_matrix(q):
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ], dtype=np.float64)


def read_images_txt(path):
    """COLMAP images.txt -> {image name: (world-from-camera R, camera centre)}."""
    poses = {}
    with open(path) as fh:
        lines = [ln for ln in fh if not ln.startswith("#")]
    # Every image is two lines: the pose, then its 2D observations.
    for i in range(0, len(lines), 2):
        parts = lines[i].split()
        if len(parts) < 10:
            continue
        qw, qx, qy, qz = (float(v) for v in parts[1:5])
        tx, ty, tz = (float(v) for v in parts[5:8])
        name = parts[9]
        r_cw = quat_to_matrix((qw, qx, qy, qz))  # world -> camera
        t = np.array([tx, ty, tz], dtype=np.float64)
        poses[name] = (r_cw.T, -r_cw.T @ t)
    return poses


def run(cmd, label):
    print(f"  {label} ...", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout[-2000:] + result.stderr[-2000:])
        raise SystemExit(f"colmap {label} failed")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True, help="poses .json for reconstruct.py")
    ap.add_argument("--stride", type=int, default=12)
    ap.add_argument("--work-dir", default=None, help="defaults to <out>.colmap")
    ap.add_argument("--views", type=int, default=6, help="pinhole views per keyframe")
    ap.add_argument("--view-size", type=int, default=640)
    ap.add_argument("--fov", type=float, default=75.0)
    ap.add_argument("--decode-width", type=int, default=2048)
    ap.add_argument("--start", type=float)
    ap.add_argument("--duration", type=float)
    ap.add_argument("--keep-images", action="store_true")
    args = ap.parse_args()

    work = args.work_dir or os.path.splitext(args.out)[0] + ".colmap"
    images = os.path.join(work, "images")
    os.makedirs(images, exist_ok=True)

    ffmpeg = find_ffmpeg()
    print(f"decoding {args.video} every {args.stride} frames ...")
    frames = decode_keyframes(
        ffmpeg, args.video, args.decode_width, args.stride, args.start, args.duration
    )
    print(f"{len(frames)} keyframes; rendering {args.views} pinhole views each")

    yaws = [2.0 * np.pi * k / args.views for k in range(args.views)]
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("this needs Pillow: pip install pillow")

    for i, frame in enumerate(frames):
        for k, yaw in enumerate(yaws):
            view = perspective_view(frame, yaw, 0.0, args.fov, args.view_size)
            arr = np.clip(view * 255.0, 0, 255).astype(np.uint8)
            # View-major naming, because COLMAP's sequential matcher pairs
            # images in filename order. Keyframe-major ordering makes it compare
            # different directions at the same instant, which barely overlap;
            # view-major makes it compare the same direction across consecutive
            # keyframes, which is exactly where the overlap is.
            Image.fromarray(arr).save(os.path.join(images, f"v{k}_kf{i:04d}.jpg"), quality=92)

    database = os.path.join(work, "database.db")
    sparse = os.path.join(work, "sparse")
    os.makedirs(sparse, exist_ok=True)
    if os.path.exists(database):
        os.remove(database)

    focal = (args.view_size / 2.0) / np.tan(np.radians(args.fov) / 2.0)
    print("running COLMAP (CPU) ...")
    run([
        "colmap", "feature_extractor",
        "--database_path", database, "--image_path", images,
        "--ImageReader.camera_model", "PINHOLE",
        "--ImageReader.single_camera", "1",
        "--ImageReader.camera_params",
        f"{focal},{focal},{args.view_size / 2.0},{args.view_size / 2.0}",
        "--SiftExtraction.use_gpu", "0",
    ], "feature extraction")

    # Sequential matching suits a walk: consecutive keyframes overlap, and the
    # loop detection still catches a path that doubles back.
    run([
        "colmap", "sequential_matcher",
        "--database_path", database,
        "--SiftMatching.use_gpu", "0",
        "--SequentialMatching.overlap", "10",
        "--SequentialMatching.quadratic_overlap", "0",
    ], "matching")

    run([
        "colmap", "mapper",
        "--database_path", database, "--image_path", images,
        "--output_path", sparse,
    ], "sparse reconstruction (bundle adjustment)")

    model = os.path.join(sparse, "0")
    if not os.path.isdir(model):
        raise SystemExit("COLMAP produced no reconstruction; try more views or a longer clip")
    run([
        "colmap", "model_converter",
        "--input_path", model, "--output_path", model, "--output_type", "TXT",
    ], "exporting model")

    poses = read_images_txt(os.path.join(model, "images.txt"))
    print(f"COLMAP registered {len(poses)} of {len(frames) * args.views} views")

    # Each keyframe's views share a centre and differ by a known yaw, so undo
    # that yaw and average what remains.
    centres, rotations, solved = [], [], []
    for i in range(len(frames)):
        cs, rs = [], []
        for k, yaw in enumerate(yaws):
            hit = poses.get(f"v{k}_kf{i:04d}.jpg")
            if hit is None:
                continue
            r_wc, centre = hit
            cy, sy = np.cos(yaw), np.sin(yaw)
            r_yaw = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
            cs.append(centre)
            rs.append(r_wc @ r_yaw.T)
        if not cs:
            centres.append(None)
            rotations.append(None)
            continue
        centres.append(np.mean(cs, axis=0))
        # Average rotations via the dominant eigenvector of the summed outer
        # products; plain elementwise averaging would not stay orthonormal.
        acc = np.zeros((3, 3))
        for r in rs:
            acc += r
        u, _, vt = np.linalg.svd(acc)
        rot = u @ vt
        if np.linalg.det(rot) < 0:
            u[:, -1] *= -1
            rot = u @ vt
        rotations.append(rot)
        solved.append(i)

    print(f"recovered 360 poses for {len(solved)} of {len(frames)} keyframes")
    if len(solved) < 2:
        raise SystemExit("too few keyframes solved to be useful")

    json.dump({
        "source_video": os.path.basename(args.video),
        "stride": args.stride,
        "keyframes": len(frames),
        "solver": "colmap-sparse-bundle-adjusted",
        "centres": [None if c is None else [float(v) for v in c] for c in centres],
        "rotations": [None if r is None else [float(v) for v in r.ravel()] for r in rotations],
    }, open(args.out, "w"), indent=1)
    print(f"wrote {args.out}")

    if not args.keep_images:
        shutil.rmtree(images, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
