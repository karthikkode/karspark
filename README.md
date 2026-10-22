# karspark

The one place that defines what runs on our **DGX Spark** (`edgexpert-bd12`, GB10, 121 GiB unified memory).
If it isn't in this repo, it isn't supposed to be on the Spark.

**Current state (2026-10-22): wiped to the OS. No models, no services.** See [docs/registry.md](docs/registry.md).

## How it works

- [docs/standard.md](docs/standard.md): the fixed layout on the box and the checklist for adding anything.
- [docs/registry.md](docs/registry.md): the table of what's deployed, ports, memory, boot status.
- [docs/lessons.md](docs/lessons.md): what we learned the first time (recipes, GB10 gotchas). Read it before re-adding a model.
- `services/<name>/`: one folder per service (README, unit, launch script, env.example). Start from `services/_template/`.
- `spark-bin/`: tools installed to `~/bin` on the Spark (`gpu-residency`, `spark-status`).

## Commands

See what's running (the same summary prints when you SSH in):

```bash
scripts/status.sh
```

Deploy a service (copies files and installs the unit; doesn't start it):

```bash
scripts/deploy.sh <service>
```

## Access

- `ssh spark`: Tailscale, `100.101.249.74`, works anywhere.
- `ssh spark-lan`: `192.168.1.20`, at home.
- Keyless SSH; `sudo` needs the password.
