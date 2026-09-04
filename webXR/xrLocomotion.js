/**
 * xrLocomotion.js — Study-friendly snap locomotion (thumbsticks only).
 *
 * Right stick X: 45° snap turn (once per deflection; hold does not spin)
 * Left stick:    snap-step 0.x m view-relative (once per deflection)
 * Both sticks:   click together → reset to spawn
 *
 * Does not touch HUD / STT / command bindings. Ignores sticks during PTT
 * and while execute/clear grips/triggers are held.
 */

/* eslint-disable sort-imports */
import * as THREE from 'three';
import { XR_AXES, XR_BUTTONS } from 'gamepad-wrapper';
import { HAPTIC, pulsePreset } from './xrHaptics.js';

const TURN_RADIANS = Math.PI / 4; // 45°
const STEP_METERS = 0.6;
const BOUNDS_INSET = 0.25;
const AXIS_ACTIVATE = 0.7;
const AXIS_RELEASE = 0.35;
const BUTTON_PRESS = 0.55;
/** Comfort blink: fade out → apply move → fade in (ms each half). */
const BLINK_HALF_MS = 70;

let player = null;
let camera = null;
let getBounds = () => null;
let getSpawn = () => null;
let getListenState = () => 'idle';

/** Camera-attached blackout quad for snap comfort blinks. */
let blinkMesh = null;
let blinkMat = null;
const blink = {
  active: false,
  phase: 'idle', // 'out' | 'in'
  t0: 0,
  pending: null, // () => void
};

const stickState = {
  rightTurn: { armed: true },
  leftStep: { armed: true },
  bothClick: { wasBoth: false },
};

const _forward = new THREE.Vector3();
const _right = new THREE.Vector3();
const _delta = new THREE.Vector3();

function ensureBlinkOverlay() {
  if (blinkMesh || !camera) return;

  blinkMat = new THREE.MeshBasicMaterial({
    color: 0x000000,
    transparent: true,
    opacity: 0,
    depthTest: false,
    depthWrite: false,
  });
  blinkMesh = new THREE.Mesh(
    new THREE.PlaneGeometry(2.2, 2.2),
    blinkMat,
  );
  blinkMesh.name = 'xr_locomotion_blink';
  blinkMesh.position.set(0, 0, -0.12);
  blinkMesh.renderOrder = 9999;
  blinkMesh.frustumCulled = false;
  blinkMesh.visible = false;
  camera.add(blinkMesh);
}

function queueBlink(action) {
  ensureBlinkOverlay();
  if (!blinkMesh || !blinkMat) {
    action?.();
    return;
  }

  // If a blink is mid-flight, apply any pending action immediately, then restart.
  if (blink.active && typeof blink.pending === 'function') {
    try { blink.pending(); } catch (_) { /* ignore */ }
  }

  blink.active = true;
  blink.phase = 'out';
  blink.t0 = performance.now();
  blink.pending = action;
  blinkMesh.visible = true;
  blinkMat.opacity = 0;
}

function tickBlink() {
  if (!blink.active || !blinkMat || !blinkMesh) return;

  const now = performance.now();
  const elapsed = now - blink.t0;
  const t = Math.min(Math.max(elapsed / BLINK_HALF_MS, 0), 1);

  if (blink.phase === 'out') {
    blinkMat.opacity = t;
    if (t >= 1) {
      if (typeof blink.pending === 'function') {
        try { blink.pending(); } catch (_) { /* ignore */ }
        blink.pending = null;
      }
      blink.phase = 'in';
      blink.t0 = now;
      blinkMat.opacity = 1;
    }
    return;
  }

  if (blink.phase === 'in') {
    blinkMat.opacity = 1 - t;
    if (t >= 1) {
      blinkMat.opacity = 0;
      blinkMesh.visible = false;
      blink.active = false;
      blink.phase = 'idle';
      blink.pending = null;
    }
  }
}

/**
 * @param {{
 *   player: THREE.Group,
 *   camera: THREE.Camera,
 *   getBounds: () => ({ min: {x,z}, max: {x,z} }|null),
 *   getSpawn: () => ({ x:number, y:number, z:number }|null),
 *   getListenState: () => 'idle'|'listening'|'processing',
 * }} opts
 */
export function initXrLocomotion(opts) {
  player = opts.player ?? null;
  camera = opts.camera ?? null;
  if (opts.getBounds) getBounds = opts.getBounds;
  if (opts.getSpawn) getSpawn = opts.getSpawn;
  if (opts.getListenState) getListenState = opts.getListenState;
  resetStickState();
  ensureBlinkOverlay();
  console.log('✓ XR locomotion ready (snap turn + snap step + blink)');
}

function resetStickState() {
  stickState.rightTurn.armed = true;
  stickState.leftStep.armed = true;
  stickState.bothClick.wasBoth = false;
}

function getAxis(gamepad, axisId) {
  if (!gamepad || typeof gamepad.getAxis !== 'function') return 0;
  const v = gamepad.getAxis(axisId);
  return typeof v === 'number' && Number.isFinite(v) ? v : 0;
}

function isPressed(gamepad, buttonId) {
  if (!gamepad) return false;
  if (typeof gamepad.getButton === 'function') {
    const btn = gamepad.getButton(buttonId);
    if (btn?.pressed) return true;
  }
  if (typeof gamepad.getButtonValue === 'function') {
    return gamepad.getButtonValue(buttonId) >= BUTTON_PRESS;
  }
  return false;
}

/**
 * Block locomotion during speech / command holds so sticks don't fight PTT.
 */
function isLocomotionLocked(controllers) {
  const listen = getListenState?.() ?? 'idle';
  if (listen === 'listening' || listen === 'processing') return true;

  const right = controllers?.right?.gamepad;
  const left = controllers?.left?.gamepad;
  // Right trigger = PTT; right squeeze = execute hold; left trigger = clear hold.
  if (isPressed(right, XR_BUTTONS.TRIGGER)) return true;
  if (isPressed(right, XR_BUTTONS.SQUEEZE)) return true;
  if (isPressed(left, XR_BUTTONS.TRIGGER)) return true;
  return false;
}

function isInsideBounds(x, z) {
  const bounds = getBounds?.();
  if (!bounds?.min || !bounds?.max) return true;
  const minX = bounds.min.x + BOUNDS_INSET;
  const maxX = bounds.max.x - BOUNDS_INSET;
  const minZ = bounds.min.z + BOUNDS_INSET;
  const maxZ = bounds.max.z - BOUNDS_INSET;
  return x >= minX && x <= maxX && z >= minZ && z <= maxZ;
}

function updateViewAxes() {
  if (!camera) {
    _forward.set(0, 0, -1);
    _right.set(1, 0, 0);
    return;
  }
  camera.getWorldDirection(_forward);
  _forward.y = 0;
  if (_forward.lengthSq() < 1e-6) {
    _forward.set(0, 0, -1);
  } else {
    _forward.normalize();
  }
  // Right = forward × up (Y-up). Using up × forward flips L/R.
  _right.set(-_forward.z, 0, _forward.x);
  if (_right.lengthSq() < 1e-6) {
    _right.set(1, 0, 0);
  } else {
    _right.normalize();
  }
}

function trySnapTurn(gamepad) {
  if (!player || !gamepad) return;

  const x = getAxis(gamepad, XR_AXES.THUMBSTICK_X);

  if (Math.abs(x) < AXIS_RELEASE) {
    stickState.rightTurn.armed = true;
    return;
  }

  if (!stickState.rightTurn.armed || Math.abs(x) < AXIS_ACTIVATE) return;

  stickState.rightTurn.armed = false;
  const sign = x > 0 ? -1 : 1; // stick right → turn right (negative Y in Three)
  queueBlink(() => {
    player.rotation.y += sign * TURN_RADIANS;
  });
  pulsePreset(gamepad, HAPTIC.helpToggle);
}

function trySnapStep(gamepad) {
  if (!player || !gamepad) return;

  const x = getAxis(gamepad, XR_AXES.THUMBSTICK_X);
  const y = getAxis(gamepad, XR_AXES.THUMBSTICK_Y);
  const mag = Math.hypot(x, y);

  if (mag < AXIS_RELEASE) {
    stickState.leftStep.armed = true;
    return;
  }

  if (!stickState.leftStep.armed || mag < AXIS_ACTIVATE) return;

  stickState.leftStep.armed = false;
  updateViewAxes();

  // Dominant axis → one cardinal step (view-relative).
  // WebXR thumbstick: Y- = forward, Y+ = back, X+ = right, X- = left.
  _delta.set(0, 0, 0);
  if (Math.abs(x) >= Math.abs(y)) {
    _delta.copy(_right).multiplyScalar(Math.sign(x) * STEP_METERS);
  } else {
    // Negate Y so stick-forward (negative Y) moves along head forward.
    _delta.copy(_forward).multiplyScalar(-Math.sign(y) * STEP_METERS);
  }

  const nextX = player.position.x + _delta.x;
  const nextZ = player.position.z + _delta.z;

  if (!isInsideBounds(nextX, nextZ)) {
    pulsePreset(gamepad, HAPTIC.deny);
    return;
  }

  queueBlink(() => {
    player.position.x = nextX;
    player.position.z = nextZ;
  });
  pulsePreset(gamepad, HAPTIC.helpToggle);
}

function tryResetToSpawn(leftPad, rightPad) {
  if (!player) return;

  const both =
    isPressed(leftPad, XR_BUTTONS.THUMBSTICK)
    && isPressed(rightPad, XR_BUTTONS.THUMBSTICK);

  if (!both) {
    stickState.bothClick.wasBoth = false;
    return;
  }

  if (stickState.bothClick.wasBoth) return;
  stickState.bothClick.wasBoth = true;

  const spawn = getSpawn?.();
  if (!spawn) {
    pulsePreset(leftPad, HAPTIC.deny);
    pulsePreset(rightPad, HAPTIC.deny);
    return;
  }

  queueBlink(() => {
    player.position.set(spawn.x, spawn.y, spawn.z);
    player.rotation.set(0, 0, 0);
  });
  pulsePreset(leftPad, HAPTIC.listenStart);
  pulsePreset(rightPad, HAPTIC.listenStart);
}

/**
 * Per-frame locomotion while presenting.
 * @param {{ renderer: THREE.WebGLRenderer, controllers: object }} ctx
 */
export function updateXrLocomotion({ renderer, controllers }) {
  const presenting = Boolean(renderer?.xr?.isPresenting);
  if (!presenting || !player) {
    resetStickState();
    if (blinkMesh) {
      blinkMesh.visible = false;
      if (blinkMat) blinkMat.opacity = 0;
      blink.active = false;
      blink.phase = 'idle';
      blink.pending = null;
    }
    return;
  }

  tickBlink();

  const leftPad = controllers?.left?.gamepad ?? null;
  const rightPad = controllers?.right?.gamepad ?? null;

  // Reset combo still available even if other sticks are locked? Prefer always
  // allow reset as an escape hatch during listening.
  tryResetToSpawn(leftPad, rightPad);

  if (isLocomotionLocked(controllers)) {
    // Keep arming logic from getting stuck after lock.
    if (rightPad && Math.abs(getAxis(rightPad, XR_AXES.THUMBSTICK_X)) < AXIS_RELEASE) {
      stickState.rightTurn.armed = true;
    }
    if (leftPad) {
      const lx = getAxis(leftPad, XR_AXES.THUMBSTICK_X);
      const ly = getAxis(leftPad, XR_AXES.THUMBSTICK_Y);
      if (Math.hypot(lx, ly) < AXIS_RELEASE) stickState.leftStep.armed = true;
    }
    return;
  }

  trySnapTurn(rightPad);
  trySnapStep(leftPad);
}

export function getLocomotionStepMeters() {
  return STEP_METERS;
}

export function disposeXrLocomotion() {
  if (blinkMesh) {
    if (blinkMesh.parent) blinkMesh.parent.remove(blinkMesh);
    blinkMesh.geometry?.dispose?.();
    blinkMat?.dispose?.();
  }
  blinkMesh = null;
  blinkMat = null;
  blink.active = false;
  blink.pending = null;
  player = null;
  camera = null;
  resetStickState();
}
