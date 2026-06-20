import json
import os
import re
from typing import Dict, List, Optional, Tuple, Any
import sys
from pathlib import Path
import google.generativeai as genai
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.aabbCheck import check_proposed_objects
from database import Database
from dotenv import load_dotenv

class VerificationAgent:
    def __init__(self, database):
        # Initialize the database
        self.database = database
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
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
            print(f"  No exact match for '{object_name}', trying semantic search...")
            objects = self.semantic_search(object_name)
            
            if not objects:
                print(f"  No objects found matching '{object_name}'")
                return None
        
        if len(objects) > 1:
            print(f" Found {len(objects)} objects with name '{object_name}'")

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
                print(f"  Semantic search found {len(result)} objects")
            
            return result
        
        except Exception as e:
            print(f"  Semantic search error: {e}")
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
    
    '''
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
    '''

    def validate_add_objects(self, complete_objects: List[Dict]) -> Dict:
        """
        Validate proposed placement for add / add_multiple.
        Runs schema check first, then AABB geometric collision check.

        Returns:
            { "valid": bool, "message": str, "has_collision": bool, "violations": list }
        """
        if not complete_objects:
            return {
                "valid": False,
                "message": "No objects to add",
                "has_collision": False,
                "violations": []
            }
        
        # Stage 1: Schema validation
        for i, obj in enumerate(complete_objects):
            if not isinstance(obj, dict):
                return {"valid": False, "message": f"Object entry {i} is not a dict",
                        "has_collision": False, "violations": []}
            
            for field in ("id", "name", "position", "rotation"):
                if field not in obj:
                    return {"valid": False, "message": f"Missing {field} for object at index {i}",
                        "has_collision": False, "violations": []}
                
            pos = obj["position"]
            rot = obj["rotation"]

            if not isinstance(pos, dict) or not all(k in pos for k in ("x", "y", "z")):
                return {"valid": False, "message": f"Invalid position for {obj.get('id', i)}",
                    "has_collision": False, "violations": []}
            if not isinstance(rot, dict) or not all(k in rot for k in ("x", "y", "z")):
                return {"valid": False, "message": f"Invalid rotation for {obj.get('id', i)}",
                    "has_collision": False, "violations": []}
            if self.database.get_object_by_id(obj["id"]):
                return {"valid": False, "message": f"Object id already exists in scene: {obj['id']}",
                        "has_collision": False, "violations": []}
            
        # Stage 2: Collision check (AABB)
        existing_objects = self.database.scene_data.get('objects', [])
        violations = check_proposed_objects(complete_objects, existing_objects)

        if violations:
            print(f"  AABB collision detected: {len(violations)} violation(s)")
            for v in violations:
                print(f"    {v['mover']} ↔ {v['anchor']} overlap={v['overlap']}")
            return {
                "valid": False,
                "message": f"Geometric collision detected between {len(violations)} object pair(s)",
                "has_collision": True,
                "violations": violations
            }
        
        return {
            "valid": True,
            "message": "Verification passed",
            "has_collision": False,
            "violations": []
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
        user_position: Optional[Dict] = None, ) -> List[str]:
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
        self, obj: Dict, allow_structural: bool ) -> Tuple[bool, str]:
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
        policy: Optional[Dict] = None, ) -> Dict:
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

        # Step 5 path: when per-target specs exist, enforce cardinality per spec instead
        # of legacy global single/multiple inference.
        remove_intent = (
            policy.get("remove_intent")
            or policy.get("delete_intent")
            or {
                "global_scope": policy.get("global_scope", "none"),
                "target_specs": policy.get("target_specs", []),
                "original_prompt": policy.get("original_prompt", ""),
            }
        )
        if isinstance(remove_intent, dict) and (
            remove_intent.get("global_scope") == "all_objects"
            or (remove_intent.get("target_specs") and isinstance(remove_intent.get("target_specs"), list))
        ):
            return self.validate_removal_against_specs(
                target_object_ids=target_object_ids,
                remove_intent=remove_intent,
                scene_state=self.database.scene_data,
                user_position=None,
                enforce_movable_only=policy.get("enforce_movable_only", True),
                allow_structural=policy.get("allow_structural", False),
            )

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

    def validate_removal_against_specs(
        self,
        target_object_ids: List[str],
        remove_intent: Dict[str, Any],
        scene_state: Dict[str, Any],
        user_position: Optional[Dict[str, Any]],
        enforce_movable_only: bool = True,
        allow_structural: bool = False,
    ) -> Dict[str, Any]:

        """
        Per-target remove verification contract.

        Checks:
        1) Selected ids exist and pass movable/protected policy.
        2) Per target_spec cardinality is satisfied.
        3) Global all_objects scope is respected when requested.
        """

        raw_ids = [str(x).strip() for x in (target_object_ids or []) if str(x).strip()]
        ids = list(dict.fromkeys(raw_ids))
        if not ids:
            return {
                "valid": False,
                "message": "No objects selected for removal",
                "has_collision": False,
                "clarification_required": True,
            }

        remove_intent = remove_intent if isinstance(remove_intent, dict) else {}
        global_scope = str(remove_intent.get("global_scope", "none")).strip().lower()
        if global_scope not in {"none", "all_objects"}:
            global_scope = "none"

        raw_specs = remove_intent.get("target_specs", [])
        target_specs = raw_specs if isinstance(raw_specs, list) else []

        objects = scene_state.get("objects", []) if isinstance(scene_state, dict) else []
        by_id = {o.get("id"): o for o in objects if isinstance(o, dict) and o.get("id")}

        missing: List[str] = []
        blocked: List[str] = []
        for oid in ids:
            obj = by_id.get(oid) or self.database.get_object_by_id(oid)
            if not obj:
                missing.append(oid)
                continue
            if enforce_movable_only:
                ok, reason = self._object_allowed_for_removal(obj, allow_structural)
                if not ok:
                    blocked.append(f"{oid} ({reason})")

        if missing:
            return {
                "valid": False,
                "message": f"Objects not found: {', '.join(missing)}",
                "has_collision": False,
                "clarification_required": False,
            }

        if blocked:
            return {
                "valid": False,
                "message": f"Cannot remove protected objects: {', '.join(blocked)}",
                "has_collision": False,
                "clarification_required": False,
            }

        # Global "all objects" contract.
        if global_scope == "all_objects":
            eligible_ids = []
            for obj in objects:
                if not isinstance(obj, dict) or not obj.get("id"):
                    continue
                if not enforce_movable_only:
                    eligible_ids.append(obj["id"])
                    continue
                ok, _ = self._object_allowed_for_removal(obj, allow_structural)
                if ok:
                    eligible_ids.append(obj["id"])

            selected_set = set(ids)
            missing_eligible = [oid for oid in eligible_ids if oid not in selected_set]
            if missing_eligible:
                return {
                    "valid": False,
                    "message": (
                        "Global all_objects intent underfilled; missing "
                        f"{len(missing_eligible)} eligible object(s)"
                    ),
                    "has_collision": False,
                    "clarification_required": False,
                }
            return {
                "valid": True,
                "message": "Verification passed",
                "has_collision": False,
            }

        # Per-spec cardinality contract.
        normalized_specs = self._normalize_target_specs(target_specs)
        if not normalized_specs:
            # No structured spec: keep compatible behavior with a successful existence/safety check.
            return {
                "valid": True,
                "message": "Verification passed",
                "has_collision": False,
            }

        selected_by_id: Dict[str, Dict[str, Any]] = {}
        for oid in ids:
            obj = by_id.get(oid) or self.database.get_object_by_id(oid)
            if isinstance(obj, dict):
                selected_by_id[oid] = obj
        unassigned_ids = set(selected_by_id.keys())

        for spec in normalized_specs:
            spec_type = spec["object_type"]
            quantity_mode = spec["quantity_mode"]
            quantity = spec["quantity"]

            eligible_of_type = []
            for obj in objects:
                if not isinstance(obj, dict):
                    continue
                if not self._object_matches_spec(obj, spec, user_position):
                    continue
                if enforce_movable_only:
                    ok, _ = self._object_allowed_for_removal(obj, allow_structural)
                    if not ok:
                        continue
                eligible_of_type.append(obj)

            if len(eligible_of_type) == 0:
                return {
                    "valid": False,
                    "message": (
                        f"No removable candidates found for '{spec_type}'. "
                        "Try a different object reference."
                    ),
                    "has_collision": False,
                    "clarification_required": True,
                }

            selected_matches = []
            for oid in list(unassigned_ids):
                obj = selected_by_id.get(oid)
                if not obj:
                    continue
                if self._object_matches_spec(obj, spec, user_position):
                    selected_matches.append({"id": oid, "obj": obj})

            # Deterministic ordering keeps assignments stable across runs.
            selected_matches.sort(
                key=lambda entry: (
                    self._distance_sq_to_user(entry["obj"], user_position),
                    str(entry["id"]),
                )
            )

            if quantity_mode == "exact":
                if len(selected_matches) < quantity:
                    return {
                        "valid": False,
                        "message": (
                            f"Spec underfilled for '{spec_type}': required {quantity}, "
                            f"selected {len(selected_matches)}"
                        ),
                        "has_collision": False,
                        "clarification_required": True,
                    }
                # Consume only the amount required for this spec; any remaining
                # selected ids may satisfy later specs.
                assigned_ids = [entry["id"] for entry in selected_matches[:quantity]]
                for oid in assigned_ids:
                    unassigned_ids.discard(oid)
            else:
                # "all" means all eligible objects of that type should be selected.
                if len(selected_matches) < len(eligible_of_type):
                    return {
                        "valid": False,
                        "message": (
                            f"Spec underfilled for all '{spec_type}': selected {len(selected_matches)} "
                            f"of {len(eligible_of_type)} eligible object(s)"
                        ),
                        "has_collision": False,
                        "clarification_required": True,
                    }
                for entry in selected_matches:
                    unassigned_ids.discard(entry["id"])

        if unassigned_ids:
            extras = ", ".join(sorted(unassigned_ids))
            return {
                "valid": False,
                "message": (
                    "Selected remove targets include extra id(s) that do not map "
                    f"to any target spec: {extras}"
                ),
                "has_collision": False,
                "clarification_required": False,
            }

        return {
            "valid": True,
            "message": "Verification passed",
            "has_collision": False,
        }

    def _normalize_target_specs(self, specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:

        normalized: List[Dict[str, Any]] = []
        for spec in specs:
            if not isinstance(spec, dict):
                continue

            object_type = str(spec.get("object_type", "")).strip()
            if not object_type:
                continue

            quantity_mode = str(spec.get("quantity_mode", "exact")).strip().lower()
            if quantity_mode not in {"exact", "all"}:
                quantity_mode = "exact"

            quantity = spec.get("quantity", 1)

            try:
                quantity = int(quantity)
            except (TypeError, ValueError):
                quantity = 1

            quantity = max(1, quantity)

            normalized.append({
                "object_type": object_type,
                "quantity_mode": quantity_mode,
                "quantity": quantity,
                "spatial_filter": spec.get("spatial_filter"),
            })

        return normalized

    def _object_matches_spec(
        self,
        obj: Dict[str, Any],
        spec: Dict[str, Any],
        user_position: Optional[Dict[str, Any]],
    ) -> bool:

        if not self._object_matches_type(obj, spec.get("object_type", "")):
            return False

        return self._object_matches_spatial_filter(
            obj=obj,
            spatial_filter=spec.get("spatial_filter"),
            user_position=user_position,
        )

    def _distance_sq_to_user(
        self,
        obj: Dict[str, Any],
        user_position: Optional[Dict[str, Any]],
    ) -> float:

        pos = obj.get("position") or {}
        ux = float((user_position or {}).get("x", 0))
        uy = float((user_position or {}).get("y", 0))
        uz = float((user_position or {}).get("z", 0))
        ox = float(pos.get("x", 0))
        oy = float(pos.get("y", 0))
        oz = float(pos.get("z", 0))

        return (ox - ux) ** 2 + (oy - uy) ** 2 + (oz - uz) ** 2

    def _object_matches_spatial_filter(
        self,
        obj: Dict[str, Any],
        spatial_filter: Any,
        user_position: Optional[Dict[str, Any]],
    ) -> bool:

        if not isinstance(spatial_filter, dict):
            return True

        relation = str(spatial_filter.get("relation", "")).strip().lower()
        target = str(spatial_filter.get("target", "")).strip().lower()

        if not relation:
            return True

        # For now, only enforce explicit user-relative filters.
        if target and target != "user":
            return True

        pos = obj.get("position") or {}
        ox = float(pos.get("x", 0))
        oz = float(pos.get("z", 0))
        ux = float((user_position or {}).get("x", 0))
        uz = float((user_position or {}).get("z", 0))

        if "left" in relation:
            return ox < ux

        if "right" in relation:
            return ox > ux

        if "front" in relation:
            return oz < uz

        if "behind" in relation or "back" in relation:
            return oz > uz

        if "near" in relation or "close" in relation:
            d2 = (ox - ux) ** 2 + (oz - uz) ** 2
            return d2 <= (1.5 ** 2)

        return True

    def _object_matches_type(self, obj: Dict[str, Any], object_type: str) -> bool:

        token = str(object_type or "").strip().lower()
        if not token:
            return False

        if token.endswith("s") and not token.endswith("ss") and len(token) > 2:
            token = token[:-1]

        name = str(obj.get("name", "")).strip().lower()
        category = str(obj.get("category", "")).strip().lower()
        subcategory = str(obj.get("subcategory", "")).strip().lower()
        
        return token in name or token == category or token == subcategory

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
