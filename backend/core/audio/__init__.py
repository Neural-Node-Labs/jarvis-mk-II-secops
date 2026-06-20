"""
core.audio — Modular audio processing pipeline.

Public API
----------
from core.audio import AudioPipeline, transcribe

AudioPipeline   — composable STT pipeline (IO → VAD → Features → Recogniser)
transcribe()    — one-shot convenience wrapper

Each sub-module is independently usable:
  from core.audio.io       import AudioIO          # format conversion
  from core.audio.vad      import VAD              # silence detection
  from core.audio.features import MFCCExtractor    # feature extraction
  from core.audio.recogniser import VoskRecogniser # speech recognition

version: 1.0.0
"""

from core.audio.pipeline import AudioPipeline
from core.audio.pipeline import transcribe

__all__ = ["AudioPipeline", "transcribe"]
