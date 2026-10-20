import math
import re
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from skills._common import (SkillContext, box_centre, format_result, infer_target, placed_objects,
                            resolve_movers, search_command)
from tools.orientation import angle_diff
from tools.rotatePlacement import normalize_yaw, rotate_about_centre

TOLERANCE_RAD = 0.02
CENTRE_TOLERANCE_M = 0.05
# Before on/proximity/facing/around: "turn the table around" is a 180° turn, not an around layout.
INFER_PRIORITY = 0
# A turn by an amount: rotate/turn/spin/flip AND an angle ("90 degrees", "turn around",
# "quarter turn"). "turn the chair to face the TV" has no angle: that is facing_placement.
TRIGGER = (r'^(?=.*\b(?:rotat\w*|turn\w*|spin\w*|flip\w*)\b)'
           r'(?=.*(?:\d+(?:\.\d+)?\s*(?:degrees?|deg\b|°)|\baround\b|\b(?:quarter|half)[\s-]turn\b|\bflip\w*\b))')

# Angle words the LLM / regex map to degrees.
TURN_WORDS = {'around': 180.0, 'half turn': 180.0, 'half-turn': 180.0, 'flip': 180.0,
              'quarter turn': 90.0, 'quarter-turn': 90.0}


class Args(BaseModel):
    target: str = Field(description="object(s) to turn in place, e.g. 'table'")
    degrees: float = Field(gt=0, description="how far to turn; 'turn around' = 180, 'quarter turn' = 90")
    direction: Literal["clockwise", "anticlockwise"] = Field(
        "clockwise", description="as seen from above; 'counterclockwise' / 'left' = anticlockwise")
    all_matching: bool = Field(False, description="true when the user means every matching target, e.g. 'all chairs'")


def _delta(args: Args) -> float:
    """Yaw change in radians. Clockwise from above is negative (tools.orientation convention)."""
    rad = math.radians(args.degrees)
    return rad if args.direction == 'anticlockwise' else -rad


def infer(parsed_command: Dict) -> Optional[Dict]:
    """'rotate the table by 180 degrees' / 'turn the desk around' -> args, or None."""
    text = parsed_command.get('original_prompt', '').lower()
    if not re.search(r'\b(?:rotat\w*|turn\w*|spin\w*|flip\w*)\b', text):
        return None
    m = search_command(r'(\d+(?:\.\d+)?)\s*(?:degrees?|deg\b|°)', parsed_command)
    if m:
        degrees = float(m.group(1))
    else:
        word = next((w for w in TURN_WORDS if re.search(r'\b' + w + r'\b', text)), None)
        if not word:
            return None
        degrees = TURN_WORDS[word]
    direction = 'anticlockwise' if re.search(r'\b(?:anti|counter)[\s-]?clockwise\b', text) else 'clockwise'
    return {'degrees': degrees, 'direction': direction, **infer_target(parsed_command, '')}


def run(args: Args, ctx: SkillContext) -> Optional[Dict]:
    if ctx.new_objects:
        return None  # turns existing objects only
    movers = resolve_movers(args.target, args.all_matching, None, ctx)
    if not movers:
        print(f"   [ROTATE] target '{args.target}' not found")
        return None

    objects_out = []
    for obj in movers:
        current = float((obj.get('rotation') or {}).get('y', 0) or 0)
        new_yaw = round(normalize_yaw(current + _delta(args)), 4)
        objects_out.append({
            'object_id': obj['id'],
            'name': obj.get('name', ''),
            'position': rotate_about_centre(obj, new_yaw),
            'rotation': {**(obj.get('rotation') or {'x': 0, 'z': 0}), 'y': new_yaw},
            'action': 'rotate',
        })
        print(f"   [ROTATE] {obj['id']}: yaw {current:.4f} -> {new_yaw:.4f} about its box centre")

    print(f"   [SKILL rotate_placement] {len(objects_out)} object(s) {args.degrees:g}° {args.direction}")
    return format_result(objects_out, f"Turned {args.degrees:g}° {args.direction} in place about the box centre")


def postcondition(args: Args, placement: Dict, ctx: SkillContext) -> List[Dict]:
    before = {o.get('id'): o for o in ctx.scene_state.get('objects', [])}
    violations = []
    for obj in placed_objects(placement, ctx):
        old = before.get(obj.get('id'))
        if not old:
            continue
        current = float((old.get('rotation') or {}).get('y', 0) or 0)
        expected = normalize_yaw(current + _delta(args))
        actual = float((obj.get('rotation') or {}).get('y', 0) or 0)
        if angle_diff(actual, expected) > TOLERANCE_RAD:
            violations.append({'message': (f"{obj['id']} rotation_y={actual:.4f}, expected {expected:.4f} "
                                           f"({args.degrees:g}° {args.direction} from {current:.4f})"),
                               'fixable': True})
            continue
        c0, c1 = box_centre(old), box_centre(obj)
        if math.hypot(c1['x'] - c0['x'], c1['z'] - c0['z']) > CENTRE_TOLERANCE_M:
            p = rotate_about_centre(old, expected)
            violations.append({'message': (f"{obj['id']} should turn in place: its box centre moved from "
                                           f"({c0['x']:.2f}, {c0['z']:.2f}) to ({c1['x']:.2f}, {c1['z']:.2f}); "
                                           f"use position x={p['x']}, z={p['z']}"),
                               'fixable': True})
    return violations
