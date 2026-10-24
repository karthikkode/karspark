# Lessons from the first setup (July–October 2026)

Everything here was learned the hard way on this box before the 2026-10-22 wipe. Read it before
re-adding a model. The full pre-wipe audit (unit files, launch scripts, the prefix-cache patch) is in git
history at commit `f64547d`.

## Hardware facts (GB10)

- **121 GiB unified memory**: CPU and GPU share it, so "VRAM" and RAM are the same pool. One big tenant at a
  time. Use `Conflicts=` plus `spark-bin/gpu-residency`, never a fixed sleep.
- Single-stream decode is memory-bandwidth-bound (~225 GB/s real). Smaller weights decode faster, so
  NVFP4 is the native, fastest format on sm_121.
- The Wi-Fi link is flaky. For HF downloads set `HF_HUB_DISABLE_XET=1` and wrap them in a resume loop.
  Check state before you retry anything that starts a process.
- `sudo` needs the password, so prefer `systemctl --user` units with linger on.

## LLM serving

- **Qwen3.8-Flash-Next (176B MoE, NVFP4)** fits on one Spark only with the **PLE-mmap overlay**, which serves
  the 44 GiB n-gram table from NVMe. The recipe was `hasso5703/dgx-spark-qwen38` v1.4 (`flash` target), on the
  official `vllm/vllm-openai:qwen38-flash-next` image. Measured: ~30 tok/s code, ~24 prose, ~2,200 tok/s
  prefill @60K, 262K ctx, MTP with 2 speculative tokens. The ~31 tok/s ceiling is MoE launch overhead, so
  tuning flags can't beat it.
  - Required flags: `-cc.cudagraph_mode=PIECEWISE` with the PLE op in splitting_ops, FlashInfer autotune off.
  - Prefix caching needed two vLLM patches on sm_121: vllm#48375 (drop the last cached page under MTP) and
    vllm#53142 (mamba block-size seed). Bake them in with a Dockerfile, not `docker commit`.
  - Failed to start: FULL cudagraphs, PLE CPU offload, fp8 KV cache. FP8-hybrid checkpoint: no win, reverted.
- **Qwen3.8-27B NVFP4 (dense)**: vLLM with MTP (3 tokens), or SGLang + DFlash2 at ~50 tok/s on code. The
  prose cost is 2–3× on any drafter.
- Put a **keepalive proxy** in front of agent clients. Tool-call parsers buffer arguments and go silent for
  minutes, and client stall detectors drop the stream. Send the official Anthropic `ping` event, not SSE comments.
- Hermes/agents: set `max_tokens` per custom model, or the 64K default output reservation overflows the context.
- After a core restart, core doesn't know which model family ComfyUI still holds. It used to skip the
  `/v1/free` then; FLUX (32 GB) stayed loaded next to the planner and the first speech job died with CUDA OOM
  (2026-10-24). Core now frees whenever the loaded family is unknown (r14).
- A thinking model's reasoning counts toward `max_tokens`. With a cap, Qwen3.8-27B spent all 16K tokens of a
  chapter reasoning and returned an empty answer (2026-10-23). Core now sets no cap; the context window is the
  limit, and `loopguard.py` stops exact repetition loops instead.
- **Hard power-off under video load (GB10 defect).** The box died with no log twice in 34 h: 2026-10-23 16:05,
  and 2026-10-24 01:49 on clip 16 of a 60-clip LTX run (about 2 min of full load per clip, 30 s gaps). No
  panic, Xid, thermal or OOM message; the journal stopped mid-flight and the box stayed off until
  power-cycled. The 1 s telemetry (`~/dgx-spark-crash/telemetry`) ended at GPU 82 C / 70 W / 96 %, hottest
  ACPI zone 93 C; that night peaked at zone 97.5 C, GPU 90 C. This is the known GB10 thermal hard cut. Fixes:
  - `host/gb10-clock-cap.service`: `nvidia-smi -lgc 300,2200`, which removed it for others at a cost of
    about 5-9 %.
  - core's thermal guard (r15): media starts below 80 C and pauses at 92 C.

  Read the ACPI zones: nvidia-smi read 8-15 C under them. A client-side "wait below 70 C on nvidia-smi" guard
  did not prevent this crash.
- Llama + vLLM `llama3_json` allows only one tool call per turn, which is a bad fit for parallel-tool agents.

## Images (ComfyUI)

- FLUX.2 dev NVFP4 + Turbo LoRA at 6 steps with 0.25 MP references: a 2.36 MP image in ~48 s.
- **Never** `--highvram` or `--gpu-only` on GB10. They pin page-locked memory and hung the machine
  (`NV_ERR_NO_MEMORY`). Use `--reserve-vram 16 --disable-pinned-memory`.
- Restart ComfyUI after switching models, because it never evicts them.
- Don't symlink model files into the HF cache. When the cache was cleaned, the default model silently broke.

## TTS

- IndexTTS-2 holds a narrator's timbre while varying emotion (needed 4 aarch64 patches). VoxCPM2 was the best
  voice designer. Fish S2 sounded flat (cloning carries prosody and timbre together). MOSS, Maya1 and
  Qwen3-TTS were tried and not chosen.

## Process lessons (why the box became unreadable)

- Three install methods (upstream installer to system units, hand-made user units, ad-hoc scripts) ended
  up on top of each other, plus three switch scripts that each knew part of the picture.
- Unit names drifted from what they ran (`vllm-122b` served the 27B).
- Loose files in `~`, `*.bak` copies instead of version control, and experiments that services depended on.
- The fix is [standard.md](standard.md): one repo, one layout, one registry.
- Anything holding the Docker socket can kill a live model. On 2026-10-23 a "harmless" karspark-core smoke
  run (an empty mode, another port) stopped Flash-Next: the image predated a fix, and core's adopt step
  stopped a same-named container that llama-swap had started. Core now adopts only containers carrying its
  `karspark.recipe` label. Before running a container manager on the box, build it from the current tree
  and prove the dangerous path read-only first.
- **2026-10-23, the first live creative run.** After about 30 min of back-to-back GPU jobs (96% use,
  83–89 °C, 70–85 W), the box **powered off hard** at 16:04:46. No log, panic, OOM or Xid; memory was
  84 of 121 GiB. It matches public reports of GB10 units powering off under sustained load, on this kernel
  and driver. Evidence and the support-case draft are in `~/dgx-spark-crash/`, made outside this repo.
  Until it is resolved, don't run long, unattended, back-to-back heavy media batches. If it happens again,
  note the time and leave the `gpu-telemetry` logger running.
- **Desktop software updates break GPU containers until the CDI spec is regenerated.** An update moved
  `libnvidia-egl-wayland` from 1.1.21 to 1.1.23 while the box was up. `/var/run/cdi/nvidia.yaml` still
  listed the old file, so every new `--gpus` container failed with "failed to fulfil mount request".
  Engines that were already running kept working. The fix is
  `sudo systemctl start nvidia-cdi-refresh.service` (or a reboot).
