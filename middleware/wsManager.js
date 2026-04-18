import {
  addObjectToScene,
  removeObjectFromScene,
  updateObjectPosition,
  updateObjectRotation
} from './sceneControl.js';

let ws = null;
let loadedObjects = null;
let scene = null;
/**
 * Connect to FastAPI WebSocket
 */

export function setupWebSocket(objectsMap, threeScene) {
  loadedObjects = objectsMap;
  scene = threeScene;
  // ws = new WebSocket('ws://localhost:8000/ws/scene');
  // const host = window.location.hostname; // Gets "10.0.0.97" for headset
  // const wsUrl = `ws://${host}:8000/ws/scene`;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const hostname = window.location.hostname; // Just hostname, no port
  const wsUrl = `${protocol}//${hostname}:8000/ws/scene`;
  
  ws = new WebSocket(wsUrl);
  
  ws.onmessage = (event) => {
    const message = JSON.parse(event.data);
    console.log('Received:', message);

    switch(message.type) {
      case 'object_position_updated':
        updateObjectPosition(message.data, loadedObjects);
        break;
      case 'object_rotation_updated':
        updateObjectRotation(message.data, loadedObjects);
        break;
      case 'object_added': 
        addObjectToScene(message.data, loadedObjects, scene);
        break;
      case 'object_removed':
        removeObjectFromScene(message.data, loadedObjects, scene);
        break;
      case 'scene_saved':
        console.log('Scene updated from backend');
        break;
      case 'scene_reset':
        console.log('🔄 Backend cleared added objects - reloading...');
        setTimeout(() => {
          window.location.reload();
        }, 1000);
        break;
    }
  };

  ws.onerror = (error) => {
    console.error('WebSocket error:', error);
  };

  ws.onclose = () => {
    console.log('WebSocket disconnected, reconnecting...');
    setTimeout(() => setupWebSocket(loadedObjects, scene), 3000); // Auto-reconnect
  };


  // Return websocket for its usage of head-tracking
  return ws;
}
