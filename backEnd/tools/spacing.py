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

# "closer to" / "farther from" (proximityPlacement): each request keeps this fraction
# of the edge-to-edge gap to the anchor (closer) or grows it by the same step (farther),
# moving at least PROXIMITY_MIN_STEP when farther.
PROXIMITY_RATIO = 0.5
PROXIMITY_MIN_STEP = 0.3
# Closest an object's edge may come to an anchor object's edge / to the user's
# headset position projected on the floor (the user is a point, not a box).
PROXIMITY_MIN_GAP = 0.1
USER_MIN_GAP = 0.5

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
