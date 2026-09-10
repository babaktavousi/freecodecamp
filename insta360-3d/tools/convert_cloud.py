#!/usr/bin/env python3
"""Convert a reconstructed PLY into the formats CAD tools actually read.

PLY is a graphics format. Navisworks and Trimble Connect do not import it, so a
cloud has to leave here as LAS (the LiDAR interchange standard), LAZ (the same
thing losslessly compressed, about 3x smaller) or PTS (plain text).

    python3 tools/convert_cloud.py --in samples/walkthrough_cloud.ply --out cloud.las
    python3 tools/convert_cloud.py --in cloud.ply --out cloud.laz
    python3 tools/convert_cloud.py --in cloud.ply --out cloud.las --split-mb 25

--split-mb writes numbered parts under a size limit, for transfers that impose
one. Each part is a complete file, and ReCap and Trimble load a set of them
into a single project, so nothing is lost by splitting.

Axes are converted from the viewer's Y-up convention to Z-up, which is what
every CAD package assumes; without it the model arrives lying on its side.

Getting into Navisworks Freedom: Freedom opens only NWD, DWF and ReCap
RCS/RCP, so convert the LAS in Autodesk ReCap and save an .rcp. Navisworks
Manage can append the LAS directly and publish an NWD.

Getting into Trimble Connect: add the LAS through Trimble Connect for Windows;
it is then viewable in the browser client as well.
"""

import argparse
import datetime
import math
import os
import struct
import sys

import numpy as np

HEADER_SIZE = 227
POINT_SIZE = 26  # LAS point data record format 2
SCALE = 0.001


def read_ply(path):
    """Read the binary little-endian PLY this project writes."""
    with open(path, "rb") as fh:
        if fh.readline().strip() != b"ply":
            raise SystemExit(f"{path} is not a PLY file")
        count, props, fmt = 0, [], None
        while True:
            line = fh.readline()
            if not line:
                raise SystemExit("unexpected end of PLY header")
            parts = line.split()
            if parts[0] == b"format":
                fmt = parts[1]
            elif parts[0] == b"element" and parts[1] == b"vertex":
                count = int(parts[2])
            elif parts[0] == b"property":
                props.append((parts[1].decode(), parts[-1].decode()))
            elif parts[0] == b"end_header":
                break
        if fmt != b"binary_little_endian":
            raise SystemExit(f"only binary_little_endian PLY is supported, got {fmt.decode()}")

        numpy_types = {
            "float": "<f4", "float32": "<f4", "double": "<f8", "float64": "<f8",
            "uchar": "u1", "uint8": "u1", "char": "i1", "int8": "i1",
            "ushort": "<u2", "uint16": "<u2", "short": "<i2", "int16": "<i2",
            "uint": "<u4", "uint32": "<u4", "int": "<i4", "int32": "<i4",
        }
        dtype = np.dtype([(name, numpy_types[kind]) for kind, name in props])
        data = np.frombuffer(fh.read(count * dtype.itemsize), dtype=dtype, count=count)

    xyz = np.stack([data["x"], data["y"], data["z"]], axis=1).astype(np.float64)
    if "red" in data.dtype.names:
        rgb = np.stack([data["red"], data["green"], data["blue"]], axis=1).astype(np.uint16)
    else:
        rgb = np.full((len(xyz), 3), 200, dtype=np.uint16)
    return xyz, rgb


def to_z_up(xyz):
    """Y-up (viewer) to Z-up (CAD), preserving handedness."""
    return np.stack([xyz[:, 0], -xyz[:, 2], xyz[:, 1]], axis=1)


def write_las(path, xyz, rgb):
    xyz = to_z_up(xyz)
    mins, maxs = xyz.min(axis=0), xyz.max(axis=0)
    offset = np.floor(mins)
    scaled = np.round((xyz - offset) / SCALE).astype(np.int32)

    header = bytearray(HEADER_SIZE)
    header[0:4] = b"LASF"
    header[24] = 1  # version major
    header[25] = 2  # version minor
    header[26:58] = b"Insta360 Point Cloud Studio".ljust(32, b"\0")[:32]
    header[58:90] = b"Insta360 Point Cloud Studio".ljust(32, b"\0")[:32]
    # Some importers reject a zero creation date as a malformed header.
    today = datetime.date.today()
    struct.pack_into("<H", header, 90, today.timetuple().tm_yday)
    struct.pack_into("<H", header, 92, today.year)
    struct.pack_into("<H", header, 94, HEADER_SIZE)
    struct.pack_into("<I", header, 96, HEADER_SIZE)
    struct.pack_into("<I", header, 100, 0)  # no variable length records
    header[104] = 2  # point data record format
    struct.pack_into("<H", header, 105, POINT_SIZE)
    struct.pack_into("<I", header, 107, len(xyz))
    struct.pack_into("<I", header, 111, len(xyz))  # all a single first return
    struct.pack_into("<3d", header, 131, SCALE, SCALE, SCALE)
    struct.pack_into("<3d", header, 155, *offset)
    struct.pack_into("<6d", header, 179,
                     maxs[0], mins[0], maxs[1], mins[1], maxs[2], mins[2])

    records = np.zeros(len(xyz), dtype=np.dtype([
        ("x", "<i4"), ("y", "<i4"), ("z", "<i4"), ("intensity", "<u2"),
        ("flags", "u1"), ("classification", "u1"), ("scan_angle", "i1"),
        ("user_data", "u1"), ("point_source", "<u2"),
        ("red", "<u2"), ("green", "<u2"), ("blue", "<u2"),
    ]))
    records["x"], records["y"], records["z"] = scaled[:, 0], scaled[:, 1], scaled[:, 2]
    luma = 0.299 * rgb[:, 0] + 0.587 * rgb[:, 1] + 0.114 * rgb[:, 2]
    records["intensity"] = (luma * 257).astype(np.uint16)
    records["flags"] = 0b00001001  # return 1 of 1
    records["classification"] = 1  # unclassified
    records["red"] = rgb[:, 0] * 257
    records["green"] = rgb[:, 1] * 257
    records["blue"] = rgb[:, 2] * 257

    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(records.tobytes())


def write_pts(path, xyz, rgb):
    xyz = to_z_up(xyz)
    luma = (0.299 * rgb[:, 0] + 0.587 * rgb[:, 1] + 0.114 * rgb[:, 2]).astype(int) - 2048
    with open(path, "w") as fh:
        fh.write(f"{len(xyz)}\n")
        for (x, y, z), (r, g, b), i in zip(xyz, rgb, luma):
            fh.write(f"{x:.4f} {y:.4f} {z:.4f} {i} {r} {g} {b}\n")


def write_laz(path, xyz, rgb):
    """LAZ is LAS losslessly compressed, roughly 3x smaller and read anywhere LAS is."""
    try:
        import laspy
    except ImportError:
        raise SystemExit("LAZ needs laspy: pip install 'laspy[lazrs]'")
    las_path = os.path.splitext(path)[0] + ".tmp.las"
    write_las(las_path, xyz, rgb)
    try:
        laspy.read(las_path).write(path)
    finally:
        os.remove(las_path)


def split_indices(count, parts):
    edges = np.linspace(0, count, parts + 1).astype(int)
    return list(zip(edges[:-1], edges[1:]))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--in", dest="source", required=True, help="input .ply")
    ap.add_argument("--out", required=True, help="output .las, .laz or .pts")
    ap.add_argument("--split-mb", type=float,
                    help="split into numbered parts no larger than this, for "
                         "transfers with a size limit. Every part is a complete "
                         "file; ReCap and Trimble load them into one project.")
    args = ap.parse_args()

    suffix = os.path.splitext(args.out)[1].lower()
    writers = {".las": write_las, ".laz": write_laz, ".pts": write_pts}
    if suffix not in writers:
        raise SystemExit("--out must end in .las, .laz or .pts")
    write = writers[suffix]

    xyz, rgb = read_ply(args.source)
    print(f"read {len(xyz):,} points from {args.source}")

    if not args.split_mb:
        write(args.out, xyz, rgb)
        print(f"wrote {args.out} ({os.path.getsize(args.out) / 1e6:.1f} MB), Z-up, metres")
        return

    # Size one part, then split by point count — every format here is fixed or
    # near-fixed cost per point, so the estimate holds.
    probe = os.path.splitext(args.out)[0] + ".probe" + suffix
    sample = min(len(xyz), 200_000)
    write(probe, xyz[:sample], rgb[:sample])
    per_point = os.path.getsize(probe) / sample
    os.remove(probe)

    parts = max(1, math.ceil(len(xyz) * per_point / (args.split_mb * 1e6)))
    stem, ext = os.path.splitext(args.out)
    print(f"splitting into {parts} part(s) of at most {args.split_mb:.0f} MB")
    for n, (lo, hi) in enumerate(split_indices(len(xyz), parts), start=1):
        part = f"{stem}_{n:02d}of{parts:02d}{ext}"
        write(part, xyz[lo:hi], rgb[lo:hi])
        print(f"  {part}  {hi - lo:,} points  {os.path.getsize(part) / 1e6:.1f} MB")


if __name__ == "__main__":
    sys.exit(main())
