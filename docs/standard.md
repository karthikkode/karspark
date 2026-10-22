# The standard: how anything gets onto the Spark

Nothing lands on the box except through this repo. **One user-facing service exists: the karspark
gateway** (llama-swap on `:8080`). Every model, whether text, TTS or image, is a *recipe* behind it.
See [architecture.md](architecture.md) for why.

## Layout on the Spark (fixed)

```
~/
├── gateway/                  config.yaml (RENDERED from this repo, never hand-edited) + env (API key, 600)
├── models/<kind>/<name>/     weights only. kind = llm | image | audio | vision
├── engines/<engine>/         runtimes: llama-swap/<ver>, hf-cli venv, our TTS/image API wrappers
├── bin/                      helpers deployed from repo spark-bin/ (ks-docker, gpu-residency, spark-status)
└── scratch/                  the ONLY place for ad-hoc work. Disposable; nothing may depend on it
```

Nothing else goes in `~`: no loose files, no `*.bak`, no clones in the home root. `spark-status` flags
anything off-standard.

## Layout in this repo

```
gateway/              VERSION (llama-swap), config.base.yaml, routing.yaml (who may run together), unit
recipes/<model>/      README.md (weights repo + revision, recipe table, measurements) + one <recipe>.yaml per recipe
engines/<engine>/     README.md (pinned image digest / version) + Dockerfile for any custom image
spark-bin/            tools copied to ~/bin on deploy
scripts/              render-config.py, deploy.sh, fetch-model.sh, status.sh
docs/registry.md      what is deployed, measured numbers, status
```

## Names

- Model folder and weights dir: `<model>` in lowercase, e.g. `qwen3.8-27b`.
- Recipe id: **`<model>:<recipe>`**, e.g. `qwen3.8-27b:vllm-mtp`, `qwen3.8-27b:sglang-dflash2`.
- The default recipe carries the alias **`<model>`**, which is what clients normally ask for. Switching the
  default means moving the alias. Clients don't change.
- Containers: `ks-<model>-<recipe>`. Custom images: `karspark/<engine>-<variant>:<ver>`.

## Adding a model: checklist

1. **Registry row** in `docs/registry.md` with status `planned` (what, why, expected memory).
2. **Weights:** `scripts/fetch-model.sh <hf-repo> <40-char-sha> <kind>/<name>`. Always pin the revision.
3. **Engine:** if the recipe needs an image we don't have, add `engines/<engine>/README.md` with the
   **digest**, or a Dockerfile for a custom build. Pull or build it on the Spark. Never `docker commit`.
4. **Recipe:** `recipes/<model>/<recipe>.yaml` plus the README table row. Rules, which `render-config.py`
   enforces:
   - backend binds `-p 127.0.0.1:${PORT}:…`, never all interfaces
   - Docker images are pinned by digest (or are `karspark/` images built here), and `cmdStop` is set
   - no secrets in recipes
   - an explicit `--gpu-memory-utilization` / memory cap, sized for the routing sets it's in
5. **Routing:** add the model to a set in `gateway/routing.yaml` only if it may run alongside others.
   A model in no set runs alone, which is right for anything big.
6. **Deploy:** `scripts/deploy.sh`. It renders, validates, pushes, and llama-swap hot-reloads.
7. **Verify** through the gateway with the key: `/v1/models`, one real request, then measure memory and
   load time and write them in the recipe README and registry.
8. **Commit** recipe + registry together.

## Removing a model or recipe

Delete the recipe file and any routing references, deploy, delete the weights dir on the Spark if no other
recipe uses them, and mark the registry row `removed (date, reason)`.

## Experiments

Only in `~/scratch/<date>-<topic>/`. When one works, promote it through the checklist. When it doesn't,
delete the folder.
