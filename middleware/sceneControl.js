import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { gsap } from 'gsap';


const gltfLoader = new GLTFLoader();

// ─── AABB state ───────────────────────────────────────────────────────────────
let aabbVisible = false;
const aabbHelpers = new Map(); // objectId → mesh (child of gltf.scene)

export function toggleAABB() {
    aabbVisible = !aabbVisible;
    console.log(`AABB helpers count: ${aabbHelpers.size}`);
    aabbHelpers.forEach(helper => { helper.visible = aabbVisible; });
    console.log(`AABB visualization: ${aabbVisible ? 'ON' : 'OFF'}`);
    return aabbVisible;
}

/**
 * Add new object to scene from WebSocket message
 */
export function addObjectToScene(data, loadedObjects, scene) {
  console.log('🎨 Adding new object to scene:', data);
  
  const { objectId, objectData } = data;
  
  // Check if already loaded
  if (loadedObjects.has(objectId)) {
    console.warn(`Object ${objectId} already exists in scene`);
    return;
  }
  
  // Load the 3D model
  gltfLoader.load(
    objectData.modelPath,
    (gltf) => {
      // Apply transformations from database
      gltf.scene.position.set(
        objectData.position.x,
        objectData.position.y,
        objectData.position.z
      );
      gltf.scene.rotation.set(
        objectData.rotation.x,
        objectData.rotation.y,
        objectData.rotation.z
      );
      gltf.scene.scale.set(
        objectData.scale.x,
        objectData.scale.y,
        objectData.scale.z
      );
      
      // Store metadata for agent access
      gltf.scene.userData = {
        id: objectData.id,
        name: objectData.name,
        category: objectData.category,
        properties: objectData.properties,
        dbReference: objectData
      };
      
      // Add to scene
      scene.add(gltf.scene);
      loadedObjects.set(objectId, gltf.scene);

      // AABB helper — child of gltf.scene, follows position/rotation/scale automatically
      if (objectData.collision) {
        const { width, height, depth } = objectData.collision;
        const sx = objectData.scale.x, sy = objectData.scale.y, sz = objectData.scale.z;

        const geo = new THREE.BoxGeometry(width / sx, height / sy, depth / sz);
        const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
          color: 0x00ff00, transparent: true, opacity: 0.15,
          side: THREE.DoubleSide, depthWrite: false
        }));
        mesh.add(new THREE.LineSegments(
          new THREE.EdgesGeometry(geo),
          new THREE.LineBasicMaterial({ color: 0x00ff00 })
        ));

        //mesh.position.set(0, (height / sy) / 2, 0);
        const offset = objectData.collision.offset;
        mesh.position.set(
            (offset?.x ?? 0) / sx,
            (offset?.y ?? height / 2) / sy,
            (offset?.z ?? 0) / sz
        );
        
        mesh.visible = aabbVisible;
        mesh.name = 'aabb_helper';
        gltf.scene.add(mesh);
        aabbHelpers.set(objectId, mesh);
      }

      console.log(`Added ${objectData.name} (${objectId}) to scene`);
      
      // Spawn animation
      gltf.scene.scale.set(0, 0, 0);
      gsap.to(gltf.scene.scale, {
        x: objectData.scale.x,
        y: objectData.scale.y,
        z: objectData.scale.z,
        duration: 0.5,
        ease: "back.out(1.7)"
      });
    },
    undefined,
    (error) => {
      console.error(`Failed to load ${objectId}:`, error);
    }
  );
}

/**
 * Update object position from WebSocket message
 * AABB moves automatically as child of threeObject
 */
export function updateObjectPosition(data, loadedObjects) {
  const threeObject = loadedObjects.get(data.objectId);
  if (threeObject) {
    gsap.to(threeObject.position, {
      x: data.position.x,
      y: data.position.y,
      z: data.position.z,
      duration: 0.5,
      ease: "power2.inOut"
    });
    console.log(`Updated ${data.name} position from backend`);
  } else {
    console.warn(`Object ${data.objectId} not found in scene`);
  }
}

/**
 * Update object rotation from WebSocket message
 * AABB rotates automatically as child of threeObject
 */
export function updateObjectRotation(data, loadedObjects) {
  const threeObject = loadedObjects.get(data.objectId);
  if (threeObject) {
    gsap.to(threeObject.rotation, {
      x: data.rotation.x,
      y: data.rotation.y,
      z: data.rotation.z,
      duration: 0.5,
      ease: "power2.inOut"
    });
    console.log(`Updated ${data.name} rotation from backend`);
  } else {
    console.warn(`Object ${data.objectId} not found in scene`);
  }
}

/**
 * Dispose geometry/material resources before removing object to prevent memory leaks
 */
function disposeThreeObject(root) {
  root.traverse((child) => {
    if (child.geometry && typeof child.geometry.dispose === 'function') {
      child.geometry.dispose();
    }
    if (child.material) {
      if (Array.isArray(child.material)) {
        child.material.forEach((mat) => {
          if (mat && typeof mat.dispose === 'function') mat.dispose();
        });
      } else if (typeof child.material.dispose === 'function') {
        child.material.dispose();
      }
    }
  });
}

/**
 * Remove object from scene from WebSocket message
 * AABB is a child of threeObject — disposed automatically with parent
 */
export function removeObjectFromScene(data, loadedObjects, scene) {
  const { objectId, name } = data || {};

  if (!objectId) {
    console.warn('removeObjectFromScene called without objectId');
    return false;
  }

  const threeObject = loadedObjects.get(objectId);
  if (!threeObject) {
    console.warn(`Object ${objectId} not found in scene`);
    return false;
  }

  aabbHelpers.delete(objectId);
  scene.remove(threeObject);
  disposeThreeObject(threeObject);
  loadedObjects.delete(objectId);

  const label = name ? `${name} (${objectId})` : objectId;
  console.log(`Removed ${label} from scene`);
  return true;
}


/**
 * Head tracking
 */
let ws = null;
let isTracking = false;

export function initHeadTracking(websocket){
  ws = websocket;
  console.log('Head tracking initialized');
}

export function startHeadTracking(){
  isTracking = true;
  console.log('Head tracking started');
}

export function stopHeadTracking(){
  isTracking = false;
  console.log('Head tracking stopped');
}

function quaternion_to_euler(q){
  const {x, y, z, w} = q;
  const sinr_cosp = 2 * (w * x + y * z);
  const cosr_cosp = 1 - 2 * (x * x + y * y);
  const roll = Math.atan2(sinr_cosp, cosr_cosp);

  const sinp = 2 * (w * y - z * x);
  const pitch = Math.abs(sinp) >= 1
    ? Math.sign(sinp) * Math.PI / 2
    : Math.asin(sinp);

  const siny_cosp = 2 * (w * z + x * y);
  const cosy_cosp = 1 - 2 * (y * y + z * z);
  const yaw = Math.atan2(siny_cosp, cosy_cosp);

  return { x: roll, y: pitch, z: yaw };
}

export function updateHeadTracking(frame, referenceSpace){
  if (!isTracking || !ws || ws.readyState !== WebSocket.OPEN){ return; }
  if (!frame || !referenceSpace){ return; }
  
  const pose = frame.getViewerPose(referenceSpace);
  if (!pose) { return; }

  const position = pose.transform.position;
  const orientation = pose.transform.orientation;
  const euler = quaternion_to_euler(orientation);

  ws.send(JSON.stringify({
    type: 'head_position_update',
    data: {
      position: { x: position.x, y: position.y, z: position.z },
      rotation: { x: euler.x, y: euler.y, z: euler.z }
    },
    timestamp: Date.now()
  }));
}