"""
Voice I/O — STT and TTS endpoints (custom AudioPipeline edition).
Replaces the previous voice_endpoints.py that depended on OpenAI Whisper.

STT  POST /api/voice/stt
     Accepts audio/webm (or any format ffmpeg can decode).
     Transcribes via core.audio.AudioPipeline:
       ffmpeg → VAD → MFCC filter → Vosk (offline, free, no API key).
     Falls back gracefully to a 501 if Vosk model isn't installed yet,
     so the frontend can use Web Speech API instead.

TTS  POST /api/voice/tts
     Body: { text: str, voice?: str, speed?: float }
     Returns audio/mpeg stream via OpenAI TTS if OPENAI_API_KEY is set,
     otherwise 501 → frontend falls back to browser speechSynthesis.

GET  /api/voice/capabilities
     Returns which backends are live so the frontend can self-configure.

GET  /api/voice/diagnostics
     Full pipeline diagnostics: model info, VAD config, ffmpeg version.

version: 2.0.0
changelog:
  2.0.0 - replaced OpenAI Whisper dependency with core.audio.AudioPipeline
          (ffmpeg + VAD + MFCC + Vosk — fully offline, zero cost)
  1.0.0 - initial OpenAI Whisper + browser fallback version
"""

import io
import logging
import os
import time

from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse

logger = logging.getLogger("jarvis.voice")

voice_router = APIRouter(prefix="/api/voice", tags=["Voice"])

# ── Config ─────────────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
TTS_VOICE      = os.getenv("TTS_VOICE", "onyx")
TTS_MODEL      = os.getenv("TTS_MODEL", "tts-1")

# AudioPipeline is lazy-imported so a missing vosk install never crashes the app
_pipeline = None


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        from core.audio import AudioPipeline
        _pipeline = AudioPipeline()
    return _pipeline


def _stt_available() -> bool:
    from core.audio.recogniser import VoskRecogniser
    return VoskRecogniser.is_available()


def _tts_available() -> bool:
    return bool(OPENAI_API_KEY)


# ── STT ────────────────────────────────────────────────────────────────────────

@voice_router.post("/stt")
async def speech_to_text(audio: UploadFile = File(...)):
    """
    Transcribe an uploaded audio file to text.

    Accepts: audio/webm, audio/ogg, audio/wav, audio/mp4, audio/mpeg
    Returns: { transcript, provider, segments_found, segments_used,
                audio_duration_s, duration_ms, stage_times_ms }

    Pipeline: ffmpeg → VAD (energy+ZCR) → MFCC filter → Vosk (offline)

    If Vosk model is not installed the endpoint returns 501 and the
    frontend automatically switches to Web Speech API.
    """
    if not _stt_available():
        raise HTTPException(
            status_code=501,
            detail={
                "error_code": "VOICE_NO_STT_BACKEND",
                "message":    "Vosk model not found. Set VOSK_MODEL_PATH or download a model.",
                "fallback":   "web_speech_api",
                "model_info": _vosk_info(),
            },
        )

    audio_bytes = await audio.read()

    if len(audio_bytes) < 100:
        raise HTTPException(
            status_code=422,
            detail={"error_code": "VOICE_AUDIO_EMPTY", "message": "Audio payload too small"},
        )

    # Derive format from content-type or filename
    ct         = audio.content_type or ""
    src_format = _guess_format(ct, audio.filename or "")

    try:
        pipeline = _get_pipeline()
        result   = await pipeline.run(audio_bytes, src_format=src_format)
    except Exception as exc:
        logger.error("[stt_error] %s", exc)
        raise HTTPException(
            status_code=502,
            detail={"error_code": "VOICE_STT_FAILED", "message": str(exc)},
        )

    if result.error:
        logger.warning("[stt_pipeline_error] %s", result.error)
        raise HTTPException(
            status_code=502,
            detail={"error_code": "VOICE_PIPELINE_ERROR", "message": result.error},
        )

    logger.info(
        "[stt_complete] transcript=%r segs=%d/%d duration_ms=%d",
        result.transcript[:60], result.segments_used, result.segments_found, result.duration_ms,
    )

    return JSONResponse({
        "transcript":       result.transcript,
        "provider":         "vosk_offline",
        "has_speech":       result.has_speech,
        "segments_found":   result.segments_found,
        "segments_used":    result.segments_used,
        "audio_duration_s": round(result.audio_duration_s, 2),
        "duration_ms":      result.duration_ms,
        "stage_times_ms":   result.stage_times_ms,
    })


# ── TTS ────────────────────────────────────────────────────────────────────────

@voice_router.post("/tts")
async def text_to_speech(request: Request):
    """
    Synthesise text to audio/mpeg.

    Body: { text: str, voice?: str, speed?: float }
    Returns: audio/mpeg binary stream.

    Uses OpenAI TTS if OPENAI_API_KEY is set.
    Returns 501 if not — frontend falls back to browser speechSynthesis.

    Available voices: alloy, echo, fable, onyx, nova, shimmer
    speed: 0.25 – 4.0 (default 1.0)
    """
    if not _tts_available():
        raise HTTPException(
            status_code=501,
            detail={
                "error_code": "VOICE_NO_TTS_BACKEND",
                "message":    "OPENAI_API_KEY not set. Browser speechSynthesis will be used.",
                "fallback":   "web_speech_api",
            },
        )

    body  = await request.json()
    text  = (body.get("text") or "").strip()
    voice = body.get("voice") or TTS_VOICE
    speed = float(body.get("speed") or 1.0)
    speed = max(0.25, min(4.0, speed))

    if not text:
        raise HTTPException(
            status_code=422,
            detail={"error_code": "VOICE_EMPTY_TEXT", "message": "text is required"},
        )

    # OpenAI TTS cap is 4096 chars
    if len(text) > 4096:
        text = text[:4093] + "…"

    import httpx
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={"model": TTS_MODEL, "voice": voice, "input": text, "speed": speed},
            )
        if response.status_code != 200:
            raise RuntimeError(f"TTS API {response.status_code}: {response.text[:300]}")

        audio_bytes = response.content
        logger.info("[tts_complete] chars=%d voice=%s size=%d", len(text), voice, len(audio_bytes))

        return StreamingResponse(
            io.BytesIO(audio_bytes),
            media_type="audio/mpeg",
            headers={"Content-Length": str(len(audio_bytes))},
        )
    except Exception as exc:
        logger.error("[tts_error] %s", exc)
        raise HTTPException(
            status_code=502,
            detail={"error_code": "VOICE_TTS_FAILED", "message": str(exc)},
        )


# ── Capabilities probe ─────────────────────────────────────────────────────────

@voice_router.get("/capabilities")
async def voice_capabilities():
    """
    Return which voice features are live.
    Frontend calls this on boot to auto-select backend vs. browser APIs.
    """
    return {
        "stt": {
            "available": _stt_available(),
            "provider":  "vosk_offline" if _stt_available() else None,
            "fallback":  "web_speech_api",
            "model":     _vosk_info(),
        },
        "tts": {
            "available": _tts_available(),
            "provider":  "openai_tts" if _tts_available() else None,
            "voices":    ["alloy", "echo", "fable", "onyx", "nova", "shimmer"] if _tts_available() else [],
            "fallback":  "web_speech_api",
        },
    }


# ── Diagnostics ────────────────────────────────────────────────────────────────

@voice_router.get("/diagnostics")
async def voice_diagnostics():
    """
    Full pipeline diagnostics — model info, VAD config, ffmpeg version.
    Useful for verifying the installation is correct.
    """
    import asyncio, shutil
    from core.audio.recogniser import VoskRecogniser
    from core.audio.vad import VADConfig

    # ffmpeg version
    ffmpeg_version = "not found"
    ffmpeg_bin = os.getenv("FFMPEG_PATH", "ffmpeg")
    if shutil.which(ffmpeg_bin):
        try:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg_bin, "-version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            ffmpeg_version = out.decode(errors="replace").splitlines()[0]
        except Exception:
            ffmpeg_version = "error reading version"

    cfg = VADConfig()
    return {
        "stt": {
            "provider":      "vosk_offline",
            "available":     _stt_available(),
            "model":         VoskRecogniser.model_info(),
        },
        "tts": {
            "provider":      "openai_tts",
            "available":     _tts_available(),
            "voice":         TTS_VOICE,
            "model":         TTS_MODEL,
        },
        "pipeline": {
            "ffmpeg":        ffmpeg_version,
            "sample_rate":   16000,
            "vad": {
                "frame_ms":         cfg.frame_ms,
                "energy_threshold": cfg.energy_threshold,
                "zcr_low":          cfg.zcr_low,
                "zcr_high":         cfg.zcr_high,
                "hangover_frames":  cfg.hangover_frames,
            },
            "mfcc_filter":   True,
        },
    }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _guess_format(content_type: str, filename: str) -> str:
    """Derive a format hint for ffmpeg from content-type or file extension."""
    ct_map = {
        "audio/webm":  "webm",
        "audio/ogg":   "ogg",
        "audio/wav":   "wav",
        "audio/x-wav": "wav",
        "audio/mp4":   "mp4",
        "audio/m4a":   "mp4",
        "audio/mpeg":  "mp3",
        "audio/mp3":   "mp3",
    }
    for key, fmt in ct_map.items():
        if key in content_type:
            return fmt
    # Fall back to file extension
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    ext_map = {"webm": "webm", "ogg": "ogg", "wav": "wav", "mp4": "mp4",
               "m4a": "mp4", "mp3": "mp3", "mpeg": "mp3"}
    return ext_map.get(ext, "webm")   # webm is safest default for browser recordings


def _vosk_info() -> dict:
    try:
        from core.audio.recogniser import VoskRecogniser
        return VoskRecogniser.model_info()
    except Exception as exc:
        return {"error": str(exc)}
