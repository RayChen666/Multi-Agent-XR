import math
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from skills._common import (PHRASE, USER_ID, SkillContext, box_centre, infer_target, is_user_phrase,
                            placed_objects, resolve_movers, resolve_target, search_command, unresolved)
from tools.aroundPlacement import around_placement_positions
from tools.orientation import angle_diff, facing_angle
from tools.spacing import MIN_CLEARANCE, USER_MIN_GAP

TOLERANCE_RAD = 0.15
INFER_PRIORITY = 3
# Only a request worded as around / surrounding.
TRIGGER = r'\b(?:around|surround\w*|encircl\w*|circle)\b'
# How far past the tool's ring radius a retried mover may drift and still count as "around".
RADIUS_SLACK_M = 0.6


class Args(BaseModel):
    target: str = Field(description="objects to arrange, e.g. 'chair'")
    anchor: str = Field(description="object to arrange them around, e.g. 'table'; 'user' for 'me'")
    all_matching: bool = Field(True, description="false only when the user names a single target object")


def _gap(anchor: Dict) -> float:
    """Clearance between ring and anchor: personal space around the user."""
    return USER_MIN_GAP if anchor.get('id') == USER_ID else MIN_CLEARANCE


def _ring_radius(anchor: Dict, mover: Dict) -> float:
    anc = anchor.get('collision') or {}
    mov = mover.get('collision') or {}
    return (max(float(anc.get('width', 0.5)), float(anc.get('depth', 0.5))) / 2
            + max(float(mov.get('width', 0.4)), float(mov.get('depth', 0.4))) / 2
            + _gap(anchor))


def infer(parsed_command: Dict) -> Optional[Dict]:
    """'put the chairs around the table' -> args, or None."""
    m = search_command(r'around\s+' + PHRASE, parsed_command)
    if not m:
        return None
    anchor = m.group(1).strip()
    # Legacy behavior: "around" arranges every matching target.
    return {'anchor': USER_ID if is_user_phrase(anchor) else anchor,
            **infer_target(parsed_command, anchor), 'all_matching': True}


def run(args: Args, ctx: SkillContext) -> Optional[Dict]:
    anchor = resolve_target(args.anchor, ctx, walls=False)
    if not anchor:
        print(f"   [AROUND] anchor '{args.anchor}' not found")
        return None
    movers = resolve_movers(args.target, args.all_matching, anchor, ctx)
    if not movers:
        print(f"   [AROUND] target '{args.target}' not found")
        return None

    positions = around_placement_positions(movers, anchor, ctx.room_bounds, gap=_gap(anchor))
    if not positions:
        return None

    is_add = bool(ctx.new_objects)
    objects_out = [{
        'object_id': obj['id'],
        'name': obj.get('name', ''),
        'position': {'x': pos['x'], 'y': pos['y'], 'z': pos['z']},
        'rotation': {'x': 0, 'y': pos['rotation_y'], 'z': 0},
        'action': 'place' if is_add else 'move',
    } for obj, pos in zip(movers, positions)]

    name = anchor.get('name', anchor.get('id'))
    print(f"   [SKILL around_placement] {len(objects_out)} object(s) around '{name}'")
    # Always multi-object shape, matching the legacy around shortcut.
    return {'objects': objects_out, 'reasoning': f"Arranged {len(objects_out)} objects evenly around {name}"}


def postcondition(args: Args, placement: Dict, ctx: SkillContext) -> List[Dict]:
    anchor = resolve_target(args.anchor, ctx, walls=False)
    if not anchor:
        return unresolved('anchor', args.anchor)
    centre = box_centre(anchor)
    ax, az = centre['x'], centre['z']
    violations = []
    for obj in placed_objects(placement, ctx):
        if obj.get('id') == anchor.get('id'):
            continue
        px = float(obj['position'].get('x', 0))
        pz = float(obj['position'].get('z', 0))
        dist = math.hypot(px - ax, pz - az)
        limit = _ring_radius(anchor, obj) + RADIUS_SLACK_M
        if dist > limit:
            violations.append({
                'message': f"{obj['id']} is {dist:.2f}m from {anchor['id']}, too far to be around it (max {limit:.2f}m)",
                'fixable': True,
            })
        expected = facing_angle(px, pz, ax, az)
        actual = float((obj.get('rotation') or {}).get('y', 0))
        if angle_diff(actual, expected) > TOLERANCE_RAD:
            violations.append({
                'message': f"{obj['id']} does not face {anchor['id']}; expected rotation_y {expected:.4f}",
                'fixable': True,
            })
    return violations
