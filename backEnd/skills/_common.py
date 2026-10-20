import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Iterable

from tools.aabbCheck import build_aabb
from tools.graphLayout import WALL_AXIS, WALL_ROTATION
from tools.orientation import facing_angle, user_yaw
from tools.spacing import ROOM_MARGIN

"""
Helpers shared by every skill: object-phrase resolution against the scene,
mover selection, and turning a proposed placement back into object dicts so
postconditions can measure it.
"""

ARTICLES = {'the', 'a', 'an', 'this', 'that', 'these', 'those', 'my'}


@dataclass
class SkillContext:
    """Everything a skill may read. Built by SceneAgent (run) and Orchestrator (postcondition)."""
    scene_state: Dict
    room_bounds: Optional[Dict] = None
    new_objects: List[Dict] = field(default_factory=list)
    user_position: Optional[Dict] = None
    # LanguageAgent's resolved_spatial_target (head-gaze for "there" / "on that"), if any.
    gaze_target: Optional[Dict] = None


def singularize(word: str) -> str:
    if word.endswith('ies') and len(word) > 4:
        return word[:-3] + 'y'
    if word.endswith('s') and not word.endswith('ss') and len(word) > 2:
        return word[:-1]
    return word


def _tokens(phrase: str) -> List[str]:
    words = phrase.lower().replace('_', ' ').replace('-', ' ').split()
    return [singularize(w) for w in words if w not in ARTICLES]


def _match_score(obj: Dict, phrase: str) -> int:
    """0 = no match. Higher = more specific match."""
    tokens = _tokens(phrase)
    if not tokens:
        return 0
    norm = ' '.join(tokens)
    oid = str(obj.get('id', '')).lower()
    name = obj.get('name', '').lower()
    cat = obj.get('category', '').lower()
    haystack = f"{name} {cat} {oid.replace('_', ' ')}"

    if norm == oid:
        return 4
    if norm in name or norm == cat:
        return 3
    if all(t in haystack for t in tokens):
        return 2
    head = tokens[-1]
    if head in name or head == cat:
        return 1
    return 0


def _distance_sq(obj: Dict, user_position: Optional[Dict]) -> float:
    if not user_position:
        return 0.0
    pos = obj.get('position', {})
    dx = float(pos.get('x', 0)) - float(user_position.get('x', 0))
    dz = float(pos.get('z', 0)) - float(user_position.get('z', 0))
    return dx * dx + dz * dz


def resolve_objects(
    phrase: str,
    objects: Iterable[Dict],
    exclude_ids: Iterable[str] = (),
    user_position: Optional[Dict] = None,
    all_matching: bool = False,
) -> List[Dict]:
    """
    Resolve a natural-language object phrase ("coffee table", "chairs") to scene
    objects. Multi-word phrases are matched as a whole first, then by tokens, then
    by head noun. Only the best-scoring tier is returned; within it, the object
    nearest to the user wins (or all of them when all_matching).
    """
    excluded = set(exclude_ids)
    scored = [(_match_score(o, phrase), o) for o in objects if o.get('id') not in excluded]
    scored = [(s, o) for s, o in scored if s > 0]
    if not scored:
        return []
    best = max(s for s, _ in scored)
    tier = [o for s, o in scored if s == best]
    tier.sort(key=lambda o: (_distance_sq(o, user_position), str(o.get('id'))))
    return tier if all_matching else tier[:1]


def resolve_anchor(phrase: str, ctx: SkillContext) -> Optional[Dict]:
    new_ids = [o.get('id') for o in ctx.new_objects]
    found = resolve_objects(phrase, ctx.scene_state.get('objects', []), new_ids, ctx.user_position)
    return found[0] if found else None


# Anchor phrases that mean the user's own body ("face me", "1m in front of me").
USER_WORDS = {'user', 'me', 'myself', 'us', 'i'}
USER_ID = 'user'


def is_user_phrase(phrase: Optional[str]) -> bool:
    return (phrase or '').strip().lower() in USER_WORDS


def user_anchor(ctx: SkillContext) -> Optional[Dict]:
    """
    The user as an anchor object: a POINT at the headset's x/z (never its y, which is
    head height), on the floor, turned to the headset yaw, with a zero-size collision box
    so the AABB-based tools treat it as that point.
    """
    if not ctx.user_position:
        return None
    floor_y = float(((ctx.room_bounds or {}).get('min') or {}).get('y', -3.0))
    return {
        'id': USER_ID, 'name': 'user', 'category': 'user',
        'position': {'x': float(ctx.user_position.get('x', 0)), 'y': floor_y,
                     'z': float(ctx.user_position.get('z', 0))},
        'rotation': {'x': 0, 'y': user_yaw(ctx.user_position), 'z': 0},
        'collision': {'width': 0.0, 'height': 0.0, 'depth': 0.0, 'offset': {'x': 0, 'y': 0, 'z': 0}},
    }


# Wall phrases ("back wall", "the left wall", "north wall") -> graph wall name. Same
# orientation convention as tools/graphLayout.py: back = -Z, front = +Z, left = -X, right = +X.
WALL_WORDS = {'back': 'back_wall', 'rear': 'back_wall', 'north': 'back_wall',
              'front': 'front_wall', 'south': 'front_wall',
              'left': 'left_wall', 'west': 'left_wall',
              'right': 'right_wall', 'east': 'right_wall'}


def wall_name(phrase: Optional[str]) -> Optional[str]:
    """'the back wall' / 'back_wall' -> 'back_wall'; None when the phrase is not a wall."""
    m = re.fullmatch(r'(?:the\s+)?(\w+)\s+wall', (phrase or '').strip().lower().replace('_', ' '))
    return WALL_WORDS.get(m.group(1)) if m else None


def wall_anchor(wall: str, ctx: SkillContext) -> Optional[Dict]:
    """
    A wall as an anchor object: a zero-thickness box covering that room boundary,
    turned to face into the room (graphLayout.WALL_ROTATION), so its `front` is the
    room side and the AABB tools measure edge-to-wall gaps directly.
    """
    if not ctx.room_bounds or wall not in WALL_AXIS:
        return None
    lo, hi = ctx.room_bounds['min'], ctx.room_bounds['max']
    axis, side = WALL_AXIS[wall]
    along = 'z' if axis == 'x' else 'x'
    centre = {'x': (lo['x'] + hi['x']) / 2, 'z': (lo['z'] + hi['z']) / 2}
    centre[axis] = float(lo[axis] if side == 'min' else hi[axis])
    return {
        'id': wall, 'name': wall.replace('_', ' '), 'category': 'wall',
        'position': {'x': centre['x'], 'y': float(lo['y']), 'z': centre['z']},
        'rotation': {'x': 0, 'y': WALL_ROTATION[wall], 'z': 0},
        # Local width runs along the wall; the wall's yaw turns it onto the right world axis.
        'collision': {'width': float(hi[along] - lo[along]), 'height': float(hi['y'] - lo['y']), 'depth': 0.0,
                      'offset': {'x': 0, 'y': float(hi['y'] - lo['y']) / 2, 'z': 0}},
    }


def is_wall(obj: Optional[Dict]) -> bool:
    return bool(obj) and obj.get('category') == 'wall'


def resolve_target(phrase: str, ctx: SkillContext, walls: bool = True) -> Optional[Dict]:
    """
    Anchor object for `phrase`: user_anchor() for 'me'/'user', wall_anchor() for
    'back wall' etc. (unless walls=False), else resolve_anchor().
    """
    if is_user_phrase(phrase):
        return user_anchor(ctx)
    wall = wall_name(phrase)
    if wall:
        return wall_anchor(wall, ctx) if walls else None
    return resolve_anchor(phrase, ctx)


def box_centre(obj: Dict) -> Dict[str, float]:
    """Footprint centre {'x','z'}: the AABB centre (collision offset applied), else the origin."""
    if obj.get('collision'):
        lo, hi = build_aabb(obj)
        return {'x': (lo[0] + hi[0]) / 2, 'z': (lo[2] + hi[2]) / 2}
    pos = obj.get('position') or {}
    return {'x': float(pos.get('x', 0)), 'z': float(pos.get('z', 0))}


def yaw_toward(target: Dict, x: float, z: float) -> float:
    """
    rotation_y for an object at (x, z) to face `target`: square-on for a wall (opposite
    the wall's into-the-room yaw), else toward its box centre (the user: headset floor point).
    """
    if is_wall(target):
        yaw = WALL_ROTATION[target['id']] + math.pi
        return math.atan2(math.sin(yaw), math.cos(yaw))
    t = box_centre(target)
    return facing_angle(x, z, t['x'], t['z'])


def unresolved(role: str, phrase: str) -> List[Dict]:
    """Postcondition result when the reference cannot be found: never pass unverified."""
    return [{'message': f"{role} '{phrase}' not found in the scene, so the placement cannot be verified",
             'fixable': False}]


def resolve_movers(target: str, all_matching: bool, anchor: Optional[Dict], ctx: SkillContext) -> List[Dict]:
    """New objects (ADD route) always move; otherwise resolve `target` in the scene."""
    if ctx.new_objects:
        return list(ctx.new_objects)
    exclude = [anchor.get('id')] if anchor else []
    return resolve_objects(target, ctx.scene_state.get('objects', []), exclude,
                           ctx.user_position, all_matching)


def placed_objects(placement: Dict, ctx: SkillContext) -> List[Dict]:
    """
    Turn a proposed placement (any SceneAgent output shape) into full object
    dicts carrying the NEW position/rotation plus collision dims.
    """
    if not placement:
        return []
    if 'complete_objects' in placement:
        return [o for o in placement['complete_objects'] if o.get('position')]

    by_id = {o.get('id'): o for o in ctx.scene_state.get('objects', [])}
    by_id.update({o.get('id'): o for o in ctx.new_objects})
    entries = placement['objects'] if 'objects' in placement else [placement]
    out = []
    for entry in entries:
        base = by_id.get(entry.get('object_id'))
        if not base or not entry.get('position'):
            continue
        out.append({**base,
                    'position': entry['position'],
                    'rotation': entry.get('rotation', base.get('rotation', {}))})
    return out


def format_result(objects_out: List[Dict], reasoning: str) -> Dict:
    """Match SceneAgent's single-vs-multi output convention."""
    if len(objects_out) == 1:
        return {**objects_out[0], 'reasoning': reasoning}
    return {'objects': objects_out, 'reasoning': reasoning}


# ---------------------------------------------------------------------------
# Regex fallback for infer(): used only when the LanguageAgent LLM picked no skill
# (or the rule-based fallback parser ran). Extracts args; execution stays in run().
# ---------------------------------------------------------------------------

# A (possibly multi-word) object phrase, ending at a clause boundary.
PHRASE = (r"(?:(?:the|a|an|this|that)\s+)?([a-z][a-z ]*?)"
          r"(?=\s+(?:and|facing|to\s+face|with|then|so|please|on|in|at)\b|\s*[,.;!?]|\s*$)")


def search_command(pattern: str, parsed_command: Dict) -> Optional[re.Match]:
    """Search the original prompt, then each spatial concept, as separate segments."""
    segments = [parsed_command.get('original_prompt', '')] + list(parsed_command.get('spatial_concepts', []))
    for segment in segments:
        m = re.search(pattern, segment.lower())
        if m:
            return m
    return None


def infer_target(parsed_command: Dict, exclude_phrase: str) -> Dict:
    """Pick the mover phrase from involved_objects, skipping the anchor itself."""
    excluded = _tokens(exclude_phrase)
    text = parsed_command.get('original_prompt', '').lower()
    for phrase in parsed_command.get('involved_objects', []):
        if _tokens(phrase) and _tokens(phrase) != excluded:
            plural = phrase.lower().endswith('s') and not phrase.lower().endswith('ss')
            return {'target': phrase, 'all_matching': plural or bool(re.search(r'\ball\b', text))}
    return {'target': '', 'all_matching': False}


def at_room_edge(obj: Dict, room_bounds: Optional[Dict], tol: float = ROOM_MARGIN + 0.01) -> bool:
    """True when obj's footprint touches the room-bound clamp margin (tools.spacing.ROOM_MARGIN)."""
    if not room_bounds or not obj.get('collision'):
        return False
    lo, hi = build_aabb(obj)
    return (lo[0] <= room_bounds['min']['x'] + tol
            or hi[0] >= room_bounds['max']['x'] - tol
            or lo[2] <= room_bounds['min']['z'] + tol
            or hi[2] >= room_bounds['max']['z'] - tol)
