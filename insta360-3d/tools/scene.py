"""Ground-truth scene used to render the synthetic 360 walkthrough sample.

Geometry is axis aligned and expressed in metres so that reconstructions can be
checked against exact known dimensions. +X right, +Y up, +Z forward (walking
direction), matching the convention used by the reconstruction pipeline.
"""

from dataclasses import dataclass, field

import numpy as np

# --- ground truth dimensions (metres) ------------------------------------
CORRIDOR_WIDTH = 2.40
CORRIDOR_HEIGHT = 2.70
CORRIDOR_START_Z = -1.0
CORRIDOR_END_Z = 12.0

ROOM_WIDTH = 8.00
ROOM_HEIGHT = 3.20
ROOM_END_Z = 20.0

DOOR_WIDTH = 0.90
DOOR_HEIGHT = 2.03

TABLE_TOP = 0.75
TABLE_SIZE = (1.60, 0.80)
CRATE_SIZE = 0.60
COLUMN_SIZE = 0.40

CAMERA_HEIGHT = 1.60

GROUND_TRUTH = {
    "corridor_width": CORRIDOR_WIDTH,
    "corridor_height": CORRIDOR_HEIGHT,
    "corridor_length": CORRIDOR_END_Z - CORRIDOR_START_Z,
    "room_width": ROOM_WIDTH,
    "room_height": ROOM_HEIGHT,
    "door_width": DOOR_WIDTH,
    "door_height": DOOR_HEIGHT,
    "table_height": TABLE_TOP,
    "table_length": TABLE_SIZE[0],
    "crate_edge": CRATE_SIZE,
    "column_edge": COLUMN_SIZE,
    "camera_height": CAMERA_HEIGHT,
}

AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


@dataclass
class Quad:
    """Axis aligned rectangle. `axis` is the constant axis of its plane."""

    axis: str
    plane: float
    u_range: tuple  # extent along the first non-constant axis
    v_range: tuple  # extent along the second non-constant axis
    material: str
    holes: list = field(default_factory=list)  # [(u0, u1, v0, v1), ...]

    @property
    def axis_i(self):
        return AXIS_INDEX[self.axis]

    @property
    def uv_axes(self):
        others = [i for i in range(3) if i != self.axis_i]
        return others[0], others[1]


def _box(cx, cz, sx, sy, sz, material, y0=0.0):
    """Six quads forming a solid box resting at height y0."""
    x0, x1 = cx - sx / 2, cx + sx / 2
    z0, z1 = cz - sz / 2, cz + sz / 2
    y1 = y0 + sy
    return [
        Quad("y", y1, (x0, x1), (z0, z1), material),
        Quad("y", y0, (x0, x1), (z0, z1), material),
        Quad("x", x0, (y0, y1), (z0, z1), material),
        Quad("x", x1, (y0, y1), (z0, z1), material),
        Quad("z", z0, (x0, x1), (y0, y1), material),
        Quad("z", z1, (x0, x1), (y0, y1), material),
    ]


def _window_recess(plane, hole, depth, sign, material="window"):
    """Backing panel and reveals for a hole cut in a wall of constant x.

    Without this a hole is an unlit void, which carries no texture and would
    be dropped by stereo matching rather than reconstructed.
    """
    v0, v1, z0, z1 = hole  # v = height, z = along the wall
    back = plane + sign * depth
    lo, hi = (plane, back) if sign > 0 else (back, plane)
    return [
        Quad("x", back, (v0, v1), (z0, z1), material),
        Quad("y", v0, (lo, hi), (z0, z1), "trim"),
        Quad("y", v1, (lo, hi), (z0, z1), "trim"),
        Quad("z", z0, (lo, hi), (v0, v1), "trim"),
        Quad("z", z1, (lo, hi), (v0, v1), "trim"),
    ]


def build_scene():
    """Corridor that opens through a doorway into a larger room."""
    hw = CORRIDOR_WIDTH / 2
    rw = ROOM_WIDTH / 2
    quads = []

    # Corridor: floor, ceiling, side walls.
    quads.append(Quad("y", 0.0, (-hw, hw), (CORRIDOR_START_Z, CORRIDOR_END_Z), "floor"))
    quads.append(
        Quad("y", CORRIDOR_HEIGHT, (-hw, hw), (CORRIDOR_START_Z, CORRIDOR_END_Z), "ceiling")
    )
    corridor_windows = [(0.95, 2.05, 3.6, 4.5), (0.95, 2.05, 7.6, 8.5)]
    for x in (-hw, hw):
        quads.append(
            Quad(
                "x",
                x,
                (0.0, CORRIDOR_HEIGHT),
                (CORRIDOR_START_Z, CORRIDOR_END_Z),
                "wall",
                holes=corridor_windows,
            )
        )
        for hole in corridor_windows:
            quads += _window_recess(x, hole, 0.30, 1.0 if x > 0 else -1.0)
    # Back wall behind the start of the walk.
    quads.append(Quad("z", CORRIDOR_START_Z, (-hw, hw), (0.0, CORRIDOR_HEIGHT), "wall"))

    # Partition wall at the end of the corridor, with the doorway cut out.
    door_hole = (-DOOR_WIDTH / 2, DOOR_WIDTH / 2, 0.0, DOOR_HEIGHT)
    quads.append(
        Quad("z", CORRIDOR_END_Z, (-rw, rw), (0.0, ROOM_HEIGHT), "wall", holes=[door_hole])
    )
    # Door reveal so the opening reads as a real thickness.
    quads.append(Quad("x", -DOOR_WIDTH / 2, (0.0, DOOR_HEIGHT), (CORRIDOR_END_Z, CORRIDOR_END_Z + 0.15), "trim"))
    quads.append(Quad("x", DOOR_WIDTH / 2, (0.0, DOOR_HEIGHT), (CORRIDOR_END_Z, CORRIDOR_END_Z + 0.15), "trim"))
    quads.append(Quad("y", DOOR_HEIGHT, (-DOOR_WIDTH / 2, DOOR_WIDTH / 2), (CORRIDOR_END_Z, CORRIDOR_END_Z + 0.15), "trim"))

    # Room: floor, ceiling, side and end walls.
    quads.append(Quad("y", 0.0, (-rw, rw), (CORRIDOR_END_Z, ROOM_END_Z), "floor"))
    quads.append(Quad("y", ROOM_HEIGHT, (-rw, rw), (CORRIDOR_END_Z, ROOM_END_Z), "ceiling"))
    room_window = (1.10, 2.30, 14.0, 16.0)
    for x in (-rw, rw):
        quads.append(
            Quad(
                "x",
                x,
                (0.0, ROOM_HEIGHT),
                (CORRIDOR_END_Z, ROOM_END_Z),
                "wall",
                holes=[room_window],
            )
        )
        quads += _window_recess(x, room_window, 0.30, 1.0 if x > 0 else -1.0)
    quads.append(Quad("z", ROOM_END_Z, (-rw, rw), (0.0, ROOM_HEIGHT), "wall"))

    # Furniture, giving the room parallax and measurable objects.
    quads += _box(-2.6, 15.0, TABLE_SIZE[0], 0.06, TABLE_SIZE[1], "table", y0=TABLE_TOP - 0.06)
    for leg_x, leg_z in [(-3.3, 14.7), (-3.3, 15.3), (-1.9, 14.7), (-1.9, 15.3)]:
        quads += _box(leg_x, leg_z, 0.06, TABLE_TOP - 0.06, 0.06, "table")
    quads += _box(2.4, 16.4, CRATE_SIZE, CRATE_SIZE, CRATE_SIZE, "crate")
    quads += _box(2.4, 16.4, CRATE_SIZE, CRATE_SIZE, CRATE_SIZE, "crate", y0=CRATE_SIZE)
    quads += _box(3.1, 17.3, CRATE_SIZE, CRATE_SIZE, CRATE_SIZE, "crate")
    quads += _box(-2.0, 18.6, COLUMN_SIZE, ROOM_HEIGHT, COLUMN_SIZE, "column")
    quads += _box(2.0, 18.6, COLUMN_SIZE, ROOM_HEIGHT, COLUMN_SIZE, "column")

    return quads


def camera_path(n_frames, fps, speed=1.25):
    """Hand-held walking path: horizon locked, with bob, sway and gentle yaw.

    Returns positions (n,3) and yaw angles (n,) in radians. Mirrors how an
    Insta360 X3 behaves with FlowState stabilisation: the horizon stays level,
    so only yaw varies meaningfully.
    """
    t = np.arange(n_frames, dtype=np.float64) / fps
    z = CORRIDOR_START_Z + 1.2 + speed * t
    x = 0.16 * np.sin(2 * np.pi * 0.45 * t)
    y = CAMERA_HEIGHT + 0.018 * np.sin(2 * np.pi * 1.9 * t)
    yaw = np.deg2rad(3.5) * np.sin(2 * np.pi * 0.32 * t)
    return np.stack([x, y, z], axis=1), yaw
