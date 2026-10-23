# The standard: how anything gets onto the Spark

Nothing lands on the box except through this repo. **One user-facing service exists: karspark-core** on
`:8080`. Every model, whether text, TTS or image, is an *engine* started from a recipe, and it is reached
only through core. See [architecture.md](architecture.md) for why.

## Layout on the Spark (fixed)

```
~/
├── core/                     env (pepper + owner key, chmod 600), config/ (recipes + config, copied by deploy)
├── data/                     karspark.db, logs.db, jobs/<id>/{in,out,ckpt}, files/  (core's state; root-owned)
├── models/<kind>/<name>/     weights only. kind = llm | image | video | audio | vision
├── engines/<engine>/         engine STATE only (caches, token maps), deployed from repo engines/<engine>/spark/
├── bin/                      spark-status (from repo spark-bin/)
└── scratch/                  the ONLY place for ad-hoc work. Disposable; nothing may depend on it
```

Nothing else goes in `~`: no loose files, no `*.bak`, no clones in the home root. `spark-status` flags
anything off-standard.

**HARD RULE: model weights live only in `~/models/<kind>/<name>/`.** Never in a Hugging Face cache, a
Docker volume, an engine cache or `~/scratch`. Downloads use `--local-dir` with no persistent Xet chunk cache
(staging is cleaned on success). Every recipe mounts its weights from `${models}` and runs with
`HF_HUB_OFFLINE=1`, so an engine can never fetch a model into a cache. `python -m karspark.config` (run by
deploy) refuses recipes that break this, and `spark-status` flags any large file outside `~/models`.

**Everything runs in Docker**: core, every engine, downloads, benchmarks. Nothing (Python, CUDA libraries,
binaries) is installed on the host, so versions can never clash. Images are built from a Dockerfile in this
repo (`scripts/build-images.sh`, tagged `karspark/...`) or pulled by pinned digest. Core starts engines as
sibling containers through the host's Docker socket, so recipe paths are host paths (`${models}`,
`${engines}`, `${data}`). Core only ever touches containers labelled `karspark.recipe=<id>`.

## Layout in this repo

```
core/                 karspark-core: the package, tests, Dockerfile, karspark-core.service
config/               modes.yaml (engines loaded together), tiers.yaml (key tiers), scheduler.yaml
recipes/<model>/      README.md (weights repo + revision, recipe table, measurements) + one <recipe>.yaml per recipe
engines/<engine>/     README.md (pinned image digest / version) + Dockerfile for any custom image
spark-bin/            tools copied to ~/bin on deploy
scripts/              deploy.sh, build-images.sh, karspark-keys, fetch-model.sh, fetch-pth.sh, status.sh, ...
docs/registry.md      what is deployed, measured numbers, status
```

## Names

- Model folder and weights dir: `<model>` in lowercase, e.g. `qwen3.8-27b`.
- Recipe id: **`<model>:<recipe>`** (folder:file), e.g. `qwen3.8-27b:vllm`, `qwen3.8-flash:vllm-salient`.
- Public model names are the recipe's `models:` list (e.g. `qwen3.8-flash`, `flux2-dev`). `text` is reserved:
  it means the text model of the current mode.
- Containers: `ks-<model>-<recipe>`. Custom images: `karspark/<engine>[-<variant>]:<ver>`; bump `-rN` on
  any change (builds skip existing tags).

## A recipe

```yaml
kind: text | media
capabilities: [text]                 # or image, layers, video, upscale_image, upscale_video
models: [qwen3.8-27b]                # public names it serves
served_name: qwen3.8-27b             # what the backend calls it (text)
port: 10030                          # fixed, 10001..10999, unique; bound on 127.0.0.1 only
memory_gib: 32                       # free memory needed before start; counts toward the mode's 115 GiB
slots: 4                             # concurrent text requests (= --max-num-seqs) or media jobs
max_tokens_default: 8192
load_timeout_s: 1200
run: |                               # docker run args; core adds --name ks-..., -d, --init and its label
  --gpus all ... -p 127.0.0.1:${PORT}:8000 -v ${models}/llm/qwen3.8-27b-nvfp4:/model:ro -e HF_HUB_OFFLINE=1
  <image pinned by digest, or karspark/...> <args>
```

Validation (`python -m karspark.config`, run by deploy) enforces: loopback-only port, pinned image,
`HF_HUB_OFFLINE=1`, weights from `${models}`, no literal secrets, nothing from `~/scratch`, unique ports,
and every mode under 115 GiB.

## Adding a model: checklist

1. **Registry row** in `docs/registry.md` with status `planned` (what, why, expected memory).
2. **Weights:** `scripts/fetch-model.sh <hf-repo> <40-char-sha> <kind>/<name>`. Always pin the revision.
   A small `.pth` that is not on Hugging Face (e.g. an ESRGAN from a GitHub release): `scripts/fetch-pth.sh
   <url> <sha256> <kind>/<name>`. It pins by sha256, stores `.safetensors`, and records the source in `SOURCES`.
3. **Engine:** if the recipe needs an image we don't have, add `engines/<engine>/README.md` with the
   **digest**, or a Dockerfile for a custom build. Pull or build it on the Spark. Never `docker commit`.
4. **Recipe:** `recipes/<model>/<recipe>.yaml` plus the README table row. Media models also need graphs in
   `engines/comfyui/adapter/workflows.py` (checked with `scripts/validate-comfy-graphs.sh`).
5. **Mode:** add the recipe to a mode in `config/modes.yaml`. A mode's engines run together, so they must
   fit in memory together.
6. **Deploy:** `scripts/deploy.sh`. It validates, builds images, copies the config and restarts core. Engines
   that keep running are adopted, not reloaded.
7. **Verify** through core with a key: `/v1/models`, one real request or job, then measure memory and load
   time (`/v1/status`, `engine_events`), and write them in the recipe README and registry.
8. **Commit** recipe + registry together.

## Removing a model or recipe

Delete the recipe file and its mode references, deploy, delete the weights dir on the Spark if no other
recipe uses them, and mark the registry row `removed (date, reason)`.

## Experiments

Only in `~/scratch/<date>-<topic>/`. When one works, promote it through the checklist. When it doesn't,
delete the folder. Never run anything that holds the Docker socket (core, test configs) against the box
without proving read-only first that it won't touch live engines (see lessons.md).
