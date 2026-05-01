import numpy as np


def build_aabb(obj):
      """
            Build an Axis-Aligned Bounding Box (AABB) for the given object.
            The AABB is defined by its minimum and maximum corners in 3D space.
            
            Parameters:
            obj (dict): A dictionary containing the object's position and size.
                        Expected keys: 'position' (dict with 'x', 'y', 'z') and 'size' (dict with 'width', 'height', 'depth').
            
            Returns:
            dict: A dictionary containing the min and max corners of the AABB.
                  Keys: 'min' (dict with 'x', 'y', 'z') and 'max' (dict with 'x', 'y', 'z').
      """
      pos = obj['position'] # [x,y,z]
      scale = obj['scale'] # []
      half = [s / 2 for s in scale]
      mins = [pos[i] - half[i] for i in range(3)]
      maxs = [pos[i] + half[i] for i in range(3)]
      return mins, maxs

def check_aabb_overlap(a_min, a_max, b_min, b_max, tolerance=0.05):
      for i in range(3):
            if a_max[i] <= b_min[i] + tolerance:
                  return False
            if b_max[i] <= a_min[i] + tolerance:
                  return False
      return True
      
def check_collision(scene_objects):
      violations = []
      boxes =[(obj["id"], *build_aabb(obj)) for obj in scene_objects] 
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