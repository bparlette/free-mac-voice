"""In-process sentence embedder for Tier 0.5a: EmbeddingGemma 300M, 4-bit ONNX, run with onnxruntime on the CPU.

Why: same accuracy as the Ollama copy of the same model in benchmarks/finetune_eval (about 6 ms a query, ~0.1 GB of
process memory instead of ~650 MB resident in Ollama). Files are fetched once by `scripts/fetch_onnx_embedder.py`
into TIER05_ONNX_DIR (default ~/.cache/free-voice/embeddinggemma-onnx). Nothing here raises into the router:
get_embedder() returns None when the files, onnxruntime or tokenizers are missing.
"""
import os
import threading
from pathlib import Path

MODEL_REL = os.path.join("onnx", "model_q4.onnx")
DEFAULT_DIR = Path.home() / ".cache" / "free-voice" / "embeddinggemma-onnx"
MAX_TOKENS = 128  # voice commands are short; longer input is truncated, not rejected


def model_dir() -> Path:
    return Path(os.environ.get("TIER05_ONNX_DIR", str(DEFAULT_DIR))).expanduser()


def available() -> bool:
    d = model_dir()
    return (d / MODEL_REL).is_file() and (d / (MODEL_REL + "_data")).is_file() and (d / "tokenizer.json").is_file()


class OnnxEmbedder:
    def __init__(self, directory: Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2  # leave the rest of the CPU to speech recognition
        self._sess = ort.InferenceSession(str(directory / MODEL_REL), opts, providers=["CPUExecutionProvider"])
        self._tok = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self._extra = [i.name for i in self._sess.get_inputs() if i.name.endswith("_features")]
        self._out = next(o.name for o in self._sess.get_outputs() if o.name == "sentence_embedding")

    def embed(self, texts: list[str]):
        """L2-normalised float32 matrix [len(texts), dim]."""
        import numpy as np
        enc = [self._tok.encode(t).ids[:MAX_TOKENS] for t in texts]
        width = max(len(e) for e in enc)
        ids = np.array([e + [0] * (width - len(e)) for e in enc], dtype=np.int64)
        mask = np.array([[1] * len(e) + [0] * (width - len(e)) for e in enc], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        for name in self._extra:  # text-only use: empty image/video/audio inputs
            feed[name] = np.zeros((0, 512), dtype=np.float32)
        out = self._sess.run([self._out], feed)[0].astype(np.float32)
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)


_instance = None
_failed = False
_lock = threading.Lock()


def get_embedder():
    """Shared embedder, or None if unavailable (missing files / packages). Failure is remembered, not retried."""
    global _instance, _failed
    if _instance is not None or _failed:
        return _instance
    with _lock:
        if _instance is None and not _failed:
            try:
                if not available():
                    raise FileNotFoundError(f"model files not found in {model_dir()}")
                _instance = OnnxEmbedder(model_dir())
            except Exception as e:
                _failed = True
                print(f"[free-voice] ONNX embedder unavailable: {e}", flush=True)
    return _instance
