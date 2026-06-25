/**
 * STT listen session + shared draft command state.
 * Desktop #listen-btn uses the same API as future XR controller bindings.
 */

/** Ms of quiet before we treat the utterance as finished (5–10s natural pauses). */
const SILENCE_END_MS = 8000;

const IDLE_LABEL = 'Listen';
const LISTENING_LABEL = 'Listening…';

let config = null;
let sttInitialized = false;
let sttMode = 'webspeech';
let sttAvailable = false;

/** @type {'idle'|'listening'|'processing'} */
let listenState = 'idle';
let draftCommand = '';
const draftListeners = new Set();
const listenStateListeners = new Set();

let recognition = null;
let listeningSession = false;
let userEndedSession = false;
let silenceTimer = null;
let cleanupInFlight = false;
let lastCombined = '';
let mediaRecorder = null;
let mediaStream = null;
let recordedChunks = [];
let recordMimeType = '';
let maxRecordTimer = null;
let pttMode = false;

function getSpeechRecognitionCtor() {
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

function setListenState(state) {
  listenState = state;

  for (const listener of listenStateListeners) {
    try {
      listener(state);
    } catch (err) {
      console.warn('onListenStateChange listener error:', err);
    }
  }

  const btn = document.getElementById('listen-btn');
  if (!btn) return;

  const listening = state === 'listening';
  btn.dataset.listening = listening ? '1' : '0';
  btn.textContent = listening ? LISTENING_LABEL : IDLE_LABEL;
  btn.classList.toggle('listening', listening);
  btn.setAttribute('aria-pressed', listening ? 'true' : 'false');
}

function syncDraftToDom() {
  const input = document.getElementById('command-input');
  if (input && input.value !== draftCommand) {
    input.value = draftCommand;
  }
}

function notifyDraftChange() {
  syncDraftToDom();
  for (const listener of draftListeners) {
    try {
      listener(draftCommand);
    } catch (err) {
      console.warn('onDraftChange listener error:', err);
    }
  }
}

export function getDraftCommand() {
  return draftCommand;
}

export function setDraftCommand(text) {
  draftCommand = (text ?? '').toString();
  notifyDraftChange();
}

export function clearDraftCommand() {
  setDraftCommand('');
}

export function onDraftChange(callback) {
  draftListeners.add(callback);
  return () => draftListeners.delete(callback);
}

export function onListenStateChange(callback) {
  listenStateListeners.add(callback);
  return () => listenStateListeners.delete(callback);
}

export function getListenState() {
  return listenState;
}

function buildTranscriptFromResults(results) {
  let final = '';
  let interim = '';
  for (let i = 0; i < results.length; i += 1) {
    const r = results[i];
    const piece = r[0] ? r[0].transcript : '';
    if (r.isFinal) final += piece;
    else interim += piece;
  }
  return (final + interim).trim();
}

function clearSilenceTimer() {
  if (silenceTimer !== null) {
    clearTimeout(silenceTimer);
    silenceTimer = null;
  }
}

function armSilenceTimer() {
  if (pttMode) return;
  clearSilenceTimer();
  silenceTimer = setTimeout(() => {
    silenceTimer = null;
    finishListeningAndCleanup('silence');
  }, SILENCE_END_MS);
}

function clearMaxRecordTimer() {
  if (maxRecordTimer !== null) {
    clearTimeout(maxRecordTimer);
    maxRecordTimer = null;
  }
}

function pickRecorderMimeType() {
  if (typeof MediaRecorder === 'undefined' || typeof MediaRecorder.isTypeSupported !== 'function') {
    return '';
  }
  const preferred = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/ogg',
    'audio/mp4',
  ];
  for (const t of preferred) {
    if (MediaRecorder.isTypeSupported(t)) return t;
  }
  return '';
}

function stopMediaStreamTracks() {
  if (mediaStream) {
    for (const track of mediaStream.getTracks()) {
      try {
        track.stop();
      } catch (_) {
        /* ignore */
      }
    }
    mediaStream = null;
  }
}

async function runCleanupPost(raw) {
  if (cleanupInFlight) return;
  cleanupInFlight = true;
  setListenState('processing');
  clearSilenceTimer();

  const trimmed = (raw || '').trim();
  if (!trimmed) {
    cleanupInFlight = false;
    setListenState('idle');
    config?.setStatus('No speech captured; try Listen again', 'error');
    return;
  }

  config?.setStatus('Cleaning up transcript…', 'processing');
  const base = config.getApiBaseUrl();

  try {
    const res = await fetch(`${base}/stt/cleanup`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transcript: trimmed }),
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      const detail = data.detail || data.message || res.statusText;
      config?.setStatus(typeof detail === 'string' ? detail : 'Cleanup failed', 'error');
      return;
    }

    const cleaned = (data.cleaned_text || '').trim();
    if (!cleaned) {
      config?.setStatus('Cleanup returned empty text', 'error');
      return;
    }

    setDraftCommand(cleaned);
    config?.setStatus('Edit if needed, then Execute or Clear', 'success');
  } catch (err) {
    console.error('STT cleanup fetch error:', err);
    config?.setStatus(`Network error: ${err.message || err}`, 'error');
  } finally {
    cleanupInFlight = false;
    setListenState('idle');
  }
}

async function runTranscribeAndCleanup(audioBlob) {
  if (cleanupInFlight) return;

  cleanupInFlight = true;
  setListenState('processing');
  clearSilenceTimer();
  clearMaxRecordTimer();

  if (!audioBlob || audioBlob.size === 0) {
    cleanupInFlight = false;
    setListenState('idle');
    config?.setStatus('No audio captured; try Listen again', 'error');
    return;
  }

  config?.setStatus('Uploading audio for transcription…', 'processing');
  const base = config.getApiBaseUrl();

  try {
    const ext = audioBlob.type.includes('ogg') ? 'ogg' : 'webm';
    const formData = new FormData();
    formData.append('audio', audioBlob, `voice_input.${ext}`);

    const trRes = await fetch(`${base}/stt/transcribe`, {
      method: 'POST',
      body: formData,
    });

    const trData = await trRes.json().catch(() => ({}));

    if (!trRes.ok) {
      const detail = trData.detail || trData.message || trRes.statusText;
      config?.setStatus(typeof detail === 'string' ? detail : 'Audio transcription failed', 'error');
      return;
    }

    const transcript = (trData.transcript || '').trim();
    if (!transcript) {
      config?.setStatus('Transcription returned empty text', 'error');
      return;
    }

    config?.setStatus('Cleaning up transcript…', 'processing');
    const cleanRes = await fetch(`${base}/stt/cleanup`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transcript }),
    });

    const cleanData = await cleanRes.json().catch(() => ({}));
    if (!cleanRes.ok) {
      const detail = cleanData.detail || cleanData.message || cleanRes.statusText;
      config?.setStatus(typeof detail === 'string' ? detail : 'Cleanup failed', 'error');
      return;
    }

    const cleaned = (cleanData.cleaned_text || '').trim();
    if (!cleaned) {
      config?.setStatus('Cleanup returned empty text', 'error');
      return;
    }

    setDraftCommand(cleaned);
    config?.setStatus('Edit if needed, then Execute or Clear', 'success');
  } catch (err) {
    console.error('STT transcribe/cleanup error:', err);
    config?.setStatus(`Network error: ${err.message || err}`, 'error');
  } finally {
    cleanupInFlight = false;
    setListenState('idle');
  }
}

function finishListeningAndCleanup(reason) {
  userEndedSession = true;
  listeningSession = false;
  clearSilenceTimer();
  setListenState('idle');

  try {
    if (recognition) recognition.stop();
  } catch (_) {
    /* ignore */
  }

  const toSend = lastCombined.trim();
  if (toSend) {
    runCleanupPost(toSend);
  } else if (reason === 'manual') {
    config?.setStatus('Nothing heard yet; try again', 'error');
  }
}

async function startRecorderSession() {
  const hasRecorderFallback =
    typeof navigator !== 'undefined'
    && navigator.mediaDevices
    && typeof navigator.mediaDevices.getUserMedia === 'function'
    && typeof MediaRecorder !== 'undefined';

  if (!hasRecorderFallback) {
    config?.setStatus('Audio recorder fallback not available', 'error');
    return false;
  }

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recordMimeType = pickRecorderMimeType();

    mediaRecorder = recordMimeType
      ? new MediaRecorder(mediaStream, { mimeType: recordMimeType })
      : new MediaRecorder(mediaStream);

    recordedChunks = [];
    mediaRecorder.ondataavailable = (ev) => {
      if (ev.data && ev.data.size > 0) {
        recordedChunks.push(ev.data);
      }
    };

    mediaRecorder.onerror = (ev) => {
      const errName = ev?.error?.name || 'unknown';
      console.error('MediaRecorder error:', ev?.error || ev);
      config?.setStatus(`Recorder error: ${errName}`, 'error');
      listeningSession = false;
      setListenState('idle');
      clearMaxRecordTimer();
      stopMediaStreamTracks();
    };

    mediaRecorder.onstop = () => {
      const type = recordMimeType || mediaRecorder?.mimeType || 'audio/webm';
      const blob = new Blob(recordedChunks, { type });
      recordedChunks = [];
      listeningSession = false;
      clearMaxRecordTimer();
      stopMediaStreamTracks();
      runTranscribeAndCleanup(blob);
    };

    mediaRecorder.start(250);
    listeningSession = true;
    setListenState('listening');
    config?.setStatus('Recording voice… Release Listen to stop and transcribe', 'processing');

    clearMaxRecordTimer();
    maxRecordTimer = setTimeout(() => {
      if (mediaRecorder && mediaRecorder.state === 'recording') {
        try {
          mediaRecorder.stop();
        } catch (_) {
          /* ignore */
        }
      }
    }, 30000);

    return true;
  } catch (err) {
    console.error('getUserMedia / MediaRecorder start failed:', err);
    const msg = err?.name === 'NotAllowedError'
      ? 'Microphone permission denied'
      : (err?.message || 'Could not start microphone');
    config?.setStatus(msg, 'error');
    listeningSession = false;
    setListenState('idle');
    clearMaxRecordTimer();
    stopMediaStreamTracks();
    return false;
  }
}

function stopRecorderSession() {
  if (!mediaRecorder) {
    config?.setStatus('Recorder is not active', 'error');
    return false;
  }
  if (mediaRecorder.state === 'recording') {
    try {
      setListenState('idle');
      mediaRecorder.stop();
      return true;
    } catch (err) {
      console.error('mediaRecorder.stop failed:', err);
      config?.setStatus('Could not stop recording', 'error');
      listeningSession = false;
      setListenState('idle');
      clearMaxRecordTimer();
      stopMediaStreamTracks();
      return false;
    }
  }
  return false;
}

function beginWebSpeechSession() {
  const SR = getSpeechRecognitionCtor();
  if (!SR) return false;

  recognition = new SR();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = 'en-US';

  userEndedSession = false;
  lastCombined = '';
  cleanupInFlight = false;
  clearSilenceTimer();

  recognition.onstart = () => {
    listeningSession = true;
    setListenState('listening');
    config?.setStatus(
      `Listening… (up to ${SILENCE_END_MS / 1000}s pauses OK; release when done)`,
      'processing',
    );
  };

  recognition.onerror = (ev) => {
    clearSilenceTimer();
    listeningSession = false;
    setListenState('idle');
    userEndedSession = true;

    if (ev.error === 'aborted') return;

    const msg =
      ev.error === 'not-allowed'
        ? 'Microphone permission denied'
        : `Speech error: ${ev.error || 'unknown'}`;
    config?.setStatus(msg, 'error');
  };

  recognition.onend = () => {
    if (userEndedSession) {
      listeningSession = false;
      if (listenState === 'listening') setListenState('idle');
      return;
    }
    if (listeningSession && recognition && !cleanupInFlight) {
      try {
        recognition.start();
      } catch (e) {
        listeningSession = false;
        setListenState('idle');
        console.warn('STT restart after onend failed:', e);
      }
    }
  };

  recognition.onresult = (event) => {
    lastCombined = buildTranscriptFromResults(event.results);
    armSilenceTimer();
  };

  try {
    recognition.start();
    return true;
  } catch (err) {
    console.error('recognition.start failed:', err);
    config?.setStatus('Could not start microphone', 'error');
    setListenState('idle');
    listeningSession = false;
    return false;
  }
}

/**
 * Start listening (hold semantics for XR; toggle start on desktop).
 * @returns {{ ok: boolean, reason?: string }}
 */
export async function startListen({ ptt = false } = {}) {
  if (!sttInitialized || !sttAvailable) {
    return { ok: false, reason: 'not_available' };
  }
  if (listenState === 'processing') {
    return { ok: false, reason: 'processing' };
  }
  if (listenState === 'listening') {
    return { ok: true, reason: 'already_listening' };
  }

  pttMode = ptt;
  clearSilenceTimer();

  if (sttMode === 'audio-upload') {
    const started = await startRecorderSession();
    return started ? { ok: true } : { ok: false, reason: 'recorder_failed' };
  }

  const started = beginWebSpeechSession();
  return started ? { ok: true } : { ok: false, reason: 'webspeech_failed' };
}

/**
 * Stop listening and run STT cleanup when audio was captured.
 * @returns {{ ok: boolean, reason?: string }}
 */
export function stopListen() {
  if (!sttInitialized || !sttAvailable) {
    return { ok: false, reason: 'not_available' };
  }
  if (listenState === 'processing') {
    return { ok: false, reason: 'processing' };
  }
  if (listenState !== 'listening') {
    return { ok: false, reason: 'not_listening' };
  }

  if (sttMode === 'audio-upload') {
    pttMode = false;
    return stopRecorderSession()
      ? { ok: true }
      : { ok: false, reason: 'recorder_stop_failed' };
  }

  finishListeningAndCleanup('manual');
  pttMode = false;
  return { ok: true };
}

/**
 * @param {{ getApiBaseUrl: () => string, setStatus: (msg: string, type: string) => void }} opts
 */
let sttPhase1Attached = false;

export function initSttPhase1(opts) {
  if (sttPhase1Attached) {
    console.warn('STT Phase 1: init already attached, skipping duplicate');
    return;
  }

  config = opts;
  const btn = document.getElementById('listen-btn');
  const input = document.getElementById('command-input');
  if (!btn || !input) {
    console.warn('STT Phase 1: listen button or command input not found');
    return;
  }

  sttPhase1Attached = true;
  sttInitialized = true;

  const SR = getSpeechRecognitionCtor();
  const hasWebSpeech = Boolean(SR);
  const hasRecorderFallback =
    typeof navigator !== 'undefined'
    && navigator.mediaDevices
    && typeof navigator.mediaDevices.getUserMedia === 'function'
    && typeof MediaRecorder !== 'undefined';

  if (!hasWebSpeech && !hasRecorderFallback) {
    sttAvailable = false;
    btn.disabled = true;
    btn.title = 'Speech recognition is not available in this browser';
    config.setStatus('Speech recognition unavailable on this device/browser', 'error');
    return;
  }

  sttAvailable = true;
  sttMode = hasWebSpeech ? 'webspeech' : 'audio-upload';

  if (input.value.trim()) {
    setDraftCommand(input.value);
  }

  input.addEventListener('input', () => {
    draftCommand = input.value;
    for (const listener of draftListeners) {
      try {
        listener(draftCommand);
      } catch (err) {
        console.warn('onDraftChange listener error:', err);
      }
    }
  });

  btn.addEventListener('click', (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    if (btn.disabled) return;

    if (listenState === 'listening') {
      stopListen();
    } else {
      startListen();
    }
  });

  if (sttMode === 'audio-upload') {
    config.setStatus('Server STT fallback ready on this browser', 'processing');
  }
}
