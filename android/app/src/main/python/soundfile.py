"""Android stand-in for the soundfile subset Your Pit Box uses: `read()`.

The desktop build reads the engineer's synthesized acknowledgements and
replies with soundfile (libsndfile), which has no Android build. Every file
the backend plays through it is a WAV it wrote itself, so the standard
library's `wave` module covers it. Returns (data, samplerate) with the same
shapes soundfile uses: a 1-D array for mono, (frames, channels) otherwise.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import numpy as np


def read(path: Any, dtype: str = "float32", **_: Any) -> tuple[np.ndarray, int]:
    with wave.open(str(Path(path)), "rb") as source:
        channels = source.getnchannels()
        width = source.getsampwidth()
        rate = source.getframerate()
        frames = source.readframes(source.getnframes())
    if width == 2:
        samples = np.frombuffer(frames, dtype="<i2")
        scale = 32768.0
    elif width == 4:
        samples = np.frombuffer(frames, dtype="<i4")
        scale = 2147483648.0
    elif width == 1:
        samples = np.frombuffer(frames, dtype=np.uint8).astype(np.int16) - 128
        scale = 128.0
    else:
        raise ValueError(f"unsupported WAV sample width {width}")
    if channels > 1:
        samples = samples.reshape(-1, channels)
    if dtype in ("float32", "float64"):
        data = (samples.astype(dtype) / scale).astype(dtype)
    elif dtype == "int16":
        data = samples.astype(np.int16) if width == 2 else (samples / (scale / 32768.0)).astype(np.int16)
    else:
        raise ValueError(f"unsupported dtype {dtype}")
    return data, rate


__all__ = ["read"]
