import sys
from pathlib import Path
from typing import Dict, Optional
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.aabbCheck import build_aabb

"""
Edge-to-edge distance placement: solves commands like
"move the sink 1.5 meters back of the table" by tracking each object's AABB
(from tools.aabbCheck.build_aabb, which already accounts for collision
width/height/depth + geometric-center offset) and positioning the mover so
that abs(bound_mover - bound_anchor) along the travel axis equals the
requested distance, edge to edge (not center to center).

Direction convention matches SceneAgent's coordinate system:
X-axis: left(-) to right(+), Z-axis: front(-) to back(+).
"""

AXIS_INDEX = {'x': 0, 'y': 1, 'z': 2}

# direction -> (axis, sign). sign=+1 places the mover on the anchor's max
# side (back/right), sign=-1 on the anchor's min side (front/left).
DIRECTIONS = {
    'back': ('z', +1),
    'behind': ('z', +1),
    'front': ('z', -1),
    'left': ('x', -1),
    'right': ('x', +1),
}


def distance_placement(
    mover: Dict,
    anchor: Dict,
    distance: float,
    direction: str,
    room_bounds: Optional[Dict] = None,
) -> Dict:
    """
    Compute a position for `mover` such that its AABB edge sits exactly
    `distance` meters from `anchor`'s AABB edge, along the axis implied by
    `direction` ('back'/'front'/'left'/'right' of anchor). The cross axis is
    centered on the anchor; y is left untouched.

    Args:
        mover:       Object dict with 'position' and 'collision' (width/height/depth/offset).
        anchor:      Object dict with 'position' and 'collision'.
        distance:    Requested edge-to-edge gap in meters (>= 0).
        direction:   One of 'back', 'behind', 'front', 'left', 'right'.
        room_bounds: Optional {'min': {'x','y','z'}, 'max': ...} for clamping.

    Returns:
        {'x': float, 'y': float, 'z': float} — new position for mover.

    Raises:
        ValueError: unknown direction, or either object lacks 'collision'.
    """
    if direction not in DIRECTIONS:
        raise ValueError(f"Unknown direction '{direction}', expected one of {sorted(DIRECTIONS)}")
    if not mover.get('collision') or not anchor.get('collision'):
        raise ValueError("Both mover and anchor require a 'collision' field to compute bounding boxes")

    axis, sign = DIRECTIONS[direction]
    ai = AXIS_INDEX[axis]

    anchor_min, anchor_max = build_aabb(anchor)

    mov_col = mover['collision']
    mov_offset = mov_col.get('offset', {'x': 0, 'y': 0, 'z': 0})
    half_extent = {'x': mov_col['width'], 'y': mov_col['height'], 'z': mov_col['depth']}[axis] / 2

    if sign > 0:
        # mover's near edge sits `distance` past anchor's max edge on this axis
        target_center = anchor_max[ai] + distance + half_extent
    else:
        target_center = anchor_min[ai] - distance - half_extent

    # AABB center -> position (undo the collision offset build_aabb applies).
    new_axis_val = target_center - mov_offset.get(axis, 0)

    cross_axis = 'x' if axis == 'z' else 'z'
    anchor_pos = anchor.get('position', {})
    cross_val = float(anchor_pos.get(cross_axis, 0)) - mov_offset.get(cross_axis, 0)

    position = dict(mover.get('position', {}))
    position[axis] = round(new_axis_val, 3)
    position[cross_axis] = round(cross_val, 3)

    if room_bounds:
        mov_hw = mov_col['width'] / 2
        mov_hd = mov_col['depth'] / 2
        position['x'] = max(room_bounds['min']['x'] + mov_hw + 0.05,
                             min(room_bounds['max']['x'] - mov_hw - 0.05, position['x']))
        position['z'] = max(room_bounds['min']['z'] + mov_hd + 0.05,
                             min(room_bounds['max']['z'] - mov_hd - 0.05, position['z']))

    return position


def measured_edge_distance(mover: Dict, anchor: Dict, direction: str) -> float:
    """
    Given mover/anchor with positions already set, return the actual
    edge-to-edge gap along `direction`'s axis: bound_mover - bound_anchor
    signed so a positive value means the constraint is satisfied on the
    requested side. Used to verify abs(bound_mover - bound_anchor) == distance
    after a placement (or LLM-proposed move) has been applied.
    """
    if direction not in DIRECTIONS:
        raise ValueError(f"Unknown direction '{direction}', expected one of {sorted(DIRECTIONS)}")

    axis, sign = DIRECTIONS[direction]
    ai = AXIS_INDEX[axis]

    mover_min, mover_max = build_aabb(mover)
    anchor_min, anchor_max = build_aabb(anchor)

    if sign > 0:
        return round(mover_min[ai] - anchor_max[ai], 3)
    return round(anchor_min[ai] - mover_max[ai], 3)
