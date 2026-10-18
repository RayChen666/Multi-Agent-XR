import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Iterable

from tools.aabbCheck import build_aabb
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
