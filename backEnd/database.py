''' _____________________________________________________________
    Database module for BackEnd Agents.                        
    Loads data from the sceneData.json in the WebXR folder.
    This module contains two different functionalities:
        1. Grab data from the .json file in the WebXR frontEnd;
        2. Update change made by the MAS backEnd to the .json file.
    _____________________________________________________________
'''

import json
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import asyncio
import copy
import re

class Database:
    """ _____________________________________________________________
        Python interface to scene data (loads from JSON)
        Agents use this to query and modify scene state
        _____________________________________________________________
    """

    def __init__(self, app=None):
        """
        Initialize data exchange 
        Args:
            json_path: Path to sceneData.json file
            app: FastAPI app instance (optional, for WebSocket broadcasting)
        """
        repo_root = Path(__file__).resolve().parents[1]
        self.json_path = repo_root / "middleware" / "sceneData.json"
        self.added_object_ids = set()
        self.id_counters = {}
        self.app = app
        self.load()

        self._initialize_id_counters()


    def _initialize_id_counters(self):
        """
        Initialize ID counters based on existing objects in the scene.
        This ensures new objects get sequential IDs.
        
        Example:
            If scene has chair_01, chair_02, chair_03
            Then id_counters['chair'] = 3
            Next chair will be chair_04
        """
        import re
        self.id_counters.clear()  # Reset counters
        
        for obj in self.objects:
            # Extract base name and number from ID (e.g., "chair_03" → "chair", 3)
            match = re.match(r'(.+?)_(\d+)$', obj['id'])
            if match:
                base_name = match.group(1)
                num = int(match.group(2))
                # Keep track of highest number for each base name
                self.id_counters[base_name] = max(self.id_counters.get(base_name, 0), num)
        
        print(f"ID Counters initialized: {self.id_counters}")

    """ _____________________________________________________________
        Basic functions: Load & Save
        _____________________________________________________________
    """

    

    def load(self):
        # load scene from JSON file
        try:
            with open (self.json_path, 'r') as f:
                self.scene_data = json.load(f)

            # For fast demo we just consider load object data right now
            self.objects = self.scene_data['objects']
            # for testing Database().load()
            # return self.objects

            # Add walls to searchable objects (read-only)
            if 'structure' in self.scene_data and 'walls' in self.scene_data['structure']:
                for wall in self.scene_data['structure']['walls']:
                    # Mark as non-movable structural element
                    wall['name'] = wall.get('type', 'wall')  # "wall"
                    wall['category'] = 'structure'
                    wall['properties'] = {'movable': False, 'structural': True}
                    self.objects.append(wall)
            
            # add floor
            if 'structure' in self.scene_data and 'floor' in self.scene_data['structure']:
                floor = self.scene_data['structure']['floor']
                floor['name'] = floor.get('type', 'floor')  # "floor"
                floor['category'] = 'structure'
                floor['properties'] = {'movable': False, 'structural': True}
                self.objects.append(floor)
        
        except FileNotFoundError:
            print(f"Scene file not found: {self.json_path}")
            raise
        except json.JSONDecodeError as e:
            print(f"Invalid JSON in scene file: {e}")
            raise
    
    '''
    def save(self, filepath: str = None):
        """
        Save current scene state back to JSON file
        
        Args:
            filepath: Optional different path to save to
        """
        save_path = filepath or self.json_path
        
        try:
            with open(save_path, 'w') as f:
                json.dump(self.scene_data, f, indent=2)
            print(f"Scene saved to {save_path}")
        except Exception as e:
            print(f"Failed to save scene: {e}")
            raise
    '''
    
    """ _____________________________________________________________
        Query Methods: basic functionalities of a database
        Extraction of data by id, name, category, etc.
        _____________________________________________________________
    """
    def get_object_by_id(self, object_id: str):
        """
        Fine the object by thier ID

        Args:
            object_id: Object ID (e.g., "table_01")
        Returns:
            Object dict or None if not found
        """
        for obj in self.objects:
            if obj['id'] == object_id:
                return obj
        return None
    
    def get_objects_by_name(self, name: str):
        """
        Find objects by name (partial match)

        Args:
            name: Object name to search for (e.g., "chair")
        Returns:
            List of matching objects
        """
        return [obj for obj in self.objects 
                if name.lower() in obj['name'].lower()]

    def get_objects_by_category(self, category: str) -> List[Dict]:
        """
        Find objects by category

        Args:
            category: Category name (e.g., "furniture")

        Returns:
        List of objects in that category
        """
        return [obj for obj in self.objects 
                if obj.get('category', '').lower() == category.lower()]

    def get_objects_near_position(self, 
                                  position: Dict[str, float], 
                                  radius: float = 1.0
                                  ) -> List[Dict]:
        """
        Find objects within radius of position

        Args:
            position: Dict with {x, y, z} keys
            radius: search radius in meters
        Returns:
            List of objects within radius
        """
        # Initialize nearby object list
        nearby = []
        for obj in self.objects:
            pos = obj['position']
            # calculate Euclidean distance (x,y,z)
            distance = (
                (pos['x'] - position['x'])**2 + 
                (pos['y'] - position['y'])**2 +
                (pos['z'] - position['z'])**2
            )**0.5

            if distance <= radius:
                nearby.append(obj)
        
        return nearby
    
    def get_movable_objects(self) -> List[Dict]:
        """
        Get all movable objects

        Returns:
            List of objects which are movable (movable=True)
        """

        return [obj for obj in self.objects 
                if obj.get('properties', {}).get('movable', False)]

    def get_spatial_relationships(self, object_id: str) -> Optional[Dict]:
        obj = self.get_object_by_id(object_id)
        return obj.get('spatialRelations') if obj else None
    
    # def get_scene_bounds (self) -> Dict:
        """
        Get scene boundaries
   
        Returns:
            Dict with min and max bounds
        """
        # return self.metadata['bounds']
    
    """ _____________________________________________________________
        Modification methods: Update scene
        _____________________________________________________________
    """

    '''
    def _broadcast_update(self, update_type: str, data: Dict):
        """Broadcast update via FastAPI WebSocket if available"""
        if self.app and hasattr(self.app.state, 'manager'):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(
                self.app.state.broadcast(update_type, data)
            )
            loop.close()
    '''
    def _broadcast_update(self, update_type: str, data: Dict):
        """Broadcast update via FastAPI WebSocket if available"""
        if not self.app or not hasattr(self.app.state, 'manager'):
            return
        
        try:
            # Try to get the running event loop (FastAPI context)
            loop = asyncio.get_running_loop()
            
            # We're in an async context - create a task
            loop.create_task(
                self.app.state.broadcast(update_type, data)
            )
            
        except RuntimeError:
            # No running loop - we're in sync context (terminal)
            # Create a temporary event loop
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    self.app.state.broadcast(update_type, data)
                )
            except Exception as e:
                print(f"⚠️ Broadcast failed in sync context: {e}")
            finally:
                loop.close()
                asyncio.set_event_loop(None)

    def update_object_position(self, 
                               object_id: str, 
                               new_position: Dict[str, float]
                               ) -> bool:
        """
        Update object position in memory

        Args:
            object_id: Object ID
            new_position: New position w/ updated rotation {x, y, z}
            (in radians)

        Returns:
            True if successful, false if cannot find the object
        """
        obj = self.get_object_by_id(object_id)
        if obj:
            obj['position'].update(new_position)
            print(f"Updated {obj['name']} position to {new_position}")

            # Broadcast via WebSocket
            self._broadcast_update('object_position_updated', {
                'objectId': object_id,
                'position': new_position,
                'name': obj['name']
            })
            return True
        return False
        
    def update_object_rotation(self, 
                               object_id: str, 
                               new_rotation: Dict[str, float]
                               ) -> bool:
        obj = self.get_object_by_id(object_id)
        if obj:
            obj['rotation'].update(new_rotation)
            print(f"Updated {obj['name']} rotation to {new_rotation}")

            # Broadcast via WebSocket
            self._broadcast_update('object_rotation_updated', {
                'objectId': object_id,
                'rotation': new_rotation,
                'name': obj['name']
            })
            return True
        return False
        
    def add_object(self,
                   object_data: Dict
                   ) -> str:
        """
        Add new object to the sceneData.json

        Args:
            object_data: complete object definition diction data structure

        Returns:
            object ID of the newly added object
        """

        # ensure that the object has an id
        if 'id' not in object_data:
            # automatically generate an id from base_name
            base_name = object_data.get('name', 'object').replace(' ', '_')
            current_count = self.id_counters.get(base_name, 0)
            next_id = current_count + 1
            self.id_counters[base_name] = next_id
            object_data['id'] = f"{base_name}_{next_id:02d}"

        self.objects.append(object_data)
        self.added_object_ids.add(object_data['id'])
        print(f"Added new object: {object_data['name']} ({object_data['id']})")
        return object_data['id']

    def remove_object(self, object_id: str) -> bool:
        """
        Remove object from scene

        Args:
            object_id: Object ID to remove

        Returns:
            True if succeed, flase if not
        """
        for i, obj in enumerate(self.objects):
            if obj['id'] == object_id:
                removed = self.objects.pop(i)
                print(f"Removed object: {removed['name']} ({object_id})")
                return True
        return False
    
    def clear_added_objects(self):
        """
        Remove all objects that were added during this session.
        Keeps original objects from sceneData.json intact.
        """
        if not self.added_object_ids:
            print("No added objects to clear")
            return 0
        
        removed_count = 0
        for object_id in list(self.added_object_ids):
            if self.remove_object(object_id):
                removed_count += 1
        
        self.added_object_ids.clear()
        self._initialize_id_counters()
        print(f"Cleared {removed_count} added object(s)")
        return removed_count
    
    """ _____________________________________________________________
        Spatial Calculation Methods (measure spatial relationships)
        _____________________________________________________________
    """
    def calculate_distance(self, 
                           pos1: Dict[str, float], 
                           pos2: Dict[str, float]) -> float:
        """
        Calculate Euclidean distance between two positions
        
        Args:
            pos1: First position {x, y, z}
            pos2: Second position {x, y, z}
        
        Returns:
            Distance in meters
        """
        euclidean_distance_btw_2pos = (
            (pos1['x'] - pos2['x'])**2 +
            (pos1['y'] - pos2['y'])**2 +
            (pos1['z'] - pos2['z'])**2 
            )**0.5
        return  euclidean_distance_btw_2pos
        
    
    """ _____________________________________________________________
        Data visualization
        _____________________________________________________________
    """

    def get_statistics(self) -> Dict:
        """
        Get statistics about the scene
        
        Returns:
            Dict with scene statistics
        """
        return {
            'total_objects': len(self.objects),
            'movable_objects': len(self.get_movable_objects()),
            'categories': list(set(obj.get('category', 'unknown') for obj in self.objects))
        }
    
    def print_statistics(self):
        """Print scene statistics"""
        stats = self.get_statistics()
        print(f"{'='*50}")
        print(f"Total Objects: {stats['total_objects']}")
        print(f"Movable Objects: {stats['movable_objects']}")
        print(f"Categories: {', '.join(stats['categories'])}")


""" _____________________________________________________________
    Test cases
    _____________________________________________________________
"""
db = Database()

# Test load data
# print(db.load()) 
# Succeed

# Test data visualization
# db.print_statistics()
"""     
        Test result: PASS
    ==================================================
        Total Objects: 6
        Movable Objects: 6
        Categories: vehicle, furniture
""" 

# Test for query methods: PASS
''' Test grab object by name
tables = db.get_objects_by_name('table')
print(f"Tables found: {len(tables)}")
for table in tables:
    print(f"  - {table['name']} at {table['position']}")

chairs = db.get_objects_by_name('chair')
print(f"Chairs found: {len(chairs)}")
for chair in chairs:
    print(f"  - {chair['name']} at {chair['position']}")
'''

# find object near table: PASS
'''
if tables:
    table_pos = tables[0]['position']
    nearby = db.get_objects_near_position(table_pos, radius = 1.0)
    print(f"\nObjects within 1m of table: {len(nearby)}")
    for obj in nearby:
        if obj['id'] != tables[0]['id']:
            distance = db.calculate_distance(table_pos, obj['position'])
            print(f"  - {obj['name']}: {distance:.2f}m away")
'''

# Test get extract movable ojects: PASS
'''
movable = db.get_movable_objects()
print(f"\nMovable objects: {len(movable)}")
for obj in movable:
    print(f"  - {obj['name']}")
'''

# test update object position & rotation
'''
db.update_object_rotation("chair_01", {"x": 0, "y": -1.5707963267948966, "z": 0})
db.update_object_position("chair_01", {"x": 0.4, "y": -1, "z": 1.5})
db.save()
'''