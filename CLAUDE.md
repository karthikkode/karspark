# karspark: agent instructions

This repo is the source of truth for the DGX Spark `edgexpert-bd12`. Follow `docs/standard.md` for
everything. No exceptions, and no "quick" installs.

## The one rule
**The karspark gateway (llama-swap, `:8080`) is the only user-facing service.** Every model, whether text,
TTS or image, is a recipe in `recipes/<model>/<recipe>.yaml` behind it. Never add a standalone systemd
unit, a public port, or a second API key for a model.

## Rules
- Changes flow repo → Spark: edit the recipes/routing, then `scripts/deploy.sh` (it renders, validates and
  hot-reloads). Never hand-edit `~/gateway/config.yaml` on the box.
- Update `docs/registry.md` and the recipe README in the same commit as the change.
- Recipe ids are `<model>:<recipe>`. The default recipe carries the bare `<model>` alias. Routing sets in
  `gateway/routing.yaml` use matrix vars (set expressions can't contain `:`).
- Backends bind `127.0.0.1:${PORT}` only. Images are pinned by digest or built from a Dockerfile here as
  `karspark/...`. Never `docker commit`.
- vLLM/SGLang memory fractions are of the whole 121 GiB. Models in the same routing set must fit together.
- Weights go in `~/models/<kind>/<name>` via `scripts/fetch-model.sh`, at a pinned 40-char revision.
- Spark layout is fixed: `~/gateway`, `~/models`, `~/engines`, `~/bin`, `~/scratch`. Nothing loose in `~`,
  no `*.bak` (git is the history), and nothing may depend on `~/scratch`.
- Secrets: only `~/gateway/env` (chmod 600) on the Spark. Never print the key or commit it. Read it inside
  the remote command.

## Access
- `ssh spark` (Tailscale) / `ssh spark-lan` (home LAN, `192.168.29.130`, DHCP). Use single-line `ssh spark '…'`.
- The Wi-Fi drops sometimes, so check state before you retry anything that starts a process.
- `sudo` needs the user's password. Hand them the exact commands.
- Never delete `~/.ssh`, `~/.local/share/keyrings`, or Tailscale state. Losing them loses the box.

## Before a restart or a big download
Ask first. A big model takes up to ~15 min to load, and downloads over the Wi-Fi take hours.
Read `docs/lessons.md` before adding any model. It has the working recipes and the traps.
