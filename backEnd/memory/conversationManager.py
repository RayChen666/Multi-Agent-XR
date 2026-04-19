import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

class ConversationManager:
    """
        Manages conversation history with persistent storage.
        
        Features:
        - Session-based conversation tracking
        - Automatic persistence to JSON/SQLite
        - History retrieval and analysis
        - Export capabilities for thesis data
        """
    
    def __init__(self, storage_path: str = "data/conversation_history.json"):
        """
        Initialize conversation manager
        
        Args:
            storage_path: Path to JSON file for persistent storage
        """
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(exist_ok=True)
        
        # In-memory cache for fast access
        self.sessions: Dict[str, List[Dict]] = self._load_from_disk()
        
        print(f"ConversationManager initialized")
        print(f"Storage: {self.storage_path}")
        print(f"Loaded {len(self.sessions)} sessions")

        def add_turn(self,
                 session_id: str,
                 user_prompt: str,
                 parsed_command: Optional[Dict] = None,
                 object_states: Optional[List] = None,
                 spatial_updates: Optional[Dict] = None,
                 execution_result: Optional[Dict] = None,
                 success: bool = False,
                 error: Optional[str] = None,
                 metadata: Optional[Dict] = None) -> Dict:
            """
            Add a conversation turn to history
            
            Args:
                session_id: Session identifier
                user_prompt: User's original command
                parsed_command: Output from Language Agent
                object_states: States of involved objects
                spatial_updates: Transformation from Scene Agent
                execution_result: Result from Code Agent
                success: Whether execution succeeded
                error: Error message if failed
                metadata: Additional metadata (scene state, user position, etc.)
            
            Returns:
                The created turn entry
            """
            # Initialize session if new
            if session_id not in self.sessions:
                self.sessions[session_id] = []
                print(f"Created new session: {session_id}")
            
            history = self.sessions[session_id]
            turn_number = len(history) + 1
            
            turn_entry = {
                'turn': turn_number,
                'timestamp': time.time(),
                'datetime': datetime.now().isoformat(),
                'user_prompt': user_prompt,
                'parsed_command': parsed_command,
                'object_states': object_states,
                'spatial_updates': spatial_updates,
                'execution_result': execution_result,
                'success': success,
                'error': error,
                'metadata': metadata or {}
            }
            
            history.append(turn_entry)

            # Auto-save to disk
            self._save_to_disk()
            
            return turn_entry
    
    def get_session_history(self, session_id: str) -> List[Dict]:
        """Get full history for a session"""
        return self.sessions.get(session_id, [])
    
    def get_recent_turns(self, session_id: str, n: int = 5) -> List[Dict]:
        """
        Get n most recent turns from a session
        
        Args:
            session_id: Session identifier
            n: Number of recent turns to retrieve
        
        Returns:
            List of recent turn entries
        """
        history = self.sessions.get(session_id, [])
        return history[-n:] if len(history) > 0 else []
    
    def _load_from_disk(self) -> Dict[str, List[Dict]]:
        """Load conversation history from JSON file"""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, 'r') as f:
                    data = json.load(f)
                    return data
            except json.JSONDecodeError as e:
                print(f"Corrupted history file, starting fresh: {e}")
                return {}
            except Exception as e:
                print(f"Failed to load history: {e}")
                return {}
        return {}
    
    def _save_to_disk(self):
        """Save conversation history to JSON file"""
        try:
            with open(self.storage_path, 'w') as f:
                json.dump(self.sessions, f, indent=2)
        except Exception as e:
            print(f"Failed to save history: {e}")


    # ============================================================
    # SESSION MANAGEMENT
    # ============================================================
    
    def get_all_session_ids(self) -> List[str]:
        """Get list of all session IDs"""
        return list(self.sessions.keys())
    
    def session_exists(self, session_id: str) -> bool:
        """Check if a session exists"""
        return session_id in self.sessions
    
    def clear_session(self, session_id: str) -> bool:
        """
        Clear history for a specific session
        
        Returns:
            True if session was cleared, False if session didn't exist
        """
        if session_id in self.sessions:
            del self.sessions[session_id]
            self._save_to_disk()
            print(f"Cleared session: {session_id}")
            return True
        return False
    
    def clear_all_sessions(self):
        """Clear all session history"""
        self.sessions = {}
        self._save_to_disk()
        print("Cleared all session history")


    def get_all_session_ids(self) -> List[str]:
            """Get list of all session IDs"""
            return list(self.sessions.keys())
    
    def session_exists(self, session_id: str) -> bool:
        """Check if a session exists"""
        return session_id in self.sessions
    
    def clear_session(self, session_id: str) -> bool:
        """
        Clear history for a specific session
        
        Returns:
            True if session was cleared, False if session didn't exist
        """
        if session_id in self.sessions:
            del self.sessions[session_id]
            self._save_to_disk()
            print(f"Cleared session: {session_id}")
            return True
        return False
    
    def clear_all_sessions(self):
        """Clear all session history"""
        self.sessions = {}
        self._save_to_disk()
        print("Cleared all session history")
    
    # ============================================================
    # STATISTICS & ANALYSIS
    # ============================================================
    
    def get_session_stats(self, session_id: str) -> Dict:
        """
        Get statistics for a session
        
        Returns:
            Dict with session statistics
        """
        history = self.sessions.get(session_id, [])
        
        if not history:
            return {
                'session_id': session_id,
                'total_turns': 0,
                'successful': 0,
                'failed': 0,
                'success_rate': 0.0
            }
        
        successful = sum(1 for turn in history if turn['success'])
        failed = len(history) - successful
        
        return {
            'session_id': session_id,
            'total_turns': len(history),
            'successful': successful,
            'failed': failed,
            'success_rate': (successful / len(history) * 100) if history else 0.0,
            'first_turn_time': history[0]['datetime'],
            'last_turn_time': history[-1]['datetime'],
            'duration_seconds': history[-1]['timestamp'] - history[0]['timestamp']
        }
    
    def get_global_stats(self) -> Dict:
        """Get statistics across all sessions"""
        total_sessions = len(self.sessions)
        total_turns = sum(len(history) for history in self.sessions.values())
        total_successful = sum(
            sum(1 for turn in history if turn['success'])
            for history in self.sessions.values()
        )
        
        return {
            'total_sessions': total_sessions,
            'total_turns': total_turns,
            'total_successful': total_successful,
            'total_failed': total_turns - total_successful,
            'global_success_rate': (total_successful / total_turns * 100) if total_turns > 0 else 0.0
        }
    
    def print_session_summary(self, session_id: str):
        """Print formatted session summary"""
        stats = self.get_session_stats(session_id)
        
        print(f"\n{'='*60}")
        print(f"Session Summary: {session_id}")
        print(f"{'='*60}")
        print(f"Total turns: {stats['total_turns']}")
        print(f"Successful: {stats['successful']}")
        print(f"Failed: {stats['failed']}")
        print(f"Success rate: {stats['success_rate']:.1f}%")
        if stats['total_turns'] > 0:
            print(f"Duration: {stats['duration_seconds']:.1f}s")
        print(f"{'='*60}\n")
    
    def print_global_summary(self):
        """Print formatted global summary"""
        stats = self.get_global_stats()
        
        print(f"\n{'='*60}")
        print(f"Global Statistics")
        print(f"{'='*60}")
        print(f"Total sessions: {stats['total_sessions']}")
        print(f"Total turns: {stats['total_turns']}")
        print(f"Successful: {stats['total_successful']}")
        print(f"Failed: {stats['total_failed']}")
        print(f"Global success rate: {stats['global_success_rate']:.1f}%")
        print(f"{'='*60}\n")
    
    # ============================================================
    # EXPORT CAPABILITIES (For Thesis)
    # ============================================================
    
    def export_session_to_json(self, session_id: str, filepath: str):
        """Export single session to JSON file"""
        history = self.get_session_history(session_id)
        
        if not history:
            print(f"No history found for session: {session_id}")
            return
        
        export_data = {
            'session_id': session_id,
            'stats': self.get_session_stats(session_id),
            'history': history
        }
        
        with open(filepath, 'w') as f:
            json.dump(export_data, f, indent=2)
        
        print(f"Exported session '{session_id}' to {filepath}")
    
    def export_all_to_json(self, filepath: str):
        """Export all sessions to JSON file"""
        export_data = {
            'export_time': datetime.now().isoformat(),
            'global_stats': self.get_global_stats(),
            'sessions': self.sessions
        }
        
        with open(filepath, 'w') as f:
            json.dump(export_data, f, indent=2)
        
        print(f"Exported all sessions to {filepath}")
    
    def export_to_csv(self, filepath: str):
        """
        Export conversation data to CSV for analysis
        
        Creates a flat table with one row per turn
        """
        import csv
        
        rows = []
        for session_id, history in self.sessions.items():
            for turn in history:
                rows.append({
                    'session_id': session_id,
                    'turn': turn['turn'],
                    'timestamp': turn['timestamp'],
                    'datetime': turn['datetime'],
                    'user_prompt': turn['user_prompt'],
                    'command_type': turn.get('parsed_command', {}).get('command_type', 'N/A'),
                    'primary_action': turn.get('parsed_command', {}).get('action_hints', {}).get('primary_action', 'N/A'),
                    'objects_involved': ', '.join(turn.get('parsed_command', {}).get('involved_objects', [])),
                    'success': turn['success'],
                    'error': turn.get('error', '')
                })
        
        with open(filepath, 'w', newline='') as f:
            if rows:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
        
        print(f"Exported {len(rows)} turns to {filepath}")
