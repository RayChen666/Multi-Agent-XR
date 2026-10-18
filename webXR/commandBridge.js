/**
 * commandBridge.js
 * Unified execute/clear API for desktop panel, XR controllers, and HUD.
 * Reads draft text from sttListen; samples world camera pose + gaze at execute time.
 */

import * as THREE from 'three';

import { clearDraftCommand, getDraftCommand, setDraftCommand } from './sttListen.js';

let camera = null;
let getApiBaseUrl = () => `${window.location.protocol}//${window.location.hostname}:8000`;
let setStatus = () => {};
let getGazeTargetFn = () => ({ type: 'none' });

const _worldPos = new THREE.Vector3();
const _worldQuat = new THREE.Quaternion();
const _euler = new THREE.Euler();

export function initCommandBridge(opts) {
  camera = opts.camera ?? null;
  if (opts.getApiBaseUrl) getApiBaseUrl = opts.getApiBaseUrl;
  if (opts.setStatus) setStatus = opts.setStatus;
  if (opts.getGazeTarget) getGazeTargetFn = opts.getGazeTarget;
}

/**
 * World-space headset/camera position (accounts for player rig offset in XR).
 */
export function getUserPosition() {
  if (!camera) {
    return { x: 0, y: 0, z: 0 };
  }
  camera.getWorldPosition(_worldPos);
  return {
    x: _worldPos.x,
    y: _worldPos.y,
    z: _worldPos.z,
  };
}

/**
 * World-space headset yaw, in radians, using the backend's object convention
 * (backEnd/tools/orientation.py): rotation.y = θ faces (sin θ, cos θ) in XZ.
 * A three.js camera looks down its local -Z, so yaw = euler.y + π: the default
 * view toward -Z is π, turned to face -X is -π/2. Includes snap turn (player rig)
 * and desktop OrbitControls. 'YXZ' order keeps yaw stable when looking up/down.
 * Sent with every command as user_position.rotation (executeCommand).
 */
export function getUserRotation() {
  if (!camera) {
    return { x: 0, y: Math.PI, z: 0 };
  }
  camera.getWorldQuaternion(_worldQuat);
  _euler.setFromQuaternion(_worldQuat, 'YXZ');
  const yaw = Math.atan2(Math.sin(_euler.y + Math.PI), Math.cos(_euler.y + Math.PI));
  return { x: 0, y: yaw, z: 0 };
}

const RAD2DEG = 180 / Math.PI;
let poseWatchTimer = null;
let lastLoggedPose = null;

function logUserPose(reason) {
  const p = getUserPosition();
  const yaw = getUserRotation().y;
  console.log(
    `[UserPose] ${reason} | yaw ${(yaw * RAD2DEG).toFixed(1)}° (${yaw.toFixed(3)} rad)`
    + ` | pos (${p.x.toFixed(2)}, ${p.y.toFixed(2)}, ${p.z.toFixed(2)})`,
  );
  lastLoggedPose = { yaw, ...p };
}

/**
 * Debug: print the user's pose whenever yaw turns ≥ 2° or the head moves ≥ 5 cm
 * (checked every `intervalMs`). watchUserPose(false) stops it.
 */
export function watchUserPose(enable = true, intervalMs = 250) {
  clearInterval(poseWatchTimer);
  poseWatchTimer = null;
  if (!enable) {
    console.log('[UserPose] watch stopped');
    return;
  }
  logUserPose('watch started');
  poseWatchTimer = setInterval(() => {
    const p = getUserPosition();
    const yaw = getUserRotation().y;
    const last = lastLoggedPose;
    const turned = Math.abs(Math.atan2(Math.sin(yaw - last.yaw), Math.cos(yaw - last.yaw))) * RAD2DEG >= 2;
    const moved = Math.hypot(p.x - last.x, p.y - last.y, p.z - last.z) >= 0.05;
    if (turned || moved) logUserPose(turned ? 'turned' : 'moved');
  }, intervalMs);
}

export function getGazeSnapshot() {
  try {
    return getGazeTargetFn() ?? { type: 'none' };
  } catch (_) {
    return { type: 'none' };
  }
}

export async function executeCommand() {
  const command = getDraftCommand().trim();

  if (!command) {
    setStatus('Please enter a command', 'error');
    return { ok: false, reason: 'empty_command' };
  }

  setStatus(`⏳ Processing: "${command}"...`, 'processing');

  try {
    const response = await fetch(`${getApiBaseUrl()}/scene/command`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        command,
        // Pose at submit time: world position + yaw (backEnd main.py UserPose).
        user_position: { ...getUserPosition(), rotation: getUserRotation() },
        gaze: getGazeSnapshot(),
      }),
    });

    const result = await response.json().catch(() => ({}));

    if (response.ok && result.status === 'success') {
      setStatus(`✅ Success: ${command}`, 'success');
      clearDraftCommand();
      return { ok: true, result };
    }

    const message = result.message || 'Unknown error';
    setStatus(`❌ Failed: ${message}`, 'error');
    return { ok: false, reason: 'api_error', message };
  } catch (error) {
    console.error('Command execution error:', error);
    setStatus(`❌ Error: ${error.message}`, 'error');
    return { ok: false, reason: 'network_error', message: error.message };
  }
}

export function clearCommand() {
  clearDraftCommand();
  const statusDiv = document.getElementById('status');
  if (statusDiv) {
    statusDiv.className = '';
    statusDiv.textContent = '';
  }
}

export function fillExample(command) {
  setDraftCommand(command);
  document.getElementById('command-input')?.focus();
}
