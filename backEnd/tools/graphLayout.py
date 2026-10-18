import math
from typing import Dict, List, Optional

from tools.aabbCheck import world_collision
from tools.orientation import facing_angle
from tools.spacing import NEXT_TO_GAP, ROOM_MARGIN, WALL_INSET

# back_wall/front_wall constrain Z, left_wall/right_wall constrain X — matches
# the orientation convention ImageAgent is instructed to use when extracting
# the graph (back=north=-Z, front=south=+Z, left=west=-X, right=east=+X).
WALL_AXIS = {
    'back_wall':  ('z', 'min'),
    'front_wall': ('z', 'max'),
    'left_wall':  ('x', 'min'),
    'right_wall': ('x', 'max'),
}
CORNER_AXES = {
    'back_left':   {'x': 'min', 'z': 'min'},
    'back_right':  {'x': 'max', 'z': 'min'},
    'front_left':  {'x': 'min', 'z': 'max'},
    'front_right': {'x': 'max', 'z': 'max'},
}
SIDE_AXIS = {'north': 'z', 'south': 'z', 'west': 'x', 'east': 'x'}

# Model forward is +Z at rotation_y=0 (same convention as facingPlacement.py /
# aroundPlacement.py). Objects against a wall face AWAY from it into the room.
WALL_ROTATION = {
    'back_wall':  0.0,           # faces +Z (south, toward user)
    'front_wall': math.pi,       # faces -Z (north, toward back)
    'left_wall':  math.pi / 2,   # faces +X (east)
    'right_wall': -math.pi / 2,  # faces -X (west)
}
# Corner placements face away from the dominant (Z-axis) wall, same as WALL_ROTATION.
# Real furniture at a corner faces one cardinal direction — a fridge at back_left
# faces south into the room, not diagonally. The Z wall (back/front) is dominant
# because that's the axis most furniture is installed along in a corner.
CORNER_ROTATION = {
'''
    'back_left':   math.pi / 4,
    'back_right':  -math.pi / 4,
    'front_left':  3 * math.pi / 4,
    'front_right': -3 * math.pi / 4,
'''
    'back_left':   0.0,       # back wall dominant → face south (+Z)
    'back_right':  0.0,       # back wall dominant → face south (+Z)
    'front_left':  math.pi,   # front wall dominant → face north (-Z)
    'front_right': math.pi,   # front wall dominant → face north (-Z)
}

# Wall inset distances (edge to wall) live in tools/spacing.py: WALL_INSET.


# A 'facing' source sits on `side` of its target and looks back at it.
SIDE_LOOK_YAW = {'east': -math.pi / 2, 'west': math.pi / 2, 'north': 0.0, 'south': math.pi}


def layout_graph_positions(nodes_to_place: List[Dict],
                            edges: List[Dict],
                            anchor_object: Optional[str],
                            room_bounds: Dict,
                            gap: float = NEXT_TO_GAP,
                            report: Optional[Dict] = None,
                            ) -> Optional[Dict[str, Dict]]:
    """
    Deterministically resolve position + rotation for each object from a
    semantic layout graph's edges, instead of asking an LLM to translate
    "next_to" / "facing" / "at_corner" relations into numbers itself.

    Each object in nodes_to_place must carry a 'source_node' field — the
    original graph node name it was created from (e.g. "office_chair" before
    asset-alias resolution).  The caller (orchestrator / AssetAgent) is
    responsible for stamping this field; without it the function returns None
    and the caller should fall back to the LLM path.

    Wall/corner placement (Phase A):
        - next_to / adjacent_to wall → half-extent + 0.1 m inward
        - close_to wall              → half-extent + 0.25 m inward
        - at_corner                  → both axes from the two bounding walls
      All wall/corner edges are resolved before any object-relative edge runs,
      so a "chair next_to desk" edge never overwrites the wall anchor a node
      already earned from its own "desk next_to back_wall" edge.

    Object-relative placement (Phase B, iterated to convergence):
        - Primary axis   : edge-to-edge spacing (half-extent + half-extent + gap)
        - Perpendicular  : aligned to target's true AABB centre (px = tx, mirroring
                           facingPlacement.py's own "same lateral position" convention)
        - 'facing' edges : only set a rotation flag here; rotation is computed
                           post-resolution via tools.orientation.facing_angle
                           — NOT from a cardinal lookup table.

    Rotation (output phase):
        facing target resolved  → tools.orientation.facing_angle
        corner placement        → CORNER_ROTATION diagonal bisector
        wall placement          → WALL_ROTATION (face away from wall)
        no constraint           → 0.0

    Args:
        nodes_to_place: object dicts with 'id', 'source_node', 'collision'.
        edges:          layout_graph['edges'] from ImageAgent output.
        anchor_object:  graph node name of the dominant piece (processed first).
        room_bounds:    {'min': {'x','y','z'}, 'max': {'x','y','z'}}.
        gap:            minimum edge-to-edge clearance between objects (metres).
        report:         optional dict; filled with 'relaxed_edges' — wall/corner edges
                        given up so an object chained to that node fits in the room.

    Sizes use each object's FINAL yaw (facing > corner > wall, as in the output
    phase): a desk turned against the right wall spans its depth along x.

    Feasibility (after Phase B): an object pushed past a wall shifts back inside
    together with every node whose position on that axis derives from the same
    root (e.g. desk + the chair placed east of it), so their relation holds. If the
    root was anchored to a wall on that axis, that wall edge is relaxed.

    Returns:
        {object_id: {'x','y','z','rotation_y'}} for every object, or None when
        any object lacks 'source_node' (signal to caller to fall back to LLM).
    """
    by_node = {n['source_node']: n for n in nodes_to_place if n.get('source_node')}
    if len(by_node) != len(nodes_to_place):
        return None  # caller should fall back to LLM

    min_b = room_bounds['min']
    max_b = room_bounds['max']
    floor_y = float(min_b['y'])

    # ── Planned yaw per node (same priority as the output phase) ───────────

    def planned_yaw(node_name: str) -> float:
        own = [e for e in edges if e.get('from') == node_name]
        for e in own:
            if (e.get('relation') == 'facing' and e.get('to') in by_node
                    and e.get('side') in SIDE_LOOK_YAW):
                return SIDE_LOOK_YAW[e['side']]
        for e in own:
            corner = (e.get('corner') or '').replace('-', '_')
            if e.get('relation') == 'at_corner' and corner in CORNER_ROTATION:
                return CORNER_ROTATION[corner]
        for e in own:
            if e.get('relation') in WALL_INSET and e.get('to') in WALL_ROTATION:
                return WALL_ROTATION[e['to']]
        return 0.0

    default_col = {'width': 1.2, 'height': 1.0, 'depth': 1.2}
    world_col = {
        n: world_collision({'collision': obj.get('collision') or default_col,
                            'rotation': {'y': planned_yaw(n)}})
        for n, obj in by_node.items()
    }

    # ── Collision geometry helpers ─────────────────────────────────────────

    def half_extent(node_name: str, axis: str) -> float:
        col = world_col[node_name]
        return col['width' if axis == 'x' else 'depth'] / 2

    def offset_of(node_name: str, axis: str) -> float:
        # collision.offset (rotated with the object) shifts the TRUE AABB centre
        # away from 'position'. All gap/wall math works in true-centre space;
        # positions are converted back at the end: position = true_centre - offset.
        return world_col[node_name]['offset'][axis]

    def true_pos(node_name: str, axis: str) -> Optional[float]:
        """True AABB centre for a resolved node, or None if not yet resolved."""
        v = resolved.get(node_name, {}).get(axis)
        return None if v is None else v + offset_of(node_name, axis)

    def tgt_center(node_name: str, axis: str) -> float:
        """Target true centre, falling back to room centre if not yet resolved."""
        v = true_pos(node_name, axis)
        return v if v is not None else (min_b[axis] + max_b[axis]) / 2

    # ── Phase A — wall and corner edges ───────────────────────────────────
    # These carry no inter-object dependencies so all can be resolved in one
    # sweep.  setdefault ensures a node's own wall anchor wins over any later
    # object-relative edge that touches the same axis.

    resolved: Dict[str, Dict] = {}
    wall_src: Dict[str, Dict[str, str]] = {}   # node -> axis -> wall edge 'to' that anchored it
    axis_src: Dict[str, Dict[str, str]] = {}   # node -> axis -> target node it was derived from

    def wall_position(axis: str, side: str, node_name: str, relation: str) -> float:
        inset = WALL_INSET.get(relation, 0.1)
        bound = float(min_b[axis]) if side == 'min' else float(max_b[axis])
        true_c = (bound + half_extent(node_name, axis) + inset
                  if side == 'min'
                  else bound - half_extent(node_name, axis) - inset)
        return true_c - offset_of(node_name, axis)

    for node_name in by_node:
        for edge in edges:
            if edge.get('from') != node_name:
                continue
            relation = edge.get('relation', '')
            target   = edge.get('to', '')
            pos      = resolved.setdefault(node_name, {})

            if relation in WALL_INSET and target in WALL_AXIS:
                axis, side = WALL_AXIS[target]
                if axis not in pos:
                    pos[axis] = wall_position(axis, side, node_name, relation)
                    wall_src.setdefault(node_name, {})[axis] = target
                pos.setdefault('_wall', target)

            elif relation == 'at_corner':
                corner = edge.get('corner', '').replace('-', '_')
                axes   = CORNER_AXES.get(corner)
                if axes:
                    for axis, side in axes.items():
                        # corner is always "against" (next_to) both walls
                        if axis not in pos:
                            pos[axis] = wall_position(axis, side, node_name, 'next_to')
                            wall_src.setdefault(node_name, {})[axis] = target
                    pos.setdefault('_corner', corner)

    # ── Phase B — object-relative edges (iterated) ────────────────────────
    # Anchor is processed first so dependency chains that root at the anchor
    # typically converge in a single pass.

    order = ([anchor_object] if anchor_object in by_node else []) + \
            [n for n in by_node if n != anchor_object]

    changed = True
    passes  = 0
    while changed and passes < len(by_node) + 2:
        changed = False
        passes += 1
        for node_name in order:
            for edge in edges:
                if edge.get('from') != node_name:
                    continue
                relation = edge.get('relation', '')
                target   = edge.get('to', '')

                if target in WALL_AXIS or relation == 'at_corner':
                    continue  # Phase A only
                if relation not in ('next_to', 'close_to', 'adjacent_to', 'facing'):
                    continue
                if target not in by_node:
                    continue

                side = edge.get('side', '')
                axis = SIDE_AXIS.get(side)
                if not axis:
                    continue
                other_axis = 'z' if axis == 'x' else 'x'

                pos = resolved.setdefault(node_name, {})

                # Primary axis: edge-to-edge spacing.
                # Never overwrite an axis already anchored by Phase A.
                if axis not in pos:
                    spacing = (half_extent(node_name, axis)
                               + half_extent(target, axis)
                               + gap)
                    tc = tgt_center(target, axis)
                    # side is where SOURCE IS relative to target:
                    #   north/west → source is in the negative direction
                    src_true = (tc - spacing if side in ('north', 'west')
                                else tc + spacing)
                    pos[axis] = src_true - offset_of(node_name, axis)
                    axis_src.setdefault(node_name, {})[axis] = target
                    changed = True

                # Perpendicular axis: align to target's AABB centre (px = tx),
                # matching facingPlacement.py's lateral-alignment convention.
                if other_axis not in pos:
                    pos[other_axis] = (tgt_center(target, other_axis)
                                       - offset_of(node_name, other_axis))
                    axis_src.setdefault(node_name, {})[other_axis] = target
                    changed = True

                # Mark 'facing' edges — rotation computed post-resolution.
                if relation == 'facing' and '_facing_target' not in pos:
                    pos['_facing_target'] = target

    # ── Fallback: any axis still unconstrained → room centre ──────────────
    for node_name in by_node:
        pos = resolved.setdefault(node_name, {})
        for axis in ('x', 'z'):
            if axis not in pos:
                # Box centre at the room centre — the same fallback tgt_center() gave any
                # node aligned to this one earlier, so they stay aligned (a chair placed
                # "east of the desk" before the desk's z was known still faces it square).
                pos[axis] = (float(min_b[axis]) + float(max_b[axis])) / 2 - offset_of(node_name, axis)

    # ── Feasibility — pull chains that cross a wall back into the room ────
    relaxed: List[Dict] = []

    def chain_root(node_name: str, axis: str) -> str:
        seen = set()
        while axis_src.get(node_name, {}).get(axis) and node_name not in seen:
            seen.add(node_name)
            node_name = axis_src[node_name][axis]
        return node_name

    for _ in range(len(by_node) + 1):
        moved = False
        for node_name in order:
            for axis in ('x', 'z'):
                half = half_extent(node_name, axis)
                lo_ok = float(min_b[axis]) + ROOM_MARGIN
                hi_ok = float(max_b[axis]) - ROOM_MARGIN
                if 2 * half > hi_ok - lo_ok:
                    continue  # wider than the room: nothing fits; room-bounds check reports it
                centre = resolved[node_name][axis] + offset_of(node_name, axis)
                if centre - half < lo_ok - 1e-6:
                    shift = lo_ok - (centre - half)
                elif centre + half > hi_ok + 1e-6:
                    shift = hi_ok - (centre + half)
                else:
                    continue
                root = chain_root(node_name, axis)
                for n in by_node:
                    if chain_root(n, axis) == root:
                        resolved[n][axis] += shift
                wall = wall_src.get(root, {}).get(axis)
                if wall and not any(r['from'] == root and r['to'] == wall for r in relaxed):
                    relaxed.append({'from': root, 'to': wall, 'shift': round(shift, 3), 'axis': axis,
                                    'because': node_name})
                moved = True
        if not moved:
            break
    if report is not None:
        report['relaxed_edges'] = relaxed

    # ── Output: build final placement dicts ───────────────────────────────
    out: Dict[str, Dict] = {}
    for node_name, entry in by_node.items():
        pos = resolved[node_name]
        x   = round(float(pos['x']), 3)
        z   = round(float(pos['z']), 3)

        # Rotation priority:
        #   1. 'facing' target → tools.orientation.facing_angle (shared yaw convention)
        #   2. corner placement → diagonal bisector (CORNER_ROTATION)
        #   3. wall placement   → face away from wall (WALL_ROTATION)
        #   4. default          → 0.0
        facing_target = pos.get('_facing_target')
        if facing_target and facing_target in resolved:
            px = x + offset_of(node_name, 'x')
            pz = z + offset_of(node_name, 'z')
            tx = resolved[facing_target]['x'] + offset_of(facing_target, 'x')
            tz = resolved[facing_target]['z'] + offset_of(facing_target, 'z')
            rotation_y = facing_angle(px, pz, tx, tz)
        elif '_corner' in pos:
            rotation_y = CORNER_ROTATION.get(pos['_corner'], 0.0)
        elif '_wall' in pos:
            rotation_y = WALL_ROTATION.get(pos['_wall'], 0.0)
        else:
            rotation_y = 0.0

        out[entry['id']] = {
            'x':          x,
            'y':          floor_y,
            'z':          z,
            'rotation_y': round(rotation_y, 4),
        }

    return out
