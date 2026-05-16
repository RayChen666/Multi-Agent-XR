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
  const hasWebSpeech = Boolean(SR);
  const hasRecorderFallback =
    typeof navigator !== 'undefined'
    && navigator.mediaDevices
    && typeof navigator.mediaDevices.getUserMedia === 'function'
    && typeof MediaRecorder !== 'undefined';

  if (!hasWebSpeech && !hasRecorderFallback) {
    btn.disabled = true;
    btn.title = 'Speech recognition is not available in this browser';
    setStatus('Speech recognition unavailable on this device/browser', 'error');
    return;
  }

  const sttMode = hasWebSpeech ? 'webspeech' : 'audio-upload';

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
      'audio/mp4'
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

  async function runTranscribeAndCleanup(audioBlob) {
    if (cleanupInFlight) return;
    
    cleanupInFlight = true;
    clearSilenceTimer();
    clearMaxRecordTimer();

    if (!audioBlob || audioBlob.size === 0) {
      cleanupInFlight = false;
      setStatus('No audio captured; try Listen again', 'error');
      return;
    }

    setStatus('Uploading audio for transcription…', 'processing');
    const base = getApiBaseUrl();

    try {
      const ext = audioBlob.type.includes('ogg') ? 'ogg' : 'webm';
      const formData = new FormData();
      formData.append('audio', audioBlob, `voice_input.${ext}`);

      const trRes = await fetch(`${base}/stt/transcribe`, {
        method: 'POST',
        body: formData
      });

      const trData = await trRes.json().catch(() => ({}));

      if (!trRes.ok) {
        const detail = trData.detail || trData.message || trRes.statusText;
        setStatus(typeof detail === 'string' ? detail : 'Audio transcription failed', 'error');
        cleanupInFlight = false;
        return;
      }

      const transcript = (trData.transcript || '').trim();
      if (!transcript) {
        setStatus('Transcription returned empty text', 'error');
        cleanupInFlight = false;
        return;
      }

      setStatus('Cleaning up transcript…', 'processing');
      const cleanRes = await fetch(`${base}/stt/cleanup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transcript })
      });
      
      const cleanData = await cleanRes.json().catch(() => ({}));
      if (!cleanRes.ok) {
        const detail = cleanData.detail || cleanData.message || cleanRes.statusText;
        setStatus(typeof detail === 'string' ? detail : 'Cleanup failed', 'error');
        cleanupInFlight = false;
        return;
      }

      const cleaned = (cleanData.cleaned_text || '').trim();
      if (!cleaned) {
        setStatus('Cleanup returned empty text', 'error');
        cleanupInFlight = false;
        return;
      }

      input.value = cleaned;
      setStatus('Edit if needed, then Execute or Clear', 'success');
    } catch (err) {
      console.error('STT transcribe/cleanup error:', err);
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

  async function startRecorderSession() {
    if (!hasRecorderFallback) {
      setStatus('Audio recorder fallback not available', 'error');
      return;
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
        setStatus(`Recorder error: ${errName}`, 'error');
        listeningSession = false;
        setListenButtonState(false);
        clearMaxRecordTimer();
        stopMediaStreamTracks();
      };

      mediaRecorder.onstop = () => {
        const type = recordMimeType || mediaRecorder?.mimeType || 'audio/webm';
        const blob = new Blob(recordedChunks, { type });
        recordedChunks = [];
        listeningSession = false;
        setListenButtonState(false);
        clearMaxRecordTimer();
        stopMediaStreamTracks();
        runTranscribeAndCleanup(blob);
      };

      mediaRecorder.start(250);
      listeningSession = true;
      setListenButtonState(true);
      setStatus('Recording voice… Tap Listen again to stop and transcribe', 'processing');

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
    } catch (err) {
      console.error('getUserMedia / MediaRecorder start failed:', err);
      const msg = err?.name === 'NotAllowedError'
        ? 'Microphone permission denied'
        : (err?.message || 'Could not start microphone');
      setStatus(msg, 'error');
      listeningSession = false;
      setListenButtonState(false);
      clearMaxRecordTimer();
      stopMediaStreamTracks();
    }
  }

  function stopRecorderSession() {
    if (!mediaRecorder) {
      setStatus('Recorder is not active', 'error');
      return;
    }
    if (mediaRecorder.state === 'recording') {
      try {
        mediaRecorder.stop();
      } catch (err) {
        console.error('mediaRecorder.stop failed:', err);
        setStatus('Could not stop recording', 'error');
        listeningSession = false;
        setListenButtonState(false);
        clearMaxRecordTimer();
        stopMediaStreamTracks();
      }
    }
  }

  btn.addEventListener('click', (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    if (btn.disabled) return;

    if (sttMode === 'audio-upload') {
      if (cleanupInFlight) {
        return;
      }
      if (!listeningSession) {
        startRecorderSession();
      } else {
        stopRecorderSession();
      }
      return;
    }

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

  if (sttMode === 'audio-upload') {
    setStatus('Server STT fallback ready on this browser', 'processing');
  }
}
