import sys
from pathlib import Path
import google.generativeai as genai
import json
from typing import Dict, Optional, List

import os
from dotenv import load_dotenv
load_dotenv()
api_key = os.getenv("API_KEY")

# Add backEnd directory to Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import Database

class CodeAgent:
    """
    Code Agent handles:
    - Translating spatial transformations into database operations
    - Executing position/rotation updates
    - Maintaining scene consistency
    """

    def __init__(self, database: Database):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-2.5-flash-lite')
        self.database = database

    def execute_transformation(self, transformation: Dict) -> Dict:
        """
        Execute spatial transformation(s) on the scene.
        Automatically handles both single-object and multi-object formats.

        Args:
            transformation: Dict with either:
            - Single: {"object_id": str, "position": {...}, "rotation": {...}, "action": str}
            - Multi: {"objects": [{...}, {...}], "reasoning": str}

        Returns:
            {
                "success": bool,
                "results": list,
                "message": str
            }
        """
        if not transformation:
            return {
                "success": False,
                "results": [],
                "message": "No transformation provided"
            }

        # Normalize to list format (works for both single and multi)
        if 'objects' in transformation:
            # Already multi-object format
            objects_to_process = transformation['objects']
            reasoning = transformation.get('reasoning', 'N/A')
        else:
            # Single-object format - wrap in list
            objects_to_process = [transformation]
            reasoning = 'Single object transformation'

        # Process all objects
        print(f"\nCode Agent executing:")
        print(f"   Objects: {len(objects_to_process)}")
        if len(objects_to_process) > 1:
            print(f"   Reasoning: {reasoning}\n")
        
        results = []
        
        for i, obj_transform in enumerate(objects_to_process, 1):
            object_id = obj_transform.get('object_id')
            position = obj_transform.get('position')
            rotation = obj_transform.get('rotation')
            action = obj_transform.get('action')

            if len(objects_to_process) > 1:
                print(f"   [{i}/{len(objects_to_process)}] {object_id}...")
            else:
                print(f"   Object ID: {object_id}")
                print(f"   Action: {action}")

            try:
                # Verify object exists
                obj = self.database.get_object_by_id(object_id)
                if not obj:
                    result = {
                        "success": False,
                        "object_id": object_id,
                        "action": action,
                        "message": f"Object {object_id} not found"
                    }
                    results.append(result)
                    print(f"Object not found")
                    continue
                
                success = True

                # Execute position update
                if action in ['move', 'place'] and position:
                    success = self.database.update_object_position(object_id, position)
                    if success:
                        print(f"Position: ({position['x']:.2f}, {position['y']:.2f}, {position['z']:.2f})")
                
                # Execute rotation update
                if action in ['rotate', 'move', 'place'] and rotation:
                    success = success and self.database.update_object_rotation(object_id, rotation)
                    if success:
                        print(f"Rotation: ({rotation['x']:.2f}, {rotation['y']:.2f}, {rotation['z']:.2f})")

                result = {
                    "success": success,
                    "object_id": object_id,
                    "action": action,
                    "message": "Success" if success else "Update failed"
                }
                results.append(result)

            except Exception as e:
                print(f"Error: {e}")
                result = {
                    "success": False,
                    "object_id": object_id,
                    "action": action,
                    "message": f"Error: {str(e)}"
                }
                results.append(result)

        # Return unified format
        all_success = all(r["success"] for r in results)
        success_count = sum(1 for r in results if r["success"])
        
        return {
            "success": all_success,
            "results": results,
            "message": f"{success_count}/{len(results)} transformations succeeded"
        }


# Test
if __name__ == "__main__":
    db = Database()
    agent = CodeAgent(db)
    
    # Test single-object
    print("\nTest 1: Single object")
    test_single = {
        'object_id': 'chair_01',
        'position': {'x': 0.5, 'y': -1.0, 'z': -1.3},
        'rotation': {'x': 0, 'y': -1.5707963267948966, 'z': 0},
        'action': 'move'
    }
    result = agent.execute_transformation(test_single)
    print(f"Result: {result}\n")
    
    # Test multi-object
    print("\nTest 2: Multiple objects")
    test_multi = {
        'objects': [
            {
                'object_id': 'chair_01',
                'position': {'x': 0.4, 'y': -1.0, 'z': -2.3},
                'rotation': {'x': 0, 'y': -1.5707963267948966, 'z': 0},
                'action': 'move'
            },
            {
                'object_id': 'stool_01',
                'position': {'x': -0.2, 'y': -1.0, 'z': -1.0},
                'rotation': {'x': 0, 'y': 0, 'z': 0},
                'action': 'move'
            }
        ],
        'reasoning': 'Separating chair and stool'
    }
    result = agent.execute_transformation(test_multi)
    print(f"Result: {result}\n")

            

