import numpy as np
import math

from tools.spacing import COLLISION_BUFFER, COLLISION_TOLERANCE, ROOM_MARGIN

def world_collision(obj) -> dict:
      """
      The object's collision box in WORLD axes, accounting for its yaw (rotation.y):
      {'width', 'height', 'depth', 'offset': {x, y, z}}.

      metadata 'collision' is in the model's local frame. The frontend's AABB helper
      is a child of the rotated model, so a desk turned 90° really spans its depth
      along x. three.js yaw θ maps local (x, z) to (x·cosθ + z·sinθ, −x·sinθ + z·cosθ)
      — the same convention as tools/orientation.forward_vector. width/depth are the
      AABB of the rotated box (exact at 0 / ±π/2 / π, conservative in between).
      """
      col = obj['collision']
      ry = float((obj.get('rotation') or {}).get('y', 0) or 0)
      c, s = math.cos(ry), math.sin(ry)
      w, d = float(col['width']), float(col['depth'])
      off = col.get('offset') or {}
      ox, oz = float(off.get('x', 0)), float(off.get('z', 0))
      return {
            'width':  abs(w * c) + abs(d * s),
            'height': float(col['height']),
            'depth':  abs(w * s) + abs(d * c),
            'offset': {'x': ox * c + oz * s, 'y': float(off.get('y', 0)), 'z': -ox * s + oz * c},
      }


def are_too_far_to_collide(obj_a, obj_b) -> bool:
      """Broad phase cull — skip pair if geometrically impossible to collide."""
      if not obj_a.get('collision') or not obj_b.get('collision'):
            return True
      col_a, col_b = world_collision(obj_a), world_collision(obj_b)

      radius_a = math.hypot(col_a['width'] / 2, col_a['depth'] / 2)
      radius_b = math.hypot(col_b['width'] / 2, col_b['depth'] / 2)

      # Measure between box centres, not origins: offsets reach 0.7 m on some assets.
      dx = (obj_a['position']['x'] + col_a['offset']['x']) - (obj_b['position']['x'] + col_b['offset']['x'])
      dz = (obj_a['position']['z'] + col_a['offset']['z']) - (obj_b['position']['z'] + col_b['offset']['z'])

      return math.hypot(dx, dz) > radius_a + radius_b


def build_aabb(obj):
      """
      Build an Axis-Aligned Bounding Box (AABB) for the given object.
      Uses collision dims from metadata including geometric center offset,
      rotated by the object's yaw (world_collision).

      Parameters:
            obj (dict): object with 'position', 'collision' and optionally 'rotation'.

      Returns:
            (mins, maxs): two lists [x, y, z] for the AABB corners.
      """
      pos = obj['position']
      col = world_collision(obj)

      w = col['width']
      h = col['height']
      d = col['depth']
      offset = col['offset']

      cx = pos['x'] + offset['x']
      cy = pos['y'] + offset['y']
      cz = pos['z'] + offset['z']

      mins = [cx - w/2, cy - h/2, cz - d/2]
      maxs = [cx + w/2, cy + h/2, cz + d/2]

      return mins, maxs

def check_aabb_overlap(a_min, a_max, b_min, b_max, tolerance=COLLISION_TOLERANCE):
      for i in range(3):
            if a_max[i] <= b_min[i] + tolerance:
                  return False
            if b_max[i] <= a_min[i] + tolerance:
                  return False
      return True


def check_proposed_objects(proposed_objects, existing_objects) -> list:
      """
      Check proposed objects against each other and against existing scene objects.
      Returns list of violations with mover/anchor/overlap context and
      concrete safe positions for SceneAgent to use on retry.
      """
      violations = []

      def check_pair(obj_a, obj_b):
            if are_too_far_to_collide(obj_a, obj_b):
                  return None
            if not obj_a.get('collision') or not obj_b.get('collision'):
                  return None

            min_a, max_a = build_aabb(obj_a)
            min_b, max_b = build_aabb(obj_b)

            if not check_aabb_overlap(min_a, max_a, min_b, max_b):
                  return None

            # compute overlap on each axis
            overlap = [
                  min(max_a[i], max_b[i]) - max(min_a[i], min_b[i])
                  for i in range(3)
            ]
            ox, oy, oz = overlap[0], overlap[1], overlap[2]

            # compute safe positions from AABB edges (not anchor center)
            # so the mover's edge clears the anchor's edge by buf meters
            # Safe values are mover POSITIONS (origins): box-centre limits minus its offset.
            buf = COLLISION_BUFFER
            mover_col = world_collision(obj_a)
            mover_half_w = mover_col['width'] / 2
            mover_half_d = mover_col['depth'] / 2
            mox, moz = mover_col['offset']['x'], mover_col['offset']['z']

            safe_x_left  = round(min_b[0] - mover_half_w - buf - mox, 3)  # mover entirely at -X of anchor
            safe_x_right = round(max_b[0] + mover_half_w + buf - mox, 3)  # mover entirely at +X of anchor
            safe_z_back  = round(min_b[2] - mover_half_d - buf - moz, 3)  # mover entirely at -Z of anchor
            safe_z_front = round(max_b[2] + mover_half_d + buf - moz, 3)  # mover entirely at +Z of anchor

            suggestion = (
                  f"Move {obj_a['id']} away from {obj_b['id']}. "
                  f"Current mover position: x={obj_a['position']['x']}, z={obj_a['position']['z']}. "
                  f"Safe X: x <= {safe_x_left} OR x >= {safe_x_right}. "
                  f"Safe Z: z <= {safe_z_back} OR z >= {safe_z_front}. "
                  f"Pick the direction closest to the user intent. "
                  f"DO NOT reuse the current position."
            )

            return {
                  "type": "collision",
                  "mover": obj_a['id'],
                  "anchor": obj_b['id'],
                  "overlap": {"x": ox, "y": oy, "z": oz},
                  "mover_position": obj_a['position'],
                  "anchor_position": obj_b['position'],
                  "severity": "error",
                  "suggestion": suggestion
            }

      # Group A — proposed vs proposed
      for i in range(len(proposed_objects)):
            for j in range(i + 1, len(proposed_objects)):
                  result = check_pair(proposed_objects[i], proposed_objects[j])
                  if result:
                        violations.append(result)

      # Group B — proposed vs existing
      for proposed in proposed_objects:
            for existing in existing_objects:
                  result = check_pair(proposed, existing)
                  if result:
                        violations.append(result)

      return violations


def check_collision(scene_objects):
      violations = []
      boxes = [(obj["id"], *build_aabb(obj)) for obj in scene_objects]
      for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                  id_a, min_a, max_a = boxes[i]
                  id_b, min_b, max_b = boxes[j]
                  if check_aabb_overlap(min_a, max_a, min_b, max_b):
                        violations.append({
                              "type": "collision",
                              "objects": [id_a, id_b],
                              "severity": "error"
                        })
      return violations

WALL_NAMES = {
      ('x', 'min'): 'left wall', ('x', 'max'): 'right wall',
      ('z', 'min'): 'back wall', ('z', 'max'): 'front wall',
}


def _wall_problem(clearance, wall, margin) -> str:
      """clearance = edge-to-wall distance, negative when the AABB is inside the wall."""
      if clearance < 0:
            return f"goes {-clearance:.2f}m into the {wall}"
      return f"is only {clearance:.2f}m from the {wall} (minimum {margin}m)"


def check_room_bounds(objects, room_bounds, margin=ROOM_MARGIN) -> list:
      """
      Every object's AABB must stay inside the walls: x/z edges at least `margin`
      inside each wall (tools/spacing.py ROOM_MARGIN). Returns one violation per
      offending object, with the nearest in-bounds position as `suggested_position`;
      `fixable` is False when the object is wider than the room itself.
      (No floor check: door/sun models' origins are not at their base, so a floor
      check would reject them at the y=-3 every placement tool uses.)
      """
      violations = []
      eps = 1e-3  # positions are rounded to mm; don't flag rounding noise
      for obj in objects:
            if not obj.get('collision') or not obj.get('position'):
                  continue
            lo, hi = build_aabb(obj)
            pos = obj['position']
            fixed = {k: pos[k] for k in ('x', 'y', 'z')}
            problems, fixable = [], True

            for i, axis in ((0, 'x'), (2, 'z')):
                  lo_ok = room_bounds['min'][axis] + margin
                  hi_ok = room_bounds['max'][axis] - margin
                  if hi[i] - lo[i] > hi_ok - lo_ok + eps:
                        fixable = False
                        problems.append(f"is {hi[i] - lo[i]:.2f}m along {axis}, wider than the room")
                  elif lo[i] < lo_ok - eps:
                        problems.append(_wall_problem(lo[i] - room_bounds['min'][axis], WALL_NAMES[axis, 'min'], margin))
                        fixed[axis] = round(pos[axis] + (lo_ok - lo[i]), 3)
                  elif hi[i] > hi_ok + eps:
                        problems.append(_wall_problem(room_bounds['max'][axis] - hi[i], WALL_NAMES[axis, 'max'], margin))
                        fixed[axis] = round(pos[axis] - (hi[i] - hi_ok), 3)

            if problems:
                  message = f"{obj['id']} " + " and ".join(problems)
                  if fixable:
                        message += f"; move its center to x={fixed['x']}, y={fixed['y']}, z={fixed['z']}"
                  violations.append({
                        'message': message,
                        'fixable': fixable,
                        'kind': 'room_bounds',
                        'object_id': obj['id'],
                        'suggested_position': fixed,
                  })
      return violations


def clamp_into_room(obj, position, room_bounds, margin=ROOM_MARGIN) -> dict:
      """
      Nearest position (origin) for `obj` at which its rotated box stays `margin`
      inside every wall. Works in box-centre space, so offsets are respected.
      An axis on which the object is wider than the room is left unchanged.
      """
      col = world_collision(obj)
      out = dict(position)
      for axis, size in (('x', col['width']), ('z', col['depth'])):
            centre = float(position[axis]) + col['offset'][axis]
            lo = room_bounds['min'][axis] + margin + size / 2
            hi = room_bounds['max'][axis] - margin - size / 2
            if lo <= hi:
                  centre = min(max(centre, lo), hi)
            out[axis] = round(centre - col['offset'][axis], 3)
      return out
