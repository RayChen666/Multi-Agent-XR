---
name: on_placement
description: >
  Put or add an object ON TOP of another object, e.g. "put the lamp on the table",
  "add a vase on the desk", "place the cup on that" (support = gazed object).
---

# On placement

## Geometry (must match tools/stackPlacement.py)
- Vertical: the object's bounding-box bottom rests exactly on the support's top
  (scene objects list it as `top_y`). This is the ONE case where y is not the floor.
- Horizontal: centered on the support, or at the gaze point on it, and kept fully
  on the support's top surface.
- Keep the object's rotation unless the command asks to turn it.

## When retrying after a collision
- Slide the object across the support's top; never lift it above `top_y` or drop it to the floor.
- If it is bigger than the support's top, say so in reasoning rather than placing it.
