---
name: graph_layout
selectable: false
description: >
  Lay out a whole room from the Memory/ImageAgent semantic layout graph (nodes + edges).
  Activated by the Vague/Complex route, never chosen from the user's words.
---

# Graph layout

Each edge says where `from` sits relative to `to`. Rules (must match tools/graphLayout.py):

## Walls and corners (always win over object-relative edges on the same axis)
- Wall side mapping: back_wall = min Z, front_wall = max Z, left_wall = min X, right_wall = max X.
- `next_to` / `adjacent_to` a wall: bounding-box edge $WALL_INSET_NEXT_TO m inside the wall.
- `close_to` a wall: bounding-box edge $WALL_INSET_CLOSE_TO m inside the wall.
- `at_corner`: $WALL_INSET_NEXT_TO m inside both walls of that corner.

## Object-relative edges (next_to / close_to / adjacent_to / facing with a side)
- `side` is where the SOURCE is relative to the target:
  north = smaller Z, south = larger Z, west = smaller X, east = larger X.
- On the side's axis: bounding boxes $NEXT_TO_GAP m apart. On the other axis: centered on the target.

## Rotation (first match wins)
1. `facing` edge → turn toward the target: rotation_y = atan2(target.x - x, target.z - z).
2. Corner → back corners face +Z (rotation_y = 0), front corners face -Z (rotation_y = π).
3. Against a wall → face away from it: back 0, front π, left π/2, right -π/2.
4. Otherwise rotation_y = 0.

## Feasibility
- Object sizes use the object's FINAL rotation (an object turned 90° spans its depth along x).
- If an object would end up past a wall, it and the objects it is placed relative to are moved
  back inside together (e.g. a desk against the right wall moves out so the chair east of it fits);
  that wall edge is then relaxed.

## When retrying after a collision
- Keep wall and corner contacts; slide along the wall to clear the collision.
- Keep every object on the side of its target that the graph says.
