#!/usr/bin/env python3
"""Synthesize benchmarks/tts_eval/sentences.txt with one TTS engine and report speed and memory. One engine per process,
so memory numbers do not mix.  Writes wavs to --out and a results json.

  Kokoro (project venv):  ./.venv/bin/python benchmarks/tts_eval/bench_tts.py kokoro --voice af_heart
  Paradee (Python 3.12):  uv run --no-project --python 3.12 --with "misaki[en]" --with espeakng-loader --with phonemizer-fork \
      --with onnxruntime --with huggingface_hub --with numpy --with soundfile \
      --with "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl" \
      python benchmarks/tts_eval/bench_tts.py paradee --paradee-src /tmp/paradee_ref [--fp32]
  (paradee/tts.py is the author's reference implementation, Apache-2.0: copy it to /tmp/paradee_ref/paradee_tts.py)
"""
import argparse, json, os, resource, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("engine", choices=["kokoro", "paradee"])
ap.add_argument("--voice", default="af_heart")
ap.add_argument("--out", default="/tmp/tts_samples")
ap.add_argument("--paradee-src", default="/tmp/paradee_ref")
ap.add_argument("--fp32", action="store_true")
ap.add_argument("--threads", type=int, default=1)
ap.add_argument("--kokoro-dir", default=os.path.expanduser("~/.config/free-voice/models/kokoro"))
args = ap.parse_args()

rss_gb = lambda: resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**30  # macOS: bytes
import numpy as np, soundfile as sf
sentences = [l.strip() for l in open(os.path.join(HERE, "sentences.txt")) if l.strip()]
os.makedirs(args.out, exist_ok=True)
r_base = rss_gb()

if args.engine == "kokoro":
    from kokoro_onnx import Kokoro
    t = time.time(); k = Kokoro(os.path.join(args.kokoro_dir, "kokoro-v1.0.onnx"), os.path.join(args.kokoro_dir, "voices-v1.0.bin"))
    load_s = time.time() - t; r_load = rss_gb()
    name = f"kokoro_{args.voice}"
    synth = lambda text: k.create(text, voice=args.voice, speed=1.0, lang="en-us")
    parts = {}
else:
    sys.path.insert(0, args.paradee_src)
    import paradee_tts as P
    t = time.time(); r0 = rss_gb()
    from misaki import en, espeak
    g2p = en.G2P(trf=False, british=False, fallback=espeak.EspeakFallback(british=False), unk="")
    g2p_gb = rss_gb() - r0
    P_threads = args.threads
    t2 = time.time(); r1 = rss_gb()
    m = P.Paradee(quantized=not args.fp32, threads=P_threads)
    model_gb = rss_gb() - r1
    load_s = time.time() - t; r_load = rss_gb()
    name = "paradee_" + ("fp32" if args.fp32 else "int8")
    synth = lambda text: (m(text), P.SAMPLE_RATE)
    parts = {"g2p_peak_growth_gb": round(g2p_gb, 2), "model_session_growth_gb": round(model_gb, 2)}

synth("Warm up sentence.")
rows = []
for i, text in enumerate(sentences):
    t = time.perf_counter(); audio, sr = synth(text); dt = time.perf_counter() - t
    audio = np.asarray(audio, np.float32).reshape(-1)
    sf.write(os.path.join(args.out, f"{name}_{i + 1:02d}.wav"), audio, sr)
    rows.append({"i": i + 1, "chars": len(text), "audio_s": round(len(audio) / sr, 2), "synth_s": round(dt, 3), "rtf": round((len(audio) / sr) / dt, 1)})
tot_a, tot_s = sum(r["audio_s"] for r in rows), sum(r["synth_s"] for r in rows)
res = {"engine": name, "threads": args.threads, "load_s": round(load_s, 2), "peak_rss_gb_total": round(rss_gb(), 2),
       "growth_after_imports_gb": round(rss_gb() - r_base, 2), **parts, "speed_x_realtime": round(tot_a / tot_s, 1),
       "median_latency_ms_short": round(1000 * sorted(r["synth_s"] for r in rows[:4])[2]), "rows": rows}
json.dump(res, open(os.path.join(args.out, f"{name}.json"), "w"), indent=1)
print(f"{name}: load {res['load_s']}s | {res['speed_x_realtime']}x real time | short-phrase median {res['median_latency_ms_short']} ms | "
      f"peak RSS {res['peak_rss_gb_total']} GB (growth since imports {res['growth_after_imports_gb']} GB) {parts}")
