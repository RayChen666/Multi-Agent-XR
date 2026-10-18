from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from skills._common import SkillContext, placed_objects
from tools.aabbCheck import build_aabb
from tools.graphLayout import (CORNER_AXES, SIDE_AXIS, WALL_AXIS, WALL_INSET,
                               layout_graph_positions)
from tools.orientation import angle_diff, facing_angle

# The layout graph is a reference-image suggestion, not a user-stated constraint:
# once retries run out, execute the latest collision-free proposal anyway.
ACCEPT_ON_EXHAUSTED = True

WALL_TOLERANCE_M = 0.15   # how much farther from a wall than its inset still counts as "against"
SIDE_TOLERANCE_M = 0.05
FACING_TOLERANCE_RAD = 0.2
OBJECT_RELATIONS = ('next_to', 'close_to', 'adjacent_to', 'facing')


class Edge(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    from_: str = Field(alias='from')
    to: str
    relation: str
    side: Optional[str] = None
    corner: Optional[str] = None


class Args(BaseModel):
    edges: List[Edge] = Field(description="layout_graph['edges'] from ImageAgent")
    anchor_object: Optional[str] = Field(None, description="graph node placed first")


def _raw_edges(args: Args) -> List[Dict]:
    return [e.model_dump(by_alias=True) for e in args.edges]


def run(args: Args, ctx: SkillContext) -> Optional[Dict]:
    if not ctx.new_objects or not ctx.room_bounds:
        return None
    report: Dict = {}
    positions = layout_graph_positions(
        nodes_to_place=ctx.new_objects,
        edges=_raw_edges(args),
        anchor_object=args.anchor_object,
        room_bounds=ctx.room_bounds,
        report=report,
    )
    if not positions or len(positions) != len(ctx.new_objects):
        print("   [GRAPH] objects missing source_node, cannot resolve from graph")
        return None

    objects_out = []
    for obj in ctx.new_objects:
        pos = positions[obj['id']]
        objects_out.append({
            'object_id': obj['id'],
            'name': obj.get('name', ''),
            'position': {'x': pos['x'], 'y': pos['y'], 'z': pos['z']},
            'rotation': {'x': 0, 'y': round(pos.get('rotation_y', 0.0), 4), 'z': 0},
            'action': 'place',
        })
    relaxed = report.get('relaxed_edges', [])
    for r in relaxed:
        print(f"   [GRAPH] moved {r['from']} {abs(r['shift']):.2f}m off {r['to']} so {r['because']} fits in the room")
    print(f"   [SKILL graph_layout] {len(objects_out)} object(s) from layout graph")
    return {'objects': objects_out,
            'reasoning': 'Deterministic graph-layout resolved from ImageAgent layout_graph',
            # Wall edges deliberately given up for feasibility; postcondition skips them.
            'relaxed_edges': relaxed}


def _wall_gap(obj: Dict, axis: str, side: str, room_bounds: Dict) -> float:
    """Distance from obj's AABB edge to the wall (negative = through the wall)."""
    lo, hi = build_aabb(obj)
    i = 0 if axis == 'x' else 2
    if side == 'min':
        return lo[i] - float(room_bounds['min'][axis])
    return float(room_bounds['max'][axis]) - hi[i]


def _center(obj: Dict, axis: str) -> float:
    lo, hi = build_aabb(obj)
    i = 0 if axis == 'x' else 2
    return (lo[i] + hi[i]) / 2


def postcondition(args: Args, placement: Dict, ctx: SkillContext) -> List[Dict]:
    """
    Check the edges graphLayout honors by construction: wall/corner contact, the
    first facing edge per node, and the first object-relative side per axis on
    axes no wall/corner already claimed (graphLayout never overwrites those).
    """
    if not ctx.room_bounds:
        return []
    by_node = {o.get('source_node'): o for o in placed_objects(placement, ctx)
               if o.get('source_node') and o.get('collision')}
    relaxed = {(r['from'], r['to']) for r in (placement or {}).get('relaxed_edges', [])}
    violations = []

    def flag(message: str):
        violations.append({'message': message, 'fixable': True})

    wall_axes: Dict[str, set] = {}
    for e in _raw_edges(args):
        node, target, relation = e['from'], e['to'], e['relation']
        if relation in WALL_INSET and target in WALL_AXIS:
            wall_axes.setdefault(node, set()).add(WALL_AXIS[target][0])
        elif relation == 'at_corner':
            axes = CORNER_AXES.get((e.get('corner') or '').replace('-', '_'))
            if axes:
                wall_axes.setdefault(node, set()).update(axes)

    side_axes_done: Dict[str, set] = {}
    facing_done: set = set()
    for e in _raw_edges(args):
        node, target, relation = e['from'], e['to'], e['relation']
        obj = by_node.get(node)
        if not obj:
            continue
        if (node, target) in relaxed:
            continue  # graphLayout gave this wall edge up so a dependent object fits

        if relation in WALL_INSET and target in WALL_AXIS:
            axis, side = WALL_AXIS[target]
            gap = _wall_gap(obj, axis, side, ctx.room_bounds)
            inset = WALL_INSET[relation]
            if gap < -0.05 or gap > inset + WALL_TOLERANCE_M:
                flag(f"{obj['id']} should be {relation} {target} ({inset}m from it) but is {gap:.2f}m away")

        elif relation == 'at_corner':
            corner = (e.get('corner') or '').replace('-', '_')
            for axis, side in (CORNER_AXES.get(corner) or {}).items():
                gap = _wall_gap(obj, axis, side, ctx.room_bounds)
                if gap < -0.05 or gap > WALL_INSET['next_to'] + WALL_TOLERANCE_M:
                    flag(f"{obj['id']} should be in the {corner} corner but is {gap:.2f}m from that wall on {axis}")

        elif relation in OBJECT_RELATIONS and target in by_node and SIDE_AXIS.get(e.get('side') or ''):
            other = by_node[target]
            side = e['side']
            axis = SIDE_AXIS[side]

            if relation == 'facing' and node not in facing_done:
                facing_done.add(node)
                expected = facing_angle(_center(obj, 'x'), _center(obj, 'z'),
                                        _center(other, 'x'), _center(other, 'z'))
                actual = float((obj.get('rotation') or {}).get('y', 0))
                if angle_diff(actual, expected) > FACING_TOLERANCE_RAD:
                    flag(f"{obj['id']} should face {other['id']} (rotation_y {expected:.4f}) but has {actual:.2f}")

            if axis in wall_axes.get(node, set()) or axis in side_axes_done.get(node, set()):
                continue
            side_axes_done.setdefault(node, set()).add(axis)
            diff = _center(obj, axis) - _center(other, axis)
            wants_less = side in ('north', 'west')
            if (wants_less and diff > -SIDE_TOLERANCE_M) or (not wants_less and diff < SIDE_TOLERANCE_M):
                flag(f"{obj['id']} should be {side} of {other['id']} "
                     f"({axis} {'<' if wants_less else '>'} {_center(other, axis):.2f}) "
                     f"but its {axis} is {_center(obj, axis):.2f}")
    return violations
