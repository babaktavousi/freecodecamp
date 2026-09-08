#!/usr/bin/env python3
"""Score a reconstruction of the synthetic sample against its ground truth.

The sample scene has exact dimensions, so accuracy can be measured rather than
eyeballed. The reconstructed cloud is first aligned to the true world with a
similarity transform fitted to the camera trajectory (Umeyama), which separates
the choice of global scale from the quality of the geometry itself. Every point
is then compared to the nearest true surface.

    python3 tools/evaluate.py --cloud samples/walkthrough_cloud.ply \\
        --meta samples/walkthrough_cloud.json --truth samples/walkthrough_360.json
"""

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scene import GROUND_TRUTH, build_scene  # noqa: E402


def read_ply(path):
    with open(path, "rb") as fh:
        header = b""
        while b"end_header" not in header:
            line = fh.readline()
            if not line:
                raise SystemExit(f"{path} is not a PLY file")
            header += line
        count = None
        for line in header.split(b"\n"):
            if line.startswith(b"element vertex"):
                count = int(line.split()[-1])
        if count is None:
            raise SystemExit("no vertex count in PLY header")
        data = np.frombuffer(
            fh.read(count * 15), dtype=[("xyz", "<f4", 3), ("rgb", "u1", 3)]
        )
    return data["xyz"].astype(np.float64), data["rgb"]


def umeyama(source, target):
    """Similarity transform (scale, rotation, translation) mapping source onto target."""
    mu_s = source.mean(axis=0)
    mu_t = target.mean(axis=0)
    s0 = source - mu_s
    t0 = target - mu_t
    cov = t0.T @ s0 / len(source)
    u, d, vt = np.linalg.svd(cov)
    sign = np.eye(3)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        sign[2, 2] = -1
    rotation = u @ sign @ vt
    variance = (s0 ** 2).sum() / len(source)
    scale = float(np.trace(np.diag(d) @ sign) / variance)
    translation = mu_t - scale * rotation @ mu_s
    return scale, rotation, translation


def distance_to_scene(points, quads, chunk=200_000):
    """Distance from each point to the nearest scene surface."""
    best = np.full(len(points), np.inf)
    for start in range(0, len(points), chunk):
        block = points[start:start + chunk]
        local = np.full(len(block), np.inf)
        for quad in quads:
            a = quad.axis_i
            ui, vi = quad.uv_axes
            u = np.clip(block[:, ui], quad.u_range[0], quad.u_range[1])
            v = np.clip(block[:, vi], quad.v_range[0], quad.v_range[1])
            inside_hole = np.zeros(len(block), dtype=bool)
            for hu0, hu1, hv0, hv1 in quad.holes:
                inside_hole |= (
                    (u >= hu0) & (u <= hu1) & (v >= hv0) & (v <= hv1)
                )
            d = np.sqrt(
                (block[:, a] - quad.plane) ** 2
                + (block[:, ui] - u) ** 2
                + (block[:, vi] - v) ** 2
            )
            d = np.where(inside_hole, np.inf, d)
            np.minimum(local, d, out=local)
        best[start:start + chunk] = local
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cloud", required=True)
    ap.add_argument("--meta", help="reconstruction sidecar JSON (defaults to <cloud>.json)")
    ap.add_argument("--truth", default="samples/walkthrough_360.json")
    ap.add_argument("--stride", type=int, default=4, help="keyframe stride in video frames")
    ap.add_argument("--spacing", type=float, default=None,
                    help="keyframe spacing in seconds (the browser app's setting); "
                         "overrides --stride and interpolates the true path")
    ap.add_argument("--inlier", type=float, default=0.10, help="inlier threshold in metres")
    args = ap.parse_args()

    meta_path = args.meta or os.path.splitext(args.cloud)[0] + ".json"
    meta = json.load(open(meta_path))
    truth = json.load(open(args.truth))

    points, _ = read_ply(args.cloud)
    estimated = np.array(meta["trajectory"], dtype=np.float64)
    true_positions = np.array(truth["camera_positions"], dtype=np.float64)

    if args.spacing:
        # Keyframes taken at a fixed time do not land on whole frames.
        frames = np.arange(len(estimated)) * args.spacing * truth["fps"]
        frames = frames[frames <= len(true_positions) - 1]
        estimated = estimated[: len(frames)]
        target = np.stack(
            [np.interp(frames, np.arange(len(true_positions)), true_positions[:, k]) for k in range(3)],
            axis=1,
        )
    else:
        indices = np.arange(len(estimated)) * args.stride
        indices = indices[indices < len(true_positions)]
        estimated = estimated[: len(indices)]
        target = true_positions[indices]

    scale, rotation, translation = umeyama(estimated, target)
    aligned_path = (scale * (rotation @ estimated.T).T) + translation
    path_error = np.linalg.norm(aligned_path - target, axis=1)

    aligned = (scale * (rotation @ points.T).T) + translation
    distances = distance_to_scene(aligned, build_scene())
    inliers = distances < args.inlier

    print(f"cloud            : {args.cloud}")
    print(f"points           : {len(points):,}")
    print(f"keyframes        : {len(estimated)}")
    print()
    print("trajectory (after similarity alignment to ground truth)")
    print(f"  residual scale : {scale:.4f}  "
          f"({'reconstruction is metric' if abs(scale - 1) < 0.02 else f'cloud is {1/scale:.3f}x true size'})")
    print(f"  mean error     : {path_error.mean():.3f} m")
    print(f"  max error      : {path_error.max():.3f} m")
    print(f"  path length    : {np.linalg.norm(np.diff(target, axis=0), axis=1).sum():.2f} m")
    print()
    print("point accuracy (distance to the true surface)")
    for q in (50, 75, 90, 95):
        print(f"  p{q:<3d}          : {np.percentile(distances, q):.3f} m")
    print(f"  mean           : {distances.mean():.3f} m")
    print(f"  within {args.inlier:.2f} m  : {100 * inliers.mean():.1f}% of points")
    print()
    print("ground truth for manual checks (metres)")
    for key, value in GROUND_TRUTH.items():
        print(f"  {key:<18}: {value}")


if __name__ == "__main__":
    main()
