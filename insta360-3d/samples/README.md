# Samples

## `walkthrough_360.webm` — the built-in test clip

A synthetic equirectangular 360 walkthrough: down a corridor, through a doorway,
into a larger room. 1920 × 960 at 12 fps, 12.5 seconds, a 1.25 m/s walk with the
head bob, sway and gentle yaw of a hand-held capture — and a level horizon,
matching what an Insta360 X-series camera produces with FlowState stabilisation
after an equirectangular export.

It is rendered rather than filmed for one reason: **every dimension is known
exactly**, so reconstruction accuracy and the measuring tools can be checked
against truth instead of judged by eye. `walkthrough_360.json` carries the exact
camera path and the scene dimensions.

VP9/WebM because every current browser decodes it; some Chromium builds ship
without H.264. The pipeline itself takes any 2:1 equirectangular video, MP4
included.

Re-render it at any size or length:

```bash
python3 tools/render_sample.py --out samples/walkthrough_360.webm --width 2560 --frames 200
```

### Ground truth for hand-checking the measure tool

| What | True size |
|---|---|
| Corridor width | 2.40 m |
| Corridor height (floor to ceiling) | 2.70 m |
| Doorway | 0.90 m wide × 2.03 m high |
| Room width | 8.00 m |
| Room height | 3.20 m |
| Table top height / length | 0.75 m / 1.60 m |
| Crates | 0.60 m cubes |
| Columns | 0.40 m square, full height |
| Camera height | 1.60 m |
| Walk length | 15.9 m |

Windows are recessed 0.30 m into the walls, so the bright glazing panels sit at
±1.50 m from the corridor centreline while the wall faces are at ±1.20 m — worth
knowing before concluding the corridor came out too wide.

## `walkthrough_cloud.ply` — the prebuilt result

The reconstruction of that clip, so the app has something to open instantly.
Binary PLY with colour, opens in CloudCompare, MeshLab and Blender too.

Rebuild it with:

```bash
python3 tools/reconstruct.py --video samples/walkthrough_360.webm \
    --out samples/walkthrough_cloud.ply --sweep-width 768 --layers 192
python3 tools/evaluate.py --cloud samples/walkthrough_cloud.ply
```

### How accurate the committed cloud is

600,000 points from 38 keyframes, scored against the exact scene it was rendered
from:

| | |
|---|---|
| Camera path error, mean / max | 0.20 m / 0.80 m over a 15.9 m walk |
| Point distance to the true surface, median | 0.17 m |
| … 90th percentile | 0.56 m |
| Points within 0.10 m of a true surface | 40% |
| Residual scale after automatic floor scaling | 1.36× too large |

The shape is recovered well; the automatic scale is what misses, for the reason
given under **Scale** in the main README — this scene's floor is regularly
tiled. Calibrating against one known length (the doorway is 2.03 m) removes that
1.36× and brings measurements to within a few centimetres, which is the workflow
to use whenever a number matters.

## Real Insta360 footage

`fetch_real_sample.py` searches Wikimedia Commons for freely licensed 360 video
and downloads what you pick, writing the licence and author alongside it:

```bash
python3 samples/fetch_real_sample.py --list           # see what is available
python3 samples/fetch_real_sample.py --index 2        # download entry 2
python3 samples/fetch_real_sample.py --youtube <url>  # anything yt-dlp handles
```

Nothing is downloaded until you name an entry, and every candidate's licence is
printed first. Not every 360 video is a walking capture — a clip shot from a
tripod has no parallax and cannot be reconstructed, whatever the pipeline does.

**Straight from your own camera:** an X3/X4/X5 records `.insv`, which is raw
dual-fisheye plus metadata and cannot be used directly. Open the file in
Insta360 Studio (free, Windows and macOS) and export **360 / equirectangular**
MP4. Insta360's own sample galleries and community sites such as 360Rumors also
publish downloadable X-series clips; check each file's terms before using it for
anything beyond private testing.

### What makes a clip reconstruct well

- **Keep walking.** Depth comes from parallax between viewpoints. Standing still
  or spinning on the spot gives none, and nothing can be recovered.
- **Texture.** Plaster, brick, carpet and asphalt match well. Plain white walls,
  glass and sky do not, and are dropped rather than guessed at.
- **Even pace.** Baselines are recovered from the footage, but a steady walk
  keeps the estimate well conditioned.
- **Light.** Motion blur in dim interiors destroys the fine detail the matcher
  needs more than noise does.
