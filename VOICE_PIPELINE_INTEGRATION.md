# Voice Pipeline — Integration Guide
# Custom AudioPipeline: ffmpeg → VAD → MFCC → Vosk (100% offline, zero cost)

---

## File layout

```
core/
└── audio/
    ├── __init__.py          public API: AudioPipeline, transcribe()
    ├── io.py                AudioIO      — any format → raw PCM (ffmpeg)
    ├── vad.py               VAD          — energy + ZCR silence detector
    ├── features.py          MFCCExtractor — pure-numpy feature extraction
    ├── recogniser.py        VoskRecogniser — offline speech recognition
    └── pipeline.py          AudioPipeline — composes all four stages

skills/
└── voice_skill.py           SkillRegistry wrapper (TOOL_CALL interface)

voice_endpoints.py           FastAPI router  (/api/voice/stt, /tts, /capabilities, /diagnostics)
frontend/src/components/
└── VoiceIO.tsx              React component (mic button + TTS toggle)
```

---

## 1. Dockerfile — add ffmpeg + vosk + numpy

```dockerfile
# ── System packages ───────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \          # ← ADD (needed by AudioIO for format conversion)
        ...existing packages...
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ───────────────────────────────────────────────────────
# Add these to backend/requirements.txt:
#   vosk>=0.3.45
#   numpy>=1.24
#   httpx>=0.27        (TTS backend calls only)
```

Or directly in the Dockerfile pip block:

```dockerfile
RUN pip install --no-cache-dir vosk numpy httpx
```

---

## 2. Download the Vosk model (one-time setup)

Add to Dockerfile (builds the model into the image — ~40 MB for small, ~1.8 GB for full):

```dockerfile
# Small model — fast, good for command-style speech (~40 MB)
RUN mkdir -p /app/data/models && \
    wget -q https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip \
         -O /tmp/vosk-model.zip && \
    unzip -q /tmp/vosk-model.zip -d /app/data/models/ && \
    rm /tmp/vosk-model.zip

ENV VOSK_MODEL_PATH=/app/data/models/vosk-model-small-en-us-0.15
```

Or download at runtime (keeps image small, requires internet on first start):

```dockerfile
# docker-entrypoint.sh — add before supervisord starts:
if [ ! -d "$VOSK_MODEL_PATH" ]; then
  echo "Downloading Vosk model..."
  mkdir -p /app/data/models
  wget -q https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip \
       -O /tmp/vosk-model.zip
  unzip -q /tmp/vosk-model.zip -d /app/data/models/
  rm /tmp/vosk-model.zip
fi
```

Or bind-mount a pre-downloaded model:

```yaml
# docker-compose.yml
volumes:
  - ./vosk_models:/app/data/models
environment:
  VOSK_MODEL_PATH: /app/data/models/vosk-model-small-en-us-0.15
```

---

## 3. Copy core/audio/ into the backend

```bash
cp -r audio/ backend/core/audio/
```

---

## 4. Copy voice_skill.py into skills/

```bash
cp voice_skill.py backend/skills/voice_skill.py
```

The SkillRegistry's dynamic discovery (`_discover_dynamic_skills`) will pick it
up automatically on next start — no edit to skill_registry.py needed.

To register it explicitly anyway, add to `_load_builtin_skills` in skill_registry.py:

```python
("voice", self._load_voice),
```

```python
def _load_voice(self):
    from skills.voice_skill import VoiceSkill
    return VoiceSkill()
```

---

## 5. Replace voice_endpoints.py

```bash
cp voice_endpoints_v2.py backend/voice_endpoints.py
```

The router import in main.py stays the same:

```python
from voice_endpoints import voice_router
app.include_router(voice_router)
```

---

## 6. Environment variables

Add to `.env`:

```env
# Vosk model path (required for offline STT)
VOSK_MODEL_PATH=/app/data/models/vosk-model-small-en-us-0.15

# ffmpeg binary (default: 'ffmpeg', already on PATH in kali-rolling)
FFMPEG_PATH=ffmpeg

# TTS — only needed if you want neural voice output (optional)
# If unset, browser speechSynthesis is used automatically
OPENAI_API_KEY=sk-...
TTS_VOICE=onyx
TTS_MODEL=tts-1
```

---

## 7. Verify the installation

After the container is running:

```bash
# Check pipeline diagnostics
curl http://localhost/api/voice/diagnostics | python3 -m json.tool

# Expected output:
# {
#   "stt": { "available": true, "model": { "exists": true, "size_mb": 40.2 } },
#   "pipeline": { "ffmpeg": "ffmpeg version 6.x ...", "sample_rate": 16000, ... }
# }
```

Or from the agent chat:

```
TOOL_CALL: voice / model_status
```

---

## Model comparison

| Model | Size | WER (english) | Speed (CPU) | Use case |
|---|---|---|---|---|
| `vosk-model-small-en-us-0.15` | 40 MB | ~10–15% | Real-time | Commands, short utterances |
| `vosk-model-en-us-0.22` | 1.8 GB | ~5–7% | ~2× slower | Free-form conversation |
| `vosk-model-en-us-0.22-lgraph` | 128 MB | ~8% | Fast | Good balance |

Download all models: https://alphacephei.com/vosk/models

---

## Pipeline data flow

```
Browser (MediaRecorder)
    │ audio/webm;codecs=opus blob
    ▼
POST /api/voice/stt
    │
[Stage 1 — AudioIO]
    │ ffmpeg: webm/opus → raw s16le PCM, 16 kHz, mono
    ▼
[Stage 2 — VAD]
    │ frame energy + ZCR → speech segments (silence stripped)
    ▼
[Stage 3 — MFCCExtractor]
    │ pure-numpy: pre-emphasis → framing → FFT → Mel filterbank → DCT
    │ segments below energy floor are dropped (keyboard noise, HVAC)
    ▼
[Stage 4 — VoskRecogniser]
    │ Vosk KaldiRecognizer: offline, CPU, no internet
    │ segments transcribed independently, joined by space
    ▼
{ "transcript": "send the recon report to swarm" }
    │
VoiceIO.tsx onTranscript() → setInput() → send() → WebSocket → Agent
```

---

## Using the pipeline in other skills

Any skill can import and use the pipeline directly:

```python
# Sentinel: transcribe a voice alert recorded from a microphone
from core.audio import transcribe
text = await transcribe(alert_audio_bytes, src_format="wav")
if "intrusion" in text or "alert" in text:
    await self._block_ip({"ip": detected_ip}, confirmed=True)

# Scheduler: create a task from a voice memo
from core.audio import AudioPipeline
pipeline = AudioPipeline()
result = await pipeline.run(voice_memo_bytes, src_format="m4a")
create_scheduled_task(description=result.transcript)

# Just VAD — check if a recording has speech before doing anything
from core.audio.vad import VAD
from core.audio.io  import AudioIO
io  = AudioIO()
vad = VAD()
pcm = await io.to_pcm(audio_bytes)
if vad.is_speech(pcm):
    ...

# Just features — for speaker analysis or activity detection
from core.audio import AudioPipeline
pipeline = AudioPipeline()
features = await pipeline.extract_features(audio_bytes)
# features: np.ndarray shape (n_frames, 39) — MFCC + Δ + ΔΔ
```

---

## TTS options (output voice)

| Option | Cost | Quality | Setup |
|---|---|---|---|
| Browser `speechSynthesis` | Free | Robotic | Zero — works now |
| OpenAI TTS (`tts-1`, `onyx`) | $15/1M chars | Neural | `OPENAI_API_KEY` |
| OpenAI TTS (`tts-1-hd`) | $30/1M chars | HD neural | `OPENAI_API_KEY` + `TTS_MODEL=tts-1-hd` |
| Coqui TTS (self-hosted) | Free | Good | Separate container (future) |

The frontend `VoiceIO.tsx` tries the backend first and falls back to
`speechSynthesis` automatically if `/api/voice/tts` returns 501.
No config change needed on the frontend side.
