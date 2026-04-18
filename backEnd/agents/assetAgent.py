import google.generativeai as genai
import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional, List
import uuid
import re


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from database import Database


class AssetAgent:
    """
    Asset Agent handles:
    - Creating new objects in the scene
    - Removing existing objects from the scene
    - Selecting appropriate assets from available library
    - Managing object lifecycle (creation/deletion)
    """

    def __init__(self, 
                 database: Database, 
                 assets_path: str = None):
        """
        Description: 
            The Asset agent is used to handle the insertion and deletion of 
            the items within the scene according to human prompt.
        
        Args:
            database: Database instance for scene management
            assets_path: Path to assets folder containing models and metadata
        """
        if assets_path is None:
            repo_root = Path(__file__).resolve().parents[2]
            self.assets_path = repo_root / "webXR" / "assets" / "gltf-glb-models"
        else:
            self.assets_path = Path(assets_path)

        genai.configure(api_key='API')
        self.model = genai.GenerativeModel('gemini-2.5-flash-lite')

        self.database = database
        self.pending_objects = []
        
        
        self.known_assets = self._load_known_assets()
        self.all_assets = self._scan_all_assets()

        print(f"📦 AssetAgent initialized")
        print(f"   Known assets (with metadata): {len(self.known_assets)}")
        print(f"   Total assets discovered: {len(self.all_assets)}")


    # Asset loading
    def _load_known_assets(self) -> Dict[str, Dict]:

        """
        Load assets that have metadata.json files.
        These assets have complete information and can be used immediately.
        
        Returns:
            Dict mapping asset names to their metadata
        """

        library = {}

        if not self.assets_path.exists():
            print(f"⚠️  Assets path not found: {self.assets_path}")
            return library
        
        # Scan all subdirectories
        for asset_dir in self.assets_path.iterdir():
            if not asset_dir.is_dir():
                continue
            
            # Look for metadata.json
            metadata_file = asset_dir / "metadata.json"
            if not metadata_file.exists():
                continue
            
            try:
                # Load metadata
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
                
                # Find the model file (.gltf or .glb)
                model_files = list(asset_dir.glob("*.gltf")) + list(asset_dir.glob("*.glb"))
                if not model_files:
                    print(f"⚠️  Skipping {asset_dir.name} - no model file found")
                    continue
                
                model_file = model_files[0]
                relative_path = str(model_file).split('webXR/')[-1]
                
                # Store asset with full metadata
                asset_name = metadata["name"]
                library[asset_name] = {
                    "name": asset_name,
                    "category": metadata["category"],
                    "subcategory": metadata["subcategory"],
                    "modelPath": str(relative_path),
                    "default_scale": metadata["default_scale"],
                    "y_offset": metadata.get("y_offset", 0.0),
                    "properties": metadata["properties"],
                    "typical_dimensions": metadata.get("typical_dimensions"),
                    "default_rotation": metadata.get("default_rotation"),
                    "has_metadata": True
                }
                
                for alias in metadata.get("aliases", []):
                    library[alias] = library[asset_name]
                
                print(f"   ✅ Loaded: {asset_name} ({metadata['category']}/{metadata['subcategory']})")
                
            except Exception as e:
                print(f"⚠️  Error loading metadata for {asset_dir.name}: {e}")
                continue
        
        return library
    
    def _scan_all_assets(self) -> Dict[str, Dict]:
        """
        Scan ALL assets in the filesystem, including those without metadata.
        This allows discovery of new assets that don't have metadata yet.
        
        Returns:
            Dict mapping asset names to basic info
        """
        all_assets = {}
        
        if not self.assets_path.exists():
            return all_assets
        
        for asset_dir in self.assets_path.iterdir():
            if not asset_dir.is_dir():
                continue
            
            model_files = list(asset_dir.glob("*.gltf")) + list(asset_dir.glob("*.glb"))
            if not model_files:
                continue
            
            model_file = model_files[0]
            asset_name = asset_dir.name.replace("_", " ")
            
            all_assets[asset_name] = {
                "name": asset_name,
                "modelPath": str(model_file),
                "folder": asset_dir.name,
                "has_metadata": asset_name in self.known_assets
            }
        
        return all_assets
    
    def _extract_object_quantities(self, prompt: str, involved_objects: List[str]) -> List[Dict]:
        """
        Extract quantity for each object type.
        
        Examples:
            "add 3 lamps and 2 chairs" → [{"object": "lamp", "quantity": 3}, {"object": "chair", "quantity": 2}]
            "add 2 chairs" → [{"object": "chair", "quantity": 2}]
            "add a chair and a table" → [{"object": "chair", "quantity": 1}, {"object": "table", "quantity": 1}]
        
        Returns:
            List of {object: str, quantity: int} dicts
        """
        
        result = []

        pattern = r'(\d+|one|two|three|four|five|six|seven|eight|nine|ten|a|an)\b'
        
        matches = re.findall(pattern, prompt.lower())
        
        word_to_num = {
            'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
            'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
            'a': 1, 'an': 1
        }
        quantities = []
        # Process matches
        for num_word in matches:
            if num_word.isdigit():
                quantities.append(int(num_word))
            else:
                 quantities.append(word_to_num.get(num_word, 1))
            
            # Check if this object word matches any involved_objects
            # Handle plurals: "lamps" → "lamp", "chairs" → "chair"
        for i, obj in enumerate(involved_objects):
            if i < len(quantities):
                quantity = quantities[i]
            else:
                quantity = 1  # Default if not enough numbers
            
            result.append({
                "object": obj,
                "quantity": quantity
            })
        
        return result
    

    def _find_best_match(self, object_name: str) -> Optional[str]:
        """
        Find the best matching asset for the given object name.
        First tries exact match, then uses LLM for semantic matching.
        
        Args:
            object_name: Name of object to find
            
        Returns:
            Asset name if found, None otherwise
        """
        # Try exact match first
        object_name_lower = object_name.lower()
        # Check known assets
        for asset_name in self.known_assets.keys():
            if asset_name.lower() == object_name_lower:
                return asset_name
        # Check all discovered assets
        for asset_name in self.all_assets.keys():
            if asset_name.lower() == object_name_lower:
                return asset_name
        # If no exact match, use LLM semantic matching
        print(f"   🔍 No exact match for '{object_name}', trying semantic search...")
        return self._llm_find_match(object_name)

    def _llm_find_match(self, object_name: str) -> Optional[str]:
        """
        Use LLM to find semantic match between user request and available assets.
        
        Args:
            object_name: User's requested object
            
        Returns:
            Best matching asset name or None
        """
        available_names = list(self.all_assets.keys())
        
        if not available_names:
            return None
        
        prompt = f"""User wants to add: "{object_name}"

                    Available assets:
                    {', '.join(available_names)}

                    Which asset best matches the user's request?
                    Return ONLY the asset name exactly as shown, or "NONE" if no good match exists.
                """
    
        try:
            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=50
                )
            )
            
            matched_name = response.text.strip()
            
            if matched_name == "NONE" or matched_name not in available_names:
                print(f"   ❌ LLM found no good match for '{object_name}'")
                return None
            
            print(f"   ✅ LLM matched '{object_name}' → '{matched_name}'")
            return matched_name
            
        except Exception as e:
            print(f"   ⚠️  LLM matching error: {e}")
            return None
    
    # ID generation
    def _generate_unique_id(self, object_name: str) -> str:
            """
            Generate unique ID following pattern: {name}_{number}
            
            Args:
                object_name: Base name for the object
                
            Returns:
                Unique ID like "chair_03"
            """
            # Get all existing objects with this name
            existing_objects = self.database.scene_data.get('objects', [])
            if hasattr(self.database, 'objects'):
                existing_objects = self.database.objects
            
            # Find objects with same base name
            base_name = object_name.replace(" ", "_").lower()
            matching_ids = [
                obj['id'] for obj in existing_objects 
                if obj['id'].startswith(f"{base_name}_")
            ]
            pending_ids = [
                obj['id'] for obj in self.pending_objects
                if obj['id'].startswith(f"{base_name}_")
            ]
            
            # Combine both lists
            all_ids = matching_ids + pending_ids
            
            # Extract numbers from matching IDs
            numbers = []
            for obj_id in all_ids:
                try:
                    # Extract number after last underscore
                    num_str = obj_id.split('_')[-1]
                    numbers.append(int(num_str))
                except (ValueError, IndexError):
                    continue
            
            # Generate next number
            next_num = max(numbers) + 1 if numbers else 1
            
            return f"{base_name}_{next_num:02d}"
    

    def create_object(self, object_name: str) -> Dict:
        """
        Create a new object with complete metadata but NO position/rotation.
        Position and rotation will be added by Scene Agent.
        
        Args:
            object_name: Name of object to create
            
        Returns:
            Complete object structure (except position/rotation)
        """
        print(f"\n🎨 AssetAgent creating object: '{object_name}'")
        
        matched_name = self._find_best_match(object_name)
        
        if not matched_name:
            raise ValueError(f"No asset found matching '{object_name}'")
        
        if matched_name not in self.known_assets:
            raise ValueError(
                f"Asset '{matched_name}' found but has no metadata.json. "
                f"Please create metadata file for this asset."
            )
        new_object = self._create_from_known(matched_name)
        self.pending_objects.append(new_object)
    
        return new_object
        
    def _create_from_known(self, asset_name: str) -> Dict:
        """
        Create object from asset with metadata.json
        
        Args:
            asset_name: Name of asset (must be in known_assets)
            
        Returns:
            Complete object structure
        """
        template = self.known_assets[asset_name]
        canonical_name = template["name"]
        new_id = self._generate_unique_id(canonical_name)
        
        print(f"   ✅ Using known asset: {canonical_name}")
        if asset_name != canonical_name:
            print(f"      (matched via alias: '{asset_name}')")
        print(f"   Generated ID: {new_id}")
        print(f"   Category: {template['category']}/{template['subcategory']}")
        
        return {
            "id": new_id,
            "name": canonical_name,
            "category": template["category"],
            "subcategory": template["subcategory"],
            "modelPath": template["modelPath"],
            "position": None,  # Scene Agent will set
            "rotation": None,  # Scene Agent will set
            "scale": template["default_scale"].copy(),
            "y_offset": template.get("y_offset", 0.0),
            "boundingBox": None,  # Scene Agent will calculate
            "properties": template["properties"].copy(),
            "spatialRelations": {
                "on": "floor_01",  # Default
                "near": []
            }
        }
        
    def process_command(self, parsed_command: Dict) -> Dict:
        """
        Main entry point from orchestrator.
        Handles both ADD command.
        
        Args:
            parsed_command: Parsed command from Language Agent containing:
                - action_hints: {"primary_action": "add" | "delete"}
                - involved_objects: [object_name]
                
        Returns:
            Dict with action results:
            - For ADD: {"action": "add", "new_object": {...}, "needs_positioning": True}
        """
        action = parsed_command.get("action_hints", {}).get("primary_action", "").lower()
        involved_objects = parsed_command.get("involved_objects", [])
        
        if not involved_objects:
            return {
                "action": "none",
                "success": False,
                "message": "No objects specified"
            }
        
        if action == "add":
            try:
                self.pending_objects = []
                original_prompt = parsed_command.get("original_prompt", "")
                object_quantities = self._extract_object_quantities(original_prompt, involved_objects)
                print(f"   📋 Parsed quantities: {object_quantities}")
                new_objects = []

                for pair in object_quantities:
                    object_name = pair["object"]
                    quantity = pair["quantity"]
                    
                    print(f"   Creating {quantity}x {object_name}...")
                    
                    for i in range(quantity):
                        new_object = self.create_object(object_name)
                        new_objects.append(new_object)
                        print(f"      ✅ Created: {new_object['id']}")
                    
                self.pending_objects = []
                    
                return {
                    "action": "add",
                    "new_objects": new_objects,
                    "quantity": len(new_objects),
                    "needs_positioning": True,
                    "success": True,
                    "message": f"Created {len(new_objects)} object(s)"
                }

            except ValueError as e:
                self.pending_objects = []
                return {
                    "action": "add",
                    "success": False,
                    "message": str(e)
                }
        
        else:
            return {
                "action": "unknown",
                "success": False,
                "message": f"Unknown action: {action}"
            }



# ============================================================================
# TEST
# ============================================================================
if __name__ == "__main__":
    from database import Database
    
    # Initialize
    db = Database()
    agent = AssetAgent(db)
    
    print("\n" + "="*60)
    print("TEST 1: Add 3 chairs and 2 tables")
    print("="*60)
    
    # Simulate parsed command from Language Agent
    add_command = {
        "original_prompt": "Add 3 chairs and 2 tables",
        "action_hints": {"primary_action": "add"},
        "involved_objects": ["chair", "table"]
    }
    
    result = agent.process_command(add_command)
    print(f"\nResult: {json.dumps(result, indent=2)}")


    


