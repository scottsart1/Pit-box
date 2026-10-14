"""Offline, bounded single-line HUD recognition. No frame uploads or downloads."""
from __future__ import annotations

import hashlib
import math
import threading
from pathlib import Path

from PIL import Image


# These are the Apache-2.0 PaddleOCR-derived models bundled in RapidOCR 3.9.2.
# Verify before constructing the engine so a missing/corrupt model never triggers
# the library's automatic model download on a driver's machine.
MODELS = {
    "Det": ("PP-OCRv6_det_small.onnx", "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f"),
    "Cls": ("ch_ppocr_mobile_v2.0_cls_mobile.onnx", "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c"),
    "Rec": ("PP-OCRv6_rec_small.onnx", "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884"),
}
MIN_SCORE = .98  # A rejection policy, not a calibrated probability of accuracy.
_lock = threading.Lock()
_engine = None


def _load_engine():
    try:
        import rapidocr
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise ValueError("The local HUD reader is missing. Reinstall the NASCAR desktop app.") from exc
    model_dir = Path(rapidocr.__file__).parent / "models"
    params = {
        "Global.log_level": "error",
        "Global.use_det": False,
        "Global.use_cls": False,
        "Global.max_side_len": 1280,
        "EngineConfig.onnxruntime.intra_op_num_threads": 2,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    }
    for kind, (name, expected_hash) in MODELS.items():
        path = model_dir / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise ValueError("A bundled HUD model is missing or damaged. Reinstall the NASCAR desktop app.")
        params[f"{kind}.model_path"] = str(path)
    return RapidOCR(params=params)


def result_reading(result) -> dict:
    texts, scores = result.txts, result.scores
    if texts is None or scores is None or len(texts) != 1 or len(scores) != 1:
        return {"text": "", "score": None, "accepted": False}
    score = float(scores[0])
    text = str(texts[0]).strip()
    accepted = bool(text) and len(text) <= 100 and math.isfinite(score) and MIN_SCORE <= score <= 1
    return {"text": text[:100], "score": round(score, 4) if math.isfinite(score) else None, "accepted": accepted}


def read_regions(frames: list[Image.Image]) -> list[dict]:
    """Run in a worker thread; serialize the cached engine's mutable call state."""
    global _engine
    if not 1 <= len(frames) <= 16:
        raise ValueError("Read between one and sixteen HUD regions.")
    with _lock:
        if _engine is None:
            _engine = _load_engine()
        readings = []
        for frame in frames:
            if min(frame.size) < 5:
                readings.append({"text": "", "score": None, "accepted": False})
                continue
            frame = frame.convert("RGB")
            frame.thumbnail((1280, 256))
            # Regions are explicitly calibrated single values/lines. Avoid full
            # frame text detection, which missed the stylized large HUD digits.
            result = _engine(frame, use_det=False, use_cls=False, use_rec=True)
            readings.append(result_reading(result))
        return readings
