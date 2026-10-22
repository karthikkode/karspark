# Registry: what is on the Spark

The single source of truth for what's deployed. Update it in the same commit as any change on the box
(see [standard.md](standard.md)).

## Services

| service | kind | model (repo @ revision) | engine | port | memory | boot | status |
|---|---|---|---|---|---|---|---|
| — | | | | | | | *nothing deployed (box wiped to OS 2026-10-22)* |

## Port plan

| range | use |
|---|---|
| 8000–8009 | LLM servers (OpenAI-compatible) |
| 8010–8019 | proxies / gateways in front of LLMs |
| 8100–8199 | audio (TTS / STT) |
| 8188 | ComfyUI (localhost only) |
| 8200–8299 | other APIs |

## Box baseline (after wipe, 2026-10-22)

- DGX OS (Ubuntu 24.04 base), NVIDIA driver 580.178.04, CUDA 13.0, Docker + NVIDIA runtime, Tailscale.
- Disk: 42 GB used of 3.7 TB. Memory: 118 of 121 GiB available.
- Home holds only `.ssh`, `.bashrc`, `.profile`, `.bash_logout`, `.local/share/keyrings`
  (kept in case it holds the Wi-Fi password).
