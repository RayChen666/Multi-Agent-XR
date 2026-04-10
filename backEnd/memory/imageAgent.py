import google.generativeai as genai
import json
import base64
import os
from pathlib import Path
from typing import Dict, List, Optional, Any


REFERENCE_IMAGE_LIBRARY = {
    "office":       "reference_layouts/office.jpg",
    "bedroom":      "reference_layouts/bedroom.jpg",
}

class ImageAgent:
    """
    Agent responsible for processing and understanding images.
    Uses Google Gemini API for image analysis and captioning.   

    This includes 2,3,4.A from original memory module design:
    2. Load a REFERENCE IMAGE for that room type (semantic layout grounding).
    3. Read the CURRENT SCENE JSON (ground-truth object state).
    4. Run a two-stage LLM reasoning call:
        Stage A — semantic layout: 
            extract 
            - anchor, 
            - zones, 
            - cluster relations from the reference image.

    Called by MemoryAgent.process() — not by the Orchestrator directly.
    """

    def __init__(self, assets_base_path: str = None):
        """
        Args:
            assets_base_path: Absolute path to the webXR/assets directory.
                              Defaults to ../../webXR/assets relative to this file.
        """
        genai.configure(api_key='API_KEY_HERE')
        self.model = genai.GenerativeModel('gemini-1.5-flash-lite')

        if assets_base_path is None:
            repo_root = Path(__file__).resolve().parents[2]
            self.assets_base = repo_root / "webXR" / "assets"
        else:
            self.assets_base = Path(assets_base_path)
        
        print("🧠 Memory module initialized")
        print(f"   Assets base: {self.assets_base}")

    # ------------------------------
    # Public API
    # ------------------------------
    def extract_semantic_layout(self, room_type: str) -> Optional[Dict]:
        """
        Called by MemoryAgent.
        Args:
            room_type: Target room type string, e.g. "office", "bedroom"
        Returns:
            semantic_layout dict or None on complete failure.

        """
        print(f"\n🖼️  ImageAgent: extracting semantic layout for '{room_type}'...")

        # Step 2: Load reference image
        image_part = self._load_reference_image(room_type)

        if image_part:
            print(f"   ✅  Reference image loaded for room type '{room_type}'")

        else:
            print(f"   ⚠️  No reference image found — using parametric LLM knowledge only")


        # Step 4A: semantic layout reasoning
        semantic_layout = self._reason_semantic_layout(room_type, image_part)

        if semantic_layout:
            print(f"   ✅ Semantic layout extracted: "
                  f"anchor='{semantic_layout.get('anchor_object')}', "
                  f"function='{semantic_layout.get('room_function')}'")
        else:
            print(f"   ❌ Semantic layout extraction failed")

        return semantic_layout
    
    # ------------------------------
    # Step 2: Load reference image
    # ------------------------------
    def _load_reference_image(self, room_type: str) -> Optional[bytes]:
        """
        Load the canonical reference image for the target room type.
 
        Returns:
            Gemini-compatible inline_data part dict, or None if not found.
        """
        relative_path = REFERENCE_IMAGE_LIBRARY.get(room_type)

        if not relative_path:
            return None
        
        image_path = self.assets_base / relative_path
        if not image_path.exists():
            print(f"   ⚠️  Reference image not found at: {image_path}")
            return None
        

        try:
            with open(image_path, "rb") as f:
                image_bytes = f.read()
            
            ext = image_path.suffix.lower()
            mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                        ".png": "image/png",  ".webp": "image/webp"}
            mime_type = mime_map.get(ext, "image/jpeg")

            return {
                "inline_data": {
                    "mime_type": mime_type,
                    "data": base64.b64encode(image_bytes).decode("utf-8")
                }
            }
        except Exception as e:
            print(f"   ⚠️  Failed to load reference image: {e}")
            return None
        
    
    # ------------------------------
    # Step 4A: Semantic layout reasoning
    # ------------------------------

    def _reason_semantic_layout(self,
                                room_type: str,
                                image_part: Optional[Dict]) -> Optional[Dict]:
        """
        Stage A: extract semantic spatial structure from reference image.
        If image_part is provided → multimodal call (image + text).
        If image_part is None    → text-only call using parametric knowledge.

        Returns:
            semantic_layout dict or None on failure.
        """
        image_context = (
            f"The reference image above shows a typical {room_type} layout. "
            f"Use it to ground your spatial analysis."
            if image_part else
            f"No reference image is available. Use your knowledge of typical "
            f"{room_type} layouts to infer the spatial structure."
        )

        text_prompt = f"""You are a semantic layout analyst for 3D interior spaces.

        Your task: extract the SPATIAL SEMANTIC STRUCTURE of a {room_type}. as a 
        GRAPH-BASED ADJACENCY LIST where:
            - NODES are the key objects and room boundaries (walls, corners)
            - EDGES are the spatial relationships between them

        {image_context}

        For each edge, encode:
            - "from"     : source object
            - "to"       : target object or boundary (e.g. "back_wall", "left_wall", "back_right_corner")
            - "relation" : one of [ close_to | next_to | at_corner | facing | on_surface_of | adjacent_to ]
            - "side"     : cardinal direction from source's perspective [ north | south | east | west ]
                           (omit if not applicable, e.g. at_corner)
            - "corner"   : corner label if relation is at_corner
                           (e.g. "back_right", "front_left")

        Also identify:
            - "anchor_object"    : the dominant furniture piece that anchors the room
            - "anchor_placement" : where the anchor sits relative to the room
            - "room_function"    : short phrase describing the primary activity
            - "layout_rationale" : one sentence explaining why this arrangement makes functional sense

        Output ONLY valid JSON — no markdown fences, no extra text:

        {{}}
        """