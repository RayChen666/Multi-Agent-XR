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
        user_position: getUserPosition(),
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
