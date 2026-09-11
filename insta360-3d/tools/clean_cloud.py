#!/usr/bin/env python3
"""Remove the two things a walking 360 capture always reconstructs wrongly.

Both have the same cause: content with no parallax. Depth comes from how much a
surface shifts between viewpoints, and two things never shift.

  Sky      is at infinity, so it never moves between frames. The matcher cannot
           place it, and scatters sky-coloured points along whatever thin, high
           contrast thing is silhouetted against it — overhead wires and bare
           branches especially, which is why those end up wrapped in blue haze.

  The operator moves *with* the camera, so they are fixed in the image and look
           exactly like infinity too. They land as a smear under the camera,
           along with the selfie stick and the shadow.

    python3 tools/clean_cloud.py --in cloud.ply --out clean.ply

The camera path in the reconstruction's .json sidecar is what makes the second
one possible: it tells us where the camera was, so "below the camera" is a
direction we can actually test rather than guess at from height alone.
"""

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from convert_cloud import read_ply  # noqa: E402


def write_ply(path, xyz, rgb):
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        "comment cleaned by Insta360 Point Cloud Studio\n"
        f"element vertex {len(xyz)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "end_header\n"
    )
    record = np.zeros(len(xyz), dtype=np.dtype([
        ("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
        ("red", "u1"), ("green", "u1"), ("blue", "u1"),
    ]))
    record["x"], record["y"], record["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    record["red"], record["green"], record["blue"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    with open(path, "wb") as fh:
        fh.write(header.encode())
        fh.write(record.tobytes())


def sky_like(rgb, margin, floor):
    """Daylight sky is blue-dominant and bright; painted blue rarely is both."""
    r = rgb[:, 0].astype(np.int16)
    b = rgb[:, 2].astype(np.int16)
    luma = 0.299 * rgb[:, 0] + 0.587 * rgb[:, 1] + 0.114 * rgb[:, 2]
    return (b > r + margin) & (luma > floor)


def too_close(xyz, trajectory, radius):
    """The operator, by distance from the camera rather than by direction.

    Angle is the wrong test: the road is directly below a head-mounted camera
    too, and a nadir cone wide enough to catch someone's shoulders deletes the
    strip of road under the walk — the densest, best-triangulated surface in
    the whole capture.

    Distance separates them cleanly. The operator's head and the stick sit a
    few tens of centimetres from the lens and stay there; the road is a fixed
    camera-height away, about 1.7 m. Nothing else in a street can be closer
    than that to a camera that is walking through it.
    """
    if trajectory is None or len(trajectory) == 0:
        return np.zeros(len(xyz), dtype=bool)
    cams = np.asarray(trajectory, dtype=np.float64)
    nearest = np.full(len(xyz), np.inf)
    for lo in range(0, len(xyz), 200_000):
        hi = min(lo + 200_000, len(xyz))
        d = ((xyz[lo:hi, None, :] - cams[None, :, :]) ** 2).sum(axis=2)
        nearest[lo:hi] = d.min(axis=1)
    return nearest < radius * radius


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--in", dest="source", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--json", help="reconstruction sidecar; defaults to the input's .json")
    ap.add_argument("--operator-radius", type=float, default=0.9,
                    help="drop points within this many metres of the camera path; "
                         "the operator and stick live here and nothing else can "
                         "(0 disables)")
    ap.add_argument("--sky-margin", type=int, default=22,
                    help="how much bluer than red a point must be to count as sky")
    ap.add_argument("--sky-luma", type=int, default=95,
                    help="how bright it must also be (0-255)")
    ap.add_argument("--keep-sky", action="store_true")
    args = ap.parse_args()

    xyz, rgb = read_ply(args.source)
    print(f"read {len(xyz):,} points")

    sidecar = args.json or os.path.splitext(args.source)[0] + ".json"
    trajectory = None
    if os.path.exists(sidecar):
        trajectory = json.load(open(sidecar)).get("trajectory")
        print(f"camera path: {len(trajectory)} poses from {os.path.basename(sidecar)}")
    elif args.operator_radius:
        print(f"no sidecar at {sidecar} — cannot locate the operator, skipping that step")

    drop = np.zeros(len(xyz), dtype=bool)
    if not args.keep_sky:
        mask = sky_like(rgb, args.sky_margin, args.sky_luma)
        print(f"  sky-coloured:      {mask.sum():>9,}  ({100 * mask.mean():.1f}%)")
        drop |= mask
    if args.operator_radius and trajectory:
        mask = too_close(xyz, trajectory, args.operator_radius)
        print(f"  on the operator:   {mask.sum():>9,}  ({100 * mask.mean():.1f}%)")
        drop |= mask

    keep = ~drop
    write_ply(args.out, xyz[keep], rgb[keep])
    print(f"kept {keep.sum():,} of {len(xyz):,} ({100 * keep.mean():.1f}%) -> {args.out}")


if __name__ == "__main__":
    sys.exit(main())
