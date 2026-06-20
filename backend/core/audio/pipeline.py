"""
core.audio.pipeline — AudioPipeline
=====================================
Composable STT pipeline: AudioIO → VAD → MFCCExtractor → VoskRecogniser.

Each stage is independently injectable so you can:
  - swap VAD for a custom one
  - add feature-based pre-filtering before recognition
  - use the pipeline for tasks other than STT (e.g. speaker energy analysis,
    silence detection for scheduler, sentinel log alerting)

Standalone usage
----------------
    from core.audio import AudioPipeline, transcribe

    # One-shot helper (auto-configures everything from env vars)
    text = await transcribe(audio_bytes)

    # Full pipeline with custom config
    from core.audio.pipeline import AudioPipeline, PipelineConfig
    from core.audio.vad import VADConfig

    pipeline = AudioPipeline(PipelineConfig(
        sample_rate     = 16000,
        vad_config      = VADConfig(energy_threshold=0.008, hangover_frames=12),
        use_mfcc_filter = True,    # drop near-silent segments by MFCC energy
        min_segment_ms  = 300,     # discard segments shorter than 300 ms
    ))

    result: PipelineResult = await pipeline.run(audio_bytes, src_format="webm")
    print(result.transcript)       # final text
    print(result.segments_found)   # how many speech segments VAD detected
    print(result.duration_ms)      # total processing time

    # Reuse the pipeline across requests (model stays loaded)
    for blob in incoming_audio_blobs:
        r = await pipeline.run(blob)
        print(r.transcript)

Other use-cases
---------------
    # Sentinel: check if an audio alert contains speech
    pipeline = AudioPipeline()
    r = await pipeline.run(alert_audio)
    if r.has_speech:
        send_alert(r.transcript)

    # Scheduler: transcribe a voice-recorded task description
    r = await pipeline.run(voice_memo_bytes, src_format="m4a")
    create_task(description=r.transcript)

    # Feature extraction only (no recognition)
    from core.audio.pipeline import AudioPipeline
    p = AudioPipeline()
    features = await p.extract_features(audio_bytes)   # returns np.ndarray

version: 1.0.0
CBD Contract:
  IN:  audio bytes (any format) + optional src_format hint
  OUT: PipelineResult (transcript, segments, timing, features)
  Errors: re-raises AudioIOError, VoskNotAvailable, VoskModelError
          with added context. Safe to catch at the skill/endpoint layer.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from core.audio.io         import AudioIO, AudioIOError
from core.audio.vad        import VAD, VADConfig
from core.audio.features   import MFCCExtractor
from core.audio.recogniser import VoskRecogniser, VoskNotAvailable, VoskModelError

logger = logging.getLogger("audio.pipeline")


# ── Config ─────────────────────────────────────────────────────────────────────

@dataclass
class PipelineConfig:
    """
    All pipeline tuning parameters in one place.
    Every field has a sensible default so `PipelineConfig()` just works.
    """
    sample_rate:      int        = 16_000
    vad_config:       VADConfig  = field(default_factory=VADConfig)

    # MFCC-based pre-filter: skip segments whose mean energy is below this
    # threshold even after VAD passes them. Catches VAD false-positives like
    # keyboard noise and HVAC hum that pass the ZCR gate.
    use_mfcc_filter:  bool  = True
    mfcc_energy_floor: float = 0.003   # RMS energy in MFCC frame

    # Minimum speech segment length to bother transcribing (ms)
    min_segment_ms:   int   = 250

    # Maximum bytes to process (guard against huge uploads)
    max_audio_bytes:  int   = 25 * 1024 * 1024   # 25 MB


# ── Result ─────────────────────────────────────────────────────────────────────

@dataclass
class PipelineResult:
    """
    Full result from one pipeline run.

    Attributes
    ----------
    transcript      : str       Final joined transcript (all segments).
    has_speech      : bool      True if at least one speech segment was found.
    segments_found  : int       Number of speech segments VAD detected.
    segments_used   : int       Segments that passed the MFCC filter and were transcribed.
    duration_ms     : int       Total pipeline wall-clock time in milliseconds.
    audio_duration_s: float     Duration of the input audio in seconds.
    raw_pcm_bytes   : int       Size of the decoded PCM in bytes.
    stage_times_ms  : dict      Per-stage timing breakdown.
    features        : Optional[np.ndarray]  MFCC features (first segment only, if computed).
    error           : Optional[str]         Set if a non-fatal error occurred.
    """
    transcript:       str
    has_speech:       bool
    segments_found:   int
    segments_used:    int
    duration_ms:      int
    audio_duration_s: float       = 0.0
    raw_pcm_bytes:    int         = 0
    stage_times_ms:   dict        = field(default_factory=dict)
    features:         Optional[np.ndarray] = None
    error:            Optional[str] = None


# ── Pipeline ───────────────────────────────────────────────────────────────────

class AudioPipeline:
    """
    Four-stage audio processing pipeline.

    Stage 1 — IO         : convert any audio format → raw PCM (ffmpeg)
    Stage 2 — VAD        : split PCM → speech segments (energy + ZCR)
    Stage 3 — Features   : extract MFCCs per segment (optional pre-filter)
    Stage 4 — Recogniser : transcribe each segment (Vosk, offline)

    All stages run in the same asyncio event loop. The ffmpeg subprocess
    (Stage 1) is the only I/O-bound step; Stages 2–4 are CPU-bound but
    fast enough on small audio clips that they don't need a thread pool.
    For very long recordings (> 30 s), consider running in an executor.

    The pipeline is stateless across calls — safe to reuse concurrently.
    The Vosk model is cached internally (class-level singleton by path).

    Parameters
    ----------
    config : PipelineConfig, optional
        Full config. Defaults work for most use-cases.
    io : AudioIO, optional
        Custom AudioIO instance (e.g. with a different ffmpeg path).
    vad : VAD, optional
        Custom VAD instance.
    extractor : MFCCExtractor, optional
        Custom MFCC extractor.
    recogniser : VoskRecogniser, optional
        Custom recogniser (e.g. a different model path).
    """

    def __init__(
        self,
        config:     PipelineConfig    | None = None,
        io:         AudioIO           | None = None,
        vad:        VAD               | None = None,
        extractor:  MFCCExtractor     | None = None,
        recogniser: VoskRecogniser    | None = None,
    ):
        self.cfg        = config or PipelineConfig()
        self._io        = io         or AudioIO(sample_rate=self.cfg.sample_rate)
        self._vad       = vad        or VAD(self.cfg.vad_config)
        self._extractor = extractor  or MFCCExtractor(sample_rate=self.cfg.sample_rate)
        self._recogniser = recogniser or VoskRecogniser(sample_rate=self.cfg.sample_rate)

    # ── Public API ─────────────────────────────────────────────────────────────

    async def run(
        self,
        audio_bytes: bytes,
        src_format:  str = "webm",
    ) -> PipelineResult:
        """
        Run the full STT pipeline on raw audio bytes.

        Parameters
        ----------
        audio_bytes : bytes
            Raw audio in any format ffmpeg supports (webm, ogg, mp4, wav, …).
        src_format : str
            Container format hint for ffmpeg. Default "webm".

        Returns
        -------
        PipelineResult
            Always returns a result — never raises. Non-fatal errors are
            captured in result.error so the caller can log and continue.
        """
        wall_start = time.monotonic()
        stage_times: dict[str, int] = {}

        if not audio_bytes:
            return PipelineResult(
                transcript="", has_speech=False, segments_found=0,
                segments_used=0, duration_ms=0, error="EMPTY_INPUT",
            )

        if len(audio_bytes) > self.cfg.max_audio_bytes:
            return PipelineResult(
                transcript="", has_speech=False, segments_found=0,
                segments_used=0, duration_ms=0,
                error=f"AUDIO_TOO_LARGE: {len(audio_bytes)} bytes > {self.cfg.max_audio_bytes}",
            )

        # ── Stage 1: IO ────────────────────────────────────────────────────────
        t0 = time.monotonic()
        try:
            pcm_bytes = await self._io.to_pcm(audio_bytes, src_format=src_format)
        except AudioIOError as exc:
            return PipelineResult(
                transcript="", has_speech=False, segments_found=0,
                segments_used=0,
                duration_ms=int((time.monotonic() - wall_start) * 1000),
                error=f"IO_ERROR: {exc}",
            )
        stage_times["io_ms"] = int((time.monotonic() - t0) * 1000)

        audio_duration_s = len(pcm_bytes) / (self.cfg.sample_rate * 2)   # s16le = 2 bytes/sample
        logger.info(
            "[pipeline] io done: pcm=%d bytes audio=%.1fs",
            len(pcm_bytes), audio_duration_s,
        )

        # ── Stage 2: VAD ───────────────────────────────────────────────────────
        t0 = time.monotonic()
        segments = self._vad.split_segments(pcm_bytes)
        stage_times["vad_ms"] = int((time.monotonic() - t0) * 1000)
        logger.info("[pipeline] vad: %d segment(s)", len(segments))

        # Filter by minimum duration
        min_bytes = int(self.cfg.min_segment_ms * self.cfg.sample_rate * 2 / 1000)
        segments  = [s for s in segments if len(s) >= min_bytes]

        if not segments:
            return PipelineResult(
                transcript="", has_speech=False, segments_found=0,
                segments_used=0, raw_pcm_bytes=len(pcm_bytes),
                duration_ms=int((time.monotonic() - wall_start) * 1000),
                audio_duration_s=audio_duration_s,
                stage_times_ms=stage_times,
            )

        segments_found = len(segments)

        # ── Stage 3: MFCC filter (optional) ───────────────────────────────────
        t0 = time.monotonic()
        first_features: Optional[np.ndarray] = None
        segments_to_recognise: list[bytes]   = []

        for i, seg in enumerate(segments):
            try:
                signal = self._io.pcm_to_numpy(seg)
                if self.cfg.use_mfcc_filter:
                    energy = self._extractor.frame_energy(signal)
                    if energy.size > 0 and float(energy.mean()) < self.cfg.mfcc_energy_floor:
                        logger.debug("[pipeline] segment %d dropped by MFCC filter (energy=%.5f)", i, energy.mean())
                        continue
                if i == 0 and first_features is None:
                    first_features = self._extractor.from_array(signal)
            except Exception as exc:
                logger.warning("[pipeline] feature extraction failed for segment %d: %s", i, exc)
            segments_to_recognise.append(seg)

        stage_times["features_ms"] = int((time.monotonic() - t0) * 1000)

        if not segments_to_recognise:
            return PipelineResult(
                transcript="", has_speech=True, segments_found=segments_found,
                segments_used=0, raw_pcm_bytes=len(pcm_bytes),
                duration_ms=int((time.monotonic() - wall_start) * 1000),
                audio_duration_s=audio_duration_s,
                stage_times_ms=stage_times,
                features=first_features,
            )

        # ── Stage 4: Recognition ───────────────────────────────────────────────
        t0 = time.monotonic()
        try:
            # Run in a thread pool so Vosk's CPU work doesn't block the event loop
            loop = asyncio.get_event_loop()
            transcript = await loop.run_in_executor(
                None,
                self._recogniser.transcribe_segments,
                segments_to_recognise,
            )
        except (VoskNotAvailable, VoskModelError) as exc:
            return PipelineResult(
                transcript="", has_speech=True, segments_found=segments_found,
                segments_used=len(segments_to_recognise),
                raw_pcm_bytes=len(pcm_bytes),
                duration_ms=int((time.monotonic() - wall_start) * 1000),
                audio_duration_s=audio_duration_s,
                stage_times_ms=stage_times,
                features=first_features,
                error=f"RECOGNISER_ERROR: {exc}",
            )
        except Exception as exc:
            logger.error("[pipeline] recogniser error: %s", exc)
            return PipelineResult(
                transcript="", has_speech=True, segments_found=segments_found,
                segments_used=len(segments_to_recognise),
                raw_pcm_bytes=len(pcm_bytes),
                duration_ms=int((time.monotonic() - wall_start) * 1000),
                audio_duration_s=audio_duration_s,
                stage_times_ms=stage_times,
                features=first_features,
                error=f"RECOGNISER_UNEXPECTED: {exc}",
            )

        stage_times["recogniser_ms"] = int((time.monotonic() - t0) * 1000)
        duration_ms = int((time.monotonic() - wall_start) * 1000)

        logger.info(
            "[pipeline] done: segs=%d/%d transcript=%r duration_ms=%d stages=%s",
            len(segments_to_recognise), segments_found,
            transcript[:60], duration_ms, stage_times,
        )

        return PipelineResult(
            transcript       = transcript,
            has_speech       = True,
            segments_found   = segments_found,
            segments_used    = len(segments_to_recognise),
            raw_pcm_bytes    = len(pcm_bytes),
            duration_ms      = duration_ms,
            audio_duration_s = audio_duration_s,
            stage_times_ms   = stage_times,
            features         = first_features,
        )

    async def extract_features(
        self,
        audio_bytes: bytes,
        src_format:  str = "webm",
    ) -> Optional[np.ndarray]:
        """
        Run only Stages 1–3: convert + VAD + MFCC extraction.
        Returns the MFCC feature matrix for the first speech segment,
        or None if no speech was found. Does NOT run the recogniser.

        Useful for speaker identification, emotion detection, or any
        downstream task that wants features but not a transcript.
        """
        try:
            pcm_bytes = await self._io.to_pcm(audio_bytes, src_format=src_format)
            segments  = self._vad.split_segments(pcm_bytes)
            if not segments:
                return None
            signal    = self._io.pcm_to_numpy(segments[0])
            return self._extractor.from_array(signal)
        except Exception as exc:
            logger.warning("[pipeline.extract_features] %s", exc)
            return None

    def vad_timeline(self, pcm_bytes: bytes) -> list[dict]:
        """
        Return the VAD frame-by-frame analysis for debugging.
        Input must already be raw s16le PCM (not raw audio).
        """
        return self._vad.analyse(pcm_bytes)


# ── Convenience function ───────────────────────────────────────────────────────

_default_pipeline: Optional[AudioPipeline] = None


async def transcribe(
    audio_bytes: bytes,
    src_format:  str = "webm",
    config:      PipelineConfig | None = None,
) -> str:
    """
    One-shot transcription convenience function.

    Creates (or reuses) a default pipeline and returns the transcript string.
    Raises nothing — returns "" on any error.

    Parameters
    ----------
    audio_bytes : bytes    Raw audio in any format.
    src_format  : str      Container hint. Default "webm".
    config      : PipelineConfig, optional
                           If provided, a new pipeline is created with this config.
                           If omitted, the module-level default pipeline is reused.

    Returns
    -------
    str
        Transcribed text, or "" if recognition failed or no speech found.
    """
    global _default_pipeline

    if config is not None:
        pipeline = AudioPipeline(config=config)
    else:
        if _default_pipeline is None:
            _default_pipeline = AudioPipeline()
        pipeline = _default_pipeline

    result = await pipeline.run(audio_bytes, src_format=src_format)
    if result.error:
        logger.warning("[transcribe] %s", result.error)
    return result.transcript
