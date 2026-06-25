import math
from typing import Dict, Optional


def facing_placement(
    target: Dict,
    mover_collision: Optional[Dict] = None,
    room_bounds: Optional[Dict] = None,
    gap: float = 0.5,
) -> Dict:
    """
    Compute position + rotation for a mover placed in front of target, facing it.

    "In front" = the +Z side of target (the side facing toward the user at z≈0).

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

    tgt_col = target.get('collision') or {}
    tgt_hd = float(tgt_col.get('depth', 0.5)) / 2

    mov_col = mover_collision or {}
    mov_hw = float(mov_col.get('width', 0.4)) / 2
    mov_hd = float(mov_col.get('depth', 0.4)) / 2

    # Place on the +Z side of target (toward the user)
    px = tx
    pz = tz + tgt_hd + gap + mov_hd

    if room_bounds:
        px = max(room_bounds['min']['x'] + mov_hw + 0.05,
                 min(room_bounds['max']['x'] - mov_hw - 0.05, px))
        pz = max(room_bounds['min']['z'] + mov_hd + 0.05,
                 min(room_bounds['max']['z'] - mov_hd - 0.05, pz))

    # Face toward target. Model forward is +Z: θ = atan2(tx-px, tz-pz)
    rotation_y = math.atan2(tx - px, tz - pz)

    return {
        'x': round(px, 3),
        'y': -1.0,
        'z': round(pz, 3),
        'rotation_y': round(rotation_y, 4),
    }
