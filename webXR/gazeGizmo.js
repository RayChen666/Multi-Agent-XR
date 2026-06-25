/* eslint-disable sort-imports */
import * as THREE from 'three';

/**
 * gazeGizmo.js
 * -------------------------------------------------------------------------
 * Visual-only head-gaze debug marker.
 *
 * Casts a single ray from the camera (head) forward direction and drops an
 * always-upright translucent cone wherever the ray first hits scene content
 * (floor, walls, or objects). This is a VISUAL CONFIRMATION tool only — it
 * does NOT resolve targets, classify hits, or feed anything into the command
 * pipeline yet.
 *
 * Usage (already wired in index.js):
 *   initGazeGizmo(scene);                                  // once, after scene load
 *   updateGazeGizmo(camera, Array.from(loadedObjects.values())); // every frame
 * -------------------------------------------------------------------------
 */

const MAX_GAZE_DISTANCE = 200; // meters; rays beyond this are treated as "no hit"
const CONE_HEIGHT = 0.5;
const CONE_RADIUS = 0.18;
const GIZMO_COLOR = 0xffff00;

// Floor plane Y in world space (matches sceneData.json structure floor / bounds.min.y
// after the 3x scale-up). Wall hits are clamped down to this so a "look at the wall"
// resolves to a point on the ground rather than partway up the wall.
const FLOOR_Y = -3;

// How often to print the resolved gaze target (milliseconds).
const LOG_INTERVAL_MS = 2000;

let raycaster = null;
let gizmo = null;
let lastLogTime = 0;

// Most recent resolved gaze target, refreshed every frame. Read at command-submit
// time (a single snapshot) and sent alongside user_position. Never null — when the
// gaze hits nothing we store an explicit { type: 'none' } so downstream code has a
// graceful, well-formed fallback instead of a missing value.
let latestTarget = { type: 'none' };

// Scratch vectors reused each frame (avoid per-frame allocations).
const _origin = new THREE.Vector3();
const _direction = new THREE.Vector3();

/**
 * Create the cone gizmo and add it to the scene (hidden until first hit).
 * @param {THREE.Scene} scene
 * @returns {THREE.Group} the gizmo group
 */
export function initGazeGizmo(scene) {
  raycaster = new THREE.Raycaster();
  raycaster.far = MAX_GAZE_DISTANCE;

  // Smooth, closed cone (32 radial segments). Inverted so the apex points
  // DOWN toward the hit point and the base opens upward.
  const geometry = new THREE.ConeGeometry(CONE_RADIUS, CONE_HEIGHT, 32);
  const material = new THREE.MeshBasicMaterial({
    color: GIZMO_COLOR,
    transparent: true,
    opacity: 0.45,
    side: THREE.DoubleSide,
    depthWrite: false,
  });

  gizmo = new THREE.Group();
  gizmo.name = 'gaze_gizmo';

  const cone = new THREE.Mesh(geometry, material);
  cone.name = 'gaze_gizmo_cone';
  // Flip the cone so the apex points down, then lift it so the apex tip
  // sits exactly on the hit point (gizmo origin) with the base above it.
  cone.rotation.x = Math.PI;
  cone.position.y = CONE_HEIGHT / 2;
  cone.renderOrder = 999;
  gizmo.add(cone);

  gizmo.visible = false;
  scene.add(gizmo);

  console.log('✓ Gaze gizmo initialized');
  return gizmo;
}

/**
 * Skip the gizmo's own meshes and the invisible AABB collision helpers when
 * deciding what the gaze ray "hit".
 * @param {THREE.Object3D} object
 * @returns {boolean}
 */
function shouldIgnore(object) {
  let node = object;
  while (node) {
    if (node === gizmo) return true;
    if (node.name === 'gaze_gizmo') return true;
    if (node.name === 'aabb_helper') return true;
    node = node.parent;
  }
  return false;
}

/**
 * Walk up from a hit mesh to the nearest ancestor carrying a scene id.
 * Floor, walls, and loaded objects all set userData.id in index.js, while the
 * actual ray hit is usually a deep child mesh of a GLTF model.
 * @param {THREE.Object3D} object
 * @returns {THREE.Object3D|null}
 */
function findIdentifiedNode(object) {
  let node = object;
  while (node) {
    if (node.userData && node.userData.id) return node;
    node = node.parent;
  }
  return null;
}

/**
 * Classify a gaze hit into a JSON-friendly target.
 *   floor  → world point as-is
 *   wall   → world x/z, Y clamped to FLOOR_Y
 *   object → object id (plus point for reference)
 * @param {THREE.Intersection} hit
 * @returns {object}
 */
function resolveGazeTarget(hit) {
  const idNode = findIdentifiedNode(hit.object);
  const id = idNode ? idNode.userData.id : null;
  const p = hit.point;

  if (id && id.toLowerCase().startsWith('floor')) {
    return {
      type: 'floor',
      point: round2(p.x, p.y, p.z),
    };
  }

  if (id && id.toLowerCase().includes('wall')) {
    return {
      type: 'wall',
      wall_id: id,
      point: round2(p.x, FLOOR_Y, p.z),
    };
  }

  if (id) {
    return {
      type: 'object',
      object_id: id,
      point: round2(p.x, p.y, p.z),
    };
  }

  // Hit some geometry with no scene id (shouldn't normally happen).
  return {
    type: 'unknown',
    point: round2(p.x, p.y, p.z),
  };
}

/**
 * Round coordinates to 2 decimal places to keep payloads/logs compact —
 * sub-centimeter precision adds no value here and just bloats the LLM
 * prompt and WebSocket traffic.
 * @returns {{x: number, y: number, z: number}}
 */
function round2(x, y, z) {
  return {
    x: Math.round(x * 100) / 100,
    y: Math.round(y * 100) / 100,
    z: Math.round(z * 100) / 100,
  };
}

/**
 * Per-frame update: cast the head-gaze ray and reposition the cone.
 *
 * @param {THREE.Camera} camera    The (head) camera.
 * @param {THREE.Object3D[]} targetObjects  Meshes to test (e.g. loadedObjects values).
 *        Passing this explicit list keeps the ray from hitting the user avatar
 *        or controllers, which live under the camera/player rather than here.
 * @returns {THREE.Intersection|null} the chosen hit, or null when nothing valid.
 */
export function updateGazeGizmo(camera, targetObjects) {
  if (!raycaster || !gizmo) return null;
  if (!targetObjects || targetObjects.length === 0) {
    gizmo.visible = false;
    return null;
  }

  // Head position + forward direction in world space.
  camera.updateMatrixWorld();
  _origin.setFromMatrixPosition(camera.matrixWorld);
  camera.getWorldDirection(_direction);
  raycaster.set(_origin, _direction);

  const intersections = raycaster.intersectObjects(targetObjects, true);

  let hit = null;
  for (let i = 0; i < intersections.length; i++) {
    if (!shouldIgnore(intersections[i].object)) {
      hit = intersections[i];
      break;
    }
  }

  // Fallback: gazing at open space (e.g. up toward the open ceiling) hits
  // nothing → hide the marker and record an explicit "none" target.
  if (!hit) {
    gizmo.visible = false;
    latestTarget = { type: 'none' };
    return null;
  }

  // Place the cone at the hit point. Orientation is left at identity so the
  // cone stays ALWAYS UPRIGHT (world-Y axis), even on vertical walls.
  gizmo.position.copy(hit.point);
  gizmo.visible = true;

  // Cache the resolved target every frame so command-submit can read the
  // latest snapshot (parallels how user_position is sampled at submit time).
  const target = resolveGazeTarget(hit);
  latestTarget = target;

  // Every LOG_INTERVAL_MS, print the resolved (global) gaze target.
  const now = (typeof performance !== 'undefined' ? performance.now() : Date.now());
  if (now - lastLogTime >= LOG_INTERVAL_MS) {
    lastLogTime = now;
    if (target.type === 'object') {
      //console.log(`[Gaze] object → ${target.object_id}`, target.point);
    } else if (target.type === 'wall') {
      //console.log(`[Gaze] wall (${target.wall_id}) → Y clamped to ${FLOOR_Y}`, target.point);
    } else {
      //console.log(`[Gaze] ${target.type} →`, target.point);
    }
  }

  return hit;
}

/**
 * Get the most recent resolved gaze target snapshot.
 *
 * Always returns a well-formed object (never null). Shapes:
 *   { type: 'floor',  point: {x,y,z} }
 *   { type: 'wall',   wall_id, point: {x, y:FLOOR_Y, z} }
 *   { type: 'object', object_id, point: {x,y,z} }
 *   { type: 'none' }                              // gaze hit nothing
 *
 * Intended to be called once at command-submit time and sent alongside
 * user_position. The backend / LanguageAgent decides whether to USE it.
 * @returns {object}
 */
export function getLatestGazeTarget() {
  return latestTarget;
}

/**
 * Manually show/hide the gizmo (e.g. for future toggling). Visual-only.
 * @param {boolean} visible
 */
export function setGazeGizmoVisible(visible) {
  if (gizmo) gizmo.visible = visible;
}
