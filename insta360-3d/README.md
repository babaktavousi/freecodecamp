# Insta360 → Point Cloud Studio

Turn a 360 video walkthrough into a 3D point cloud you can navigate like a CAD
model and measure real distances in — entirely in a web browser.

Shoot a walk through a building or down a street with an Insta360 X3 (or X4, X5,
ONE X2), export the clip as equirectangular MP4, drop it into the app, and it
reconstructs a coloured point cloud of the space. Then fly, orbit and walk
through it with the navigation you would expect from Navisworks or Recap, and
measure lengths point to point.

```
insta360-3d/
├── web/                 the browser app (no build step, no bundler)
│   ├── index.html
│   ├── src/pipeline/    video → poses → depth → points
│   ├── src/viewer/      rendering, navigation, measurement
│   └── tests/           node --test unit tests for the geometry
├── tools/               Python CLI: same pipeline, for long or high-res clips
└── samples/             a ready-to-run sample, and a fetcher for real footage
```

## Quick start

```bash
cd insta360-3d
npm start                      # static server, no dependencies
# open http://localhost:8080/web/
```

Click **Load the sample walkthrough**. A prebuilt point cloud opens straight
away, and the sample video is loaded into the left panel so you can press
**Build point cloud** and watch the reconstruction happen live.

Any static server works — `python3 -m http.server 8080` from this directory
serves it just as well. Opening `index.html` straight off disk will not work,
because ES modules and workers need a real HTTP origin.

## Using your own Insta360 footage

1. **Record a walk.** Keep moving — the reconstruction gets its depth from
   parallax between viewpoints, so a camera that stands still recovers nothing.
   Walk at a normal pace, avoid spinning on the spot, and prefer textured
   surfaces over blank white walls.
2. **Export equirectangular video.** An X3 records `.insv`, which is raw
   dual-fisheye and cannot be used directly. Open it in Insta360 Studio and
   export as a **360 / equirectangular** MP4 (any resolution; 5.7K is fine).
   Leave FlowState stabilisation on — the pipeline expects a level horizon.
3. **Load it** with the **Video** button, or drop it onto the window.
4. Set **Camera height above floor** to how high the camera actually was
   (about 1.6 m handheld at chest height, more on a selfie stick). This is what
   turns the model metric.
5. Press **Build point cloud**.

`samples/fetch_real_sample.py` finds and downloads freely licensed 360 videos
from Wikimedia Commons (including clips shot on Insta360 cameras), printing each
one's licence and author first:

```bash
python3 samples/fetch_real_sample.py --list
python3 samples/fetch_real_sample.py --index 3 --out samples/real_360.mp4
python3 samples/fetch_real_sample.py --youtube <url>   # via yt-dlp
```

## Navigating and measuring

Navigation follows the conventions of desktop model-review tools:

| | |
|---|---|
| **Orbit** | drag to rotate about the pivot; double-click to set the pivot |
| **Pan** | drag to slide the view; middle-drag works in any mode |
| **Zoom** | drag up/down, or wheel — the wheel zooms towards the cursor |
| **Look** | turn the camera in place |
| **Walk** | WASD at a fixed eye height, drag to look, Shift to hurry |
| **Fly** | WASD plus Q/E for down and up |

Right-drag orbits and middle-drag pans no matter which tool is selected, so you
can reframe without leaving the measuring tool.

**Measuring.** Pick **Distance** and click two points; clicks snap to real
reconstructed points rather than to an interpolated surface. **Path** measures a
multi-segment run and reports the cumulative length. Each measurement lists its
ΔX/ΔY/ΔZ components, the list exports to CSV, and units switch between metres,
centimetres, millimetres and feet & inches.

**Scale.** A single moving camera recovers shape exactly but size only up to one
global factor — this is inherent to monocular capture, not a shortcut taken
here. The app resolves it by finding the floor beneath the walked path and
applying the camera height you entered.

That automatic step is the weakest link in the chain, and it is worth knowing
why: a strongly repetitive floor (tiling, decking, paving) offers the stereo
matcher several equally good answers one tile apart, which biases the floor
towards the camera and the model towards being too large. Higher **Detail**
settings resolve the pattern and reduce it. When measurements matter, measure
one thing you actually know — a door is usually 2.03 m, a step 0.17 m — pick it
under **Calibrate scale**, type the true length, and the whole cloud is rescaled
to match. One calibration fixes every measurement in the model.

## How it works

Both the browser app and the Python CLI implement the same pipeline; the browser
runs the dense stage on the GPU, the CLI runs it in numpy.

1. **Keyframes.** Frames are sampled at a fixed time spacing. Spacing sets the
   stereo baseline: 0.3–0.5 s at walking pace gives roughly half a metre.
2. **Rotation.** A bounded search for the horizontal shift between frames gives
   a coarse yaw. The search is deliberately limited to plausible turn rates —
   repeating architecture puts convincing false matches all around the sphere.
3. **Correspondences.** The next frame is de-rotated by the current estimate, so
   the tracker only has to explain parallax, and a grid of points is matched
   with coarse-to-fine block matching with sub-pixel refinement.
4. **Relative pose.** Rotation and translation direction are solved jointly from
   the spherical epipolar constraint `(p × R q) · t = 0`, alternating a
   closed-form null space for `t` with a small-angle least-squares update for
   `R`, under Tukey reweighting. Several yaw hypotheses are tried and the one
   that explains the most correspondences wins; then the frame is re-tracked
   with the improved rotation and re-solved.
5. **Baselines.** Depth comes out in units of each pair's own baseline. Since
   scene depth changes slowly between neighbouring keyframes,
   `b(i+1)/b(i) = m(i)/m(i+1)` for median triangulated depth `m`, which tracks
   changes of walking pace around an otherwise constant stride.
6. **Dense depth.** A spherical plane sweep: every pixel's ray is placed at each
   of N inverse-depth hypotheses, the resulting 3D point is projected into the
   neighbouring keyframes, and photometric agreement is scored on high-pass
   images (so exposure changes do not matter). The best two neighbours are
   averaged, so one occlusion does not destroy a hypothesis.
7. **Filtering.** Points are kept only where a genuinely competing local minimum
   was beaten by a clear margin, where the image has local contrast, and away
   from the poles — which hold the selfie stick and the sky. What survives is
   then thinned by a grid: real surfaces are seen by several keyframes and fill
   their neighbourhood, while a mismatched pixel lands alone.
8. **Scale and levelling.** The floor is found from the strip directly beneath
   the walked path — the best reconstructed surface in any walking capture — by
   taking its densest height layer and fitting a plane to that layer. The cloud
   is rotated level on it, and the camera height you entered sets metric scale.

### Why not photogrammetry software?

COLMAP or Metashape will produce a denser and more accurate model from the same
footage, and if that is what you need, use them — this repository's `tools/`
output is a normal PLY that those tools' viewers read. What this project offers
instead is the whole loop in a browser tab, in a minute or two on a laptop GPU,
with measurement built in and nothing to install.

## Accuracy

`samples/walkthrough_360.webm` is a synthetic walkthrough rendered from a scene
with exactly known dimensions, so accuracy can be measured rather than
eyeballed:

```bash
python3 tools/evaluate.py --cloud samples/walkthrough_cloud.ply
```

It aligns the reconstructed camera path to the true one and reports trajectory
error, the residual scale factor, and each point's distance to the true surface.
Current numbers for the committed sample are in `samples/README.md`.

What limits accuracy, in rough order:

- **Depth quantisation.** Hypotheses are uniform in inverse depth, so far
  surfaces are sampled coarsely. More layers cost time linearly.
- **Baseline.** Distant geometry needs a longer baseline than nearby geometry.
  Wider keyframe spacing helps far surfaces and hurts near ones.
- **Drift.** Poses are chained frame to frame with no loop closure, so error
  accumulates over a long walk. Expect a few percent of the path length.
- **Texture.** Blank plaster, glass and sky produce no usable match and are
  filtered out rather than guessed at.

## Python CLI

For long clips, higher resolutions, or batch work:

```bash
pip install numpy imageio-ffmpeg
python3 tools/reconstruct.py --video samples/walkthrough_360.webm \
    --out cloud.ply --sweep-width 1024 --layers 192
```

Useful flags: `--stride` (keyframe spacing in video frames), `--sweep-width`,
`--layers`, `--neighbours`, `--min-confidence`, `--camera-height`,
`--start`/`--duration`. The output PLY opens in the web app, CloudCompare,
MeshLab and Blender.

`tools/render_sample.py` re-renders the synthetic sample at any resolution or
length, and `tools/evaluate.py` scores a reconstruction against it.

## Tests

```bash
npm test        # node --test: geometry, pose solving, tracking, floor fitting
```

The unit tests cover the parts where a sign error is invisible until the whole
model is wrong: the equirectangular projection conventions, the pose solver
(against synthetic correspondences, clean and with outliers), sub-pixel
tracking, and the floor-plane fit.

## Requirements

Viewing and measuring a point cloud needs WebGL2 — Chrome, Edge, Firefox and
Safari 15+ on the desktop, and most recent phones. Reconstruction additionally
needs `EXT_color_buffer_float` for the depth sweep, and enough GPU to enjoy it;
the app says so plainly if either is missing.

Nothing is uploaded anywhere: video decoding, motion estimation and the depth
sweep all run locally in the page, so footage of a client's building never
leaves the machine.

## Licence and third-party code

This directory follows the licence of the repository it sits in
(BSD-3-Clause, see `../LICENSE.md`).

`web/vendor/three/` holds three.js r180 (MIT, © three.js authors) — the build
and `PLYLoader`, copied in rather than fetched from a CDN so the app runs
offline and can be audited. Nothing else is bundled; the Python tools need only
numpy and ffmpeg.
