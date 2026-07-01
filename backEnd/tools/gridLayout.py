import math
from typing import Dict, List


def grid_layout_positions(objects: List[Dict], room_bounds: Dict, gap: float = 0.15) -> List[Dict]:
    """
    Deterministically place N objects in a grid that fits inside room_bounds.

    Args:
        objects:     List of object dicts, each optionally containing a 'collision' key
                     with 'width' and 'depth' fields.
        room_bounds: Dict with 'min' and 'max' sub-dicts (each with 'x', 'y', 'z').
        gap:         Minimum clearance between adjacent object bounding boxes (metres).

    Returns:
        List of {'x', 'y', 'z'} position dicts in the same order as `objects`,
        or an empty list if no valid grid fits in the room.
    """
    if not objects or not room_bounds:
        return []

    W = max((obj.get('collision') or {}).get('width', 0.4) for obj in objects)
    D = max((obj.get('collision') or {}).get('depth', 0.4) for obj in objects)

    wall_buf = 0.1
    min_x = room_bounds['min']['x'] + W / 2 + wall_buf
    max_x = room_bounds['max']['x'] - W / 2 - wall_buf
    min_z = room_bounds['min']['z'] + D / 2 + wall_buf
    max_z = room_bounds['max']['z'] - D / 2 - wall_buf

    cell_x = W + gap
    cell_z = D + gap
    available_x = max_x - min_x
    available_z = max_z - min_z
    N = len(objects)

    best = None
    for cols in range(N, 0, -1):
        rows = math.ceil(N / cols)
        if (cols - 1) * cell_x <= available_x + 1e-6 and (rows - 1) * cell_z <= available_z + 1e-6:
            best = (cols, rows)
            break

    if not best:
        return []

    cols, rows = best
    start_x = (min_x + max_x) / 2 - (cols - 1) * cell_x / 2
    start_z = (min_z + max_z) / 2 - (rows - 1) * cell_z / 2

    positions = []
    for i in range(N):
        r = i // cols
        c = i % cols
        positions.append({
            'x': round(start_x + c * cell_x, 3),
            'y': -1.0,
            'z': round(start_z + r * cell_z, 3),
        })
    return positions
