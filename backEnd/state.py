from typing import TypedDict, Optional, List, Dict, Any
from typing_extensions import Literal

"""
State Schema for Multi-Agent System (MAS)

This module defines the shared state that flows through the LangGraph workflow.
Each agent reads from and writes to this state as the command progresses through
the pipeline.

Workflow stages:
1. Language Agent → parses user prompt, determines command_type
2. Routing → based on command_type
   - ADD/DELETE → Asset Agent
   - POS/ROTATE → Scene Agent directly
   - Vague/Complex → Memory Agent → Asset Agent
3. Scene Agent → calculates spatial transformations
4. Verification Agent → checks collisions, validates placement
5. Code Agent → executes database updates
"""

class MASState(TypedDict):
    """
    State shared across all agents in the LangGraph workflow.
    
    This state object is passed between nodes and accumulates information
    as the command progresses through the pipeline.
    """
    
    # ============================================================================
    # INPUT (from user)
    # ============================================================================
    
    user_prompt: str
    """The original natural language command from the user"""
    
    session_id: str
    """Session identifier for conversation tracking and history"""
    
    # ============================================================================
    # ROUTING
    # ============================================================================
    
    command_type: Literal["ADD/DELETE", "POS/ROTATE", "Vague/Complex"]
    """
    Command classification from Language Agent:
    - ADD/DELETE: Adding or removing objects (needs Asset Agent)
    - POS/ROTATE: Simple positioning/rotation (Scene Agent directly)
    - Vague/Complex: Needs memory context or complex reasoning
    """
    
    # ============================================================================
    # AGENT OUTPUTS
    # ============================================================================
    
    parsed_command: Optional[Dict[str, Any]]
    """
    Output from Language Agent containing:
    - original_prompt: str
    - command_type: str
    - involved_objects: List[str]
    - spatial_concepts: List[str]
    - intent_summary: str
    - action_hints: Dict
    """
    
    selected_assets: Optional[Dict[str, Any]]
    """
    Output from Asset Agent containing:
    - objects: List[str] - object names/IDs to add or remove
    - action: "add" | "remove"
    - metadata: Optional details about assets
    """
    
    proposed_placement: Optional[Dict[str, Any]]
    """
    Output from Scene Agent containing:
    Single object format:
    - object_id: str
    - position: {x, y, z}
    - rotation: {x, y, z}
    - action: "move" | "rotate" | "place"
    - reasoning: str
    
    Multi-object format:
    - objects: List[Dict] (each with above fields)
    - reasoning: str
    """
    
    verification_result: Optional[Dict[str, Any]]
    """
    Output from Verification Agent containing:
    - has_collision: bool
    - valid: bool
    - message: str
    - violations: Optional[List[Dict]] each with mover, anchor, overlap, mover_position, anchor_position, suggestion
    """
    
    collision_info: Optional[Dict[str, Any]]
    """
    Detailed collision information for retry logic:
    - colliding_pairs: List[Dict] each with mover, anchor, overlap, mover_position, anchor_position, suggestion
    - suggestion: str
    - severity: Optional[str]
    """
    
    # ============================================================================
    # SCENE CONTEXT
    # ============================================================================
    
    scene_state: Dict[str, Any]
    """
    Current scene state from database:
    - objects: List[Dict] with id, name, position, rotation, category
    - metadata: Optional scene metadata
    """
    
    object_states: Optional[List[Dict[str, Any]]]
    """
    Current states of involved objects (retrieved by Verification Agent):
    Each object contains:
    - id: str
    - name: str
    - position: {x, y, z}
    - rotation: {x, y, z}
    This tracks the ACTUAL state of objects BEFORE transformation.
    Essential for maintaining accurate state history in conversation memory.
    """
    
    memory_context: Optional[Dict[str, Any]]
    """
    Context from Memory Agent for vague/complex commands:
    - recent_turns: List[Dict] - recent conversation history
    - relevant_objects: Optional[List[str]]
    - context_summary: str
    """
    
    # ============================================================================
    # ITERATION TRACKING (for collision retry logic)
    # ============================================================================
    
    iteration_count: int
    """Current iteration number for retry logic (starts at 0)"""
    
    max_iteration: int
    """Maximum allowed iterations before giving up (default: 3)"""
    
    # ============================================================================
    # FINAL RESULTS
    # ============================================================================
    
    success: bool
    """Whether the command executed successfully"""
    
    error_message: Optional[str]
    """Error message if command failed"""
    
    final_actions: Optional[List[Dict[str, Any]]]
    """
    List of executed actions from Code Agent:
    Each action contains:
    - success: bool
    - object_id: str
    - action: str
    - message: str
    """


# ============================================================================
# HELPER TYPES (for type safety in agents)
# ============================================================================

class ParsedCommand(TypedDict):
    """Output format from Language Agent"""
    original_prompt: str
    command_type: Literal["ADD/DELETE", "POS/ROTATE", "Vague/Complex"]
    involved_objects: List[str]
    spatial_concepts: List[str]
    intent_summary: str
    action_hints: Dict[str, Any]


class SpatialTransformation(TypedDict):
    """Single object transformation from Scene Agent"""
    object_id: str
    position: Dict[str, float]  # {x, y, z}
    rotation: Dict[str, float]  # {x, y, z}
    action: Literal["move", "rotate", "place", "arrange"]
    reasoning: str


class MultiObjectTransformation(TypedDict):
    """Multi-object transformation from Scene Agent"""
    objects: List[SpatialTransformation]
    reasoning: str


class VerificationResult(TypedDict):
    """Output from Verification Agent"""
    has_collision: bool
    valid: bool
    message: str
    violations: Optional[List[Dict[str, Any]]]


class ExecutionResult(TypedDict):
    """Single execution result from Code Agent"""
    success: bool
    object_id: str
    action: str
    message: str


class RemoveTargetSpec(TypedDict, total=False):
    """
    Per-target removal specification for delete intent resolution.

    One command may carry multiple independent target specs.
    Example: "delete the chair and the table" =>
      - {object_type: "chair", quantity_mode: "exact", quantity: 1, ...}
      - {object_type: "table", quantity_mode: "exact", quantity: 1, ...}
    """

    object_type: str
    """
    Canonical object category/type phrase to resolve in scene state.
    Example: "chair", "coffee table", "lamp".
    """

    quantity_mode: Literal["exact", "all"]
    """
    - "exact": remove exactly `quantity` matches for this target spec.
    - "all": remove all matches for this target spec.
    """

    quantity: int
    """
    Required when quantity_mode is "exact".
    Intended default (when omitted by parser) is 1.
    """

    reference_type: Literal["deictic", "definite", "indefinite", "numeric", "all"]
    """
    Linguistic reference class inferred from prompt:
    - deictic: "this/that"
    - definite: "the"
    - indefinite: "a/an"
    - numeric: explicit count ("2 chairs")
    - all: universal scope for this type ("all chairs")
    """

    spatial_filter: Optional[Dict[str, Any]]
    """
    Optional spatial constraints for this target spec.
    Example shapes may include relation phrases or structured constraints
    (near window, left of table, closest to user, etc.).
    """

    selection_policy: str
    """
    Tie-break/selection strategy within this target spec.
    Default policy is expected to be "nearest_to_user".
    """


class RemoveIntent(TypedDict):
    """
    Structured delete intent shared across agents.
    """
    
    global_scope: Literal["none", "all_objects"]
    """
    Global remove scope:
    - "none": resolve from target_specs only.
    - "all_objects": remove all eligible movable objects.
    """

    target_specs: List[RemoveTargetSpec]
    """
    Independent per-type/per-reference target groups for deletion.
    """

    original_prompt: str
    """
    Original user text for traceability and downstream verification.
    """



