"""
core.audio.vad — VAD (Voice Activity Detection)
================================================
Pure-numpy energy + zero-crossing-rate silence detector and segment splitter.

No ML model, no external dependencies beyond numpy.
Works on raw s16le PCM bytes or float32 numpy arrays.

Standalone usage
----------------
    from core.audio.vad import VAD

    vad = VAD(sample_rate=16000)

    # Detect whether a chunk is speech
    is_speech: bool = vad.is_speech(pcm_bytes)

    # Split a long recording into speech segments (strips silence)
    segments: list[bytes] = vad.split_segments(pcm_bytes)

    # Get a timeline of speech/silence decisions
    timeline: list[dict] = vad.analyse(pcm_bytes)

How it works
------------
Each frame (default 30 ms) is scored by two features:

1. Short-time energy (STE)
   Sum of squared sample amplitudes, normalised to [0, 1].
   High energy → likely speech or loud noise.

2. Zero-crossing rate (ZCR)
   How many times the signal crosses zero per second.
   Speech: 1000–4000 Hz → ~2000–8000 crossings/s.
   Silence: very low ZCR.
   Unvoiced consonants (s, f, sh): very high ZCR.

Decision rule:
   frame is SPEECH  if  (energy > energy_threshold) AND
                         (zcr    > zcr_low_threshold) AND
                         (zcr    < zcr_high_threshold)

Segments are formed by merging consecutive speech frames with
configurable min/max speech duration and hangover (trailing silence
to avoid clipping word endings).

Parameters (all tunable via constructor)
-----------------------------------------
  sample_rate         int     16000
  frame_ms            int     30        frame duration in ms
  energy_threshold    float   0.005     normalised RMS energy floor
  zcr_low             int     500       min ZCR for voiced speech (Hz)
  zcr_high            int     6000      max ZCR (above = background hiss)
  hangover_frames     int     8         silence frames before segment end
  min_speech_frames   int     5         minimum frames to count as speech
  min_silence_frames  int     10        silence gap needed to split segments
  max_segment_s       float   30.0      hard cap per segment (seconds)
  pre_roll_frames     int     2         frames prepended before speech onset

version: 1.0.0
CBD Contract:
  IN:  raw PCM bytes (s16le, mono) OR numpy float32 array
  OUT: list[bytes] segments OR bool OR list[dict] timeline
  Errors: VADError for invalid input
"""

import logging
from dataclasses import dataclass, field
from typing import List

import numpy as np

logger = logging.getLogger("audio.vad")


class VADError(ValueError):
    """Raised for invalid input to VAD methods."""


@dataclass
class VADConfig:
    """All VAD tuning parameters in one place."""
    sample_rate:        int   = 16_000
    frame_ms:           int   = 30
    energy_threshold:   float = 0.005
    zcr_low:            int   = 500
    zcr_high:           int   = 6_000
    hangover_frames:    int   = 8
    min_speech_frames:  int   = 5
    min_silence_frames: int   = 10
    max_segment_s:      float = 30.0
    pre_roll_frames:    int   = 2


@dataclass
class FrameResult:
    """Result for a single VAD frame."""
    frame_idx:   int
    time_s:      float
    energy:      float
    zcr:         float
    is_speech:   bool


class VAD:
    """
    Voice Activity Detector.

    All public methods accept either raw PCM bytes (s16le, mono)
    or a float32 numpy array in [-1, 1].
    """

    def __init__(self, config: VADConfig | None = None, **kwargs):
        """
        Parameters
        ----------
        config : VADConfig, optional
            Full config object. If omitted, kwargs are passed to VADConfig.

        Examples
        --------
        VAD()
        VAD(energy_threshold=0.01, hangover_frames=12)
        VAD(VADConfig(frame_ms=20, zcr_high=5000))
        """
        self.cfg = config or VADConfig(**kwargs)
        self._frame_len = int(self.cfg.sample_rate * self.cfg.frame_ms / 1000)

    # ── Public API ─────────────────────────────────────────────────────────────

    def is_speech(self, audio: bytes | np.ndarray) -> bool:
        """
        Return True if the audio chunk contains detectable speech.
        Uses the mean speech-frame ratio across all frames.
        """
        signal = self._to_float32(audio)
        if signal.size == 0:
            return False
        results = self._analyse_frames(signal)
        if not results:
            return False
        speech_ratio = sum(1 for r in results if r.is_speech) / len(results)
        return speech_ratio > 0.25   # at least 25 % of frames are voiced

    def split_segments(self, audio: bytes | np.ndarray) -> list[bytes]:
        """
        Split audio into voiced speech segments, stripping silence.

        Returns
        -------
        list[bytes]
            Each element is a raw s16le PCM byte string of one speech segment.
            Returns [audio] (the whole input) if no silence is found.
        """
        signal  = self._to_float32(audio)
        results = self._analyse_frames(signal)
        if not results:
            return [self._to_bytes(signal)]

        segments      = []
        in_speech     = False
        speech_start  = 0
        hangover      = 0
        silence_count = 0
        speech_count  = 0

        for i, frame in enumerate(results):
            if frame.is_speech:
                silence_count = 0
                speech_count += 1
                hangover = self.cfg.hangover_frames
                if not in_speech:
                    # Pre-roll: go back a few frames to catch attack transients
                    speech_start = max(0, i - self.cfg.pre_roll_frames)
                    in_speech    = True
            else:
                silence_count += 1
                if in_speech:
                    if hangover > 0:
                        hangover -= 1
                    else:
                        # End of segment
                        if speech_count >= self.cfg.min_speech_frames:
                            seg = self._extract_segment(signal, speech_start, i)
                            if seg is not None:
                                segments.append(seg)
                        in_speech     = False
                        speech_count  = 0
                        silence_count = 0

            # Hard-cap: flush if segment runs too long
            if in_speech:
                duration_s = (i - speech_start) * self.cfg.frame_ms / 1000
                if duration_s >= self.cfg.max_segment_s:
                    seg = self._extract_segment(signal, speech_start, i)
                    if seg is not None:
                        segments.append(seg)
                    speech_start  = i
                    speech_count  = 0

        # Flush trailing speech
        if in_speech and speech_count >= self.cfg.min_speech_frames:
            seg = self._extract_segment(signal, speech_start, len(results))
            if seg is not None:
                segments.append(seg)

        if not segments:
            # No silence detected — return whole audio as one segment
            logger.debug("[vad] no silence found — returning full audio")
            return [self._to_bytes(signal)]

        logger.debug("[vad] split → %d segment(s)", len(segments))
        return segments

    def analyse(self, audio: bytes | np.ndarray) -> list[dict]:
        """
        Return a frame-by-frame timeline for debugging/visualisation.

        Returns
        -------
        list[dict]
            Each dict: { frame, time_s, energy, zcr, is_speech }
        """
        signal  = self._to_float32(audio)
        results = self._analyse_frames(signal)
        return [
            {
                "frame":     r.frame_idx,
                "time_s":    round(r.time_s, 3),
                "energy":    round(r.energy, 6),
                "zcr":       round(r.zcr, 1),
                "is_speech": r.is_speech,
            }
            for r in results
        ]

    def trim_silence(self, audio: bytes | np.ndarray) -> bytes:
        """
        Remove leading and trailing silence from audio.
        Returns raw s16le bytes.
        """
        segments = self.split_segments(audio)
        if not segments:
            return b""
        # Concatenate all segments (already silence-trimmed individually)
        signal = self._to_float32(b"".join(segments))
        return self._to_bytes(signal)

    # ── Frame analysis ─────────────────────────────────────────────────────────

    def _analyse_frames(self, signal: np.ndarray) -> list[FrameResult]:
        """Compute energy + ZCR for each frame and classify speech/silence."""
        n      = len(signal)
        fl     = self._frame_len
        n_frames = n // fl
        results  = []

        for i in range(n_frames):
            frame = signal[i * fl : (i + 1) * fl]
            energy = float(np.sqrt(np.mean(frame ** 2)))         # RMS energy
            zcr    = self._zero_crossing_rate(frame)

            is_speech = (
                energy > self.cfg.energy_threshold
                and self.cfg.zcr_low  < zcr < self.cfg.zcr_high
            )

            results.append(FrameResult(
                frame_idx = i,
                time_s    = i * self.cfg.frame_ms / 1000,
                energy    = energy,
                zcr       = zcr,
                is_speech = is_speech,
            ))

        return results

    @staticmethod
    def _zero_crossing_rate(frame: np.ndarray) -> float:
        """
        Compute zero-crossing rate in crossings per second.
        A zero crossing occurs when consecutive samples have opposite signs.
        """
        if len(frame) < 2:
            return 0.0
        crossings = np.sum(np.diff(np.sign(frame)) != 0)
        # Normalise: ZCR = crossings / frame_duration_s
        # frame duration = len(frame) / sample_rate — but we don't store sample_rate
        # here, so return raw crossings-per-frame and scale at call site.
        # Actually: to keep it in Hz-equivalent, we need sample_rate.
        # We return crossings * (sample_rate / frame_len) at call site.
        return float(crossings)

    def _analyse_frames(self, signal: np.ndarray) -> list[FrameResult]:
        """Compute energy + ZCR for each frame and classify speech/silence."""
        n        = len(signal)
        fl       = self._frame_len
        n_frames = n // fl
        results  = []
        sr       = self.cfg.sample_rate

        for i in range(n_frames):
            frame  = signal[i * fl : (i + 1) * fl]
            energy = float(np.sqrt(np.mean(frame ** 2)))

            # ZCR in crossings per second
            crossings = int(np.sum(np.diff(np.sign(frame)) != 0))
            zcr = crossings * (sr / fl)

            is_speech = (
                energy > self.cfg.energy_threshold
                and self.cfg.zcr_low < zcr < self.cfg.zcr_high
            )

            results.append(FrameResult(
                frame_idx = i,
                time_s    = i * self.cfg.frame_ms / 1000,
                energy    = energy,
                zcr       = zcr,
                is_speech = is_speech,
            ))

        return results

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _extract_segment(
        self, signal: np.ndarray, start_frame: int, end_frame: int
    ) -> bytes | None:
        fl    = self._frame_len
        start = start_frame * fl
        end   = min(end_frame * fl, len(signal))
        if end <= start:
            return None
        seg = signal[start:end]
        return self._to_bytes(seg)

    @staticmethod
    def _to_float32(audio: bytes | np.ndarray) -> np.ndarray:
        """Convert s16le bytes or float32 array to float32 in [-1, 1]."""
        if isinstance(audio, np.ndarray):
            if audio.dtype == np.float32:
                return audio
            return audio.astype(np.float32)
        if not audio:
            return np.array([], dtype=np.float32)
        arr = np.frombuffer(audio, dtype=np.int16)
        return arr.astype(np.float32) / 32768.0

    @staticmethod
    def _to_bytes(signal: np.ndarray) -> bytes:
        """Convert float32 array back to s16le bytes."""
        clipped = np.clip(signal, -1.0, 1.0)
        return (clipped * 32767).astype(np.int16).tobytes()
