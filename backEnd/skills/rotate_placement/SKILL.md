---
name: rotate_placement
description: >
  Turn existing object(s) in place by an angle, e.g. "rotate the table by 180 degrees",
  "turn the desk 90 degrees anticlockwise", "turn the sofa around". Only when an amount is
  given; "turn the chair to face the TV" is facing_placement.
---

# Rotate placement

## Geometry (must match tools/rotatePlacement.py)
- rotation.y is in radians; clockwise as seen from above is NEGATIVE, anticlockwise positive.
  new rotation_y = current rotation_y ∓ degrees·π/180, wrapped to [-π, π].
- The object turns about its bounding-box CENTER, not its model origin (`position`):
  the box center (position + center_offset) stays where it is, so `position` itself
  changes whenever center_offset is not zero.
  new position = box center − center_offset rotated to the new yaw.
- y is unchanged.

## When retrying after a collision
- Keep the requested angle and the box center; if the turned object collides, say so in
  reasoning rather than moving it elsewhere or changing the angle.
