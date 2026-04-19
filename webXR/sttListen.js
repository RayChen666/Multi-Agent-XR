/**
 * Phase 1: Web Speech API (Chromium) + backend /stt/cleanup.
 * Fills #command-input with cleaned text only; raw transcript is not stored in UI.
 *
 * Web Speech has no standard "silence length" knob. We use continuous + interim
 * results and only run cleanup after SILENCE_END_MS with no new audio (resets on
 * every partial/final result). Second click on Listen ends early and runs cleanup.
 */

/** Ms of quiet before we treat the utterance as finished (5–10s natural pauses). */
const SILENCE_END_MS = 8000;

const IDLE_LABEL = 'Listen';
const LISTENING_LABEL = 'Listening…';

function getSpeechRecognitionCtor() {
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

function setListenButtonState(listening) {
  const btn = document.getElementById('listen-btn');
  if (!btn) return;

  btn.dataset.listening = listening ? '1' : '0';
  btn.textContent = listening ? LISTENING_LABEL : IDLE_LABEL;
  btn.classList.toggle('listening', listening);
  btn.setAttribute('aria-pressed', listening ? 'true' : 'false');
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

/**
 * @param {{ getApiBaseUrl: () => string, setStatus: (msg: string, type: string) => void }} opts
 */
let sttPhase1Attached = false;

export function initSttPhase1(opts) {
  if (sttPhase1Attached) {
    console.warn('STT Phase 1: init already attached, skipping duplicate');
    return;
  }

  const { getApiBaseUrl, setStatus } = opts;
  const btn = document.getElementById('listen-btn');
  const input = document.getElementById('command-input');
  if (!btn || !input) {
    console.warn('STT Phase 1: listen button or command input not found');
    return;
  }

  sttPhase1Attached = true;

  const SR = getSpeechRecognitionCtor();
  if (!SR) {
    btn.disabled = true;
    btn.title = 'Speech recognition not supported in this browser';
    setStatus('Speech recognition unavailable (use Chromium)', 'error');
    return;
  }

  let recognition = null;
  let listeningSession = false;
  let userEndedSession = false;
  let silenceTimer = null;
  let cleanupInFlight = false;
  let lastCombined = '';

  function clearSilenceTimer() {
    if (silenceTimer !== null) {
      clearTimeout(silenceTimer);
      silenceTimer = null;
    }
  }

  function armSilenceTimer() {
    clearSilenceTimer();
    silenceTimer = setTimeout(() => {
      silenceTimer = null;
      finishListeningAndCleanup('silence');
    }, SILENCE_END_MS);
  }

  async function runCleanupPost(raw) {
    if (cleanupInFlight) return;
    cleanupInFlight = true;
    clearSilenceTimer();

    const trimmed = (raw || '').trim();
    if (!trimmed) {
      cleanupInFlight = false;
      setStatus('No speech captured; try Listen again', 'error');
      return;
    }

    setStatus('Cleaning up transcript…', 'processing');
    const base = getApiBaseUrl();

    try {
      const res = await fetch(`${base}/stt/cleanup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transcript: trimmed }),
      });

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        const detail = data.detail || data.message || res.statusText;
        setStatus(typeof detail === 'string' ? detail : 'Cleanup failed', 'error');
        cleanupInFlight = false;
        return;
      }

      const cleaned = (data.cleaned_text || '').trim();
      if (!cleaned) {
        setStatus('Cleanup returned empty text', 'error');
        cleanupInFlight = false;
        return;
      }

      input.value = cleaned;
      setStatus('Edit if needed, then Execute or Clear', 'success');
    } catch (err) {
      console.error('STT cleanup fetch error:', err);
      setStatus(`Network error: ${err.message || err}`, 'error');
    } finally {
      cleanupInFlight = false;
    }
  }

  function finishListeningAndCleanup(reason) {
    userEndedSession = true;
    listeningSession = false;
    clearSilenceTimer();
    setListenButtonState(false);

    try {
      if (recognition) recognition.stop();
    } catch (_) {
      /* ignore */
    }

    const toSend = lastCombined.trim();
    if (toSend) {
      runCleanupPost(toSend);
    } else if (reason === 'manual') {
      setStatus('Nothing heard yet; try again', 'error');
    }
  }

  btn.addEventListener('click', (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    if (btn.disabled) return;

    if (listeningSession && recognition) {
      finishListeningAndCleanup('manual');
      return;
    }

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
      setListenButtonState(true);
      setStatus(
        `Listening… (up to ${SILENCE_END_MS / 1000}s pauses OK; tap again when done)`,
        'processing',
      );
    };

    recognition.onerror = (ev) => {
      clearSilenceTimer();
      listeningSession = false;
      setListenButtonState(false);
      userEndedSession = true;

      if (ev.error === 'aborted') return;

      const msg =
        ev.error === 'not-allowed'
          ? 'Microphone permission denied'
          : `Speech error: ${ev.error || 'unknown'}`;
      setStatus(msg, 'error');
    };

    recognition.onend = () => {
      if (userEndedSession) {
        listeningSession = false;
        setListenButtonState(false);
        return;
      }
      if (listeningSession && recognition && !cleanupInFlight) {
        try {
          recognition.start();
        } catch (e) {
          listeningSession = false;
          setListenButtonState(false);
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
    } catch (err) {
      console.error('recognition.start failed:', err);
      setStatus('Could not start microphone', 'error');
      setListenButtonState(false);
      listeningSession = false;
    }
  });
}
