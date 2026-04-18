import json
import os
import re
from typing import Dict, List, Optional, Tuple
import sys
from pathlib import Path
import google.generativeai as genai
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
from dotenv import load_dotenv
load_dotenv()
api_key = os.getenv("API_KEY")

from database import Database


class VerificationAgent:
    def __init__(self, database):
        # Initialize the database
        self.database = database
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-2.5-flash-lite')

    def get_object_state (self, object_name: str) -> Optional[Dict]:
        """
        Get current state of an object by name
        
        Args:
            object_name: Name of the object (e.g., "chair")
        
        Returns:
            Object dict with id, position, rotation, or None if not found
        """

        objects = self.database.get_objects_by_name(object_name)

        if not objects:
            print(f"No exact match for '{object_name}', trying semantic search...")
            objects = self.semantic_search(object_name)
            
            if not objects:
                print(f"No objects found matching '{object_name}'")
                return None
        
        if len(objects) > 1:
            print(f" ✓ Found {len(objects)} objects with name '{object_name}'")

        return [
            {
                'id': obj['id'],
                'name': obj['name'],
                'position': obj['position'],
                'rotation': obj['rotation']
            }
            for obj in objects
        ]

    def semantic_search(self, query: str) -> list:
        """
        Use LLM to find objects matching semantic query.
        Single compact method for all vague queries.
        """
        all_objects = self.database.scene_data.get('objects', [])

        if not all_objects:
            return []
        
        # Ask LLM which objects match
        object_list = "\n".join([f"- {obj['id']}: {obj['name']}" for obj in all_objects])
            
        prompt = f"""Query: "{query}"
            Available objects: {object_list}

            Which object IDs match? Consider semantic meaning (e.g., "furniture" = chairs, tables, sofas).
            Return JSON array of IDs, e.g., ["chair_01", "table_01"] or [] if none match.
            """
        
        try:
            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=200,
                    response_mime_type="application/json"
                )
            )
            
            matching_ids = json.loads(response.text)
            
            result = [obj for obj in all_objects if obj['id'] in matching_ids]
            
            if result:
                print(f"Semantic search found {len(result)} objects")
            
            return result
        
        except Exception as e:
            print(f"Semantic search error: {e}")
            return []

    def validate_transformation (self, transformation: Dict) -> bool:
        """
        Validate a transformation before execution.
        
        Args:
            transformation: Transformation dict to validate
            
        Returns:
            True if valid, False otherwise
        """
        if not transformation:
            return False
        
        required_fields = ['object_id', 'position', 'rotation', 'action']
        for field in required_fields:
            if field not in transformation:
                print(f"Missing required field: {field}")
                return False
        
        # Validate position format
        position = transformation['position']
        if not all(k in position for k in ['x', 'y', 'z']):
            print(f"Invalid position format")
            return False
        
        # Validate rotation format
        rotation = transformation['rotation']
        if not all(k in rotation for k in ['x', 'y', 'z']):
            print(f"Invalid rotation format")
            return False
        
        return True

    def validate_add_objects(self, complete_objects: List[Dict]) -> Dict:
        """
        Validate proposed_placement for add / add_multiple (not move/rotate shape).

        Returns:
            { "valid": bool, "message": str, "has_collision": bool }
        """
        if not complete_objects:
            return {
                "valid": False,
                "message": "No objects to add",
                "has_collision": False,
            }

        for i, obj in enumerate(complete_objects):

            if not isinstance(obj, dict):
                return {
                    "valid": False,
                    "message": f"Object entry {i} is not a dict",
                    "has_collision": False,
                }

            for field in ("id", "name", "position", "rotation"):
                if field not in obj:
                    return {
                        "valid": False,
                        "message": f"Missing {field} for object at index {i}",
                        "has_collision": False,
                    }

            pos = obj["position"]
            rot = obj["rotation"]

            if not isinstance(pos, dict) or not all(
                k in pos for k in ("x", "y", "z")
            ):
                return {
                    "valid": False,
                    "message": f"Invalid position for {obj.get('id', i)}",
                    "has_collision": False,
                }
                
            if not isinstance(rot, dict) or not all(
                k in rot for k in ("x", "y", "z")
            ):
                return {
                    "valid": False,
                    "message": f"Invalid rotation for {obj.get('id', i)}",
                    "has_collision": False,
                }

            if self.database.get_object_by_id(obj["id"]):
                return {
                    "valid": False,
                    "message": f"Object id already exists in scene: {obj['id']}",
                    "has_collision": False,
                }

        return {
            "valid": True,
            "message": "Verification passed",
            "has_collision": False,
        }

    def _infer_removal_multiplicity(self, policy: Dict) -> str:
        """
        Decide whether the user intent reads as a single target or multiple.
        Used to catch accidental mass-delete when the prompt was singular.
        """
        scope = (policy.get("scope_hint") or "contextual").strip().lower()
        if scope in ("all_movable", "all_matching_type"):
            return "multiple"

        prompt = (policy.get("original_prompt") or "").lower()
        if any(
            p in prompt
            for p in (
                "everything",
                "all objects",
                "clear the scene",
                "clear everything",
                "delete everything",
                "remove everything",
            )
        ):
            return "multiple"
            
        if re.search(r"\ball\b", prompt):
            return "multiple"

        padded = f" {prompt} "
        if any(
            f" {w} " in padded
            for w in (
                "those",
                "these",
                "both",
                "two",
                "three",
                "four",
                "five",
            )
        ):
            return "multiple"

        involved = policy.get("involved_objects") or []
        for token in involved:
            t = str(token).lower().strip()
            if len(t) >= 4 and t.endswith("s") and not t.endswith("ss"):
                return "multiple"

        return "single"

    def infer_removal_multiplicity(self, policy: Dict) -> str:
        """Public helper: same rules as validate_removal (single vs multiple intent)."""
        return self._infer_removal_multiplicity(policy)

    def narrow_removal_ids_to_closest(
        self,
        object_ids: List[str],
        scene_state: Dict,
        user_position: Optional[Dict] = None,
    ) -> List[str]:
        """
        When singular intent produced multiple ids, keep one instance: closest to the user.
        """
        if len(object_ids) <= 1:
            return object_ids

        ux = uy = uz = 0.0
        if user_position:
            ux = float(user_position.get("x", 0))
            uy = float(user_position.get("y", 0))
            uz = float(user_position.get("z", 0))

        by_id = {
            o["id"]: o
            for o in scene_state.get("objects", [])
            if o.get("id")
        }

        best_id: Optional[str] = None
        best_d2: Optional[float] = None
        for oid in object_ids:
            o = by_id.get(oid)
            if not o:
                continue
            p = o.get("position") or {}
            x = float(p.get("x", 0))
            y = float(p.get("y", 0))
            z = float(p.get("z", 0))
            d2 = (x - ux) ** 2 + (y - uy) ** 2 + (z - uz) ** 2
            if best_d2 is None or d2 < best_d2:
                best_d2 = d2
                best_id = oid

        if best_id is None:
            return [object_ids[0]]
        return [best_id]

    def _object_allowed_for_removal(
        self, obj: Dict, allow_structural: bool
    ) -> Tuple[bool, str]:
        """Movable / non-structural guard (default: block floor, walls, fixed props)."""
        if allow_structural:
            return True, ""

        if (obj.get("category") or "").lower() == "structure":
            return False, "category=structure"

        props = obj.get("properties") or {}
        if props.get("structural") is True:
            return False, "structural"
        if props.get("movable") is False:
            return False, "not movable"

        return True, ""

    def validate_removal(
        self,
        target_object_ids: List[str],
        policy: Optional[Dict] = None,
    ) -> Dict:
        """
        Validate a proposed removal before execution (not transform / collision).

        Steps:
        1. Normalize ids (dedupe, drop blanks).
        2. Singular vs plural: align count with intent from scope_hint + prompt heuristics.
        3. Existence: every id must resolve in the scene DB.
        4. Safety: optional movable-only / non-structural policy.

        Args:
            target_object_ids: SceneAgent-resolved ids to delete.
            policy: Optional keys:
                - scope_hint: from AssetAgent remove_intent ("contextual" | "all_movable" | "all_matching_type")
                - original_prompt: user text (singular/plural heuristics)
                - involved_objects: language hints
                - enforce_movable_only: default True
                - allow_structural: default False (if True, skip structural/movable blocks)

        Returns:
            { "valid": bool, "message": str, "has_collision": False }
        """
        policy = policy or {}

        raw = [str(x).strip() for x in (target_object_ids or []) if str(x).strip()]
        ids = list(dict.fromkeys(raw))

        if not ids:
            return {
                "valid": False,
                "message": "No objects selected for removal",
                "has_collision": False,
            }

        multiplicity = self._infer_removal_multiplicity(policy)
        if multiplicity == "single" and len(ids) != 1:
            return {
                "valid": False,
                "message": (
                    f"Singular removal intent requires exactly one object; "
                    f"got {len(ids)} ({', '.join(ids)})"
                ),
                "has_collision": False,
            }

        enforce_movable = policy.get("enforce_movable_only", True)
        allow_structural = policy.get("allow_structural", False)

        missing: List[str] = []
        blocked: List[str] = []

        for oid in ids:
            obj = self.database.get_object_by_id(oid)
            if not obj:
                missing.append(oid)
                continue
            if enforce_movable:
                ok, reason = self._object_allowed_for_removal(obj, allow_structural)
                if not ok:
                    blocked.append(f"{oid} ({reason})")

        if missing:
            return {
                "valid": False,
                "message": f"Objects not found: {', '.join(missing)}",
                "has_collision": False,
            }

        if blocked:
            return {
                "valid": False,
                "message": f"Cannot remove protected objects: {', '.join(blocked)}",
                "has_collision": False,
            }

        return {
            "valid": True,
            "message": "Verification passed",
            "has_collision": False,
        }

# Test
if __name__ == "__main__":
    from database import Database
    
    # Initialize database
    db = Database()
    
    # Initialize code agent
    agent = VerificationAgent(db)
    
    # Get object state
    states = agent.get_object_state("chair")
    print(f"Current state: {states}")
    
    first_state = states[0]
    id = first_state.get("id")
    name = first_state.get("name")
    position = first_state.get("position")
    rotation = first_state.get("rotation")
    # Execute update
    if states:
        updates = {
            'position': {'x': 0.5, 'y': 0, 'z': -1.5}
        }
    
    test_transformation = {
        'object_id': id,
        'name': name,
        'position': position,
        'rotation': {'x': 0, 'y': -1.5707963267948966, 'z': 0},
        'action': 'move'
    }

    # Test validation
    result = agent.validate_transformation(test_transformation)
    print(result)
