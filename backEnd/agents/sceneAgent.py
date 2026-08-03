import google.generativeai as genai
import json
import os
import math
import re
import sys
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.gridLayout import grid_layout_positions
from tools.aroundPlacement import around_placement_positions
from tools.facingPlacement import facing_placement
from tools.graphLayout import layout_graph_positions
from tools.distancePlacement import distance_placement

class SceneAgent:
    """
    Scene Agent handles spatial reasoning and determines:
    - Object positions relative to user/other objects
    - Object rotations
    - Spatial relationships and constraints
    
    Now works with enriched Language Agent output that preserves semantic context.
    """
    def __init__(self, use_llm_reasoning=True):
        # Google Gemini Studio initialize
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = genai.GenerativeModel('gemini-2.5-flash-lite')
        self.use_llm_reasoning = use_llm_reasoning

    def calculate_spatial_transformation(self,
                                         parsed_command: Dict,
                                         scene_state: Dict,
                                         user_position: Dict = None,
                                         feedback: Optional[Dict] = None,
                                         new_objects_to_position: Optional[List[Dict]] = None,
                                         layout_graph: Optional[Dict] = None,
                                         room_bounds=None,
                                         anchor_object: Optional[str] = None,
                                        ) -> Dict:
        """
        Calculate the actual position and rotation for objects based on parsed command.
        
        Args:
            parsed_command: Output from LanguageAgent with structure:
                {
                    "original_prompt": str,  # Full user command (semantic richness!)
                    "command_type": str,
                    "involved_objects": list,
                    "spatial_concepts": list,
                    "intent_summary": str,
                    "action_hints": dict
                }
            scene_state: Current scene state from database
            user_position: User's current position {x, y, z, rotation}
            feedback: Optional feedback from Verification Agent (for iterations)
                {
                    "previous_attempt": dict,
                    "collision_with": list,
                    "suggestion": str
                }
        
        Returns:
            Dict with:
            {
                "object_id": str,
                "position": {"x": float, "y": float, "z": float},
                "rotation": {"x": float, "y": float, "z": float},
                "action": "place" | "move" | "rotate" | "arrange",
                "reasoning": str
            }
            
            OR for complex multi-object commands:
            {
                "objects": [
                    {
                        "object_id": str,
                        "position": {...},
                        "rotation": {...},
                        "action": str
                    },
                    ...
                ],
                "reasoning": str
            }
        """
        # Default user position if not provided
        if user_position is None:
            user_position = {
                'x': 0, 'y': 0, 'z': 0,
                'rotation': {'x': 0, 'y': 0, 'z': 0}
            }
        
        print(f"\nScene Agent processing:")
        print(f"   Original Prompt: '{parsed_command.get('original_prompt', 'N/A')}'")
        print(f"   Command Type: {parsed_command.get('command_type', 'N/A')}")
        print(f"   Objects: {parsed_command.get('involved_objects', [])}")
        print(f"   Spatial Concepts: {parsed_command.get('spatial_concepts', [])}")
        
        if feedback:
            print(f"   Iteration with feedback: {feedback.get('suggestion', 'N/A')}")

        result = self._llm_spatial_reasoning(
            parsed_command,
            scene_state,
            user_position,
            feedback,
            new_objects_to_position,
            layout_graph,
            room_bounds,
            anchor_object,
        )
        return self._apply_facing_rotation(result, parsed_command, scene_state)
    
    def _compute_rotation(self,
                      parsed_command: Dict,
                      scene_state: Dict) -> Optional[float]:
        
        # Handle degree-based rotation commands in Python instead of LLM.
        # Returns new Y rotation in radians, or None if not a degree rotation command.
        
        original_prompt = parsed_command.get("original_prompt", "").lower()
        spatial_concepts = " ".join(parsed_command.get("spatial_concepts", [])).lower()
        text = original_prompt + " " + spatial_concepts
        degree_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:degrees?|°)', text)
        if not degree_match:
            return None
        
        degrees = float(degree_match.group(1))
        radians = degrees * math.pi / 180

        # Determine direction
        if "anticlockwise" in text or "anti-clockwise" in text or "counter" in text:
            delta = radians        # anticlockwise = positive
        else:
            delta = -radians       # clockwise = negative (default)

        involved = parsed_command.get("involved_objects", [])
        if not involved:
            return None
        object_name = involved[0].lower()

        # Find current rotation from scene state
        current_y = 0.0
        for obj in scene_state.get("objects", []):
            if object_name in obj.get("name", "").lower():
                current_y = obj.get("rotation", {}).get("y", 0.0)
                print(f"   [ROTATION] Found '{obj['name']}' current y={current_y:.4f}")
                break

        new_y = current_y + delta

        # Normalize to [-π, π]
        while new_y > math.pi:
            new_y -= 2 * math.pi
        while new_y < -math.pi:
            new_y += 2 * math.pi

        print(f"   [ROTATION] {degrees}° {'anti' if delta > 0 else ''}clockwise: "
            f"{current_y:.4f} + {delta:.4f} = {new_y:.4f}")
        return new_y


    def resolve_removal_targets(
        self,
        parsed_command: Dict,
        scene_state: Dict,
        user_position: Dict = None,
        feedback: Optional[Dict] = None, ) -> Optional[Dict]:
        """
        LLM-driven selection of which scene object id(s) to remove.

        Parallel to calculate_spatial_transformation, but output is ids only
        (no position/rotation), so delete stays spatial-reasoning-first without
        forcing the placement JSON schema.

        Args:
            parsed_command: LanguageAgent output (original_prompt, involved_objects,
                spatial_concepts, action_hints, etc.)
            scene_state: Current scene (objects list, etc.)
            user_position: Viewer pose {x,y,z, rotation:{x,y,z}}
            feedback: Optional retry hint from verification (same shape as spatial path)

        Returns:
            {
                "action": "remove",
                "target_object_ids": [str, ...],
                "reasoning": str
            }
            or None if resolution fails completely.
        """
        if user_position is None:
            user_position = {
                "x": 0,
                "y": 0,
                "z": 0,
                "rotation": {"x": 0, "y": 0, "z": 0},
            }

        print(f"\nScene Agent (remove) resolving targets:")
        print(f"   Original Prompt: '{parsed_command.get('original_prompt', 'N/A')}'")
        print(f"   Objects: {parsed_command.get('involved_objects', [])}")
        print(f"   Spatial Concepts: {parsed_command.get('spatial_concepts', [])}")
        if parsed_command.get("remove_intent"):
            print(f"   Remove intent (hints): {parsed_command.get('remove_intent')}")
        if feedback:
            print(f"Feedback: {feedback.get('suggestion', 'N/A')}")

        if not self.use_llm_reasoning:
            return self._fallback_removal_calculation(parsed_command, scene_state)

        return self._llm_removal_reasoning(
            parsed_command, scene_state, user_position, feedback
        )

    def _llm_removal_reasoning(
        self,
        parsed_command: Dict,
        scene_state: Dict,
        user_position: Dict,
        feedback: Optional[Dict] = None, ) -> Optional[Dict]:

        scene_objects = self._build_scene_objects_index(scene_state)
        remove_intent = self._extract_remove_intent(parsed_command)
        target_specs = remove_intent.get("target_specs", [])
        global_scope = remove_intent.get("global_scope", "none")
        original_prompt = parsed_command.get("original_prompt", "")

        if global_scope == "all_objects":
            all_ids = [
                obj["id"] for obj in scene_objects
                if obj.get("movable", False)
            ]

            return {
                "action": "remove",
                "target_object_ids": all_ids,
                "reasoning": "Global scope all_objects: selected all movable objects",
            }

        if not target_specs:
            target_specs = self._build_fallback_target_specs(parsed_command)
            if not target_specs:
                return self._fallback_removal_calculation(parsed_command, scene_state)

        selected_ids: List[str] = []
        selected_set = set()
        reasoning_parts: List[str] = []

        for spec in target_specs:
            # 1) Candidate pool for this object type.
            candidates = self._match_candidates_for_spec(scene_objects, spec)

            if not candidates:
                target_label = spec.get("object_type", "unknown")
                reasoning_parts.append(f"{target_label}: no candidates")
                continue

            # 2) LLM-first proposal by spatial semantics / language cues.
            llm_result = self._llm_candidate_ids_for_spec(
                spec=spec,
                candidates=candidates,
                parsed_command=parsed_command,
                remove_intent=remove_intent,
                user_position=user_position,
                feedback=feedback,
                already_selected=list(selected_set),
            )

            # 3) Deterministic policy enforces validity/quota/dedupe and fills gaps only.
            picked, policy_meta = self._apply_hybrid_selection_for_spec(
                candidates=candidates,
                spec=spec,
                user_position=user_position,
                already_selected=selected_set,
                llm_candidate_ids=llm_result.get("candidate_ids", []),
                llm_confidence=llm_result.get("confidence"),
            )

            for oid in picked:
                if oid not in selected_set:
                    selected_set.add(oid)
                    selected_ids.append(oid)

            target_label = spec.get("object_type", "unknown")
            requested = (
                "all" if spec.get("quantity_mode") == "all"
                else str(max(1, int(spec.get("quantity", 1))))
            )

            llm_conf = llm_result.get("confidence")
            llm_conf_text = (
                f"{float(llm_conf):.2f}" if isinstance(llm_conf, (int, float)) else "n/a"
            )

            llm_reason = (llm_result.get("reasoning") or "").strip()
            reasoning_parts.append(
                f"{target_label}: requested {requested}, selected {len(picked)}, "
                f"llm_conf={llm_conf_text}, policy={policy_meta}"
            )

            if llm_reason:
                reasoning_parts.append(f"{target_label} llm: {llm_reason}")

        if not selected_ids:
            return self._fallback_removal_calculation(parsed_command, scene_state)

        return {
            "action": "remove",
            "target_object_ids": selected_ids,
            "reasoning": " | ".join(reasoning_parts) or f"Resolved remove targets for '{original_prompt}'",
        }

    def _validate_removal_response(self, result: Dict, scene_state: Dict) -> bool:

        if not result or result.get("action") != "remove":
            return False
        
        ids = result.get("target_object_ids")

        if not isinstance(ids, list):
            return False
        
        valid_ids = {obj["id"] for obj in scene_state.get("objects", [])}

        for oid in ids:
            if not isinstance(oid, str) or oid not in valid_ids:
                return False

        return True

    def _fallback_removal_calculation(
            self, parsed_command: Dict, 
            scene_state: Dict ) -> Optional[Dict]:
        """Deterministic fallback with per-spec quotas."""
        scene_objects = self._build_scene_objects_index(scene_state)
        remove_intent = self._extract_remove_intent(parsed_command)
        target_specs = remove_intent.get("target_specs", [])
        global_scope = remove_intent.get("global_scope", "none")

        if global_scope == "all_objects":
            all_ids = [
                obj["id"] for obj in scene_objects
                if obj.get("movable", False)
            ]
            
            return {
                "action": "remove",
                "target_object_ids": all_ids,
                "reasoning": "Fallback: global all_objects",
            }

        if not target_specs:
            target_specs = self._build_fallback_target_specs(parsed_command)

        if not target_specs:
            return None

        user_position = {
            "x": 0, "y": 0, "z": 0,
            "rotation": {"x": 0, "y": 0, "z": 0},
        }

        selected_ids: List[str] = []
        selected_set = set()

        for spec in target_specs:
            candidates = self._match_candidates_for_spec(scene_objects, spec)
            picked = self._deterministic_select_for_spec(
                candidates=candidates,
                spec=spec,
                user_position=user_position,
                already_selected=selected_set,
            )

            for oid in picked:
                if oid not in selected_set:
                    selected_set.add(oid)
                    selected_ids.append(oid)

        if not selected_ids:
            print(f"   Fallback removal: no match for specs {target_specs}")
            return None

        return {
            "action": "remove",
            "target_object_ids": selected_ids,
            "reasoning": "Fallback: per-spec deterministic selection",
        }

    def _build_scene_objects_index(self, scene_state: Dict) -> List[Dict]:

        out: List[Dict] = []

        for obj in scene_state.get("objects", []):
            out.append({
                "id": obj.get("id"),
                "name": obj.get("name", ""),
                "position": obj.get("position", {}),
                "rotation": obj.get("rotation", {}),
                "category": obj.get("category", "unknown"),
                "movable": obj.get("properties", {}).get("movable", False),
                "raw": obj,
            })

        return [o for o in out if o.get("id")]

    def _extract_remove_intent(self, parsed_command: Dict) -> Dict:

        remove_intent = parsed_command.get("remove_intent") or {}
        nested = remove_intent.get("delete_intent")

        if isinstance(nested, dict):
            source = nested
        else:
            action_hints = parsed_command.get("action_hints", {}) or {}
            source = action_hints.get("delete_intent", {}) or {}

        if not isinstance(source, dict):
            source = {}

        global_scope = source.get("global_scope", remove_intent.get("global_scope", "none"))

        if global_scope not in {"none", "all_objects"}:
            global_scope = "none"

        raw_specs = source.get("target_specs", remove_intent.get("target_specs", []))
        target_specs: List[Dict] = []

        if isinstance(raw_specs, list):
            for spec in raw_specs:
                if not isinstance(spec, dict):
                    continue

                obj_type = str(spec.get("object_type", "")).strip()
                
                if not obj_type:
                    continue

                quantity_mode = spec.get("quantity_mode", "exact")

                if quantity_mode not in {"exact", "all"}:
                    quantity_mode = "exact"
                quantity = spec.get("quantity", 1)

                try:
                    quantity = int(quantity)
                except (TypeError, ValueError):
                    quantity = 1

                quantity = max(1, quantity)

                target_specs.append({
                    "object_type": obj_type,
                    "quantity_mode": quantity_mode,
                    "quantity": quantity,
                    "reference_type": spec.get("reference_type", "definite"),
                    "spatial_filter": spec.get("spatial_filter"),
                    "selection_policy": spec.get("selection_policy", "nearest_to_user"),
                })

        return {
            "global_scope": global_scope,
            "target_specs": target_specs,
            "original_prompt": parsed_command.get("original_prompt", ""),
        }

    def _build_fallback_target_specs(self, parsed_command: Dict) -> List[Dict]:

        involved = parsed_command.get("involved_objects", []) or []
        specs: List[Dict] = []
        seen = set()

        for token in involved:
            obj_type = str(token).strip().lower()
            if not obj_type:
                continue

            if obj_type.endswith("s") and not obj_type.endswith("ss") and len(obj_type) > 2:
                obj_type = obj_type[:-1]

            if obj_type in seen:
                continue

            seen.add(obj_type)

            specs.append({
                "object_type": obj_type,
                "quantity_mode": "exact",
                "quantity": 1,
                "reference_type": "definite",
                "spatial_filter": None,
                "selection_policy": "nearest_to_user",
            })

        return specs

    def _match_candidates_for_spec(self, scene_objects: List[Dict], spec: Dict) -> List[Dict]:

        obj_type = str(spec.get("object_type", "")).strip().lower()

        if not obj_type:
            return []

        if obj_type.endswith("s") and not obj_type.endswith("ss") and len(obj_type) > 2:
            obj_type = obj_type[:-1]

        matches = []
        for obj in scene_objects:
            name = str(obj.get("name", "")).lower()
            category = str(obj.get("category", "")).lower()
            obj_id = str(obj.get("id", "")).lower()
            if obj_type in name or obj_type == category or obj_type == obj_id:
                matches.append(obj)

        return matches

    def _distance_sq(self, obj: Dict, user_position: Dict) -> float:
        """
        Calculate squared distance between object and user position.
        """
        pos = obj.get("position", {})
        ux = float(user_position.get("x", 0))
        uy = float(user_position.get("y", 0))
        uz = float(user_position.get("z", 0))
        ox = float(pos.get("x", 0))
        oy = float(pos.get("y", 0))
        oz = float(pos.get("z", 0))
        return (ox - ux) ** 2 + (oy - uy) ** 2 + (oz - uz) ** 2

    def _deterministic_select_for_spec(
        self,
        candidates: List[Dict],
        spec: Dict,
        user_position: Dict,
        already_selected: set,
    ) -> List[str]:
        """
        Deterministic selection for a given spec.
        """
        if not candidates:
            return []

        sorted_candidates = sorted(
            candidates,
            key=lambda o: (self._distance_sq(o, user_position), str(o.get("id"))),
        )
        available = [o for o in sorted_candidates if o["id"] not in already_selected]

        quantity_mode = spec.get("quantity_mode", "exact")
        if quantity_mode == "all":
            return [o["id"] for o in available]

        quantity = max(1, int(spec.get("quantity", 1)))
        return [o["id"] for o in available[:quantity]]

    def _apply_hybrid_selection_for_spec(
        self,
        candidates: List[Dict],
        spec: Dict,
        user_position: Dict,
        already_selected: set,
        llm_candidate_ids: List[str],
        llm_confidence: Optional[float],
    ) -> Tuple[List[str], str]:

        """
        LLM+deterministic hybrid selector:
        - LLM proposes ranked IDs.
        - Deterministic layer enforces id validity, dedupe, per-spec quota.
        - If underfilled, deterministic nearest fill completes the quota.
        """

        if not candidates:
            return [], "no_candidates"

        valid_candidates = {obj["id"] for obj in candidates}
        available_sorted = self._deterministic_select_for_spec(
            candidates=candidates,
            spec={"quantity_mode": "all", "quantity": 999999},
            user_position=user_position,
            already_selected=already_selected,
        )

        llm_ranked = []
        seen = set()

        for oid in llm_candidate_ids or []:
            if (
                isinstance(oid, str)
                and oid in valid_candidates
                and oid not in already_selected
                and oid not in seen
            ):
                seen.add(oid)
                llm_ranked.append(oid)

        quantity_mode = spec.get("quantity_mode", "exact")

        if quantity_mode == "all":
            # For "all", if LLM proposes a non-empty subset (e.g., implicit spatial constraint),
            # honor it. If LLM returns nothing, deterministic policy selects all available.
            if llm_ranked:
                return llm_ranked, "llm_all_subset"
            return available_sorted, "deterministic_all"

        quantity = max(1, int(spec.get("quantity", 1)))
        selected = llm_ranked[:quantity]

        # Fill any shortfall deterministically by nearest remaining candidates.
        if len(selected) < quantity:
            for oid in available_sorted:
                if oid in selected:
                    continue

                selected.append(oid)
                if len(selected) >= quantity:
                    break

        conf = (
            float(llm_confidence)
            if isinstance(llm_confidence, (int, float))
            else None
        )
        
        used_llm = len(llm_ranked[:quantity])

        if used_llm == 0:
            mode = "deterministic_only"
        elif len(selected) > used_llm:
            mode = "llm_plus_fill"
        else:
            mode = "llm_only"

        if conf is not None:
            mode = f"{mode}@{conf:.2f}"
        return selected, mode

    def _llm_candidate_ids_for_spec(
        self,
        spec: Dict,
        candidates: List[Dict],
        parsed_command: Dict,
        remove_intent: Dict,
        user_position: Dict,
        feedback: Optional[Dict] = None,
        already_selected: Optional[List[str]] = None,
    ) -> Dict:
        
        if not candidates:
            return {"candidate_ids": [], "confidence": None, "reasoning": ""}

        feedback_context = ""
        if feedback:
            feedback_context = f"\nFEEDBACK: {json.dumps(feedback, indent=2)}\n"

        quantity_mode = spec.get("quantity_mode", "exact")
        quantity = max(1, int(spec.get("quantity", 1)))
        already_selected = already_selected or []

        prompt = f"""You are selecting candidate object IDs for ONE delete target group.
Return JSON only with this shape:
{{
  "candidate_ids": ["id1", "id2"],
  "confidence": 0.0,
  "reasoning": "one short sentence"
}}

USER REQUEST:
"{parsed_command.get('original_prompt', '')}"

TARGET SPEC:
{json.dumps(spec, indent=2)}

REMOVE INTENT:
{json.dumps(remove_intent, indent=2)}

USER POSITION:
{json.dumps(user_position, indent=2)}

ALREADY SELECTED IDS FOR OTHER TARGET GROUPS:
{json.dumps(already_selected, indent=2)}

AVAILABLE CANDIDATES (ONLY pick ids from this list):
{json.dumps(candidates, indent=2)}
{feedback_context}

Rules:
- Return only ids from AVAILABLE CANDIDATES.
- Rank candidate_ids from most likely to least likely for THIS target spec.
- If quantity_mode is "exact", prefer returning at least {quantity} candidates when possible.
- If quantity_mode is "all", return all ids that match this target spec.
- Avoid IDs in ALREADY SELECTED IDS unless truly necessary.
- confidence is 0..1 confidence in your ranked list.
- Keep reasoning short.
"""
        try:
            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=512,
                    response_mime_type="application/json",
                ),
            )
            payload = json.loads(response.text)
            ids = payload.get("candidate_ids", [])
            if not isinstance(ids, list):
                ids = []
            valid = {o["id"] for o in candidates}
            cleaned = [oid for oid in ids if isinstance(oid, str) and oid in valid]
            confidence = payload.get("confidence")
            if isinstance(confidence, (int, float)):
                confidence = max(0.0, min(1.0, float(confidence)))
            else:
                confidence = None
            return {
                "candidate_ids": cleaned,
                "confidence": confidence,
                "reasoning": str(payload.get("reasoning", "")).strip(),
            }
        except Exception:
            return {"candidate_ids": [], "confidence": None, "reasoning": ""}

    
    def _apply_facing_rotation(self, result: Dict, parsed_command: Dict, scene_state: Dict) -> Dict:
        """
        Post-process any placement result: if a 'facing X' target is detected,
        recompute each object's rotation_y from its final position toward the target.
        This ensures the facing constraint survives collision-avoidance retries where
        the LLM repositions the object but drops the rotation.
        """
        if not result:
            return result
        target = self._detect_facing_target(parsed_command, scene_state)
        if not target:
            return result

        tx = float(target.get('position', {}).get('x', 0))
        tz = float(target.get('position', {}).get('z', 0))

        def _rot(px, pz) -> Dict:
            return {'x': 0, 'y': round(math.atan2(tx - float(px), tz - float(pz)), 4), 'z': 0}

        if 'objects' in result:
            for obj in result['objects']:
                pos = obj.get('position', {})
                obj['rotation'] = _rot(pos.get('x', 0), pos.get('z', 0))
        elif 'position' in result:
            pos = result.get('position', {})
            result['rotation'] = _rot(pos.get('x', 0), pos.get('z', 0))

        return result

    def _detect_facing_target(self, parsed_command: Dict, scene_state: Dict) -> Optional[Dict]:
        """Return the scene object that 'facing X' refers to, or None."""
        prompt = parsed_command.get('original_prompt', '').lower()
        concepts = ' '.join(parsed_command.get('spatial_concepts', [])).lower()
        text = prompt + ' ' + concepts

        m = re.search(r'(?:facing|to\s+face|faces?)\s+(?:the\s+)?(\w+)', text)
        if not m:
            return None

        target_type = m.group(1).lower()
        if target_type.endswith('s') and not target_type.endswith('ss') and len(target_type) > 2:
            target_type = target_type[:-1]

        for obj in scene_state.get('objects', []):
            name = obj.get('name', '').lower()
            cat = obj.get('category', '').lower()
            oid = obj.get('id', '').lower()
            if target_type in name or target_type == cat or target_type == oid:
                return obj

        return None

    def _try_facing_placement(
        self,
        parsed_command: Dict,
        scene_state: Dict,
        room_bounds: Optional[Dict],
        new_objects_to_position: Optional[List[Dict]],
    ) -> Optional[Dict]:
        """Deterministic facing-placement. Returns result dict or None if not applicable."""
        target = self._detect_facing_target(parsed_command, scene_state)
        if not target:
            return None

        target_id = target.get('id')

        if new_objects_to_position:
            movers = new_objects_to_position
        else:
            involved = parsed_command.get('involved_objects', [])
            movers = []
            seen: set = set()
            for token in involved:
                t = token.lower()
                if t.endswith('s') and not t.endswith('ss') and len(t) > 2:
                    t = t[:-1]
                for obj in scene_state.get('objects', []):
                    oid = obj.get('id')
                    if oid in seen or oid == target_id:
                        continue
                    name = obj.get('name', '').lower()
                    cat = obj.get('category', '').lower()
                    obj_id = str(oid).lower()
                    if t in name or t == cat or t == obj_id:
                        movers.append(obj)
                        seen.add(oid)

        if not movers:
            return None

        target_name = target.get('name', target_id)
        is_add = bool(new_objects_to_position)
        tx = float(target.get('position', {}).get('x', 0))
        tz = float(target.get('position', {}).get('z', 0))
        objects_out = []

        for obj in movers:
            if is_add:
                # New object: place in front of target then face it
                pos = facing_placement(
                    target=target,
                    mover_collision=obj.get('collision'),
                    room_bounds=room_bounds,
                )
                objects_out.append({
                    'object_id': obj['id'],
                    'name': obj.get('name', ''),
                    'position': {'x': pos['x'], 'y': pos['y'], 'z': pos['z']},
                    'rotation': {'x': 0, 'y': pos['rotation_y'], 'z': 0},
                    'action': 'place',
                })
            else:
                # Existing object: keep current position, only update rotation
                cur_pos = obj.get('position', {})
                px = float(cur_pos.get('x', 0))
                pz = float(cur_pos.get('z', 0))
                rotation_y = math.atan2(tx - px, tz - pz)
                objects_out.append({
                    'object_id': obj['id'],
                    'name': obj.get('name', ''),
                    'position': cur_pos,
                    'rotation': {'x': 0, 'y': round(rotation_y, 4), 'z': 0},
                    'action': 'rotate',
                })

        print(f"   Using deterministic facing-placement: {len(movers)} objects facing '{target_name}'")
        reasoning = f"{'Placed' if is_add else 'Rotated'} facing {target_name}"
        if len(objects_out) == 1:
            result = objects_out[0]
            result['reasoning'] = reasoning
            return result
        return {'objects': objects_out, 'reasoning': reasoning}

    def _detect_around_anchor(self, parsed_command: Dict, scene_state: Dict) -> Optional[Dict]:
        """Return the scene object that 'around X' refers to, or None."""
        prompt = parsed_command.get('original_prompt', '').lower()
        concepts = ' '.join(parsed_command.get('spatial_concepts', [])).lower()
        text = prompt + ' ' + concepts

        if 'around' not in text:
            return None

        m = re.search(r'around\s+(?:the\s+)?(\w+)', text)
        if not m:
            return None

        anchor_type = m.group(1).lower()
        if anchor_type.endswith('s') and not anchor_type.endswith('ss') and len(anchor_type) > 2:
            anchor_type = anchor_type[:-1]

        for obj in scene_state.get('objects', []):
            name = obj.get('name', '').lower()
            cat = obj.get('category', '').lower()
            oid = obj.get('id', '').lower()
            if anchor_type in name or anchor_type == cat or anchor_type == oid:
                return obj

        return None

    def _try_around_placement(
        self,
        parsed_command: Dict,
        scene_state: Dict,
        room_bounds: Optional[Dict],
        new_objects_to_position: Optional[List[Dict]],
    ) -> Optional[Dict]:
        """
        Deterministic around-placement. Returns result dict or None if not applicable.
        Only runs on the first attempt (no feedback) because positions are always identical.
        """
        anchor = self._detect_around_anchor(parsed_command, scene_state)
        if not anchor:
            return None

        anchor_id = anchor.get('id')

        if new_objects_to_position:
            movers = new_objects_to_position
        else:
            involved = parsed_command.get('involved_objects', [])
            movers = []
            seen: set = set()
            for token in involved:
                t = token.lower()
                if t.endswith('s') and not t.endswith('ss') and len(t) > 2:
                    t = t[:-1]
                for obj in scene_state.get('objects', []):
                    oid = obj.get('id')
                    if oid in seen or oid == anchor_id:
                        continue
                    name = obj.get('name', '').lower()
                    cat = obj.get('category', '').lower()
                    obj_id = str(oid).lower()
                    if t in name or t == cat or t == obj_id:
                        movers.append(obj)
                        seen.add(oid)

        if not movers:
            return None

        positions = around_placement_positions(movers, anchor, room_bounds)
        if not positions:
            return None

        is_add = bool(new_objects_to_position)
        objects_out = []
        for obj, pos in zip(movers, positions):
            objects_out.append({
                'object_id': obj['id'],
                'name': obj.get('name', ''),
                'position': {'x': pos['x'], 'y': pos['y'], 'z': pos['z']},
                'rotation': {'x': 0, 'y': pos['rotation_y'], 'z': 0},
                'action': 'place' if is_add else 'move',
            })

        anchor_name = anchor.get('name', anchor_id)
        print(f"   Using deterministic around-placement: {len(movers)} objects around '{anchor_name}'")
        return {
            'objects': objects_out,
            'reasoning': f"Arranged {len(movers)} objects evenly around {anchor_name}",
        }

    def _detect_distance_constraint(self, parsed_command: Dict, scene_state: Dict) -> Optional[Dict]:
        """
        Parse 'N meters back/front/left/right of X' style commands, e.g.
        "move the sink 1.5 meters back of the table". Returns
        {'distance': float, 'direction': str, 'anchor': dict} or None.
        """
        prompt = parsed_command.get('original_prompt', '').lower()
        concepts = ' '.join(parsed_command.get('spatial_concepts', [])).lower()
        text = prompt + ' ' + concepts

        m = re.search(
            r'(\d+(?:\.\d+)?)\s*(?:meters?|metres?|m)\b\s*(?:to\s+the\s+)?'
            r'(in front of|front of|back of|behind|left of|right of)\s+(?:the\s+)?(\w+)',
            text,
        )
        if not m:
            return None

        distance = float(m.group(1))
        direction_map = {
            'in front of': 'front',
            'front of': 'front',
            'back of': 'back',
            'behind': 'back',
            'left of': 'left',
            'right of': 'right',
        }
        direction = direction_map.get(m.group(2))
        if not direction:
            return None

        anchor_type = m.group(3).lower()
        if anchor_type.endswith('s') and not anchor_type.endswith('ss') and len(anchor_type) > 2:
            anchor_type = anchor_type[:-1]

        anchor = None
        for obj in scene_state.get('objects', []):
            name = obj.get('name', '').lower()
            cat = obj.get('category', '').lower()
            oid = obj.get('id', '').lower()
            if anchor_type in name or anchor_type == cat or anchor_type == oid:
                anchor = obj
                break
        if not anchor:
            return None

        return {'distance': distance, 'direction': direction, 'anchor': anchor}

    def _try_distance_placement(
        self,
        parsed_command: Dict,
        scene_state: Dict,
        room_bounds: Optional[Dict],
        new_objects_to_position: Optional[List[Dict]],
    ) -> Optional[Dict]:
        """
        Deterministic edge-to-edge distance placement. Tracks the mover's and
        anchor's bounding boxes (via tools.distancePlacement, which builds AABBs
        from collision width/height/depth + offset) and solves for the position
        where abs(bound_mover - bound_anchor) equals the requested distance.
        Returns None if no distance constraint is detected in the command.
        """
        constraint = self._detect_distance_constraint(parsed_command, scene_state)
        if not constraint:
            return None

        anchor = constraint['anchor']
        anchor_id = anchor.get('id')

        if new_objects_to_position:
            movers = new_objects_to_position
        else:
            involved = parsed_command.get('involved_objects', [])
            movers = []
            seen: set = set()
            for token in involved:
                t = token.lower()
                if t.endswith('s') and not t.endswith('ss') and len(t) > 2:
                    t = t[:-1]
                for obj in scene_state.get('objects', []):
                    oid = obj.get('id')
                    if oid in seen or oid == anchor_id:
                        continue
                    name = obj.get('name', '').lower()
                    cat = obj.get('category', '').lower()
                    obj_id = str(oid).lower()
                    if t in name or t == cat or t == obj_id:
                        movers.append(obj)
                        seen.add(oid)

        if not movers:
            return None

        is_add = bool(new_objects_to_position)
        objects_out = []
        for obj in movers:
            try:
                pos = distance_placement(
                    mover=obj,
                    anchor=anchor,
                    distance=constraint['distance'],
                    direction=constraint['direction'],
                    room_bounds=room_bounds,
                )
            except ValueError as e:
                print(f"   [DISTANCE] skipped {obj.get('id')}: {e}")
                continue

            rotation = {'x': 0, 'y': 0, 'z': 0} if is_add else obj.get('rotation', {'x': 0, 'y': 0, 'z': 0})
            objects_out.append({
                'object_id': obj['id'],
                'name': obj.get('name', ''),
                'position': pos,
                'rotation': rotation,
                'action': 'place' if is_add else 'move',
            })

        if not objects_out:
            return None

        anchor_name = anchor.get('name', anchor_id)
        print(f"   Using deterministic distance-placement: {len(objects_out)} object(s) "
              f"{constraint['distance']}m {constraint['direction']} of '{anchor_name}'")
        reasoning = f"Placed {constraint['distance']}m {constraint['direction']} of {anchor_name} (edge-to-edge)"

        if len(objects_out) == 1:
            result = objects_out[0]
            result['reasoning'] = reasoning
            return result
        return {'objects': objects_out, 'reasoning': reasoning}

    def _grid_layout_positions(self, objects: List[Dict], room_bounds: Dict, gap: float = 0.15) -> List[Dict]:
        return grid_layout_positions(objects, room_bounds, gap)

    def _build_gaze_target_context(self, resolved_spatial_target: Optional[Dict]) -> str:
        """
        Turn the LanguageAgent's gaze-resolved deictic target into an
        authoritative placement instruction injected into the spatial prompt.

        The LanguageAgent only populates resolved_spatial_target when the command
        used a vague POSITION reference ("there"/"on that") with no explicit
        anchor and a valid gaze existed. So when present here, it should win over
        the default "in front of the user" heuristic.

        Returns "" (no-op) when there is no usable gaze target, so non-deictic
        commands behave exactly as before.
        """
        if not isinstance(resolved_spatial_target, dict):
            return ""

        target_type = resolved_spatial_target.get('type')
        point = resolved_spatial_target.get('point') or {}

        if target_type in ('floor', 'wall'):
            gx = point.get('x')
            gz = point.get('z')
            if gx is None or gz is None:
                return ""
            return f"""

            RESOLVED GAZE TARGET (AUTHORITATIVE — this is exactly what 'there'/'here' means):
            - The user pointed with their head-gaze at this floor location.
            - Place the object centered at x = {gx}, z = {gz} (keep y on the floor, y = -3.0).
            - This OVERRIDES default placement heuristics. Do NOT place it "in front of the user".
            - You MAY apply only small offsets to respect room bounds or avoid collisions.
            """

        if target_type == 'object' and resolved_spatial_target.get('object_id'):
            object_id = resolved_spatial_target.get('object_id')
            near_txt = ""
            if point.get('x') is not None and point.get('z') is not None:
                near_txt = f" (gaze point near x = {point.get('x')}, z = {point.get('z')})"
            return f"""

            RESOLVED GAZE TARGET (AUTHORITATIVE — this is exactly what 'there'/'on that' means):
            - The user pointed with their head-gaze at an existing object: {object_id}{near_txt}.
            - Place the target object in direct relation to {object_id} (e.g. on top of it or
              immediately beside it), not at a generic default location.
            - This OVERRIDES default placement heuristics. Respect room bounds and avoid collisions.
            """

        return ""

    def _llm_spatial_reasoning(self,
                               parsed_command: Dict,
                               scene_state: Dict,
                               user_position: Dict,
                               feedback: Optional[Dict] = None,
                               new_objects_to_position: Optional[List[Dict]] = None,
                               layout_graph: Optional[Dict] = None,
                               room_bounds=None,
                               anchor_object: Optional[str] = None,
                               ) -> Dict:

        # Deterministic spatial shortcuts (first attempt only — positions are always identical).
        if not feedback:
            distance_result = self._try_distance_placement(
                parsed_command, scene_state, room_bounds, new_objects_to_position
            )
            if distance_result:
                return distance_result

            facing_result = self._try_facing_placement(
                parsed_command, scene_state, room_bounds, new_objects_to_position
            )
            if facing_result:
                return facing_result

            around_result = self._try_around_placement(
                parsed_command, scene_state, room_bounds, new_objects_to_position
            )
            if around_result:
                return around_result

            # Graph-layout resolver: converts ImageAgent layout_graph edges directly
            # into collision-safe coordinates — no LLM needed for the complex route.
            if layout_graph and new_objects_to_position and room_bounds:
                graph_positions = layout_graph_positions(
                    nodes_to_place=new_objects_to_position,
                    edges=layout_graph.get('edges', []),
                    anchor_object=anchor_object,
                    room_bounds=room_bounds,
                )
                if graph_positions and len(graph_positions) == len(new_objects_to_position):
                    print(f"   Using deterministic graph layout for {len(new_objects_to_position)} objects")
                    objects_out = []
                    for obj in new_objects_to_position:
                        pos = graph_positions[obj['id']]
                        objects_out.append({
                            'object_id': obj['id'],
                            'name': obj.get('name', ''),
                            'position': {'x': pos['x'], 'y': pos['y'], 'z': pos['z']},
                            'rotation': {'x': 0, 'y': round(pos.get('rotation_y', 0.0), 4), 'z': 0},
                            'action': 'place',
                        })
                    return {
                        'objects': objects_out,
                        'reasoning': 'Deterministic graph-layout resolved from ImageAgent layout_graph',
                    }

        # If feedback exists and every collision pair is between proposed objects,
        # the LLM has already failed to solve the layout numerically — use deterministic grid.
        if feedback and new_objects_to_position and room_bounds:
            pairs = feedback.get('colliding_pairs', [])
            new_ids = {o['id'] for o in new_objects_to_position}
            if pairs and all(p.get('mover') in new_ids and p.get('anchor') in new_ids for p in pairs):
                grid_positions = self._grid_layout_positions(new_objects_to_position, room_bounds)
                if grid_positions:
                    print(f"   Using deterministic grid layout for {len(new_objects_to_position)} objects (LLM failed proposed-vs-proposed constraints)")
                    objects_out = []
                    for obj, pos in zip(new_objects_to_position, grid_positions):
                        objects_out.append({
                            'object_id': obj['id'],
                            'name': obj['name'],
                            'position': pos,
                            'rotation': {'x': 0, 'y': 0, 'z': 0},
                            'action': 'add',
                        })
                    return {'objects': objects_out}

        # LLM context
        scene_objects = [
            {
                'id': obj['id'],
                'name': obj['name'],
                'position': obj['position'],
                'rotation': obj['rotation'],
                'category': obj.get('category', 'unknown')
            }
            for obj in scene_state.get('objects', [])
        ]
        new_objects_section = ""
        if new_objects_to_position:
            new_objects_info = []
            for obj in new_objects_to_position:
                entry = {
                    'id': obj['id'],
                    'name': obj['name'],
                    'category': obj.get('category', 'unknown'),
                    'properties': obj.get('properties', {}),
                }
                col = obj.get('collision')
                if col and isinstance(col, dict):
                    w = col.get('width', 0)
                    d = col.get('depth', 0)
                    entry['collision_footprint'] = {
                        'width_x': round(w, 3),
                        'depth_z': round(d, 3),
                        'min_center_spacing_x': round(w + 0.1, 3),
                        'min_center_spacing_z': round(d + 0.1, 3),
                    }
                new_objects_info.append(entry)

            new_objects_section = f"""

            NEWLY CREATED OBJECTS (need position/rotation):
            {json.dumps(new_objects_info, indent=2)}

            IMPORTANT: These objects have been created but have no position/rotation yet.
            You MUST provide position and rotation for ALL of these objects.
            If collision_footprint is provided, use min_center_spacing_x/z to ensure
            adjacent objects of the same type do not overlap each other.
            """

        # Extract key information from enriched Language Agent output
        original_prompt = parsed_command.get('original_prompt', '')
        command_type = parsed_command.get('command_type', 'POS/ROTATE')
        involved_objects = parsed_command.get('involved_objects', [])
        spatial_concepts = parsed_command.get('spatial_concepts', [])
        intent_summary = parsed_command.get('intent_summary', original_prompt)
        action_hints = parsed_command.get('action_hints', {})
        resolved_spatial_target = parsed_command.get('resolved_spatial_target')

        # Phase D: consume the gaze-resolved deictic target ("there" / "on that").
        # When present, it is authoritative and overrides default placement heuristics.
        gaze_target_context = self._build_gaze_target_context(resolved_spatial_target)
        
        # Build feedback context if this is an iteration
        feedback_context = ""
        if feedback:
            collision_pairs = feedback.get('colliding_pairs', [])

            # Use the pre-computed safe positions from aabbCheck (edge-based, exact).
            # Deduplicate per anchor — all movers of the same size hitting the same anchor
            # produce the same safe x/z boundaries, so one entry per anchor suffices.
            seen_anchors: dict = {}  # anchor -> suggestion string
            per_mover_lines = ""
            for pair in collision_pairs:
                mover = pair.get('mover', '?')
                anchor = pair.get('anchor', '?')
                suggestion = pair.get('suggestion', '')
                per_mover_lines += f"\n    - {suggestion}"
                if anchor not in seen_anchors:
                    seen_anchors[anchor] = suggestion

            feedback_context = f"""

            ⚠️ COLLISION DETECTED — REQUIRED REPOSITIONING:
            {per_mover_lines}

            CRITICAL RULES:
            - The safe x/z values above are computed from the actual bounding box edges — they are exact.
            - Every object MUST be placed at one of the listed safe coordinates.
            - Do NOT reason about whether a position "looks far enough" — trust the safe values.
            - Do NOT reuse any position from the previous attempt.
            - If placing multiple objects of the same type, ensure they also don't collide with each other.
            """


        # Build bounds context — always inject when available (all routes)
        bounds_context = ""
        if room_bounds:
            min_x = room_bounds["min"]["x"]
            max_x = room_bounds["max"]["x"]
            min_z = room_bounds["min"]["z"]
            max_z = room_bounds["max"]["z"]
            min_y = room_bounds["min"]["y"]
            bounds_context = f"""

            ROOM BOUNDS (hard limits — ALL objects MUST be placed strictly within these):
            - Left wall:  x = {min_x}  → place objects at x ≥ {min_x + 0.1:.2f}
            - Right wall: x = {max_x}  → place objects at x ≤ {max_x - 0.1:.2f}
            - Back wall:  z = {min_z}  → place objects at z ≥ {min_z + 0.1:.2f}
            - Front wall: z = {max_z}  → place objects at z ≤ {max_z - 0.1:.2f}
            - Floor Y: {min_y}

            INWARD OFFSET RULE:
            - "attach to wall" / "against wall" / "next to wall" → offset 0.1–0.2m inward from wall
            - "close to wall" / "near wall"                      → offset 0.2–0.3m inward from wall
            - General placement (not wall-specific)              → offset 0.3–0.5m inward from wall

            Examples given these bounds:
            object attached to left wall        → x = {min_x + 0.15:.2f}
            object attached to back wall        → z = {min_z + 0.15:.2f}
            object close to back wall           → z = {min_z + 0.25:.2f}
            object at front-left corner         → x = {min_x + 0.15:.2f}, z = {max_z - 0.15:.2f}
            object at front-right corner        → x = {max_x - 0.15:.2f}, z = {max_z - 0.15:.2f}
            object at back-left corner          → x = {min_x + 0.15:.2f}, z = {min_z + 0.15:.2f}
            object at back-right corner         → x = {max_x - 0.15:.2f}, z = {min_z + 0.15:.2f}
            """

        # Build layout graph context — complex route only
        anchor = anchor_object or "the dominant furniture piece"
        layout_graph_context = ""
        if layout_graph:
            layout_graph_context = f"""

            SPATIAL LAYOUT GRAPH (from Memory Agent — use this for placement):
            This graph encodes the intended spatial relationships between furniture pieces.
            Each edge defines how two objects should be positioned relative to each other or to room boundaries.
            Follow these relationships as closely as possible when assigning positions.

            {json.dumps(layout_graph, indent=2)}

            GRAPH INTERPRETATION RULES:
            - "next_to" with a side (e.g., "west") → place object on that side of the target, 0.1–0.2m offset inward from wall
            - "facing" → object should be oriented toward the target (update Y rotation accordingly)
            - "close_to" with a side → place object near that side, 0.2–0.3m offset
            - "at_corner" with a corner key → place at the named room corner, offset inward 0.1–0.2m on both axes
            - IMPORTANT: These graph edges OVERRIDE generic placement heuristics.
            Do not apply default assumptions (e.g. "chair goes in front of desk") when a graph edge says otherwise.
            - Wall side mapping: back_wall=most negative Z, front_wall=most positive Z, left_wall=most negative X, right_wall=most positive X

            ANCHOR RULE: Place '{anchor}' against its designated wall first, then derive all other positions from graph edges relative to it.
            """

        new_objects_context = ""
        if new_objects_to_position and len(new_objects_to_position) > 0:
            new_objects_context = f"""

            POSITIONING NEW OBJECTS:
            You are positioning {len(new_objects_to_position)} NEW object(s) to add to the scene:
            """

            for obj in new_objects_to_position:
                new_objects_context += f"\n    - {obj['id']} ({obj['name']}, {obj.get('category', 'unknown')})"

            if len(new_objects_to_position) > 1:
                new_objects_context += """

            MULTI-OBJECT POSITIONING REQUIREMENTS:
            - Position ALL objects with logical spatial relationships
            - Group similar objects together (e.g., chairs around a table)
            - Maintain proper spacing between objects (minimum 0.1m)
            - Consider functional arrangements (e.g., lamps for lighting, chairs for seating)
            - Ensure aesthetic balance and avoid overcrowding
            - All objects should be on the floor (y = -3.0)

            YOU MUST return multi-object format with ALL objects:
            {{
            "objects": [
                {{"object_id": "chair_03", "position": {{"x": ..., "y": -3.0, "z": ...}}, "rotation": {{...}}, "action": "place"}},
                {{"object_id": "table_02", "position": {{"x": ..., "y": -3.0, "z": ...}}, "rotation": {{...}}, "action": "place"}}
            ],
            "reasoning": "Positioned chairs around table..."
            }}
            """
            else:
                new_objects_context += """

                SINGLE NEW OBJECT:
                Return single-object format:
                {{
                "object_id": "...",
                "position": {{"x": ..., "y": -3.0, "z": ...}},
                "rotation": {{"x": 0, "y": 0, "z": 0}},
                "action": "place",
                "reasoning": "Placed object at ..."
                }}
                """
        
        new_objects_context = ""
        if new_objects_to_position and len(new_objects_to_position) > 0:
            new_objects_context = f"""

            POSITIONING NEW OBJECTS:
            You are positioning {len(new_objects_to_position)} NEW object(s) to add to the scene:
            """
            
            for obj in new_objects_to_position:
                new_objects_context += f"\n    - {obj['id']} ({obj['name']}, {obj.get('category', 'unknown')})"
            
            if len(new_objects_to_position) > 1:
                new_objects_context += """

            MULTI-OBJECT POSITIONING REQUIREMENTS:
            - Position ALL objects with logical spatial relationships
            - Group similar objects together (e.g., chairs around a table)
            - Maintain proper spacing between objects (minimum 0.3m)
            - Consider functional arrangements (e.g., lamps for lighting, chairs for seating)
            - Ensure aesthetic balance and avoid overcrowding
            - All objects should be on the floor (y = -3.0)
            
            YOU MUST return multi-object format with ALL objects:
            {{
            "objects": [
                {{"object_id": "chair_03", "position": {{"x": ..., "y": -3.0, "z": ...}}, "rotation": {{...}}, "action": "place"}},
                {{"object_id": "chair_04", "position": {{"x": ..., "y": -3.0, "z": ...}}, "rotation": {{...}}, "action": "place"}},
                {{"object_id": "table_02", "position": {{"x": ..., "y": -3.0, "z": ...}}, "rotation": {{...}}, "action": "place"}}
            ],
            "reasoning": "Positioned 2 chairs around table for seating arrangement..."
            }}
            """
            else:
                new_objects_context += """

            SINGLE NEW OBJECT:
            Return single-object format:
            {{
            "object_id": "...",
            "position": {{"x": ..., "y": -3.0, "z": ...}},
            "rotation": {{"x": 0, "y": 0, "z": 0}},
            "action": "place",
            "reasoning": "Placed object at ..."
            }}
            """


        # Prompt engineering
        prompt = f"""You are an expert spatial reasoning AI for a 3D virtual environment.

            USER'S ORIGINAL REQUEST:
            "{original_prompt}"

            INTENT ANALYSIS:
            - Command Type: {command_type}
            - Primary Action: {action_hints.get('primary_action', 'unknown')}
            - High-level Goal: {intent_summary}
            - Objects Involved: {', '.join(involved_objects) if involved_objects else 'None'}
            - Spatial Concepts: {', '.join(spatial_concepts) if spatial_concepts else 'None'}

            USER POSITION & ORIENTATION:
            Position: ({user_position['x']:.2f}, {user_position['y']:.2f}, {user_position['z']:.2f})
            Facing Direction (Y-rotation): {user_position.get('rotation', {}).get('y', 0):.2f} radians

            CURRENT SCENE OBJECTS:
            {json.dumps(scene_objects, indent=2)}
            {new_objects_section}

            COORDINATE SYSTEM:
            - X-axis: Left (-) to Right (+)
            - Y-axis: Down (-) to Up (+), floor is at y=-3
            - Z-axis: Forward (-) to Backward (+)
            - Rotations in radians
            - User typically faces -Z direction (forward)

            {bounds_context}
            {gaze_target_context}
            {layout_graph_context}

            SPATIAL REASONING RULES:
            1. "next to" = 1.5 meters offset horizontally
            2. "in front of" = offset in -Z direction relative to reference
            3. "behind" = offset in +Z direction
            4. "on" = place on top (y-offset by ~0.3m above surface)
            5. "between X and Y" = midpoint between two objects
            6. "forward/backward/left/right" relative to USER's facing direction
            7. For rotation: convert degrees to radians (90° = 1.5708 radians)
            8. For multiple objects of the same type: arrange them with spacing (0.5-0.8m apart)
            9. For aesthetic goals like "cozy" or "spacious", consider spacing and orientation
            10. ALL objects must be placed on the floor (y = -3.0)
            11. Ensure that all the objects manipulated are on the floor 
            
            {new_objects_context}
            {feedback_context}

            ROTATION RULES:
            - ONLY update rotation if the command explicitly mentions rotation/orientation:
            ✅ "rotate chair 90 degrees", "turn table around", "face the window"
            → update: "rotation": {{"x": 0, "y": 1.57, "z": 0}}
            
            - For POSITION-ONLY commands, preserve existing rotation:
            ✅ "move chair left", "place lamp closer", "shift table forward"
            
            - When ADDING new objects with spatial context, you MAY include rotation for logical orientation:
            ✅ "add chair next to table" → update rotation to face table

            - When command is unclear about rotation, preserves existing rotation

            TASK:
            Calculate EXACT position and rotation for the target object(s).

            For MULTIPLE NEW OBJECTS (e.g., "add 3 chairs"):
            - Arrange them in a sensible pattern (line, arc, cluster)
            - Space them appropriately (0.5-0.8m apart)
            - Consider user's viewing position
            - Return array format with all objects

            OUTPUT REQUIREMENTS:
            - Return valid JSON only, no additional text
            - For SINGLE object: Return single object transformation
            - For MULTIPLE objects: Return array of transformations with "objects" key
            - Include reasoning for spatial calculations
            - Ensure coordinates are realistic
            - Object IDs must match exactly

            For SINGLE OBJECT:
            {{
                "object_id": "chair_01",
                "position": {{"x": 0.5, "y": -3.0, "z": -1.5}},
                "rotation": {{"x": 0, "y": 0, "z": 0}},
                "action": "move",
                "reasoning": "Moved chair closer"
            }}

            For SINGLE OBJECT (with rotation):
            {{
                "object_id": "chair_01",
                "position": {{"x": 0.5, "y": -3.0, "z": -1.5}},
                "rotation": {{"x": 0, "y": 1.57, "z": 0}},
                "action": "place",
                "reasoning": "Placed and rotated chair to face table"
            }}

            For MULTIPLE OBJECTS:
            {{
                "objects": [
                    {{
                        "object_id": "chair_01",
                        "position": {{"x": -0.4, "y": -3.0, "z": -2.0}},
                        "rotation": {{"x": 0, "y": 0, "z": 0}},
                        "action": "place"
                    }},
                    {{
                        "object_id": "chair_02",
                        "position": {{"x": 0.4, "y": -3.0, "z": -2.0}},
                        "rotation": {{"x": 0, "y": 0, "z": 0}},
                        "action": "place"
                    }}
                ],
                "reasoning": "Arranged in a row facing user"
            }}
            """
        
        # Check if this is a degree-based rotation — handle in Python, not LLM
        if parsed_command.get("action_hints", {}).get("primary_action") == "rotate":
            new_y = self._compute_rotation(parsed_command, scene_state)
            if new_y is not None:
                # Find the object ID
                involved = parsed_command.get("involved_objects", [])
                object_name = involved[0].lower() if involved else ""
                target_obj = next(
                    (obj for obj in scene_state.get("objects", [])
                    if object_name in obj.get("name", "").lower()),
                    None
                )
                if target_obj:
                    print(f"   [ROTATION] Python computed y={new_y:.4f}, bypassing LLM")
                    return {
                        "object_id": target_obj["id"],
                        "position": target_obj["position"],
                        "rotation": {"x": 0, "y": new_y, "z": 0},
                        "action": "rotate",
                        "reasoning": f"Python-computed rotation: {new_y:.4f} radians"
                    }
        # Call LLM
        try:
            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.3,
                    max_output_tokens=4096,
                    response_mime_type="application/json"
                )
            )
            
            result = json.loads(response.text)
            
            # Validate
            if not self._validate_transformation(result):
                print("LLM output validation failed, using fallback")
                return self._fallback_calculation(parsed_command, scene_state, user_position, new_objects_to_position)
            
            print(f"   Spatial reasoning complete")
            if 'objects' in result:
                print(f"      Positioned {len(result['objects'])} objects")
            if 'reasoning' in result:
                print(f"      Reasoning: {result['reasoning']}")
            
            return result
        
        except Exception as e:
            print(f"LLM spatial reasoning error: {e}")
            return self._fallback_calculation(parsed_command, scene_state, user_position, new_objects_to_position)
        
        except json.JSONDecodeError as e:
            print(f"LLM JSON parsing error: {e}")
            print(f"Raw response: {response.text if 'response' in locals() else 'No response'}")
            return self._fallback_calculation(parsed_command, scene_state, user_position)
        
        except Exception as e:
            print(f"LLM spatial reasoning error: {e}")
            return self._fallback_calculation(parsed_command, scene_state, user_position)

    def _validate_transformation(self, result: Dict) -> bool:
        """
        Validate LLM output has correct structure.
        """
        # Check if it's a multi-object response
        if 'objects' in result:
            if not isinstance(result['objects'], list) or len(result['objects']) == 0:
                return False
            
            for obj in result['objects']:
                if not self._validate_single_object(obj):
                    return False
            
            return True
        else:
            return self._validate_single_object(result)
    
    def _validate_single_object(self, obj: Dict) -> bool:
        """Validate a single object transformation"""
        required_keys = ['object_id', 'position', 'rotation', 'action']
        
        if not all(key in obj for key in required_keys):
            return False
        
        # Validate position format
        pos = obj.get('position', {})
        if not all(k in pos for k in ['x', 'y', 'z']):
            return False
        
        # Validate rotation format
        rot = obj.get('rotation', {})
        if not all(k in rot for k in ['x', 'y', 'z']):
            return False
        
        # Check if values are numbers
        try:
            float(pos['x'])
            float(pos['y'])
            float(pos['z'])
            float(rot['x'])
            float(rot['y'])
            float(rot['z'])
        except (ValueError, TypeError):
            return False
        
        return True
    
    def _fallback_calculation(self,
                         parsed_command: Dict,
                         scene_state: Dict,
                         user_position: Dict,
                         new_objects: Optional[List[Dict]] = None) -> Dict:
        """
        Simple fallback when LLM fails.
        Handles both existing and new objects.
        """
        print("Using fallback calculation")
        
        # If we have new objects, place them in a simple row
        if new_objects and len(new_objects) > 0:
            print(f"   Placing {len(new_objects)} new objects in default positions")
            
            objects_result = []
            spacing = 0.6  # meters between objects
            start_x = -(len(new_objects) - 1) * spacing / 2  # Center the row
            
            for i, obj in enumerate(new_objects):
                x_pos = start_x + (i * spacing)
                objects_result.append({
                    'object_id': obj['id'],
                    'position': {
                        'x': x_pos,
                        'y': -3.0,  # Floor level
                        'z': -2.0   # 2 meters in front of user
                    },
                    'rotation': {'x': 0, 'y': 0, 'z': 0},
                    'action': 'place'
                })
            
            return {
                'objects': objects_result,
                'reasoning': 'Fallback: Simple row arrangement'
            }
        
        # Original fallback for existing objects
        involved_objects = parsed_command.get('involved_objects', [])
        target_name = involved_objects[0] if involved_objects else 'unknown'
        
        target_obj = None
        for obj in scene_state.get('objects', []):
            if target_name.lower() in obj['name'].lower():
                target_obj = obj
                break
        
        if not target_obj:
            print(f"Target object '{target_name}' not found")
            return None
        
        current_pos = target_obj['position']
        action = parsed_command.get('action_hints', {}).get('primary_action', 'move')
        
        return {
            'object_id': target_obj['id'],
            'position': {
                'x': current_pos['x'],
                'y': current_pos['y'],
                'z': current_pos['z'] - 0.3
            },
            'rotation': target_obj['rotation'].copy(),
            'action': action,
            'reasoning': 'Fallback: Simple forward movement'
        }


# Test
if __name__ == "__main__":
    agent = SceneAgent(use_llm_reasoning=True)
    
    # Mock scene state
    mock_scene = {
        'objects': [
            {
                'id': 'chair_01',
                'name': 'chair',
                'position': {'x': 0.4, 'y': -1.0, 'z': -1.5},
                'rotation': {'x': 0, 'y': 0, 'z': 0},
                'category': 'furniture'
            },
            {
                'id': 'table_01',
                'name': 'table',
                'position': {'x': 0, 'y': -1, 'z': -1.5},
                'rotation': {'x': 0, 'y': 0, 'z': 0},
                'category': 'furniture'
            },
            {
                'id': 'lamp_01',
                'name': 'lamp',
                'position': {'x': -0.5, 'y': -0.7, 'z': -1.5},
                'rotation': {'x': 0, 'y': 0, 'z': 0},
                'category': 'lighting'
            }
        ]
    }
    
    # Mock user position
    user_pos = {
        'x': 0, 'y': 0, 'z': 0,
        'rotation': {'x': 0, 'y': 0, 'z': 0}
    }
    
    # Test commands with NEW Language Agent format
    test_commands = [
        # Simple command
        {
            'original_prompt': 'move the chair left',
            'command_type': 'POS/ROTATE',
            'involved_objects': ['chair'],
            'spatial_concepts': ['move left relative to user'],
            'intent_summary': 'Simple leftward movement of chair',
            'action_hints': {
                'primary_action': 'move',
                'requires_asset_selection': False,
                'requires_spatial_reasoning': True
            }
        },
        
        # Medium command
        {
            'original_prompt': 'rotate the table 90 degrees',
            'command_type': 'POS/ROTATE',
            'involved_objects': ['table'],
            'spatial_concepts': ['rotate 90 degrees clockwise'],
            'intent_summary': 'Rotate table by specific angle',
            'action_hints': {
                'primary_action': 'rotate',
                'requires_asset_selection': False,
                'requires_spatial_reasoning': False
            }
        },
        
        # Complex command
        {
            'original_prompt': 'create a cozy reading corner with lamp next to chair',
            'command_type': 'Vague/Complex',
            'involved_objects': ['lamp', 'chair'],
            'spatial_concepts': [
                'cozy reading corner composition',
                'lamp positioned next to chair',
                'aesthetic goal: cozy atmosphere'
            ],
            'intent_summary': 'Create a functional and aesthetic reading space with proper lighting',
            'action_hints': {
                'primary_action': 'arrange',
                'requires_asset_selection': True,
                'requires_spatial_reasoning': True
            }
        }
    ]
    
    for i, cmd in enumerate(test_commands, 1):
        print(f"\n{'='*60}")
        print(f"TEST {i}: {cmd['original_prompt']}")
        print('='*60)
        
        result = agent.calculate_spatial_transformation(cmd, mock_scene, user_pos)
        
        if result:
            print(f"\nFinal Result:")
            print(json.dumps(result, indent=2))
        else:
            print("\nFailed to calculate transformation")