from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional, Set
from orchestrator import Orchestrator
from __init__ import LanguageAgent, SceneAgent, AssetAgent, CodeAgent, VerificationAgent
from database import Database
from stt.routes import router as stt_router
import uvicorn
import json
import ssl

class CommandRequest(BaseModel):
    command: str
    user_position: Optional[Dict[str, float]] = None

'''Server side code with WebSocket support'''
app = FastAPI(title = "XR Multi-Agent Spatial System")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stt_router)



# Initialize scene database
scene_database = Database(app=app)

# Initialize specialized agents
language_agent = LanguageAgent()
scene_agent = SceneAgent()
asset_agent = AssetAgent(scene_database)
code_agent = CodeAgent(scene_database)
verification_agent = VerificationAgent(scene_database)

# Initialize orchestration agent
orchestration_agent = Orchestrator(language_agent, 
                                   scene_agent, 
                                   asset_agent,
                                   code_agent, 
                                   verification_agent,
                                   scene_database)

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self. active_connections.add(websocket)
        print(f"Client connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        print(f"Client disconnected. Total: {len(self.active_connections)}")
    
    async def broadcast(self, message: Dict):
        """Broadcast message to all connected clients"""
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.add(connection)
        
        # clean up disconnected clients
        for conn in disconnected:
            self.active_connections.discard(conn)
        
        if self.active_connections:
            print(f"Broadcast to {len(self.active_connections)} clients")

# Global connection manager
manager = ConnectionManager()

# WebSocket endpoint
@app.websocket("/ws/scene")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    print("New client connected - clearing added objects")
    removed = scene_database.clear_added_objects()

    if removed > 0:
        # Broadcast the reset notification
        await broadcast_scene_update('scene_reset', {
            'message': f'Cleared {removed} added object(s)',
            'removed_count': removed
        })
        
    try:
        while True:
            # Keep connection alive and listen for messages
            data = await websocket.receive_text()
            message = json.loads(data)
            # Handle client messages (optional)
            if message.get('type') == 'ping':
                await websocket.send_json({'type': 'pong'})
    
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket)

# function for agents to broadcast updates
async def broadcast_scene_update(update_type: str, data: dict):
    """Helper function for broadcasting scene updates"""
    message = {
        'type': update_type,
        'data': data 
    }
    await manager.broadcast(message)

# Make broadcast function accessible to database
app.state.broadcast = broadcast_scene_update
app.state.manager = manager

# Example API endpoints
@app.get("/")
async def root():
    return {"message": "XR Multi-Agent Spatial System API"}

@app.get("/scene/stats")
async def get_scene_stats():
    """Get scene statistics"""
    return scene_database.get_statistics()

@app.post("/scene/update-position")
async def update_position(object_id: str, x: float, y:float, z: float):
    """Update object position and broadcast to all clients"""
    success = scene_database.update_object_position(
        object_id, 
        {"x": x, "y": y, "z": z}
    )

    if success:
        obj = scene_database.get_object_by_id(object_id)
        # Broadcast to all connected WebSocket clients
        
        await broadcast_scene_update('object_position_updated', {
            'objectId': object_id,
            'position': {'x': x, 'y': y, 'z': z},
            'name': obj['name'] if obj else 'unknown'
        }) 
        
        # Save without double-broadcast
        # scene_database.save() 

        return {"status": "success", "objectId": object_id}

    raise HTTPException(status_code=404, detail="Object not found")

@app.post("/scene/update-rotation")
async def update_rotation(object_id: str, x: float, y:float, z: float):
    """Update object rotation and broadcast to all clients"""
    success = scene_database.update_object_rotation(
        object_id,
        {"x": x, "y": y, "z": z}
    )
    if success:
        obj = scene_database.get_object_by_id(object_id)
        # Broadcast to all connected WebSocket clients
        await broadcast_scene_update('object_rotation_updated', {
            'objectId': object_id,
            'rotation': {'x': x, 'y': y, 'z': z},
            'name': obj['name'] if obj else 'unknown'
        }) 
        return {"status": "success", "objectId": object_id}
    
    raise HTTPException(status_code=404, detail="Object not found")


@app.post("/scene/command")
async def process_natural_language_command(request: CommandRequest):
    """
    Process natural language command through the multi-agent system
    
    Body:
        {
            "command": "move the chair forward",
            "user_position": {"x": 0, "y": 0, "z": 0}  // optional
        }
    """
    try:
        # Update user position if provided from VR headset
        if request.user_position:
            orchestration_agent.user_position = request.user_position
        
        # Process command through orchestrator
        success = orchestration_agent.process_command(request.command)
        
        if success:
            # Save changes to database file
            # scene_database.save()
            
            return {
                "status": "success",
                "command": request.command,
                "message": "Command executed successfully"
            }
        else:
            raise HTTPException(
                status_code=400, 
                detail="Failed to execute command"
            )
        
    except HTTPException:
        raise
    
    except Exception as e:
        print(f"Error in /scene/command: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing command: {str(e)}"
        )

if __name__ == "__main__":
    # configure ssl
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000,
        ssl_keyfile='./key.pem',
        ssl_certfile='./cert.pem'
        )