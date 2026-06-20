"""
Voice Skill — Skill registry wrapper for core.audio.AudioPipeline.

Exposes STT, VAD analysis, feature extraction, and pipeline diagnostics
as first-class skill actions so the agent can invoke them via TOOL_CALL
exactly like any other skill — or the Swarm can fan tasks to them.

version: 1.0.0
CBD Component Contract:
  Name:             VoiceSkill
  Logical Function: Speech processing (STT, VAD, features, diagnostics)
  Actions:
    transcribe       → { audio_b64, src_format? } → { transcript, ... }
    vad_analyse      → { audio_b64, src_format? } → { timeline, segments }
    extract_features → { audio_b64, src_format? } → { shape, mean_energy }
    diagnostics      → {}                          → pipeline + model info
    model_status     → {}                          → Vosk availability
  Error-Schema: { error_code: VOICE_*, message: str }
  Failure Map:  all exceptions caught, returned as SkillResult.fail()

Usage from agent tool call:
  TOOL_CALL: voice / transcribe
  { "audio_b64": "<base64 encoded webm>", "src_format": "webm" }

Usage from another skill:
  from core.audio import transcribe
  text = await transcribe(audio_bytes)

Usage standalone:
  from skills.voice_skill import VoiceSkill
  skill = VoiceSkill()
  result = await skill.execute("transcribe", {"audio_b64": b64str})
"""

import base64
import logging
import traceback

from core.skill_registry import SkillResult

logger = logging.getLogger("skill.voice")


class VoiceSkill:

    async def execute(self, action: str, params: dict, confirmed: bool = False) -> SkillResult:
        dispatch = {
            "transcribe":       self._transcribe,
            "vad_analyse":      self._vad_analyse,
            "extract_features": self._extract_features,
            "diagnostics":      self._diagnostics,
            "model_status":     self._model_status,
        }
        fn = dispatch.get(action)
        if fn is None:
            return SkillResult.fail(
                f"VOICE_UNKNOWN_ACTION: '{action}'. Valid: {', '.join(dispatch)}"
            )
        try:
            return await fn(params, confirmed)
        except Exception as exc:
            logger.error("[voice_skill.%s] %s\n%s", action, exc, traceback.format_exc())
            return SkillResult.fail(f"VOICE_INTERNAL_ERROR: {type(exc).__name__}: {exc}")

    # ── Actions ────────────────────────────────────────────────────────────────

    async def _transcribe(self, params: dict, _confirmed: bool) -> SkillResult:
        """
        Transcribe base64-encoded audio to text.

        params:
          audio_b64  : str   Base64-encoded audio bytes
          src_format : str   Container format hint (default: webm)
        """
        audio_b64  = params.get("audio_b64", "")
        src_format = params.get("src_format", "webm")

        if not audio_b64:
            return SkillResult.fail("VOICE_MISSING_AUDIO: audio_b64 is required")

        try:
            audio_bytes = base64.b64decode(audio_b64)
        except Exception:
            return SkillResult.fail("VOICE_INVALID_B64: could not decode audio_b64")

        from core.audio import AudioPipeline
        pipeline = AudioPipeline()
        result   = await pipeline.run(audio_bytes, src_format=src_format)

        if result.error:
            return SkillResult.fail(f"VOICE_PIPELINE_ERROR: {result.error}")

        return SkillResult.ok({
            "transcript":       result.transcript,
            "has_speech":       result.has_speech,
            "segments_found":   result.segments_found,
            "segments_used":    result.segments_used,
            "audio_duration_s": result.audio_duration_s,
            "duration_ms":      result.duration_ms,
            "stage_times_ms":   result.stage_times_ms,
        })

    async def _vad_analyse(self, params: dict, _confirmed: bool) -> SkillResult:
        """
        Run VAD analysis only — returns per-frame speech/silence timeline.
        Useful for debugging microphone input or inspecting recorded audio.

        params:
          audio_b64  : str   Base64-encoded audio bytes
          src_format : str   Container format hint (default: webm)
        """
        audio_b64  = params.get("audio_b64", "")
        src_format = params.get("src_format", "webm")

        if not audio_b64:
            return SkillResult.fail("VOICE_MISSING_AUDIO: audio_b64 is required")

        try:
            audio_bytes = base64.b64decode(audio_b64)
        except Exception:
            return SkillResult.fail("VOICE_INVALID_B64: could not decode audio_b64")

        from core.audio.io  import AudioIO
        from core.audio.vad import VAD

        io_conv  = AudioIO()
        vad      = VAD()

        try:
            pcm_bytes = await io_conv.to_pcm(audio_bytes, src_format=src_format)
        except Exception as exc:
            return SkillResult.fail(f"VOICE_IO_ERROR: {exc}")

        timeline = vad.analyse(pcm_bytes)
        segments = vad.split_segments(pcm_bytes)

        speech_frames  = sum(1 for f in timeline if f["is_speech"])
        total_frames   = len(timeline)
        speech_ratio   = round(speech_frames / total_frames, 3) if total_frames else 0

        return SkillResult.ok({
            "total_frames":   total_frames,
            "speech_frames":  speech_frames,
            "silence_frames": total_frames - speech_frames,
            "speech_ratio":   speech_ratio,
            "segments_found": len(segments),
            "timeline":       timeline[:200],  # cap at 200 frames for output size
        })

    async def _extract_features(self, params: dict, _confirmed: bool) -> SkillResult:
        """
        Extract MFCC features from audio without running recognition.
        Returns feature matrix statistics (not the full matrix — too large for tool output).

        params:
          audio_b64  : str   Base64-encoded audio bytes
          src_format : str   Container format hint (default: webm)
        """
        audio_b64  = params.get("audio_b64", "")
        src_format = params.get("src_format", "webm")

        if not audio_b64:
            return SkillResult.fail("VOICE_MISSING_AUDIO: audio_b64 is required")

        try:
            audio_bytes = base64.b64decode(audio_b64)
        except Exception:
            return SkillResult.fail("VOICE_INVALID_B64: could not decode audio_b64")

        from core.audio import AudioPipeline
        import numpy as np

        pipeline = AudioPipeline()
        features = await pipeline.extract_features(audio_bytes, src_format=src_format)

        if features is None:
            return SkillResult.ok({
                "has_features": False,
                "message": "No speech detected — features could not be extracted",
            })

        return SkillResult.ok({
            "has_features":  True,
            "shape":         list(features.shape),
            "n_frames":      features.shape[0],
            "n_coefficients": features.shape[1],
            "mean_energy":   round(float(np.abs(features).mean()), 4),
            "max_energy":    round(float(np.abs(features).max()), 4),
            "summary_vector_shape": [features.shape[1] * 2],
        })

    async def _diagnostics(self, params: dict, _confirmed: bool) -> SkillResult:
        """
        Return full pipeline diagnostics: model path, ffmpeg version, VAD config.
        """
        import shutil, asyncio, os
        from core.audio.recogniser import VoskRecogniser
        from core.audio.vad import VADConfig

        ffmpeg_bin     = os.getenv("FFMPEG_PATH", "ffmpeg")
        ffmpeg_version = "not found"
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
        return SkillResult.ok({
            "vosk":    VoskRecogniser.model_info(),
            "ffmpeg":  ffmpeg_version,
            "vad": {
                "frame_ms":         cfg.frame_ms,
                "energy_threshold": cfg.energy_threshold,
                "zcr_low":          cfg.zcr_low,
                "zcr_high":         cfg.zcr_high,
                "hangover_frames":  cfg.hangover_frames,
                "min_speech_frames": cfg.min_speech_frames,
            },
            "mfcc": {
                "n_mfcc":        13,
                "n_mels":        40,
                "sample_rate":   16000,
                "hop_length_ms": 10,
                "win_length_ms": 25,
            },
        })

    async def _model_status(self, params: dict, _confirmed: bool) -> SkillResult:
        """Quick check: is the Vosk model installed and ready?"""
        from core.audio.recogniser import VoskRecogniser
        info      = VoskRecogniser.model_info()
        available = VoskRecogniser.is_available()
        return SkillResult.ok({
            "available": available,
            "model":     info,
            "message":   (
                "Vosk model ready — offline STT active."
                if available else
                f"Vosk model not found at '{info.get('path')}'. "
                "Download from https://alphacephei.com/vosk/models and set VOSK_MODEL_PATH."
            ),
        })
