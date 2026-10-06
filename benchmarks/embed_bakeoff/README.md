Tier 0.5a embedding bake-off (2026-10-06). Compares the legacy n-gram hash, nomic-embed-text, EmbeddingGemma v1 (Ollama)
and EmbeddingGemma 2 (sentence-transformers, needs torch+torchvision+pillow) against the real `_INTENT_EXAMPLES`.
Run: `python bakeoff.py [name-substring]`. Test set is small (77 positives / 30 negatives) and hand-written; re-run
after growing it. Production default is embeddinggemma v1 @ 0.78 (see TIER05_EMBED_THRESHOLD in free_voice.py).
