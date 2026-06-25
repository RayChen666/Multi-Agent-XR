import math
from typing import Dict, List, Optional


def around_placement_positions(
    movers: List[Dict],
    anchor: Dict,
    room_bounds: Optional[Dict] = None,
    gap: float = 0.15,
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
        start_angle: Angle in radians for the first mover (0 = in front of anchor, -Z).

    Returns:
        List of {'x', 'y', 'z', 'rotation_y'} dicts in same order as movers,
        or empty list if anchor has no usable position.
    """
    if not movers or not anchor:
        return []

    ax = float(anchor.get('position', {}).get('x', 0))
    az = float(anchor.get('position', {}).get('z', 0))

    anc_col = anchor.get('collision') or {}
    anc_hw = float(anc_col.get('width', 0.5)) / 2
    anc_hd = float(anc_col.get('depth', 0.5)) / 2

    mov_col = (movers[0].get('collision') or {})
    mov_hw = float(mov_col.get('width', 0.4)) / 2
    mov_hd = float(mov_col.get('depth', 0.4)) / 2

    # Conservative uniform radius: sum of max half-extents + gap
    radius = max(anc_hw, anc_hd) + max(mov_hw, mov_hd) + gap

    N = len(movers)
    angle_step = 2 * math.pi / N

    x_margin = mov_hw + 0.05
    z_margin = mov_hd + 0.05

    positions = []
    for i in range(N):
        angle = start_angle + i * angle_step

        # angle=0 → in front of anchor (-Z), angle=π/2 → right (+X)
        px = ax + radius * math.sin(angle)
        pz = az - radius * math.cos(angle)

        if room_bounds:
            px = max(room_bounds['min']['x'] + x_margin,
                     min(room_bounds['max']['x'] - x_margin, px))
            pz = max(room_bounds['min']['z'] + z_margin,
                     min(room_bounds['max']['z'] - z_margin, pz))

        # Face toward anchor.
        # Model forward is +Z: rotating by θ makes object face (sinθ, cosθ) in XZ.
        # To face direction (ax-px, az-pz): sin(θ)=ax-px, cos(θ)=az-pz → θ=atan2(ax-px, az-pz)
        rotation_y = math.atan2(ax - px, az - pz)

        positions.append({
            'x': round(px, 3),
            'y': -1.0,
            'z': round(pz, 3),
            'rotation_y': round(rotation_y, 4),
        })

    return positions
