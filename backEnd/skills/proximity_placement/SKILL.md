---
name: proximity_placement
description: >
  Move existing object(s) closer to / farther from another object or the user, e.g.
  "move both chairs closer to the table", "bring the lamp closer to me",
  "push the sofa away from the TV", "move the desk closer to the left wall".
  No explicit side (front/left/...) is given.
---

# Proximity placement

## Geometry (must match tools/proximityPlacement.py)
- The anchor is an object, a wall (back/front/left/right wall = that room bound), or the
  user ("me"): the user is a POINT, the headset position projected on the floor; gaps to
  the user run from the mover's box edge to that point.
- Each mover moves on its OWN side of the anchor: a chair left of the table moves right,
  one right of it moves left. Never move movers to the same spot or re-center them.
- Distances are EDGE-TO-EDGE between bounding boxes, measured per axis. A mover travels
  only on the axes where its box is separated from the anchor's; off a corner it moves
  diagonally, changing both gaps by the same fraction.
- closer: each gap becomes $PROXIMITY_RATIO of what it was, but never below $PROXIMITY_MIN_GAP m
  to an object or $USER_MIN_GAP m to the user. farther: each gap grows by the same step,
  at least $PROXIMITY_MIN_STEP m. A stated distance ("0.5 m closer") replaces the ratio.
- Keep rotation and y unchanged.

## When retrying after a collision
- Shorten the move along the same direction (stop just before the obstacle); do not
  sidestep the mover around it.
- If a mover cannot move at all (blocked, already at the minimum gap, or at a wall),
  leave it where it is and say so in reasoning.
