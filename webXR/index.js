/* eslint-disable sort-imports */
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { Text } from 'troika-three-text';
import { XR_BUTTONS } from 'gamepad-wrapper';
import { gsap } from 'gsap';
import { init } from './init.js';
import { loadSceneDatabase, getSceneDatabase, SceneQuery } from '../middleware/sceneLoader.js';
import { setupWebSocket } from '../middleware/wsManager.js';
import { initHeadTracking, startHeadTracking, updateHeadTracking, toggleAABB  } from '../middleware/sceneControl.js';


// Global scene query interface for agents
let sceneQuery;
let loadedObjects = new Map(); // Maps object IDs to THREE.js objects
let sceneDatabase;
let websocket;
let xrReferenceSpace = null;
let userAvatar = null;



async function setupScene({ scene, camera, renderer, player, controllers }) {
  // Load scene database from JSON
  console.log('Loading scene from JSON...');
  sceneDatabase = await loadSceneDatabase();

  // Initialize scene query system
  sceneQuery = new SceneQuery(sceneDatabase);

  // Load textures
  const textureLoader = new THREE.TextureLoader();
  const textures = new Map();

  // Load structure (floor and walls)
  await loadStructure(scene, textureLoader, textures);

  // Load all objects from database
  await loadObjects(scene);

  // Setup lighting from database
  loadLighting(scene);

  // Load user avatar
  loadUserAvatar(camera);

  // Make sceneQuery globally accessible for agents
  window.sceneQuery = sceneQuery;
  window.loadedObjects = loadedObjects;
  window.sceneDatabase = sceneDatabase;

  console.log('✅ Scene loaded from JSON with', sceneDatabase.objects.length, 'objects');


  // head tracking
  // Add websocket
  websocket = setupWebSocket(loadedObjects, scene);


  // toggle AABB with '\ key
  window.addEventListener('keydown', (e) => {

      if (e.key === '\\') toggleAABB();
  });


  initHeadTracking(websocket);

  renderer.xr.addEventListener('sessionstart', async() =>{
    console.log('🥽 XR session started');
    const session = renderer.xr.getSession();
    xrReferenceSpace = await session.requestReferenceSpace('local');
    startHeadTracking();
  });

  renderer.xr.addEventListener('sessioned', () => {
    console.log('🥽 XR session ended');
    xrReferenceSpace = null;
  });

}

/**
 * Load structural elements (floor, walls) from database
 */
function loadStructure(scene, textureLoader, textures) {
  return new Promise((resolve) => {
    // Load floor
    const floorData = sceneDatabase.structure.floor;
    const floorTexture = textureLoader.load(floorData.texture);
    const floorGeometry = new THREE.PlaneGeometry(
      floorData.geometry.width,
      floorData.geometry.height
    );
    const floorMaterial = new THREE.MeshStandardMaterial({
      map: floorTexture
    });
    const floor = new THREE.Mesh(floorGeometry, floorMaterial);
    floor.position.set(floorData.position.x, floorData.position.y, floorData.position.z);
    floor.rotation.set(floorData.rotation.x, floorData.rotation.y, floorData.rotation.z);
    floor.scale.set(floorData.scale.x, floorData.scale.y, floorData.scale.z);
    floor.userData.id = floorData.id;
    scene.add(floor);
    loadedObjects.set(floorData.id, floor);

    // Load walls
    const wallTexture = textureLoader.load(sceneDatabase.structure.walls[0].texture);
    sceneDatabase.structure.walls.forEach((wallData, index) => {
      const wallGeometry = new THREE.PlaneGeometry(
        wallData.geometry.width,
        wallData.geometry.height
      );
      const wallMaterial = new THREE.MeshStandardMaterial({
        map: wallTexture
      });

      const isFrontWall = wallData.id === 'wall_front';


      // Create front face, only front wall to be one-sided for visibility
      const wall1 = new THREE.Mesh(wallGeometry, wallMaterial);
      if (!isFrontWall){
        
        wall1.position.set(wallData.position.x, wallData.position.y, wallData.position.z);
        wall1.rotation.set(wallData.rotation.x, wallData.rotation.y, wallData.rotation.z);
        wall1.scale.set(wallData.scale.x, wallData.scale.y, wallData.scale.z);
        wall1.userData.id = `${wallData.id}_front`;
        scene.add(wall1);
        loadedObjects.set(`${wallData.id}_front`, wall1);
      }

      // Create back face (for visibility from both sides)
      const wall2 = new THREE.Mesh(wallGeometry, wallMaterial);
      wall2.position.set(wallData.position.x, wallData.position.y, wallData.position.z);
      wall2.rotation.set(
        wallData.rotation.x + (wallData.rotation.y === 0 ? Math.PI : 0),
        wallData.rotation.y + (wallData.rotation.y !== 0 ? Math.PI : 0),
        wallData.rotation.z
      );
      wall2.scale.set(wallData.scale.x, wallData.scale.y, wallData.scale.z);
      wall2.userData.id = `${wallData.id}_back`;
      scene.add(wall2);

      loadedObjects.set(`${wallData.id}_front`, wall1);
      loadedObjects.set(`${wallData.id}_back`, wall2);
    });

    resolve();
  });
}

/**
 * Load all objects from database
 */
function loadObjects(scene) {
  const gltfLoader = new GLTFLoader();

  // Create array of promises for all object loads
  const loadPromises = sceneDatabase.objects.map(objData => {
    return new Promise((resolve, reject) => {
      gltfLoader.load(
        objData.modelPath,
        (gltf) => {
          // Apply transformations from database
          gltf.scene.position.set(
            objData.position.x,
            objData.position.y,
            objData.position.z
          );
          gltf.scene.rotation.set(
            objData.rotation.x,
            objData.rotation.y,
            objData.rotation.z
          );
          gltf.scene.scale.set(
            objData.scale.x,
            objData.scale.y,
            objData.scale.z
          );

          // Store metadata for agent access
          gltf.scene.userData = {
            id: objData.id,
            name: objData.name,
            category: objData.category,
            properties: objData.properties,
            dbReference: objData
          };

          scene.add(gltf.scene);
          loadedObjects.set(objData.id, gltf.scene);


          // AABB helper — child of gltf.scene, follows position/rotation automatically
          if (objData.collision) {
              const { width, height, depth } = objData.collision;
              const sx = objData.scale.x, sy = objData.scale.y, sz = objData.scale.z;

              const geo = new THREE.BoxGeometry(width / sx, height / sy, depth / sz);
              const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
                  color: 0x00ff00, transparent: true, opacity: 0.15,
                  side: THREE.DoubleSide, depthWrite: false
              }));
              mesh.add(new THREE.LineSegments(
                  new THREE.EdgesGeometry(geo),
                  new THREE.LineBasicMaterial({ color: 0x00ff00 })
              ));
              mesh.position.set(0, (height / sy) / 2, 0);
              mesh.visible = false; // hidden by default, toggle with B
              mesh.name = 'aabb_helper';
              gltf.scene.add(mesh);
          }


          console.log(`✓ Loaded: ${objData.name} (${objData.id})`);
          resolve();
        },
        undefined,
        (error) => {
          console.error(`✗ Error loading ${objData.name}:`, error);
          reject(error);
        }
      );
    });
  });

  // Wait for all objects to load
  return Promise.all(loadPromises);
}

/**
 * Load lighting from database
 */
function loadLighting(scene) {
  sceneDatabase.lighting.forEach(lightData => {
    let light;

    if (lightData.type === 'ambient') {
      light = new THREE.AmbientLight(lightData.color, lightData.intensity);
    } else if (lightData.type === 'directional') {
      light = new THREE.DirectionalLight(lightData.color, lightData.intensity);
      if (lightData.position) {
        light.position.set(
          lightData.position.x,
          lightData.position.y,
          lightData.position.z
        );
      }
    }

    if (light) {
      light.userData.id = lightData.id;
      scene.add(light);
      console.log(`✓ Added ${lightData.type} light: ${lightData.id}`);
    }
  });
}

/**
 * Load user avatar function
 */
function loadUserAvatar(camera){
  /*
  const gltfLoader = new GLTFLoader();
  gltfLoader.load(
    '/assets/gltf-glb-models/sun/sun.glb',
    (gltf) => {
      userAvatar = gltf.scene;
      userAvatar.scale.set(0.01, 0.01, 0.01);
      userAvatar.position.set(0, 0.01, -0.1);
      camera.add(userAvatar);
      console.log('✓ User avatar loaded');
    },
    undefined,
    (error) => {
      console.error('✗ Error loading user avatar:', error);
    }
  );
  */
  
  // for lower render cost
  const avatarGeometry = new THREE.SphereGeometry(0.02);
  const avatarMaterial = new THREE.MeshStandardMaterial({ 
    color: 'green', 
    emissive: 0x440000, // glow effect
    roughness: 0.3,
    metalness: 0.2
  });
  userAvatar = new THREE.Mesh(avatarGeometry, avatarMaterial);
  userAvatar.position.set(0, 0.05, -0.3); // Remove scale line
  userAvatar.scale.set(0.5, 0.5, 0.5);
  camera.add(userAvatar);
  
  
  
  console.log('✓ User avatar loaded');
  
}

// Animation function
function onFrame(delta, time, { scene, camera, renderer, player, controllers }) {
  // Add any per-frame logic here
  // For example, update spatial relationships based on user position
  gsap.ticker.tick();

  // Update head tracking every frame while in XR
  if (renderer.xr.isPresenting && xrReferenceSpace) {
    const frame = renderer.xr.getFrame();
    if (frame){
      updateHeadTracking(frame, xrReferenceSpace);
    }
  }
}

init(setupScene, onFrame);