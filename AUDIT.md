# Deployment Audit — All Files From This Thread
# Mighty Jarvis MKII — Voice Pipeline + README Update
# Generated against thread outputs on 2026-06-19

---

## SUMMARY

| Category | Files | Action |
|---|---|---|
| New Python packages | `core/audio/` (6 files) | CREATE |
| New skill | `skills/voice_skill.py` | CREATE |
| Replace file | `voice_endpoints.py` | REPLACE existing |
| New React component | `frontend/src/components/VoiceIO.tsx` | CREATE |
| Patch existing | `main.py` | 2-line edit |
| Patch existing | `App.tsx` | 6 small edits |
| Patch existing | `Dockerfile` | 2 additions |
| Patch existing | `backend/requirements.txt` | 2 lines |
| Patch existing | `.env` | 1 variable |
| Docs | `README.md` | REPLACE existing |
| Docs (internal) | `VOICE_PIPELINE_INTEGRATION.md` | KEEP as reference |
| Discard | `VOICE_INTEGRATION.md` | DISCARD — superseded |

---

## SECTION 1 — FILES TO CREATE (new, no existing file to replace)

### 1.1 `backend/core/audio/__init__.py`
**Source:** `core/audio/__init__.py` from outputs
**Destination:** `backend/core/audio/__init__.py`
**What it does:** Public API surface. Exposes `AudioPipeline` and `transcribe()`
so any other module can do `from core.audio import transcribe`.

---

### 1.2 `backend/core/audio/io.py`
**Source:** `core/audio/io.py` from outputs
**Destination:** `backend/core/audio/io.py`
**What it does:** `AudioIO` class. Wraps ffmpeg as an async subprocess to convert
any audio format (webm, ogg, mp4, wav, mp3) to raw 16-bit signed little-endian
PCM at 16 kHz mono — the exact format Vosk and Whisper expect.
**External dep:** `ffmpeg` binary (added to Dockerfile in Section 3).

---

### 1.3 `backend/core/audio/vad.py`
**Source:** `core/audio/vad.py` from outputs
**Destination:** `backend/core/audio/vad.py`
**What it does:** `VAD` class. Pure-numpy voice activity detector using per-frame
short-time energy + zero-crossing rate. Splits a recording into speech segments,
strips silence, and can produce a frame-by-frame timeline for debugging.
**External dep:** `numpy` (added to requirements.txt in Section 3).

---

### 1.4 `backend/core/audio/features.py`
**Source:** `core/audio/features.py` from outputs
**Destination:** `backend/core/audio/features.py`
**What it does:** `MFCCExtractor` class. Pure-numpy MFCC pipeline:
pre-emphasis → Hamming window → FFT → Mel filterbank → log → DCT → Δ + ΔΔ.
Used as a pre-filter inside the pipeline to discard near-silent segments
(keyboard noise, HVAC hum) that pass the VAD energy gate.
**External dep:** `numpy` only.

---

### 1.5 `backend/core/audio/recogniser.py`
**Source:** `core/audio/recogniser.py` from outputs
**Destination:** `backend/core/audio/recogniser.py`
**What it does:** `VoskRecogniser` class. Wraps Vosk's `KaldiRecognizer`.
Model is loaded once and cached as a module-level singleton keyed by path.
Supports single-shot `transcribe()`, multi-segment `transcribe_segments()`,
and incremental stream mode `reset()` / `feed()` / `finalise()`.
**External dep:** `vosk` package + Vosk model files (Section 3 + Section 4).

---

### 1.6 `backend/core/audio/pipeline.py`
**Source:** `core/audio/pipeline.py` from outputs
**Destination:** `backend/core/audio/pipeline.py`
**What it does:** `AudioPipeline` class. Composes AudioIO → VAD → MFCCExtractor
→ VoskRecogniser into one `await pipeline.run(audio_bytes)` call.
Returns `PipelineResult` with transcript, segment counts, per-stage timings,
and optional MFCC feature matrix. Also exposes `extract_features()` for
non-STT use-cases and `transcribe()` as a module-level one-shot helper.
**External dep:** all four modules above.

---

### 1.7 `backend/skills/voice_skill.py`
**Source:** `voice_skill.py` from outputs
**Destination:** `backend/skills/voice_skill.py`
**What it does:** `VoiceSkill` — SkillRegistry wrapper. Exposes the pipeline
as TOOL_CALL actions: `transcribe`, `vad_analyse`, `extract_features`,
`diagnostics`, `model_status`. The agent can call it directly; other skills
can import `from core.audio import transcribe` instead.

**Registration:** The SkillRegistry's dynamic discovery (`_discover_dynamic_skills`)
will pick it up automatically on next start — no edit to `skill_registry.py` needed.
To register it explicitly, add to the `loaders` list in `_load_builtin_skills`:

```python
# In skill_registry.py — _load_builtin_skills() loaders list:
("voice", self._load_voice),

# And add the loader method:
def _load_voice(self):
    from skills.voice_skill import VoiceSkill
    return VoiceSkill()

# And add to _STATIC_SKILL_MODULES set:
"skills.voice_skill",
```

---

### 1.8 `frontend/src/components/VoiceIO.tsx`
**Source:** `VoiceIO.tsx` from outputs
**Destination:** `frontend/src/components/VoiceIO.tsx`
**What it does:** React component. Mic button (STT) + speaker button (TTS).
On mount, calls `GET /api/voice/capabilities` to detect which backends are live
and silently selects the best option. STT: records via MediaRecorder → POSTs
webm blob to `/api/voice/stt` → Vosk transcript → `onTranscript()` callback.
TTS: called by parent via `voiceRef.current.speak(text)` after each agent reply.
Tries `/api/voice/tts` first; falls back to `window.speechSynthesis` if 501.
Matches existing JARVIS HUD theme via the `J` theme prop.

---

## SECTION 2 — FILES TO REPLACE (drop-in replacements)

### 2.1 `backend/voice_endpoints.py`
**Source:** `voice_endpoints.py` from outputs
**Destination:** `backend/voice_endpoints.py`
**Replaces:** The version written earlier in this thread that used OpenAI Whisper
for STT. This version uses `core.audio.AudioPipeline` (Vosk, offline, free).
**TTS is unchanged** — still OpenAI TTS if `OPENAI_API_KEY` is set, browser
`speechSynthesis` otherwise.
**New endpoint:** `GET /api/voice/diagnostics` — returns model info, ffmpeg
version, VAD config. Useful for verifying the install.

**Import in main.py stays exactly the same:**
```python
from voice_endpoints import voice_router
app.include_router(voice_router)
```

---

### 2.2 `README.md`
**Source:** `README.md` from outputs
**Destination:** project root `README.md`
**Replaces:** whatever README existed before (if any).
**Note:** Does NOT yet include the voice pipeline section. Update it by
appending the content of `VOICE_PIPELINE_INTEGRATION.md` Section "Pipeline
data flow" under a new `## Voice Pipeline` heading if desired.

---

## SECTION 3 — FILES TO PATCH (small edits to existing files)

### 3.1 `backend/main.py` — 2 lines

Find the internal imports block (around line 35). Add after the last import:

```python
from voice_endpoints import voice_router
```

Find `app.add_middleware(CORSMiddleware, ...)` block (around line 73).
Add immediately after it (before the static file mount block):

```python
app.include_router(voice_router)
```

That is the complete change to main.py. The router mounts all four endpoints:
`/api/voice/stt`, `/api/voice/tts`, `/api/voice/capabilities`, `/api/voice/diagnostics`.

---

### 3.2 `frontend/src/App.tsx` — 6 edits

**Edit 1 — import VoiceIO** (top of file, with other component imports):
```tsx
import VoiceIO, { type VoiceIOHandle } from "./components/VoiceIO";
```

**Edit 2 — add voiceRef** (inside App component, near other useRef declarations):
```tsx
const voiceRef = useRef<VoiceIOHandle>(null);
```

**Edit 3 — speak after agent reply** (inside `handleEvt`, in the `type === "done"` block,
after the `setMessages(prev => prev.map(...))` call):
```tsx
setMessages(prev => {
  const last = prev.filter(m => m.role === "assistant" && !m.streaming).at(-1);
  if (last?.content) voiceRef.current?.speak(last.content);
  return prev;
});
```

**Edit 4 — stop TTS on HALT** (inside `handleEvt`, in the `type === "halted"` block):
```tsx
voiceRef.current?.stopSpeaking();
```

**Edit 5 — place VoiceIO in the input dock** (in the input row JSX, after the
hidden `<input ref={fileRef}.../>` and before the textarea wrapper `<div>`):
```tsx
<VoiceIO
  ref={voiceRef}
  onTranscript={t => {
    setInput(t);
    setTimeout(() => reactMode ? startClientReact(t) : send(t), 80);
  }}
  J={J}
  disabled={streaming}
/>
```
Remove the `setTimeout` auto-send lines if you prefer the transcript to land
in the textarea for review before sending.

**Edit 6 — update keyboard hint** (bottom of input dock):
```tsx
// Before:
<span>Enter — send · Shift+Enter — newline · drag & drop to attach</span>
// After:
<span>Enter — send · Shift+Enter — newline · drag & drop to attach · 🎙 voice</span>
```

---

### 3.3 `Dockerfile` — 2 additions

**Addition 1 — ffmpeg** (in the `apt-get install` block, around line 20):
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        kali-defaults \
        ...rest unchanged...
```
Note: ffmpeg may already be present in kali-rolling. The install is idempotent.

**Addition 2 — Vosk model download** (after the `pip install` block, before
`COPY backend/ ./`):
```dockerfile
# Download Vosk small English model (~40 MB) — free, offline STT
RUN mkdir -p /app/data/models && \
    wget -q https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip \
         -O /tmp/vosk-model.zip && \
    unzip -q /tmp/vosk-model.zip -d /app/data/models/ && \
    rm /tmp/vosk-model.zip

ENV VOSK_MODEL_PATH=/app/data/models/vosk-model-small-en-us-0.15
```

Alternative: bind-mount a pre-downloaded model folder instead of baking it
into the image (keeps image smaller, requires the folder to exist on the host):
```yaml
# docker-compose.yml volumes:
- ./vosk_models:/app/data/models
```
```env
# .env:
VOSK_MODEL_PATH=/app/data/models/vosk-model-small-en-us-0.15
```

---

### 3.4 `backend/requirements.txt` — 2 lines

Add anywhere (alphabetical order recommended):
```
httpx>=0.27
numpy>=1.24
vosk>=0.3.45
```
`httpx` is used only by the TTS backend call. If `OPENAI_API_KEY` is never set,
it is never imported at runtime (lazy import inside the endpoint).

---

### 3.5 `.env` — 1 required variable, 3 optional

```env
# REQUIRED for offline STT (must match the extracted model folder name)
VOSK_MODEL_PATH=/app/data/models/vosk-model-small-en-us-0.15

# OPTIONAL — only needed for neural TTS voice output
# If unset, browser speechSynthesis is used automatically (free, no setup)
OPENAI_API_KEY=sk-...
TTS_VOICE=onyx
TTS_MODEL=tts-1
```

---

## SECTION 4 — FILES TO DISCARD

### 4.1 `VOICE_INTEGRATION.md`
**Discard.** This was the integration guide for the original OpenAI Whisper
STT version. It describes `WHISPER_HOST`, `WHISPER_MODEL`, and the old
degradation matrix — all superseded by the Vosk pipeline.
Use `VOICE_PIPELINE_INTEGRATION.md` instead, which documents the correct
current architecture.

---

## SECTION 5 — VERIFICATION CHECKLIST

After deploying, run these checks in order:

```bash
# 1. Container starts healthy
docker compose up --build -d
docker compose ps   # sir-app should show "healthy"

# 2. Pipeline diagnostics (checks ffmpeg + Vosk model)
curl http://localhost/api/voice/diagnostics | python3 -m json.tool
# Expected: stt.available=true, pipeline.ffmpeg contains "ffmpeg version"

# 3. Capabilities probe (what the frontend will see on boot)
curl http://localhost/api/voice/capabilities | python3 -m json.tool
# Expected: stt.available=true, stt.provider="vosk_offline"

# 4. STT smoke test (requires a real webm audio file)
curl -X POST http://localhost/api/voice/stt \
     -H "Authorization: Bearer <token>" \
     -F "audio=@test.webm" | python3 -m json.tool
# Expected: { "transcript": "...", "provider": "vosk_offline" }

# 5. Agent TOOL_CALL test (from chat)
# Send this message to the agent:
#   "Use the voice skill to check the model status"
# Expected agent tool call: voice / model_status
# Expected response: { "available": true, "message": "Vosk model ready..." }
```

---

## SECTION 6 — DEPENDENCY SUMMARY

| Dependency | Type | Size | Cost | Why |
|---|---|---|---|---|
| `ffmpeg` | apt package | ~30 MB | Free | Audio format conversion (AudioIO) |
| `numpy` | pip package | ~18 MB | Free | VAD energy/ZCR, MFCC extraction |
| `vosk` | pip package | ~5 MB | Free | Offline speech recognition engine |
| Vosk small model | Downloaded file | ~40 MB | Free | English acoustic model |
| `httpx` | pip package | ~1 MB | Free | Async HTTP for TTS API calls only |
| OpenAI TTS API | Cloud API | — | $15/1M chars | Neural voice output (optional) |

**Minimum viable install (zero cost, zero cloud):**
`ffmpeg` + `numpy` + `vosk` + Vosk model + browser `speechSynthesis`

**Full install (best quality voice output):**
Add `OPENAI_API_KEY` for neural TTS. STT stays offline regardless.

---

## SECTION 7 — MODULE REUSE MAP

These modules can be imported independently by other skills/components:

```python
# One-shot transcription from any skill
from core.audio import transcribe
text = await transcribe(audio_bytes, src_format="webm")

# Just VAD — check if audio contains speech (Sentinel, Scheduler)
from core.audio.io  import AudioIO
from core.audio.vad import VAD
pcm = await AudioIO().to_pcm(audio_bytes)
has_speech = VAD().is_speech(pcm)
segments   = VAD().split_segments(pcm)

# Just features — for speaker analysis, emotion, activity detection
from core.audio import AudioPipeline
features = await AudioPipeline().extract_features(audio_bytes)
# → np.ndarray shape (n_frames, 39): MFCC + Δ + ΔΔ

# Full pipeline with custom config
from core.audio.pipeline import AudioPipeline, PipelineConfig
from core.audio.vad import VADConfig
pipeline = AudioPipeline(PipelineConfig(
    vad_config=VADConfig(energy_threshold=0.008),
    min_segment_ms=500,
))
result = await pipeline.run(audio_bytes)
print(result.transcript, result.segments_found, result.duration_ms)
```
