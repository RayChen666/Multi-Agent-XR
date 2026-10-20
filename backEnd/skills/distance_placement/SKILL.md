---
name: distance_placement
description: >
  Place or move an object an explicit number of meters in front of / behind / left of /
  right of another object, e.g. "move the sink 1.5 meters behind the table",
  "add a chair 2m to the left of the desk", "put the lamp 1m in front of me" / "2m to my left"
  (anchor = 'user'). Only when the user states a number.
---

# Distance placement

## Geometry (must match tools/distancePlacement.py)
- Distance is EDGE-TO-EDGE between the two bounding boxes, not center-to-center.
- Directions are relative to the ANCHOR, never to where the user stands (unless the anchor IS the user):
  front = the side the anchor itself faces; back/behind = the opposite side;
  left/right = as seen by someone standing in front of the anchor, facing it.
- Each scene object lists its resolved sides in `sides`, e.g. a desk with rotation_y = 0
  has front = +Z, back = -Z, left = -X, right = +X. Use those, do not re-derive them.
- Anchor = the user: a POINT at the headset's x/z (never its y); front = where the user looks,
  left/right = the user's OWN left/right, snapped to the nearest world axis.
- On the cross axis the mover is centered on the anchor.
- y is left unchanged.

## When retrying after a collision
- Keep the requested gap on the travel axis exactly; resolve the collision by sliding
  along the CROSS axis instead.
- If the requested gap cannot fit inside the room bounds, say so in reasoning rather
  than silently shrinking it.
