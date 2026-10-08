#!/usr/bin/env python3
"""Download the 4-bit ONNX EmbeddingGemma used by TIER05_EMBED_BACKEND=onnx|compare (about 220 MB, once).

    ./.venv/bin/python scripts/fetch_onnx_embedder.py [target_dir]

Default target: ~/.cache/free-voice/embeddinggemma-onnx (override with TIER05_ONNX_DIR). Source:
onnx-community/embeddinggemma-300m-ONNX (Apache-2.0 / Gemma terms apply). Files are copied out of the Hugging Face
cache so onnxruntime sees plain files (it rejects the cache's symlinks for external weight data).
"""
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import onnx_embed  # noqa: E402

REPO = "onnx-community/embeddinggemma-300m-ONNX"
FILES = ["onnx/model_q4.onnx", "onnx/model_q4.onnx_data", "tokenizer.json"]


def main() -> int:
    from huggingface_hub import hf_hub_download
    target = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else onnx_embed.model_dir()
    for rel in FILES:
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_file() and dst.stat().st_size > 0:
            print(f"have {rel}")
            continue
        print(f"fetching {rel} ...")
        shutil.copyfile(hf_hub_download(REPO, rel), dst)
    os.environ["TIER05_ONNX_DIR"] = str(target)
    emb = onnx_embed.OnnxEmbedder(target)
    v = emb.embed(["task: classification | query: open safari"])
    print(f"ok: {target}  (embedding dim {v.shape[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
