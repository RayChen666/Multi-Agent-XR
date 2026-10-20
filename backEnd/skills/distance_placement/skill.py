from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from skills._common import (PHRASE, USER_ID, SkillContext, at_room_edge, format_result, infer_target,
                            is_user_phrase, placed_objects, resolve_movers, resolve_target,
                            search_command, unresolved)
from tools.distancePlacement import distance_placement, measured_edge_distance
from tools.orientation import user_direction_axis, user_yaw

TOLERANCE_M = 0.05
# Regex-fallback order across skills (lower runs first): an explicit distance wins.
INFER_PRIORITY = 0
# Only a request that states a distance ("1.5 meters", "2m", "one meter").
TRIGGER = (r'(?:\d+(?:\.\d+)?\s*(?:m|meters?|metres?|cm|centimeters?|centimetres?)\b'
           r'|\b(?:one|two|three|four|five|half\s+a)\s+(?:meters?|metres?)\b)')

DIRECTION_WORDS = {
    'in front of': 'front', 'front of': 'front',
    'back of': 'back', 'behind': 'back',
    'left of': 'left', 'right of': 'right',
}


class Args(BaseModel):
    target: str = Field(description="object being placed or moved, e.g. 'sink'")
    anchor: str = Field(description="reference object, e.g. 'coffee table'; 'user' for 'me' / 'my left'")
    distance_m: float = Field(ge=0, description="edge-to-edge gap in meters")
    direction: Literal["front", "back", "left", "right"] = Field(
        description="side of the anchor; 'behind' = back. For the user: their own front/left/...")
    all_matching: bool = Field(False, description="true when the user means every matching target, e.g. 'all chairs'")


def infer(parsed_command: Dict) -> Optional[Dict]:
    """'1.5 meters behind the coffee table' / '2m to my left' -> args, or None."""
    m = search_command(r'(\d+(?:\.\d+)?)\s*(?:meters?|metres?|m)\b\s*(?:to\s+|in\s+)?my\s+'
                       r'(left|right|front|back)\b', parsed_command)
    if m:
        return {'anchor': USER_ID, 'distance_m': float(m.group(1)), 'direction': m.group(2),
                **infer_target(parsed_command, 'me')}
    m = search_command(
        r'(\d+(?:\.\d+)?)\s*(?:meters?|metres?|m)\b\s*(?:to\s+the\s+)?'
        r'(in front of|front of|back of|behind|left of|right of)\s+' + PHRASE,
        parsed_command,
    )
    if not m:
        return None
    anchor = m.group(3).strip()
    return {'anchor': USER_ID if is_user_phrase(anchor) else anchor, 'distance_m': float(m.group(1)),
            'direction': DIRECTION_WORDS[m.group(2)], **infer_target(parsed_command, anchor)}


def _axis_sign(args: Args, anchor: Dict, ctx: SkillContext):
    """The user's own front/left/...; None = the anchor object's sides (direction_axis)."""
    if anchor.get('id') != USER_ID:
        return None
    return user_direction_axis(user_yaw(ctx.user_position), args.direction)


def run(args: Args, ctx: SkillContext) -> Optional[Dict]:
    # Walls are not anchors here: centring on a wall's midpoint is not "N m from the wall".
    anchor = resolve_target(args.anchor, ctx, walls=False)
    if not anchor:
        print(f"   [DISTANCE] anchor '{args.anchor}' not found")
        return None
    movers = resolve_movers(args.target, args.all_matching, anchor, ctx)
    if not movers:
        print(f"   [DISTANCE] target '{args.target}' not found")
        return None

    is_add = bool(ctx.new_objects)
    objects_out = []
    for obj in movers:
        try:
            pos = distance_placement(mover=obj, anchor=anchor, distance=args.distance_m,
                                     direction=args.direction, room_bounds=ctx.room_bounds,
                                     axis_sign=_axis_sign(args, anchor, ctx))
        except ValueError as e:
            print(f"   [DISTANCE] skipped {obj.get('id')}: {e}")
            continue
        rotation = {'x': 0, 'y': 0, 'z': 0} if is_add else obj.get('rotation', {'x': 0, 'y': 0, 'z': 0})
        objects_out.append({
            'object_id': obj['id'],
            'name': obj.get('name', ''),
            'position': pos,
            'rotation': rotation,
            'action': 'place' if is_add else 'move',
        })
    if not objects_out:
        return None

    anchor_name = anchor.get('name', anchor.get('id'))
    print(f"   [SKILL distance_placement] {len(objects_out)} object(s) "
          f"{args.distance_m}m {args.direction} of '{anchor_name}'")
    return format_result(objects_out,
                         f"Placed {args.distance_m}m {args.direction} of {anchor_name} (edge-to-edge)")


def postcondition(args: Args, placement: Dict, ctx: SkillContext) -> List[Dict]:
    anchor = resolve_target(args.anchor, ctx, walls=False)
    if not anchor:
        return unresolved('anchor', args.anchor)
    if not anchor.get('collision'):
        return [{'message': f"{anchor['id']} has no collision data, so the distance cannot be measured",
                 'fixable': False}]
    axis_sign = _axis_sign(args, anchor, ctx)
    violations = []
    for obj in placed_objects(placement, ctx):
        if obj.get('id') == anchor.get('id') or not obj.get('collision'):
            continue
        gap = measured_edge_distance(obj, anchor, args.direction, axis_sign)
        if abs(gap - args.distance_m) <= TOLERANCE_M:
            continue
        anchor_name = anchor.get('name', anchor.get('id'))
        if at_room_edge(obj, ctx.room_bounds):
            violations.append({
                'message': (f"{obj['id']} cannot be {args.distance_m}m {args.direction} of "
                            f"{anchor_name}: that position is outside the room"),
                'fixable': False,
            })
        else:
            expected = distance_placement(obj, anchor, args.distance_m, args.direction, ctx.room_bounds,
                                          axis_sign)
            violations.append({
                'message': (f"{obj['id']} is {gap:.2f}m {args.direction} of {anchor_name}, expected "
                            f"{args.distance_m}m edge-to-edge (e.g. x={expected['x']}, z={expected['z']})"),
                'fixable': True,
            })
    return violations
