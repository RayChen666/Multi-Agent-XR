from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from skills._common import (PHRASE, SkillContext, format_result, infer_target, placed_objects,
                            resolve_anchor, resolve_movers, search_command, unresolved)
from tools.spacing import REST_TOLERANCE
from tools.stackPlacement import (centre_over_support, footprint_fits, resting_gap,
                                  stack_placement)

# Before facing: "put the lamp on the desk facing the sofa" is an "on" placement whose
# rotation the facing post-process still fixes.
INFER_PRIORITY = 1
# Only a request worded as on top of something.
TRIGGER = r'\b(?:on|onto|atop)\b'

# Phrases after "on" that are not a support object.
NOT_SUPPORTS = {'floor', 'ground', 'left', 'right', 'side', 'top', 'wall', 'it', 'them', 'that', 'there'}


class Args(BaseModel):
    target: str = Field(description="object being put on top, e.g. 'lamp'")
    support: str = Field(description="object it rests on, e.g. 'desk'; for 'on that' use the gazed object_id")
    all_matching: bool = Field(False, description="true when the user means every matching target")


def infer(parsed_command: Dict) -> Optional[Dict]:
    """'put the lamp on the desk' / 'on top of the desk' -> args, or None."""
    m = search_command(r'(?<!turn\s)\b(?:on\s+top\s+of|onto|on)\s+' + PHRASE, parsed_command)
    if not m:
        return None
    support = m.group(1).strip()
    if not support or support.split()[0] in NOT_SUPPORTS:
        return None
    return {'support': support, **infer_target(parsed_command, support)}


def run(args: Args, ctx: SkillContext) -> Optional[Dict]:
    support = resolve_anchor(args.support, ctx)
    if not support:
        print(f"   [ON] support '{args.support}' not found")
        return None
    movers = resolve_movers(args.target, args.all_matching, support, ctx)
    if not movers:
        print(f"   [ON] target '{args.target}' not found")
        return None

    # "put it on that": aim for the gaze hit when the user is looking at this support.
    gaze = ctx.gaze_target or {}
    point = gaze.get('point') if gaze.get('object_id') == support.get('id') else None

    is_add = bool(ctx.new_objects)
    objects_out = []
    for obj in movers:
        try:
            pos = stack_placement(obj, support, point)
        except ValueError as e:
            print(f"   [ON] skipped {obj.get('id')}: {e}")
            continue
        objects_out.append({
            'object_id': obj['id'],
            'name': obj.get('name', ''),
            'position': pos,
            'rotation': {'x': 0, 'y': 0, 'z': 0} if is_add else obj.get('rotation', {'x': 0, 'y': 0, 'z': 0}),
            'action': 'place' if is_add else 'move',
        })
    if not objects_out:
        return None

    name = support.get('name', support.get('id'))
    print(f"   [SKILL on_placement] {len(objects_out)} object(s) on '{name}'")
    return format_result(objects_out, f"Placed on top of {name}")


def postcondition(args: Args, placement: Dict, ctx: SkillContext) -> List[Dict]:
    support = resolve_anchor(args.support, ctx)
    if not support:
        return unresolved('support', args.support)
    if not support.get('collision'):
        return [{'message': f"{support['id']} has no collision data, so resting on it cannot be checked",
                 'fixable': False}]
    name = support.get('name', support.get('id'))
    violations = []
    for obj in placed_objects(placement, ctx):
        if obj.get('id') == support.get('id') or not obj.get('collision'):
            continue
        if not footprint_fits(obj, support):
            violations.append({'message': f"{obj['id']} is larger than the top of {name} and cannot rest on it",
                               'fixable': False})
            continue
        gap = resting_gap(obj, support)
        if abs(gap) > REST_TOLERANCE:
            expected_y = stack_placement(obj, support)['y']
            state = 'floating above' if gap > 0 else 'sunk into'
            violations.append({'message': f"{obj['id']} is {state} {name} by {abs(gap):.2f}m; set y={expected_y}",
                               'fixable': True})
        if not centre_over_support(obj, support):
            violations.append({'message': f"{obj['id']} is not over {name}'s top surface",
                               'fixable': True})
    return violations
