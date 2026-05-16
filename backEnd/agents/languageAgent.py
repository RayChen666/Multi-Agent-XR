import json
import google.generativeai as genai
from pathlib import Path
from dotenv import load_dotenv
import os
import re

class LanguageAgent:
    def __init__(self):
        # Configure Gemini API
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = genai.GenerativeModel('gemini-2.5-flash-lite')
    
    def parse_prompt(self, 
                     prompt: str,
                     context_history: list = None) -> dict:
        """
        Parse & Decide: Analyze command for routing WITHOUT losing semantic information.
        
        Returns enriched analysis that preserves the original prompt's full context
        for downstream agents to interpret with their specialized knowledge.
        """

        # Build context string from history
        context_str = ""
        if context_history and len(context_history) > 0:
            context_str = "\nRECENT CONVERSATION HISTORY:\n"
            for turn in context_history:
                context_str += f"Turn {turn['turn']}: User: \"{turn['user_prompt']}\"\n"
                if turn['success']:
                    context_str += f"  → Success: {turn.get('spatial_updates', {}).get('action', 'N/A')}\n"
                else:
                    context_str += f"  → Failed: {turn.get('error', 'Unknown error')}\n"
            context_str += "\nUse this history to understand pronouns (\"it\", \"them\") and implicit references.\n"

        system_prompt = """You are a command analyzer for a 3D spatial reasoning system.
        
        Analyze user commands and output JSON with:
        
        {
            "original_prompt": <exact user prompt - preserve this!>,
            "command_type": "ADD/DELETE" | "POS/ROTATE" | "Vague/Complex",
            "involved_objects": [list of all objects mentioned],
            "spatial_concepts": [key spatial relationships - keep natural language!],
            "intent_summary": <high-level goal in natural language>,
            "action_hints": {
                "primary_action": "place" | "move" | "rotate" | "add" | "remove" | "arrange",
                "requires_asset_selection": true | false,
                "requires_spatial_reasoning": true | false,
                "delete_intent": {
                    "global_scope": "none" | "all_objects",
                    "target_specs": [
                        {
                            "object_type": string,
                            "quantity_mode": "exact" | "all",
                            "quantity": number,
                            "reference_type": "deictic" | "definite" | "indefinite" | "numeric" | "all",
                            "spatial_filter": object | null,
                            "selection_policy": "nearest_to_user"
                        }
                    ],
                    "original_prompt": string
                }
            }
        }
        
        IMPORTANT:
        - action_hints.delete_intent is REQUIRED when primary_action is "remove".
        - For non-remove actions, omit delete_intent.
        
        CLASSIFICATION RULES:

        "ADD/DELETE":
        - Creating NEW objects with explicit creation words
        ✅ "add a chair", "create a new lamp", "bring in a table"
        ❌ NOT "place a lamp" (ambiguous - could be moving existing)
        - Removing objects
        ✅ "delete table", "remove the cup", "take away the chair"
        - KEY INDICATORS: "new", "another", "add", "create", "delete", "remove"
        
        REMOVE-INTENT RULES (CRITICAL):
        - Build delete_intent PER TARGET GROUP, not globally.
        - "delete the chair and the table" => two target_specs (chair x1, table x1).
        - "delete 2 chairs" => one target_spec with quantity_mode="exact", quantity=2.
        - "delete all objects" => global_scope="all_objects", target_specs=[].
        - For "this/that/the/a/an <object>", quantity_mode="exact", quantity=1 for that object type.
        - Use selection_policy="nearest_to_user" by default for ambiguous instance selection.

        "POS/ROTATE":
        - Moving existing objects
        ✅ "move chair left", "push table forward", "place lamp on table"
        - Rotating objects
        ✅ "rotate chair 90 degrees", "turn table around"
        - Positioning existing objects
        ✅ "put the chair next to desk", "position lamp behind sofa"
        - KEY: Single object transformation, no creation/deletion

        "Vague/Complex":
        - Multiple objects with interdependencies
        ✅ "arrange dining setup", "organize workspace"
        - Aesthetic/functional goals
        ✅ "make room cozy", "create reading corner"
        - Unclear or multi-step
        ✅ "put things in order", "set up for dinner"
        - Room-level creation or transformation — when the object of "create/make/convert"
          is a ROOM TYPE, not a specific asset
        ✅ "create an office", "make this a bedroom", "convert to living room",
           "set up an office", "turn this into a kitchen"
        - KEY: Requires planning, multiple steps, or unclear intent
        - KEY: If "create/make/convert/set up/turn into" is followed by a ROOM TYPE
          (office, bedroom, kitchen, living room, dining room, studio, gym, library)
          → ALWAYS classify as Vague/Complex, NEVER ADD/DELETE

        INVOLVED OBJECTS - CRITICAL RULES:
        
        ONLY include objects that the user wants to CREATE, MOVE, or DELETE. DO NOT include:
        - Objects that already exist and are only used as spatial references
        - Objects mentioned with "existing", "current", "the" when used for positioning
        - Environmental/structural elements: "wall", "floor", "ceiling", "room", "corner"
        
        Examples:
        ✅ "add 2 chairs next to the existing hockey table"
           → involved_objects: ["chairs"]  (NOT "hockey table" - it's existing!)
           → spatial_concepts: ["next to existing hockey table"]
        
        ✅ "remove the lamp near the window"
           → involved_objects: ["lamp"]  (NOT "window")
           → spatial_concepts: ["near the window"]
        
        ✅ "place chair next to the desk"
           → involved_objects: ["chair"]  (NOT "desk" - it's a reference point)
           → spatial_concepts: ["next to the desk"]
        
        ✅ "add table and 4 chairs around it"
           → involved_objects: ["table", "chairs"]  (both being created)
        
        ❌ "add a chair and place it next to the table"
           → involved_objects: ["chair", "table"]  WRONG - table is existing!
           → Should be: involved_objects: ["chair"]
        
        Type/Variant adjectives:
        - If an adjective describes a DIFFERENT TYPE/VARIANT of the object, it is PART of the object name.
          ✅ "add an ergonomic chair"  → involved_objects: ["ergonomic chair"]
          ✅ "add a dining table"      → involved_objects: ["dining table"]
          ✅ "add an office desk"      → involved_objects: ["office desk"]
          ✅ "add a coffee table"      → involved_objects: ["coffee table"]
        
        Appearance adjectives:
        - If an adjective describes appearance/size/color only, it is NOT part of the object name.
          ✅ "add a red chair"         → involved_objects: ["chair"]
          ✅ "add a small table"       → involved_objects: ["table"]
          ✅ "add a big lamp"          → involved_objects: ["lamp"]
        - Type-variant adjectives (ergonomic, dining, office, coffee, standing, folding) STAY with the object.
        - Appearance adjectives (red, blue, big, small, tall, short) do NOT STAY with the object.

        SPATIAL CONCEPTS:
        - DON'T reduce to simple keywords
        - PRESERVE the natural language descriptions
        - Examples: 
          ✅ "cozy reading corner with lamp next to chair facing window"
          ❌ "next_to, facing"
        
        CRITICAL DISAMBIGUATION:
        FIRST CHECK: Does the command target a ROOM TYPE?
        Room types: office, bedroom, kitchen, living room, dining room, 
                    studio, gym, library, workspace, meeting room
        If "create/make/convert/set up/turn into" + ROOM TYPE → Vague/Complex immediately.
        
        OTHERWISE:
        Does command mention "add", "create new", "another", "delete", "remove"?
        ├─ YES → ADD/DELETE
        └─ NO → Is it "move", "rotate", "place", "put"?
            ├─ YES → POS/ROTATE
            └─ NO → Is it multi-object or aesthetic goal?
                ├─ YES → Vague/Complex
                └─ NO → Default to POS/ROTATE
        
        INTENT SUMMARY:
        - Capture the high-level goal
        - What is the user trying to achieve?
        - Examples:
          "Create a comfortable reading space"
          "Rearrange furniture for better flow"
          "Simple leftward movement of chair"
        
        EXAMPLES:
        
        Input: "move chair left"
        {
            "original_prompt": "move chair left",
            "command_type": "POS/ROTATE",
            "involved_objects": ["chair"],
            "spatial_concepts": ["move left relative to user"],
            "intent_summary": "Simple leftward movement of chair",
            "action_hints": {
                "primary_action": "move",
                "requires_asset_selection": false,
                "requires_spatial_reasoning": true
            }
        }
        
        Input: "add an ergonomic chair next to the table"
        {
            "original_prompt": "add an ergonomic chair next to the table",
            "command_type": "ADD/DELETE",
            "involved_objects": ["ergonomic chair"],
            "spatial_concepts": ["next to table"],
            "intent_summary": "Add an ergonomic chair next to the existing table",
            "action_hints": {
                "primary_action": "add",
                "requires_asset_selection": true,
                "requires_spatial_reasoning": true
            }
        }
        
        Input: "add 2 ergonomic chairs, with each placed at left and right side of existing hockey table, next to the back wall"
        {
            "original_prompt": "add 2 ergonomic chairs, with each placed at left and right side of existing hockey table, next to the back wall",
            "command_type": "ADD/DELETE",
            "involved_objects": ["ergonomic chairs"],
            "spatial_concepts": ["each chair at left and right side of existing hockey table", "next to back wall"],
            "intent_summary": "Add two ergonomic chairs positioned on either side of the existing hockey table, near the back wall",
            "action_hints": {
                "primary_action": "add",
                "requires_asset_selection": true,
                "requires_spatial_reasoning": true
            }
        }
        
        Input: "create a cozy reading corner with a lamp next to the chair facing the window"
        {
            "original_prompt": "create a cozy reading corner with a lamp next to the chair facing the window",
            "command_type": "Vague/Complex",
            "involved_objects": ["lamp", "chair"],
            "spatial_concepts": [
                "cozy reading corner composition",
                "lamp positioned next to chair",
                "chair oriented facing window",
                "aesthetic goal: cozy atmosphere"
            ],
            "intent_summary": "Create a functional and aesthetic reading space with proper lighting and window view",
            "action_hints": {
                "primary_action": "arrange",
                "requires_asset_selection": true,
                "requires_spatial_reasoning": true
            }
        }
        
        Input: "rotate the table 90 degrees"
        {
            "original_prompt": "rotate the table 90 degrees",
            "command_type": "POS/ROTATE",
            "involved_objects": ["table"],
            "spatial_concepts": ["rotate 90 degrees clockwise"],
            "intent_summary": "Rotate table by specific angle",
            "action_hints": {
                "primary_action": "rotate",
                "requires_asset_selection": false,
                "requires_spatial_reasoning": false
            }
        }

        Input: "remove the coffee table"
        {
            "original_prompt": "remove the coffee table",
            "command_type": "ADD/DELETE",
            "involved_objects": ["coffee table"],
            "spatial_concepts": ["remove coffee table"],
            "intent_summary": "Remove the coffee table from the scene",
            "action_hints": {
                "primary_action": "remove",
                "requires_asset_selection": false,
                "requires_spatial_reasoning": false,
                "delete_intent": {
                    "global_scope": "none",
                    "target_specs": [
                        {
                            "object_type": "coffee table",
                            "quantity_mode": "exact",
                            "quantity": 1,
                            "reference_type": "definite",
                            "spatial_filter": null,
                            "selection_policy": "nearest_to_user"
                        }
                    ],
                    "original_prompt": "remove the coffee table"
                }
            }
        }

        Input: "delete the chair and the table"
        {
            "original_prompt": "delete the chair and the table",
            "command_type": "ADD/DELETE",
            "involved_objects": ["chair", "table"],
            "spatial_concepts": [],
            "intent_summary": "Delete one chair and one table",
            "action_hints": {
                "primary_action": "remove",
                "requires_asset_selection": false,
                "requires_spatial_reasoning": true,
                "delete_intent": {
                    "global_scope": "none",
                    "target_specs": [
                        {
                            "object_type": "chair",
                            "quantity_mode": "exact",
                            "quantity": 1,
                            "reference_type": "definite",
                            "spatial_filter": null,
                            "selection_policy": "nearest_to_user"
                        },
                        {
                            "object_type": "table",
                            "quantity_mode": "exact",
                            "quantity": 1,
                            "reference_type": "definite",
                            "spatial_filter": null,
                            "selection_policy": "nearest_to_user"
                        }
                    ],
                    "original_prompt": "delete the chair and the table"
                }
            }
        }

        Input: "delete 2 chairs"
        {
            "original_prompt": "delete 2 chairs",
            "command_type": "ADD/DELETE",
            "involved_objects": ["chair"],
            "spatial_concepts": [],
            "intent_summary": "Delete two chairs",
            "action_hints": {
                "primary_action": "remove",
                "requires_asset_selection": false,
                "requires_spatial_reasoning": true,
                "delete_intent": {
                    "global_scope": "none",
                    "target_specs": [
                        {
                            "object_type": "chair",
                            "quantity_mode": "exact",
                            "quantity": 2,
                            "reference_type": "numeric",
                            "spatial_filter": null,
                            "selection_policy": "nearest_to_user"
                        }
                    ],
                    "original_prompt": "delete 2 chairs"
                }
            }
        }

        Input: "delete all objects"
        {
            "original_prompt": "delete all objects",
            "command_type": "ADD/DELETE",
            "involved_objects": ["all objects"],
            "spatial_concepts": [],
            "intent_summary": "Delete all removable objects in the scene",
            "action_hints": {
                "primary_action": "remove",
                "requires_asset_selection": false,
                "requires_spatial_reasoning": false,
                "delete_intent": {
                    "global_scope": "all_objects",
                    "target_specs": [],
                    "original_prompt": "delete all objects"
                }
            }
        }

        Input: "arrange the dining table with 4 chairs around it and place a vase in the center"
        {
            "original_prompt": "arrange the dining table with 4 chairs around it and place a vase in the center",
            "command_type": "Vague/Complex",
            "involved_objects": ["table", "chairs", "vase"],
            "spatial_concepts": [
                "4 chairs arranged around table",
                "even distribution pattern",
                "vase as centerpiece on table",
                "dining setup composition"
            ],
            "intent_summary": "Create a complete dining arrangement with table, chairs, and centerpiece",
            "action_hints": {
                "primary_action": "arrange",
                "requires_asset_selection": true,
                "requires_spatial_reasoning": true
            }
        }
        
        Input: "move the couch away from the wall to make space for the bookshelf"
        {
            "original_prompt": "move the couch away from the wall to make space for the bookshelf",
            "command_type": "Vague/Complex",
            "involved_objects": ["couch", "wall", "bookshelf"],
            "spatial_concepts": [
                "move couch away from wall",
                "create space behind couch",
                "implied: bookshelf will occupy the created space"
            ],
            "intent_summary": "Rearrange couch to accommodate bookshelf placement behind it",
            "action_hints": {
                "primary_action": "move",
                "requires_asset_selection": false,
                "requires_spatial_reasoning": true
            }
        }

        Input: "get rid of the lamp next to the window"
        {
            "original_prompt": "get rid of the lamp next to the window",
            "command_type": "ADD/DELETE",
            "involved_objects": ["lamp"],
            "spatial_concepts": ["next to the window"],
            "intent_summary": "Identify and remove the specific lamp located near the window",
            "action_hints": {
                "primary_action": "remove",
                "requires_asset_selection": false,
                "requires_spatial_reasoning": true
            }
        }
        
        Input: "make the room look more spacious"
        {
            "original_prompt": "make the room look more spacious",
            "command_type": "Vague/Complex",
            "involved_objects": ["all objects"],
            "spatial_concepts": [
                "increase perceived spaciousness",
                "optimize furniture arrangement",
                "aesthetic goal: openness"
            ],
            "intent_summary": "Rearrange room layout to maximize perceived space",
            "action_hints": {
                "primary_action": "arrange",
                "requires_asset_selection": false,
                "requires_spatial_reasoning": true
            }
        }

        Input: "Create an office"
        {
            "original_prompt": "Create an office",
            "command_type": "Vague/Complex",
            "involved_objects": [],
            "spatial_concepts": ["create an office environment", "composition of office furniture and layout"],
            "intent_summary": "Set up a complete office environment with appropriate furniture",
            "action_hints": {
                "primary_action": "arrange",
                "requires_asset_selection": true,
                "requires_spatial_reasoning": true
            }
        }
        
        CRITICAL: Always preserve the original_prompt field exactly as given!
        """
        
        full_prompt = f"{system_prompt}{context_str}\n\nInput: {prompt}\n\nOutput JSON:"
        
        try:
            response = self.model.generate_content(
                full_prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=500,
                    response_mime_type="application/json"
                )
            )
            
            response_text = response.text
            
            # Extract JSON
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            
            if json_start != -1 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                parsed = json.loads(json_str)

                if 'original_prompt' not in parsed:
                    parsed['original_prompt'] = prompt
                
                if 'command_type' not in parsed:
                    parsed['command_type'] = 'Vague/Complex'  # Safe default
                
                if 'involved_objects' not in parsed:
                    parsed['involved_objects'] = []
                
                if 'spatial_concepts' not in parsed:
                    parsed['spatial_concepts'] = []
                
                if 'intent_summary' not in parsed:
                    parsed['intent_summary'] = prompt
                
                if 'action_hints' not in parsed:
                    parsed['action_hints'] = {
                        'primary_action': 'place',
                        'requires_asset_selection': True,
                        'requires_spatial_reasoning': True
                    }

                self._normalize_delete_intent(parsed, prompt)
                
                print(f"Language Agent analyzed:")
                print(f"   Command Type: {parsed['command_type']}")
                print(f"   Objects: {parsed['involved_objects']}")
                print(f"   Intent: {parsed['intent_summary']}")
                
                return parsed
            else:
                print(f"No valid JSON in response: {response_text}")
                return self._fallback_parse(prompt)
        
        except json.JSONDecodeError as e:
            print(f"JSON parsing error: {e}")
            print(f"Response: {response.text}")
            return self._fallback_parse(prompt)
        except Exception as e:
            print(f"Gemini API error: {e}")
            return self._fallback_parse(prompt)
    
    def _fallback_parse(self, prompt: str) -> dict:
        """
        Rule-based fallback if Gemini fails.
        Still preserves original prompt and uses heuristics for routing.
        """
        prompt_lower = prompt.lower()
        
        # Detect primary action
        if any(word in prompt_lower for word in ['add', 'delete', 'remove', 'take away', 'get rid of']):
            command_type = 'ADD/DELETE'
            primary_action = 'add' if 'add' in prompt_lower else 'remove'

        elif any(phrase in prompt_lower for phrase in ['new ', 'another ', 'bring in']):
            command_type = 'ADD/DELETE'
            primary_action = 'add'
        
        # POS/ROTATE
        elif any(word in prompt_lower for word in ['move', 'push', 'pull', 'shift', 'place', 'put', 'position']):
            command_type = 'POS/ROTATE'
            primary_action = 'move'
        
        elif any(word in prompt_lower for word in ['rotate', 'turn', 'spin']):
            command_type = 'POS/ROTATE'
            primary_action = 'rotate'
        
        # Arrangement or aesthetic goals
        elif any(word in prompt_lower for word in ['arrange', 'organize', 'setup', 'make', 'create']):
            command_type = 'Vague/Complex'
            primary_action = 'arrange'
        
        else:
            command_type = 'Vague/Complex'
            primary_action = 'arrange'
        
        # Extract objects (simple keyword matching)
        common_objects = ['chair', 'table', 'coffee', 'cup', 'book', 
                         'lamp', 'sofa', 'desk', 'window', 'door', 'wall',
                         'bookshelf', 'vase', 'couch', 'bed', 'shelf']
        involved_objects = [obj for obj in common_objects if obj in prompt_lower]
        
        # Basic spatial concepts
        spatial_keywords = ['next to', 'in front', 'behind', 'on', 'under', 'between',
                           'left', 'right', 'forward', 'backward', 'around', 'facing']
        spatial_concepts = [keyword for keyword in spatial_keywords if keyword in prompt_lower]
        
        print(f"Using fallback parser")
        parsed = {
            'original_prompt': prompt, 
            'command_type': command_type,
            'involved_objects': involved_objects,
            'spatial_concepts': spatial_concepts if spatial_concepts else [prompt_lower],
            'intent_summary': prompt,
            'action_hints': {
                'primary_action': primary_action,
                'requires_asset_selection': command_type == 'ADD/DELETE',
                'requires_spatial_reasoning': True
            }
        }
        self._normalize_delete_intent(parsed, prompt)
        return parsed

    def _normalize_delete_intent(self, parsed: dict, prompt: str) -> None:
        """
        Ensure remove commands always carry a well-formed action_hints.delete_intent.
        This keeps downstream delete handling robust even when LLM output is partial.
        """
        action_hints = parsed.setdefault('action_hints', {})
        primary = str(action_hints.get('primary_action', '')).strip().lower()
        command_type = str(parsed.get('command_type', '')).strip()
        prompt_lower = (prompt or '').lower()

        # Recover remove action if command_type indicates delete-ish phrasing.
        if not primary and command_type == 'ADD/DELETE':
            if any(k in prompt_lower for k in ('delete', 'remove', 'take away', 'get rid of', 'clear')):
                primary = 'remove'
                action_hints['primary_action'] = 'remove'

        if primary not in {'remove', 'delete'}:
            action_hints.pop('delete_intent', None)
            return

        existing = action_hints.get('delete_intent')
        if not isinstance(existing, dict):
            action_hints['delete_intent'] = self._build_delete_intent_fallback(
                prompt=prompt,
                involved_objects=parsed.get('involved_objects', []),
                spatial_concepts=parsed.get('spatial_concepts', []),
            )
            return

        global_scope = existing.get('global_scope', 'none')
        if global_scope not in {'none', 'all_objects'}:
            global_scope = 'none'

        normalized_specs = []
        for spec in existing.get('target_specs', []) if isinstance(existing.get('target_specs', []), list) else []:
            if not isinstance(spec, dict):
                continue
            object_type = str(spec.get('object_type', '')).strip()
            if not object_type:
                continue
            quantity_mode = spec.get('quantity_mode', 'exact')
            if quantity_mode not in {'exact', 'all'}:
                quantity_mode = 'exact'
            quantity = spec.get('quantity', 1)
            try:
                quantity = int(quantity)
            except (TypeError, ValueError):
                quantity = 1
            quantity = max(1, quantity)
            reference_type = spec.get('reference_type', 'definite')
            if reference_type not in {'deictic', 'definite', 'indefinite', 'numeric', 'all'}:
                reference_type = 'definite'

            normalized_specs.append({
                'object_type': object_type,
                'quantity_mode': quantity_mode,
                'quantity': quantity,
                'reference_type': reference_type,
                'spatial_filter': spec.get('spatial_filter'),
                'selection_policy': spec.get('selection_policy', 'nearest_to_user'),
            })

        if global_scope == 'all_objects':
            normalized_specs = []
        elif not normalized_specs:
            fallback = self._build_delete_intent_fallback(
                prompt=prompt,
                involved_objects=parsed.get('involved_objects', []),
                spatial_concepts=parsed.get('spatial_concepts', []),
            )
            global_scope = fallback['global_scope']
            normalized_specs = fallback['target_specs']

        action_hints['delete_intent'] = {
            'global_scope': global_scope,
            'target_specs': normalized_specs,
            'original_prompt': prompt,
        }

    def _build_delete_intent_fallback(
        self,
        prompt: str,
        involved_objects: list,
        spatial_concepts: list,
    ) -> dict:
        """
        Coarse, deterministic remove-intent builder for fallback/normalization.
        """
        text = (prompt or '').lower()

        wipe_phrases = (
            'all objects',
            'everything',
            'clear the scene',
            'clear everything',
            'remove everything',
            'delete everything',
        )
        if any(p in text for p in wipe_phrases):
            return {
                'global_scope': 'all_objects',
                'target_specs': [],
                'original_prompt': prompt,
            }

        numeric_words = {
            'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
            'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
        }

        def _singularize(word: str) -> str:
            w = word.strip().lower()
            if len(w) > 3 and w.endswith('ies'):
                return w[:-3] + 'y'
            if len(w) > 2 and w.endswith('s') and not w.endswith('ss'):
                return w[:-1]
            return w

        target_specs = []
        seen_types = set()
        objects = involved_objects if isinstance(involved_objects, list) else []
        for raw_obj in objects:
            obj = _singularize(str(raw_obj))
            if not obj or obj in seen_types or obj == 'all object':
                continue
            seen_types.add(obj)
            obj_pat = re.escape(obj)

            quantity_mode = 'exact'
            quantity = 1
            reference_type = 'definite'

            if re.search(rf"\ball\s+(?:the\s+)?{obj_pat}s?\b", text):
                quantity_mode = 'all'
                reference_type = 'all'
            else:
                num_match = re.search(
                    rf"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+{obj_pat}s?\b",
                    text,
                )
                if num_match:
                    token = num_match.group(1)
                    quantity = int(token) if token.isdigit() else numeric_words.get(token, 1)
                    reference_type = 'numeric'
                elif re.search(rf"\b(this|that)\s+{obj_pat}\b", text):
                    reference_type = 'deictic'
                elif re.search(rf"\b(a|an)\s+{obj_pat}\b", text):
                    reference_type = 'indefinite'

            target_specs.append({
                'object_type': obj,
                'quantity_mode': quantity_mode,
                'quantity': max(1, quantity),
                'reference_type': reference_type,
                'spatial_filter': None,
                'selection_policy': 'nearest_to_user',
            })

        # Fallback: if remove wording exists but no object extracted, emit empty local scope.
        return {
            'global_scope': 'none',
            'target_specs': target_specs,
            'original_prompt': prompt,
        }


# Test
if __name__ == "__main__":
    agent = LanguageAgent()
    
    test_cases = [
        # Simple commands
        #"move the chair left",
        #"rotate the table 90 degrees",
        "make this space an office",
        # "place the cup on the table",
        #"put the chair next to the desk",
        
        # Complex commands
        #"create a cozy reading corner with a lamp next to the chair facing the window",
        #"arrange the dining table with 4 chairs around it and place a vase in the center",
        #"move the couch away from the wall to make space for the bookshelf",
        #"make the room look more spacious",
        #"organize the workspace better"
    ]

    for prompt in test_cases:
        print(f"\n{'='*60}")
        print(f"Input: '{prompt}'")
        print(f"{'='*60}")
        result = agent.parse_prompt(prompt)
        print(f"\nOutput:")
        print(json.dumps(result, indent=2))