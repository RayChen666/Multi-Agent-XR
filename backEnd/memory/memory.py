import json
import os
from pathlib import Path
from typing import Dict, List, Optional

try:
    # When imported as part of the package (from orchestrator)
    from memory.imageAgent import ImageAgent
except ModuleNotFoundError:
    # When run directly as a standalone script for testing
    from imageAgent import ImageAgent


# ============================================================================
# REFERENCE IMAGE LIBRARY
# Maps target room types to canonical reference image paths.
# Images live in: webXR/assets/reference_layouts/<room_type>.jpg
# ============================================================================
REFERENCE_IMAGE_LIBRARY = {
    "office":      "reference_layouts/office.jpg",
    "bedroom":     "reference_layouts/bedroom.jpg",
    "living room": "reference_layouts/living_room.jpg",
    "dining room": "reference_layouts/dining_room.jpg",
    "kitchen":     "reference_layouts/kitchen.jpg",
    "studio":      "reference_layouts/studio.jpg",
    "library":     "reference_layouts/library.jpg",
    "gym":         "reference_layouts/gym.jpg",
}

# Nodes in the layout_graph that are architectural / structural —
# never added as furniture assets.
ARCHITECTURAL_NODES = {
    "floor", "ceiling", "door", "window",
    "back_wall", "front_wall", "left_wall", "right_wall",
    "back_right_corner", "front_left_corner",
    "back_left_corner", "front_right_corner",
}


class Memory:
    """
    Memory Agent — Complex Route only.

    Triggered when the Language Agent classifies a command as "Vague/Complex",
    meaning the user intent requires whole-room reasoning rather than a single
    object transformation.

    Responsibilities
    ----------------
    1.  Detect the TARGET room type from the user's natural-language command.
    2.  Delegate to ImageAgent → get semantic layout graph (nodes + edges).
    3.  Derive population plan via PURE DATA TRANSFORMATION of the layout graph
        — no second LLM call needed.
    4.  Return a unified memory_context dict for the Orchestrator to store in
        state["memory_context"], and patch parsed_command for the Asset Agent.

    CURRENT SCOPE: Create-from-scratch only.
    # TODO: conversion route — add KEEP/REMOVE delta reasoning against existing
    #        scene objects once the population baseline is stable.

    Output contract (state["memory_context"])
    ------------------------------------------
    {
        "target_room_type": str,
        "semantic_layout":  { ... },         # graph from ImageAgent
        "population_plan": {
            "keep":   [],                    # always empty for now
            "remove": [],                    # always empty for now
            "add": [
                {
                    "object":      str,      # canonical object type name
                    "quantity":    int,
                    "description": str
                }
            ],
            "population_rationale": str
        },
        "room_dimensions":  dict,            # derived from scene_state metadata
        "layout_rationale": str              # forwarded to Verification Agent
    }
    """

    def __init__(self, assets_base_path: str = None):

        print("Memory module initialized")
        
        # ImageAgent handles reference image loading + semantic layout extraction
        self.image_agent = ImageAgent(assets_base_path)


    # =========================================================================
    # PUBLIC API — called by Orchestrator._memory_node()
    # =========================================================================

    def process(self,
                parsed_command: Dict,
                scene_state: Dict) -> Optional[Dict]:
        """
        Main entry point. Called from the Orchestrator memory node.

        Args:
            parsed_command : enriched output from LanguageAgent.parse_prompt()
            scene_state    : database.scene_data (ground-truth JSON)

        Returns:
            memory_context dict or None on failure.
        """
        print("\nMemoryAgent: starting complex-route reasoning...")

        original_prompt = parsed_command.get("original_prompt", "")
        intent_summary  = parsed_command.get("intent_summary", original_prompt)

        # ── Step 1: detect target room type ──────────────────────────────────
        target_room_type = self._detect_target_room_type(
            original_prompt, intent_summary
        )
        print(f"   Target room type detected: '{target_room_type}'")

        # ── Step 2: ImageAgent → semantic layout graph ────────────────────────
        semantic_layout = self.image_agent.extract_semantic_layout(target_room_type)
        if not semantic_layout:
            print("   ImageAgent returned no semantic layout — aborting")
            return None

        # ── Step 3: derive room dimensions from scene metadata ────────────────
        room_dims = self._get_room_dimensions(scene_state)
        print(f"   Room dimensions: {room_dims}")

        # ── Step 4: population plan via data transformation (no LLM) ─────────
        population_plan = self._reason_population_plan(
            target_room_type,
            semantic_layout,
            room_dims
        )
        if not population_plan:
            print("   Population planning failed — aborting")
            return None

        print(f"   Population plan: "
              f"add={len(population_plan.get('add', []))} object type(s)")

        # ── Step 5: assemble memory_context ──────────────────────────────────
        memory_context = {
            "target_room_type":  target_room_type,
            "semantic_layout":   semantic_layout,
            "population_plan":   population_plan,
            "room_dimensions":   room_dims,
            "layout_rationale":  semantic_layout.get("layout_rationale", ""),
        }

        # Patch parsed_command so Asset Agent consumes the ADD list
        self._patch_parsed_command_for_asset_agent(parsed_command, population_plan)

        print("   MemoryAgent complete — memory_context ready\n")
        return memory_context


    # =========================================================================
    # STEP 1 — detect target room type
    # =========================================================================

    def _detect_target_room_type(self,
                                  original_prompt: str,
                                  intent_summary: str) -> str:
        """
        Use LLM to extract target room type from user command.
        Falls back to keyword matching if the LLM call fails.
        """

        # Keyword fallback
        text = (original_prompt + " " + intent_summary).lower()
        for room_type in REFERENCE_IMAGE_LIBRARY:
            if room_type in text:
                return room_type

        return "unknown"


    # =========================================================================
    # STEP 4 — population plan via pure data transformation
    # =========================================================================

    def _reason_population_plan(self,
                                 target_room_type: str,
                                 semantic_layout: Dict,
                                 room_dims: Dict) -> Optional[Dict]:
        """
        Derives the population plan directly from the layout_graph
        produced by ImageAgent — NO additional LLM call.

        Logic:
          1. Read layout_graph.nodes from semantic_layout
          2. Filter out architectural / boundary nodes
          3. Build the ADD list — 1 of each furniture node
          4. Return population_plan dict

        This replaces the previous LLM-based approach which caused
        JSON parse errors and added unnecessary latency + token cost.
        The ImageAgent already did the hard reasoning; we just read its output.
        """
        layout_graph = semantic_layout.get("layout_graph", {})
        nodes        = layout_graph.get("nodes", [])

        if not nodes:
            print("   No nodes in layout_graph — using fallback")
            return self._fallback_population_plan()

        # Filter to furniture nodes only — exclude architectural + boundary nodes
        furniture_nodes = [
            node for node in nodes
            if node.lower() not in ARCHITECTURAL_NODES
            and "wall"   not in node.lower()
            and "window" not in node.lower()
            and "corner" not in node.lower()
            and "door"   not in node.lower()
            and "floor"  not in node.lower()
            and "ceil"   not in node.lower()
        ]

        if not furniture_nodes:
            print("   No furniture nodes found in layout_graph — using fallback")
            return self._fallback_population_plan()

        print(f"   Furniture nodes extracted: {furniture_nodes}")

        # Build ADD list — quantity 1 per furniture type
        add_list = [
            {
                "object":      node,
                "quantity":    1,
                "description": f"{node} suitable for a {target_room_type}"
            }
            for node in furniture_nodes
        ]

        return {
            "keep":   [],
            "remove": [],
            "add":    add_list,
            "population_rationale": (
                f"Furniture nodes extracted directly from ImageAgent layout_graph "
                f"for a {target_room_type} — no additional LLM call required."
            )
        }


    # =========================================================================
    # STEP 5 — patch parsed_command for Asset Agent
    # =========================================================================

    def _patch_parsed_command_for_asset_agent(self,
                                               parsed_command: Dict,
                                               population_plan: Dict) -> None:
        """
        Rewrite parsed_command in place so the Asset Agent receives
        a well-formed ADD command derived from the population plan.

        The Asset Agent reads:
          - parsed_command["involved_objects"]
          - parsed_command["action_hints"]["primary_action"]

        We also inject:
          - parsed_command["objects_to_remove"]  → for Execution Agent
          - parsed_command["objects_to_keep"]    → for Scene Agent context

        Mutates in place — the Orchestrator passes the same dict reference
        through the graph, so all downstream agents see the update.
        """
        add_items = population_plan.get("add", [])

        if not add_items:
            parsed_command["involved_objects"] = []
            parsed_command["action_hints"]["primary_action"] = "arrange"
        else:
            # Expand quantities into flat list
            # e.g. [{"object": "desk", "quantity": 1}, {"object": "chair", "quantity": 2}]
            # →    ["desk", "chair", "chair"]
            involved = []
            for item in add_items:
                obj_name = item.get("object", "")
                qty      = max(1, int(item.get("quantity", 1)))
                involved.extend([obj_name] * qty)

            parsed_command["involved_objects"]                         = involved
            parsed_command["action_hints"]["primary_action"]           = "add"
            parsed_command["action_hints"]["requires_asset_selection"] = True

        # Always inject remove/keep for downstream agents
        parsed_command["objects_to_remove"] = population_plan.get("remove", [])
        parsed_command["objects_to_keep"]   = population_plan.get("keep",   [])

        print(f"   parsed_command patched:")
        print(f"      add    → {parsed_command.get('involved_objects', [])}")
        print(f"      remove → {parsed_command.get('objects_to_remove', [])}")
        print(f"      keep   → {parsed_command.get('objects_to_keep', [])}")


    # =========================================================================
    # HELPERS
    # =========================================================================

    def _get_room_dimensions(self, scene_state: Dict) -> Dict:
        """
        Derive room dimensions from scene_state metadata bounds.
        Falls back to conservative defaults if metadata is absent.
        """
        metadata = scene_state.get("metadata", {})
        bounds   = metadata.get("bounds", {})
        min_b    = bounds.get("min", {"x": -1,  "y": -1,  "z": -2.5})
        max_b    = bounds.get("max", {"x":  1,  "y":  0.5,"z": -0.5})
        return {
            "x": round(max_b["x"] - min_b["x"], 3),
            "y": round(max_b["y"] - min_b["y"], 3),
            "z": round(max_b["z"] - min_b["z"], 3),
        }

    def _fallback_population_plan(self) -> Dict:
        """
        Minimal safe fallback: empty add list.
        Prevents pipeline crash when layout_graph is missing or empty.
        """
        return {"keep": [], "remove": [], "add": [],
                "population_rationale": "Fallback — no furniture nodes found."}


# =============================================================================
# STANDALONE TEST
# =============================================================================
if __name__ == "__main__":

    agent = Memory()

    mock_parsed_command = {
        "original_prompt": "make this plain space into an office",
        "command_type":    "Vague/Complex",
        "involved_objects": [],
        "spatial_concepts": ["room creation from scratch", "office setup"],
        "intent_summary":  "Furnish an empty space as a functional office",
        "action_hints": {
            "primary_action":             "arrange",
            "requires_asset_selection":   False,
            "requires_spatial_reasoning": True
        }
    }

    # Empty scene — structure matches real sceneData.json
    mock_scene_state = {
        "metadata": {
            "sceneName": "empty_room",
            "bounds": {
                "min": {"x": -1, "y": -1,   "z": -2.5},
                "max": {"x":  1, "y":  0.5, "z": -0.5}
            }
        },
        "objects": []
    }

    result = agent.process(mock_parsed_command, mock_scene_state)

    print("\n" + "="*60)
    print("MEMORY AGENT OUTPUT (memory_context):")
    print("="*60)
    print(json.dumps(result, indent=2))

    print("\n" + "="*60)
    print("PATCHED parsed_command (seen by Asset Agent):")
    print("="*60)
    print(json.dumps(mock_parsed_command, indent=2))

