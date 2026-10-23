"""Planner bake-off: the creative-mode LLM's real jobs, run against one model through karspark.

    python3 planner/bakeoff.py <base-url> <model> <label> [--runs 2]

Tasks (planner/tasks.json): FLUX/LTX prompts from story beats, a JSON shot plan, a long-form chapter, and
multi-turn karspark tool calls with scripted tool results. Auto-checks what can be checked (valid JSON,
fields, durations, the right tool with the right arguments, silent-video policy) and measures speed;
everything the model wrote goes to results/<label>/ for a side-by-side human read. Stdlib only.
Sampling is left to the server (vLLM applies each model's generation_config).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def chat(base, model, messages, tools=None, max_tokens=None, timeout=1800, extra=None):
    body = {"model": model, "messages": messages, **(extra or {})}   # no cap unless asked, like karspark
    if max_tokens:
        body["max_tokens"] = max_tokens
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(f"{base}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    dt = time.time() - t0
    msg = data["choices"][0]["message"]
    usage = data.get("usage") or {}
    return msg, {"s": round(dt, 2), "completion_tokens": usage.get("completion_tokens", 0),
                 "prompt_tokens": usage.get("prompt_tokens", 0),
                 "tok_s": round(usage.get("completion_tokens", 0) / dt, 1) if dt else 0}


def parse_json(text):
    """The first JSON object or array in text (models like ```json fences)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    for i in sorted(j for j in (text.find("{"), text.find("[")) if j >= 0):
        try:
            return json.JSONDecoder().raw_decode(text[i:])[0]
        except ValueError:
            continue
    return None


NEGATED = re.compile(r"\b(no|without|never|not)\s+(any\s+)?[\w-]+(\s*(,|or|and|nor)\s*(no\s+)?[\w-]+)*", re.I)
FORBIDDEN_AUDIO = re.compile(r"\b(music|soundtrack|score|singing|song|voice[- ]?over|narrat\w*|dialogue|speaks?|says)\b", re.I)


def task_prompts(base, model, t):
    msgs = [{"role": "system", "content": t["system"]}, {"role": "user", "content": t["user"]}]
    msg, m = chat(base, model, msgs)
    out = parse_json(msg.get("content"))
    checks = {"json": isinstance(out, list) and len(out) == t["expect_items"]}
    if checks["json"]:
        checks["fields"] = all(isinstance(x, dict) and x.get("flux") and x.get("ltx") for x in out)
        checks["flux_length"] = all(30 <= len(str(x.get("flux", "")).split()) <= 250 for x in out)
        # "no music, no speech" is the right instruction; only un-negated mentions count
        checks["ltx_silent"] = not any(FORBIDDEN_AUDIO.search(NEGATED.sub("", str(x.get("ltx", "")))) for x in out)
    return msg, m, checks


def task_plan(base, model, t):
    msgs = [{"role": "system", "content": t["system"]}, {"role": "user", "content": t["user"]}]
    msg, m = chat(base, model, msgs)
    out = parse_json(msg.get("content"))
    shots = out.get("shots") if isinstance(out, dict) else None
    checks = {"json": isinstance(shots, list) and len(shots) > 0}
    if checks["json"]:
        need = ("id", "duration", "keyframe_prompt", "motion_prompt")
        checks["fields"] = all(isinstance(s, dict) and all(k in s for k in need) for s in shots)
        durs = [float(s.get("duration") or 0) for s in shots if isinstance(s, dict)]
        checks["each_le_20s"] = all(0 < d <= 20 for d in durs)
        checks["total_close"] = abs(sum(durs) - t["total_s"]) <= 0.15 * t["total_s"]
    return msg, m, checks


def task_longform(base, model, t):
    msgs = [{"role": "system", "content": t["system"]}, {"role": "user", "content": t["user"]}]
    msg, m = chat(base, model, msgs, timeout=3600, extra=t.get("extra"))
    words = len((msg.get("content") or "").split())
    return msg, m, {"length": t["min_words"] <= words <= t["max_words"], "words": words}


def _matches(args, want):
    for k, v in want.items():
        got = args.get(k)
        if isinstance(v, str) and v.startswith("~"):          # substring, case-insensitive
            if v[1:].lower() not in json.dumps(got).lower():
                return False
        elif isinstance(v, list):                              # every listed item present
            if not isinstance(got, list) or not all(x in got for x in v):
                return False
        elif got != v and str(got) != str(v):
            return False
    return True


def task_tools(base, model, t, tools):
    """Scripted multi-turn: each step expects a tool call; we answer with the scripted result."""
    msgs = [{"role": "system", "content": t["system"]}, {"role": "user", "content": t["user"]}]
    steps, metrics = [], []
    for i, step in enumerate(t["steps"]):
        msg, m = chat(base, model, msgs, tools=tools)
        metrics.append(m)
        calls = msg.get("tool_calls") or []
        ok = False
        call = None
        for c in calls:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except ValueError:
                args = None
            if c["function"]["name"] == step["tool"] and isinstance(args, dict) and _matches(args, step.get("args", {})):
                ok, call = True, c
                break
        steps.append({"step": i, "want": step["tool"], "ok": ok,
                      "got": [(c["function"]["name"], c["function"].get("arguments")) for c in calls],
                      "content": (msg.get("content") or "")[:400]})
        if not ok:
            break
        msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": [call]})
        msgs.append({"role": "tool", "tool_call_id": call.get("id", f"call_{i}"), "content": json.dumps(step["result"])})
    done = len(steps) == len(t["steps"]) and all(s["ok"] for s in steps)
    if done and t.get("final_mentions"):
        msg, m = chat(base, model, msgs, tools=tools)
        metrics.append(m)
        text = (msg.get("content") or "").lower()
        steps.append({"step": "final", "ok": all(x.lower() in text for x in t["final_mentions"]),
                      "content": (msg.get("content") or "")[:600]})
    agg = {"s": round(sum(x["s"] for x in metrics), 2),
           "completion_tokens": sum(x["completion_tokens"] for x in metrics),
           "tok_s": round(sum(x["completion_tokens"] for x in metrics) / max(sum(x["s"] for x in metrics), 1e-6), 1)}
    return {"steps": steps}, agg, {"steps_ok": sum(1 for s in steps if s["ok"]), "steps_total": len(t["steps"]) +
                                  (1 if t.get("final_mentions") else 0)}


RUN = {"prompts": task_prompts, "plan": task_plan, "longform": task_longform}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("model")
    ap.add_argument("label")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--only", default="", help="comma-separated task ids")
    a = ap.parse_args()
    spec = json.load(open(os.path.join(HERE, "tasks.json")))
    out_dir = os.path.join("results", a.label)
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    for run in range(a.runs):
        for t in spec["tasks"]:
            if a.only and t["id"] not in a.only.split(","):
                continue
            try:
                if t["kind"] == "tools":
                    out, m, checks = task_tools(a.base, a.model, t, spec["tools"])
                else:
                    out, m, checks = RUN[t["kind"]](a.base, a.model, t)
                err = None
            except Exception as e:                              # a failed task is a result, not a crash
                out, m, checks, err = None, {}, {}, repr(e)
            row = {"run": run, "task": t["id"], "kind": t["kind"], "metrics": m, "checks": checks, "error": err}
            rows.append(row)
            with open(os.path.join(out_dir, f"{t['id']}.run{run}.json"), "w") as f:
                json.dump({**row, "output": out}, f, indent=1, ensure_ascii=False)
            print(f"[{a.label}] run{run} {t['id']:<22} {json.dumps(checks)} {m.get('tok_s', '-')} tok/s"
                  + (f" ERROR {err}" if err else ""), flush=True)
    passed = sum(bool(v) for r in rows for k, v in r["checks"].items() if isinstance(v, bool))
    total = sum(1 for r in rows for v in r["checks"].values() if isinstance(v, bool))
    tool_ok = sum(r["checks"].get("steps_ok", 0) for r in rows)
    tool_total = sum(r["checks"].get("steps_total", 0) for r in rows)
    speeds = [r["metrics"]["tok_s"] for r in rows if r["metrics"].get("tok_s")]
    summary = {"label": a.label, "model": a.model, "checks": f"{passed}/{total}", "tool_steps": f"{tool_ok}/{tool_total}",
               "median_tok_s": sorted(speeds)[len(speeds) // 2] if speeds else None,
               "errors": sum(1 for r in rows if r["error"])}
    json.dump({"summary": summary, "rows": rows}, open(os.path.join(out_dir, "summary.json"), "w"), indent=1)
    print(json.dumps(summary))
    return 0 if not summary["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
