"""
core.audio.features — MFCCExtractor
=====================================
Pure-numpy Mel-frequency cepstral coefficient (MFCC) extractor.

No librosa, no torchaudio, no scipy. Only numpy and the standard library.

What MFCCs are and why we compute them
---------------------------------------
Raw audio waveforms are hard to match directly — identical spoken words from
different speakers look nothing alike at the sample level. MFCCs compress the
perceptually-relevant frequency content of each short frame into a compact
fixed-size vector (default: 13 coefficients), making it much easier for a
recogniser (or distance metric) to compare speech features regardless of
speaker, pitch, or recording level.

The pipeline for each frame:
  1. Pre-emphasis filter  — boost high frequencies (compensates for lip/vocal roll-off)
  2. Hamming window       — smooth frame edges to reduce spectral leakage
  3. FFT power spectrum   — convert time domain → frequency domain
  4. Mel filterbank       — warp frequencies to the perceptual Mel scale
                            (humans hear pitch logarithmically)
  5. Log compression      — mimic how the ear compresses loudness
  6. DCT                  — decorrelate filterbank energies into MFCC coefficients
  7. Δ and ΔΔ (optional)  — first and second temporal derivatives (velocity/acceleration)

Standalone usage
----------------
    from core.audio.features import MFCCExtractor
    import numpy as np

    extractor = MFCCExtractor(sample_rate=16000, n_mfcc=13)

    # From raw PCM bytes
    features: np.ndarray = extractor.from_bytes(pcm_bytes)
    # shape: (n_frames, n_mfcc * 3)  — coefficients + delta + delta-delta

    # From float32 array
    features = extractor.from_array(signal_f32)

    # Just the static MFCCs (no deltas)
    features = extractor.from_bytes(pcm_bytes, include_deltas=False)
    # shape: (n_frames, n_mfcc)

    # Frame-level energy (useful for VAD correlation)
    energy = extractor.frame_energy(signal_f32)  # shape: (n_frames,)

version: 1.0.0
CBD Contract:
  IN:  raw PCM bytes (s16le, mono) OR numpy float32 array
  OUT: numpy.ndarray of shape (n_frames, n_features)
  Errors: FeatureError for invalid input dimensions
"""

import logging
import math
from typing import Optional

import numpy as np

logger = logging.getLogger("audio.features")


class FeatureError(ValueError):
    """Raised when feature extraction receives invalid input."""


class MFCCExtractor:
    """
    MFCC feature extractor.

    Parameters
    ----------
    sample_rate   : int    Sample rate of the input PCM (Hz). Default 16000.
    n_mfcc        : int    Number of MFCC coefficients. Default 13.
    n_fft         : int    FFT size. Default 512.
    hop_length_ms : int    Frame hop in milliseconds. Default 10.
    win_length_ms : int    Analysis window in milliseconds. Default 25.
    n_mels        : int    Number of Mel filterbank bands. Default 40.
    fmin          : float  Lowest Mel frequency (Hz). Default 0.
    fmax          : float  Highest Mel frequency (Hz). Default sample_rate/2.
    pre_emphasis  : float  Pre-emphasis coefficient. Default 0.97.
    """

    def __init__(
        self,
        sample_rate:   int   = 16_000,
        n_mfcc:        int   = 13,
        n_fft:         int   = 512,
        hop_length_ms: int   = 10,
        win_length_ms: int   = 25,
        n_mels:        int   = 40,
        fmin:          float = 0.0,
        fmax:          float = None,
        pre_emphasis:  float = 0.97,
    ):
        self.sample_rate   = sample_rate
        self.n_mfcc        = n_mfcc
        self.n_fft         = n_fft
        self.hop_length    = int(sample_rate * hop_length_ms / 1000)
        self.win_length    = int(sample_rate * win_length_ms / 1000)
        self.n_mels        = n_mels
        self.fmin          = fmin
        self.fmax          = fmax or sample_rate / 2.0
        self.pre_emphasis  = pre_emphasis

        # Pre-compute Hamming window and Mel filterbank (constant per config)
        self._window    = np.hamming(self.win_length).astype(np.float32)
        self._mel_basis = self._build_mel_filterbank()
        self._dct_basis = self._build_dct_basis()

        logger.debug(
            "[features] MFCCExtractor sr=%d n_mfcc=%d n_mels=%d hop=%d win=%d",
            sample_rate, n_mfcc, n_mels, self.hop_length, self.win_length,
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def from_bytes(
        self,
        pcm_bytes:       bytes,
        include_deltas:  bool = True,
        include_delta2:  bool = True,
    ) -> np.ndarray:
        """
        Extract MFCC features from raw s16le PCM bytes.

        Returns
        -------
        np.ndarray of shape (n_frames, n_features)
          n_features = n_mfcc            if include_deltas=False
          n_features = n_mfcc * 2        if include_deltas=True, include_delta2=False
          n_features = n_mfcc * 3        if both True (default)
        """
        signal = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        return self.from_array(signal, include_deltas=include_deltas, include_delta2=include_delta2)

    def from_array(
        self,
        signal:         np.ndarray,
        include_deltas: bool = True,
        include_delta2: bool = True,
    ) -> np.ndarray:
        """
        Extract MFCC features from a float32 numpy array in [-1, 1].

        Returns
        -------
        np.ndarray of shape (n_frames, n_features)
        """
        if signal.ndim != 1:
            raise FeatureError(f"Expected 1-D signal, got shape {signal.shape}")
        if len(signal) < self.win_length:
            raise FeatureError(
                f"Signal too short ({len(signal)} samples) for window ({self.win_length})"
            )

        signal = self._pre_emphasise(signal)
        frames = self._frame_signal(signal)            # (n_frames, win_length)
        power  = self._power_spectrum(frames)          # (n_frames, n_fft//2+1)
        mel    = self._apply_mel_filterbank(power)     # (n_frames, n_mels)
        log_mel = np.log(mel + 1e-10)                 # log compression
        mfcc   = self._apply_dct(log_mel)             # (n_frames, n_mfcc)

        if not include_deltas:
            return mfcc.astype(np.float32)

        delta  = self._compute_delta(mfcc)
        if not include_delta2:
            return np.concatenate([mfcc, delta], axis=1).astype(np.float32)

        delta2 = self._compute_delta(delta)
        return np.concatenate([mfcc, delta, delta2], axis=1).astype(np.float32)

    def frame_energy(self, signal: np.ndarray) -> np.ndarray:
        """
        Compute per-frame RMS energy.

        Returns
        -------
        np.ndarray of shape (n_frames,)
        """
        frames = self._frame_signal(signal)
        return np.sqrt(np.mean(frames ** 2, axis=1))

    def summarise(self, features: np.ndarray) -> np.ndarray:
        """
        Summarise a variable-length feature matrix into a fixed-size vector.
        Returns the mean + std of each coefficient across frames.
        Shape: (n_features * 2,)

        Useful when you need a fixed-size descriptor for a whole utterance
        (e.g. for distance-based matching or clustering).
        """
        if features.size == 0:
            return np.zeros(features.shape[1] * 2 if features.ndim == 2 else 0)
        return np.concatenate([features.mean(axis=0), features.std(axis=0)])

    # ── DSP pipeline ───────────────────────────────────────────────────────────

    def _pre_emphasise(self, signal: np.ndarray) -> np.ndarray:
        """
        Apply first-order FIR high-pass filter: y[n] = x[n] - α·x[n-1]
        Boosts high-frequency content to partially compensate for the
        natural roll-off of the vocal tract.
        """
        if self.pre_emphasis <= 0:
            return signal
        emphasised = np.empty_like(signal)
        emphasised[0] = signal[0]
        emphasised[1:] = signal[1:] - self.pre_emphasis * signal[:-1]
        return emphasised

    def _frame_signal(self, signal: np.ndarray) -> np.ndarray:
        """
        Divide signal into overlapping frames.

        Returns
        -------
        np.ndarray of shape (n_frames, win_length)
          Each row is one windowed frame.
        """
        n_frames = 1 + (len(signal) - self.win_length) // self.hop_length
        indices  = (
            np.arange(self.win_length)[None, :]
            + self.hop_length * np.arange(n_frames)[:, None]
        )
        frames = signal[indices]               # shape: (n_frames, win_length)
        frames = frames * self._window         # apply Hamming window
        return frames

    def _power_spectrum(self, frames: np.ndarray) -> np.ndarray:
        """
        Compute one-sided power spectrum of each frame.

        Pads each frame to n_fft with zeros before FFT.
        Returns shape (n_frames, n_fft // 2 + 1).
        """
        pad     = self.n_fft - self.win_length
        padded  = np.pad(frames, ((0, 0), (0, pad)), mode="constant") if pad > 0 else frames
        fft_out = np.fft.rfft(padded, n=self.n_fft)
        power   = (np.abs(fft_out) ** 2) / self.n_fft
        return power

    def _apply_mel_filterbank(self, power: np.ndarray) -> np.ndarray:
        """
        Map power spectrum bins to Mel filterbank energies.
        Returns shape (n_frames, n_mels).
        """
        return np.dot(power, self._mel_basis.T)

    def _apply_dct(self, log_mel: np.ndarray) -> np.ndarray:
        """
        Apply DCT-II to each frame's log-Mel spectrum to get MFCCs.
        Returns shape (n_frames, n_mfcc).
        """
        return np.dot(log_mel, self._dct_basis.T)

    @staticmethod
    def _compute_delta(features: np.ndarray, width: int = 9) -> np.ndarray:
        """
        Compute first-order temporal derivative (delta) of features.
        Uses the regression formula over a ±(width//2) context window.
        Returns same shape as input.
        """
        n_frames, n_feat = features.shape
        half  = width // 2
        denom = 2 * sum(i ** 2 for i in range(1, half + 1))
        delta = np.zeros_like(features)

        # Pad edges by replication
        padded = np.concatenate(
            [np.tile(features[0], (half, 1)), features, np.tile(features[-1], (half, 1))],
            axis=0,
        )

        for t in range(n_frames):
            delta[t] = sum(
                i * (padded[t + half + i] - padded[t + half - i])
                for i in range(1, half + 1)
            ) / denom

        return delta

    # ── Filter construction ────────────────────────────────────────────────────

    def _build_mel_filterbank(self) -> np.ndarray:
        """
        Build a (n_mels, n_fft//2+1) Mel filterbank matrix.

        Each row is one triangular filter evenly spaced on the Mel scale.
        Filters overlap with their neighbours and sum to 1 at their centre.
        """
        def hz_to_mel(f):   return 2595 * math.log10(1 + f / 700)
        def mel_to_hz(m):   return 700 * (10 ** (m / 2595) - 1)

        mel_min  = hz_to_mel(self.fmin)
        mel_max  = hz_to_mel(self.fmax)
        mel_pts  = np.linspace(mel_min, mel_max, self.n_mels + 2)
        hz_pts   = np.array([mel_to_hz(m) for m in mel_pts])

        # Map Hz points to FFT bin indices
        n_bins   = self.n_fft // 2 + 1
        bin_pts  = np.floor(hz_pts / (self.sample_rate / self.n_fft)).astype(int)
        bin_pts  = np.clip(bin_pts, 0, n_bins - 1)

        filterbank = np.zeros((self.n_mels, n_bins), dtype=np.float32)
        for m in range(1, self.n_mels + 1):
            left   = bin_pts[m - 1]
            center = bin_pts[m]
            right  = bin_pts[m + 1]
            for k in range(left, center + 1):
                if center > left:
                    filterbank[m - 1, k] = (k - left) / (center - left)
            for k in range(center, right + 1):
                if right > center:
                    filterbank[m - 1, k] = (right - k) / (right - center)

        return filterbank

    def _build_dct_basis(self) -> np.ndarray:
        """
        Build a (n_mfcc, n_mels) DCT-II basis matrix.
        DCT-II: C[k, n] = cos(π·k·(n + 0.5) / N)
        """
        n    = self.n_mels
        k    = self.n_mfcc
        basis = np.zeros((k, n), dtype=np.float32)
        for i in range(k):
            for j in range(n):
                basis[i, j] = math.cos(math.pi * i * (j + 0.5) / n)
        return basis
