import numpy as np
import math

def are_too_far_to_collide(obj_a, obj_b) -> bool:
      """Broad phase cull — skip pair if geometrically impossible to collide."""
      col_a = obj_a.get('collision', {})
      col_b = obj_b.get('collision', {})

      width_a = col_a.get("width", 0)
      depth_a = col_a.get("depth", 0)
      width_b = col_b.get("width", 0)
      depth_b = col_b.get("depth", 0)

      radius_a = math.sqrt((width_a / 2) ** 2 + (depth_a / 2) ** 2)
      radius_b = math.sqrt((width_b / 2) ** 2 + (depth_b / 2) ** 2)

      threshold = radius_a + radius_b

      pos_a = obj_a['position']
      pos_b = obj_b['position']

      dx = pos_a['x'] - pos_b['x']
      dz = pos_a['z'] - pos_b['z']

      distance = math.sqrt(dx ** 2 + dz ** 2)

      return distance > threshold


def build_aabb(obj):
      """
      Build an Axis-Aligned Bounding Box (AABB) for the given object.
      Uses collision dims from metadata including geometric center offset.

      Parameters:
            obj (dict): object with 'position' and 'collision' fields.

      Returns:
            (mins, maxs): two lists [x, y, z] for the AABB corners.
      """
      pos = obj['position']
      col = obj['collision']

      w = col['width']
      h = col['height']
      d = col['depth']
      offset = col.get('offset', {'x': 0, 'y': 0, 'z': 0})

      ox = offset.get('x', 0)
      oy = offset.get('y', 0)
      oz = offset.get('z', 0)

      cx = pos['x'] + ox
      cy = pos['y'] + oy
      cz = pos['z'] + oz

      mins = [cx - w/2, cy - h/2, cz - d/2]
      maxs = [cx + w/2, cy + h/2, cz + d/2]

      return mins, maxs

def check_aabb_overlap(a_min, a_max, b_min, b_max, tolerance=0.05):
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
            buf = 0.1
            mover_half_w = obj_a['collision']['width'] / 2
            mover_half_d = obj_a['collision']['depth'] / 2

            safe_x_left  = round(min_b[0] - mover_half_w - buf, 3)  # mover right of anchor left edge
            safe_x_right = round(max_b[0] + mover_half_w + buf, 3)  # mover left of anchor right edge
            safe_z_back  = round(min_b[2] - mover_half_d - buf, 3)  # mover in front of anchor back edge
            safe_z_front = round(max_b[2] + mover_half_d + buf, 3)  # mover behind anchor front edge

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