"""Tier 0.5a classifier: softmax regression over sentence embeddings (numpy only, trains in well under a second).

Replaces nearest-example matching when the neural embedder is active. Trained at startup from the router's own example
phrases (one class per action) plus `tier05_negatives.txt` (ordinary speech and TV-style lines that are NOT commands, class
"none"). Benchmarks: benchmarks/finetune_eval/bench_classifier.py, ledger section K.
"""
import numpy as np

NONE = "none"
TEMP = 20.0  # embeddings are L2-normalised with small angles between phrases, so logits are scaled up


def train(X: np.ndarray, y: np.ndarray, n_cls: int, l2: float = 1e-3, lr: float = 0.5, epochs: int = 400):
    """X: (n, d) L2-normalised embeddings, y: (n,) class ids. Returns (W, b)."""
    X = np.asarray(X, np.float32)
    W = np.zeros((X.shape[1], n_cls), np.float32)
    b = np.zeros(n_cls, np.float32)
    Y = np.eye(n_cls, dtype=np.float32)[np.asarray(y)]
    for _ in range(epochs):
        P = _softmax(TEMP * (X @ W) + b)
        G = (P - Y) / len(X)
        W -= lr * (TEMP * X.T @ G + l2 * W)
        b -= lr * G.sum(0)
    return W, b


def predict(W: np.ndarray, b: np.ndarray, X: np.ndarray) -> np.ndarray:
    return _softmax(TEMP * (np.asarray(X, np.float32) @ W) + b)


def _softmax(Z: np.ndarray) -> np.ndarray:
    Z = Z - Z.max(axis=-1, keepdims=True)
    E = np.exp(Z)
    return E / E.sum(axis=-1, keepdims=True)


def read_negatives(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    except OSError:
        return []
