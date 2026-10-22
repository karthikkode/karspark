# karspark

Defines everything that runs on our **DGX Spark** (`edgexpert-bd12`, GB10, 121 GiB unified memory).
If it isn't in this repo, it isn't supposed to be on the Spark.

## One endpoint for every model

```
http://100.101.249.74:8080/v1      OpenAI-compatible (chat, embeddings, audio, images) + Anthropic /v1/messages
Authorization: Bearer <KARSPARK_API_KEY>       (key lives in ~/gateway/env on the Spark)
http://100.101.249.74:8080/ui      dashboard: loaded models, logs, load/unload
```

The key works as `Authorization: Bearer <key>` or `x-api-key: <key>` (Anthropic SDKs). The `/ui` page
asks the browser for a login: any username, with the key as the password.

The gateway is **llama-swap**. Ask for a model by name, and it starts that model's backend on demand
(stopping whatever has to make room), proxies the request, and unloads the model when it goes idle. Each
model can have several **recipes** (engine + flags), named `<model>:<recipe>`. The bare `<model>` alias
points at the default recipe.

Get the key on the laptop:

```bash
ssh spark "sed -n 's/^KARSPARK_API_KEY=//p' ~/gateway/env"
```

What's deployed: [docs/registry.md](docs/registry.md).

## Docs

- [docs/architecture.md](docs/architecture.md): why a single gateway, and how recipes and coexistence work
- [docs/standard.md](docs/standard.md): layout and the checklist for adding a model
- [docs/registry.md](docs/registry.md): recipes, engines and ports actually on the box
- [docs/lessons.md](docs/lessons.md): working recipes and GB10 traps from the first setup

## Commands

Status (the same summary prints when you SSH in):

```bash
scripts/status.sh
```

Download weights at a pinned revision:

```bash
scripts/fetch-model.sh <hf-repo> <revision-sha> <kind>/<name>
```

Render + validate + push the gateway config (llama-swap hot-reloads, so the endpoint never drops):

```bash
scripts/deploy.sh
```

## Access

`ssh spark` (Tailscale, works anywhere) · `ssh spark-lan` (`192.168.29.130`, at home) · `sudo` needs the password.
