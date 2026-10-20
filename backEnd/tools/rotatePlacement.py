import math
import sys
from pathlib import Path
from typing import Dict
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.aabbCheck import world_collision

"""
Rotate an object in place about its bounding-box centre.

An object's `position` is its model origin, and the frontend (three.js) turns the
model about that origin. Many assets have their origin far from the geometry
(desk: 0.72 m, cabinet: 0.72 m), so changing only rotation.y swings the object
around a corner. rotate_about_centre() moves the origin so the box centre (x, z)
stays put:

    centre       = position + offset(old yaw)
    new position = centre   - offset(new yaw)

offset(yaw) is collision.offset rotated by yaw (tools.aabbCheck.world_collision).
"""


def normalize_yaw(yaw: float) -> float:
    """Wrap to [-π, π]."""
    return math.atan2(math.sin(yaw), math.cos(yaw))


def rotate_about_centre(obj: Dict, new_yaw: float) -> Dict:
    """
    Position for `obj` (with its CURRENT rotation and position) after turning it to
    `new_yaw` so its box centre does not move. y is unchanged. Objects without
    collision data keep their position (centre unknown = origin).
    """
    position = dict(obj.get('position') or {})
    if not obj.get('collision'):
        return position
    old_off = world_collision(obj)['offset']
    new_off = world_collision({**obj, 'rotation': {**(obj.get('rotation') or {}), 'y': new_yaw}})['offset']
    for axis in ('x', 'z'):
        position[axis] = round(float(position.get(axis, 0)) + old_off[axis] - new_off[axis], 3)
    return position
