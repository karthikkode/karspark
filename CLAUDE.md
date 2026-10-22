# karspark — agent instructions

This repo manages the DGX Spark `edgexpert-bd12` and the models it serves. Read `README.md` (current state)
and `docs/findings.md` (known problems) before changing anything on the box.

## Access
- `ssh spark` (Tailscale, 100.101.249.74) works anywhere; `ssh spark-lan` (192.168.1.20) at home. Keyless.
- `sudo` on the Spark needs the user's password. Anything under `/etc/systemd/system` needs the user to run it.
  Prefer user units (`systemctl --user`), which don't need sudo.
- Use single-line `ssh spark '…'` commands. The Wi-Fi link drops sometimes: check state before you retry
  anything that starts a process, so you don't launch duplicates.

## Truth and mirror
- The Spark is the source of truth. `spark/` is a **read-only mirror**: never edit it by hand.
  Refresh it with `scripts/pull-config.sh`, then use `git diff` to see what changed on the box.
- To change the box: edit on the Spark (or write the new file here outside `spark/`, then copy it over),
  restart, verify, pull-config, commit.
- Never commit secrets. `~/.config/qwen38/api-key` and `~/.config/qwen38-27b-hybrid/env` stay on the Spark.
  Read the key remotely inside the command (`$(cat ~/.config/qwen38/api-key)`) and never print it.

## Memory rule
121 GiB unified memory, one big tenant at a time. Switch models through their systemd units (whose
`Conflicts=` + `gpu_residency.sh` handle eviction), never by `docker run`/`vllm serve` by hand.
Never `pkill -f` a pattern that could match your own ssh command line.

## Restarts are disruptive
Flash takes about 13–15 min to load, and other machines and agents use it. Ask before stopping or switching
the live model, unless the user has already asked for that switch.

## Docs
When something on the box changes, update the matching `docs/` page and the README "What is running" table
in the same commit.
