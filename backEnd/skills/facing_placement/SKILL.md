---
name: facing_placement
description: >
  Turn an object to face another object, or add a new object in front of another and
  facing it, e.g. "turn the chair to face the TV", "add a sofa facing the fireplace",
  "rotate all chairs to face me" (facing = 'user').
  Use only when facing is the main request and no explicit distance is given.
---

# Facing placement

## Geometry (must match tools/facingPlacement.py)
- Model forward is +Z. rotation_y = atan2(target.x - mover.x, target.z - mover.z) in radians,
  where target = the target's bounding-box CENTER (position + center_offset), not its origin.
- The user ("me", anchor 'user') is a POINT: the headset's x/z on the floor (never its y).
- A wall (back/front/left/right wall) is faced square-on: rotation_y points straight at it
  (back wall = π, front wall = 0, left wall = -π/2, right wall = π/2), not at its midpoint.
- Existing objects keep their position; only rotation_y changes.
- New objects are placed on the target's front side (its `sides.front`) with a $FACING_GAP m edge gap
  ($USER_MIN_GAP m from the user, whose front is where they look),
  centered on the target across that axis.

## When retrying after a collision
- Move the object wherever the collision requires, then recompute rotation_y from its
  NEW position so it still faces the target.
