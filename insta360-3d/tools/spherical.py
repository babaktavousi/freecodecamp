"""Spherical geometry helpers shared by the reconstruction pipeline.

Conventions (identical to the browser implementation in web/src/pipeline):
  * Equirectangular frames are 2:1, longitude increasing to the right,
    longitude 0 at the horizontal centre of the image, latitude +90 deg at the
    top row. The camera looks along +Z, +X is right, +Y is up.
  * Camera rotations are yaw-only. Insta360 X-series footage is horizon locked
    by FlowState stabilisation, so roll and pitch stay near zero; a small pitch
    offset is still estimated and applied as an image shift.
"""

import numpy as np


def equirect_directions(width, height):
    """(H, W, 3) unit ray directions for an equirectangular image."""
    i = np.arange(width, dtype=np.float32) + 0.5
    j = np.arange(height, dtype=np.float32) + 0.5
    lon = (i / width) * 2.0 * np.pi - np.pi
    lat = np.pi / 2.0 - (j / height) * np.pi
    lon, lat = np.meshgrid(lon, lat)
    cos_lat = np.cos(lat)
    return np.stack(
        [cos_lat * np.sin(lon), np.sin(lat), cos_lat * np.cos(lon)], axis=-1
    ).astype(np.float32)


def directions_to_uv(dirs, width, height):
    """Project unit directions onto equirectangular pixel coordinates."""
    x, y, z = dirs[..., 0], dirs[..., 1], dirs[..., 2]
    lon = np.arctan2(x, z)
    lat = np.arcsin(np.clip(y, -1.0, 1.0))
    u = (lon + np.pi) / (2.0 * np.pi) * width - 0.5
    v = (np.pi / 2.0 - lat) / np.pi * height - 0.5
    return u, v


def sample_bilinear(image, u, v):
    """Bilinear sample with horizontal wrap and vertical clamp.

    `image` is (H, W) or (H, W, C); returns the sampled values.
    """
    h, w = image.shape[:2]
    u0 = np.floor(u).astype(np.int32)
    v0 = np.floor(v).astype(np.int32)
    fu = (u - u0).astype(np.float32)
    fv = (v - v0).astype(np.float32)
    u0m = np.mod(u0, w)
    u1m = np.mod(u0 + 1, w)
    v0c = np.clip(v0, 0, h - 1)
    v1c = np.clip(v0 + 1, 0, h - 1)

    if image.ndim == 3:
        fu = fu[..., None]
        fv = fv[..., None]
    top = image[v0c, u0m] * (1 - fu) + image[v0c, u1m] * fu
    bot = image[v1c, u0m] * (1 - fu) + image[v1c, u1m] * fu
    return top * (1 - fv) + bot * fv


def yaw_matrix(angle):
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]], dtype=np.float32)


def box_blur(img, radius):
    """Separable box blur via summed-area style cumulative sums."""
    if radius <= 0:
        return img
    k = 2 * radius + 1
    pad = np.pad(img, ((radius, radius), (radius, radius)), mode="edge")
    cs = np.cumsum(pad, axis=0, dtype=np.float32)
    cs = np.concatenate([np.zeros((1, cs.shape[1]), np.float32), cs], axis=0)
    img = (cs[k:, :] - cs[:-k, :]) / k
    cs = np.cumsum(img, axis=1, dtype=np.float32)
    cs = np.concatenate([np.zeros((cs.shape[0], 1), np.float32), cs], axis=1)
    return (cs[:, k:] - cs[:, :-k]) / k


def high_pass(gray, radius=6):
    """Remove local brightness so matching is robust to exposure changes."""
    return gray - box_blur(gray, radius)


def to_gray(rgb):
    return (
        0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    ).astype(np.float32)


def estimate_yaw_pitch(gray_a, gray_b, max_pitch_px=4, band_deg=45.0, max_yaw_deg=30.0):
    """Estimate the (yaw, pitch) image shift taking frame A onto frame B.

    Brute-force SAD over a horizon band, limited to plausible turn rates: a
    walking capture cannot swing 180 degrees between keyframes, and repetitive
    architecture (tiles, columns, railings) puts convincing false minima all
    around the sphere if the search is left unbounded.
    """
    h, w = gray_a.shape
    band = int(round(h * band_deg / 180.0))
    r0 = max(1, h // 2 - band // 2)
    r1 = min(h - 1, h // 2 + band // 2)
    a = high_pass(gray_a, 4)[r0:r1]
    b = high_pass(gray_b, 4)[r0:r1]

    limit = max(2, int(round(w * max_yaw_deg / 360.0)))
    shifts = list(range(-limit, limit + 1))
    best = (np.inf, 0, 0)
    costs = {}
    for dy in range(-max_pitch_px, max_pitch_px + 1):
        bs = np.roll(b, -dy, axis=0)
        for dx in shifts:
            c = np.abs(a - np.roll(bs, -dx, axis=1)).mean()
            if dy == 0:
                costs[dx] = c
            if c < best[0]:
                best = (c, dx, dy)
    _, dx, dy = best

    # Sub-pixel yaw from a parabola through the neighbouring costs.
    if dy == 0 and (dx - 1) in costs and (dx + 1) in costs:
        c0, c1, c2 = costs[dx - 1], costs[dx], costs[dx + 1]
        denom = c0 - 2 * c1 + c2
        sub = 0.5 * (c0 - c2) / denom if abs(denom) > 1e-9 else 0.0
    else:
        sub = 0.0
    return dx + float(np.clip(sub, -1.0, 1.0)), float(dy)


def track_points(gray_a, gray_b, points, levels=4, radius=2, patch=4):
    """Coarse-to-fine block matching flow for a sparse set of points.

    `points` is (N, 2) in (x, y) pixels of the full-resolution image. Returns
    the matched positions in B and a matching cost per point.
    """
    pyr_a, pyr_b = [gray_a], [gray_b]
    for _ in range(levels - 1):
        pyr_a.append(_downsample2(pyr_a[-1]))
        pyr_b.append(_downsample2(pyr_b[-1]))

    flow = np.zeros_like(points, dtype=np.float32)
    for lvl in range(levels - 1, -1, -1):
        scale = 1.0 / (2 ** lvl)
        a, b = pyr_a[lvl], pyr_b[lvl]
        pa = points * scale
        pb = pa + flow * scale
        offs = np.arange(-patch, patch + 1, dtype=np.float32)
        oy, ox = np.meshgrid(offs, offs, indexing="ij")
        ox, oy = ox.ravel(), oy.ravel()

        ref = sample_bilinear(a, pa[:, 0:1] + ox[None, :], pa[:, 1:2] + oy[None, :])
        ref = ref - ref.mean(axis=1, keepdims=True)

        span = 2 * radius + 1
        grid = np.empty((span, span, len(points)), dtype=np.float32)
        for iy, dy in enumerate(range(-radius, radius + 1)):
            for ix, dx in enumerate(range(-radius, radius + 1)):
                cur = sample_bilinear(
                    b, pb[:, 0:1] + ox[None, :] + dx, pb[:, 1:2] + oy[None, :] + dy
                )
                cur = cur - cur.mean(axis=1, keepdims=True)
                grid[iy, ix] = np.abs(ref - cur).mean(axis=1)

        flat = grid.reshape(span * span, -1)
        best_idx = np.argmin(flat, axis=0)
        best_cost = flat[best_idx, np.arange(len(points))]
        iy, ix = np.divmod(best_idx, span)

        # Sub-pixel peak from a parabola through the neighbouring costs. Integer
        # matches alone leave ~0.5 px of noise per pair, which compounds into
        # metres of heading drift over a long walk.
        sub_x = _parabola_offset(grid, iy, ix, span, axis=1)
        sub_y = _parabola_offset(grid, iy, ix, span, axis=0)
        best_d = np.stack([(ix - radius) + sub_x, (iy - radius) + sub_y], axis=1)
        flow += best_d / scale
    return points + flow, best_cost


def _parabola_offset(grid, iy, ix, span, axis):
    """Sub-sample offset of the cost minimum along one axis, in [-0.5, 0.5]."""
    n = grid.shape[2]
    cols = np.arange(n)
    interior = (ix > 0) & (ix < span - 1) if axis == 1 else (iy > 0) & (iy < span - 1)
    lo = np.where(axis == 1, np.maximum(ix - 1, 0), np.maximum(iy - 1, 0))
    hi = np.where(axis == 1, np.minimum(ix + 1, span - 1), np.minimum(iy + 1, span - 1))
    if axis == 1:
        c0, c1, c2 = grid[iy, lo, cols], grid[iy, ix, cols], grid[iy, hi, cols]
    else:
        c0, c1, c2 = grid[lo, ix, cols], grid[iy, ix, cols], grid[hi, ix, cols]
    denom = c0 - 2 * c1 + c2
    safe = np.abs(denom) > 1e-9
    offset = np.divide(0.5 * (c0 - c2), denom, out=np.zeros_like(denom), where=safe)
    return np.where(interior & safe, np.clip(offset, -0.5, 0.5), 0.0)


def _downsample2(img):
    """Halve an image, low-passing first with a [1 2 1] kernel.

    Plain 2x2 averaging aliases fine repeating detail - tiling, railings, brick -
    into the coarse pyramid levels, and a bad coarse match cannot be recovered
    by the small search windows used further down.
    """
    h, w = img.shape
    horizontal = (
        0.25 * np.roll(img, 1, axis=1) + 0.5 * img + 0.25 * np.roll(img, -1, axis=1)
    )
    padded = np.pad(horizontal, ((1, 1), (0, 0)), mode="edge")
    blurred = 0.25 * padded[:-2] + 0.5 * padded[1:-1] + 0.25 * padded[2:]
    return blurred[: (h // 2) * 2 : 2, : (w // 2) * 2 : 2]


def _skew(v):
    return np.array(
        [[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]], dtype=np.float64
    )


def _expm_so3(w):
    theta = np.linalg.norm(w)
    if theta < 1e-12:
        return np.eye(3)
    k = _skew(w / theta)
    return np.eye(3) + np.sin(theta) * k + (1.0 - np.cos(theta)) * (k @ k)


def refine_pose(p, q, rotation, iters=6):
    """Joint relative rotation and translation direction from correspondences.

    `p` are unit directions in frame A, `q` the matching directions in frame B.
    Both satisfy the spherical epipolar constraint

        (p x (R q)) . t = 0

    where R maps B's camera frame into A's and t is the (unit) baseline
    direction in A's frame. Alternating between the closed-form null space for
    t and a small-angle least-squares update for R converges in a handful of
    passes from a yaw-only initialisation, and unlike the eight-point algorithm
    it needs no SVD, so the same routine ports directly to the browser.

    Returns (R, t, inlier_mask). t points from A towards B.
    """
    p = np.asarray(p, dtype=np.float64)
    q = np.asarray(q, dtype=np.float64)
    r = np.asarray(rotation, dtype=np.float64).copy()
    t = np.array([0.0, 0.0, 1.0])
    weights = np.ones(len(p))

    for _ in range(iters):
        qr = q @ r.T
        n = np.cross(p, qr)
        norm = np.linalg.norm(n, axis=1, keepdims=True)
        valid = norm[:, 0] > 1e-7
        nn = np.where(valid[:, None], n / np.maximum(norm, 1e-12), 0.0)

        m = (nn * weights[:, None]).T @ nn
        _, vecs = np.linalg.eigh(m)
        t_new = vecs[:, 0]
        if t_new @ t < 0 and np.linalg.norm(t) > 0:
            t_new = -t_new
        t = t_new

        residual = nn @ t
        scale = max(1.4826 * np.median(np.abs(residual[valid])), 1e-5)
        weights = valid / (1.0 + (residual / (3.0 * scale)) ** 2)

        # Small-angle rotation update for R <- exp(w) R. Differentiating
        # (p x (exp(w) R q)) . t gives J = t (p . qr) - p (qr . t); it is scaled
        # by the same 1/|p x qr| as the residual, since mixing the normalised
        # residual with an unnormalised Jacobian makes the iteration drift.
        inv_norm = np.where(valid, 1.0 / np.maximum(norm[:, 0], 1e-12), 0.0)
        jac = (
            t[None, :] * np.einsum("ij,ij->i", p, qr)[:, None] - p * (qr @ t)[:, None]
        ) * inv_norm[:, None]
        jw = jac * weights[:, None]
        hess = jw.T @ jac + np.eye(3) * 1e-9
        grad = jw.T @ residual
        try:
            omega = -np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            break
        omega = np.clip(omega, -0.2, 0.2)
        if np.linalg.norm(omega) < 1e-7:
            break
        r = _expm_so3(omega) @ r

    inliers = weights > 0.5
    return r.astype(np.float32), t.astype(np.float32), inliers


def warp_equirect(img, rotation):
    """Resample an equirectangular image at directions rotated by `rotation`.

    The result is what the camera would have seen with that rotation removed,
    so the tracker afterwards only has to explain parallax. Exact for any
    rotation, unlike the horizontal-shift approximation.
    """
    h, w = img.shape[:2]
    dirs = equirect_directions(w, h).reshape(-1, 3) @ np.asarray(rotation, dtype=np.float32).T
    u, v = directions_to_uv(dirs.reshape(h, w, 3), w, h)
    return sample_bilinear(img, u.astype(np.float32), v.astype(np.float32))


def pose_residuals(p, q, rotation, t):
    """Epipolar residuals |(p x (R q)) . t| for unit correspondences."""
    qr = np.asarray(q) @ np.asarray(rotation).T
    n = np.cross(p, qr)
    norm = np.linalg.norm(n, axis=1)
    ok = norm > 1e-7
    r = np.full(len(p), np.inf)
    r[ok] = np.abs((n[ok] / norm[ok, None]) @ t)
    return r


def triangulate_depths(p, q, t):
    """Depth along p, in units of the baseline, for pure-translation pairs.

    Camera B sits at C_b = C_a + b * t. A point at depth Z along p appears in B
    along q, so Z * p - b * t is parallel to q, giving
    Z / b = |q x t| / |q x p|.
    """
    qxp = np.cross(q, p)
    qxt = np.cross(q, t[None, :])
    denom = np.linalg.norm(qxp, axis=1)
    z = np.linalg.norm(qxt, axis=1) / np.maximum(denom, 1e-9)
    sign = np.sign(np.einsum("ij,ij->i", qxp, qxt))
    z = np.where(denom > 1e-6, z * sign, np.nan)
    return z


def estimate_floor(points, scale_hint):
    """Locate the floor in a reconstructed cloud.

    RANSAC alone struggles here: a reconstructed floor is a slab tens of
    centimetres thick rather than a surface, so a band tight enough to be
    selective locks onto one face of it while a band loose enough to contain it
    stops discriminating. The densest height layer is found first - in a walking
    capture the floor is the flattest, most-seen surface - and a plane is then
    fitted to that layer by reweighted least squares.

    Mirrors estimateFloor() in web/src/pipeline/geometry.js.

    Returns (normal, d, inliers) or None.
    """
    n = len(points)
    if n < 200:
        return None

    ys = points[:, 1]
    low, high = np.percentile(ys, [1, 99])
    span = float(high - low)
    if span <= 0:
        return None

    bin_size = max(scale_hint / 200.0, span / 400.0, 1e-6)
    bins = max(4, int(np.ceil(span / bin_size)))
    counts, edges = np.histogram(ys, bins=bins, range=(low, low + bins * bin_size))
    # Smooth over three bins so a single spike cannot win.
    smoothed = np.convolve(counts, np.ones(3), mode="same")
    level = float(edges[int(np.argmax(smoothed))] + bin_size * 0.5)

    band = max(scale_hint / 40.0, bin_size * 4.0)
    slab = points[np.abs(ys - level) <= band]
    if len(slab) < 300:
        return None

    normal = np.array([0.0, 1.0, 0.0])
    d = -level
    weights = np.ones(len(slab))
    for _ in range(5):
        total = weights.sum()
        if total < 1e-9:
            break
        centroid = (slab * weights[:, None]).sum(axis=0) / total
        centred = slab - centroid
        cov = (centred * weights[:, None]).T @ centred
        _, vecs = np.linalg.eigh(cov)
        normal = vecs[:, 0]
        if normal[1] < 0:
            normal = -normal
        d = float(-normal @ centroid)
        residual = np.abs(slab @ normal + d)
        scale = max(1.4826 * float(np.median(residual)), band / 20.0)
        weights = 1.0 / (1.0 + (residual / (2.0 * scale)) ** 2)

    # A floor that came out steeply tilted is not a floor.
    if normal[1] < 0.9:
        return None
    # Support is counted over the whole band: a reconstructed floor is a slab,
    # and a thinner test would reject good fits on noisier models.
    inliers = int((np.abs(slab @ normal + d) < band).sum())
    return normal.astype(np.float32), d, inliers
