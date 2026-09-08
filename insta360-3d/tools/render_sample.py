#!/usr/bin/env python3
"""Render a synthetic equirectangular 360 walkthrough video.

The output mimics what an Insta360 X3 (or X4/X5/ONE X2) produces after
exporting an .insv to an equirectangular MP4: a 2:1 frame, horizon locked,
front of the camera at the centre of the image. Because the scene dimensions
are known exactly, the clip doubles as an accuracy test fixture for the
reconstruction pipeline and the measurement tools.

    python3 tools/render_sample.py --out samples/walkthrough_360.webm
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scene import GROUND_TRUTH, build_scene, camera_path  # noqa: E402

MATERIALS = {
    "floor": dict(color=(0.42, 0.40, 0.38), pattern="tile", rough=0.85),
    "ceiling": dict(color=(0.78, 0.78, 0.76), pattern="ceiling", rough=0.35),
    "wall": dict(color=(0.66, 0.63, 0.58), pattern="plaster", rough=0.70),
    "trim": dict(color=(0.52, 0.45, 0.38), pattern="plaster", rough=0.60),
    "table": dict(color=(0.45, 0.31, 0.20), pattern="wood", rough=0.65),
    "crate": dict(color=(0.58, 0.44, 0.26), pattern="wood", rough=0.75),
    "column": dict(color=(0.60, 0.60, 0.62), pattern="plaster", rough=0.55),
    "window": dict(color=(0.82, 0.86, 0.95), pattern="glazing", rough=0.20),
}
MATERIAL_IDS = {name: i for i, name in enumerate(MATERIALS)}


def _fract(x):
    return x - np.floor(x)


def _hash2(ix, iy, seed=0.0):
    h = np.sin(ix * 127.1 + iy * 311.7 + seed * 74.7) * 43758.5453
    return _fract(h)


def _value_noise(u, v, scale, seed=0.0):
    """Bilinearly interpolated value noise over surface coordinates."""
    x, y = u / scale, v / scale
    ix, iy = np.floor(x), np.floor(y)
    fx, fy = x - ix, y - iy
    fx = fx * fx * (3.0 - 2.0 * fx)
    fy = fy * fy * (3.0 - 2.0 * fy)
    n00 = _hash2(ix, iy, seed)
    n10 = _hash2(ix + 1, iy, seed)
    n01 = _hash2(ix, iy + 1, seed)
    n11 = _hash2(ix + 1, iy + 1, seed)
    return (n00 * (1 - fx) + n10 * fx) * (1 - fy) + (n01 * (1 - fx) + n11 * fx) * fy


def _stripe(x, period, width):
    return (_fract(x / period) < (width / period)).astype(np.float32)


def equirect_directions(width, height):
    """Unit ray directions for every pixel of an equirectangular frame."""
    i = np.arange(width, dtype=np.float32) + 0.5
    j = np.arange(height, dtype=np.float32) + 0.5
    lon = (i / width) * 2.0 * np.pi - np.pi
    lat = np.pi / 2.0 - (j / height) * np.pi
    lon, lat = np.meshgrid(lon, lat)
    cos_lat = np.cos(lat)
    return np.stack([cos_lat * np.sin(lon), np.sin(lat), cos_lat * np.cos(lon)], axis=-1)


def trace(dirs_flat, origin, quads):
    """Nearest-hit ray cast against the axis aligned quad soup."""
    n = dirs_flat.shape[0]
    best_t = np.full(n, np.inf, dtype=np.float32)
    best_u = np.zeros(n, dtype=np.float32)
    best_v = np.zeros(n, dtype=np.float32)
    best_mat = np.zeros(n, dtype=np.int16)
    best_axis = np.zeros(n, dtype=np.int8)

    for quad in quads:
        a = quad.axis_i
        d_a = dirs_flat[:, a]
        denom = np.where(np.abs(d_a) < 1e-7, np.nan, d_a)
        t = (quad.plane - origin[a]) / denom
        hit = np.isfinite(t) & (t > 1e-3) & (t < best_t)
        if not hit.any():
            continue

        ui, vi = quad.uv_axes
        idx = np.nonzero(hit)[0]
        tt = t[idx]
        u = origin[ui] + dirs_flat[idx, ui] * tt
        v = origin[vi] + dirs_flat[idx, vi] * tt
        inside = (
            (u >= quad.u_range[0])
            & (u <= quad.u_range[1])
            & (v >= quad.v_range[0])
            & (v <= quad.v_range[1])
        )
        for hu0, hu1, hv0, hv1 in quad.holes:
            inside &= ~((u >= hu0) & (u <= hu1) & (v >= hv0) & (v <= hv1))
        if not inside.any():
            continue

        idx = idx[inside]
        best_t[idx] = tt[inside]
        best_u[idx] = u[inside]
        best_v[idx] = v[inside]
        best_mat[idx] = MATERIAL_IDS[quad.material]
        best_axis[idx] = a

    return best_t, best_u, best_v, best_mat, best_axis


def shade(best_t, u, v, mat, axis, exposure=1.0):
    """Procedural texturing. Surfaces carry fine detail on purpose: stereo
    matching needs texture, and blank CG walls would flatter the pipeline in a
    way real plaster and asphalt do not."""
    rgb = np.zeros((u.shape[0], 3), dtype=np.float32)
    valid = np.isfinite(best_t)

    detail = (
        0.55 * _value_noise(u, v, 0.045, 1.0)
        + 0.30 * _value_noise(u, v, 0.17, 2.0)
        + 0.15 * _value_noise(u, v, 0.9, 3.0)
    )

    for name, props in MATERIALS.items():
        m = valid & (mat == MATERIAL_IDS[name])
        if not m.any():
            continue
        base = np.array(props["color"], dtype=np.float32)
        um, vm, dm = u[m], v[m], detail[m]
        tex = 0.80 + 0.40 * dm

        if props["pattern"] == "tile":
            grout = np.maximum(_stripe(um, 0.60, 0.02), _stripe(vm, 0.60, 0.02))
            tile_id = _hash2(np.floor(um / 0.60), np.floor(vm / 0.60), 7.0)
            tex = tex * (0.92 + 0.16 * tile_id) * (1.0 - 0.45 * grout)
        elif props["pattern"] == "plaster":
            band = 0.06 * np.sin(vm * 6.0) + 0.04 * np.sin(um * 3.3)
            tex = tex + band
            # Sparse posters / signage give strong, unambiguous features.
            poster = (_hash2(np.floor(um / 1.7), np.floor(vm / 1.3), 11.0) > 0.86).astype(np.float32)
            poster *= ((_fract(um / 1.7) > 0.2) & (_fract(um / 1.7) < 0.8)).astype(np.float32)
            poster *= ((_fract(vm / 1.3) > 0.25) & (_fract(vm / 1.3) < 0.75)).astype(np.float32)
            tex = tex * (1.0 - poster) + poster * (0.35 + 0.9 * dm)
        elif props["pattern"] == "ceiling":
            panel = _stripe(vm, 3.0, 0.9) * ((np.abs(um) < 0.45).astype(np.float32))
            tex = tex * (1.0 + 1.5 * panel)
        elif props["pattern"] == "glazing":
            mullion = np.maximum(_stripe(um, 0.55, 0.035), _stripe(vm, 0.70, 0.035))
            tex = (1.35 + 0.30 * dm) * (1.0 - 0.55 * mullion)
        elif props["pattern"] == "wood":
            grain = 0.5 + 0.5 * np.sin(um * 26.0 + 5.0 * _value_noise(um, vm, 0.35, 5.0))
            tex = tex * (0.80 + 0.35 * grain)

        col = base[None, :] * tex[:, None]
        if props["pattern"] == "plaster":
            col[:, 2] *= 0.97
        rgb[m] = col

    # Distance falloff plus a per-orientation term, standing in for the soft
    # interior lighting a hand-held 360 camera sees.
    dist = np.where(valid, best_t, 1.0)
    light = 0.45 + 0.55 * np.exp(-dist / 16.0)
    face = np.select(
        [axis == 0, axis == 1, axis == 2],
        [0.92, 1.0, 0.86],
        default=1.0,
    ).astype(np.float32)
    shading = (light * face * exposure)[:, None]
    rgb = rgb * shading
    rgb[~valid] = 0.04
    return np.clip(rgb, 0.0, 1.0)


def yaw_matrix(angle):
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]], dtype=np.float32)


def render_frame(dirs, origin, yaw, quads, exposure=1.0):
    h, w, _ = dirs.shape
    world_dirs = dirs.reshape(-1, 3) @ yaw_matrix(yaw).T
    best_t, u, v, mat, axis = trace(np.ascontiguousarray(world_dirs), origin, quads)
    rgb = shade(best_t, u, v, mat, axis, exposure)
    return (rgb.reshape(h, w, 3) * 255.0 + 0.5).astype(np.uint8)


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise SystemExit("ffmpeg not found. Install ffmpeg or `pip install imageio-ffmpeg`.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="samples/walkthrough_360.webm")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--frames", type=int, default=150)
    ap.add_argument("--speed", type=float, default=1.25, help="walking speed in m/s")
    ap.add_argument("--crf", type=int, default=20)
    args = ap.parse_args()

    width = args.width
    height = width // 2
    quads = build_scene()
    dirs = equirect_directions(width, height)
    positions, yaws = camera_path(args.frames, args.fps, args.speed)

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    ffmpeg = find_ffmpeg()
    # VP9/WebM by default: every current browser decodes it, while H.264 is
    # absent from some Chromium builds.
    if out_path.endswith(".webm"):
        codec = ["-c:v", "libvpx-vp9", "-crf", str(args.crf + 10), "-b:v", "0",
                 "-row-mt", "1", "-speed", "2"]
    else:
        codec = ["-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf),
                 "-movflags", "+faststart"]
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(args.fps), "-i", "-",
        "-an", *codec, "-pix_fmt", "yuv420p", out_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    for k in range(args.frames):
        frame = render_frame(dirs, positions[k].astype(np.float32), float(yaws[k]), quads)
        proc.stdin.write(frame.tobytes())
        if (k + 1) % 8 == 0 or k == args.frames - 1:
            print(f"  rendered {k + 1}/{args.frames} frames", flush=True)
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit("ffmpeg failed")

    meta = {
        "description": "Synthetic equirectangular 360 walkthrough (corridor into a room)",
        "projection": "equirectangular",
        "width": width,
        "height": height,
        "fps": args.fps,
        "frames": args.frames,
        "walking_speed_mps": args.speed,
        "ground_truth_metres": GROUND_TRUTH,
        "camera_positions": positions.round(4).tolist(),
        "camera_yaw_rad": np.round(yaws, 5).tolist(),
    }
    meta_path = os.path.splitext(out_path)[0] + ".json"
    with open(meta_path, "w") as fh:
        json.dump(meta, fh, indent=2)
    print(f"wrote {out_path}\nwrote {meta_path}")


if __name__ == "__main__":
    main()
