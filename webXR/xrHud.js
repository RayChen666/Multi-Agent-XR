/**
 * xrHud.js — In-headset UI (XR only): wrist-anchored command, status, help.
 *
 * - Right wrist: status + command draft (+ pip when collapsed)
 * - Left wrist: compact help card (left-grip toggle)
 * Public setters used by STT + controller bindings are unchanged.
 */

import * as THREE from 'three';
import { Text } from 'troika-three-text';

let hud = null;
let helpVisible = false;
/** XR session presenting — HUD system may attach/show. */
let sessionActive = false;
/** Right-wrist command/status revealed via gaze. */
let contentRevealed = false;

const GAZE_ENTER_MS = 180;
const GAZE_EXIT_MS = 400;
const WRIST_HIT_RADIUS = 0.14;
/** Quick opacity fade for gaze reveal / hide+ (ms). */
const FADE_MS = 180;

/** Shared local pose on a gripSpace: watch-face, readable when glancing at wrist. */
const WRIST_LOCAL_POS = new THREE.Vector3(0.0, 0.02, 0.07);
// No Y=π flip — that mirrored text/panels on the wrong face.
const WRIST_LOCAL_ROT = new THREE.Euler(-Math.PI / 2, 0, 0);

const HELP_BULLETS = [
  'Right index (T1): hold to talk, release to transcribe.',
  'Right grip (T0): hold 2s to Execute.',
  'Left index (T1): hold 2s to Clear.',
  'Left grip (T0): toggle this Help card.',
  'Right stick L/R: snap turn 45° (one turn per push).',
  'Left stick: snap-step in that direction.',
  'Press both sticks: reset to spawn.',
  'Look at a spot/object for “move it there.” Yellow marker = gaze.',
].map((line) => `• ${line}`).join('\n');

const _wristWorld = new THREE.Vector3();
const _camOrigin = new THREE.Vector3();
const _camDir = new THREE.Vector3();
const _toWrist = new THREE.Vector3();
const _closest = new THREE.Vector3();

let gazeEnterSince = null;
let gazeExitSince = null;
let lastRightGrip = null;
let lastLeftGrip = null;
/** 0–1 opacity factors for soft fade (content ↔ pip). */
let contentFade = 0;
let pipFade = 1;
let helpFade = 0;
let lastFadeTime = null;

function makePanel({ width, height, color, opacity = 0.9 }) {
  const plane = new THREE.Mesh(
    new THREE.PlaneGeometry(width, height),
    new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity,
      depthWrite: false,
      side: THREE.DoubleSide,
    }),
  );
  plane.renderOrder = 1100;
  return plane;
}

function makeLabel(initialText, fontSize, maxWidth, color = 0xffffff, anchorX = 'center') {
  const text = new Text();
  text.text = initialText;
  text.fontSize = fontSize;
  text.color = color;
  text.anchorX = anchorX;
  text.anchorY = 'middle';
  text.maxWidth = maxWidth;
  text.position.z = 0.002;
  text.renderOrder = 1101;
  text.sync();
  return text;
}

function applyWristPose(root) {
  root.position.copy(WRIST_LOCAL_POS);
  root.rotation.copy(WRIST_LOCAL_ROT);
}

/**
 * Build wrist HUD. Camera arg kept for call-site compatibility.
 * @param {THREE.Camera} [_camera]
 */
export function initXrHud(_camera) {
  if (hud) return hud;

  // —— Right wrist: main command + status ——
  const mainRoot = new THREE.Group();
  mainRoot.name = 'xr_hud_main_root';
  mainRoot.visible = false;
  applyWristPose(mainRoot);

  const statusGroup = new THREE.Group();
  statusGroup.name = 'xr_hud_status';
  statusGroup.position.set(0, 0.055, 0);

  const statusBg = makePanel({
    width: 0.18,
    height: 0.028,
    color: 0x37474f,
    opacity: 0.9,
  });
  const statusText = makeLabel('Ready', 0.011, 0.17);
  statusGroup.add(statusBg, statusText);

  const commandGroup = new THREE.Group();
  commandGroup.name = 'xr_hud_command';
  commandGroup.position.set(0, 0.01, 0);

  const commandBg = makePanel({
    width: 0.18,
    height: 0.055,
    color: 0x1a237e,
    opacity: 0.92,
  });
  const commandTitle = makeLabel('Command', 0.009, 0.17, 0xb0bec5);
  commandTitle.anchorY = 'top';
  commandTitle.position.set(0, 0.022, 0.003);

  const commandBody = makeLabel('(speak: right index)', 0.01, 0.16);
  commandBody.position.set(0, -0.005, 0.003);
  commandGroup.add(commandBg, commandTitle, commandBody);

  const contentGroup = new THREE.Group();
  contentGroup.name = 'xr_hud_content';
  contentGroup.visible = false;
  contentGroup.add(statusGroup, commandGroup);

  // Pip 1.5× previous size (was 0.04 × 0.016).
  const pipGroup = new THREE.Group();
  pipGroup.name = 'xr_hud_pip';
  pipGroup.position.set(0, 0.055, 0);

  const pipBg = makePanel({
    width: 0.06,
    height: 0.024,
    color: 0x455a64,
    opacity: 0.75,
  });
  const pipText = makeLabel('•', 0.015, 0.06, 0xb0bec5);
  pipGroup.add(pipBg, pipText);

  mainRoot.add(pipGroup, contentGroup);

  // —— Left wrist: help only ——
  const helpRoot = new THREE.Group();
  helpRoot.name = 'xr_hud_help_root';
  helpRoot.visible = false;
  applyWristPose(helpRoot);

  const helpGroup = new THREE.Group();
  helpGroup.name = 'xr_help_overlay';
  helpGroup.position.set(0, 0.02, 0);
  helpGroup.visible = false;

  const helpBg = makePanel({
    width: 0.2,
    height: 0.175,
    color: 0x1a1a2e,
    opacity: 0.94,
  });
  const helpTitle = makeLabel('Help', 0.012, 0.18);
  helpTitle.position.set(0, 0.072, 0.003);

  const helpBullets = makeLabel(HELP_BULLETS, 0.007, 0.185, 0xffffff, 'left');
  helpBullets.anchorY = 'top';
  helpBullets.position.set(-0.09, 0.058, 0.003);
  helpBullets.lineHeight = 1.22;

  const helpDismiss = makeLabel('Left grip again to close', 0.007, 0.18, 0xb0bec5);
  helpDismiss.position.set(0, -0.075, 0.003);

  helpGroup.add(helpBg, helpTitle, helpBullets, helpDismiss);
  helpRoot.add(helpGroup);

  hud = {
    mainRoot,
    helpRoot,
    contentGroup,
    pipGroup,
    pipText,
    commandBody,
    commandGroup,
    statusGroup,
    statusText,
    statusBg,
    helpGroup,
    helpTexts: [helpTitle, helpBullets, helpDismiss],
    holdProgress: 0,
    contentFadeTargets: collectFadeTargets(contentGroup),
    pipFadeTargets: collectFadeTargets(pipGroup),
    helpFadeTargets: collectFadeTargets(helpGroup),
  };

  contentFade = 0;
  pipFade = 1;
  helpFade = 0;
  lastFadeTime = null;
  applyRevealState();
  console.log('✓ XR HUD initialized (right=command, left=help)');
  return hud;
}

function collectFadeTargets(root) {
  const targets = [];
  root.traverse((obj) => {
    if (obj.isMesh && obj.material) {
      const mat = obj.material;
      if (!mat.transparent) return;
      if (mat.userData._hudBaseOpacity == null) {
        mat.userData._hudBaseOpacity = mat.opacity;
      }
      targets.push({ kind: 'mesh', material: mat });
    } else if (typeof obj.sync === 'function' && 'opacity' in obj) {
      if (obj.userData._hudBaseOpacity == null) {
        obj.userData._hudBaseOpacity = obj.opacity ?? 1;
      }
      targets.push({ kind: 'text', text: obj });
    }
  });
  return targets;
}

function applyFadeFactor(targets, factor) {
  const t = Math.min(Math.max(factor, 0), 1);
  for (const entry of targets) {
    if (entry.kind === 'mesh') {
      const base = entry.material.userData._hudBaseOpacity ?? 1;
      entry.material.opacity = base * t;
    } else if (entry.kind === 'text') {
      const base = entry.text.userData._hudBaseOpacity ?? 1;
      entry.text.opacity = base * t;
      entry.text.sync();
    }
  }
}

function lerpFade(current, target, dtMs) {
  if (FADE_MS <= 0) return target;
  const step = dtMs / FADE_MS;
  if (current < target) return Math.min(current + step, target);
  if (current > target) return Math.max(current - step, target);
  return current;
}

function tickFades() {
  if (!hud) return;

  const now = performance.now();
  const dt = lastFadeTime == null ? 16 : Math.min(now - lastFadeTime, 50);
  lastFadeTime = now;

  const wantContent = sessionActive && contentRevealed ? 1 : 0;
  const wantPip = sessionActive && !contentRevealed && Boolean(lastRightGrip) ? 1 : 0;
  const wantHelp = sessionActive && helpVisible && Boolean(lastLeftGrip) ? 1 : 0;

  contentFade = lerpFade(contentFade, wantContent, dt);
  pipFade = lerpFade(pipFade, wantPip, dt);
  helpFade = lerpFade(helpFade, wantHelp, dt);

  applyFadeFactor(hud.contentFadeTargets, contentFade);
  applyFadeFactor(hud.pipFadeTargets, pipFade);
  applyFadeFactor(hud.helpFadeTargets, helpFade);
}

function applyRevealState() {
  if (!hud) return;

  tickFades();

  // Keep nodes visible while fading out so opacity can animate.
  hud.contentGroup.visible = contentFade > 0.01;
  hud.pipGroup.visible = pipFade > 0.01;
  hud.mainRoot.visible = sessionActive && Boolean(lastRightGrip);

  hud.helpGroup.visible = helpFade > 0.01;
  hud.helpRoot.visible = sessionActive && Boolean(lastLeftGrip) && helpFade > 0.01;
}

function detachRoot(root) {
  if (root?.parent) {
    root.parent.remove(root);
  }
}

function detachAll() {
  if (!hud) return;
  detachRoot(hud.mainRoot);
  detachRoot(hud.helpRoot);
  lastRightGrip = null;
  lastLeftGrip = null;
}

function attachToGrip(root, grip, lastGripRef) {
  if (!root || !grip) return null;
  if (lastGripRef !== grip) {
    detachRoot(root);
    grip.add(root);
  }
  return grip;
}

function isLookingAtGrip(camera, grip) {
  if (!camera || !grip) return false;

  camera.updateMatrixWorld();
  grip.updateWorldMatrix(true, false);

  _camOrigin.setFromMatrixPosition(camera.matrixWorld);
  camera.getWorldDirection(_camDir);
  grip.getWorldPosition(_wristWorld);

  _toWrist.subVectors(_wristWorld, _camOrigin);
  const dist = _toWrist.length();
  if (dist < 0.05 || dist > 0.85) return false;

  _toWrist.multiplyScalar(1 / dist);
  const cosAngle = _camDir.dot(_toWrist);
  if (cosAngle < 0.95) return false;

  _closest.copy(_camOrigin).addScaledVector(_camDir, dist);
  return _closest.distanceTo(_wristWorld) <= WRIST_HIT_RADIUS;
}

/**
 * Per-frame: attach panels to wrists + gaze-reveal right-wrist content.
 * @param {{ camera?: THREE.Camera, controllers?: object, presenting?: boolean }} ctx
 */
export function updateXrHud({ camera, controllers, presenting } = {}) {
  if (!hud) return;

  if (!presenting) {
    sessionActive = false;
    contentRevealed = false;
    helpVisible = false;
    contentFade = 0;
    pipFade = 0;
    helpFade = 0;
    lastFadeTime = null;
    gazeEnterSince = null;
    gazeExitSince = null;
    detachAll();
    applyRevealState();
    return;
  }

  sessionActive = true;

  const rightGrip = controllers?.right?.gripSpace ?? null;
  const leftGrip = controllers?.left?.gripSpace ?? null;

  if (rightGrip) {
    lastRightGrip = attachToGrip(hud.mainRoot, rightGrip, lastRightGrip);
  } else {
    detachRoot(hud.mainRoot);
    lastRightGrip = null;
  }

  if (leftGrip) {
    lastLeftGrip = attachToGrip(hud.helpRoot, leftGrip, lastLeftGrip);
  } else {
    detachRoot(hud.helpRoot);
    lastLeftGrip = null;
  }

  // Gaze reveal only for the right-wrist command/status stack.
  const now = performance.now();
  const lookingRight = isLookingAtGrip(camera, lastRightGrip);

  if (lookingRight) {
    gazeExitSince = null;
    if (!contentRevealed) {
      if (gazeEnterSince === null) gazeEnterSince = now;
      if (now - gazeEnterSince >= GAZE_ENTER_MS) {
        contentRevealed = true;
        gazeEnterSince = null;
      }
    }
  } else {
    gazeEnterSince = null;
    if (contentRevealed) {
      if (gazeExitSince === null) gazeExitSince = now;
      if (now - gazeExitSince >= GAZE_EXIT_MS) {
        contentRevealed = false;
        gazeExitSince = null;
      }
    }
  }

  applyRevealState();
}

export function toggleXrHelpMenu() {
  helpVisible = !helpVisible;
  applyRevealState();
}

export function setXrHelpMenuVisible(visible) {
  helpVisible = visible;
  applyRevealState();
}

/**
 * Called by controller bindings when XR presenting toggles.
 * Actual panel visibility is wrist-gaze / help-toggle driven.
 */
export function setXrHudVisible(visible) {
  sessionActive = Boolean(visible);
  if (!visible) {
    contentRevealed = false;
    setXrHelpMenuVisible(false);
    gazeEnterSince = null;
    gazeExitSince = null;
    detachAll();
  }
  applyRevealState();
}

export function setXrCommandText(text) {
  if (!hud?.commandBody) return;
  const display = (text || '').trim();
  hud.commandBody.text = display || '(no command yet)';
  hud.commandBody.sync();
}

export function setXrStatusText(text) {
  if (!hud?.statusText) return;
  const label = text || 'Ready';
  hud.statusText.text = label;
  hud.statusText.sync();

  if (hud.pipText) {
    const short =
      label.startsWith('Listening') ? 'Listen'
        : label.startsWith('Processing') ? '…'
          : label.startsWith('Hold') ? 'Hold'
            : label.startsWith('Executing') ? 'Go'
              : '•';
    hud.pipText.text = short;
    hud.pipText.sync();
  }
}

/**
 * Visual hold progress for execute/clear (0–1). Resets to 0 when idle.
 */
export function setXrHoldProgress(progress) {
  if (!hud?.statusBg) return;
  hud.holdProgress = progress;
  if (progress <= 0) {
    hud.statusBg.material.color.setHex(0x37474f);
    return;
  }
  const t = Math.min(Math.max(progress, 0), 1);
  const base = new THREE.Color(0x37474f);
  const accent = new THREE.Color(0xff9800);
  hud.statusBg.material.color.copy(base).lerp(accent, t);
}

export function disposeXrHud() {
  if (!hud) return;
  detachAll();
  hud.commandBody?.dispose?.();
  hud.statusText?.dispose?.();
  hud.pipText?.dispose?.();
  for (const t of hud.helpTexts ?? []) {
    t?.dispose?.();
  }
  for (const root of [hud.mainRoot, hud.helpRoot]) {
    root.traverse((obj) => {
      if (obj.geometry) obj.geometry.dispose?.();
      if (obj.material) obj.material.dispose?.();
    });
  }
  hud = null;
  helpVisible = false;
  sessionActive = false;
  contentRevealed = false;
  contentFade = 0;
  pipFade = 1;
  helpFade = 0;
  lastFadeTime = null;
  gazeEnterSince = null;
  gazeExitSince = null;
}
