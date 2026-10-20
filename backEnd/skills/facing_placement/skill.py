from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from skills._common import (PHRASE, USER_ID, SkillContext, box_centre, format_result, infer_target,
                            is_user_phrase, placed_objects, resolve_movers, resolve_target,
                            search_command, unresolved, yaw_toward)
from tools.facingPlacement import facing_placement
from tools.orientation import angle_diff
from tools.rotatePlacement import rotate_about_centre
from tools.spacing import FACING_GAP, USER_MIN_GAP

TOLERANCE_RAD = 0.1
INFER_PRIORITY = 2
# Only a request worded as facing: "attach the chairs to the back wall" is not one.
TRIGGER = r'\b(?:fac(?:e|es|ing)|towards?|look(?:s|ing)?\s+at)\b'


class Args(BaseModel):
    target: str = Field(description="object that should face something, e.g. 'chair'")
    facing: str = Field(description="object to face, e.g. 'tv'; 'user' for 'me'")
    all_matching: bool = Field(False, description="true when the user means every matching target, e.g. 'all chairs'")


def infer_facing_phrase(parsed_command: Dict) -> Optional[str]:
    m = search_command(r'(?:facing|to\s+face|faces?)\s+' + PHRASE, parsed_command)
    return m.group(1).strip() if m else None


def infer(parsed_command: Dict) -> Optional[Dict]:
    """'turn the chair to face the tv' / 'face me' -> args, or None."""
    facing = infer_facing_phrase(parsed_command)
    if not facing:
        return None
    return {'facing': USER_ID if is_user_phrase(facing) else facing, **infer_target(parsed_command, facing)}


def run(args: Args, ctx: SkillContext) -> Optional[Dict]:
    facing_obj = resolve_target(args.facing, ctx)
    if not facing_obj:
        print(f"   [FACING] '{args.facing}' not found")
        return None
    movers = resolve_movers(args.target, args.all_matching, facing_obj, ctx)
    if not movers:
        print(f"   [FACING] target '{args.target}' not found")
        return None

    is_add = bool(ctx.new_objects)
    t = box_centre(facing_obj)
    gap = USER_MIN_GAP if facing_obj.get('id') == USER_ID else FACING_GAP
    objects_out = []
    for obj in movers:
        if is_add:
            pos = facing_placement(target=facing_obj, mover_collision=obj.get('collision'),
                                   room_bounds=ctx.room_bounds, gap=gap)
            objects_out.append({
                'object_id': obj['id'],
                'name': obj.get('name', ''),
                'position': {'x': pos['x'], 'y': pos['y'], 'z': pos['z']},
                'rotation': {'x': 0, 'y': pos['rotation_y'], 'z': 0},
                'action': 'place',
            })
        else:
            # Turn in place about the box centre (tools.rotatePlacement), aiming from that centre.
            c = box_centre(obj)
            rot_y = round(yaw_toward(facing_obj, c['x'], c['z']), 4)
            objects_out.append({
                'object_id': obj['id'],
                'name': obj.get('name', ''),
                'position': rotate_about_centre(obj, rot_y),
                'rotation': {'x': 0, 'y': rot_y, 'z': 0},
                'action': 'rotate',
            })

    name = facing_obj.get('name', facing_obj.get('id'))
    print(f"   [SKILL facing_placement] {len(objects_out)} object(s) facing '{name}' "
          f"at ({t['x']:.2f}, {t['z']:.2f})")
    return format_result(objects_out, f"{'Placed' if is_add else 'Rotated'} facing {name}")


def postcondition(args: Args, placement: Dict, ctx: SkillContext) -> List[Dict]:
    facing_obj = resolve_target(args.facing, ctx)
    if not facing_obj:
        return unresolved('facing target', args.facing)
    t = box_centre(facing_obj)
    violations = []
    for obj in placed_objects(placement, ctx):
        if obj.get('id') == facing_obj.get('id'):
            continue
        c = box_centre(obj)  # aimed from the box centre, as run() and SceneAgent's facing pass do
        expected = yaw_toward(facing_obj, c['x'], c['z'])
        actual = float((obj.get('rotation') or {}).get('y', 0))
        if angle_diff(actual, expected) > TOLERANCE_RAD:
            violations.append({
                'message': (f"{obj['id']} rotation_y={actual:.2f} does not face "
                            f"{facing_obj.get('id')} at x={t['x']:.2f}, z={t['z']:.2f}; expected {expected:.4f}"),
                'fixable': True,
            })
    return violations
