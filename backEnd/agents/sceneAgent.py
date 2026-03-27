import google.generativeai as genai
import json
import os
import math
import re
from typing import Dict, List, Optional, Tuple


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
        genai.configure(api_key='AIzaSyC1oTPSi3cjstRY3FCGr1byEOnOnWxWpUE')
        self.model = genai.GenerativeModel('gemini-2.5-flash-lite')
        self.use_llm_reasoning = use_llm_reasoning

    def calculate_spatial_transformation(self,
                                         parsed_command: Dict,
                                         scene_state: Dict,
                                         user_position: Dict = None,
                                         feedback: Optional[Dict] = None,
                                         new_objects_to_position: Optional[List[Dict]] = None 
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
        
        print(f"\n🧠 Scene Agent processing:")
        print(f"   Original Prompt: '{parsed_command.get('original_prompt', 'N/A')}'")
        print(f"   Command Type: {parsed_command.get('command_type', 'N/A')}")
        print(f"   Objects: {parsed_command.get('involved_objects', [])}")
        print(f"   Spatial Concepts: {parsed_command.get('spatial_concepts', [])}")
        
        if feedback:
            print(f"   🔄 Iteration with feedback: {feedback.get('suggestion', 'N/A')}")

        return self._llm_spatial_reasoning(
            parsed_command,
            scene_state,
            user_position,
            feedback,
            new_objects_to_position
        )
    
    def _llm_spatial_reasoning(self,
                               parsed_command: Dict,
                               scene_state: Dict,
                               user_position: Dict,
                               feedback: Optional[Dict] = None,
                               new_objects_to_position: Optional[List[Dict]] = None
                               ) -> Dict:

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
            new_objects_info = [
                {
                    'id': obj['id'],
                    'name': obj['name'],
                    'category': obj.get('category', 'unknown'),
                    'properties': obj.get('properties', {})
                }
                for obj in new_objects_to_position
            ]
            new_objects_section = f"""
            
            NEWLY CREATED OBJECTS (need position/rotation):
            {json.dumps(new_objects_info, indent=2)}
            
            IMPORTANT: These objects have been created but have no position/rotation yet.
            You MUST provide position and rotation for ALL of these objects.
            """

        # Extract key information from enriched Language Agent output
        original_prompt = parsed_command.get('original_prompt', '')
        command_type = parsed_command.get('command_type', 'POS/ROTATE')
        involved_objects = parsed_command.get('involved_objects', [])
        spatial_concepts = parsed_command.get('spatial_concepts', [])
        intent_summary = parsed_command.get('intent_summary', original_prompt)
        action_hints = parsed_command.get('action_hints', {})
        
        # Build feedback context if this is an iteration
        feedback_context = ""
        if feedback:
            feedback_context = f"""
            
            FEEDBACK FROM PREVIOUS ATTEMPT:
            Previous placement: {json.dumps(feedback.get('previous_attempt', {}), indent=2)}
            Collision with: {feedback.get('collision_with', [])}
            Suggestion: {feedback.get('suggestion', 'Try alternative placement')}
            
            IMPORTANT: Use this feedback to adjust your spatial reasoning and avoid the same collision.
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
            - All objects should be on the floor (y = -1.0)
            
            YOU MUST return multi-object format with ALL objects:
            {{
            "objects": [
                {{"object_id": "chair_03", "position": {{"x": ..., "y": -1.0, "z": ...}}, "rotation": {{...}}, "action": "place"}},
                {{"object_id": "chair_04", "position": {{"x": ..., "y": -1.0, "z": ...}}, "rotation": {{...}}, "action": "place"}},
                {{"object_id": "table_02", "position": {{"x": ..., "y": -1.0, "z": ...}}, "rotation": {{...}}, "action": "place"}}
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
            "position": {{"x": ..., "y": -1.0, "z": ...}},
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
    - Y-axis: Down (-) to Up (+), floor is at y=-1
    - Z-axis: Forward (-) to Backward (+)
    - Rotations in radians
    - User typically faces -Z direction (forward)

    SPATIAL REASONING RULES:
    1. "next to" = 0.5 meters offset horizontally
    2. "in front of" = offset in -Z direction relative to reference
    3. "behind" = offset in +Z direction
    4. "on" = place on top (y-offset by ~0.3m above surface)
    5. "between X and Y" = midpoint between two objects
    6. "forward/backward/left/right" relative to USER's facing direction
    7. For rotation: convert degrees to radians (90° = 1.5708 radians)
    8. For multiple objects of the same type: arrange them with spacing (0.5-0.8m apart)
    9. For aesthetic goals like "cozy" or "spacious", consider spacing and orientation
    10. ALL objects must be placed on the floor (y = -1.0)
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
        "position": {{"x": 0.5, "y": -1.0, "z": -1.5}},
        "rotation": {{"x": 0, "y": 0, "z": 0}},
        "action": "move",
        "reasoning": "Moved chair closer"
    }}

    For SINGLE OBJECT (with rotation):
    {{
        "object_id": "chair_01",
        "position": {{"x": 0.5, "y": -1.0, "z": -1.5}},
        "rotation": {{"x": 0, "y": 1.57, "z": 0}},
        "action": "place",
        "reasoning": "Placed and rotated chair to face table"
    }}

    For MULTIPLE OBJECTS:
    {{
        "objects": [
            {{
                "object_id": "chair_01",
                "position": {{"x": -0.4, "y": -1.0, "z": -2.0}},
                "rotation": {{"x": 0, "y": 0, "z": 0}},
                "action": "place"
            }},
            {{
                "object_id": "chair_02",
                "position": {{"x": 0.4, "y": -1.0, "z": -2.0}},
                "rotation": {{"x": 0, "y": 0, "z": 0}},
                "action": "place"
            }}
        ],
        "reasoning": "Arranged in a row facing user"
    }}
    """
        
        # Call LLM
        try:
            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.3,
                    max_output_tokens=2048,
                    response_mime_type="application/json"
                )
            )
            
            result = json.loads(response.text)
            
            # Validate
            if not self._validate_transformation(result):
                print("⚠️ LLM output validation failed, using fallback")
                return self._fallback_calculation(parsed_command, scene_state, user_position, new_objects_to_position)
            
            print(f"   ✅ Spatial reasoning complete")
            if 'objects' in result:
                print(f"      Positioned {len(result['objects'])} objects")
            if 'reasoning' in result:
                print(f"      💭 Reasoning: {result['reasoning']}")
            
            return result
        
        except Exception as e:
            print(f"❌ LLM spatial reasoning error: {e}")
            return self._fallback_calculation(parsed_command, scene_state, user_position, new_objects_to_position)
        
        except json.JSONDecodeError as e:
            print(f"❌ LLM JSON parsing error: {e}")
            print(f"Raw response: {response.text if 'response' in locals() else 'No response'}")
            return self._fallback_calculation(parsed_command, scene_state, user_position)
        
        except Exception as e:
            print(f"❌ LLM spatial reasoning error: {e}")
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
        print("⚠️ Using fallback calculation")
        
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
                        'y': -1.0,  # Floor level
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
            print(f"❌ Target object '{target_name}' not found")
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
            print(f"\n📦 Final Result:")
            print(json.dumps(result, indent=2))
        else:
            print("\n❌ Failed to calculate transformation")