import math
import sys
from pathlib import Path
from typing import Dict, Optional
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.orientation import direction_axis, facing_angle
from tools.aabbCheck import build_aabb, clamp_into_room, world_collision
from tools.spacing import FACING_GAP


def facing_placement(
    target: Dict,
    mover_collision: Optional[Dict] = None,
    room_bounds: Optional[Dict] = None,
    gap: float = FACING_GAP,
) -> Dict:
    """
    Compute position + rotation for a mover placed in front of target, facing it.

    "In front" = the side target itself faces (tools.orientation.direction_axis),
    e.g. the +Z side for rotation_y = 0. The mover is centered on target across it.

    Args:
        target:          Target object dict with 'position' and optionally 'collision'.
        mover_collision: Mover's collision dict with 'width'/'depth'. None uses defaults.
        room_bounds:     Optional {'min': {'x','y','z'}, 'max': ...} for clamping.
        gap:             Clear space between target front edge and mover back edge (metres).

    Returns:
        {'x', 'y', 'z', 'rotation_y'}
    """
    tx = float(target.get('position', {}).get('x', 0))
    tz = float(target.get('position', {}).get('z', 0))

    # Target's real (rotated, offset) box; defaults when it has no collision data.
    if target.get('collision'):
        t_min, t_max = build_aabb(target)
        t_centre = {'x': (t_min[0] + t_max[0]) / 2, 'z': (t_min[2] + t_max[2]) / 2}
        t_half = {'x': (t_max[0] - t_min[0]) / 2, 'z': (t_max[2] - t_min[2]) / 2}
    else:
        t_centre, t_half = {'x': tx, 'z': tz}, {'x': 0.25, 'z': 0.25}

    # Place on the target's front side, `gap` clear of its edge. The mover will look
    # back at the target, so size it with that yaw (it faces along -front).
    axis, sign = direction_axis(target, 'front')
    look = (-sign, 0.0) if axis == 'x' else (0.0, -sign)
    mover = {'collision': mover_collision or {'width': 0.4, 'height': 0.8, 'depth': 0.4},
             'rotation': {'y': math.atan2(look[0], look[1])}}
    m_col = world_collision(mover)
    m_half = {'x': m_col['width'] / 2, 'z': m_col['depth'] / 2}

    centre = dict(t_centre)
    centre[axis] += sign * (t_half[axis] + gap + m_half[axis])
    position = {'x': centre['x'] - m_col['offset']['x'], 'y': -3.0, 'z': centre['z'] - m_col['offset']['z']}
    if room_bounds:
        position = clamp_into_room(mover, position, room_bounds)
    px, pz = position['x'], position['z']

    # Face toward target (yaw convention lives in tools/orientation.py).
    rotation_y = facing_angle(px, pz, tx, tz)

    return {
        'x': round(px, 3),
        'y': -3.0,
        'z': round(pz, 3),
        'rotation_y': round(rotation_y, 4),
    }
