"""API-format ComfyUI graphs for every karspark media job.

Each graph follows the official ComfyUI template for its model (comfyui-workflow-templates 0.11.94 in this
image), flattened (no subgraphs, switches or math nodes; Python does that) and pointed at our files:

  flux2-dev           image_flux2_fp8.json                 text-to-image, or 1..6 reference images
  qwen-image-layered  image_qwen_image_layered.json        image -> RGBA layers (or text -> layers)
  ltx-2.5             video_ltx2_5_{t2v,i2v,flf2v}.json    text / first frame / first + last frame -> video
  seedvr2-*           utility_seedvr2_*_upscale_*.json     one-step upscaling, images and video
  flux2-refine, esrgan-anime                               anime-safe and instant image upscaling

Every sampler is KarsparkSampler (../karspark_nodes), so any job can pause at a step boundary and resume
from its checkpoint. Templates that use KSampler are rebuilt from the same pieces (CFGGuider,
BasicScheduler, KSamplerSelect, RandomNoise) for that reason; the maths is identical.

build(kind, params, inputs, ctx):
  params  the job's parameters, including "seed"
  inputs  logical name -> file name in ComfyUI's input folder: "image", "reference_images" (list),
          "first_frame", "last_frame", "video"
  ctx     {"job_dir": "/jobs/<id>", "run_id": "<per attempt>", "out": "<id>/out"} (output folder = /jobs)

Check graphs offline with scripts/validate-comfy-graphs.sh (ComfyUI's own validator, no GPU).
"""
FLUX2 = {
    "unet": "flux2-dev-nvfp4.safetensors",
    "clip": "mistral_3_small_flux2_fp4_mixed.safetensors",
    "vae": "flux2-vae.safetensors",
    "turbo_lora": "Flux2TurboComfyv2.safetensors",
}
QWEN_LAYERED = {
    "unet": "qwen_image_layered_fp8mixed.safetensors",
    "clip": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
    "vae": "qwen_image_layered_vae.safetensors",
}
LTX25 = {
    "unet": "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
    "clip": "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
    "vae": "ltx-2.5-video-vae-bf16.safetensors",
    "audio_vae": "ltx-2.5-audio-vae-bf16.safetensors",
    "upscaler": "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
}
SEEDVR2 = {
    "seedvr2-7b": "seedvr2_7b_fp16.safetensors",                       # best quality (images)
    "seedvr2-7b-sharp": "seedvr2_7b_sharp_int8_convrot.safetensors",   # more detail; watch faces
    "seedvr2-3b": "seedvr2_3b_fp16.safetensors",                       # faster (video default)
}
SEEDVR2_VAE = "seedvr2_ema_vae_fp16.safetensors"
ESRGAN_ANIME = "RealESRGAN_x4plus_anime_6B.safetensors"

# Distilled schedules, verbatim from the LTX-2.5 templates.
LTX_SIGMAS_STAGE1 = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
LTX_SIGMAS_STAGE2 = "0.85, 0.7250, 0.4219, 0.0"
# Template default minus "cartoon" (we make anime). With the distilled model's CFG 1 it has little effect.
LTX_NEGATIVE = "pc game, console game, video game, childish, ugly, blurry, distorted"
# LTX-2.5 always generates a soundtrack with the video. We keep videos silent unless asked: "audio": "sfx"
# keeps the track and steers it to sound effects only. CFG is 1, so the negative prompt can't do this;
# the direction goes in the prompt.
LTX_AUDIO_MODES = ("none", "sfx")
LTX_SFX_DIRECTION = ("Audio: only the diegetic sound effects of the scene (footsteps, wind, cloth, impacts, "
                     "ambience). No music, no background score, no speech, no voices, no singing.")

MAX_REFS = 6

# Public model names per job type; the first is the default.
MODELS = {
    "image": ["flux2-dev"],
    "layers": ["qwen-image-layered"],
    "video": ["ltx-2.5"],
    "upscale_image": ["seedvr2-7b", "seedvr2-7b-sharp", "seedvr2-3b", "flux2-refine", "esrgan-anime"],
    "upscale_video": ["seedvr2-3b", "seedvr2-7b", "seedvr2-7b-sharp"],
}


class WorkflowError(Exception):
    pass


class Graph:
    """Tiny builder: add() returns the node id; out(id, slot) is a link."""

    def __init__(self, ctx):
        self.nodes = {}
        self.ctx = ctx

    def add(self, class_type, **inputs):
        nid = str(len(self.nodes) + 1)
        self.nodes[nid] = {"class_type": class_type, "inputs": inputs}
        return nid

    @staticmethod
    def out(nid, slot=0):
        return [nid, slot]

    def sample(self, noise, guider, sampler, sigmas, latent, stage):
        """Pausable SamplerCustomAdvanced. `sigmas` and `latent` are links, the rest node ids."""
        return self.add("KarsparkSampler", noise=self.out(noise), guider=self.out(guider), sampler=self.out(sampler),
                        sigmas=sigmas, latent_image=latent, job_dir=self.ctx["job_dir"], stage=stage,
                        run_id=self.ctx["run_id"])

    def prefix(self, name):
        return f'{self.ctx["out"]}/{name}'


def build(kind, params, inputs, ctx):
    builders = {"image": build_image, "layers": build_layers, "video": build_video,
                "upscale_image": build_upscale_image, "upscale_video": build_upscale_video}
    if kind not in builders:
        raise WorkflowError(f"unknown job type {kind!r}")
    model = params.get("model") or MODELS[kind][0]
    if model not in MODELS[kind]:
        raise WorkflowError(f"model {model!r} can't do {kind}; use one of: {', '.join(MODELS[kind])}")
    return builders[kind]({**params, "model": model}, inputs, ctx)


def _size(params, default, multiple):
    s = params.get("size") or default
    try:
        w, h = (int(x) for x in str(s).lower().split("x"))
    except ValueError:
        raise WorkflowError(f"size must be WIDTHxHEIGHT, got {s!r}")
    if not (256 <= w <= 4096 and 256 <= h <= 4096):
        raise WorkflowError("size must be between 256 and 4096 on each side")
    return w // multiple * multiple, h // multiple * multiple


def _seed(params):
    return int(params.get("seed", 0))


def _loras(g, model, params):
    """Character/style LoRAs from ~/models/image/loras: [{"name": "x.safetensors", "strength": 1.0}]."""
    for lora in params.get("loras") or []:
        if not isinstance(lora, dict) or not lora.get("name"):
            raise WorkflowError('loras must be a list of {"name": ..., "strength": ...}')
        model = g.add("LoraLoaderModelOnly", model=g.out(model), lora_name=lora["name"],
                      strength_model=float(lora.get("strength", 1.0)))
    return model


def _flux2_model(g, params, turbo):
    unet = g.add("UNETLoader", unet_name=FLUX2["unet"], weight_dtype="default")
    clip = g.add("CLIPLoader", clip_name=FLUX2["clip"], type="flux2", device="default")
    vae = g.add("VAELoader", vae_name=FLUX2["vae"])
    m = unet
    if turbo:
        m = g.add("LoraLoaderModelOnly", model=g.out(m), lora_name=FLUX2["turbo_lora"], strength_model=1.0)
    return _loras(g, m, params), clip, vae


# ---------------------------------------------------------------- images
def build_image(params, inputs, ctx):
    """FLUX.2 [dev]. Default: the Turbo LoRA at 12 steps; "turbo": false = the base model at 20 steps.
    Up to 6 reference images (character sheets, style, objects), chained as ReferenceLatent like the
    template. Without "size", a reference-image job takes the first reference's aspect ratio at 1 MP."""
    if not params.get("prompt"):
        raise WorkflowError("prompt is required")
    refs = list(inputs.get("reference_images") or [])
    if len(refs) > MAX_REFS:
        raise WorkflowError(f"at most {MAX_REFS} reference images")
    turbo = params.get("turbo", True)
    steps = int(params.get("steps") or (12 if turbo else 20))
    g = Graph(ctx)
    m, clip, vae = _flux2_model(g, params, turbo)
    cond = g.add("CLIPTextEncode", text=params["prompt"], clip=g.out(clip))
    cond = g.add("FluxGuidance", conditioning=g.out(cond), guidance=float(params.get("guidance", 4.0)))
    first_size = None
    for name in refs:
        img = g.add("LoadImage", image=name)
        img = g.add("ImageScaleToTotalPixels", image=g.out(img), upscale_method="area", megapixels=1.0,
                    resolution_steps=1)
        if first_size is None:
            first_size = g.add("GetImageSize", image=g.out(img))
        lat = g.add("VAEEncode", pixels=g.out(img), vae=g.out(vae))
        cond = g.add("ReferenceLatent", conditioning=g.out(cond), latent=g.out(lat))
    if first_size is not None and not params.get("size"):
        w, h = g.out(first_size, 0), g.out(first_size, 1)
    else:
        w, h = _size(params, "1024x1024", 16)
    latent = g.add("EmptyFlux2LatentImage", width=w, height=h, batch_size=1)
    out = g.sample(g.add("RandomNoise", noise_seed=_seed(params)),
                   g.add("BasicGuider", model=g.out(m), conditioning=g.out(cond)),
                   g.add("KSamplerSelect", sampler_name="euler"),
                   g.out(g.add("Flux2Scheduler", steps=steps, width=w, height=h)), g.out(latent), "flux2")
    img = g.add("VAEDecode", samples=g.out(out), vae=g.out(vae))
    g.add("SaveImage", images=g.out(img), filename_prefix=g.prefix("image"))
    return g.nodes


def build_layers(params, inputs, ctx):
    """Qwen-Image-Layered: splits "image" into `layers` RGBA layers (background first) for parallax.
    The optional prompt describes the whole picture, including what is hidden behind foreground objects;
    it does not pick the layers. No image = text-to-layers at "size". Template settings: 20 steps, CFG 2.5,
    640 px (the model card's 50 steps / CFG 4 is ~2.5x slower); "resolution": 1024 for high-res."""
    layers = int(params.get("layers", 4))
    if not 1 <= layers <= 10:
        raise WorkflowError("layers must be 1..10")
    g = Graph(ctx)
    unet = g.add("UNETLoader", unet_name=QWEN_LAYERED["unet"], weight_dtype="default")
    m = g.add("ModelSamplingAuraFlow", model=g.out(unet), shift=1.0)
    clip = g.add("CLIPLoader", clip_name=QWEN_LAYERED["clip"], type="qwen_image", device="default")
    vae = g.add("VAELoader", vae_name=QWEN_LAYERED["vae"])
    pos = g.add("CLIPTextEncode", text=params.get("prompt", ""), clip=g.out(clip))
    neg = g.add("CLIPTextEncode", text=params.get("negative_prompt", ""), clip=g.out(clip))
    if inputs.get("image"):
        img = g.add("LoadImage", image=inputs["image"])
        img = g.add("ImageScaleToMaxDimension", image=g.out(img), upscale_method="lanczos",
                    largest_size=int(params.get("resolution", 640)))
        size = g.add("GetImageSize", image=g.out(img))
        w, h = g.out(size, 0), g.out(size, 1)
        lat = g.add("VAEEncode", pixels=g.out(img), vae=g.out(vae))
        pos = g.add("ReferenceLatent", conditioning=g.out(pos), latent=g.out(lat))
        neg = g.add("ReferenceLatent", conditioning=g.out(neg), latent=g.out(lat))
    else:
        if not params.get("prompt"):
            raise WorkflowError("give an image to split, or a prompt for text-to-layers")
        w, h = _size(params, "640x640", 16)
    latent = g.add("EmptyQwenImageLayeredLatentImage", width=w, height=h, layers=layers, batch_size=1)
    # The template's KSampler(euler, simple, CFG 2.5, 20 steps), in pieces so it can pause.
    out = g.sample(g.add("RandomNoise", noise_seed=_seed(params)),
                   g.add("CFGGuider", model=g.out(m), positive=g.out(pos), negative=g.out(neg),
                         cfg=float(params.get("cfg", 2.5))),
                   g.add("KSamplerSelect", sampler_name="euler"),
                   g.out(g.add("BasicScheduler", model=g.out(m), scheduler="simple",
                               steps=int(params.get("steps", 20)), denoise=1.0)), g.out(latent), "layers")
    # Frame 0 is the composite; keep frames 1.. (the layers) as a batch of RGBA images.
    out = g.add("LatentCut", samples=g.out(out), dim="t", index=1, amount=16384)
    out = g.add("LatentCutToBatch", samples=g.out(out), dim="t", slice_size=1)
    img = g.add("VAEDecode", samples=g.out(out), vae=g.out(vae))
    g.add("SaveImage", images=g.out(img), filename_prefix=g.prefix("layer"))
    return g.nodes


# ---------------------------------------------------------------- video
def build_video(params, inputs, ctx):
    """LTX-2.5 distilled. The inputs pick the template:
      no frames            text-to-video: 8 steps at half size, 2x latent upscale, 3 refine steps
      first_frame          image-to-video: the same two stages, the frame anchored at 0.7, then 1.0
      first + last frame   one stage at full size, guides at frames 0 and -1 (strength 0.7)
    "size" is a multiple of 64 (default 1280x720), "duration" 1..20 s (default 5), "fps" (default 24).
    "audio": "none" (default, silent video) or "sfx" (sound effects only, never music or voices).
    The template's optional prompt enhancer is not installed; prompts go in as written."""
    if not params.get("prompt"):
        raise WorkflowError("prompt is required")
    if inputs.get("last_frame") and not inputs.get("first_frame"):
        raise WorkflowError("last_frame needs first_frame too")
    audio_mode = params.get("audio", "none")
    if audio_mode not in LTX_AUDIO_MODES:
        raise WorkflowError(f"audio must be one of: {', '.join(LTX_AUDIO_MODES)}")
    w, h = _size(params, "1280x720", 64)
    fps = int(params.get("fps", 24))
    duration = float(params.get("duration", 5))
    if not 1 <= duration <= 20:
        raise WorkflowError("duration must be 1..20 seconds")
    frames = int(round(duration * fps / 8)) * 8 + 1  # LTX wants 8k+1 frames
    prompt = params["prompt"] + ("\n\n" + LTX_SFX_DIRECTION if audio_mode == "sfx" else "")
    g = Graph(ctx)
    unet = g.add("UNETLoader", unet_name=LTX25["unet"], weight_dtype="default")
    clip = g.add("CLIPLoader", clip_name=LTX25["clip"], type="ltxv", device="default")
    vae = g.add("VAELoader", vae_name=LTX25["vae"])
    avae = g.add("VAELoader", vae_name=LTX25["audio_vae"])
    pos = g.add("CLIPTextEncode", text=prompt, clip=g.out(clip))
    neg = g.add("CLIPTextEncode", text=params.get("negative_prompt", LTX_NEGATIVE), clip=g.out(clip))
    cond = g.add("LTXVConditioning", positive=g.out(pos), negative=g.out(neg), frame_rate=float(fps))
    # The model is audio+video; the audio latent is sampled either way and simply not decoded for "none".
    audio = g.add("LTXVEmptyLatentAudio", frames_number=frames, frame_rate=fps, batch_size=1, audio_vae=g.out(avae))
    if inputs.get("last_frame"):
        video_lat, audio_lat = _ltx_first_last(g, inputs, unet, vae, cond, audio, w, h, frames, params)
    else:
        video_lat, audio_lat = _ltx_two_stage(g, inputs, unet, vae, cond, audio, w, h, frames, params)
    imgs = g.add("VAEDecodeTiled", samples=video_lat, vae=g.out(vae), tile_size=512, overlap=64,
                 temporal_size=64, temporal_overlap=16)
    extra = {}
    if audio_mode != "none":
        extra["audio"] = g.out(g.add("LTXVAudioVAEDecode", samples=audio_lat, audio_vae=g.out(avae)))
    video = g.add("CreateVideo", images=g.out(imgs), fps=float(fps), bit_depth=8, **extra)
    g.add("SaveVideo", video=g.out(video), filename_prefix=g.prefix("video"), format="auto", **{"format.codec": "auto"})
    return g.nodes


def _ltx_two_stage(g, inputs, unet, vae, cond, audio, w, h, frames, params):
    """video_ltx2_5_t2v / i2v. Returns (video latent link, audio latent link)."""
    ups = g.add("LatentUpscaleModelLoader", model_name=LTX25["upscaler"])
    guider = g.add("LTXVDualCFGGuider", model=g.out(unet), positive=g.out(cond, 0), negative=g.out(cond, 1),
                   video_cfg=1.0, audio_cfg=1.0)
    sampler = g.add("KSamplerSelect", sampler_name="euler_ancestral")
    first = None
    if inputs.get("first_frame"):
        first = g.add("LoadImage", image=inputs["first_frame"])
        first = g.add("ImageScaleToMaxDimension", image=g.out(first), upscale_method="lanczos", largest_size=1536)
        first = g.add("LTXVPreprocess", image=g.out(first), img_compression=18)
    # Stage 1: half resolution.
    v = g.add("EmptyLTXVLatentVideo", width=w // 2, height=h // 2, length=frames, batch_size=1)
    if first:
        v = g.add("LTXVImgToVideoInplace", vae=g.out(vae), image=g.out(first), latent=g.out(v), strength=0.7,
                  bypass=False)
    av = g.add("LTXVConcatAVLatent", video_latent=g.out(v), audio_latent=g.out(audio))
    s1 = g.sample(g.add("RandomNoise", noise_seed=_seed(params)), guider, sampler,
                  g.out(g.add("ManualSigmas", sigmas=LTX_SIGMAS_STAGE1)), g.out(av), "ltx-s1")
    sep = g.add("LTXVSeparateAVLatent", av_latent=g.out(s1))
    # Stage 2: 2x latent upscale, re-anchor the first frame, refine.
    v = g.add("LTXVLatentUpsampler", samples=g.out(sep, 0), upscale_model=g.out(ups), vae=g.out(vae))
    if first:
        v = g.add("LTXVImgToVideoInplace", vae=g.out(vae), image=g.out(first), latent=g.out(v), strength=1.0,
                  bypass=False)
    av = g.add("LTXVConcatAVLatent", video_latent=g.out(v), audio_latent=g.out(sep, 1))
    s2 = g.sample(g.add("RandomNoise", noise_seed=42), guider, sampler,
                  g.out(g.add("ManualSigmas", sigmas=LTX_SIGMAS_STAGE2)), g.out(av), "ltx-s2")
    sep = g.add("LTXVSeparateAVLatent", av_latent=g.out(s2))
    return g.out(sep, 0), g.out(sep, 1)


def _ltx_first_last(g, inputs, unet, vae, cond, audio, w, h, frames, params):
    """video_ltx2_5_flf2v: one stage at full size. Returns (video latent link, audio latent link)."""
    def frame(name):
        img = g.add("LoadImage", image=inputs[name])
        img = g.add("ImageScale", image=g.out(img), upscale_method="nearest-exact", width=w, height=h, crop="center")
        return g.add("LTXVPreprocess", image=g.out(img), img_compression=18)

    first, last = frame("first_frame"), frame("last_frame")
    v = g.add("EmptyLTXVLatentVideo", width=w, height=h, length=frames, batch_size=1)
    a = g.add("LTXVAddGuide", positive=g.out(cond, 0), negative=g.out(cond, 1), vae=g.out(vae), latent=g.out(v),
              image=g.out(first), frame_idx=0, strength=float(params.get("frame_strength", 0.7)))
    b = g.add("LTXVAddGuide", positive=g.out(a, 0), negative=g.out(a, 1), vae=g.out(vae), latent=g.out(a, 2),
              image=g.out(last), frame_idx=-1, strength=float(params.get("frame_strength", 0.7)))
    guider = g.add("LTXVDualCFGGuider", model=g.out(unet), positive=g.out(b, 0), negative=g.out(b, 1),
                   video_cfg=1.0, audio_cfg=1.0)
    av = g.add("LTXVConcatAVLatent", video_latent=g.out(b, 2), audio_latent=g.out(audio))
    s = g.sample(g.add("RandomNoise", noise_seed=_seed(params)), guider,
                 g.add("SamplerEulerAncestral", eta=0.0, s_noise=1.0),
                 g.out(g.add("ManualSigmas", sigmas=LTX_SIGMAS_STAGE1)), g.out(av), "ltx-flf")
    sep = g.add("LTXVSeparateAVLatent", av_latent=g.out(s))
    crop = g.add("LTXVCropGuides", positive=g.out(b, 0), negative=g.out(b, 1), latent=g.out(sep, 0))
    return g.out(crop, 2), g.out(sep, 1)


# ---------------------------------------------------------------- upscaling
def _scale(params, default):
    s = float(params.get("scale", default))
    if not 1 < s <= 4:
        raise WorkflowError("scale must be > 1 and <= 4")
    return s


def _seedvr2(g, images, model, scale, params, video):
    """SeedVR2 one-step restoration, as in the official templates: lanczos resize to the target, encode,
    one euler step at denoise 1, decode, then colour-match to the resized input. Video goes through
    SeedVR2TemporalChunk, so each chunk is sampled (and checkpointed) on its own."""
    unet = g.add("UNETLoader", unet_name=SEEDVR2[model], weight_dtype="default")
    vae = g.add("VAELoader", vae_name=SEEDVR2_VAE)
    resized = g.add("ImageScaleBy", image=images, upscale_method="lanczos", scale_by=scale)
    pre = g.add("SeedVR2Preprocess", resized_images=g.out(resized))
    temporal = 64 if video else 4096
    lat = g.out(g.add("VAEEncodeTiled", pixels=g.out(pre), vae=g.out(vae), tile_size=512, overlap=128,
                      temporal_size=temporal, temporal_overlap=8))
    if video:
        chunk = g.add("SeedVR2TemporalChunk", latent=lat, temporal_overlap=0, chunking_mode="auto")
        lat = g.out(chunk, 0)
    cond = g.add("SeedVR2Conditioning", model=g.out(unet), vae_conditioning=lat)
    # The template's KSampler(1 step, CFG 1, euler, simple), in pieces so it can pause.
    out = g.out(g.sample(g.add("RandomNoise", noise_seed=_seed(params)),
                         g.add("CFGGuider", model=g.out(unet), positive=g.out(cond, 0), negative=g.out(cond, 1), cfg=1.0),
                         g.add("KSamplerSelect", sampler_name="euler"),
                         g.out(g.add("BasicScheduler", model=g.out(unet), scheduler="simple", steps=1, denoise=1.0)),
                         lat, "seedvr2"))
    if video:
        out = g.out(g.add("SeedVR2TemporalMerge", latents=out, temporal_overlap=g.out(chunk, 1)))
    img = g.add("VAEDecodeTiled", samples=out, vae=g.out(vae), tile_size=512, overlap=128,
                temporal_size=temporal, temporal_overlap=8)
    # The templates use "none"; "lab" (the node default) keeps colours closer to the input.
    return g.add("SeedVR2PostProcessing", images=g.out(img), original_resized_images=g.out(resized),
                 color_correction_method=params.get("color_correction", "lab"))


def build_upscale_image(params, inputs, ctx):
    """Upscale one image ("scale" > 1 and <= 4, default 2):
      seedvr2-7b / seedvr2-7b-sharp / seedvr2-3b   one-step diffusion restoration (best general quality)
      flux2-refine   anime-safe: RealESRGAN-anime to the target size, then FLUX.2 Turbo img2img at low denoise
                     (default 0.35), so lines, shading and the character stay FLUX.2's own. Optional
                     "prompt" (describe the image) and "loras" (the character LoRA)
      esrgan-anime   RealESRGAN x4plus anime 6B, then resized to "scale". Instant, no new detail"""
    model = params["model"]
    if not inputs.get("image"):
        raise WorkflowError("image is required")
    scale = _scale(params, 2)
    g = Graph(ctx)
    src = g.add("LoadImage", image=inputs["image"])
    if model in SEEDVR2:
        img = _seedvr2(g, g.out(src), model, scale, params, video=False)
    else:
        gan = g.add("UpscaleModelLoader", model_name=ESRGAN_ANIME)
        img = g.add("ImageUpscaleWithModel", upscale_model=g.out(gan), image=g.out(src))
        img = g.add("ImageScaleBy", image=g.out(img), upscale_method="lanczos", scale_by=scale / 4)
        if model == "flux2-refine":
            img = _flux2_refine(g, img, params)
    g.add("SaveImage", images=g.out(img), filename_prefix=g.prefix("upscale"))
    return g.nodes


def _flux2_refine(g, img, params):
    denoise = float(params.get("denoise", 0.35))
    if not 0.05 <= denoise <= 0.8:
        raise WorkflowError("denoise must be 0.05..0.8")
    m, clip, vae = _flux2_model(g, params, turbo=True)
    cond = g.add("CLIPTextEncode", text=params.get("prompt", ""), clip=g.out(clip))
    cond = g.add("FluxGuidance", conditioning=g.out(cond), guidance=float(params.get("guidance", 4.0)))
    size = g.add("GetImageSize", image=g.out(img))
    lat = g.add("VAEEncode", pixels=g.out(img), vae=g.out(vae))
    sigmas = g.add("Flux2Scheduler", steps=int(params.get("steps", 12)), width=g.out(size, 0), height=g.out(size, 1))
    sigmas = g.add("SplitSigmasDenoise", sigmas=g.out(sigmas), denoise=denoise)
    # SplitSigmasDenoise's second output is the low-noise tail: img2img from the encoded image.
    out = g.sample(g.add("RandomNoise", noise_seed=_seed(params)),
                   g.add("BasicGuider", model=g.out(m), conditioning=g.out(cond)),
                   g.add("KSamplerSelect", sampler_name="euler"), g.out(sigmas, 1), g.out(lat), "refine")
    return g.add("VAEDecode", samples=g.out(out), vae=g.out(vae))


def build_upscale_video(params, inputs, ctx):
    """Upscale a video with SeedVR2 (default seedvr2-3b; seedvr2-7b for hero shots), keeping its audio
    track (if any) and frame rate. "scale" defaults to 2. Use it past LTX-2.5's own 720p (720p -> 1440p)."""
    if not inputs.get("video"):
        raise WorkflowError("video is required")
    scale = _scale(params, 2)
    g = Graph(ctx)
    parts = g.add("GetVideoComponents", video=g.out(g.add("LoadVideo", file=inputs["video"])))
    img = _seedvr2(g, g.out(parts, 0), params["model"], scale, params, video=True)
    v = g.add("CreateVideo", images=g.out(img), fps=g.out(parts, 2), audio=g.out(parts, 1), bit_depth=8)
    g.add("SaveVideo", video=g.out(v), filename_prefix=g.prefix("upscale"), format="auto", **{"format.codec": "auto"})
    return g.nodes
