"""
core.audio.io — AudioIO
=======================
Converts any audio format (webm, ogg, mp4, m4a, mp3, wav …) to raw
16-bit signed little-endian PCM at a target sample rate.

Uses ffmpeg as the only system dependency — already present in the
Dockerfile (kali-rolling base image ships ffmpeg via apt).

Standalone usage
----------------
    from core.audio.io import AudioIO

    io = AudioIO(sample_rate=16000)

    # From bytes (e.g. UploadFile.read())
    pcm: bytes = await io.to_pcm(audio_bytes, src_format="webm")

    # From file path
    pcm: bytes = await io.file_to_pcm("/tmp/recording.webm")

    # Probe duration without decoding
    duration_s: float = await io.probe_duration("/tmp/recording.webm")

Design notes
------------
- No Python audio libraries (soundfile, librosa, pydub) are used.
  ffmpeg handles every codec/container combination.
- Output is always: 16 kHz (configurable), mono, s16le raw PCM.
  This is the format Vosk, faster-whisper, and most ML audio models expect.
- Subprocess is run via asyncio so the FastAPI event loop never blocks.
- Temp files are cleaned up in finally blocks regardless of outcome.

version: 1.0.0
CBD Contract:
  IN:  audio bytes (any container/codec) OR file path
  OUT: raw PCM bytes (s16le, mono, configurable sample rate)
  Errors: AudioIOError wraps subprocess stderr for caller clarity
"""

import asyncio
import logging
import os
import tempfile

logger = logging.getLogger("audio.io")

FFMPEG_BIN   = os.getenv("FFMPEG_PATH", "ffmpeg")
DEFAULT_RATE = 16_000   # Hz — Vosk and Whisper both want 16 kHz


class AudioIOError(RuntimeError):
    """Raised when ffmpeg conversion fails."""


class AudioIO:
    """
    Format-agnostic audio converter built on ffmpeg.

    Parameters
    ----------
    sample_rate : int
        Output PCM sample rate in Hz. Default 16000.
    ffmpeg_bin : str
        Path to the ffmpeg executable. Defaults to FFMPEG_PATH env var or 'ffmpeg'.
    timeout : int
        Max seconds to wait for ffmpeg. Default 60.
    """

    def __init__(
        self,
        sample_rate: int = DEFAULT_RATE,
        ffmpeg_bin: str  = FFMPEG_BIN,
        timeout: int     = 60,
    ):
        self.sample_rate = sample_rate
        self.ffmpeg_bin  = ffmpeg_bin
        self.timeout     = timeout

    # ── Public API ─────────────────────────────────────────────────────────────

    async def to_pcm(self, audio_bytes: bytes, src_format: str = "webm") -> bytes:
        """
        Convert raw audio bytes to s16le PCM.

        Parameters
        ----------
        audio_bytes : bytes
            Raw audio data in any format ffmpeg supports.
        src_format : str
            Container hint (e.g. 'webm', 'ogg', 'mp4').
            Passed to ffmpeg as -f <src_format> to help it demux correctly.

        Returns
        -------
        bytes
            Raw PCM: 16-bit signed little-endian, mono, self.sample_rate Hz.
        """
        if not audio_bytes:
            raise AudioIOError("Empty audio input")

        src_path = dst_path = None
        try:
            # Write source to a temp file so ffmpeg can seek it
            with tempfile.NamedTemporaryFile(
                suffix=f".{src_format}", delete=False
            ) as src_f:
                src_f.write(audio_bytes)
                src_path = src_f.name

            dst_path = src_path + ".pcm"
            await self._run_ffmpeg(src_path, dst_path, src_format=src_format)

            with open(dst_path, "rb") as f:
                return f.read()

        finally:
            for p in (src_path, dst_path):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

    async def file_to_pcm(self, path: str) -> bytes:
        """
        Convert an on-disk audio file to s16le PCM.

        Parameters
        ----------
        path : str
            Absolute or relative path to the audio file.

        Returns
        -------
        bytes
            Raw PCM: 16-bit signed little-endian, mono, self.sample_rate Hz.
        """
        if not os.path.isfile(path):
            raise AudioIOError(f"File not found: {path}")

        dst_path = path + ".pcm"
        try:
            await self._run_ffmpeg(path, dst_path)
            with open(dst_path, "rb") as f:
                return f.read()
        finally:
            if os.path.exists(dst_path):
                try:
                    os.remove(dst_path)
                except Exception:
                    pass

    async def probe_duration(self, path: str) -> float:
        """
        Return audio duration in seconds without decoding the full file.

        Uses ffprobe (ships with ffmpeg). Returns 0.0 on failure.
        """
        ffprobe = self.ffmpeg_bin.replace("ffmpeg", "ffprobe")
        cmd = [
            ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return float(stdout.decode().strip())
        except Exception:
            return 0.0

    def pcm_to_numpy(self, pcm_bytes: bytes):
        """
        Convert raw s16le PCM bytes to a float32 numpy array in [-1, 1].

        Returns numpy.ndarray of shape (n_samples,).
        Raises ImportError if numpy is not installed.
        """
        import numpy as np
        arr = np.frombuffer(pcm_bytes, dtype=np.int16)
        return arr.astype(np.float32) / 32768.0

    # ── Internal ───────────────────────────────────────────────────────────────

    async def _run_ffmpeg(
        self,
        src: str,
        dst: str,
        src_format: str | None = None,
    ) -> None:
        """
        Run ffmpeg to convert src → dst (raw s16le PCM).

        ffmpeg flags:
          -y            overwrite output without asking
          -f <fmt>      input format hint (optional)
          -i <src>      input file
          -vn           drop video stream
          -acodec pcm_s16le   16-bit signed little-endian PCM
          -ar <rate>    resample to target sample rate
          -ac 1         downmix to mono
          -f s16le      raw PCM output container (no header)
        """
        cmd = [self.ffmpeg_bin, "-y"]
        if src_format:
            cmd += ["-f", src_format]
        cmd += [
            "-i",      src,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar",     str(self.sample_rate),
            "-ac",     "1",
            "-f",      "s16le",
            dst,
        ]

        logger.debug("[audio.io] ffmpeg %s → %s", src, dst)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            raise AudioIOError(f"ffmpeg timed out after {self.timeout}s")

        if proc.returncode != 0:
            err = stderr.decode(errors="replace")[-600:]
            raise AudioIOError(f"ffmpeg error (rc={proc.returncode}): {err}")

        logger.debug("[audio.io] converted ok → %s (%d bytes)", dst, os.path.getsize(dst))
