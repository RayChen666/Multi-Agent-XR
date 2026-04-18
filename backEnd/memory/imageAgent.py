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
        genai.configure(api_key='API')
        self.model = genai.GenerativeModel('gemini-3.1-pro-preview')

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

        # Step 4A-1: Enumerate all nodes first
        nodes = self._enumerate_nodes(room_type, image_part)

        if nodes:
            print(f"   ✅ Node enumeration complete: {nodes}")
        else:
            print(f"   ⚠️  Node enumeration failed — falling back to edge reasoning without node list")

        # Step 4A-2: Build edges from confirmed node list
        semantic_layout = self._reason_edges(room_type, image_part, nodes)

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
    # Step 4A-1: Node enumeration (Call 1)
    # ------------------------------
    def _enumerate_nodes(self,
                         room_type: str,
                         image_part: Optional[Dict]) -> Optional[List[str]]:
        """
        Call 1: Exhaustively list every distinct furniture object visible
        in the reference image (or known from parametric knowledge).

        Returns:
            List of object name strings, or None on failure.
        """
        image_context = (
            f"The reference image above shows a typical {room_type} layout. "
            f"Carefully examine EVERY object visible in the image."
            if image_part else
            f"No reference image is available. List the objects you would "
            f"typically expect in a {room_type}."
        )

        text_prompt = f"""You are a furniture inventory specialist for 3D interior spaces.

        Your task: list EVERY distinct furniture or architectural object visible 
        in this {room_type} floor plan. 

        {image_context}

        Rules:
        - Scan the ENTIRE image systematically: top-left → top-right → center → bottom-left → bottom-right.
        - Do NOT stop after identifying the primary layout cluster.
        - Include ALL objects, even secondary or peripheral ones.
        - Use simple lowercase names (e.g. "desk", "office_chair", "cabinet", "sofa").
        - Include structural elements: walls (back_wall, left_wall, right_wall, front_wall) 
          and corners (back_left_corner, back_right_corner, front_left_corner, front_right_corner).
        - Do NOT include doors or windows as furniture nodes.
        
        Output ONLY valid JSON — no markdown fences, no extra text:

        {{
            "nodes": ["<object>", "<object>", ...]
        }}
        """

        try:
            if image_part:
                contents = [image_part, {"text": text_prompt}]
            else:
                contents = text_prompt

            response = self.model.generate_content(
                contents,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=500,
                    response_mime_type="application/json"
                )
            )
            parsed = self._safe_parse_json(response.text, "node_enumeration")
            return parsed.get("nodes") if parsed else None

        except Exception as e:
            print(f"   ❌ Node enumeration LLM error: {e}")
            return None

    # ------------------------------
    # Step 4A-2: Edge reasoning (Call 2)
    # ------------------------------
    def _reason_edges(self,
                      room_type: str,
                      image_part: Optional[Dict],
                      nodes: Optional[List[str]]) -> Optional[Dict]:
        """
        Call 2: Build the full layout graph using the confirmed node list.
        If nodes is None, falls back to unconstrained single-pass extraction.

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

        orientation_convention = (
            "Orientation convention for this floor plan:\n"
            "  - NORTH (back_wall)  = TOP of the image\n"
            "  - SOUTH (front_wall) = BOTTOM of the image\n"
            "  - WEST  (left_wall)  = LEFT of the image\n"
            "  - EAST  (right_wall) = RIGHT of the image\n"
            "Apply this consistently for ALL objects before assigning any edges.\n"
        )

        node_constraint = (
            f"You MUST use exactly this node list (already verified from the image):\n"
            f"{json.dumps(nodes)}\n"
            f"Do not add or remove any furniture nodes."
            if nodes else
            f"Identify all furniture nodes yourself from the image."
        )

        text_prompt = f"""You are a semantic layout analyst for 3D interior spaces.

        Your task: build the SPATIAL SEMANTIC STRUCTURE of a {room_type} as a 
        GRAPH-BASED ADJACENCY LIST where:
            - NODES are the key objects and room boundaries (walls, corners)
            - EDGES are the spatial relationships between them

        {image_context}

        {orientation_convention}

        {node_constraint}

        For each edge, encode:
            - "from"     : source object
            - "to"       : target object or boundary (e.g. "back_wall", "left_wall", "back_right_corner")
            - "relation" : one of [ close_to | next_to | at_corner | facing | on_surface_of | adjacent_to ]
            - "side"     : cardinal direction from source's perspective [ north | south | east | west ]
                           (omit if not applicable, e.g. at_corner)
            - "corner"   : corner label if relation is at_corner
                           (e.g. "back_right", "front_left")

        Edge rules:
            - Each object must have AT LEAST one edge to a wall or corner.
            - Only encode the DOMINANT spatial relationship per object pair — 
              do not add redundant or contradictory edges for the same pair.
            - "facing" encodes which direction the object is oriented toward.
            - "side" is always from the SOURCE object's perspective.
            - Each corner can be claimed by AT MOST one object. - 
              If two objects compete for the same corner, assign it to the one physically closest.


        Also identify:
            - "anchor_object"    : the dominant furniture piece that anchors the room
            - "anchor_placement" : where the anchor sits relative to the room
                                   (e.g. against_longest_wall | center | corner | against_back_wall)

        Output ONLY valid JSON — no markdown fences, no extra text:

        {{
            "anchor_object":    "<dominant furniture piece>",
            "anchor_placement": "<placement>",
            "layout_graph": {{
                "nodes": ["<object>", "<object>", "back_wall", "left_wall", ...],
                "edges": [
                    {{
                        "from":     "<object>",
                        "to":       "<object or wall>",
                        "relation": "<relation>",
                        "side":     "<north|south|east|west>"
                    }},
                    {{
                        "from":     "<object>",
                        "to":       "<corner>",
                        "relation": "at_corner",
                        "corner":   "<back_right|front_left|back_left|front_right>"
                    }}
                ]
            }}
        }}

        Example for a living room:
        {{
            "anchor_object":    "sofa",
            "anchor_placement": "against_longest_wall",
            "layout_graph": {{
                "nodes": ["sofa", "coffee_table", "tv_stand", "lamp", "back_wall", "left_wall"],
                "edges": [
                    {{"from": "sofa",         "to": "back_wall",    "relation": "next_to",  "side": "north"}},
                    {{"from": "coffee_table", "to": "sofa",         "relation": "close_to", "side": "south"}},
                    {{"from": "tv_stand",     "to": "sofa",         "relation": "facing",   "side": "south"}},
                    {{"from": "lamp",         "to": "back_wall",    "relation": "at_corner", "corner": "back_right"}}
                ]
            }}
        }}
        Now build the layout graph for a {room_type}:"""

        try:
            if image_part:
                contents = [image_part, {"text": text_prompt}]
            else:
                contents = text_prompt

            response = self.model.generate_content(
                contents,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=1500,
                    response_mime_type="application/json"
                )
            )
            return self._safe_parse_json(response.text, "semantic_layout")

        except Exception as e:
            print(f"   ❌ Edge reasoning LLM error: {e}")
            return None

    # ------------------------------
    # Helper methods
    # ------------------------------
    def _safe_parse_json(self, text: str, label: str) -> Optional[Dict]:
        """Strip markdown fences and parse JSON safely."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start != -1 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError as e:
                    print(f"   ❌ JSON parse error in {label}: {e}")
            return None


# ---------------
# TEST
# ---------------
if __name__ == "__main__":
    agent = ImageAgent()

    for room in ["office"]:
        print(f"\n{'='*60}")
        print(f"TEST: extract_semantic_layout('{room}')")
        print("="*60)
        result = agent.extract_semantic_layout(room)
        print(json.dumps(result, indent=2))


        