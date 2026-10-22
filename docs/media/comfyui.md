# ComfyUI — image generation

**Status 2026-10-22: STOPPED** (unit enabled at boot, but Flash wins the boot race and its `Conflicts=` stops ComfyUI).

| | |
|---|---|
| Code | `~/ComfyUI` (upstream `comfyanonymous/ComfyUI`, commit `1568e6c`) |
| Python | `~/flux2/.venv` (torch 2.13 cu130) |
| Models | `~/ComfyUI/models` → symlink to **`~/models/comfyui`** |
| Custom nodes | `katha_reference_cleanup`, `websocket_image_save.py` |
| Unit | user `comfyui.service`: `main.py --listen 127.0.0.1 --port 8188` (localhost only; reach it over an SSH tunnel) |
| Manual launcher | `~/flux2/start_comfyui.sh [--lan]`, which adds `--reserve-vram 16 --disable-pinned-memory` and `expandable_segments` |
| Drop-in | `90-hybrid-qwen-flux.conf`: waits for 40 GiB instead of evicting the 122B/27B (coexistence mode) |

## Models installed

| file | size | use |
|---|---|---|
| `diffusion_models/flux2-dev-nvfp4.safetensors` (→ HF cache) | ~20 GB | FLUX.2 [dev] NVFP4, **default** |
| `diffusion_models/flux2-dev-nvfp4-mixed.safetensors` (→ `~/models/flux2-dev-nvfp4-hf`) | | FLUX.2 mixed precision |
| `loras/Flux2TurboComfyv2.safetensors` | | Turbo LoRA (6 steps) |
| `text_encoders/mistral_3_small_flux2_fp4_mixed.safetensors` | | FLUX.2 text encoder |
| `vae/flux2-vae.safetensors` | | FLUX.2 VAE |
| `diffusion_models/qwen_image_2.1_{nvfp4,int8_convrot}.safetensors` | 4.2 / 7.3 GB | Qwen-Image 2.1 |
| `diffusion_models/qwen_image_layered_fp8mixed.safetensors` | 20.5 GB | Qwen-Image layered |
| `text_encoders/qwen3vl_8b_{nvfp4,int8_convrot}`, `qwen_2.5_vl_7b_fp8_scaled` | 7.5–9.4 GB | Qwen-Image encoders |
| `vae/qwen_image_2.1_vae_bf16`, `qwen_image_layered_vae` | | |
| `upscale_models/4x-UltraSharp.safetensors` | 64 MB | upscaler |

The `~/models/comfyui` tree is 68 GB. Scripts: `~/flux2/{generate,edit,upscale}.py`.

## Tuning notes (from `~/flux2/THROUGHPUT.md`, mirrored at [spark/flux2/THROUGHPUT.md](../../spark/flux2/THROUGHPUT.md))

- Defaults: pure NVFP4 + Turbo LoRA, **6 steps**, guidance 2.5, **0.25 MP references**. A 2-reference
  2.36 MP image takes 116 s → 48 s with these.
- **Restart ComfyUI after switching models.** It never evicts cached models, so swapping models in one
  session leaks 20+ GB and eventually OOMs the driver.
- **Never** use `--highvram` or `--gpu-only` on GB10. They pin page-locked memory, and that has hung the
  whole machine (`NV_ERR_NO_MEMORY`).

## Start / tunnel

```bash
ssh spark 'systemctl --user start comfyui'
```

```bash
ssh -N -L 8188:127.0.0.1:8188 spark
```

Starting it stops Flash (`Conflicts=`) unless you're in the 27B coexistence mode.
