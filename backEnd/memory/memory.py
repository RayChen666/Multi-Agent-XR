import google.generativeai as genai
import json
import base64
import os
from pathlib import Path
from typing import Dict, List, Optional, Any

# ============================================================================
#   REFERENCE IMAGE LIBRARY
#   Maps target room types to canonical reference image paths.
#   Images live in: webXR/assets/reference_layouts/<room_type>.jpg
# ============================================================================

class Memory:

    """
        This memory-specific agent is for complex route only

        It is triggered when the Language Agent classifies a command as "Vague/Complex", 
        meaning the user intent requires whole-room reasoning rather than a single object 
        transformation.

        Functionality:
        1. Detect the TARGET room type from the user's natural-language command.
        2. Load a REFERENCE IMAGE for that room type (semantic layout grounding).
        3. Read the CURRENT SCENE JSON (ground-truth object state).
        4. Run a two-stage LLM reasoning call:
            Stage A — semantic layout: 
                extract anchor, zones, cluster relations from the reference image.
            Stage B — delta planning: 
                compare current scene against the target layout → decide what to 
                KEEP / REMOVE / ADD, with quantities and descriptions the Asset 
                Agent can consume directly.
        5. Return a unified memory_context dict that the Orchestrator stores in
        state["memory_context"] before handing off to the Asset Agent and
        Scene Agent.
        
        Output contract (state["memory_context"])
        ------------------------------------------
        {
            "target_room_type": str,
            "semantic_layout": {
                "anchor_object":    str,          # e.g. "desk"
                "anchor_placement": str,          # e.g. "against_longest_wall"
                "functional_zones": {             # zone_name → description
                    "primary_work_zone": str,
                    "storage_zone":      str,
                    "circulation_space": str
                },
                "cluster_relations": [            # relational graph
                    {
                        "type":      str,         # e.g. "office_chair"
                        "relation":  str,         # e.g. "in_front_of"
                        "reference": str          # e.g. "desk"
                    }
                ],
                "room_function":    str           # e.g. "focused_individual_work"
            },
            "delta_plan": {
                "keep":   [str],                  # object IDs to keep as-is
                "remove": [str],                  # object IDs to remove
                "add": [                          # new objects for Asset Agent
                    {
                        "object":      str,       # canonical object type name
                        "quantity":    int,
                        "description": str        # rich description for asset matching
                    }
                ]
            },
            "spatial_constraints_from_current_scene": {
                "room_dimensions":   dict,        # {x, y, z} from scene_state
                "occupied_zones":    list,        # rough zone labels still occupied
                "freed_zones":       list         # zones freed after removals
            },
            "layout_rationale": str               # for Verification Agent
        }

    """

    def __init__(self, assets_base_path: str = None):
        genai.configure(api_key='Your-API-Key')
        self.model = genai.GenerativeModel('gemini-2.5-flash')

        if assets_base_path is None:
            repo_root = Path(__file__).resolve().parents[2]
            self.assets_base = repo_root / "webXR" / "assets"
        else:
            self.assets_base = Path(assets_base_path)
        
        print("🧠 Memory module initialized")
        print(f"   Assets base: {self.assets_base}")

    # =========================================================================
    # Public API  —  these methods are called by Orchestrator
    # =========================================================================

    def process(self,
                parsed_command: Dict,
                scene_state: Dict) -> Optional[Dict]:
        """
        Main entry point.  Called from the Orchestrator memory node.

        Args:
            parsed_command : enriched output from LanguageAgent.parse_prompt()
            scene_state    : current scene state from database
                             database.scene_data  (ground-truth JSON)
        Returns:
            memory_context dict (for both asset and scene agents) or None on failure.
        """

        print("\n🧠 MemoryAgent: starting complex-route reasoning...")
        original_prompt = parsed_command.get("original_prompt", "")
        intent_summary  = parsed_command.get("intent_summary", original_prompt)