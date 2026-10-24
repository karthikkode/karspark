"""karspark ComfyUI nodes: sampling that can pause without losing progress, and a fast GAN upscaler.

`KarsparkSampler` is `SamplerCustomAdvanced` plus three things, all keyed to a per-job directory that the
adapter owns (`/jobs/<job_id>`, shared with karspark-core):

  progress   writes `progress.json` ({stage, step, total}) after every sampling step
  pause      if the file `PAUSE` exists at a step boundary, saves the current latent + step to
             `ckpt/<stage>.<n>.pause.pt` and stops the run (InterruptProcessingException)
  resume     on the next run of the same graph, a saved pause state continues from that step, and a
             finished stage (`ckpt/<stage>.<n>.done.pt`) is returned without sampling again

`<n>` counts calls of the same stage within one run, so list execution (SeedVR2 video chunks) checkpoints
per chunk. Resuming recomputes at most the one step that was in flight. Ancestral noise is fast-forwarded,
so a resumed euler_ancestral run draws the same noise it would have drawn uninterrupted.
"""
import inspect
import json
import os
import time
from pathlib import Path

import torch

import comfy.model_management
import comfy.sample
import comfy.samplers
import comfy.utils
import latent_preview
from comfy.k_diffusion import sampling as k_sampling

_calls: dict[tuple[str, str], int] = {}


def _call_index(run_id, stage):
    key = (run_id, stage)
    _calls[key] = _calls.get(key, -1) + 1
    return _calls[key]


def _atomic_save(obj, path: Path):
    """Write, fsync, rename, fsync the folder: a power cut leaves the old file or the new one, never an empty
    one (the box once lost power mid-run and the renamed checkpoint was empty: docs/lessons.md)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        torch.save(obj, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _load(path: Path):
    """A checkpoint, or None if it is missing or unreadable (cut short by a power loss). An unreadable one is
    set aside and the stage runs again from its start; seeds are fixed, so the output is the same."""
    if not path.exists():
        return None
    try:
        return torch.load(path, weights_only=False)
    except Exception as e:
        print(f"[karspark] unreadable checkpoint {path.name} ({type(e).__name__}: {e}); redoing that stage", flush=True)
        os.replace(path, path.with_suffix(path.suffix + ".bad"))
        return None


def _to_cpu(x):
    """Detach a latent (plain or nested tensor) onto the CPU for saving."""
    if hasattr(x, "tensors") and not isinstance(x, torch.Tensor):
        return type(x)([t.detach().to("cpu") for t in x.tensors])
    if getattr(x, "is_nested", False):
        return x.detach().to("cpu")
    return x.detach().to("cpu").clone()


def _to(x, like):
    ref = like.tensors[0] if hasattr(like, "tensors") else like
    return x.to(device=ref.device, dtype=ref.dtype)


class CheckpointingSampler(comfy.samplers.Sampler):
    """Wraps a KSAMPLER so sampling can stop at a step boundary and later continue from it."""

    def __init__(self, inner, resume_state, state_path: Path, pause_flag: Path, on_step):
        self.inner = inner
        self.resume_state = resume_state
        self.state_path = state_path
        self.pause_flag = pause_flag
        self.on_step = on_step

    def max_denoise(self, model_wrap, sigmas):
        return self.inner.max_denoise(model_wrap, sigmas)

    def sample(self, model_wrap, sigmas, extra_args, callback, noise, latent_image=None, denoise_mask=None,
               disable_pbar=False):
        inner = self.inner
        if not isinstance(inner, comfy.samplers.KSAMPLER):
            # Unknown sampler type: run it whole. A pause then takes effect when this stage finishes.
            return inner.sample(model_wrap, sigmas, extra_args, callback, noise, latent_image, denoise_mask,
                                disable_pbar)
        extra_args["denoise_mask"] = denoise_mask
        model_k = comfy.samplers.KSamplerX0Inpaint(model_wrap, sigmas)
        model_k.latent_image = latent_image
        if inner.inpaint_options.get("random", False):
            generator = torch.manual_seed(extra_args.get("seed", 41) + 1)
            model_k.noise = torch.randn(noise.shape, generator=generator, device="cpu").to(noise.dtype).to(noise.device)
        else:
            model_k.noise = noise

        start = 0
        if self.resume_state is not None:
            start = int(self.resume_state["step"])
            x = _to(self.resume_state["x"], noise)
        else:
            x = model_wrap.inner_model.model_sampling.noise_scaling(sigmas[0], noise, latent_image,
                                                                    inner.max_denoise(model_wrap, sigmas))
        total = len(sigmas) - 1

        def k_callback(d):
            i = start + d["i"]
            if callback is not None:
                callback(i, d["denoised"], d["x"], total)
            self.on_step(i + 1, total)
            if self.pause_flag.exists():
                # d["x"] is the state *before* step i is applied, so resume recomputes step i.
                _atomic_save({"step": i, "x": _to_cpu(d["x"])}, self.state_path)
                raise comfy.model_management.InterruptProcessingException()

        kwargs = dict(inner.extra_options)
        params = inspect.signature(inner.sampler_function).parameters
        if start > 0 and "noise_sampler" in params and kwargs.get("noise_sampler") is None:
            seed = extra_args.get("seed")
            ns = k_sampling.default_noise_sampler(x, seed=seed)
            # Each ancestral step that does not land on sigma 0 draws noise once; replay the draws we skip.
            for j in range(start):
                if float(sigmas[j + 1]) > 0:
                    ns(sigmas[j], sigmas[j + 1])
            kwargs["noise_sampler"] = ns
        samples = inner.sampler_function(model_k, x, sigmas[start:], extra_args=extra_args, callback=k_callback,
                                         disable=disable_pbar, **kwargs)
        return model_wrap.inner_model.model_sampling.inverse_noise_scaling(sigmas[-1], samples)


class KarsparkSampler:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "noise": ("NOISE",), "guider": ("GUIDER",), "sampler": ("SAMPLER",), "sigmas": ("SIGMAS",),
            "latent_image": ("LATENT",),
            "job_dir": ("STRING", {"default": ""}),
            "stage": ("STRING", {"default": "s1"}),
            "run_id": ("STRING", {"default": ""}),
        }}

    RETURN_TYPES = ("LATENT", "LATENT")
    RETURN_NAMES = ("output", "denoised_output")
    FUNCTION = "sample"
    CATEGORY = "karspark"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return time.time()  # never reuse ComfyUI's cache: checkpoints decide what is skipped

    def sample(self, noise, guider, sampler, sigmas, latent_image, job_dir, stage, run_id):
        job = Path(job_dir)
        ckpt = job / "ckpt"
        ckpt.mkdir(parents=True, exist_ok=True)
        name = f"{stage}.{_call_index(run_id, stage)}"
        done_path, state_path = ckpt / f"{name}.done.pt", ckpt / f"{name}.pause.pt"

        out = _load(done_path)
        if out is not None:
            return (out, out)

        resume = _load(state_path)
        if resume is None and (job / "PAUSE").exists():
            # Paused between stages (e.g. during a decode): stop before this stage starts. Nothing is lost.
            raise comfy.model_management.InterruptProcessingException()
        progress_path = job / "progress.json"

        def on_step(step, total):
            tmp = progress_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"stage": name, "step": step, "total": total, "t": time.time()}))
            os.replace(tmp, progress_path)

        wrapped = CheckpointingSampler(sampler, resume, state_path, job / "PAUSE", on_step)

        # Same body as SamplerCustomAdvanced (comfy_extras/nodes_custom_sampler.py), with our sampler.
        latent = latent_image.copy()
        samples_in = comfy.sample.fix_empty_latent_channels(guider.model_patcher, latent["samples"],
                                                            latent.get("downscale_ratio_spacial", None),
                                                            latent.get("downscale_ratio_temporal", None))
        latent["samples"] = samples_in
        noise_mask = latent.get("noise_mask")
        x0_output = {}
        callback = latent_preview.prepare_callback(guider.model_patcher, sigmas.shape[-1] - 1, x0_output)
        disable_pbar = not comfy.utils.PROGRESS_BAR_ENABLED
        samples = guider.sample(noise.generate_noise(latent), samples_in, wrapped, sigmas, denoise_mask=noise_mask,
                                callback=callback, disable_pbar=disable_pbar, seed=noise.seed)
        samples = samples.to(comfy.model_management.intermediate_device())
        out = latent.copy()
        out.pop("downscale_ratio_spacial", None)
        out.pop("downscale_ratio_temporal", None)
        out["samples"] = samples
        _atomic_save({k: (_to_cpu(v) if isinstance(v, torch.Tensor) or hasattr(v, "tensors") else v)
                      for k, v in out.items()}, done_path)
        state_path.unlink(missing_ok=True)
        return (out, out)


class KarsparkUpscaleWithModel:
    """`ImageUpscaleWithModel` in fp16, one whole frame per pass. The stock node forces fp32 and 512 px tiles:
    on the GB10 RealESRGAN-anime ran 2.6 s a 1280x704 frame (fp16 is 1.85x faster with a mean difference of
    0.0004, whole frames skip the ~1.7x tile overlap, and channels-last memory is another 1.5x). Real-ESRGAN's own
    inference runs fp16 too. Falls back
    to smaller tiles on OOM, and to fp32 for a model that can't run in half precision."""
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "upscale"
    CATEGORY = "karspark"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"upscale_model": ("UPSCALE_MODEL",), "image": ("IMAGE",)}}

    def upscale(self, upscale_model, image):
        mm = comfy.model_management
        mm.load_models_gpu([upscale_model.patcher], memory_required=image.nelement() * 2 * 64, force_full_load=True)
        device = upscale_model.patcher.load_device
        half = getattr(upscale_model, "supports_half", True) and device.type == "cuda"
        net = (upscale_model.model.half() if half else upscale_model.model.float()).to(memory_format=torch.channels_last)
        dtype = torch.float16 if half else torch.float32
        scale = upscale_model.scale
        out_device = mm.intermediate_device()
        frames = image[..., :3].movedim(-1, -3)
        n, _, h, w = frames.shape
        out = torch.empty((n, 3, h * scale, w * scale), dtype=mm.intermediate_dtype(), device=out_device)
        pbar = comfy.utils.ProgressBar(n)
        tile = max(h, w)
        try:
            with torch.inference_mode():
                for i in range(n):
                    x = frames[i:i + 1].to(device, dtype).contiguous(memory_format=torch.channels_last)
                    while True:
                        try:
                            if tile >= max(h, w):
                                y = net(x)
                            else:
                                y = comfy.utils.tiled_scale(x, lambda a: net(a), tile_x=tile, tile_y=tile, overlap=32,
                                                            upscale_amount=scale, output_device=device)
                            break
                        except Exception as e:
                            mm.raise_non_oom(e)
                            mm.soft_empty_cache()
                            tile = min(tile, 1024) // 2
                            if tile < 128:
                                raise
                    out[i] = torch.clamp(y[0].float(), 0, 1).to(out_device, out.dtype)
                    pbar.update(1)
        finally:
            upscale_model.model.float().to(memory_format=torch.contiguous_format)
        return (out.movedim(-3, -1),)


NODE_CLASS_MAPPINGS = {"KarsparkSampler": KarsparkSampler, "KarsparkUpscaleWithModel": KarsparkUpscaleWithModel}
NODE_DISPLAY_NAME_MAPPINGS = {"KarsparkSampler": "Karspark Sampler (pausable)",
                              "KarsparkUpscaleWithModel": "Karspark Upscale with model (fp16, whole frames)"}
