import sys
from pathlib import Path
from typing import Dict, Optional
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.aabbCheck import build_aabb, world_collision

"""
"On" placement: rest a mover on top of a support ("put the lamp on the table").

Vertical: the mover's AABB bottom sits exactly on the support's AABB top.
build_aabb puts an object's bottom at position.y + offset.y - height/2, so
    position.y = support_top - (offset.y - height/2).
Horizontal: the mover's AABB centre goes over the support's AABB centre, or over
`point` (e.g. the gaze hit on the support) when given, clamped so the mover's
footprint stays on the support's top.

The result touches but does not penetrate the support, which aabbCheck already
treats as no collision (overlaps below COLLISION_TOLERANCE are ignored).
"""


def _offset(obj: Dict, axis: str) -> float:
    return world_collision(obj)['offset'][axis]


def stack_placement(mover: Dict, support: Dict, point: Optional[Dict] = None) -> Dict:
    """
    Args:
        mover:   object dict with 'collision' (position optional).
        support: object dict with 'position' and 'collision'.
        point:   optional {'x','z'} on the support to aim for.

    Returns:
        {'x', 'y', 'z'} position for the mover.

    Raises:
        ValueError: either object lacks 'collision'.
    """
    if not mover.get('collision') or not support.get('collision'):
        raise ValueError("Both mover and support require a 'collision' field")

    s_min, s_max = build_aabb(support)
    col = world_collision(mover)
    half = {'x': col['width'] / 2, 'z': col['depth'] / 2}

    pos = {}
    for i, axis in ((0, 'x'), (2, 'z')):
        centre = (s_min[i] + s_max[i]) / 2
        if point and axis in point:
            centre = float(point[axis])
        lo, hi = s_min[i] + half[axis], s_max[i] - half[axis]
        # Mover wider than the support on this axis: centre it (postcondition flags it).
        centre = (s_min[i] + s_max[i]) / 2 if lo > hi else min(max(centre, lo), hi)
        pos[axis] = round(centre - _offset(mover, axis), 3)

    pos['y'] = round(s_max[1] - (_offset(mover, 'y') - col['height'] / 2), 3)
    return {'x': pos['x'], 'y': pos['y'], 'z': pos['z']}


def resting_gap(mover: Dict, support: Dict) -> float:
    """mover bottom − support top (0 = resting; > 0 floating; < 0 sunk into it)."""
    m_min, _ = build_aabb(mover)
    _, s_max = build_aabb(support)
    return round(m_min[1] - s_max[1], 3)


def footprint_fits(mover: Dict, support: Dict) -> bool:
    """True when the mover's footprint is no larger than the support's top."""
    mc, sc = world_collision(mover), world_collision(support)
    return mc['width'] <= sc['width'] + 1e-6 and mc['depth'] <= sc['depth'] + 1e-6


def centre_over_support(mover: Dict, support: Dict) -> bool:
    """True when the mover's AABB centre (x, z) lies within the support's top."""
    m_min, m_max = build_aabb(mover)
    s_min, s_max = build_aabb(support)
    return all(s_min[i] <= (m_min[i] + m_max[i]) / 2 <= s_max[i] for i in (0, 2))
