/**
 * xrControllerBindings.js
 * XR controller actions:
 *   Right T1 (index)  — hold PTT listen, release → STT process
 *   Right T0 (thumb)  — hold 2s → execute
 *   Left T1 (index)   — hold 2s → clear draft
 *   Left T0 (thumb)   — press → toggle help overlay
 *
 * Haptics: vibration-only secondary feedback (no-op on PC/emulator).
 */

import { HAPTIC, pulsePreset } from './xrHaptics.js';
import {
  setXrHoldProgress,
  setXrHudVisible,
  setXrStatusText,
  toggleXrHelpMenu,
} from './xrHud.js';
import { XR_BUTTONS } from 'gamepad-wrapper';

const TRIGGER_THRESHOLD = 0.55;
export const HOLD_DURATION_MS = 2000;

/** @type {import('./xrControllerBindings.js').XrActionDeps | null} */
let actions = null;

const bindingState = {
  right_trigger_index: { wasDown: false },
  right_trigger_thumb: { wasDown: false, detector: null, firedHalf: false },
  left_trigger_index: { wasDown: false, detector: null, firedHalf: false },
  left_trigger_thumb: { wasDown: false },
};

/**
 * @typedef {object} XrActionDeps
 * @property {() => Promise<{ok: boolean}>|{ok: boolean}} startListen
 * @property {() => {ok: boolean}} stopListen
 * @property {() => Promise<{ok: boolean}>} executeCommand
 * @property {() => void} clearCommand
 * @property {() => 'idle'|'listening'|'processing'} getListenState
 */

/**
 * Reusable hold detector for execute / clear.
 */
export class HoldDetector {
  constructor(durationMs, onComplete) {
    this.durationMs = durationMs;
    this.onComplete = onComplete;
    this.pressStart = null;
    this.fired = false;
  }

  update(pressed, now = performance.now()) {
    if (!pressed) {
      this.pressStart = null;
      this.fired = false;
      return { holding: false, progress: 0, completed: false };
    }

    if (this.pressStart === null) {
      this.pressStart = now;
    }

    const elapsed = now - this.pressStart;
    const progress = Math.min(elapsed / this.durationMs, 1);
    let completed = false;

    if (!this.fired && elapsed >= this.durationMs) {
      this.fired = true;
      completed = true;
      this.onComplete?.();
    }

    return { holding: true, progress, completed };
  }
}

function isButtonPressed(gamepad, button) {
  if (!gamepad) return false;

  if (typeof gamepad.getButton === 'function') {
    const btn = gamepad.getButton(button);
    if (btn?.pressed) return true;
  }

  if (typeof gamepad.getButtonValue === 'function') {
    return gamepad.getButtonValue(button) >= TRIGGER_THRESHOLD;
  }

  return false;
}

function isBusy() {
  const state = actions?.getListenState?.();
  return state === 'listening' || state === 'processing';
}

function resetBindingState() {
  for (const key of Object.keys(bindingState)) {
    bindingState[key].wasDown = false;
    bindingState[key].firedHalf = false;
    bindingState[key].detector?.update(false);
  }
  setXrHoldProgress(0);
}

/**
 * Wire STT / execute / clear into controller bindings.
 * @param {XrActionDeps} deps
 */
export function wireXrControllerActions(deps) {
  actions = deps;

  bindingState.right_trigger_thumb.detector = new HoldDetector(HOLD_DURATION_MS, () => {
    if (isBusy()) {
      setXrStatusText('Wait — still processing speech');
      return;
    }
    setXrStatusText('Executing command…');
    actions.executeCommand().then((result) => {
      if (result?.ok) {
        setXrStatusText('Command sent');
      } else if (actions.getListenState() === 'idle') {
        setXrStatusText('Ready');
      }
    });
  });

  bindingState.left_trigger_index.detector = new HoldDetector(HOLD_DURATION_MS, () => {
    if (isBusy()) {
      setXrStatusText('Wait — still processing speech');
      return;
    }
    actions.clearCommand();
    setXrStatusText('Command cleared');
  });
}

export function initXrControllerBindings() {
  resetBindingState();
}

function onRightIndexDown(gamepad) {
  if (!actions) return;
  if (isBusy()) {
    setXrStatusText('Busy — wait for processing to finish');
    pulsePreset(gamepad, HAPTIC.deny);
    return;
  }
  setXrStatusText('Listening… (release to transcribe)');
  pulsePreset(gamepad, HAPTIC.listenStart);
  void actions.startListen();
}

function onRightIndexUp(gamepad) {
  if (!actions) return;
  const result = actions.stopListen();
  if (result?.ok) {
    pulsePreset(gamepad, HAPTIC.listenStop);
    setXrStatusText('Processing speech…');
  } else if (actions.getListenState() === 'idle') {
    setXrStatusText('Ready');
  }
}

function pollIndexBinding(hand, button, state, onDown, onUp) {
  return (controllers) => {
    const gamepad = controllers?.[hand]?.gamepad;
    if (!gamepad) return;

    const pressed = isButtonPressed(gamepad, button);

    if (pressed && !state.wasDown) {
      onDown(gamepad);
    } else if (!pressed && state.wasDown) {
      onUp(gamepad);
    }

    state.wasDown = pressed;
  };
}

function pollToggleBinding(hand, button, state, onToggle) {
  return (controllers) => {
    const gamepad = controllers?.[hand]?.gamepad;
    if (!gamepad) return;

    const pressed = isButtonPressed(gamepad, button);

    if (pressed && !state.wasDown) {
      onToggle(gamepad);
    }

    state.wasDown = pressed;
  };
}

function pollHoldBinding(hand, button, state, statusWhileHolding) {
  return (controllers) => {
    const gamepad = controllers?.[hand]?.gamepad;
    if (!gamepad || !state.detector) return;

    const pressed = isButtonPressed(gamepad, button);
    const { holding, progress, completed } = state.detector.update(pressed);

    if (!pressed) {
      state.firedHalf = false;
    }

    if (holding && !isBusy()) {
      setXrHoldProgress(progress);
      setXrStatusText(`${statusWhileHolding} ${Math.round(progress * 100)}%`);

      // Milestone pulses: ~50% tick, then stronger pulse at complete.
      if (!state.firedHalf && progress >= 0.5) {
        state.firedHalf = true;
        pulsePreset(gamepad, HAPTIC.holdHalf);
      }
      if (completed) {
        pulsePreset(gamepad, HAPTIC.holdComplete);
      }
    } else if (!holding && state.wasDown) {
      setXrHoldProgress(0);
      if (!isBusy() && actions?.getListenState?.() === 'idle') {
        setXrStatusText('Ready');
      }
    } else if (holding && isBusy() && completed) {
      pulsePreset(gamepad, HAPTIC.deny);
    }

    state.wasDown = pressed;
  };
}

/**
 * Poll controllers each frame while in XR.
 * @param {{ renderer: THREE.WebGLRenderer, controllers: object }} ctx
 */
export function updateXrControllerBindings({ renderer, controllers }) {
  const presenting = Boolean(renderer?.xr?.isPresenting);
  setXrHudVisible(presenting);

  if (!presenting) {
    resetBindingState();
    return;
  }

  if (!actions) return;

  pollIndexBinding(
    'right',
    XR_BUTTONS.TRIGGER,
    bindingState.right_trigger_index,
    onRightIndexDown,
    onRightIndexUp,
  )(controllers);

  pollHoldBinding(
    'right',
    XR_BUTTONS.SQUEEZE,
    bindingState.right_trigger_thumb,
    'Hold to execute',
  )(controllers);

  pollHoldBinding(
    'left',
    XR_BUTTONS.TRIGGER,
    bindingState.left_trigger_index,
    'Hold to clear',
  )(controllers);

  pollToggleBinding(
    'left',
    XR_BUTTONS.SQUEEZE,
    bindingState.left_trigger_thumb,
    (gamepad) => {
      pulsePreset(gamepad, HAPTIC.helpToggle);
      toggleXrHelpMenu();
    },
  )(controllers);
}

export function getHoldDurationMs() {
  return HOLD_DURATION_MS;
}

export function disposeXrControllerBindings() {
  actions = null;
  resetBindingState();
}
