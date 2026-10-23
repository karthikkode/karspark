# karspark: agent instructions

This repo is the source of truth for the DGX Spark `edgexpert-bd12`. Follow `docs/standard.md` for
everything. No exceptions, and no "quick" installs.

## The one rule
**karspark-core (`:8080`) is the only user-facing service.** Every model, whether text, TTS or image, is a
recipe in `recipes/<model>/<recipe>.yaml`, started by core as part of a mode (`config/modes.yaml`). Never
add a standalone systemd unit, a public port, or a key outside core's key store.

## Rules
- Changes flow repo → Spark: edit recipes/config/core, then `scripts/deploy.sh` (validates, builds,
  copies config, restarts core; engines keep running). Never hand-edit `~/core/config` on the box.
- Update `docs/registry.md` and the recipe README in the same commit as the change. Any behaviour change
  also updates the docs that describe it, in the same commit (the table at the end of `docs/README.md`).
  Agents in other projects rely on `docs/agents.md` and the MCP `INSTRUCTIONS`, so keep both accurate.
- Recipe ids are `<model>:<recipe>`. Public model names are a recipe's `models:` list; `text` is reserved.
- Engines bind `127.0.0.1:${PORT}` only (fixed port per recipe). Images are pinned by digest or built from a
  Dockerfile here as `karspark/...` (bump `-rN` on change). Never `docker commit`.
- vLLM/SGLang memory fractions are of the whole 121 GiB. Engines in the same mode must fit together (115 GiB).
- Weights go in `~/models/<kind>/<name>` via `scripts/fetch-model.sh`, at a pinned 40-char revision.
- Spark layout is fixed: `~/core`, `~/data`, `~/models`, `~/engines`, `~/bin`, `~/scratch`. Nothing loose in
  `~`, no `*.bak` (git is the history), and nothing may depend on `~/scratch`.
- Secrets: only `~/core/env` (chmod 600) on the Spark. Never print a key or commit it. Read it inside the
  remote command. Project keys come from `scripts/karspark-keys`.
- Core's tests (`cd core && uv run pytest`) and `scripts/validate-comfy-graphs.sh` must pass before a deploy.
- Never run anything holding the Docker socket against the box (core, test configs) without first proving
  read-only that it won't touch live engines. A smoke run once killed Flash-Next (docs/lessons.md).

## Docs
`docs/README.md` maps every doc to its reader. Using karspark from another project: `docs/agents.md`.
Changing it: this file, `docs/standard.md`, `docs/lessons.md`, `docs/registry.md`.

## Access
- `ssh spark` (Tailscale) / `ssh spark-lan` (home LAN, `192.168.29.130`, DHCP). Use single-line `ssh spark '…'`.
- The Wi-Fi drops sometimes, so check state before you retry anything that starts a process.
- `sudo` needs the user's password. Hand them the exact commands.
- Never delete `~/.ssh`, `~/.local/share/keyrings`, or Tailscale state. Losing them loses the box.

## Before a restart or a big download
Ask first. A big model takes up to ~15 min to load, and downloads over the Wi-Fi take hours. A mode switch
reloads models too. Read `docs/lessons.md` before adding any model. It has the working recipes and the traps.
