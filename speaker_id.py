"""Speaker identification and voice verification for Free Mac Voice.

Uses a lightweight (17.6MB) ONNX speaker embedding model running on CPU in ~3ms.
Rejects ambient TV/podcast speech before STT transcription or routing.
"""
from __future__ import annotations

import os
import sys
import time
import urllib.request
import numpy as np

MODEL_URL = "https://huggingface.co/deepghs/pyannote-embedding-onnx/resolve/main/model.onnx"
DEFAULT_CONFIG_DIR = os.path.expanduser("~/.config/free-voice")
DEFAULT_MODEL_PATH = os.path.join(DEFAULT_CONFIG_DIR, "models", "speaker_embedding.onnx")
DEFAULT_PROFILE_PATH = os.path.join(DEFAULT_CONFIG_DIR, "speaker_profile.npy")

SPEAKER_THRESHOLD = float(os.environ.get("VOICE_SPEAKER_THRESHOLD", "0.25"))
SPEAKER_VERIFICATION_ENABLED = os.environ.get("VOICE_SPEAKER_VERIFICATION", "1").lower() in ("1", "true", "yes")

_session = None


def get_model_path() -> str:
    return os.environ.get("VOICE_SPEAKER_MODEL_PATH", DEFAULT_MODEL_PATH)


def get_profile_path() -> str:
    return os.environ.get("VOICE_SPEAKER_PROFILE_PATH", DEFAULT_PROFILE_PATH)


def ensure_model(model_path: str | None = None) -> str:
    """Ensure the ONNX speaker embedding model is downloaded and return its path."""
    path = model_path or get_model_path()
    if os.path.exists(path) and os.path.getsize(path) > 10_000_000:
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    print(f"Downloading 17.6 MB speaker embedding model to {path}...")
    try:
        urllib.request.urlretrieve(MODEL_URL, tmp_path)
        os.replace(tmp_path, path)
        print("Speaker embedding model downloaded successfully.")
        return path
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise RuntimeError(f"Failed to download speaker embedding model: {e}") from e


def _get_session(model_path: str | None = None):
    global _session
    if _session is not None:
        return _session
    import onnxruntime as ort

    path = ensure_model(model_path)
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    opts.inter_op_num_threads = 1
    _session = ort.InferenceSession(path, sess_options=opts, providers=["CPUExecutionProvider"])
    return _session


def extract_embedding(audio: np.ndarray, sample_rate: int = 16000, model_path: str | None = None) -> np.ndarray:
    """Extract a 512-dim unit-normalized speaker embedding vector from 16kHz audio."""
    sess = _get_session(model_path)
    if audio.ndim > 1:
        audio = audio.flatten()
    audio = audio.astype(np.float32)

    # Need at least 0.5s of audio (8000 frames)
    if len(audio) < sample_rate * 0.5:
        pad_len = int(sample_rate * 0.5) - len(audio)
        audio = np.pad(audio, (0, pad_len))

    waveform = np.expand_dims(audio, axis=0)
    outputs = sess.run(None, {"waveform": waveform})
    emb = outputs[0][0]
    norm = np.linalg.norm(emb)
    if norm > 1e-10:
        emb = emb / norm
    return emb


def is_speaker_enrolled(profile_path: str | None = None) -> bool:
    """Check if a speaker voice profile is enrolled and present on disk."""
    path = profile_path or get_profile_path()
    return os.path.isfile(path) and os.path.getsize(path) > 100


def load_speaker_profile(profile_path: str | None = None) -> np.ndarray | None:
    """Load the enrolled speaker profile vector, or None if not enrolled."""
    path = profile_path or get_profile_path()
    if not os.path.isfile(path):
        return None
    try:
        prof = np.load(path)
        norm = np.linalg.norm(prof)
        if norm > 1e-10:
            prof = prof / norm
        return prof
    except Exception:
        return None


def verify_speaker(audio: np.ndarray, profile_path: str | None = None, threshold: float | None = None) -> tuple[bool, float]:
    """Verify if the audio matches the enrolled speaker.

    Returns (is_match, cosine_similarity).
    If no profile is enrolled or verification is disabled, returns (True, 1.0).
    """
    if not SPEAKER_VERIFICATION_ENABLED:
        return True, 1.0

    profile = load_speaker_profile(profile_path)
    if profile is None:
        return True, 1.0

    thresh = threshold if threshold is not None else SPEAKER_THRESHOLD
    try:
        emb = extract_embedding(audio, model_path=get_model_path())
        sim = float(np.dot(emb, profile))
        return (sim >= thresh), sim
    except Exception:
        return True, 1.0


def enroll_speaker(samples: list[np.ndarray], profile_path: str | None = None) -> float:
    """Enroll speaker profile from audio samples. Returns self-consistency score."""
    if not samples:
        raise ValueError("At least one audio sample required for enrollment")

    embeddings = [extract_embedding(s) for s in samples]
    avg = np.mean(embeddings, axis=0)
    norm = np.linalg.norm(avg)
    if norm > 1e-10:
        avg = avg / norm

    path = profile_path or get_profile_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.save(path, avg)

    if len(embeddings) > 1:
        sims = [float(np.dot(embeddings[0], e)) for e in embeddings[1:]]
        consistency = float(np.mean(sims))
    else:
        consistency = 1.0

    return consistency


def reset_speaker(profile_path: str | None = None) -> bool:
    """Delete enrolled speaker profile."""
    path = profile_path or get_profile_path()
    if os.path.exists(path):
        os.remove(path)
        return True
    return False
