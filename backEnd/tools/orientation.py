import math

"""
Single source of the scene's yaw convention. Every placement tool, skill and
agent that turns an object toward a point must go through facing_angle().

Model forward is +Z: rotating by θ about Y makes an object face (sin θ, cos θ)
in XZ. To face direction (tx - px, tz - pz): sin θ = tx - px, cos θ = tz - pz,
so θ = atan2(tx - px, tz - pz).
"""


def facing_angle(px: float, pz: float, tx: float, tz: float) -> float:
    """rotation_y (radians) that turns an object at (px, pz) to face (tx, tz)."""
    return math.atan2(tx - px, tz - pz)


def angle_diff(a: float, b: float) -> float:
    """Smallest absolute difference between two angles, in [0, π]."""
    return abs((a - b + math.pi) % (2 * math.pi) - math.pi)


# ---------------------------------------------------------------------------
# Object-relative directions ("in front of X", "left of X").
#   front / back : X's own facing (intrinsic) — independent of where the user stands.
#   left / right : as seen by an observer standing in front of X, facing it.
# With rotation_y = 0 (X faces +Z): front = +Z, back = -Z, left = -X, right = +X.
# ---------------------------------------------------------------------------

DIRECTION_ALIASES = {'front': 'front', 'in front': 'front',
                     'back': 'back', 'behind': 'back',
                     'left': 'left', 'right': 'right'}


def forward_vector(rotation_y: float):
    """(x, z) unit vector an object with this yaw faces."""
    return math.sin(rotation_y), math.cos(rotation_y)


def direction_vector(rotation_y: float, direction: str):
    """
    (x, z) unit vector for `direction` relative to an object with this yaw.
    The observer facing X looks along -forward; their right is (-d.z, d.x)
    for look direction d, i.e. (cos ry, -sin ry).
    """
    key = DIRECTION_ALIASES.get(direction)
    if key is None:
        raise ValueError(f"Unknown direction '{direction}', expected one of {sorted(DIRECTION_ALIASES)}")
    fx, fz = forward_vector(rotation_y)
    return {
        'front': (fx, fz),
        'back':  (-fx, -fz),
        'right': (fz, -fx),
        'left':  (-fz, fx),
    }[key]


def direction_axis(anchor: dict, direction: str):
    """
    Snap `direction` of `anchor` to the nearest world axis: ('x' | 'z', +1 | -1).
    Placement tools work on axis-aligned bounding boxes, and scene objects sit at
    0 / ±π/2 / π, so snapping loses nothing in practice.
    """
    ry = float((anchor.get('rotation') or {}).get('y', 0) or 0)
    vx, vz = direction_vector(ry, direction)
    if abs(vx) > abs(vz):
        return 'x', (1 if vx > 0 else -1)
    return 'z', (1 if vz > 0 else -1)


# ---------------------------------------------------------------------------
# User-relative directions ("move it left", "push it forward") — no reference object.
# The user's yaw uses the same convention as objects: they look along (sin yaw, cos yaw).
# The frontend sends it as user_position.rotation.y (webXR/commandBridge.js getUserRotation).
# ---------------------------------------------------------------------------

# Spawn view looks down -Z, which is yaw = π in this convention (not 0).
DEFAULT_USER_YAW = math.pi

EGOCENTRIC_ALIASES = {'forward': 'forward', 'front': 'forward', 'ahead': 'forward',
                      'backward': 'backward', 'back': 'backward', 'backwards': 'backward',
                      'left': 'left', 'right': 'right'}


def user_direction_vector(yaw: float, direction: str):
    """
    (x, z) unit vector for `direction` as the user sees it. Looking along d, the
    user's right is (-d.z, d.x): for the spawn view (d = -Z) that is +X.
    """
    key = EGOCENTRIC_ALIASES.get(direction)
    if key is None:
        raise ValueError(f"Unknown direction '{direction}', expected one of {sorted(EGOCENTRIC_ALIASES)}")
    dx, dz = forward_vector(yaw)
    return {
        'forward':  (dx, dz),
        'backward': (-dx, -dz),
        'right':    (-dz, dx),
        'left':     (dz, -dx),
    }[key]


def user_yaw(user_position) -> float:
    """Yaw from a backend user_position dict; DEFAULT_USER_YAW when absent."""
    rotation = (user_position or {}).get('rotation') or {}
    yaw = rotation.get('y')
    return DEFAULT_USER_YAW if yaw is None else float(yaw)
