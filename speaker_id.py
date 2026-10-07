"""Speaker identification and voice verification for Free Mac Voice.

Rejects ambient TV/podcast speech before STT transcription or routing. Two
interchangeable embedding backends, each with its own enrolled profile:

* ``campplus``  - WeSpeaker CAM++ (VoxCeleb, 29 MB ONNX, ~11 ms per 2 s clip on an
  M4). Much wider same-vs-different-speaker margin. Needs ``kaldi-native-fbank``.
* ``pyannote`` - the original 17.6 MB model (legacy; weak margin).

``VOICE_SPEAKER_BACKEND`` = ``auto`` (default) uses CAM++ as soon as a CAM++
profile has been enrolled (re-run ``--enroll``), and otherwise keeps the legacy
model so an existing profile keeps working untouched.
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
import urllib.request
import numpy as np

DEFAULT_CONFIG_DIR = os.path.expanduser("~/.config/free-voice")
MODELS_DIR = os.path.join(DEFAULT_CONFIG_DIR, "models")

# legacy backend (name kept for backwards compatibility)
MODEL_URL = "https://huggingface.co/deepghs/pyannote-embedding-onnx/resolve/main/model.onnx"
DEFAULT_MODEL_PATH = os.path.join(MODELS_DIR, "speaker_embedding.onnx")
DEFAULT_PROFILE_PATH = os.path.join(DEFAULT_CONFIG_DIR, "speaker_profile.npy")

CAMPLUS_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
               "speaker-recongition-models/wespeaker_en_voxceleb_CAM%2B%2B.onnx")
CAMPLUS_SHA256 = "c46fad10b5f81e1aa4a60c162714208577093655076c5450f8c469e522ec54ef"
CAMPLUS_MODEL_PATH = os.path.join(MODELS_DIR, "wespeaker_campplus.onnx")
CAMPLUS_PROFILE_PATH = os.path.join(DEFAULT_CONFIG_DIR, "speaker_profile_campplus.npy")

SPEAKER_THRESHOLD = float(os.environ.get("VOICE_SPEAKER_THRESHOLD", "0.17"))  # legacy backend
# CAM++ cosine: same speaker ~0.4-0.6, other voices ~0.0-0.2 (see benchmarks/). Re-tune from the
# "speaker ok (sim=...)" / "Speaker mismatch" log lines after enrolling.
CAMPLUS_THRESHOLD = float(os.environ.get("VOICE_SPEAKER_THRESHOLD_CAMPPLUS", "0.30"))
SPEAKER_VERIFICATION_ENABLED = os.environ.get("VOICE_SPEAKER_VERIFICATION", "1").lower() in ("1", "true", "yes")
SPEAKER_BACKEND = os.environ.get("VOICE_SPEAKER_BACKEND", "auto").strip().lower()

_session = None  # legacy session (kept for backwards compatibility)
_sessions: dict[str, object] = {}
_profile_cache: dict[str, tuple[float, np.ndarray]] = {}


def _have_fbank() -> bool:
    try:
        import kaldi_native_fbank  # noqa: F401
        return True
    except Exception:
        return False


def active_backend() -> str:
    """'campplus' or 'pyannote' (see module docstring)."""
    if SPEAKER_BACKEND in ("campplus", "pyannote"):
        return SPEAKER_BACKEND
    if os.path.isfile(CAMPLUS_PROFILE_PATH) and _have_fbank():
        return "campplus"
    return "pyannote"


def get_threshold(backend: str | None = None) -> float:
    return CAMPLUS_THRESHOLD if (backend or active_backend()) == "campplus" else SPEAKER_THRESHOLD


def get_model_path(backend: str | None = None) -> str:
    if (backend or active_backend()) == "campplus":
        return os.environ.get("VOICE_SPEAKER_MODEL_PATH_CAMPPLUS", CAMPLUS_MODEL_PATH)
    return os.environ.get("VOICE_SPEAKER_MODEL_PATH", DEFAULT_MODEL_PATH)


def get_profile_path(backend: str | None = None) -> str:
    if (backend or active_backend()) == "campplus":
        return os.environ.get("VOICE_SPEAKER_PROFILE_PATH_CAMPPLUS", CAMPLUS_PROFILE_PATH)
    return os.environ.get("VOICE_SPEAKER_PROFILE_PATH", DEFAULT_PROFILE_PATH)


def _download(url: str, path: str, min_bytes: int, sha256: str | None = None) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    print(f"Downloading speaker embedding model to {path}...")
    try:
        urllib.request.urlretrieve(url, tmp_path)
        if os.path.getsize(tmp_path) < min_bytes:
            raise RuntimeError("download is too small")
        if sha256:
            h = hashlib.sha256()
            with open(tmp_path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != sha256:
                raise RuntimeError("checksum mismatch")
        os.replace(tmp_path, path)
        print("Speaker embedding model downloaded successfully.")
        return path
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise RuntimeError(f"Failed to download speaker embedding model: {e}") from e


def ensure_model(model_path: str | None = None, backend: str | None = None) -> str:
    """Ensure the ONNX speaker embedding model is downloaded and return its path."""
    backend = backend or active_backend()
    path = model_path or get_model_path(backend)
    if backend == "campplus":
        if os.path.exists(path) and os.path.getsize(path) > 20_000_000:
            return path
        return _download(CAMPLUS_URL, path, 20_000_000, CAMPLUS_SHA256)
    if os.path.exists(path) and os.path.getsize(path) > 10_000_000:
        return path
    return _download(MODEL_URL, path, 10_000_000)


def _get_session(model_path: str | None = None, backend: str | None = None):
    global _session
    backend = backend or active_backend()
    if backend == "pyannote" and model_path is None and _session is not None:
        return _session
    key = f"{backend}:{model_path or ''}"
    if key in _sessions:
        return _sessions[key]
    import onnxruntime as ort

    path = ensure_model(model_path, backend)
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    opts.inter_op_num_threads = 1
    sess = ort.InferenceSession(path, sess_options=opts, providers=["CPUExecutionProvider"])
    _sessions[key] = sess
    if backend == "pyannote" and model_path is None:
        _session = sess
    return sess


def _campplus_fbank(audio: np.ndarray) -> np.ndarray:
    """Official WeSpeaker front-end: samples x32768, 80-dim Kaldi fbank (25/10 ms,
    hamming, dither 0, snip_edges) then per-utterance mean subtraction (CMN).
    WeSpeaker nets collapse without the CMN step."""
    import kaldi_native_fbank as knf
    o = knf.FbankOptions()
    o.frame_opts.samp_freq = 16000
    o.frame_opts.frame_length_ms = 25
    o.frame_opts.frame_shift_ms = 10
    o.frame_opts.dither = 0.0
    o.frame_opts.window_type = "hamming"
    o.frame_opts.snip_edges = True
    o.mel_opts.num_bins = 80
    o.mel_opts.high_freq = 0.0
    fb = knf.OnlineFbank(o)
    fb.accept_waveform(16000, (audio * 32768.0).astype(np.float32).tolist())
    fb.input_finished()
    feats = np.stack([fb.get_frame(i) for i in range(fb.num_frames_ready)]).astype(np.float32)
    return feats - feats.mean(axis=0, keepdims=True)


def extract_embedding(audio: np.ndarray, sample_rate: int = 16000, model_path: str | None = None,
                      backend: str | None = None) -> np.ndarray:
    """Extract a unit-normalized speaker embedding (512-dim) from 16 kHz audio."""
    backend = backend or active_backend()
    sess = _get_session(model_path, backend)
    if audio.ndim > 1:
        audio = audio.flatten()
    audio = audio.astype(np.float32)

    if backend == "campplus":
        min_len = sample_rate  # CAM++ needs ~1 s; tile short clips rather than zero-pad
        if len(audio) < min_len:
            audio = np.resize(audio, min_len)
        emb = sess.run(None, {"feats": _campplus_fbank(audio)[None]})[0][0]
    else:
        # Need at least 0.5s of audio (8000 frames)
        if len(audio) < sample_rate * 0.5:
            pad_len = int(sample_rate * 0.5) - len(audio)
            audio = np.pad(audio, (0, pad_len))
        emb = sess.run(None, {"waveform": np.expand_dims(audio, axis=0)})[0][0]
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
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cached = _profile_cache.get(path)
    if cached and cached[0] == mtime:  # verify_speaker runs per utterance: skip the disk read
        return cached[1]
    try:
        prof = np.load(path)
        norm = np.linalg.norm(prof)
        if norm > 1e-10:
            prof = prof / norm
        _profile_cache[path] = (mtime, prof)
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

    backend = active_backend()
    profile = load_speaker_profile(profile_path or get_profile_path(backend))
    if profile is None:
        return True, 1.0

    thresh = threshold if threshold is not None else get_threshold(backend)
    try:
        emb = extract_embedding(audio, backend=backend)
        if emb.shape != profile.shape:  # profile belongs to a different model: don't compare
            return True, 1.0
        sim = float(np.dot(emb, profile))
        return (sim >= thresh), sim
    except Exception:
        return True, 1.0


def _speech_windows(audio: np.ndarray, sr: int = 16000, win_s: float = 2.0) -> list[np.ndarray]:
    """Split a recording into 2 s windows, dropping silent ones (matches the 2 s clips
    that get scored later). Short recordings come back whole."""
    audio = audio.flatten().astype(np.float32)
    win = int(sr * win_s)
    if len(audio) < win * 1.5:
        return [audio]
    wins = [audio[i:i + win] for i in range(0, len(audio) - win + 1, win // 2)]
    rms = np.array([float(np.sqrt(np.mean(w ** 2))) for w in wins])
    keep = [w for w, r in zip(wins, rms) if r > max(np.percentile(rms, 20), 0.005) * 1.5] or wins
    return keep


def enroll_speaker(samples: list[np.ndarray], profile_path: str | None = None) -> float:
    """Enroll speaker profile(s) from audio samples. Returns self-consistency score.

    Without an explicit ``profile_path`` the profile is written for every backend
    that can run here (so switching backends never needs a second recording) and
    the consistency of the preferred backend (CAM++ when available) is returned.
    """
    if not samples:
        raise ValueError("At least one audio sample required for enrollment")

    backends = ["pyannote"]
    if profile_path is None and _have_fbank():
        backends.insert(0, "campplus")
    best = None
    for backend in backends:
        try:
            if backend == "campplus":
                clips = [w for s in samples for w in _speech_windows(s)]
            else:
                clips = list(samples)
            embeddings = [extract_embedding(c, backend=backend) for c in clips]
        except Exception:
            if best is None and backend == backends[-1]:
                raise
            continue  # e.g. CAM++ download failed: keep the legacy profile
        avg = np.mean(embeddings, axis=0)
        norm = np.linalg.norm(avg)
        if norm > 1e-10:
            avg = avg / norm

        path = profile_path or get_profile_path(backend)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        np.save(path, avg)
        _profile_cache.pop(path, None)

        if len(embeddings) > 1:
            consistency = float(np.mean([np.dot(e, avg) for e in embeddings]))
        else:
            consistency = 1.0
        if best is None:
            best = consistency
    return best if best is not None else 1.0


def reset_speaker(profile_path: str | None = None) -> bool:
    """Delete enrolled speaker profile(s)."""
    paths = [profile_path] if profile_path else [get_profile_path("campplus"), get_profile_path("pyannote")]
    removed = False
    for path in paths:
        if os.path.exists(path):
            os.remove(path)
            _profile_cache.pop(path, None)
            removed = True
    return removed
