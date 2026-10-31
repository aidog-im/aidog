"""mycore.stt — local speech-to-text (car 4c media face).

faster-whisper behind a tiny contract: available() probes without loading a
model; transcribe(bytes) decodes the audio itself and returns
{"text", "language", "duration"}. Everything stdlib except faster-whisper,
av and numpy (all installed; the tiny/base/small weights are in the HF cache).

Known incompatibility (proven 2026-10-23): faster_whisper.audio.decode_audio
calls av.open(..., metadata_errors=...) which av 19 rejects — so decoding is
done here by hand: av.open(BytesIO) -> AudioResampler("s16","mono",16000)
-> float32 / 32768 -> model.transcribe(ndarray, beam_size=1).

Models lazy-load as a singleton per size (thread-safe); an invalid size or
undecodable input raises SttError with an honest English message. English
comments per repo i18n policy.
"""
from __future__ import annotations

import io
import os
import threading
from pathlib import Path

MODELS = ("tiny", "base", "small")
SAMPLE_RATE = 16000

_lock = threading.Lock()
_cache: dict[str, object] = {}          # model size -> loaded WhisperModel


class SttError(RuntimeError):
    """Honest STT failure; message carries context, never payload bytes."""


def _hub_dir() -> Path:
    env = os.environ.get("HF_HUB_CACHE", "").strip()
    if env:
        return Path(env)
    hf = os.environ.get("HF_HOME", "").strip()
    if hf:
        return Path(hf) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _cached(size: str) -> bool:
    """True when the Systran weight snapshot for `size` is already on disk
    (probing the cache never loads the model)."""
    root = _hub_dir() / f"models--Systran--faster-whisper-{size}"
    snap = root / "snapshots"
    try:
        return snap.is_dir() and any(p.is_file() for p in snap.rglob("*"))
    except OSError:
        return False


def available() -> bool:
    """Import check + weight-cache probe; never loads a model."""
    try:
        import faster_whisper  # noqa: F401
    except Exception:  # noqa: BLE001 — missing dep = unavailable, honestly
        return False
    return any(_cached(m) for m in MODELS)


def _decode(data: bytes) -> "object":
    """Bytes (mp3 / ogg-opus / webm-opus / wav ...) -> float32 mono 16 kHz."""
    if not isinstance(data, (bytes, bytearray, memoryview)) or not data:
        raise SttError("empty audio data")
    try:
        import av
        from av.audio.resampler import AudioResampler
    except Exception as e:  # noqa: BLE001
        raise SttError(f"decoder unavailable: {type(e).__name__}") from None
    try:
        with av.open(io.BytesIO(bytes(data))) as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                raise SttError("no audio stream in input")
            resampler = AudioResampler("s16", "mono", SAMPLE_RATE)
            chunks = []
            for frame in container.decode(stream):
                chunks.extend(f.to_ndarray() for f in resampler.resample(frame))
            chunks.extend(f.to_ndarray() for f in resampler.resample(None))
    except SttError:
        raise
    except Exception as e:  # noqa: BLE001 — corrupt container, unsupported codec
        raise SttError(f"audio decode failed: {type(e).__name__}") from None
    if not chunks:
        raise SttError("empty audio after decode")
    try:
        import numpy as np
        arr = np.concatenate(chunks, axis=-1).reshape(-1).astype("float32")
    except Exception as e:  # noqa: BLE001
        raise SttError(f"audio decode failed: {type(e).__name__}") from None
    if arr.size == 0:
        raise SttError("empty audio after decode")
    return arr / 32768.0


def _load(size: str):
    """Lazy singleton per model size (thread-safe; first call pays the load)."""
    with _lock:
        got = _cache.get(size)
        if got is not None:
            return got
        try:
            from faster_whisper import WhisperModel
            model = WhisperModel(size, device="cpu", compute_type="int8",
                                 local_files_only=_cached(size))
        except Exception as e:  # noqa: BLE001 — cache miss + offline, bad dep
            raise SttError(f"model '{size}' unavailable: {type(e).__name__}") from None
        _cache[size] = model
        return model


def transcribe(data: bytes, *, model: str = "base",
               language: str | None = None) -> dict:
    """Decode `data` and transcribe it. Returns {"text", "language",
    "duration"} (duration in seconds). Invalid size / undecodable or empty
    input raise SttError — never a silent empty string for broken input."""
    if model not in MODELS:
        raise SttError(f"unsupported model '{model}' (expected {'|'.join(MODELS)})")
    audio = _decode(data)
    engine = _load(model)
    try:
        segments, info = engine.transcribe(audio, beam_size=1, language=language)
        text = " ".join(seg.text.strip() for seg in segments).strip()
    except SttError:
        raise
    except Exception as e:  # noqa: BLE001
        raise SttError(f"transcription failed: {type(e).__name__}") from None
    duration = getattr(info, "duration", None)
    if not isinstance(duration, (int, float)) or duration <= 0:
        duration = len(audio) / SAMPLE_RATE
    # D2 ledger: one voice event for the daily anonymous scenario counts.
    # Lazy import + silent no-op — an inert or missing ledger never touches
    # transcription (and this call happens before the result is returned).
    try:
        from . import ledger as _ledger
        _ledger.note_event("voice")
    except Exception:  # noqa: BLE001 — reporting never breaks a call
        pass
    return {"text": text,
            "language": str(getattr(info, "language", "") or ""),
            "duration": round(float(duration), 3)}
