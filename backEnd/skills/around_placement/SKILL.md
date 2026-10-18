---
name: around_placement
description: >
  Arrange several objects evenly around one anchor object, each facing it, e.g.
  "put the chairs around the table", "add 4 stools around the island".
---

# Around placement

## Geometry (must match tools/aroundPlacement.py)
- Movers sit on a circle centered on the anchor, evenly spaced (360°/N apart),
  the first one directly in front of the anchor (its `sides.front`).
- Radius = max(anchor half-width, anchor half-depth) + max(mover half-extents) + $MIN_CLEARANCE m.
- Every mover faces the anchor: rotation_y = atan2(anchor.x - x, anchor.z - z).

## When retrying after a collision
- Rotate the whole ring (same start offset for all movers) or enlarge the radius slightly;
  do not drop movers or bunch them on one side.
- Recompute each rotation_y from the new position so all still face the anchor.
