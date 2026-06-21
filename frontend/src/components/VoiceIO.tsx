/**
 * VoiceIO.tsx — v1.0.0
 *
 * Mic input (STT) + speaker output (TTS) for the JARVIS HUD.
 * Sits inside the input dock between the attach button and the textarea.
 *
 * STT flow:
 *   1. User holds / clicks the mic button → MediaRecorder captures audio/webm.
 *   2. On release/click-off, blob is POSTed to POST /api/voice/stt.
 *   3. If the backend returns 501 (no Whisper configured) the component
 *      falls back to the Web Speech API (SpeechRecognition) automatically.
 *   4. Transcript lands in the parent textarea via onTranscript().
 *
 * TTS flow:
 *   1. Parent calls speak(text) after each assistant "done" event.
 *   2. Tries POST /api/voice/tts first (OpenAI neural voice).
 *   3. If backend returns 501, falls back to window.speechSynthesis.
 *   4. TTS can be toggled off entirely with the speaker button.
 *
 * Props:
 *   onTranscript  — called with the transcribed string when STT is done
 *   speak         — ref-forwarded method so the parent can trigger TTS
 *   J             — the JARVIS theme object (same one used in App.tsx)
 *   disabled      — grey-out while the agent is streaming
 *
 * Usage in App.tsx input dock (replace the comment block):
 *   import VoiceIO, { VoiceIOHandle } from "./components/VoiceIO";
 *   const voiceRef = useRef<VoiceIOHandle>(null);
 *
 *   // after "done" event, inside handleEvt:
 *   if (type === "done") {
 *     setStreaming(false);
 *     const lastAsst = messages.filter(m => m.role === "assistant").at(-1);
 *     if (lastAsst) voiceRef.current?.speak(lastAsst.content);
 *   }
 *
 *   // in JSX, inside the input row, after the attach button:
 *   <VoiceIO ref={voiceRef} onTranscript={t => setInput(t)} J={J} disabled={streaming} />
 */

import React, {
  useState, useRef, useEffect, useCallback, useImperativeHandle, forwardRef,
} from "react";

// ── Types ──────────────────────────────────────────────────────────────────────

interface JTheme {
  bg: string; bgCard: string; bgPanel: string;
  accent: string; accentDim: string; accentGlow: string; accentGlow2: string;
  ok: string; okDim: string; err: string; errDim: string;
  warn: string; warnDim: string;
  textPri: string; textSec: string; textDim: string;
  border: string; borderMid: string;
  fontMono: string; fontHeader: string;
}

interface Props {
  onTranscript: (text: string) => void;
  J: JTheme;
  disabled?: boolean;
}

export interface VoiceIOHandle {
  speak: (text: string) => void;
  stopSpeaking: () => void;
}

// ── Constants ──────────────────────────────────────────────────────────────────

const API_BASE = (import.meta as any).env?.VITE_API_URL || "";
const VOICE_STT_URL = `${API_BASE}/api/voice/stt`;
const VOICE_TTS_URL = `${API_BASE}/api/voice/tts`;
const VOICE_CAP_URL = `${API_BASE}/api/voice/capabilities`;

// Silence threshold for auto-stop VAD (ms after last speech activity)
const VAD_SILENCE_MS = 1800;
// Max recording duration guard
const MAX_RECORD_MS = 60_000;

// ── Utility ────────────────────────────────────────────────────────────────────

function stripMarkdown(md: string): string {
  return md
    .replace(/```[\s\S]*?```/g, "")        // code blocks
    .replace(/`[^`]*`/g, "")               // inline code
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1") // links
    .replace(/#{1,6}\s/g, "")              // headings
    .replace(/[*_~|]+/g, "")              // bold/italic/strikethrough
    .replace(/^\s*[-*+]\s/gm, "")         // list bullets
    .replace(/\n{2,}/g, ". ")
    .replace(/\n/g, " ")
    .trim();
}

function getAuth(): { token: string } | null {
  const t = sessionStorage.getItem("jarvis_token");
  return t ? { token: t } : null;
}

// ── Main component ─────────────────────────────────────────────────────────────

const VoiceIO = forwardRef<VoiceIOHandle, Props>(({ onTranscript, J, disabled = false }, ref) => {
  // STT state
  const [micState, setMicState] = useState<"idle" | "recording" | "processing" | "error">("idle");
  const [micError, setMicError] = useState<string | null>(null);
  const [sttProvider, setSttProvider] = useState<"backend" | "webspeech" | "unknown">("unknown");

  // TTS state
  const [ttsEnabled, setTtsEnabled] = useState(false);
  const [ttsPlaying, setTtsPlaying] = useState(false);
  const [ttsProvider, setTtsProvider] = useState<"backend" | "webspeech" | "unknown">("unknown");

  // MediaRecorder
  const mediaRecRef   = useRef<MediaRecorder | null>(null);
  const chunksRef     = useRef<Blob[]>([]);
  const recordTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const vadTimerRef   = useRef<ReturnType<typeof setTimeout> | null>(null);
  const streamRef     = useRef<MediaStream | null>(null);

  // Web Speech API (STT fallback)
  const wsrRef = useRef<SpeechRecognition | null>(null);

  // TTS
  const audioRef   = useRef<HTMLAudioElement | null>(null);
  const utterRef   = useRef<SpeechSynthesisUtterance | null>(null);
  const ttsQueueRef = useRef<string[]>([]);
  const ttsActiveRef = useRef(false);

  // ── Probe backend capabilities on mount ─────────────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const auth = getAuth();
        const res = await fetch(VOICE_CAP_URL, {
          headers: auth ? { Authorization: `Bearer ${auth.token}` } : {},
        });
        if (res.ok) {
          const caps = await res.json();
          setSttProvider(caps.stt?.available ? "backend" : "webspeech");
          setTtsProvider(caps.tts?.available ? "backend" : "webspeech");
        }
      } catch {
        setSttProvider("webspeech");
        setTtsProvider("webspeech");
      }
    })();
  }, []);

  // ── TTS engine ───────────────────────────────────────────────────────────────

  const _playNext = useCallback(async () => {
    if (ttsActiveRef.current) return;
    const text = ttsQueueRef.current.shift();
    if (!text) return;
    ttsActiveRef.current = true;
    setTtsPlaying(true);

    const clean = stripMarkdown(text);
    if (!clean) { ttsActiveRef.current = false; setTtsPlaying(false); _playNext(); return; }

    // Try backend TTS (OpenAI neural voice)
    if (ttsProvider === "backend") {
      try {
        const auth = getAuth();
        const res = await fetch(VOICE_TTS_URL, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(auth ? { Authorization: `Bearer ${auth.token}` } : {}),
          },
          body: JSON.stringify({ text: clean }),
        });
        if (res.status === 501) {
          // Backend told us to fall back
          setTtsProvider("webspeech");
          throw new Error("fallback");
        }
        if (!res.ok) throw new Error(`TTS ${res.status}`);

        const blob = await res.blob();
        const url  = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audioRef.current = audio;
        audio.onended = () => {
          URL.revokeObjectURL(url);
          ttsActiveRef.current = false;
          setTtsPlaying(false);
          _playNext();
        };
        audio.onerror = () => {
          ttsActiveRef.current = false;
          setTtsPlaying(false);
          _playNext();
        };
        audio.play().catch(() => {
          ttsActiveRef.current = false;
          setTtsPlaying(false);
        });
        return;
      } catch (e: any) {
        if (e.message !== "fallback") {
          ttsActiveRef.current = false;
          setTtsPlaying(false);
          return;
        }
      }
    }

    // Web Speech API fallback
    if (!("speechSynthesis" in window)) {
      ttsActiveRef.current = false;
      setTtsPlaying(false);
      return;
    }
    window.speechSynthesis.cancel();
    const utt = new SpeechSynthesisUtterance(clean);
    utt.rate  = 1.05;
    utt.pitch = 0.92;
    // Prefer a deeper male voice if available (fits JARVIS theme)
    const voices = window.speechSynthesis.getVoices();
    const preferred = voices.find(v =>
      /male|david|mark|james|google uk english male/i.test(v.name)
    ) || voices.find(v => v.lang.startsWith("en"));
    if (preferred) utt.voice = preferred;
    utt.onend = () => { ttsActiveRef.current = false; setTtsPlaying(false); _playNext(); };
    utt.onerror = () => { ttsActiveRef.current = false; setTtsPlaying(false); };
    utterRef.current = utt;
    window.speechSynthesis.speak(utt);
  }, [ttsProvider]);

  const speak = useCallback((text: string) => {
    if (!ttsEnabled || !text.trim()) return;
    ttsQueueRef.current.push(text);
    _playNext();
  }, [ttsEnabled, _playNext]);

  const stopSpeaking = useCallback(() => {
    ttsQueueRef.current = [];
    ttsActiveRef.current = false;
    setTtsPlaying(false);
    audioRef.current?.pause();
    audioRef.current = null;
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  }, []);

  useImperativeHandle(ref, () => ({ speak, stopSpeaking }), [speak, stopSpeaking]);

  // ── STT helpers ──────────────────────────────────────────────────────────────

  const _stopRecording = useCallback(() => {
    if (recordTimerRef.current) { clearTimeout(recordTimerRef.current); recordTimerRef.current = null; }
    if (vadTimerRef.current)    { clearTimeout(vadTimerRef.current);    vadTimerRef.current    = null; }
    if (mediaRecRef.current && mediaRecRef.current.state !== "inactive") {
      mediaRecRef.current.stop();
    }
  }, []);

  const _submitAudio = useCallback(async (blob: Blob) => {
    if (blob.size < 100) { setMicState("idle"); return; }
    setMicState("processing");

    const auth = getAuth();
    const formData = new FormData();
    formData.append("audio", blob, "recording.webm");

    try {
      const res = await fetch(VOICE_STT_URL, {
        method: "POST",
        headers: auth ? { Authorization: `Bearer ${auth.token}` } : {},
        body: formData,
      });

      if (res.status === 501) {
        // Server said no STT — switch to Web Speech API for this session
        setSttProvider("webspeech");
        setMicState("idle");
        setMicError("No server STT — switched to browser mic");
        setTimeout(() => setMicError(null), 3000);
        return;
      }
      if (!res.ok) throw new Error(`STT ${res.status}`);

      const { transcript } = await res.json();
      if (transcript) onTranscript(transcript);
      setMicState("idle");
    } catch (e: any) {
      setMicError(e.message || "Transcription failed");
      setMicState("error");
      setTimeout(() => { setMicState("idle"); setMicError(null); }, 3000);
    }
  }, [onTranscript]);

  // VAD: reset silence timer on each data chunk
  const _resetVad = useCallback(() => {
    if (vadTimerRef.current) clearTimeout(vadTimerRef.current);
    vadTimerRef.current = setTimeout(() => {
      _stopRecording();
    }, VAD_SILENCE_MS);
  }, [_stopRecording]);

  const _startWebSpeech = useCallback(() => {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) {
      setMicError("Speech recognition not supported in this browser");
      setMicState("error");
      setTimeout(() => { setMicState("idle"); setMicError(null); }, 3000);
      return;
    }
    const rec = new SR() as SpeechRecognition;
    rec.continuous     = false;
    rec.interimResults = false;
    rec.lang           = "en-US";
    rec.onresult = (e: SpeechRecognitionEvent) => {
      const text = Array.from(e.results).map(r => r[0].transcript).join(" ").trim();
      if (text) onTranscript(text);
    };
    rec.onerror = (e: SpeechRecognitionErrorEvent) => {
      if (e.error !== "no-speech") {
        setMicError(`STT error: ${e.error}`);
        setMicState("error");
        setTimeout(() => { setMicState("idle"); setMicError(null); }, 3000);
      } else {
        setMicState("idle");
      }
    };
    rec.onend = () => setMicState("idle");
    wsrRef.current = rec;
    rec.start();
    setMicState("recording");
  }, [onTranscript]);

  const _startMediaRecorder = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      streamRef.current = stream;
      chunksRef.current = [];

      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : MediaRecorder.isTypeSupported("audio/webm")
        ? "audio/webm"
        : "";

      const rec = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      mediaRecRef.current = rec;

      rec.ondataavailable = (e) => {
        if (e.data.size > 0) {
          chunksRef.current.push(e.data);
          _resetVad();
        }
      };

      rec.onstop = () => {
        stream.getTracks().forEach(t => t.stop());
        streamRef.current = null;
        const blob = new Blob(chunksRef.current, { type: mimeType || "audio/webm" });
        _submitAudio(blob);
      };

      rec.start(200); // ondataavailable every 200 ms for VAD
      setMicState("recording");

      // Hard timeout
      recordTimerRef.current = setTimeout(() => _stopRecording(), MAX_RECORD_MS);
      // Initial VAD timer
      _resetVad();
    } catch (e: any) {
      setMicError(e.name === "NotAllowedError" ? "Mic permission denied" : `Mic error: ${e.message}`);
      setMicState("error");
      setTimeout(() => { setMicState("idle"); setMicError(null); }, 4000);
    }
  }, [_resetVad, _stopRecording, _submitAudio]);

  const toggleMic = useCallback(() => {
    if (disabled) return;
    if (micState === "recording") {
      // Stop
      if (wsrRef.current) { wsrRef.current.stop(); wsrRef.current = null; }
      else _stopRecording();
      return;
    }
    if (micState !== "idle") return;
    stopSpeaking(); // silence TTS while mic is active

    if (sttProvider === "webspeech" || !navigator.mediaDevices) {
      _startWebSpeech();
    } else {
      _startMediaRecorder();
    }
  }, [disabled, micState, sttProvider, stopSpeaking, _startWebSpeech, _startMediaRecorder, _stopRecording]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      _stopRecording();
      stopSpeaking();
      streamRef.current?.getTracks().forEach(t => t.stop());
    };
  }, [_stopRecording, stopSpeaking]);

  // ── Render ───────────────────────────────────────────────────────────────────

  const isRecording  = micState === "recording";
  const isProcessing = micState === "processing";
  const isError      = micState === "error";

  const micColor = isError
    ? J.err
    : isRecording
    ? J.ok
    : isProcessing
    ? J.warn
    : disabled
    ? J.textDim
    : J.textSec;

  const micBg = isRecording
    ? `${J.ok}12`
    : isProcessing
    ? `${J.warn}0A`
    : "transparent";

  const micBorder = isRecording
    ? `1px solid ${J.ok}44`
    : isError
    ? `1px solid ${J.err}44`
    : `1px solid transparent`;

  const micTitle = isRecording
    ? "Click to stop recording"
    : isProcessing
    ? "Transcribing…"
    : sttProvider === "webspeech"
    ? "Speak (browser STT)"
    : "Speak (Whisper STT)";

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 2, flexShrink: 0 }}>
      {/* ── Mic button ── */}
      <button
        onClick={toggleMic}
        disabled={disabled || isProcessing}
        title={micError || micTitle}
        style={{
          position: "relative",
          padding: "9px 11px",
          borderRadius: 4,
          fontSize: 14,
          background: micBg,
          border: micBorder,
          color: micColor,
          transition: "all 0.2s",
          lineHeight: 1,
          // Pulse glow while recording
          boxShadow: isRecording ? `0 0 12px ${J.ok}33, 0 0 4px ${J.ok}22` : "none",
          animation: isRecording ? "hud-pulse 1.4s infinite" : "none",
        }}
      >
        {isProcessing
          ? <span style={{ display: "inline-block", animation: "hud-spin 1s linear infinite", fontSize: 12 }}>◈</span>
          : isRecording
          ? "⏹"   // stop icon while recording
          : "🎙"}

        {/* Recording ring */}
        {isRecording && (
          <span style={{
            position: "absolute",
            inset: -3,
            borderRadius: 6,
            border: `1px solid ${J.ok}`,
            opacity: 0.5,
            pointerEvents: "none",
            animation: "hud-pulse 1s infinite",
          }} />
        )}
      </button>

      {/* ── TTS toggle ── */}
      <button
        onClick={() => {
          const next = !ttsEnabled;
          setTtsEnabled(next);
          if (!next) stopSpeaking();
        }}
        title={ttsEnabled
          ? `TTS on (${ttsProvider === "backend" ? "OpenAI" : "browser"}) — click to mute`
          : "TTS off — click to enable voice output"}
        style={{
          padding: "9px 11px",
          borderRadius: 4,
          fontSize: 14,
          background: ttsEnabled ? `${J.accent}0A` : "transparent",
          border: `1px solid ${ttsEnabled ? `${J.accent}33` : "transparent"}`,
          color: ttsPlaying
            ? J.accent
            : ttsEnabled
            ? J.accentDim
            : J.textDim,
          transition: "all 0.2s",
          lineHeight: 1,
          boxShadow: ttsPlaying ? `0 0 10px ${J.accentGlow}` : "none",
          animation: ttsPlaying ? "hud-pulse 1.8s infinite" : "none",
          position: "relative",
        }}
      >
        {ttsPlaying ? "🔊" : ttsEnabled ? "🔈" : "🔇"}

        {/* "off" crosshair overlay when muted */}
        {!ttsEnabled && (
          <span style={{
            position: "absolute",
            top: "50%", left: "50%",
            transform: "translate(-50%, -50%) rotate(-45deg)",
            width: "70%", height: 1.5,
            background: J.textDim,
            borderRadius: 1,
            pointerEvents: "none",
            opacity: 0.6,
          }} />
        )}
      </button>

      {/* ── Transient status label ── */}
      {(isRecording || isProcessing || micError) && (
        <span style={{
          fontSize: 9,
          color: isError ? J.err : isRecording ? J.ok : J.warn,
          fontFamily: J.fontHeader,
          fontWeight: 600,
          letterSpacing: "0.1em",
          animation: "hud-pulse 1.2s infinite",
          whiteSpace: "nowrap",
          userSelect: "none",
        }}>
          {isError
            ? `⚠ ${micError}`
            : isProcessing
            ? "TRANSCRIBING…"
            : "● REC"}
        </span>
      )}
    </div>
  );
});

VoiceIO.displayName = "VoiceIO";
export default VoiceIO;
