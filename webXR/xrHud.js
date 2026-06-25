/**
 * xrHud.js — In-headset UI (XR only): command draft, status, help overlay.
 */

import * as THREE from 'three';
import { Text } from 'troika-three-text';

let hudRoot = null;
let helpVisible = false;

const HELP_BULLETS = [
  'Speak and control the room without looking at a screen. Keep your eyes on the scene when pointing at objects.',
  'Right hand — index trigger (T1): Hold to talk. Release when finished; your words are transcribed into the command field.',
  'Right hand — grip (T0): Hold for 2 seconds to Execute the current command.',
  'Left hand — index trigger (T1): Hold for 2 seconds to Clear the command.',
  'Left hand — grip (T0): Press to open or close this Help menu.',
  'Gaze: Look at a spot or object to ground commands like “move it there.” The yellow marker shows where you’re looking.',
].map((line) => `• ${line}`).join('\n');

function makePanel({ width, height, color, opacity = 0.9 }) {
  const plane = new THREE.Mesh(
    new THREE.PlaneGeometry(width, height),
    new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity,
      depthWrite: false,
    }),
  );
  plane.renderOrder = 1100;
  return plane;
}

function makeLabel(initialText, fontSize, maxWidth, color = 0xffffff) {
  const text = new Text();
  text.text = initialText;
  text.fontSize = fontSize;
  text.color = color;
  text.anchorX = 'center';
  text.anchorY = 'middle';
  text.maxWidth = maxWidth;
  text.position.z = 0.002;
  text.renderOrder = 1101;
  text.sync();
  return text;
}

/**
 * @param {THREE.Camera} camera
 */
export function initXrHud(camera) {
  if (hudRoot) return hudRoot;

  const root = new THREE.Group();
  root.name = 'xr_hud_root';
  root.visible = false;

  // —— Command draft (STT result) ——
  const commandGroup = new THREE.Group();
  commandGroup.position.set(0, -0.1, -0.58);

  const commandBg = makePanel({
    width: 0.52,
    height: 0.14,
    color: 0x1a237e,
    opacity: 0.92,
  });

  const commandTitle = makeLabel('Command', 0.022, 0.48);
  commandTitle.anchorY = 'top';
  commandTitle.position.set(0, 0.045, 0.003);

  const commandBody = makeLabel('(speak with right index trigger)', 0.024, 0.46);
  commandBody.position.set(0, -0.012, 0.003);

  commandGroup.add(commandBg, commandTitle, commandBody);

  // —— Status strip ——
  const statusGroup = new THREE.Group();
  statusGroup.position.set(0, -0.2, -0.58);

  const statusBg = makePanel({
    width: 0.52,
    height: 0.055,
    color: 0x37474f,
    opacity: 0.88,
  });

  const statusText = makeLabel('Ready', 0.022, 0.48);
  statusGroup.add(statusBg, statusText);

  // —— Help overlay (hidden until left grip T0) ——
  const helpGroup = new THREE.Group();
  helpGroup.name = 'xr_help_overlay';
  helpGroup.position.set(0, 0.04, -0.5);
  helpGroup.visible = false;

  const helpBg = makePanel({
    width: 0.74,
    height: 0.62,
    color: 0x1a1a2e,
    opacity: 0.94,
  });

  const helpTitle = makeLabel('Help Menu', 0.04, 0.68);
  helpTitle.position.set(0, 0.26, 0.003);

  const helpSubtitle = makeLabel('MAS-XR — Quick controls', 0.029, 0.68, 0xce93d8);
  helpSubtitle.position.set(0, 0.2, 0.003);

  const helpBullets = makeLabel(HELP_BULLETS, 0.02, 0.66);
  helpBullets.anchorX = 'left';
  helpBullets.anchorY = 'top';
  helpBullets.position.set(-0.32, 0.14, 0.003);
  helpBullets.lineHeight = 1.38;

  const helpTip = makeLabel(
    'Tip: After speaking, check your command on the panel before you execute. If something looks wrong, clear and try again.',
    0.019,
    0.66,
    0xffcc80,
  );
  helpTip.anchorX = 'left';
  helpTip.anchorY = 'top';
  helpTip.position.set(-0.32, -0.2, 0.003);
  helpTip.lineHeight = 1.35;

  const helpDismiss = makeLabel('Press left grip (T0) again to dismiss.', 0.02, 0.66, 0xb0bec5);
  helpDismiss.position.set(0, -0.27, 0.003);

  helpGroup.add(helpBg, helpTitle, helpSubtitle, helpBullets, helpTip, helpDismiss);

  root.add(commandGroup, statusGroup, helpGroup);
  camera.add(root);

  hudRoot = {
    root,
    commandBody,
    statusText,
    statusBg,
    helpGroup,
    helpTexts: [helpTitle, helpSubtitle, helpBullets, helpTip, helpDismiss],
    holdProgress: 0,
  };

  return hudRoot;
}

export function toggleXrHelpMenu() {
  helpVisible = !helpVisible;
  if (hudRoot?.helpGroup) {
    hudRoot.helpGroup.visible = helpVisible;
  }
}

export function setXrHelpMenuVisible(visible) {
  helpVisible = visible;
  if (hudRoot?.helpGroup) {
    hudRoot.helpGroup.visible = visible;
  }
}

export function setXrHudVisible(visible) {
  if (hudRoot?.root) {
    hudRoot.root.visible = visible;
  }
  if (!visible) {
    setXrHelpMenuVisible(false);
  }
}

export function setXrCommandText(text) {
  if (!hudRoot?.commandBody) return;
  const display = (text || '').trim();
  hudRoot.commandBody.text = display || '(no command yet)';
  hudRoot.commandBody.sync();
}

export function setXrStatusText(text) {
  if (!hudRoot?.statusText) return;
  hudRoot.statusText.text = text || 'Ready';
  hudRoot.statusText.sync();
}

/**
 * Visual hold progress for execute/clear (0–1). Resets to 0 when idle.
 */
export function setXrHoldProgress(progress) {
  if (!hudRoot?.statusBg) return;
  hudRoot.holdProgress = progress;
  if (progress <= 0) {
    hudRoot.statusBg.material.color.setHex(0x37474f);
    return;
  }
  const t = Math.min(Math.max(progress, 0), 1);
  const base = new THREE.Color(0x37474f);
  const accent = new THREE.Color(0xff9800);
  hudRoot.statusBg.material.color.copy(base).lerp(accent, t);
}

export function disposeXrHud() {
  if (!hudRoot) return;
  if (hudRoot.root.parent) {
    hudRoot.root.parent.remove(hudRoot.root);
  }
  hudRoot.commandBody?.dispose?.();
  hudRoot.statusText?.dispose?.();
  for (const t of hudRoot.helpTexts ?? []) {
    t?.dispose?.();
  }
  hudRoot.root.traverse((obj) => {
    if (obj.geometry) obj.geometry.dispose?.();
    if (obj.material) obj.material.dispose?.();
  });
  hudRoot = null;
  helpVisible = false;
}
