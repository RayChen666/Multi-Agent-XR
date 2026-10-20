import math
from typing import Dict, List, Optional

from tools.aabbCheck import build_aabb
from tools.orientation import facing_angle
from tools.spacing import MIN_CLEARANCE, ROOM_MARGIN


def around_placement_positions(
    movers: List[Dict],
    anchor: Dict,
    room_bounds: Optional[Dict] = None,
    gap: float = MIN_CLEARANCE,
    start_angle: float = 0.0,
) -> List[Dict]:
    """
    Place N mover objects evenly around an anchor object.

    Args:
        movers:      List of mover dicts, each optionally with a 'collision' key
                     containing 'width' and 'depth'.
        anchor:      Anchor object dict with 'position' and optionally 'collision'.
        room_bounds: Optional {'min': {'x','y','z'}, 'max': ...}.
                     Positions outside bounds are clamped inward.
        gap:         Clearance between mover bbox edge and anchor bbox edge (metres).
        start_angle: Offset in radians for the first mover, measured from the anchor's
                     front (its own facing, see tools.orientation).

    Returns:
        List of {'x', 'y', 'z', 'rotation_y'} dicts in same order as movers,
        or empty list if anchor has no usable position.
    """
    if not movers or not anchor:
        return []

    # Ring centre = the anchor's box centre (collision offset and yaw applied), not its
    # origin; a zero-size collision box (the user, skills._common.user_anchor) is a point.
    if anchor.get('collision'):
        a_min, a_max = build_aabb(anchor)
        ax, az = (a_min[0] + a_max[0]) / 2, (a_min[2] + a_max[2]) / 2
        anc_hw, anc_hd = (a_max[0] - a_min[0]) / 2, (a_max[2] - a_min[2]) / 2
    else:
        ax = float(anchor.get('position', {}).get('x', 0))
        az = float(anchor.get('position', {}).get('z', 0))
        anc_hw = anc_hd = 0.25

    mov_col = (movers[0].get('collision') or {})
    mov_hw = float(mov_col.get('width', 0.4)) / 2
    mov_hd = float(mov_col.get('depth', 0.4)) / 2

    # Conservative uniform radius: sum of max half-extents + gap
    radius = max(anc_hw, anc_hd) + max(mov_hw, mov_hd) + gap

    N = len(movers)
    angle_step = 2 * math.pi / N
    # Ring parameterisation: angle θ → (sin θ, -cos θ). θ = π - ry points along the
    # anchor's forward (sin ry, cos ry), so the first mover sits in front of the anchor.
    anchor_ry = float((anchor.get('rotation') or {}).get('y', 0) or 0)
    start_angle = start_angle + math.pi - anchor_ry

    x_margin = mov_hw + ROOM_MARGIN
    z_margin = mov_hd + ROOM_MARGIN

    positions = []
    for i in range(N):
        angle = start_angle + i * angle_step

        px = ax + radius * math.sin(angle)
        pz = az - radius * math.cos(angle)

        if room_bounds:
            px = max(room_bounds['min']['x'] + x_margin,
                     min(room_bounds['max']['x'] - x_margin, px))
            pz = max(room_bounds['min']['z'] + z_margin,
                     min(room_bounds['max']['z'] - z_margin, pz))

        # Face toward anchor (yaw convention lives in tools/orientation.py).
        rotation_y = facing_angle(px, pz, ax, az)

        positions.append({
            'x': round(px, 3),
            'y': -3.0,
            'z': round(pz, 3),
            'rotation_y': round(rotation_y, 4),
        })

    return positions
