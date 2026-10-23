# Planner bake-off

Picks the `creative` mode's LLM (`qwen3.8-27b` vs `muse-glimmer-30b`) on the planner's real jobs, through
karspark like any client. `bakeoff.py` is stdlib only and runs in `karspark/bench:4`.

| task | kind | auto-checked |
|---|---|---|
| `prompts-beats` | 4 story beats → FLUX keyframe + LTX motion prompts (JSON) | valid JSON, fields, FLUX length, LTX prompts describe no music or speech |
| `plan-30s` | a 30 s silent short as a JSON shot list | valid JSON, fields, every shot ≤ 20 s, total within 15% of 30 s |
| `longform-chapter` | an opening chapter, 1,500–2,000 words | length |
| `tools-character-video` | reference image → keyframe → i2v → `wait_job` → report | each tool call and its key arguments, the final answer names the output |
| `tools-layers-upscale` | split into 3 layers → upscale only the background | same |
| `tools-first-last` | first-to-last transition with sound effects | same (`audio: "sfx"`) |

Tool results are scripted, so no media job runs. Each task runs `--runs` times (default 2). Every output
is saved for a human read; prose quality and prompt quality are judged by reading them side by side, not by
the auto-checks.

```bash
scripts/bakeoff.sh qwen3.8-27b            # with the 27B in `creative`
scripts/bakeoff.sh qwen3.8-27b busy       # again while a video job runs (speed next to ComfyUI)
# put muse-glimmer-30b:vllm-dflash in `creative` (config/modes.yaml), deploy, switch, then:
scripts/bakeoff.sh muse-glimmer-30b
scripts/bakeoff.sh muse-glimmer-30b busy
```

Results land in `recipes/<model>/bench/<date>-<label>/` (`summary.json`: checks passed, tool steps
passed, median tok/s, errors; one JSON per task and run with the full output).

**Deciding.** Tool steps must be near-perfect: the planner exists to drive the tools. Between models that
both drive them reliably, prose and prompt quality from the side-by-side read decides, then speed while
ComfyUI is busy.

## Result (2026-10-23)

`qwen3.8-27b` stays in `creative`: tool steps 18/18 vs Glimmer's 12/18. Glimmer was better at prose and
faster (29 vs 24 tok/s). Numbers are in both recipe READMEs, and raw outputs are in `recipes/<model>/bench/`.
