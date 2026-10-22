# <service>

Copy this folder to `services/<service>/`, fill everything in, then follow docs/standard.md.

| | |
|---|---|
| Purpose | why we run this |
| Model | `<hf-org>/<repo>` @ `<revision sha>` → `~/models/<kind>/<name>` |
| Engine | e.g. vLLM 0.x in `~/engines/vllm`, or image `karspark/<service>:<ver>` |
| Port | from the port plan in docs/registry.md |
| Auth | `--api-key` from `~/services/<service>/env` (required on 0.0.0.0) |
| Memory | measured GiB when loaded |
| Load time | measured |
| Measured | tok/s (code / prose), prefill, date |
| Sources | recipe links, patches and why |
