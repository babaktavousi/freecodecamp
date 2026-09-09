#!/usr/bin/env python3
"""Triage a 360 clip before spending an hour reconstructing it.

Reconstruction needs three things, and a clip that lacks any one of them cannot
be rescued by settings:

  1. a true 2:1 equirectangular frame     (not dual-fisheye, not letterboxed)
  2. one continuous shot                  (cuts and dissolves break pose chaining)
  3. a camera that keeps moving           (parallax is where depth comes from)

This checks all three and names the best usable segment, so you reconstruct the
right seconds of the right clip instead of discovering the problem afterwards.

    python3 tools/check_clip.py --video my_walk.mp4

Every published 360 video is not a walkthrough. Tripod shots, edited montages
and drone footage are all common, and all fail here for reasons no amount of
tuning fixes.
"""

import argparse
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reconstruct import find_ffmpeg, probe_size  # noqa: E402

PROXY_WIDTH = 320


def decode_proxy(ffmpeg, path, width, height, start, duration):
    """Decode the whole clip small and grey, for analysis only."""
    cmd = [ffmpeg, "-loglevel", "error"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", path]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-vf", f"scale={width}:{height}", "-vsync", "0",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    n = width * height * 3
    frames = []
    while True:
        raw = proc.stdout.read(n)
        if len(raw) < n:
            break
        frames.append(
            np.frombuffer(raw, np.uint8).reshape(height, width, 3).mean(2)
        )
    proc.stdout.close()
    proc.wait()
    return frames


def probe_fps(ffmpeg, path):
    out = subprocess.run([ffmpeg, "-i", path], capture_output=True, text=True).stderr
    for line in out.splitlines():
        if "Video:" in line:
            for token in line.split(","):
                if "fps" in token:
                    try:
                        return float(token.strip().split(" ")[0])
                    except ValueError:
                        pass
    return 25.0


def find_static_band(frames, height):
    """Rows that never change are a logo bar, not scene: they break the projection.

    Returned as the first row of the trailing static band, or None.
    """
    if len(frames) < 4:
        return None
    # Sample the middle only: black intro and outro frames are static everywhere
    # and would hide the band by making the whole frame look unchanging.
    lo, hi = int(len(frames) * 0.2), int(len(frames) * 0.8)
    middle = frames[lo:hi] or frames
    stack = np.stack(middle[:: max(1, len(middle) // 8)][:8])
    row_std = stack.std(0).mean(1)
    static = row_std < 1.0
    y = height - 1
    if not static[y]:
        return None
    while y > height // 2 and static[y - 1]:
        y -= 1
    return y


def analyse(frames, fps):
    """Per-frame sharpness and inter-frame motion, in the horizon band."""
    h = frames[0].shape[0]
    band = slice(int(h * 0.35), int(h * 0.75))
    sharp = np.array([
        np.diff(f[band], axis=1).var() + np.diff(f[band], axis=0).var()
        for f in frames
    ])
    motion = np.array([
        np.abs(frames[i][band] - frames[i - 1][band]).mean()
        for i in range(1, len(frames))
    ])
    return sharp, motion


def usable_segments(sharp, motion, fps, min_seconds):
    """Runs that are both in focus and actually moving.

    Blur marks dissolves; near-zero motion marks a camera that has stopped. Both
    are useless, for different reasons.

    Thresholds are fractions of the clip's own median rather than percentile
    ranks: a rank always rejects a fixed share of frames, which would chop a
    perfectly steady walk into fragments at its own natural variation.
    """
    sharp_ok = sharp > 0.5 * np.median(sharp)
    move_ok = np.concatenate([[False], motion > max(1.5, 0.35 * np.median(motion))])
    ok = sharp_ok & move_ok
    runs, start = [], None
    for i, good in enumerate(ok):
        if good and start is None:
            start = i
        elif not good and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(ok)))
    least = int(min_seconds * fps)
    return [(a, b) for a, b in runs if b - a >= least]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--video", required=True)
    ap.add_argument("--min-segment", type=float, default=6.0,
                    help="shortest segment worth reporting, seconds")
    ap.add_argument("--start", type=float)
    ap.add_argument("--duration", type=float)
    args = ap.parse_args()

    ffmpeg = find_ffmpeg()
    width, height = probe_size(ffmpeg, args.video)
    fps = probe_fps(ffmpeg, args.video)
    ratio = width / height
    print(f"{os.path.basename(args.video)}: {width}x{height}  ratio {ratio:.3f}  {fps:.1f} fps")

    ph = max(2, int(round(PROXY_WIDTH / ratio)) // 2 * 2)
    frames = decode_proxy(ffmpeg, args.video, PROXY_WIDTH, ph, args.start, args.duration)
    if len(frames) < 8:
        raise SystemExit("could not decode enough frames to judge this clip")
    print(f"analysed {len(frames)} frames ({len(frames) / fps:.1f} s)")

    problems, notes = [], []

    # 1. projection
    band_row = find_static_band(frames, ph)
    crop = None
    if band_row is not None:
        content = int(round(band_row / ph * height))
        notes.append(
            f"a static band covers the bottom {height - content} px - "
            f"a logo bar, not scene content"
        )
        target = int(round(width / 2))
        if abs(content - target) / target < 0.15 or content > target:
            crop = target
            notes.append(f"crop to {width}x{target} to recover a 2:1 frame: "
                         f"-vf crop={width}:{target}:0:0")
    if abs(ratio - 2.0) > 0.02 and crop is None:
        problems.append(
            f"not 2:1 equirectangular (ratio {ratio:.2f}). Dual-fisheye .insv and "
            "letterboxed exports both look like this; re-export as 360/equirectangular."
        )

    # 2 and 3. continuity and motion
    sharp, motion = analyse(frames, fps)
    med_motion = float(np.median(motion))
    print(f"motion: median {med_motion:.1f}, p90 {np.percentile(motion, 90):.1f}  "
          f"(per-frame greyscale difference)")

    if med_motion < 1.5:
        problems.append(
            "the camera barely moves. Depth comes from parallax between "
            "viewpoints, so a tripod or hand-held-in-place clip yields nothing."
        )

    segments = usable_segments(sharp, motion, fps, args.min_segment)
    offset = args.start or 0.0
    if not segments:
        problems.append(
            f"no continuous moving segment of at least {args.min_segment:.0f}s. "
            "Blurred dissolves and stops between viewpoints mean this is an "
            "edited montage rather than one walk."
        )
    else:
        print(f"\n{len(segments)} usable segment(s), longest first:")
        for a, b in sorted(segments, key=lambda r: r[0] - r[1])[:5]:
            print(f"  {offset + a / fps:6.1f}s - {offset + b / fps:6.1f}s "
                  f"({(b - a) / fps:.1f}s, motion {motion[a:b].mean():.1f})")

    for note in notes:
        print(f"\nnote: {note}")

    print()
    if problems:
        print("NOT USABLE:")
        for p in problems:
            print(f"  - {p}")
        return 1

    best = max(segments, key=lambda r: r[1] - r[0])
    start_s = offset + best[0] / fps
    length = (best[1] - best[0]) / fps
    best_motion = float(motion[best[0]:best[1]].mean())
    if best_motion < 6.0:
        print(f"Caution: the best segment moves slowly (motion {best_motion:.1f}; a "
              "brisk walk reads around 9). Weak parallax gives noisy depth, and\n"
              "two segments that both look continuous may still be different "
              "places in an edited clip - this check cannot tell.\n")
    print("WORTH TRYING. Reconstruct the best segment with:")
    if crop:
        print(f"  ffmpeg -ss {start_s:.1f} -t {length:.1f} -i {args.video} \\")
        print(f"      -vf crop={width}:{crop}:0:0,scale=1920:960 clip.webm")
        print("  python3 tools/reconstruct.py --video clip.webm --out cloud.ply")
    else:
        print(f"  python3 tools/reconstruct.py --video {args.video} \\")
        print(f"      --start {start_s:.1f} --duration {length:.1f} --out cloud.ply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
