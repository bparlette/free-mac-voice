#!/usr/bin/env python3
"""Wake word through the REAL acoustic path: synthetic speech is played out of the Mac's speakers and recorded back through the
microphone, then run through the assistant's recognizer and wake parser. Unlike bench_wake.py this includes the room, the speaker and
the microphone, so it shows how much real audio hurts recall and false wakes. Stop the live assistant first (it would act on the
commands and compete for the mic):   launchctl unload ~/Library/LaunchAgents/com.free-mac-voice.plist
  ./.venv/bin/python benchmarks/wake_eval/bench_wake_room.py [--n 30]
Output volume is left as the user has it. The voices are synthetic, so this still is not a person talking.
"""
import argparse, json, os, random, subprocess, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "finetune_eval")); sys.path.insert(0, os.path.join(HERE, "..", ".."))
import numpy as np, sounddevice as sd, soundfile as sf
from scipy.signal import resample_poly
from common import split_test
import free_voice as fv  # noqa: E402
fv.log = lambda *a, **k: None
ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=30); ap.add_argument("--volume", type=int, default=0, help="temporarily set output volume 0-100 (restored afterwards); 0 = leave as is")
args = ap.parse_args()
TV = ["Mike, turn the volume down, I can't hear myself think", "Mark, close the window before the bugs get in", "Max, play fetch with the ball",
      "Make sure you lock the door", "Mark my words, you'll regret this", "Mike, pause the game for a second", "Mark, mute the TV", "Max, lock the screen",
      "Match point, Federer", "Make a timer for ten minutes"]
rows = split_test(0)[1]; rnd = random.Random(11)
cmds = [r["text"] for r in rnd.sample([r for r in rows if r["is_command"]], args.n)]
non = [r["text"] for r in rnd.sample([r for r in rows if not r["is_command"]], args.n)]
k = fv._get_kokoro(); voices = ["af_heart", "am_fenrir"]
mic = next(i for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0)
SR = 16000

def play_and_record(text, voice):
    s, sr = k.create(text, voice=voice, speed=1.0, lang="en-us")
    path = tempfile.mktemp(suffix=".wav"); sf.write(path, np.asarray(s, np.float32), sr)
    dur = len(s) / sr + 1.5
    rec = sd.rec(int(dur * SR), samplerate=SR, channels=1, dtype="float32", device=mic)
    time.sleep(0.3); subprocess.run(["afplay", path], check=False); sd.wait(); os.remove(path)
    return rec[:, 0]

def get_volume(): return int(subprocess.run(["osascript", "-e", "output volume of (get volume settings)"], capture_output=True, text=True).stdout.strip() or 0)
def set_volume(v): subprocess.run(["osascript", "-e", f"set volume output volume {v}"], check=False)
old_volume = get_volume()
if args.volume: set_volume(args.volume)
import atexit; atexit.register(lambda: set_volume(old_volume))
quiet = sd.rec(int(2 * SR), samplerate=SR, channels=1, dtype="float32", device=mic); sd.wait()
res = {"mic": sd.query_devices(mic)["name"], "room_noise_rms": round(float(np.sqrt(np.mean(quiet ** 2))), 5), "voices": voices, "sets": {}}
cal = play_and_record("Testing, one two three, testing the microphone.", "af_heart")
res["calibration_rms"] = round(float(np.sqrt(np.mean(cal ** 2))), 5); res["volume_used"] = get_volume(); print("calibration rms", res["calibration_rms"], "noise", res["room_noise_rms"], "volume", res["volume_used"], flush=True)
if res["calibration_rms"] < 3 * res["room_noise_rms"]: sys.exit("the microphone does not hear the speaker at this volume; raise --volume or move the mic")
sets = {"mac_command": ["Mac, " + c for c in cmds], "non_command": non, "tv_name_line": TV}
for name, lines in sets.items():
    woke = 0; heard = []; levels = []
    for i, t in enumerate(lines):
        a = play_and_record(t, voices[i % 2]); levels.append(float(np.sqrt(np.mean(a ** 2))))
        h = fv.transcribe(a).strip(); w, _ = fv.parse_wake_word(h, "mac"); woke += bool(w); heard.append((t, h, bool(w)))
        print(f"{name:12s} woke={int(w)} | {t[:44]:44s} -> {h[:50]}", flush=True)
    res["sets"][name] = {"clips": len(lines), "woke": woke, "median_rms": round(float(np.median(levels)), 5),
                         "missed_or_false_examples": [[t, h] for t, h, w in heard if (w != (name == "mac_command"))][:6]}
print(json.dumps(res, indent=1)); json.dump(res, open(os.path.join(HERE, "results", "wake_word_room.json"), "w"), indent=1)
