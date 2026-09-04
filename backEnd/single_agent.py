"""
Single-Agent build of the spatial-reasoning system.

ONE LLM call does all reasoning (intent + asset choice + spatial math + final action
plan). The other agents' *deterministic* plumbing is reused (asset materialization,
AABB collision check, DB/WebSocket execution) but NONE of their prompts / LLM logic.

This module exposes `SingleAgentOrchestrator`, which mirrors the public contract of
`orchestrator.Orchestrator` so `main.py`, the HTTP endpoint, the WebSocket layer, and the
frontend do not change:

    - process_command(user_prompt, session_id="default") -> bool
    - attributes: .user_position, .gaze, .last_result_state

See GAMEPLAN.md for the full spec.
"""

import os
import sys
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import google.generativeai as genai
from dotenv import load_dotenv

# Ensure `backEnd/` is importable when launched from the backEnd cwd (as main.py does).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools.aabbCheck import check_proposed_objects


# ============================================================================
# PROMPT KNOWLEDGE BLOCKS
# ----------------------------------------------------------------------------
# These port the *coverage* of the multi-agent prompts (LanguageAgent
# classification/disambiguation, SceneAgent spatial cues, delete-intent
# semantics) plus few-shot examples into the single agent, so it is briefed at
# parity with the MAS — the difference we test is one-pass integration, not
# prompt quality. Kept as plain strings (single braces) and injected as values
# so no f-string brace-escaping is needed.
# ============================================================================

_DISAMBIGUATION_GUIDE = """OBJECT & INTENT DISAMBIGUATION (decide carefully before acting):
  - Room-type vs asset: "make this an office" / "turn this into a bedroom" is a COMPLEX
    arrangement (add several fitting furniture pieces), NOT a single "office" object.
    But "add an office desk" is a single asset.
  - Type-variant adjectives are PART of the asset name ("ergonomic chair", "coffee table",
    "dining table" -> keep them). Appearance adjectives are NOT ("red chair" -> chair,
    "small lamp" -> lamp).
  - Reference objects are NOT targets: "add a chair next to the table" acts on the CHAIR;
    the table is only an anchor. "move the couch away from the wall" acts on the couch only.
  - Resolve pronouns ("it", "that", "them") using the RECENT CONVERSATION and the scene;
    default to the most recently touched object when otherwise ambiguous."""

_DELETE_GUIDE = """REMOVAL SEMANTICS:
  - "remove/delete the <X>" or "this/that <X>" -> remove ONE X (nearest to the user if
    several match).
  - "remove 2 chairs" -> remove exactly that many (nearest first).
  - "remove all chairs" -> remove every chair. "delete everything" / "all objects" ->
    remove all MOVABLE objects.
  - NEVER remove structural / non-movable items (walls, floor, or anything movable:false)."""

_FEW_SHOT_EXAMPLES = """EXAMPLES (format illustrations only — ALWAYS use the REAL ids/assets from the lists
above and compute REAL coordinates for the current scene and bounds):

Command: "move the chair to the left"
{
  "operations": [
    {"op": "move", "object_id": "chair_01", "position": {"x": -1.2, "y": -3.0, "z": -4.5}, "rotation": {"x": 0, "y": 0, "z": 0}}
  ],
  "reasoning": "Shifted the chair left along -X from its current spot."
}

Command: "rotate the table 90 degrees"
{
  "operations": [
    {"op": "rotate", "object_id": "table_01", "rotation": {"x": 0, "y": -1.5708, "z": 0}}
  ],
  "reasoning": "90 deg clockwise = -1.5708 rad added to the table's current y-rotation."
}

Command: "add an ergonomic chair next to the table"
{
  "operations": [
    {"op": "add", "asset": "ergonomic chair", "position": {"x": 1.3, "y": -3.0, "z": -4.5}, "rotation": {"x": 0, "y": 0, "z": 0}}
  ],
  "reasoning": "Placed a new chair ~1.2m beside the existing table (table is only an anchor)."
}

Command: "add a table and 2 chairs around it"
{
  "operations": [
    {"op": "add", "asset": "table", "position": {"x": 0.0, "y": -3.0, "z": -4.5}, "rotation": {"x": 0, "y": 0, "z": 0}},
    {"op": "add", "asset": "chair", "position": {"x": -0.9, "y": -3.0, "z": -4.5}, "rotation": {"x": 0, "y": 1.5708, "z": 0}},
    {"op": "add", "asset": "chair", "position": {"x": 0.9, "y": -3.0, "z": -4.5}, "rotation": {"x": 0, "y": -1.5708, "z": 0}}
  ],
  "reasoning": "Table centered; two chairs on opposite sides, each facing the table."
}

Command: "remove that lamp"
{
  "operations": [
    {"op": "remove", "object_id": "lamp_02"}
  ],
  "reasoning": "Removed the lamp nearest the user."
}

Command: "delete all the chairs"
{
  "operations": [
    {"op": "remove", "object_id": "chair_01"},
    {"op": "remove", "object_id": "chair_02"}
  ],
  "reasoning": "Removed every chair currently in the scene."
}

Command: "put it over there"   (head-gaze on a floor point, no explicit anchor)
{
  "operations": [
    {"op": "move", "object_id": "chair_01", "position": {"x": 1.2, "y": -3.0, "z": -5.4}, "rotation": {"x": 0, "y": 0, "z": 0}}
  ],
  "reasoning": "Moved the most recently touched object to the gazed-at floor point."
}

Command: "what do you think of this room?"
{
  "operations": [],
  "reasoning": "No spatial action requested."
}"""


# ============================================================================
# THE BRAIN — sole reasoning agent
# ============================================================================
class SingleAgent:
    """
    The single reasoning agent. In one LLM call it consumes the full context and returns
    an action plan: a list of move/rotate/add/remove operations. It does the intent
    parsing, asset selection, and ALL coordinate/rotation math itself (no deterministic
    placement helpers are provided — that is intentional).
    """

    # Documented generation config (kept in parity with the MAS model family).
    MODEL_NAME = "gemini-2.5-flash-lite"
    TEMPERATURE = 0.2
    MAX_OUTPUT_TOKENS = 4096

    def __init__(self):
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = genai.GenerativeModel(self.MODEL_NAME)

    def generate_plan(self, context: Dict[str, Any], feedback: Optional[Dict] = None) -> Dict:
        """
        Build the prompt, make one LLM call, and return a parsed action plan.

        Returns a dict of shape:
            {"operations": [...], "reasoning": str}
        On any parse/API failure returns an empty plan (fail-soft, never raises).
        """
        prompt = self._build_prompt(context, feedback)

        try:
            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=self.TEMPERATURE,
                    max_output_tokens=self.MAX_OUTPUT_TOKENS,
                    response_mime_type="application/json",
                ),
            )
            text = response.text or ""
            plan = self._parse_plan(text)
            print(
                f"   [SingleAgent] plan: {len(plan.get('operations', []))} op(s)"
                f" — {plan.get('reasoning', '')[:80]}"
            )
            return plan
        except Exception as e:
            print(f"   [SingleAgent] LLM error: {e}")
            return {"operations": [], "reasoning": f"LLM error: {e}"}

    @staticmethod
    def _parse_plan(text: str) -> Dict:
        """Defensive JSON extraction: first '{' .. last '}' then json.loads.

        Always returns a dict with `operations`, `reasoning`, and `schema_valid`
        (False when the model produced unparseable / malformed output).
        """
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start == -1 or end <= start:
                return {"operations": [], "reasoning": "no JSON found", "schema_valid": False}
            parsed = json.loads(text[start:end])
            ops = parsed.get("operations")
            schema_valid = isinstance(ops, list)
            if not schema_valid:
                ops = []
            return {
                "operations": ops,
                "reasoning": str(parsed.get("reasoning", "")),
                "schema_valid": schema_valid,
            }
        except Exception as e:
            return {"operations": [], "reasoning": f"parse error: {e}", "schema_valid": False}

    def _build_prompt(self, context: Dict[str, Any], feedback: Optional[Dict]) -> str:
        user_prompt = context.get("user_prompt", "")
        scene_inventory = context.get("scene_inventory", [])
        room_bounds = context.get("room_bounds", {})
        user_position = context.get("user_position", {})
        gaze = context.get("gaze", {"type": "none"})
        asset_catalog = context.get("asset_catalog", [])
        recent_context = context.get("recent_context", [])

        context_str = ""
        if recent_context:
            context_str = "\nRECENT CONVERSATION (for resolving 'it'/'them'/'that'):\n"
            for turn in recent_context:
                context_str += f"  Turn {turn.get('turn')}: \"{turn.get('user_prompt')}\""
                if turn.get("involved_objects"):
                    context_str += f" -> objects: {', '.join(turn['involved_objects'])}"
                context_str += "\n"

        gaze_str = self._build_gaze_block(gaze)

        min_b = room_bounds.get("min", {})
        max_b = room_bounds.get("max", {})

        return f"""You are a spatial-reasoning agent for a 3D VR room. You are the ONLY agent:
in ONE response you must understand the user's command, classify the intent, choose
assets when adding, resolve which existing objects are involved, and compute exact
positions and rotations — all at once. Return ONLY JSON.

USER COMMAND:
"{user_prompt}"
{context_str}
CURRENT SCENE OBJECTS (use these EXACT ids; never invent an id; never move objects with "movable": false):
{json.dumps(scene_inventory, indent=2)}

BUILDABLE ASSETS (for "add" only — pick a "name" from this list; do NOT output modelPath/collision/scale):
{json.dumps(asset_catalog, indent=2)}

ROOM BOUNDS (place every object strictly inside; offset ~0.1-0.2m in from walls):
  x: {min_b.get('x')} .. {max_b.get('x')}
  z: {min_b.get('z')} .. {max_b.get('z')}
  floor is at y = {min_b.get('y')}

USER POSITION & FACING:
  position: ({user_position.get('x')}, {user_position.get('y')}, {user_position.get('z')})
  y-rotation (radians): {user_position.get('rotation', {}).get('y', 0)}
{gaze_str}
COORDINATE SYSTEM:
  - X: left(-) to right(+); Z: forward(-) to backward(+); Y: down(-) to up(+)
  - Rotations in radians. User typically faces -Z.
  - Keep objects on the floor (y = {min_b.get('y')}) unless the command clearly says otherwise.

{_DISAMBIGUATION_GUIDE}

SPATIAL RULES:
  - "next to" ~= 1.0-1.5m offset; "in front of" = -Z of ref; "behind" = +Z of ref.
  - "on" = on top of a surface (raise y accordingly).
  - "left/right/forward/backward" are relative to the USER's facing direction.
  - "between X and Y" = midpoint of the two references.
  - "around <anchor>" = distribute the objects evenly on a ring around the anchor, each
    facing it, and clear of its footprint.
  - "facing <target>" = set y-rotation = atan2(target_x - obj_x, target_z - obj_z).
  - "rotate N degrees" -> convert to radians yourself (90 deg = 1.5708) and ADD to the
    object's current y-rotation (clockwise is negative by default).
  - For multiple new objects, space them so their collision footprints do not overlap
    (leave >= 0.1m between edges) and keep a sensible arrangement.
  - Use head-gaze ONLY for vague positions ("there"/"here"/"on that") when there is no
    explicit anchor in the command. If gaze is unavailable, do not invent coordinates.

{_DELETE_GUIDE}

{_FEW_SHOT_EXAMPLES}

OUTPUT — return EXACTLY this JSON shape and nothing else:
{{
  "operations": [
    {{"op": "move",   "object_id": "<existing id>", "position": {{"x":0,"y":{min_b.get('y')},"z":0}}, "rotation": {{"x":0,"y":0,"z":0}}}},
    {{"op": "rotate", "object_id": "<existing id>", "rotation": {{"x":0,"y":1.57,"z":0}}}},
    {{"op": "add",    "asset": "<name from buildable assets>", "position": {{"x":0,"y":{min_b.get('y')},"z":0}}, "rotation": {{"x":0,"y":0,"z":0}}}},
    {{"op": "remove", "object_id": "<existing id>"}}
  ],
  "reasoning": "one short sentence"
}}

FINAL RULES (obey strictly):
  - "op" is one of: move | rotate | add | remove.
  - move/rotate/remove: "object_id" MUST be an id from CURRENT SCENE OBJECTS. Do NOT invent ids.
  - add: "asset" MUST be a name from BUILDABLE ASSETS.
  - Emit ALL objects the command implies (do not drop any in multi-object requests).
  - Keep every object within ROOM BOUNDS and on the floor unless told otherwise.
  - If the command cannot be acted on, return "operations": [] with a short reasoning.
{self._build_feedback_block(feedback)}"""

    @staticmethod
    def _build_gaze_block(gaze: Dict) -> str:
        if not isinstance(gaze, dict) or gaze.get("type") in (None, "none"):
            return (
                "\nHEAD-GAZE: unavailable this turn. Do NOT invent coordinates for vague"
                " positions.\n"
            )
        gtype = gaze.get("type")
        point = gaze.get("point") or {}
        if gtype == "object":
            return (
                "\nHEAD-GAZE: user is looking AT object "
                f"{gaze.get('object_id')} (near {point}). Treat 'there'/'on that' as this"
                " object when no explicit anchor is given.\n"
            )
        return (
            f"\nHEAD-GAZE: user is looking at a {gtype} point {point}. Treat 'there'/'here'"
            " as this point when no explicit anchor is given.\n"
        )

    @staticmethod
    def _build_feedback_block(feedback: Optional[Dict]) -> str:
        if not feedback:
            return ""
        pairs = feedback.get("colliding_pairs", [])
        lines = ""
        for p in pairs:
            lines += f"\n    - {p.get('suggestion', '')}"
        return f"""

COLLISION FEEDBACK — your previous plan caused overlaps. Reposition to the safe
coordinates below (they are computed from exact bounding-box edges). Do NOT reuse the
previous positions:{lines}
"""


# ============================================================================
# THE DRIVER — mirrors Orchestrator's public contract
# ============================================================================
class SingleAgentOrchestrator:
    """
    Drives the single-agent pipeline while preserving the external contract that main.py
    depends on. Reuses ONLY deterministic helpers from the other agents:
      - asset_agent.create_object / known_assets   (asset materialization + catalog)
      - verification_agent._object_allowed_for_removal (movable/structural guard)
      - database.*                                  (execution + broadcast)
      - tools.aabbCheck.check_proposed_objects      (collision)
    """

    # ---- Tunable config (Stage 9: params to confirm) --------------------------
    # Kept as class attributes so both the study lead and the code have ONE place
    # to read/adjust the knobs that define the single-agent condition.
    MAX_ITERATION = 3            # collision retry budget (mirrors the MAS verifier loop)
    RECENT_CONTEXT_LIMIT = 5     # turns of history injected for pronoun resolution
    # Per-command JSONL run-log (Stage 7). Non-fatal; disable with SINGLE_AGENT_LOG=0.
    LOG_ENABLED = os.getenv("SINGLE_AGENT_LOG", "1") != "0"
    LOG_PATH = Path(__file__).resolve().parent / "logs" / "single_agent_runs.jsonl"
    # NOTE: brain sampling knobs (temperature / max output tokens) live on
    # SingleAgent.TEMPERATURE and SingleAgent.MAX_OUTPUT_TOKENS.

    def __init__(
        self,
        language_agent,
        scene_agent,
        asset_agent,
        code_agent,
        verification_agent,
        database,
        user_position: Optional[Dict[str, Any]] = None,
        conversation_manager=None,
    ):
        # Only these three are actually used; the rest are accepted to keep the
        # constructor signature identical to Orchestrator (drop-in swap in main.py).
        self.asset_agent = asset_agent
        self.verification_agent = verification_agent
        self.database = database

        self.brain = SingleAgent()

        self.gaze: Dict[str, Any] = {"type": "none"}
        self.user_position = user_position or {
            "x": 0, "y": 0, "z": 0,
            "rotation": {"x": 0, "y": 0, "z": 0},
        }
        self.max_iteration = self.MAX_ITERATION
        self.last_result_state: Dict[str, Any] = {}
        self.conversation_history: Dict[str, List[Dict]] = {}
        # Per-command run metrics, (re)initialized at the top of process_command
        # and consumed by _log_run at every exit point.
        self._run: Dict[str, Any] = {}

    # ------------------------------------------------------------------ PUBLIC
    def process_command(self, user_prompt: str, session_id: str = "default") -> bool:
        if session_id not in self.conversation_history:
            self.conversation_history[session_id] = []
            print(f"Created new session: {session_id}")

        # Stage 7: initialize per-command run metrics (logged at every exit point).
        self._run = {
            "timestamp": time.time(),
            "session_id": session_id,
            "prompt": user_prompt,
            "start": time.perf_counter(),
            "attempts": 0,
            "retries": 0,
            "collided": False,
            "schema_valid": True,
            "num_ops": 0,
            "op_types": [],
            "dropped": 0,
        }

        print(f"\n{'='*60}\n[SingleAgent] Processing: '{user_prompt}'\n{'='*60}")

        try:
            context = self._build_context(user_prompt, session_id)

            feedback: Optional[Dict] = None
            for attempt in range(self.max_iteration + 1):
                plan = self.brain.generate_plan(context, feedback=feedback)
                self._run["attempts"] += 1
                self._run["schema_valid"] = bool(plan.get("schema_valid", True))
                operations = plan.get("operations", [])
                self._run["num_ops"] = len(operations)
                self._run["op_types"] = [o.get("op") for o in operations]

                if not operations:
                    # Fail-soft: separate a malformed/unparseable response from a
                    # valid "no action needed" plan (never surface a 500).
                    if not self._run["schema_valid"]:
                        err = "Could not parse action plan"
                    else:
                        err = plan.get("reasoning") or "No actionable operations"
                    return self._finish(
                        user_prompt, session_id, success=False, error_message=err,
                    )

                remove_ops = [o for o in operations if o.get("op") == "remove"]
                spatial_ops = [o for o in operations if o.get("op") in ("add", "move", "rotate")]

                # 1) Removal safety (existence + movable/structural).
                removal_errors = self._validate_removals(remove_ops)
                if removal_errors:
                    return self._finish(
                        user_prompt, session_id, success=False,
                        error_message="Cannot remove: " + "; ".join(removal_errors),
                        verification_result={
                            "valid": False,
                            "has_collision": False,
                            "clarification_required": True,
                            "message": "Cannot remove: " + "; ".join(removal_errors),
                        },
                    )

                # 2) Prepare spatial ops (materialize adds, resolve moves), drop hallucinations.
                proposed, exec_spatial, dropped = self._prepare_spatial(spatial_ops)
                self._run["dropped"] = len(dropped)

                if not exec_spatial and not remove_ops:
                    return self._finish(
                        user_prompt, session_id, success=False,
                        error_message="No valid operations"
                        + (f" (dropped: {', '.join(dropped)})" if dropped else ""),
                    )

                # 3) Collision verification (AABB) over adds + moves.
                moved_ids = {p["id"] for p in proposed}
                existing = [
                    o for o in self.database.scene_data.get("objects", [])
                    if o.get("id") not in moved_ids
                ]
                violations = check_proposed_objects(proposed, existing) if proposed else []

                if violations:
                    self._run["collided"] = True
                    collision_info = self._collision_info(violations)
                    if attempt < self.max_iteration:
                        self._run["retries"] += 1
                        print(f"   Collision — retry {attempt + 1}/{self.max_iteration}")
                        feedback = collision_info
                        continue
                    return self._finish(
                        user_prompt, session_id, success=False,
                        error_message="Unable to perform action: movement causes a collision with existing objects",
                        verification_result={
                            "valid": False, "has_collision": True,
                            "message": "Collision after max retries",
                            "violations": violations,
                        },
                        collision_info=collision_info,
                    )

                # 4) No collisions — execute.
                return self._execute(
                    user_prompt, session_id, remove_ops, exec_spatial, dropped,
                )

            # Should not reach here.
            return self._finish(
                user_prompt, session_id, success=False,
                error_message="Exhausted attempts without a valid plan",
            )

        except Exception as e:
            print(f"\n[SingleAgent] Pipeline error: {e}")
            self.last_result_state = {"success": False, "error_message": str(e)}
            self._log_run(False, str(e))
            return False

    # ------------------------------------------------------------- CONTEXT
    def _build_context(self, user_prompt: str, session_id: str) -> Dict[str, Any]:
        return {
            "user_prompt": user_prompt,
            "scene_inventory": self._build_scene_inventory(),
            "room_bounds": self.database.scene_data.get("metadata", {}).get("bounds", {}),
            "user_position": self.user_position,
            "gaze": self.gaze,
            "asset_catalog": self._build_asset_catalog(),
            "recent_context": self._get_recent_context(session_id, self.RECENT_CONTEXT_LIMIT),
        }

    def _build_scene_inventory(self) -> List[Dict]:
        inventory = []
        for obj in self.database.scene_data.get("objects", []):
            entry = {
                "id": obj.get("id"),
                "name": obj.get("name", ""),
                "category": obj.get("category", "unknown"),
                "position": obj.get("position", {}),
                "rotation": obj.get("rotation", {}),
                "movable": obj.get("properties", {}).get("movable", False),
            }
            if obj.get("collision"):
                entry["collision"] = obj["collision"]
            inventory.append(entry)
        return inventory

    def _build_asset_catalog(self) -> List[Dict]:
        catalog = []
        seen = set()
        for meta in self.asset_agent.known_assets.values():
            name = meta.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            col = meta.get("collision") or {}
            catalog.append({
                "name": name,
                "category": meta.get("category"),
                "footprint": {"width": col.get("width"), "depth": col.get("depth")},
            })
        return catalog

    def _get_recent_context(self, session_id: str, limit: int = 5) -> List[Dict]:
        history = self.conversation_history.get(session_id, [])
        if not history:
            return []
        current_ids = {o["id"] for o in self.database.scene_data.get("objects", [])}
        out = []
        for t in history[-limit:]:
            still = [oid for oid in t.get("involved_objects", []) if oid in current_ids]
            out.append({
                "turn": t.get("turn"),
                "user_prompt": t.get("user_prompt"),
                "involved_objects": still,
            })
        return out

    # ------------------------------------------------------------- VALIDATION
    def _validate_removals(self, remove_ops: List[Dict]) -> List[str]:
        errors = []
        for op in remove_ops:
            oid = op.get("object_id")
            obj = self.database.get_object_by_id(oid)
            if not obj:
                errors.append(f"{oid} (not found)")
                continue
            ok, reason = self.verification_agent._object_allowed_for_removal(
                obj, allow_structural=False
            )
            if not ok:
                errors.append(f"{oid} ({reason})")
        return errors

    def _prepare_spatial(
        self, spatial_ops: List[Dict]
    ) -> Tuple[List[Dict], List[Dict], List[str]]:
        """
        Returns (proposed, exec_spatial, dropped).
          - proposed: objects (with collision) for AABB — only 'add' and 'move' contribute.
          - exec_spatial: normalized ops to execute.
          - dropped: human-readable notes for skipped/hallucinated ops.
        """
        proposed: List[Dict] = []
        exec_spatial: List[Dict] = []
        dropped: List[str] = []

        # Reset pending so ids don't inflate across retries.
        self.asset_agent.pending_objects = []

        for op in spatial_ops:
            kind = op.get("op")

            if kind == "add":
                asset = op.get("asset")
                try:
                    obj = self.asset_agent.create_object(asset)
                except Exception as e:
                    dropped.append(f"add '{asset}' ({e})")
                    continue
                obj["position"] = op.get("position") or {"x": 0, "y": -3.0, "z": 0}
                obj["rotation"] = op.get("rotation") or {"x": 0, "y": 0, "z": 0}
                exec_spatial.append({"op": "add", "object": obj})
                if obj.get("collision"):
                    proposed.append(obj)

            elif kind == "move":
                oid = op.get("object_id")
                obj = self.database.get_object_by_id(oid)
                if not obj:
                    dropped.append(f"move '{oid}' (not found)")
                    continue
                new_pos = op.get("position") or obj.get("position")
                exec_spatial.append({
                    "op": "move", "object_id": oid,
                    "position": new_pos, "rotation": op.get("rotation"),
                })
                proposed.append({**obj, "position": new_pos})

            elif kind == "rotate":
                oid = op.get("object_id")
                obj = self.database.get_object_by_id(oid)
                if not obj:
                    dropped.append(f"rotate '{oid}' (not found)")
                    continue
                # Rotation does not change the AABB footprint position -> not in `proposed`.
                exec_spatial.append({
                    "op": "rotate", "object_id": oid,
                    "rotation": op.get("rotation") or obj.get("rotation"),
                })

        if dropped:
            print(f"   Dropped ops: {dropped}")
        return proposed, exec_spatial, dropped

    # ------------------------------------------------------------- EXECUTION
    def _execute(
        self,
        user_prompt: str,
        session_id: str,
        remove_ops: List[Dict],
        exec_spatial: List[Dict],
        dropped: List[str],
    ) -> bool:
        print("[SingleAgent] Executing plan...")
        results: List[Dict] = []
        resolved_ids: List[str] = []

        # Removals first.
        for op in remove_ops:
            oid = op.get("object_id")
            obj = self.database.get_object_by_id(oid)
            name = (obj.get("name") if obj else "") or ""
            ok = False
            try:
                ok = self.database.remove_object(oid)
                if ok:
                    self.database._broadcast_update("object_removed", {
                        "objectId": oid, "type": "remove", "name": name,
                    })
                    resolved_ids.append(oid)
            except Exception as e:
                print(f"   remove {oid} error: {e}")
            results.append({"success": ok, "op": "remove", "object_id": oid})

        # Spatial ops.
        for op in exec_spatial:
            kind = op["op"]
            try:
                if kind == "add":
                    obj = op["object"]
                    self.database.add_object(obj)
                    self.database._broadcast_update("object_added", {
                        "objectId": obj["id"], "objectData": obj, "name": obj["name"],
                    })
                    resolved_ids.append(obj["id"])
                    results.append({"success": True, "op": "add", "object_id": obj["id"]})

                elif kind == "move":
                    oid = op["object_id"]
                    ok = self.database.update_object_position(oid, op["position"])
                    if op.get("rotation"):
                        ok = ok and self.database.update_object_rotation(oid, op["rotation"])
                    if ok:
                        resolved_ids.append(oid)
                    results.append({"success": ok, "op": "move", "object_id": oid})

                elif kind == "rotate":
                    oid = op["object_id"]
                    ok = self.database.update_object_rotation(oid, op["rotation"])
                    if ok:
                        resolved_ids.append(oid)
                    results.append({"success": ok, "op": "rotate", "object_id": oid})
            except Exception as e:
                print(f"   {kind} error: {e}")
                results.append({"success": False, "op": kind,
                                "object_id": op.get("object_id")})

        all_success = bool(results) and all(r["success"] for r in results) and not dropped
        n_ok = sum(1 for r in results if r["success"])

        error_message = None
        if not all_success:
            failed = [r for r in results if not r["success"]]
            parts = []
            if failed:
                parts.append(f"{len(failed)} op(s) failed")
            if dropped:
                parts.append(f"dropped: {', '.join(dropped)}")
            error_message = "; ".join(parts) or "Some operations did not complete"

        final_actions = [{
            "success": all_success,
            "count": n_ok,
            "message": f"{n_ok}/{len(results)} operation(s) succeeded",
        }] + [{"object_id": oid} for oid in resolved_ids]

        return self._finish(
            user_prompt, session_id,
            success=all_success,
            error_message=error_message,
            verification_result={"valid": True, "has_collision": False,
                                 "message": "Verification passed"},
            final_actions=final_actions,
            resolved_ids=resolved_ids,
        )

    # ------------------------------------------------------------- FINALIZE
    def _finish(
        self,
        user_prompt: str,
        session_id: str,
        success: bool,
        error_message: Optional[str] = None,
        verification_result: Optional[Dict] = None,
        collision_info: Optional[Dict] = None,
        final_actions: Optional[List[Dict]] = None,
        resolved_ids: Optional[List[str]] = None,
    ) -> bool:
        self.last_result_state = {
            "success": success,
            "error_message": error_message,
            "verification_result": verification_result,
            "collision_info": collision_info,
            "final_actions": final_actions,
        }
        self._store_turn(session_id, user_prompt, success, resolved_ids or [])
        self._log_run(success, error_message)
        print(f"[SingleAgent] Done — success={success}"
              + (f", error='{error_message}'" if error_message else ""))
        return success

    def _log_run(self, success: bool, error_message: Optional[str]) -> None:
        """Stage 7: append one JSONL line per command for offline MAS-vs-SAS analysis.

        Best-effort and fully non-fatal: any failure here must never affect the
        request. Captures the signals the comparison cares about — schema validity,
        collision incidence, retry count, dropped/hallucinated ops, and latency.
        """
        if not self.LOG_ENABLED:
            return
        run = getattr(self, "_run", None)
        if not run:
            return
        try:
            latency_ms = round((time.perf_counter() - run["start"]) * 1000, 1)
            entry = {
                "timestamp": run["timestamp"],
                "session_id": run["session_id"],
                "prompt": run["prompt"],
                "num_ops": run["num_ops"],
                "op_types": run["op_types"],
                "attempts": run["attempts"],
                "retries": run["retries"],
                "collided": run["collided"],
                "hallucinated_or_dropped": run["dropped"],
                "schema_valid": run["schema_valid"],
                "latency_ms": latency_ms,
                "success": success,
                "error_message": error_message,
            }
            self.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(self.LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            print(f"   [log] write failed (non-fatal): {e}")

    def _store_turn(
        self, session_id: str, user_prompt: str, success: bool, resolved_ids: List[str]
    ) -> None:
        history = self.conversation_history.setdefault(session_id, [])
        history.append({
            "turn": len(history) + 1,
            "timestamp": time.time(),
            "user_prompt": user_prompt,
            "involved_objects": resolved_ids,
            "success": success,
        })
        MAX_HISTORY = 100
        if len(history) > MAX_HISTORY:
            self.conversation_history[session_id] = history[-MAX_HISTORY:]

    @staticmethod
    def _collision_info(violations: List[Dict]) -> Dict:
        return {
            "colliding_pairs": [
                {
                    "mover": v["mover"],
                    "anchor": v["anchor"],
                    "overlap": v["overlap"],
                    "mover_position": v["mover_position"],
                    "anchor_position": v["anchor_position"],
                    "suggestion": v.get("suggestion", "Reposition to avoid overlap"),
                }
                for v in violations
            ],
            "suggestion": "Reposition mover objects to eliminate overlaps",
        }
