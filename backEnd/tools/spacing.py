"""
Single source of every gap / inset in the placement pipeline. All values are
EDGE-TO-EDGE between axis-aligned bounding boxes (tools.aabbCheck.build_aabb),
in metres — never centre-to-centre.

Tools import these instead of hard-coding numbers, and the SceneAgent prompt and
SKILL.md files are generated from them (skills.registry substitutes $NAME), so the
LLM is always told the same numbers the deterministic tools use.
"""

# "next to" / "beside" / "adjacent to" another object (graphLayout object gap).
NEXT_TO_GAP = 0.1

# Minimum clearance between any two placed objects (gridLayout / aroundPlacement gap).
MIN_CLEARANCE = 0.15

# A new object placed in front of a target so it can face it (facingPlacement).
FACING_GAP = 0.5

# Distance from a wall, by relation (graphLayout). "against" / "attached to" = next_to.
WALL_INSET = {
    'next_to':     0.1,
    'adjacent_to': 0.1,
    'close_to':    0.25,
}

# Hard minimum between an object's edge and any wall (every room-bounds clamp).
ROOM_MARGIN = 0.05

# aabbCheck: overlaps shallower than this are not collisions; safe-position
# suggestions keep this much extra clearance.
COLLISION_TOLERANCE = 0.1
COLLISION_BUFFER = 0.05

# "on": an object counts as resting on a support when its bottom is within this
# of the support's top.
REST_TOLERANCE = 0.03


def prompt_values() -> dict:
    """Flat name -> value map for $NAME substitution in prompts / SKILL.md."""
    values = {k: v for k, v in globals().items() if k.isupper() and isinstance(v, (int, float))}
    values.update({f"WALL_INSET_{k.upper()}": v for k, v in WALL_INSET.items()})
    return values
