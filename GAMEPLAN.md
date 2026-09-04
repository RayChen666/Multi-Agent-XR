# GAMEPLAN — Single-Agent Build

Execution plan for adding a **single-agent** variant of the spatial-reasoning system.
This file is the source of truth for the implementing agent. `big_writeup.txt` is the
human rationale; this is the build spec. Follow it literally. Do not improvise beyond it
without flagging.

> Core idea: ONE LLM call does all reasoning (intent + asset choice + spatial math + final
> action plan). Reuse only the **deterministic** plumbing from the other agents (asset
> materialization, AABB collision, DB/WebSocket). Do **not** reuse their prompts or LLM
> logic. Keep the orchestrator's external contract identical so `main.py`, HTTP, WebSocket,
> and the frontend do not change.

---

## 0. Invariants — DO NOT CHANGE

These must stay byte-for-byte compatible. If a step seems to require changing one of these,
STOP and re-check the plan.

- **External orchestrator contract** consumed by `backEnd/main.py`:
  - `process_command(user_prompt: str, session_id: str = "default") -> bool`
  - attributes: `.user_position`, `.gaze`, `.last_result_state`
  - `last_result_state` keys read by `main.py`: `success`, `error_message`,
    `verification_result` (incl. `clarification_required`), `collision_info`
    (incl. `colliding_pairs`).
- **Frontend / WebXR**, assets, `middleware/sceneData.json`, render path.
- **HTTP endpoint** `/scene/command` and the **WebSocket** broadcast layer.
- The **existing multi-agent code** (`orchestrator.py`, the 4 agents). We ADD a new file
  and change ONE wiring line in `main.py`. We do not delete or rewrite the MAS.
- **Model + generation config parity** with the MAS so both builds are comparable:
  `gemini-2.5-flash-lite`.

---

## 1. Deliverables

1. New file: `backEnd/single_agent.py` containing:
   - `SingleAgent` — the sole reasoning brain (builds prompt, one LLM call, parses plan).
   - `SingleAgentOrchestrator` — thin driver that mirrors the `Orchestrator` public API
     (build context -> brain -> verify + retry -> execute -> populate `last_result_state`).
2. One-line wiring change in `backEnd/main.py` (swap which orchestrator is instantiated).
3. (Optional, recommended) per-command JSONL log for objective backing.

No other files change.

---

## 2. Reuse map — deterministic ONLY (plumbing, not brains)

| Reuse (allowed) | From | What it does | How to call |
|---|---|---|---|
| Asset materialization | `AssetAgent.create_object(name)` | catalog lookup + id gen + inject `modelPath`/`collision`/`scale`/`properties`; returns object with `position=None`, `rotation=None` | agent picks an asset **name**; we materialize |
| Asset catalog (for the prompt) | `AssetAgent.known_assets` | dict of buildable asset names + metadata | list names + category + collision footprint into the prompt |
| Collision check | `tools/aabbCheck.check_proposed_objects(proposed, existing)` | AABB collisions + safe-position suggestions | after the plan, before execute |
| Removal safety guard | `VerificationAgent._object_allowed_for_removal(obj, allow_structural=False)` | movable / non-structural check | per remove op |
| Execution | `Database.update_object_position/rotation`, `add_object`, `remove_object` | mutate scene | see executor mapping |
| Broadcast | `Database._broadcast_update(type, data)` | WebSocket push | see executor mapping |
| Room bounds | `db.scene_data["metadata"]["bounds"]` | hard placement limits | inject into prompt |

**DO NOT reuse** (these are the "brains" / precision crutches we withhold):
- `LanguageAgent.parse_prompt` (intent parsing) — the single agent parses intent itself.
- `SceneAgent._llm_spatial_reasoning`, `_compute_rotation`, and the placement helpers
  `facing` / `around` / `grid` (`tools/facingPlacement`, `aroundPlacement`, `gridLayout`).
  **All coordinate + rotation math goes through the single LLM call.**
- `SceneAgent._llm_removal_reasoning` (id selection) — the single agent selects ids itself.
- `AssetAgent._llm_find_match` semantic LLM matching used as a reasoning step — the single
  agent chooses the asset name; only the deterministic `create_object` lookup is reused.

---

## 3. `SingleAgent` (the brain)

### 3.1 Inputs (context assembled by the driver)
- `user_prompt: str`
- `scene_inventory`: list of every object with `id, name, category, position, rotation,
  collision, movable` (include walls/floor but mark `movable=false` so it never moves them).
- `room_bounds`: `db.scene_data["metadata"]["bounds"]` (`min/max` x/y/z).
- `user_position`: `{x, y, z, rotation:{...}}`.
- `gaze`: the snapshot (`{"type":"none"}` | floor/wall/object with `point`/`object_id`).
- `asset_catalog`: buildable names + category + collision footprint (from `known_assets`).
- `recent_context`: last few turns (prompt + resolved ids) for pronoun resolution parity.

### 3.2 Output — the Action Plan (single JSON object)

```json
{
  "operations": [
    { "op": "move",   "object_id": "chair_01", "position": {"x": 0.5, "y": -3.0, "z": -1.5}, "rotation": {"x": 0, "y": 0, "z": 0} },
    { "op": "rotate", "object_id": "table_01", "rotation": {"x": 0, "y": 1.57, "z": 0} },
    { "op": "add",    "asset": "ergonomic chair", "position": {"x": 1.2, "y": -3.0, "z": -2.0}, "rotation": {"x": 0, "y": 3.14, "z": 0} },
    { "op": "remove", "object_id": "lamp_02" }
  ],
  "reasoning": "one short free-form explanation"
}
```

Rules the prompt MUST state:
- `op` is one of `move | rotate | add | remove`.
- For `move`/`rotate`/`remove`: `object_id` **must** be an id from `scene_inventory`.
  Never invent ids. Never target `movable=false` objects.
- For `add`: `asset` **must** be a name from `asset_catalog`. Do NOT output `modelPath`,
  `collision`, or `scale` — the system fills those.
- Floor is `y = -3.0`; keep objects on the floor unless the command says otherwise.
- All positions must respect `room_bounds` (place strictly inside).
- Use `gaze` to ground deictic "there/here/on that" ONLY when there is no explicit anchor
  in the command; if `gaze.type == "none"`, do not fabricate coordinates.
- `rotate N degrees`: the agent computes the new radians itself (degrees→radians, direction,
  add to current rotation). No helper is provided (intentional).
- Return an empty `operations` list with a `reasoning` if nothing can be done (fail-soft).

### 3.3 Model config (parity + document these exact values)
- model: `gemini-2.5-flash-lite`
- `temperature`: **0.2** (single documented value; note it in code + paper)
- `response_mime_type`: `application/json`
- `max_output_tokens`: **4096** (plans can be multi-op)
- Parse defensively: extract first `{` … last `}`, `json.loads`, on failure return
  `{"operations": [], "reasoning": "<parse error>"}` (never crash).

---

## 4. `SingleAgentOrchestrator` (the driver)

Mirror the `Orchestrator` constructor signature so `main.py` needs no arg changes:

```python
def __init__(self, language_agent, scene_agent, asset_agent, code_agent,
             verification_agent, database, user_position=None, conversation_manager=None):
```

Keep and USE only: `asset_agent` (materialize), `verification_agent` (`_object_allowed_for_removal`),
`database` (execute). Instantiate `self.brain = SingleAgent()`. Keep `self.gaze = {"type":"none"}`,
`self.user_position`, `self.last_result_state = {}`, and a `self.conversation_history = {}`.

### 4.1 `process_command(user_prompt, session_id="default") -> bool`

Pipeline (straight line, no LangGraph):

1. **Build context** (Section 3.1). Room bounds + scene inventory + asset catalog + gaze +
   user_position + recent context.
2. **Call the brain** → action plan. `attempt = 0`.
3. **Split ops**: `remove_ops`, and `spatial_ops` (`add` + `move` + `rotate`).
4. **Validate removals** (existence + `_object_allowed_for_removal`). If any invalid →
   set `verification_result = {"valid": False, "clarification_required": True,
   "message": ...}`, `success=False`, store turn, return False.
5. **Build `proposed` for AABB**:
   - `move`/`rotate`: `{**db.get_object_by_id(id), "position": <new or current>}`
     (rotate keeps current position; move uses new position).
   - `add`: materialize via `asset_agent.create_object(asset)` → set chosen
     `position`/`rotation` → include in `proposed`. Keep the materialized object for execution.
   - `existing` = `db.scene_data["objects"]` minus the ids being moved (adds are not yet in scene).
6. **Verify**: `violations = check_proposed_objects(proposed, existing)`.
   - If violations and `attempt < 3`: build `collision_info.colliding_pairs` (same shape the
     MAS uses: `mover, anchor, overlap, mover_position, anchor_position, suggestion`), feed as
     feedback into a fresh brain call, `attempt += 1`, go to step 3.
   - If violations and `attempt == 3`: `success=False`,
     `error_message="Unable to perform action: movement causes a collision with existing objects"`,
     set `collision_info`, store turn, return False.
   - If no violations: continue.
7. **Execute** (see 4.2). If all ops succeed → `success=True`.
8. **Populate `last_result_state`** with `success, error_message, verification_result,
   collision_info, final_actions`. **Store turn** in history. Return `success`.

Wrap the whole thing in try/except; on exception set
`last_result_state = {"success": False, "error_message": str(e)}` and return False.

### 4.2 Executor mapping (match current orchestrator behavior exactly)

| op | DB call | Broadcast (required?) |
|---|---|---|
| `move` | `update_object_position(id, position)` then `update_object_rotation(id, rotation)` if present | broadcast is **internal** to those calls — do nothing extra |
| `rotate` | `update_object_rotation(id, rotation)` | internal — nothing extra |
| `add` | materialized obj → set `position`/`rotation` → `add_object(obj)` | **must** call `db._broadcast_update("object_added", {"objectId": id, "objectData": obj, "name": name})` |
| `remove` | `remove_object(id)` | **must** call `db._broadcast_update("object_removed", {"objectId": id, "type": "remove", "name": name})` |

> Reason: `update_object_position/rotation` already broadcast internally; `add_object` and
> `remove_object` do NOT — so we broadcast those ourselves, exactly like `orchestrator.py`.

Populate `final_actions` in the same style the MAS uses (a summary entry + per-object
`{"object_id": id}` entries) so history/pronoun resolution keeps working.

---

## 5. `main.py` wiring (the only edit outside the new file)

Change the import + instantiation to use the new driver; keep all constructor args so the
signature matches:

```python
# from orchestrator import Orchestrator
from single_agent import SingleAgentOrchestrator

# orchestration_agent = Orchestrator(language_agent, scene_agent, asset_agent,
#                                    code_agent, verification_agent, scene_database)
orchestration_agent = SingleAgentOrchestrator(language_agent, scene_agent, asset_agent,
                                              code_agent, verification_agent, scene_database)
```

Nothing else in `main.py` changes. The 409 (collision) / 422 (clarification) / 400 error
paths keep working because `last_result_state` carries the same keys.

---

## 6. Edge cases / fail-soft rules

- Malformed JSON from the brain → treat as empty plan; `success=False`,
  `error_message="Could not parse action plan"`. Never 500 from a parse error.
- Hallucinated `object_id` (not in scene) → skip that op, record it; if it was the only op,
  fail with a clear message. (Counts as an observable single-agent weakness — log it.)
- Unknown `asset` name → `create_object` raises `ValueError`; catch, skip, record.
- Empty `operations` → `success=False`, `error_message="No actionable operations"`.
- Removal of `movable=false` / structural → blocked by the guard; `clarification_required`.
- Mixed success (some ops ok, some fail) → `success=False` with a message listing failures
  (do not silently report success).

---

## 7. (Optional) per-command log

Append one JSON line per command to `backEnd/logs/single_agent_runs.jsonl`:
`timestamp, session_id, prompt, num_ops, op_types, retries, collided(before final),
hallucinated_ids, schema_valid, latency_ms, success, error_message`.
Cheap, and gives objective backing (latency/retries/hallucination) alongside the
subjective study.

---

## 8. Acceptance checklist (verify before calling it done)

- [ ] `single_agent.py` created; `main.py` swapped to `SingleAgentOrchestrator`.
- [ ] Server starts; `/scene/command` responds; WebSocket updates reach the frontend.
- [ ] `move the chair left` → chair moves, broadcast seen.
- [ ] `rotate the table 90 degrees` → correct radians computed by the agent.
- [ ] `add a chair next to the table` → asset materialized, placed, `object_added` broadcast.
- [ ] `add 3 chairs around the table` → multi-op plan; collisions trigger retry (≤3).
- [ ] `remove the lamp` → removed, `object_removed` broadcast.
- [ ] `delete the floor` → blocked with clarification (422), no crash.
- [ ] Collision that can't resolve in 3 tries → 409 with `colliding_pairs`.
- [ ] Garbage/ambiguous command → fail-soft, no 500.
- [ ] No changes to frontend, assets, sceneData, or the MAS files.

---

## 9. Params to confirm before building

- `temperature` (default 0.2) and `max_output_tokens` (default 4096).
- Retry budget (default 3, matches MAS).
- Whether to ship the optional JSONL log now or later.
- Whether the single agent gets `recent_context` (recommended: yes, for parity).
