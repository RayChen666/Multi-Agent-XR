/**
 * xrHaptics.js — Lightweight XR controller vibration helpers.
 *
 * Safe no-op on PC / Immersive Web Emulator / missing actuators.
 * No audio — vibration only.
 */

/** @type {WeakMap<object, Gamepad>} */
const rawGamepadByWrapper = new WeakMap();

/**
 * Resolve the underlying Gamepad from a GamepadWrapper or raw pad.
 * @param {any} gamepadOrWrapper
 * @returns {Gamepad|null}
 */
function resolveRawGamepad(gamepadOrWrapper) {
  if (!gamepadOrWrapper) return null;

  if (Array.isArray(gamepadOrWrapper.hapticActuators)
    || typeof gamepadOrWrapper.vibrationActuator?.playEffect === 'function') {
    return gamepadOrWrapper;
  }

  if (gamepadOrWrapper.gamepad && gamepadOrWrapper.gamepad !== gamepadOrWrapper) {
    return gamepadOrWrapper.gamepad;
  }

  const cached = rawGamepadByWrapper.get(gamepadOrWrapper);
  if (cached) return cached;

  return null;
}

/**
 * Cache raw gamepad from XR "connected" payload when available.
 * Optional — pulse() still works via wrapper.gamepad.
 * @param {any} wrapper
 * @param {Gamepad} raw
 */
export function registerXrGamepad(wrapper, raw) {
  if (wrapper && raw) {
    rawGamepadByWrapper.set(wrapper, raw);
  }
}

/**
 * Fire a one-shot vibration if a real XR haptic actuator exists.
 * Returns false on PC/emulator/unsupported — never throws.
 *
 * @param {any} gamepadOrWrapper GamepadWrapper or Gamepad
 * @param {{ intensity?: number, durationMs?: number }} [opts]
 * @returns {boolean}
 */
export function pulse(gamepadOrWrapper, { intensity = 0.5, durationMs = 40 } = {}) {
  try {
    const pad = resolveRawGamepad(gamepadOrWrapper);
    if (!pad) return false;

    const strength = Math.min(Math.max(intensity, 0), 1);
    const duration = Math.max(1, durationMs | 0);

    const actuators = pad.hapticActuators;
    if (Array.isArray(actuators) && actuators.length > 0 && typeof actuators[0].pulse === 'function') {
      const result = actuators[0].pulse(strength, duration);
      if (result && typeof result.catch === 'function') {
        result.catch(() => {});
      }
      return true;
    }

    // Some browsers expose a single vibrationActuator (Gamepad Extensions).
    const actuator = pad.vibrationActuator;
    if (actuator && typeof actuator.playEffect === 'function') {
      const result = actuator.playEffect('dual-rumble', {
        startDelay: 0,
        duration,
        weakMagnitude: strength * 0.6,
        strongMagnitude: strength,
      });
      if (result && typeof result.catch === 'function') {
        result.catch(() => {});
      }
      return true;
    }

    return false;
  } catch (_) {
    return false;
  }
}

/** Preset cues used by controller bindings. */
export const HAPTIC = {
  listenStart: { intensity: 0.6, durationMs: 50 },
  listenStop: { intensity: 0.3, durationMs: 30 },
  holdHalf: { intensity: 0.35, durationMs: 30 },
  holdComplete: { intensity: 0.9, durationMs: 70 },
  helpToggle: { intensity: 0.25, durationMs: 25 },
  deny: { intensity: 0.2, durationMs: 40 },
};

export function pulsePreset(gamepadOrWrapper, preset) {
  return pulse(gamepadOrWrapper, preset || HAPTIC.listenStart);
}
