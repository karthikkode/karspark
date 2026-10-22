# The standard: how anything gets onto the Spark

The Spark was wiped to the OS on 2026-10-22. From here on, **nothing lands on the box except through
this repo.** If it isn't in the repo, it isn't supposed to be on the Spark.

## Layout on the Spark (fixed)

```
~/
├── models/<kind>/<name>/     weights only. kind = llm | image | audio | vision
├── engines/<engine>/         shared runtimes: venvs, source checkouts (e.g. engines/vllm, engines/comfyui)
├── services/<service>/       one dir per service, deployed from this repo: launch.sh, env (secret, 600)
├── bin/                      helpers deployed from repo spark-bin/ (gpu-residency, spark-status)
└── scratch/                  the ONLY place for ad-hoc experiments. Disposable and never referenced by a service
```

Nothing else goes in `~`. No loose `*.py` or `*.sh` files, no `*.bak`, no clones in the home root.
Upstream recipe repos are cloned under `engines/` or `scratch/`, never into `~`.

Docker images are tagged `karspark/<service>:<version>` and built from a Dockerfile in
`services/<service>/`. **Never use `docker commit`**: an image nobody can rebuild is a liability, and a
committed image bakes the container's command line, API keys included, into its metadata.

## Layout in this repo

```
services/<service>/
  README.md        what it is, model + revision, engine + version, port, memory, how measured, sources
  service.unit     systemd --user unit (installed as ~/.config/systemd/user/<service>.service)
  launch.sh        the exact command; reads secrets from ~/services/<service>/env
  env.example      variable NAMES only, never values
  Dockerfile       if it runs in a container
spark-bin/         tools copied to ~/bin on deploy
docs/registry.md   the single table of what is deployed, ports, memory, boot status
```

## Adding a model or service: checklist

1. **Decide and record.** Add a row to `docs/registry.md` with status `planned`: name, kind, port,
   expected memory, why it's needed. Pick a port from the port plan there.
2. **Download** into `~/models/<kind>/<name>/` with a pinned revision:
   `HF_HUB_DISABLE_XET=1 hf download <repo> --revision <sha> --local-dir ~/models/<kind>/<name>`
   (Xet hangs on the Spark's Wi-Fi). Write the repo + revision into the service README.
3. **Write the service** from `services/_template/`. Rules:
   - binds `0.0.0.0` only with `--api-key`; anything without auth binds `127.0.0.1`
   - GPU services: `Conflicts=` every other GPU service, plus `ExecStartPre=%h/bin/gpu-residency <GiB> …`
   - `Restart=on-failure`, a `StartLimitBurst` so a broken config can't loop through 10-minute weight loads
   - secrets live only in `~/services/<service>/env` (chmod 600)
4. **Deploy:** `scripts/deploy.sh <service>`. It copies the files and installs the unit, but doesn't start it.
5. **Start and verify:** `ssh spark 'systemctl --user start <service>'`, then `/v1/models` plus one real request.
   Write the measured numbers (tok/s, memory, load time) into the service README.
6. **Boot:** at most **one** GPU service is `enabled`. Record which one in the registry.
7. **Commit** the service dir and the registry row together. Status becomes `deployed`.

## Removing something

Stop and disable the unit, delete its `~/services/<service>` dir, unit file and model dir, then mark the
registry row `removed (date, reason)`. Delete the service dir from the repo; git history keeps it.

## Experiments

Experiments are welcome, but only in `~/scratch/<date>-<topic>/`. When one works, promote it through the
checklist. When it doesn't, `rm -rf` the folder. A service must never depend on anything in `scratch/`.
