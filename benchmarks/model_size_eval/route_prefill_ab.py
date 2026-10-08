"""A/B: does the assistant's pre-filled answer start (`{"action": "`, meant for thinking models) help or hurt a given model?
  OLLAMA_MODEL=qwen3-vl:4b-instruct ./.venv/bin/python benchmarks/model_size_eval/route_prefill_ab.py qwen3-vl:4b-instruct
"""
import json, random, sys, time, statistics, urllib.request, urllib.error
import os; HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "finetune_eval")); sys.path.insert(0, os.path.join(HERE, "..", ".."))
from common import split_test, ACCEPT
import free_voice as fv
MODEL=sys.argv[1]
ACC={k:set(v) for k,v in ACCEPT.items()}; ACC["set_volume"]|={"volume_up","volume_down"}; ACC["mute"]|={"tv_mute"}
for k in ("media_play_pause","next_track"): ACC[k]|={"media"}
rows=split_test(0)[1]; rnd=random.Random(7)
cmds=rnd.sample([r for r in rows if r["is_command"]],60); non=rnd.sample([r for r in rows if not r["is_command"]],60)
NOFMT = False
def ask(text, prefill):
    global NOFMT
    msgs=[{"role":"system","content":fv._TIER1_SYSTEM},{"role":"user","content":text}]
    if prefill: msgs.append({"role":"assistant","content":'{"action": "'})
    body={"model":MODEL,"keep_alive":"20m","think":False,"options":{"temperature":0,"num_predict":96 if NOFMT else 64,"num_ctx":1024},"messages":msgs,"stream":False}
    if not NOFMT: body["format"]="json"
    req=urllib.request.Request(fv.OLLAMA_HOST+"/api/chat",data=json.dumps(body).encode(),headers={"Content-Type":"application/json"})
    t=time.time()
    try: d=json.load(urllib.request.urlopen(req,timeout=60))
    except urllib.error.HTTPError as e:
        if e.code==501 and not NOFMT:   # e.g. Qwen3.5 on Ollama 0.40: "structured output is unavailable"
            NOFMT=True; print(f"[note] {MODEL}: Ollama refuses format=json here; retrying without it (the assistant's own routing would fail on this model)"); return ask(text, prefill)
        raise
    c=d["message"]["content"].strip()
    if prefill and not c.startswith("{"): c='{"action": "'+c
    try: p, _ = json.JSONDecoder().raw_decode(c[c.find("{"):]) if "{" in c else ({}, 0)
    except Exception: p={}
    if not isinstance(p, dict): p={}
    try: conf=float(p.get("confidence",0))
    except Exception: conf=0
    a=str(p.get("action","none")); ok=a!="none" and a in fv._TIER1_ACTIONS and conf>=fv.TIER1_MIN_CONFIDENCE
    return (a if ok else None), time.time()-t
ask("open safari", False)
for label,prefill in (("with prefill (current code)",True),("no prefill",False)):
    right=wrong=miss=fa=0; lat=[]
    for r in cmds:
        a,dt=ask(r["text"],prefill); lat.append(dt)
        if a is None: miss+=1
        elif a in ACC[r["intent"]]: right+=1
        else: wrong+=1
    for r in non:
        a,dt=ask(r["text"],prefill); lat.append(dt); fa+= a is not None
    print(f"{MODEL} {label:28s}: {right}/60 correct, {wrong} wrong, {miss} no answer; {fa}/60 false accepts; median {statistics.median(lat):.2f}s")
