from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from skills._common import (PHRASE, SkillContext, format_result, infer_target,
                            placed_objects, resolve_anchor, resolve_movers, search_command)
from tools.facingPlacement import facing_placement
from tools.orientation import angle_diff, facing_angle

TOLERANCE_RAD = 0.1
INFER_PRIORITY = 2


class Args(BaseModel):
    target: str = Field(description="object that should face something, e.g. 'chair'")
    facing: str = Field(description="object to face, e.g. 'tv'")
    all_matching: bool = Field(False, description="true when the user means every matching target, e.g. 'all chairs'")


def infer_facing_phrase(parsed_command: Dict) -> Optional[str]:
    m = search_command(r'(?:facing|to\s+face|faces?)\s+' + PHRASE, parsed_command)
    return m.group(1).strip() if m else None


def infer(parsed_command: Dict) -> Optional[Dict]:
    """'turn the chair to face the tv' -> args, or None."""
    facing = infer_facing_phrase(parsed_command)
    if not facing:
        return None
    return {'facing': facing, **infer_target(parsed_command, facing)}


def run(args: Args, ctx: SkillContext) -> Optional[Dict]:
    facing_obj = resolve_anchor(args.facing, ctx)
    if not facing_obj:
        print(f"   [FACING] '{args.facing}' not found")
        return None
    movers = resolve_movers(args.target, args.all_matching, facing_obj, ctx)
    if not movers:
        print(f"   [FACING] target '{args.target}' not found")
        return None

    is_add = bool(ctx.new_objects)
    tx = float(facing_obj.get('position', {}).get('x', 0))
    tz = float(facing_obj.get('position', {}).get('z', 0))
    objects_out = []
    for obj in movers:
        if is_add:
            pos = facing_placement(target=facing_obj, mover_collision=obj.get('collision'),
                                   room_bounds=ctx.room_bounds)
            objects_out.append({
                'object_id': obj['id'],
                'name': obj.get('name', ''),
                'position': {'x': pos['x'], 'y': pos['y'], 'z': pos['z']},
                'rotation': {'x': 0, 'y': pos['rotation_y'], 'z': 0},
                'action': 'place',
            })
        else:
            cur = obj.get('position', {})
            rot_y = facing_angle(float(cur.get('x', 0)), float(cur.get('z', 0)), tx, tz)
            objects_out.append({
                'object_id': obj['id'],
                'name': obj.get('name', ''),
                'position': cur,
                'rotation': {'x': 0, 'y': round(rot_y, 4), 'z': 0},
                'action': 'rotate',
            })

    name = facing_obj.get('name', facing_obj.get('id'))
    print(f"   [SKILL facing_placement] {len(objects_out)} object(s) facing '{name}'")
    return format_result(objects_out, f"{'Placed' if is_add else 'Rotated'} facing {name}")


def postcondition(args: Args, placement: Dict, ctx: SkillContext) -> List[Dict]:
    facing_obj = resolve_anchor(args.facing, ctx)
    if not facing_obj:
        return []
    tx = float(facing_obj.get('position', {}).get('x', 0))
    tz = float(facing_obj.get('position', {}).get('z', 0))
    violations = []
    for obj in placed_objects(placement, ctx):
        if obj.get('id') == facing_obj.get('id'):
            continue
        pos = obj['position']
        expected = facing_angle(float(pos.get('x', 0)), float(pos.get('z', 0)), tx, tz)
        actual = float((obj.get('rotation') or {}).get('y', 0))
        if angle_diff(actual, expected) > TOLERANCE_RAD:
            violations.append({
                'message': (f"{obj['id']} rotation_y={actual:.2f} does not face "
                            f"{facing_obj.get('id')}; expected {expected:.4f}"),
                'fixable': True,
            })
    return violations
