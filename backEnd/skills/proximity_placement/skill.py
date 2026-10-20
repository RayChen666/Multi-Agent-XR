import re
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from skills._common import (PHRASE, USER_ID, SkillContext, at_room_edge, format_result, infer_target,
                            is_user_phrase, placed_objects, resolve_movers, resolve_target,
                            search_command, unresolved)
from tools.aabbCheck import build_aabb
from tools.proximityPlacement import footprint_gap, proximity_positions, user_anchor_box
from tools.spacing import PROXIMITY_MIN_GAP, USER_MIN_GAP

# After distance_placement ("1.5 m behind the table" is not a closer/farther request).
INFER_PRIORITY = 1
# Only a request worded as closer / farther: "attach the chairs to the back wall" is not one.
TRIGGER = r'\b(?:closer|nearer|farther|further|away\s+from|towards?)\b'
TOLERANCE_M = 0.01


class Args(BaseModel):
    target: str = Field(description="object(s) being moved, e.g. 'chair'")
    anchor: str = Field("user", description="reference object, e.g. 'table'; 'user' for 'me' "
                                            "or when no reference is named; for 'that' use the gazed object_id")
    mode: Literal["closer", "farther"] = Field(description="'closer' (nearer/toward) or 'farther' (away from)")
    step_m: Optional[float] = Field(None, gt=0, description="only when the user states it, e.g. '0.5 m closer'")
    all_matching: bool = Field(False, description="true when the user means every matching target, e.g. 'both chairs'")


def infer(parsed_command: Dict) -> Optional[Dict]:
    """'move both chairs closer to the table' / 'farther from me' -> args, or None."""
    m = search_command(r'\b(closer|nearer)\s+to\s+' + PHRASE, parsed_command)
    mode = 'closer'
    if not m:
        m = search_command(r'\b(?:(?:farther|further)\s+(?:away\s+)?from|away\s+from)\s+()' + PHRASE,
                           parsed_command)
        mode = 'farther'
    if not m:
        return None
    anchor = m.group(2).strip()
    step = search_command(r'(\d+(?:\.\d+)?)\s*(?:meters?|metres?|m)\s+'
                          r'(?:closer|nearer|farther|further|away)\b', parsed_command)
    args = {'anchor': USER_ID if is_user_phrase(anchor) else anchor, 'mode': mode,
            **infer_target(parsed_command, anchor)}
    if step:
        args['step_m'] = float(step.group(1))
    return args


def _resolve(args: Args, ctx: SkillContext) -> Tuple[Optional[Dict], Optional[Tuple], str, float]:
    """(anchor object or None for the user, anchor box, display name, min gap)."""
    if is_user_phrase(args.anchor):
        if not ctx.user_position:
            return None, None, 'user', USER_MIN_GAP
        return None, user_anchor_box(ctx.user_position), 'user', USER_MIN_GAP
    anchor = resolve_target(args.anchor, ctx)  # an object or a wall ('back wall')
    if not anchor or not anchor.get('collision'):
        return None, None, args.anchor, PROXIMITY_MIN_GAP
    return anchor, build_aabb(anchor), anchor.get('name', anchor.get('id')), PROXIMITY_MIN_GAP


def _solve(args: Args, ctx: SkillContext, movers: List[Dict], anchor: Optional[Dict], box, min_gap) -> Dict:
    skip = {o.get('id') for o in movers} | ({anchor.get('id')} if anchor else set())
    obstacles = [o for o in ctx.scene_state.get('objects', []) if o.get('id') not in skip]
    return proximity_positions(movers, box, args.mode, min_gap, obstacles,
                               ctx.room_bounds, step_m=args.step_m)


def run(args: Args, ctx: SkillContext) -> Optional[Dict]:
    if ctx.new_objects:
        return None  # moves existing objects only
    anchor, box, name, min_gap = _resolve(args, ctx)
    if box is None:
        print(f"   [PROXIMITY] anchor '{args.anchor}' not found")
        return None
    movers = [o for o in resolve_movers(args.target, args.all_matching, anchor, ctx) if o.get('collision')]
    if not movers:
        print(f"   [PROXIMITY] target '{args.target}' not found")
        return None

    solved = _solve(args, ctx, movers, anchor, box, min_gap)
    objects_out, notes = [], []
    for obj in movers:
        r = solved[obj['id']]
        print(f"   [PROXIMITY] {obj['id']}: gap {r['gap_before']:.2f}m -> {r['gap_after']:.2f}m ({r['status']})")
        if r['status'] != 'moved':
            notes.append(f"{obj['id']} {r['status'].replace('_', ' ')}")
        objects_out.append({
            'object_id': obj['id'],
            'name': obj.get('name', ''),
            'position': r['position'],
            'rotation': obj.get('rotation', {'x': 0, 'y': 0, 'z': 0}),
            'action': 'move',
        })

    word = 'closer to' if args.mode == 'closer' else 'farther from'
    print(f"   [SKILL proximity_placement] {len(objects_out)} object(s) {word} '{name}'")
    reasoning = f"Moved {word} {name}, each along its own side (edge-to-edge)"
    if notes:
        reasoning += "; " + ", ".join(notes)
    return format_result(objects_out, reasoning)


def postcondition(args: Args, placement: Dict, ctx: SkillContext) -> List[Dict]:
    anchor, box, name, min_gap = _resolve(args, ctx)
    if box is None:
        return unresolved('anchor', args.anchor)
    before = {o.get('id'): o for o in ctx.scene_state.get('objects', [])}
    word = 'closer to' if args.mode == 'closer' else 'farther from'
    violations = []
    for obj in placed_objects(placement, ctx):
        old = before.get(obj.get('id'))
        if not old or not obj.get('collision') or (anchor and obj.get('id') == anchor.get('id')):
            continue
        gap_before = footprint_gap(build_aabb(old), box)
        gap_after = footprint_gap(build_aabb(obj), box)

        if args.mode == 'closer':
            if gap_after < min_gap - 0.05 and gap_before >= min_gap:
                violations.append({'message': (f"{obj['id']} is {gap_after:.2f}m from {name}, closer than "
                                               f"the {min_gap}m minimum"), 'fixable': True})
                continue
            if gap_after < gap_before - TOLERANCE_M or gap_before <= min_gap + TOLERANCE_M:
                continue
        else:
            if gap_after > gap_before + TOLERANCE_M or at_room_edge(obj, ctx.room_bounds):
                continue

        # Not moved the requested way: is the tool able to do better from the original spot?
        expected = _solve(args, ctx, [old], anchor, box, min_gap)[old['id']]
        if abs(expected['gap_after'] - expected['gap_before']) <= TOLERANCE_M:
            violations.append({'message': (f"{obj['id']} cannot move {word} {name}: "
                                           f"{expected['status'].replace('_', ' ')}"), 'fixable': False})
        else:
            p = expected['position']
            violations.append({'message': (f"{obj['id']} should move {word} {name} (gap {gap_before:.2f}m, "
                                           f"now {gap_after:.2f}m; e.g. x={p['x']}, z={p['z']})"),
                               'fixable': True})
    return violations
