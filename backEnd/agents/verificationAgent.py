import json
import os
from typing import Dict, Optional
import sys
from pathlib import Path
import google.generativeai as genai
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import Database


class VerificationAgent:
    def __init__(self, database):
        # Initialize the database
        self.database = database
        genai.configure(api_key='API-Key')
        self.model = genai.GenerativeModel('gemini-2.5-flash-lite')

    def get_object_state (self, object_name: str) -> Optional[Dict]:
        """
        Get current state of an object by name
        
        Args:
            object_name: Name of the object (e.g., "chair")
        
        Returns:
            Object dict with id, position, rotation, or None if not found
        """

        objects = self.database.get_objects_by_name(object_name)

        if not objects:
            print(f"  🔍 No exact match for '{object_name}', trying semantic search...")
            objects = self.semantic_search(object_name)
            
            if not objects:
                print(f"  ⚠️ No objects found matching '{object_name}'")
                return None
        
        if len(objects) > 1:
            print(f" ✓ Found {len(objects)} objects with name '{object_name}'")

        return [
            {
                'id': obj['id'],
                'name': obj['name'],
                'position': obj['position'],
                'rotation': obj['rotation']
            }
            for obj in objects
        ]

    def semantic_search(self, query: str) -> list:
        """
        Use LLM to find objects matching semantic query.
        Single compact method for all vague queries.
        """
        all_objects = self.database.scene_data.get('objects', [])

        if not all_objects:
            return []
        
        # Ask LLM which objects match
        object_list = "\n".join([f"- {obj['id']}: {obj['name']}" for obj in all_objects])
            
        prompt = f"""Query: "{query}"
            Available objects: {object_list}

            Which object IDs match? Consider semantic meaning (e.g., "furniture" = chairs, tables, sofas).
            Return JSON array of IDs, e.g., ["chair_01", "table_01"] or [] if none match.
            """
        
        try:
            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=200,
                    response_mime_type="application/json"
                )
            )
            
            matching_ids = json.loads(response.text)
            
            result = [obj for obj in all_objects if obj['id'] in matching_ids]
            
            if result:
                print(f"  ✅ Semantic search found {len(result)} objects")
            
            return result
        
        except Exception as e:
            print(f"  ❌ Semantic search error: {e}")
            return []

    def validate_transformation (self, transformation: Dict) -> bool:
        """
        Validate a transformation before execution.
        
        Args:
            transformation: Transformation dict to validate
            
        Returns:
            True if valid, False otherwise
        """
        if not transformation:
            return False
        
        required_fields = ['object_id', 'position', 'rotation', 'action']
        for field in required_fields:
            if field not in transformation:
                print(f"⚠️ Missing required field: {field}")
                return False
        
        # Validate position format
        position = transformation['position']
        if not all(k in position for k in ['x', 'y', 'z']):
            print(f"⚠️ Invalid position format")
            return False
        
        # Validate rotation format
        rotation = transformation['rotation']
        if not all(k in rotation for k in ['x', 'y', 'z']):
            print(f"⚠️ Invalid rotation format")
            return False
        
        return True

# Test
if __name__ == "__main__":
    from database import Database
    
    # Initialize database
    db = Database()
    
    # Initialize code agent
    agent = VerificationAgent(db)
    
    # Get object state
    states = agent.get_object_state("chair")
    print(f"Current state: {states}")
    
    first_state = states[0]
    id = first_state.get("id")
    name = first_state.get("name")
    position = first_state.get("position")
    rotation = first_state.get("rotation")
    # Execute update
    if states:
        updates = {
            'position': {'x': 0.5, 'y': 0, 'z': -1.5}
        }
    
    test_transformation = {
        'object_id': id,
        'name': name,
        'position': position,
        'rotation': {'x': 0, 'y': -1.5707963267948966, 'z': 0},
        'action': 'move'
    }

    # Test validation
    result = agent.validate_transformation(test_transformation)
    print(result)
