# karspark: agent instructions

This repo is the source of truth for the DGX Spark `edgexpert-bd12`. Follow `docs/standard.md` for
everything. No exceptions, and no "quick" installs.

## Rules
- Nothing goes on the Spark except through this repo: a `services/<name>/` folder, a row in
  `docs/registry.md`, then `scripts/deploy.sh <name>`. Update the registry in the same commit.
- Spark layout is fixed: `~/models/<kind>/<name>`, `~/engines/<engine>`, `~/services/<service>`, `~/bin`,
  `~/scratch`. Never leave files loose in `~`, never write `*.bak` copies (git is the history), and never
  let a service depend on `~/scratch`.
- `spark-status` flags anything off-standard in `~`. Clean it up or ask the user.
- One GPU service at a time (121 GiB unified). Every GPU unit gets `Conflicts=` with the others plus
  `ExecStartPre=%h/bin/gpu-residency <GiB>`. At most one GPU unit is enabled at boot.
- Docker images come from a Dockerfile in the service folder and are tagged `karspark/<service>:<ver>`.
  Never `docker commit`.
- Secrets live only in `~/services/<service>/env` (chmod 600) on the Spark. The repo holds `env.example`
  with names only. Never print a key. Read it inside the remote command.
- Anything bound to `0.0.0.0` must require `--api-key`. Otherwise bind `127.0.0.1`.

## Access
- `ssh spark` (Tailscale) / `ssh spark-lan` (home LAN). Use single-line `ssh spark '…'` commands.
- The Wi-Fi drops sometimes, so check state before you retry anything that starts a process.
- `sudo` needs the user's password. Hand them the exact commands. Prefer `systemctl --user`.
- Never delete `~/.ssh`, `~/.local/share/keyrings`, or Tailscale state. Losing them loses the box.

## Before a restart or a big download
Ask first. Loading a model takes minutes, and downloads over the Wi-Fi take hours.
Read `docs/lessons.md` before adding any model. It has the working recipes and the traps.
