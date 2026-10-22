# Audit findings (2026-10-22)

Nothing here is broken right now: Flash has been serving for 9 days with 0 restarts. These are the things
that will bite on the next reboot, rebuild or switch. Ordered by how much they matter.

## 1. The live image can't be rebuilt, and it holds the API key

`qwen38-flash:v1.4-prefixfix` was made with `docker commit` from a running container. So:

- No Dockerfile produces it. If the image is lost or pruned, the fix is gone. The diff is now saved in
  [patches/vllm-prefixfix.patch](../patches/vllm-prefixfix.patch).
- The commit recorded the container's full command line as the image `CMD`, and that includes the
  **live API key** (same value as `~/.config/qwen38/api-key`). Anyone who can run `docker history` sees it,
  and so does any registry it gets pushed to.

**Fix:** write `images/flash-prefixfix/Dockerfile` here (`FROM qwen38-flash:v1.4` + the patch), build it
as `v1.5`, point `launch-flash.sh` at it, then rotate the key and update the clients.

## 2. Three conflicting GPU units are enabled at boot

`qwen38-flash`, `qwen38-27b-hybrid` and `comfyui` are all `enabled` (WantedBy=default.target), and Flash
`Conflicts=` the other two. At boot systemd has to drop some of them, and which one survives isn't
something you chose. Flash happened to win on 2026-10-12.

**Fix:** enable exactly one boot model (Flash) and disable the other two. Start those on demand.

## 3. Two layers of units, and three switch scripts

- `qwen38-flash` exists as a **system** unit (disabled) and as a **user** unit (live). Both run the same
  launch script with the same container name and port. Enabling the system one, which the upstream
  `~/dgx-spark-qwen38/switch-model.sh` does, would fight the user unit.
- `qwen38-keepalive` (system, running) and `qwen38-sglang` (system) are still on the system layer.
- `~/.config/qwen38/launch-flash.sh` says it is "regenerated on every install", so **re-running upstream `install.sh`
  would overwrite the local changes** (prefixfix image, prefix caching, seqs 8, util 0.85).
- Switch scripts: upstream `switch-model.sh` (system units), `~/bin/switch-model.sh` (vllm-122b + Hermes),
  and `switch-flash.sh` (launch-script swap). None of them knows the full picture.

**Fix:** standardize on user units (sudo isn't needed for them), move keepalive to a user unit, and write a
single `scripts/switch.sh <flash|27b|27b+comfy|abliterated|comfy>` in this repo. Never run upstream
`install.sh` again without diffing against `spark/` first.

## 4. Unauthenticated model ports on 0.0.0.0

- `:8002` (abliterated) never passes `--api-key`: the unit loads the env file but `serve.sh` ignores it.
- `:8000` started through `vllm-122b` has no key either. The hybrid unit does add one.

Both bind every interface: LAN and Tailscale. On the home Wi-Fi anyone on the network can use them.

**Fix:** pass `--api-key "$VLLM_API_KEY"` in the abliterated `serve.sh`. Use only the hybrid unit for the 27B.

## 5. `vllm-122b.service` is misnamed, and its coexistence drop-in does nothing

- The unit serves **Qwen3.8-27B**: drop-in `serve-qwen38-27b.conf` replaces ExecStart. The 122B weights are gone.
- Its `90-hybrid-qwen-flux.conf` sets an empty `Conflicts=` to allow ComfyUI alongside. systemd can't
  reset dependencies from a drop-in, and `systemctl show` still lists `Conflicts=comfyui.service`.
  `qwen38-27b-hybrid.service` is the unit that actually coexists.

**Fix:** retire `vllm-122b` and point `~/bin/switch-model.sh` and `render_chapter.sh` at `qwen38-27b-hybrid`.

## 6. Disk you can reclaim (~240 GB, no rush with 3 TB free)

| item | size | why it's safe |
|---|---|---|
| `~/models/qwen38-flash-next-nvfp4-fp8hybrid` | 139 GB | FP8-hybrid experiment was reverted |
| image `qwen38-flash-dgx:latest` | 20.6 GB | superseded blazux build |
| image `qwen38-flash:v1.4` | 20.6 GB | keep **until** the Dockerfile from #1 exists, since it's the base |
| `~/fish-speech` | 2.8 GB | superseded by IndexTTS-2 |
| `~/models/moss-*` + `~/moss-vg` | ~16 GB | MOSS rejected for voice design |

Left in place; the user decides.

## 7. Stale references

- `indextts-2.service` appears in the `Conflicts=` lists of 3 units, but no such unit exists.
- `~/models/qwen38-27b-abliterated/DOWNLOAD-STATUS.txt` says `downloading`. The download finished
  (18/18 shards). The `.download.pid` and `.log` next to it are leftovers.
- The Spark-side **Hermes gateway** is running with 0 cron jobs, and its config still names `qwen35-122b`
  on `:8000`, which no longer exists. Either point it at Flash (`:30001`, `qwen3.8-flash-next`) or stop it.
- `vllm-122b.service` header comment and the `ComfyUI` unit both talk about the 122B.

## 8. Memory headroom while Flash runs

109/121 GiB used, about 12 GiB available, with `--gpu-memory-utilization 0.85`. That's fine for a headless
box. Don't start anything else heavy (ComfyUI, TTS, a second vLLM) by hand while Flash is up, because
`gpu_residency.sh` only protects starts that go through systemd.
