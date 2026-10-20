import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.aabbCheck import build_aabb, clamp_into_room
from tools.spacing import NEXT_TO_GAP, PROXIMITY_MIN_STEP, PROXIMITY_RATIO

"""
"Closer to" / "farther from": solves commands like "move both chairs closer to the
table" or "bring the lamp closer to me".

Each mover is handled on its OWN side of the anchor: a chair left of the table moves
+X, one right of it moves -X. Per mover:
  1. Edge-to-edge gaps to the anchor on x and z (tools.aabbCheck.build_aabb boxes,
     so yaw and collision offsets are respected). An axis with gap > 0 is one the
     two footprints are separated on.
  2. Travel only on the separated axes, toward (closer) or away from (farther) the
     anchor. One separated axis -> straight in; both (mover off the anchor's corner)
     -> both gaps change by the same fraction, i.e. a diagonal move. The cross axis
     is never re-centred, so the mover keeps its place along the anchor's side.
  3. closer: gap -> max(min_gap, gap * ratio); a gap already <= min_gap is left alone.
     farther: gap -> gap + max(gap * (1 - ratio), PROXIMITY_MIN_STEP).
     step_m (an explicit "0.5 m closer") replaces the ratio on every travel axis.
  4. Walk the move in small steps and stop NEXT_TO_GAP before the first obstacle (other
     scene objects and movers already placed). Movers are processed nearest-first, so a
     chair behind another on the same side stops behind it. Then clamp into the room.
Rotation is left to the caller (unchanged for a plain move).

The anchor is an object dict or the user: user_anchor_box() turns the headset
position into a zero-size box, so gaps to the user run from the mover's edge to that
floor point.
"""

AXES = (('x', 0), ('z', 2))
PATH_STEPS = 40


def user_anchor_box(user_position: Dict) -> Tuple[List[float], List[float]]:
    """The user as a point on the floor: a zero-size box at the headset's x/z (y unbounded)."""
    x, z = float(user_position.get('x', 0)), float(user_position.get('z', 0))
    return [x, float('-inf'), z], [x, float('inf'), z]


def axis_gaps(box: Tuple[List[float], List[float]], anchor_box: Tuple[List[float], List[float]]) -> Dict[str, Tuple[float, int]]:
    """{axis: (edge-to-edge gap, side)}; side = +1 when box is on the anchor's max side."""
    (lo, hi), (alo, ahi) = box, anchor_box
    out = {}
    for axis, i in AXES:
        above, below = lo[i] - ahi[i], alo[i] - hi[i]
        out[axis] = (above, 1) if above >= below else (below, -1)
    return out


def footprint_gap(box, anchor_box) -> float:
    """Shortest x/z distance between two footprints (0 when they overlap in plan)."""
    gaps = [max(g, 0.0) for g, _ in axis_gaps(box, anchor_box).values()]
    return (gaps[0] ** 2 + gaps[1] ** 2) ** 0.5


def _overlaps(a, b) -> bool:
    (alo, ahi), (blo, bhi) = a, b
    return all(alo[i] < bhi[i] - 1e-6 and blo[i] < ahi[i] - 1e-6 for i in range(3))


def _inflate(box, margin: float):
    """Grow a box by margin on x and z (obstacle clearance)."""
    lo, hi = box
    return ([lo[0] - margin, lo[1], lo[2] - margin], [hi[0] + margin, hi[1], hi[2] + margin])


def _moved_box(obj: Dict, position: Dict):
    return build_aabb({**obj, 'position': position})


def proximity_positions(movers: List[Dict],
                        anchor_box: Tuple[List[float], List[float]],
                        mode: str,
                        min_gap: float,
                        obstacles: List[Dict] = (),
                        room_bounds: Optional[Dict] = None,
                        ratio: float = PROXIMITY_RATIO,
                        step_m: Optional[float] = None,
                        ) -> Dict[str, Dict]:
    """
    Args:
        movers:      object dicts with 'id', 'position', 'collision' (and 'rotation').
        anchor_box:  (mins, maxs) of the anchor — build_aabb(anchor) or user_anchor_box().
        mode:        'closer' | 'farther'.
        min_gap:     closest edge-to-edge gap allowed on a travel axis (closer).
        obstacles:   other scene objects the movers must not run into (not the anchor).
        room_bounds: {'min','max'} for the final clamp.
        step_m:      explicit distance per travel axis instead of the ratio.

    Returns:
        {mover_id: {'position': {...}, 'status': 'moved' | 'at_min_gap' | 'overlapping'
                    | 'blocked' | 'at_wall', 'gap_before': float, 'gap_after': float}}
    """
    if mode not in ('closer', 'farther'):
        raise ValueError(f"mode must be 'closer' or 'farther', got {mode!r}")

    order = sorted(movers, key=lambda o: footprint_gap(build_aabb(o), anchor_box))
    blockers = [_inflate(build_aabb(o), NEXT_TO_GAP) for o in obstacles if o.get('collision')]
    out: Dict[str, Dict] = {}

    for obj in order:
        box = build_aabb(obj)
        start = dict(obj['position'])
        gaps = axis_gaps(box, anchor_box)
        gap_before = footprint_gap(box, anchor_box)
        travel = [(axis, gap, side) for axis, (gap, side) in gaps.items() if gap > 0]

        delta = {'x': 0.0, 'z': 0.0}
        if not travel:
            status = 'overlapping'   # already over/under the anchor in plan: no side to move along
        else:
            for axis, gap, side in travel:
                if mode == 'closer':
                    target = gap - step_m if step_m is not None else gap * ratio
                    new_gap = min(gap, max(min_gap, target))
                else:
                    step = step_m if step_m is not None else max(gap * (1 - ratio), PROXIMITY_MIN_STEP)
                    new_gap = gap + step
                # side=+1: mover sits past the anchor's max edge, so shrinking the gap moves it -axis.
                delta[axis] = (new_gap - gap) * side
            status = 'moved' if any(abs(d) > 1e-6 for d in delta.values()) else 'at_min_gap'

        # Walk toward the target; stop at the last free step before an obstacle.
        # Obstacles the mover already overlaps or is within clearance of (e.g. a rug under
        # it) cannot stop it.
        in_way = [b for b in blockers if not _overlaps(box, b)]
        position = start
        if status == 'moved':
            for k in range(1, PATH_STEPS + 1):
                t = k / PATH_STEPS
                trial = {**start, 'x': start['x'] + delta['x'] * t, 'z': start['z'] + delta['z'] * t}
                if any(_overlaps(_moved_box(obj, trial), b) for b in in_way):
                    status = 'blocked'
                    break
                position = trial
            if room_bounds:
                clamped = clamp_into_room(obj, position, room_bounds)
                if (abs(clamped['x'] - position['x']) > 1e-3 or abs(clamped['z'] - position['z']) > 1e-3):
                    status = 'at_wall'
                position = clamped

        position = {**position, 'x': round(float(position['x']), 3), 'z': round(float(position['z']), 3)}
        final_box = _moved_box(obj, position)
        blockers.append(_inflate(final_box, NEXT_TO_GAP))
        out[obj['id']] = {
            'position': position,
            'status': status,
            'gap_before': round(gap_before, 3),
            'gap_after': round(footprint_gap(final_box, anchor_box), 3),
        }
    return out
