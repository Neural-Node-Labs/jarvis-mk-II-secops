"""
core.audio.recogniser — VoskRecogniser
=======================================
Offline speech-to-text using the Vosk toolkit.

Vosk facts
----------
- Apache 2.0 licensed. Completely free. No API key, no internet, no cost.
- Smallest useful model (vosk-model-small-en-us): ~40 MB.
- Standard English model (vosk-model-en-us-0.22): ~1.8 GB, excellent quality.
- Runs fully on CPU. No GPU required.
- Accepts raw s16le PCM at 16 kHz (exactly what AudioIO produces).

Model download (run once, or add to Dockerfile)
------------------------------------------------
    # Small model (fast, ~40 MB, good for command detection)
    wget -q https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
    unzip vosk-model-small-en-us-0.15.zip -d /app/data/models/

    # Full model (accurate, ~1.8 GB, best for free-form speech)
    wget -q https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip
    unzip vosk-model-en-us-0.22.zip -d /app/data/models/

    # Set env var to point at the extracted folder
    VOSK_MODEL_PATH=/app/data/models/vosk-model-small-en-us-0.15

Standalone usage
----------------
    from core.audio.recogniser import VoskRecogniser

    rec = VoskRecogniser()                    # uses VOSK_MODEL_PATH env var
    rec = VoskRecogniser("/path/to/model")    # explicit path

    # Transcribe raw PCM bytes (s16le, 16 kHz, mono)
    text: str = rec.transcribe(pcm_bytes)

    # Transcribe multiple segments (e.g. from VAD.split_segments)
    text: str = rec.transcribe_segments([seg1, seg2, seg3])

    # Stream-mode: feed chunks incrementally
    rec.reset()
    rec.feed(chunk1)
    rec.feed(chunk2)
    result = rec.finalise()

    # Check model availability without raising
    ok: bool = VoskRecogniser.is_available()
    info: dict = VoskRecogniser.model_info("/path/to/model")

Design notes
------------
- The Vosk model is loaded once and cached as a class-level singleton
  (keyed by path) to avoid expensive reloads on every request.
- The recogniser is NOT thread-safe — each concurrent request gets its
  own KaldiRecognizer instance. The model itself is shared safely.
- Vosk works best on 16 kHz mono s16le PCM, which is exactly what
  AudioIO.to_pcm() outputs — no resampling needed between modules.
- If vosk is not installed, all public methods raise VoskNotAvailable
  with a clear install message rather than an ImportError cascade.

version: 1.0.0
CBD Contract:
  IN:  raw PCM bytes (s16le, 16 kHz, mono)
  OUT: str transcript
  Errors: VoskNotAvailable, VoskModelError, VoskTranscribeError
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger("audio.recogniser")

VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", "/app/data/models/vosk-model-small-en-us-0.15")
DEFAULT_SAMPLE_RATE = 16_000


# ── Exceptions ─────────────────────────────────────────────────────────────────

class VoskNotAvailable(RuntimeError):
    """Raised when the vosk package is not installed."""


class VoskModelError(RuntimeError):
    """Raised when the model path is missing or invalid."""


class VoskTranscribeError(RuntimeError):
    """Raised when transcription fails unexpectedly."""


# ── Model cache ────────────────────────────────────────────────────────────────

_MODEL_CACHE: dict[str, object] = {}


def _load_model(model_path: str):
    """
    Load a Vosk model from disk, caching it by path.
    Subsequent calls with the same path return the cached model instantly.
    """
    if model_path in _MODEL_CACHE:
        return _MODEL_CACHE[model_path]

    try:
        from vosk import Model, SetLogLevel
        SetLogLevel(-1)   # suppress Vosk's verbose kaldi logs
    except ImportError:
        raise VoskNotAvailable(
            "vosk is not installed. Add 'vosk' to requirements.txt and rebuild. "
            "Download a model from https://alphacephei.com/vosk/models"
        )

    if not os.path.isdir(model_path):
        raise VoskModelError(
            f"Vosk model not found at '{model_path}'. "
            f"Set VOSK_MODEL_PATH env var or pass model_path explicitly. "
            f"Download: https://alphacephei.com/vosk/models"
        )

    logger.info("[recogniser] loading Vosk model from %s …", model_path)
    model = Model(model_path)
    _MODEL_CACHE[model_path] = model
    logger.info("[recogniser] Vosk model loaded ok: %s", model_path)
    return model


# ── Main class ─────────────────────────────────────────────────────────────────

class VoskRecogniser:
    """
    Offline speech recogniser backed by Vosk.

    Each instance owns one KaldiRecognizer (not thread-safe).
    The underlying Model is shared across instances (thread-safe, cached).

    Parameters
    ----------
    model_path  : str, optional
        Path to the extracted Vosk model directory.
        Defaults to VOSK_MODEL_PATH environment variable.
    sample_rate : int
        Input PCM sample rate. Must match AudioIO output. Default 16000.
    """

    def __init__(
        self,
        model_path:  str | None = None,
        sample_rate: int        = DEFAULT_SAMPLE_RATE,
    ):
        self.model_path  = model_path or VOSK_MODEL_PATH
        self.sample_rate = sample_rate
        self._model      = None   # lazy: loaded on first transcribe call
        self._rec        = None   # KaldiRecognizer instance

    # ── Public API ─────────────────────────────────────────────────────────────

    def transcribe(self, pcm_bytes: bytes) -> str:
        """
        Transcribe a complete PCM audio clip.

        Parameters
        ----------
        pcm_bytes : bytes
            Raw s16le PCM, mono, self.sample_rate Hz.

        Returns
        -------
        str
            Transcribed text, stripped of leading/trailing whitespace.
            Returns "" if nothing was recognised.
        """
        if not pcm_bytes:
            return ""

        self._ensure_loaded()
        rec = self._make_recognizer()

        # Feed in chunks so Vosk can do incremental decoding
        chunk_size = 4000
        for i in range(0, len(pcm_bytes), chunk_size):
            chunk = pcm_bytes[i : i + chunk_size]
            rec.AcceptWaveform(chunk)

        result = json.loads(rec.FinalResult())
        text   = result.get("text", "").strip()
        logger.debug("[recogniser] transcript=%r", text[:80])
        return text

    def transcribe_segments(self, segments: list[bytes]) -> str:
        """
        Transcribe multiple PCM segments (e.g. from VAD.split_segments)
        and join the results.

        Parameters
        ----------
        segments : list[bytes]
            List of raw s16le PCM byte strings.

        Returns
        -------
        str
            Space-joined transcripts of all segments.
        """
        if not segments:
            return ""
        parts = [self.transcribe(seg) for seg in segments if seg]
        return " ".join(p for p in parts if p).strip()

    def reset(self) -> None:
        """
        Reset the recogniser state for a new stream.
        Call this before feeding chunks for a new utterance.
        """
        self._ensure_loaded()
        self._rec = self._make_recognizer()

    def feed(self, pcm_chunk: bytes) -> Optional[str]:
        """
        Feed a chunk of PCM to the running recogniser.

        Returns a partial result string if Vosk detected a phrase boundary,
        or None if it's still accumulating.

        Call reset() before starting a new stream.
        """
        if self._rec is None:
            self.reset()
        if self._rec.AcceptWaveform(pcm_chunk):
            result = json.loads(self._rec.Result())
            return result.get("text", "").strip() or None
        return None

    def finalise(self) -> str:
        """
        Flush the recogniser and return the final transcript.
        Call after the last feed() chunk.
        """
        if self._rec is None:
            return ""
        result = json.loads(self._rec.FinalResult())
        self._rec = None
        return result.get("text", "").strip()

    # ── Class-level utilities ──────────────────────────────────────────────────

    @staticmethod
    def is_available(model_path: str | None = None) -> bool:
        """
        Return True if vosk is installed AND the model exists.
        Safe to call without raising.
        """
        try:
            import vosk  # noqa: F401
        except ImportError:
            return False
        path = model_path or VOSK_MODEL_PATH
        return os.path.isdir(path)

    @staticmethod
    def model_info(model_path: str | None = None) -> dict:
        """
        Return information about the configured model path.
        Does not load the model.
        """
        path    = model_path or VOSK_MODEL_PATH
        exists  = os.path.isdir(path)
        size_mb = 0.0
        files   = 0
        if exists:
            try:
                for dirpath, _, fnames in os.walk(path):
                    for fn in fnames:
                        size_mb += os.path.getsize(os.path.join(dirpath, fn))
                        files   += 1
                size_mb /= (1024 * 1024)
            except Exception:
                pass
        return {
            "path":       path,
            "exists":     exists,
            "size_mb":    round(size_mb, 1),
            "file_count": files,
            "vosk_installed": _vosk_importable(),
        }

    # ── Internal ───────────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._model is None:
            self._model = _load_model(self.model_path)

    def _make_recognizer(self):
        """Create a fresh KaldiRecognizer from the cached model."""
        try:
            from vosk import KaldiRecognizer
            return KaldiRecognizer(self._model, self.sample_rate)
        except Exception as exc:
            raise VoskTranscribeError(f"Failed to create recognizer: {exc}") from exc


def _vosk_importable() -> bool:
    try:
        import vosk  # noqa: F401
        return True
    except ImportError:
        return False
