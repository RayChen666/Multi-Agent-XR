import google.generativeai as genai
import json
import os
import time
from typing import Dict, Optional, Any, TypedDict, List
from typing_extensions import Literal
from pathlib import Path
import sys
from langgraph.graph import StateGraph, END
# Add backEnd directory to Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from __init__ import LanguageAgent, SceneAgent, AssetAgent, CodeAgent, VerificationAgent
from database import Database
from state import MASState
from backEnd.memory.conversationManager import ConversationManager
from __init__  import Memory

class Orchestrator:
    """
        Orchestrator: Manages the workflow between Language, Scene, and Code agents.
        
        Workflow:
        1. User prompt → Language Agent (parse to structured command)
        2. Structured command + current state → Scene Agent (calculate spatial changes)
        3. Spatial changes → Code Agent (execute database updates)
    """

    def __init__(self, 
                 language_agent: LanguageAgent, 
                 scene_agent: SceneAgent, 
                 asset_agent: AssetAgent,
                 code_agent: CodeAgent, 
                 verification_agent: VerificationAgent,
                 database:  Database,
                 user_position: Optional[Dict[str, Any]] = None,
                 conversation_manager: Optional[ConversationManager] = None):
        
        # Initialize
        self.workflow =self._build_graph()
        self.app = self.workflow.compile()

        self.language_agent = language_agent
        self.scene_agent =  scene_agent
        self.asset_agent = asset_agent
        self.code_agent = code_agent
        self.database = database
        self.verification_agent = verification_agent
        self.user_position = user_position
        self.conversation_manager = conversation_manager

        # This stores the conversation history of the current session
        self.conversation_history = {}

        
        # Default user position if not provided
        # In case when the head track mode is not activated
        if user_position is None:
            user_position = {
                'x': 0, 'y': 0, 'z': 0,
                'rotation': {'x': 0, 'y': 0, 'z': 0}
            }

    """Build the LangGraph workflow"""  
    def _build_graph(self) -> StateGraph:
        
        workflow = StateGraph(MASState)

        # Add nodes
        workflow.add_node("parse_decider", self._parse_and_decide)
        workflow.add_node("scene_agent", self._scene_node)
        workflow.add_node("asset_agent", self._asset_node)
        workflow.add_node("verification_agent", self._verification_node)
        workflow.add_node("execution_agent", self._execution_node)
        workflow.add_node("memory", self._memory_node)
        workflow.add_node("increment_iteration", self._increment_iteration)
        
        # Set entry point
        workflow.set_entry_point("parse_decider")

        # Add conditional routing from parse_decider
        workflow.add_conditional_edges(
            "parse_decider",
            self._route_command,
            {
                "asset_agent": "asset_agent",
                "scene_agent": "scene_agent",
                "memory": "memory"
            }
        )


        """Add edges and conditional edges"""
        # Case: ADD/DELETE path (asset → scene → verification → execution)
        workflow.add_edge("asset_agent", "scene_agent")
        # Case: Vague/Complex path (memory → asset → scene)
        workflow.add_edge("memory", "asset_agent")
        # Case: POS/ROTATE path (scene → verification → execution)
        workflow.add_edge("scene_agent", "verification_agent")

        # Execution phase
        # workflow.add_edge("verification_agent", "execution_agent")

        # Conditional: iterate or end
        workflow.add_conditional_edges(
            "verification_agent",
            self._check_verification_result,
            {
                "execution_agent": "execution_agent", # No collision -> continue
                #"scene_agent": "scene_agent",
                "increment_iteration": "increment_iteration",
                "end": END
            }
        )
        # increment → back to scene for retry
        workflow.add_edge("increment_iteration", "scene_agent")
        # Iterate back to the initial state
        workflow.add_edge("execution_agent", END)

        return workflow


        
            


    def _route_command(self, state: MASState) -> Literal["asset_agent", 
                                                         "scene_agent",
                                                         "memory"]:
        
        """Route based on command type"""
        command_type = state.get("command_type")
        
        print(f"\nRouting: {command_type}")

        if command_type == "ADD/DELETE":
            return "asset_agent"
        elif command_type == "POS/ROTATE":
            return "scene_agent"
        else:  # Vague/Complex
            return "memory"

    def _check_verification_result(self, state: MASState) -> Literal["execution_agent",  
                                                                     "increment_iteration",
                                                                     "end"]:
        """Decide what to do based on verification result"""
        verification = state.get("verification_result", {})
        has_collision = verification.get("has_collision", False)
        iteration_count = state.get("iteration_count", 0)
        max_iterations = state.get("max_iteration", 3)
        
        if not has_collision:
            
            if verification.get("valid") is False:
                print("Verification failed — stopping before execution")
                if not state.get("error_message"):
                    state["error_message"] = verification.get(
                        "message", "Verification failed"
                    )
                state["success"] = False
                return "end"
            
            print("Verification passed - proceeding to execution")
            
            return "execution_agent"
        
        if iteration_count < max_iterations:
            print(f"Collision detected - retrying ({iteration_count + 1}/{max_iterations})")
            #state["iteration_count"] = iteration_count + 1
            #return "scene_agent"
            return "increment_iteration"
        
        print(f"Max retries reached - giving up")
        #state["error_message"] = "Max retries reached due to collisions"
        return "end"                                                  

    def _parse_and_decide(self, state: MASState) -> MASState:
        """Parse user prompt and decide command type"""
        print(f"\n{'='*60}")
        print(f"Processing: '{state['user_prompt']}'")
        print(f"Session: {state.get('session_id', 'default')}")
        print(f"{'='*60}\n")
        
        print("Step 1: Language Agent parsing...")

        session_id = state.get("session_id", "default")
        recent_context = self._get_recent_context(session_id)
        parsed_command = self.language_agent.parse_prompt(
            state["user_prompt"],
            context_history = recent_context
            )

        if not parsed_command:
            print("Failed to parse command")
            state["success"] = False
            state["error_message"] = "Parse failed"
            state["parsed_command"] = None
            state["command_type"] = None
            return state
        
        command_type = parsed_command.get("command_type")
        
        print(f"Parsed: {parsed_command}\n")
        state["parsed_command"] = parsed_command
        state["command_type"] = command_type

        return state


    
    def _asset_node(self, state: MASState) -> MASState:
        """
        Asset Agent: Create new objects (ADD only)
        """
        print("Step 2: Asset Agent processing...")
        parsed_command = state.get("parsed_command")

        if not parsed_command:
            print("ERROR: No parsed_command in state")
            state["success"] = False
            state["error_message"] = "Asset Agent requires parsed_command"
            return state
        
        # Process with AssetAgent
        result = self.asset_agent.process_command(parsed_command)

        if not result.get("success", False):
            print(f"Asset operation failed: {result.get('message')}")
            state["success"] = False
            state["error_message"] = result.get("message", "Asset operation failed")
            return state

        state["selected_assets"] = result
    
        print(f"Asset operation complete: {result.get('message', '')}\n")
        return state
    
    
    def _scene_node(self, state: MASState) -> MASState:
        """
        Scene Agent: Calculate spatial transformations
        """
        print("Step 3: Scene Agent calculating spatial changes...")

        parsed_command = state.get("parsed_command")
        scene_state = state.get("scene_state")
        selected_assets = state.get("selected_assets")
        # Get raw bounds from scene_state metadata
        room_bounds = state.get("scene_state", {}).get("metadata", {}).get("bounds")


        # Hygiene: upstream failure (e.g. AssetAgent) — do not run Scene logic.
        # Note: cannot use success==False alone; initial state keeps success False until execution.
        if state.get("error_message") and selected_assets is None:
            print("   Skipping Scene Agent (upstream error)")
            return state

        # CASE 0: REMOVE — SceneAgent (LLM) resolves which object id(s) to delete
        if selected_assets and selected_assets.get("action") == "remove":
            feedback = None
            if state.get("iteration_count", 0) > 0:
                collision_info = state.get("collision_info", {})
                if collision_info:
                    feedback = {
                        "previous_attempt": state.get("proposed_placement", {}),
                        #"collision_with": collision_info.get("colliding_objects", []),
                        "colliding_pairs": collision_info.get("colliding_pairs", []),
                        "suggestion": collision_info.get(
                            "suggestion", "Adjust selection"
                        ),
                    }

            parsed_for_removal = {**(parsed_command or {})}
            ri = selected_assets.get("remove_intent")
            if ri:
                parsed_for_removal["remove_intent"] = ri

            result = self.scene_agent.resolve_removal_targets(
                parsed_for_removal,
                scene_state,
                self.user_position,
                feedback=feedback,
            )

            if not result or not result.get("target_object_ids"):
                print("Scene Agent could not resolve removal targets")
                state["success"] = False
                state["error_message"] = "Removal target resolution failed"
                state["proposed_placement"] = None
                return state

            object_ids = list(result.get("target_object_ids", []))
            removal_policy = {
                "scope_hint": (ri or {}).get("scope_hint", "contextual"),
                "original_prompt": (parsed_command or {}).get("original_prompt", ""),
                "involved_objects": (parsed_command or {}).get("involved_objects", []),
            }
            if (
                self.verification_agent.infer_removal_multiplicity(removal_policy)
                == "single"
                and len(object_ids) > 1
            ):
                before_n = len(object_ids)
                object_ids = self.verification_agent.narrow_removal_ids_to_closest(
                    object_ids,
                    scene_state,
                    self.user_position,
                )
                print(
                    f"   Singular intent: narrowed {before_n} candidate(s) → 1 "
                    f"(closest to user: {object_ids[0]})"
                )
                base_reason = result.get("reasoning") or ""
                result = {
                    **result,
                    "reasoning": (
                        f"{base_reason} [Narrowed to single target nearest user: {object_ids[0]}]"
                    ).strip(),
                }

            state["proposed_placement"] = {
                "action": "remove",
                "object_ids": object_ids,
                "reasoning": result.get("reasoning"),
            }
            print(
                f"   Scene Agent resolved removal of {len(object_ids)} object(s)"
            )
            return state

        # CASE 1: ADD operation - Position new object
        if selected_assets and selected_assets.get("needs_positioning"):
            new_objects = selected_assets.get("new_objects", [])
            if not new_objects:
                new_objects = [selected_assets["new_object"]]
            
            print(f"   Positioning {len(new_objects)} new object(s)")

            object_details = [
                {
                    "id": obj["id"],
                    "name": obj["name"],
                    "category": obj["category"]
                }
                for obj in new_objects
            ]
            
            # Get feedback from previous iteration if any (for collision retry)
            feedback = None
            if state.get("iteration_count", 0) > 0:
                collision_info = state.get("collision_info")
                if collision_info:
                    feedback = {
                        "previous_attempt": state.get("proposed_placement"),
                        #"collision_with": collision_info.get("colliding_objects", []),
                        "colliding_pairs": collision_info.get("colliding_pairs", []),
                        "suggestion": collision_info.get("suggestion", "Try alternative placement")
                    }
            
            # Calculate position/rotation using Scene Agent

            '''
            spatial_updates = self.scene_agent.calculate_spatial_transformation(
                parsed_command,
                scene_state,
                self.user_position,
                new_objects_to_position=object_details, 
                feedback=feedback
            )
            '''

            # now with the updated version we grab sceneGraph from memory context if the command route is complex/vague
            memory_context = state.get("memory_context")
            layout_graph = None
            anchor_object = None
            if memory_context:
                semantic_layout = memory_context.get("semantic_layout", {})
                layout_graph = semantic_layout.get("layout_graph")
                anchor_object = semantic_layout.get("anchor_object")
                
            
            spatial_updates = self.scene_agent.calculate_spatial_transformation(
                parsed_command,
                scene_state,
                self.user_position,
                new_objects_to_position=object_details,
                feedback=feedback,
                layout_graph=layout_graph,
                room_bounds=room_bounds,
                anchor_object=anchor_object,
            )
            
            if not spatial_updates:
                print("Failed to calculate spatial updates for new object")
                state["success"] = False
                state["error_message"] = "Spatial calculation failed"
                return state
            
            if "objects" in spatial_updates:
                positioned_objects = spatial_updates["objects"]
            else:
                # Single object format - wrap it
                positioned_objects = [spatial_updates]
            
            complete_objects = []
            for new_obj in new_objects:
                # Find matching position data
                pos_data = next(
                    (p for p in positioned_objects if p.get("object_id") == new_obj["id"]),
                    None
                )
                
                if pos_data:
                    base_position = pos_data.get("position")
                    y_offset = new_obj.get("y_offset", 0.0)
                    
                    adjusted_position = {
                        "x": base_position["x"],
                        "y": base_position["y"] + y_offset,
                        "z": base_position["z"]
                    }
                    new_obj["position"] = adjusted_position
                    new_obj["rotation"] = pos_data.get("rotation")
                    
                    if y_offset != 0.0:
                        print(f"   {new_obj['id']} positioned at ({adjusted_position['x']:.2f}, "
                            f"{adjusted_position['y']:.2f}, {adjusted_position['z']:.2f}) "
                            f"[y_offset: {y_offset:.2f}]")
                    else:
                        print(f"   {new_obj['id']} positioned at ({adjusted_position['x']:.2f}, "
                            f"{adjusted_position['y']:.2f}, {adjusted_position['z']:.2f})")
                    
                    complete_objects.append(new_obj)
                else:
                    print(f"   No position calculated for {new_obj['id']}")
            
            state["proposed_placement"] = {
                "action": "add_multiple" if len(complete_objects) > 1 else "add",
                "complete_objects": complete_objects  # Array
            }

        # CASE 2: normal placement action
        else: 
            feedback = None
            if state.get("iteration_count", 0) > 0:
                collision_info = state.get("collision_info", {})
                if collision_info:
                    feedback = {
                        "previous_attempt": state.get("proposed_placement", {}),
                        #"collision_with": collision_info.get("colliding_objects", []),
                        "colliding_pairs": collision_info.get("colliding_pairs", []),
                        "suggestion": collision_info.get("suggestion", "Try alternative placement")
                    }

            spatial_updates = self.scene_agent.calculate_spatial_transformation(
                parsed_command,
                scene_state,
                self.user_position,
                feedback=feedback,
                room_bounds=room_bounds,
            )

            if not spatial_updates:
                print("Failed to calculate spatial updates")
                state["success"] = False
                state["error_message"] = "Spatial calculation failed"
                state["proposed_placement"] = None
                return state
            '''
            objects_to_update = []
            if "objects" in spatial_updates:
                objects_to_update = spatial_updates["objects"]
            else:
                objects_to_update = [spatial_updates]

            # Apply y_offset (modifies in-place)
            for obj_update in objects_to_update:
                object_id = obj_update.get("object_id")
                existing_obj = self.database.get_object_by_id(object_id)
                if existing_obj and "position" in obj_update:
                    y_offset = existing_obj.get("y_offset", 0.0)
                    if y_offset != 0.0:
                        base_position = obj_update["position"]
                        adjusted_position = {
                            "x": base_position["x"],
                            "y": base_position["y"] + y_offset, 
                            "z": base_position["z"]
                        }
                        obj_update["position"] = adjusted_position
                        print(f"   Applied y_offset ({y_offset:.2f}) to {object_id}")
            '''
            state["proposed_placement"] = spatial_updates

        print(f"Calculated updates\n")

        return state
        

    def _verification_node(self, state: MASState) -> MASState:
        """
        Verification Agent: Check for collisions and validate placement
        """
        print("Step 4: Verification Agent checking...")
        proposed_placement = state.get("proposed_placement", {})
        if not proposed_placement:
            state["verification_result"] = {
                "has_collision": False,
                "valid": False,
                "message": "No placement to verify"
            }
            return state
        

        # REMOVE: dedicated validation (existence, movable/structural, singular vs plural)
        if proposed_placement.get("action") == "remove":
            object_ids = proposed_placement.get("object_ids", [])
            parsed = state.get("parsed_command") or {}
            selected = state.get("selected_assets") or {}
            remove_intent = selected.get("remove_intent") or {}
            removal_policy = {
                "scope_hint": remove_intent.get("scope_hint", "contextual"),
                "original_prompt": parsed.get("original_prompt", ""),
                "involved_objects": parsed.get("involved_objects", []),
                "enforce_movable_only": True,
                "allow_structural": False,
            }
            state["verification_result"] = self.verification_agent.validate_removal(
                object_ids,
                removal_policy,
            )
            state["collision_info"] = None
            return state

        '''
        if proposed_placement.get("action") in ("add", "add_multiple"):
            complete_objects = proposed_placement.get("complete_objects", [])
            state["verification_result"] = (
                self.verification_agent.validate_add_objects(complete_objects)
            )
            state["collision_info"] = None
            return state
        
        is_valid = self.verification_agent.validate_transformation(proposed_placement)
        
        if not is_valid:
            print("Invalid transformation format")
            state["verification_result"] = {
                "has_collision": False,
                "valid": False,
                "message": "Invalid format"
            }
            return state
        
        # Check for collisions (simplified - implement proper collision detection)
        # For now, just validate that objects exist
        objects_to_check = []
        if "objects" in proposed_placement:
            objects_to_check = proposed_placement["objects"]
        else:
            objects_to_check = [proposed_placement]
        
        has_collision = False
        colliding_objects = []
        
        for obj_transform in objects_to_check:
            object_id = obj_transform.get("object_id")
            obj_state = self.database.get_object_by_id(object_id)
            
            if not obj_state:
                print(f"Object {object_id} not found")
                has_collision = True
                colliding_objects.append(object_id)
        
        if has_collision:
            print(f"Collision detected with: {colliding_objects}")
            state["verification_result"] = {
                "has_collision": True,
                "valid": False,
                "message": "Collision detected"
            }
            state["collision_info"] = {
                "colliding_objects": colliding_objects,
                "suggestion": "Adjust placement to avoid collision"
            }
        else:
            print("No collisions detected\n")
            state["verification_result"] = {
                "has_collision": False,
                "valid": True,
                "message": "Verification passed"
            }
            state["collision_info"] = None
        
        return state
        '''
        # ADD: proposed_placement uses complete_objects, not move/rotate shape
        if proposed_placement.get("action") in ("add", "add_multiple"):
            complete_objects = proposed_placement.get("complete_objects", [])
            result = self.verification_agent.validate_add_objects(complete_objects)
            state["verification_result"] = result

            # populate collision_info for SceneAgent retry context
            if result.get("has_collision") and result.get("violations"):
                state["collision_info"] = {
                    "colliding_pairs": [
                        {
                            "mover": v["mover"],
                            "anchor": v["anchor"],
                            "overlap": v["overlap"],
                            "mover_position": v["mover_position"],
                            "anchor_position": v["anchor_position"],
                            "suggestion": v.get("suggestion", "Reposition to avoid overlap"),
                        }
                        for v in result["violations"]
                    ],
                    "suggestion": "Reposition mover objects to eliminate overlaps"
                }
            else:
                state["collision_info"] = None

            return state

        # POS/ROTATE — schema check then AABB against existing scene
        is_valid = self.verification_agent.validate_transformation(proposed_placement)

        if not is_valid:
            print("Invalid transformation format")
            state["verification_result"] = {
                "has_collision": False,
                "valid": False,
                "message": "Invalid format"
            }
            state["collision_info"] = None
            return state

        # build proposed object list from transformation
        if "objects" in proposed_placement:
            moved_objects = proposed_placement["objects"]
        else:
            moved_objects = [proposed_placement]

        # get full object data for each moved object (needs collision dims)
        proposed = []
        for transform in moved_objects:
            obj_id = transform.get("object_id")
            obj = self.database.get_object_by_id(obj_id)
            if obj:
                proposed.append({
                    **obj,
                    "position": transform["position"],  # use NEW position
                })

        # existing scene excluding the moved objects themselves
        moved_ids = {o["id"] for o in proposed}
        existing = [
            o for o in self.database.scene_data.get("objects", [])
            if o["id"] not in moved_ids
        ]

        from tools.aabbCheck import check_proposed_objects
        violations = check_proposed_objects(proposed, existing)

        if violations:
            print(f"  POS/ROTATE collision detected: {len(violations)} violation(s)")
            state["verification_result"] = {
                "has_collision": True,
                "valid": False,
                "message": f"Movement causes collision with {len(violations)} object(s)",
                "violations": violations
            }
            state["collision_info"] = {
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
                "suggestion": "Reposition mover objects to eliminate overlaps"
            }
        else:
            print("No collisions detected\n")
            state["verification_result"] = {
                "has_collision": False,
                "valid": True,
                "message": "Verification passed"
            }
            state["collision_info"] = None

        return state
    
        

    
    def _execution_node(self, state: MASState) -> MASState:
        """
        Code Agent: Execute the spatial transformation
        """
        print("Step 5: Code Agent executing changes...")
        
        proposed_placement = state.get("proposed_placement", {})
        
        if not proposed_placement:
            print("No placement to execute")
            state["success"] = False
            state["error_message"] = "No placement to execute"
            return state
        
        action = proposed_placement.get("action")

        if action == "remove":
            object_ids = proposed_placement.get("object_ids", [])

            removed_ids: List[str] = []
            failed_ids: List[str] = []

            removed_entries: List[Dict[str, str]] = []

            for object_id in object_ids:
                try:
                    obj = self.database.get_object_by_id(object_id)
                    display_name = (obj.get("name") if obj else "") or ""
                    if self.database.remove_object(object_id):
                        removed_ids.append(object_id)
                        removed_entries.append(
                            {"objectId": object_id, "name": display_name}
                        )
                        
                        self.database._broadcast_update(
                            "object_removed",
                            {
                                "objectId": object_id,
                                "type": "remove",
                                "name": display_name,
                            },
                        )
                    else:
                        failed_ids.append(object_id)
                except Exception:
                    failed_ids.append(object_id)

            if object_ids and len(failed_ids) == 0:
                state["success"] = True
                state["final_actions"] = [{
                    "success": True,
                    "count": len(removed_ids),
                    "action": "remove",
                    "message": f"Removed {len(removed_ids)} object(s)",
                    "removed": removed_entries,
                }]
                state["final_actions"] += [
                    {
                        "object_id": remove_id,
                    }
                    for remove_id in removed_ids
                ]
            else:
                state["success"] = False
                state["error_message"] = (
                    f"Only {len(removed_ids)}/{len(object_ids)} objects removed"
                    if object_ids
                    else "No objects to remove"
                )
                if failed_ids:
                    state["error_message"] += f". Failed: {', '.join(failed_ids)}"


        elif action in ["add", "add_multiple"]:
            complete_objects = proposed_placement.get("complete_objects", [])
            
            if not complete_objects:
                print("No objects to add")
                state["success"] = False
                state["error_message"] = "No objects to add"
                return state
            
            print(f"   Adding {len(complete_objects)} object(s) to scene...")
            
            success_count = 0
            failed_objects = []
            
            for obj in complete_objects:
                print(f"   Adding {obj['id']} ({obj['name']})...")
                print(f"      Position: ({obj['position']['x']:.2f}, {obj['position']['y']:.2f}, {obj['position']['z']:.2f})")
                print(f"      Rotation: ({obj['rotation']['x']:.2f}, {obj['rotation']['y']:.2f}, {obj['rotation']['z']:.2f})")
                
                # Add to database (in-memory)
                try:
                    self.database.add_object(obj)
                    
                    # Broadcast to WebSocket
                    self.database._broadcast_update('object_added', {
                        'objectId': obj['id'],
                        'objectData': obj,
                        'name': obj['name']
                    })
                    
                    print(f"      Successfully added\n")
                    success_count += 1
                    
                except Exception as e:
                    print(f"      Failed: {e}\n")
                    failed_objects.append(obj['id'])
            
            if success_count == len(complete_objects):
                state["success"] = True
                state["final_actions"] = [
                        {
                        "success": True,
                        "count": success_count,
                        "action": "add_multiple" if len(complete_objects) > 1 else "add",
                        "message": f"Added {success_count} object(s)"
                    }
                ]
                state["final_actions"] += [
                    {
                        "object_id": obj["id"],
                    } for obj in complete_objects
                ] 
            else:
                state["success"] = False
                state["error_message"] = f"Only {success_count}/{len(complete_objects)} objects added"
                if failed_objects:
                    state["error_message"] += f". Failed: {', '.join(failed_objects)}"
        else:
            # Use CodeAgent for normal transformations
            result = self.code_agent.execute_transformation(proposed_placement)
            success = result.get("success", False)
            
            if success:
                print(f"\nTransformation completed successfully!")
                state["success"] = True
                state["final_actions"] = result.get("results", [])
            else:
                print(f"\nTransformation failed")
                state["success"] = False
                state["error_message"] = result.get("message", "Execution failed")
        
        print(f"{'='*60}\n")
        
        # Store turn in history
        self._store_turn(state)
        
        return state
    
    def _memory_node(self, state: MASState) -> MASState:
        # Memory: Retrieve relevant context for vague/complex commands
        """
        
        session_id = state.get("session_id", "default")
        user_prompt = state.get("user_prompt", "")
        
        # Get recent conversation context
        recent_context = self._get_recent_context(session_id, limit=10)
        
        state["memory_context"] = {
            "recent_turns": recent_context,
            "context_summary": f"Retrieved {len(recent_context)} previous turns"
        }
        
        print(f"Retrieved {len(recent_context)} previous turns\n")

        """
        memory = Memory(assets_base_path=str(
            Path(__file__).resolve().parent.parent / "webXR" / "assets"
        ))
        
        
        print("Step 2: Memory Agent retrieving context...")
        memory_context = memory.process(
            parsed_command=state["parsed_command"],
            scene_state=state["scene_state"],
        )

        if not memory_context:
            print("Memory Agent failed — aborting")
            state["success"] = False
            state["error_message"] = "Memory Agent returned no context"
            return state
        
        state["memory_context"] = memory_context
        print(f"Memory context ready — target: {memory_context['target_room_type']}\n")



        return state
    
    # ============================================================================
    # HELPER METHODS
    # ============================================================================

    def _increment_iteration(self, state: MASState) -> MASState:
        state["iteration_count"] = state.get("iteration_count", 0) + 1
        print(f"   Iteration count: {state['iteration_count']}")
        return state

    def _get_recent_context(self, session_id: str, limit: int = 5) -> List[Dict]:
        """Get recent conversation context for a session"""
        if session_id not in self.conversation_history:
            return []
        
        full_history = self.conversation_history[session_id]
        current_ids = {obj['id'] for obj in self.database.scene_data.get('objects', [])}
        
        context = []
        for t in full_history[-limit:]:
            # filter involved_objects to only those still in scene
            still_existing = [
                oid for oid in t.get('involved_objects', [])
                if oid in current_ids
            ]
            context.append({
                'turn': t['turn'],
                'user_prompt': t['user_prompt'],
                'involved_objects': still_existing,  # only existing objects
                'command_type': t['command_type'],
                'success': t['success']
            })
        print(f"   Recent context: {[{'turn': c['turn'], 'objects': c['involved_objects']} for c in context]}")
        return context
    '''
        return [
            {
                'turn': t['turn'],
                'user_prompt': t['user_prompt'],
                'involved_objects': t.get('involved_objects', []),  # resolved IDs
                'command_type': t['command_type'],
                'success': t['success']
            }
            for t in full_history[-limit:]
        ]
'''
    def _store_turn(self, state: MASState):
            """Store a complete turn in conversation history"""
            session_id = state.get("session_id", "default")
            
            if session_id not in self.conversation_history:
                self.conversation_history[session_id] = []
            
            full_history = self.conversation_history[session_id]

            final_actions = state.get("final_actions", []) or []
            resolved_ids = [
                a["object_id"] for a in final_actions 
                if a.get("object_id")
            ]
            print(f"   Storing turn — final_actions: {final_actions}")
            print(f"   Storing turn — resolved_ids: {resolved_ids}")

            turn_entry = {
                'turn': len(full_history) + 1,
                'timestamp': time.time(),
                'user_prompt': state.get("user_prompt", ""),
                'parsed_command': state.get("parsed_command"),
                'command_type': state.get("command_type"),
                # resolved IDs from execution
                'involved_objects': resolved_ids,         
                'proposed_placement': state.get("proposed_placement"),
                'verification_result': state.get("verification_result"),
                'final_actions': state.get("final_actions"),
                'success': state.get("success", False),
                'error_message': state.get("error_message"),
                'iteration_count': state.get("iteration_count", 0),
            }
            
            full_history.append(turn_entry)
            
            # Limit history size
            MAX_HISTORY_PER_SESSION = 100
            if len(full_history) > MAX_HISTORY_PER_SESSION:
                self.conversation_history[session_id] = full_history[-MAX_HISTORY_PER_SESSION:]
    
    # ============================================================================
    # PUBLIC API
    # ============================================================================

    def process_command(self, 
                       user_prompt: str,
                       session_id: str = "default") -> bool:
        """
        Process a user command through the LangGraph workflow
        
        Args:
            user_prompt: Natural language command from user
            session_id: Session identifier for conversation tracking
            
        Returns:
            True if command executed successfully, False otherwise
        """
        
        # Initialize session if needed
        if session_id not in self.conversation_history:
            self.conversation_history[session_id] = []
            print(f"Created new session: {session_id}")
        
        # Create initial state
        initial_state: MASState = {
            "user_prompt": user_prompt,
            "session_id": session_id,
            "command_type": "POS/ROTATE",  # Will be determined by Language Agent
            "parsed_command": None,
            "selected_assets": None,
            "proposed_placement": None,
            "verification_result": None,
            "collision_info": None,
            "scene_state": self.database.scene_data,
            "memory_context": None,
            "iteration_count": 0,
            "max_iteration": 3,
            "success": False,
            "error_message": None,
            "final_actions": None,
        }
        
        # Run the workflow
        try:
            final_state = self.app.invoke(initial_state)
            return final_state.get("success", False)
        
        except Exception as e:
            print(f"\nWorkflow error: {e}")
            return False


    '''
    def process_command (self, 
                         user_prompt: str,
                         session_id: str = "default") -> bool:
        """
        Process a user command through the complete agent pipeline

        Args:
            user_prompt: Natural language command from user

        Returns:
            T if command executed successfully through the pipeline, F otherwise

        """

        # initialize session history
        if session_id not in self.conversation_history:
            self.conversation_history[session_id] = []
            print(f"Created new session: {session_id}")

        full_history = self.conversation_history[session_id]
        recent_context = full_history[-5:] if len(full_history) > 0 else []

        print(f"\n{'='*60}")
        print(f"Processing command: '{user_prompt}'")
        print(f"Session: {session_id} (Turn {len(full_history) + 1})")
        print(f"{'='*60}\n")

        # Phase 1: Language agent parse user command
        print("Step 1: Language Agent parsing...")
        parsed_command = self.language_agent.parse_prompt(
            user_prompt,
            context_history = recent_context
            )

        print(f"Parsed: {parsed_command}\n")

        if not parsed_command:
            # Store failure in history
            self._store_turn(session_id, user_prompt, parsed_command=None, 
                        success=False, error="Parse failed")
            print (f"Failed to parse command")
            return False
        
        #  Phase 2: get obj state
        print("Step 2: Getting current object states...")
        involved_objects = parsed_command['involved_objects']

        if not involved_objects:
            self._store_turn(session_id, user_prompt, parsed_command, 
                        success=False, error="No objects specified")
            print("No objects specified in command")
            return False

        # Get state for ALL involved objects
        object_states = []
        for obj_name in involved_objects:
            current_state = self.verification_agent.get_object_state(obj_name)
            
            if not current_state or len(current_state) == 0:
                print(f"Object '{obj_name}' not found in scene")
                continue
            
            object_states.extend(current_state)
            for state in current_state:
                print(f"   Found: {state['name']} (ID: {state['id']})")
                print(f"   Position: {state['position']}")
                print(f"   Rotation: {state['rotation']}")
        
        if not object_states:
            print("No valid objects found to process")
            self._store_turn(session_id, user_prompt, parsed_command,
                            object_states=[], success=False, error="No valid objects found")
            return False

        # Phase 3: Scene agent do the spatial reasoning
        print("Step 3: Scene Agent calculating spatial changes...")
        spatial_updates = self.scene_agent.calculate_spatial_transformation(
            parsed_command,
            self.database.scene_data,
            self.user_position
        )

        if not spatial_updates:
            print("Failed to calculate spatial updates")
            self._store_turn(session_id, user_prompt, parsed_command,
                        object_states=object_states, spatial_updates=None,
                        success=False, error="Spatial calculation failed")
            return False
        
        print(f"   Calculated updates: {spatial_updates}\n")

        # Phase 4: Code agent to execute changes
        print("⚙️ Step 4: Code Agent executing changes...")
        result = self.code_agent.execute_transformation(
            spatial_updates  # FIXED: Single argument
        )
        success = result.get('success', False) if isinstance(result, dict) else False


        if success:
            print(f"\nCommand completed successfully!")
            print(f"{'='*60}\n")
        else:
            print(f"\nCommand failed to execute")
            print(f"   Error: {result.get('message', 'Unknown error')}")

            print(f"{'='*60}\n")
        

        # Store EVERYTHING in history
        self._store_turn(
            session_id=session_id,
            user_prompt=user_prompt,
            parsed_command=parsed_command,
            object_states=object_states,
            spatial_updates=spatial_updates,
            execution_result=result,
            success=success,
            error=result.get('message') if not success else None
        )
        
        return success
        '''
    


# Test
if __name__ == "__main__":

    
    # Initialize all components
    db = Database()
    language_agent = LanguageAgent()
    scene_agent = SceneAgent()
    asset_agent = AssetAgent(db) 
    code_agent = CodeAgent(db)
    verification_agent = VerificationAgent(db)
    
    # Initialize orchestrator
    orchestrator = Orchestrator(
        language_agent, 
        scene_agent, 
        asset_agent,
        code_agent, 
        verification_agent, 
        db
    )
    
    # Test commands
    test_commands = [
        
        "add a hockeny table and place it at left back corner"
        
    ]
    '''
        "rotate the table 90 degrees",
        "move the chair a little forward",
        "move the chair to the right",
        "add 2 lamps and 3 tables and place them evenly"
    '''
    
    for command in test_commands:
        orchestrator.process_command(command)
        input("\nPress Enter to continue...\n")









