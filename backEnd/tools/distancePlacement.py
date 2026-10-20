import sys
from pathlib import Path
from typing import Dict, Optional, Tuple
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.aabbCheck import build_aabb, clamp_into_room, world_collision
from tools.orientation import direction_axis

"""
Edge-to-edge distance placement: solves commands like
"move the sink 1.5 meters back of the table" by tracking each object's AABB
(from tools.aabbCheck.build_aabb, which already accounts for collision
width/height/depth + geometric-center offset) and positioning the mover so
that abs(bound_mover - bound_anchor) along the travel axis equals the
requested distance, edge to edge (not center to center).

Directions are relative to the ANCHOR, not the user (tools.orientation.direction_axis):
front/back follow the anchor's own facing; left/right are as seen by someone standing
in front of the anchor. For an anchor with rotation_y = 0: front = +Z, back = -Z,
left = -X, right = +X.
"""

AXIS_INDEX = {'x': 0, 'y': 1, 'z': 2}


def distance_placement(
    mover: Dict,
    anchor: Dict,
    distance: float,
    direction: str,
    room_bounds: Optional[Dict] = None,
    axis_sign: Optional[Tuple[str, int]] = None,
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
        direction:   One of 'back', 'behind', 'front', 'left', 'right' (relative to anchor).
        room_bounds: Optional {'min': {'x','y','z'}, 'max': ...} for clamping.
        axis_sign:   Precomputed (axis, sign) overriding `direction`, e.g. from
                     tools.orientation.user_direction_axis when the anchor is the user.

    Returns:
        {'x': float, 'y': float, 'z': float} — new position for mover.

    Raises:
        ValueError: unknown direction, or either object lacks 'collision'.
    """
    if not mover.get('collision') or not anchor.get('collision'):
        raise ValueError("Both mover and anchor require a 'collision' field to compute bounding boxes")

    # sign=+1 places the mover on the anchor's max side of `axis`, -1 on its min side.
    axis, sign = axis_sign or direction_axis(anchor, direction)
    ai = AXIS_INDEX[axis]

    anchor_min, anchor_max = build_aabb(anchor)

    # Mover box in world axes (its current yaw; new objects have none yet).
    mov_col = world_collision(mover)
    mov_offset = mov_col['offset']
    half_extent = {'x': mov_col['width'], 'y': mov_col['height'], 'z': mov_col['depth']}[axis] / 2

    if sign > 0:
        # mover's near edge sits `distance` past anchor's max edge on this axis
        target_center = anchor_max[ai] + distance + half_extent
    else:
        target_center = anchor_min[ai] - distance - half_extent

    # AABB center -> position (undo the collision offset build_aabb applies).
    new_axis_val = target_center - mov_offset.get(axis, 0)

    # Cross axis: mover box centre on the anchor's box centre.
    cross_axis = 'x' if axis == 'z' else 'z'
    ci = AXIS_INDEX[cross_axis]
    cross_val = (anchor_min[ci] + anchor_max[ci]) / 2 - mov_offset.get(cross_axis, 0)

    position = dict(mover.get('position', {}))
    position[axis] = round(new_axis_val, 3)
    position[cross_axis] = round(cross_val, 3)

    if room_bounds:
        position = clamp_into_room(mover, position, room_bounds)

    return position


def measured_edge_distance(mover: Dict, anchor: Dict, direction: str,
                           axis_sign: Optional[Tuple[str, int]] = None) -> float:
    """
    Given mover/anchor with positions already set, return the actual
    edge-to-edge gap along `direction`'s axis: bound_mover - bound_anchor
    signed so a positive value means the constraint is satisfied on the
    requested side. Used to verify abs(bound_mover - bound_anchor) == distance
    after a placement (or LLM-proposed move) has been applied. axis_sign as in
    distance_placement().
    """
    axis, sign = axis_sign or direction_axis(anchor, direction)
    ai = AXIS_INDEX[axis]

    mover_min, mover_max = build_aabb(mover)
    anchor_min, anchor_max = build_aabb(anchor)

    if sign > 0:
        return round(mover_min[ai] - anchor_max[ai], 3)
    return round(anchor_min[ai] - mover_max[ai], 3)
