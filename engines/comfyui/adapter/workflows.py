"""API-format ComfyUI graphs behind the adapter's OpenAI-style endpoints.

Each graph follows the official ComfyUI template that ships in this image (comfyui-workflow-templates
0.11.94), flattened (no subgraphs, switches or math nodes; Python does that) and pointed at our files:

  flux2-dev           image_flux2_fp8.json            text-to-image, or edit/compose from 1..N reference images
  qwen-image-layered  image_qwen_image_layered.json   image -> RGBA layers (or text -> layers)
  ltx-2.5             video_ltx2_5_{i2v,t2v}.json     two-stage distilled (half res, then 2x latent upscale), with audio
  seedvr2-*           utility_seedvr2_*_upscale_{image,video}.json   one-step upscaling, images and video

Check graphs offline with engines/comfyui/tools/validate_graphs.py (runs ComfyUI's own validation, no GPU).
"""
MODELS = ["flux2-dev", "ltx-2.5", "qwen-image-layered"]

# Files under ~/models (see ../extra_model_paths.yaml).
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
# Distilled two-stage schedule, verbatim from the template.
LTX_SIGMAS_STAGE1 = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
LTX_SIGMAS_STAGE2 = "0.85, 0.7250, 0.4219, 0.0"
# Template default minus "cartoon" (we make anime). With the distilled model's CFG 1 it has little effect.
LTX_NEGATIVE = "pc game, console game, video game, childish, ugly, blurry, distorted"

MAX_REFS = 6


class WorkflowError(Exception):
    pass


class Graph:
    """Tiny builder: add() returns the node id; out(id, slot) is a link."""

    def __init__(self):
        self.nodes = {}

    def add(self, class_type, **inputs):
        nid = str(len(self.nodes) + 1)
        self.nodes[nid] = {"class_type": class_type, "inputs": inputs}
        return nid

    @staticmethod
    def out(nid, slot=0):
        return [nid, slot]


def _size(req, default, multiple):
    s = req.get("size") or default
    try:
        w, h = (int(x) for x in str(s).lower().split("x"))
    except ValueError:
        raise WorkflowError(f"size must be WIDTHxHEIGHT, got {s!r}")
    if not (256 <= w <= 4096 and 256 <= h <= 4096):
        raise WorkflowError("size must be between 256 and 4096 on each side")
    return w // multiple * multiple, h // multiple * multiple


def _loras(g, model, req):
    """Character/style LoRAs from ~/models/image/loras: [{"name": "x.safetensors", "strength": 1.0}]."""
    for l in req.get("loras") or []:
        if not isinstance(l, dict) or not l.get("name"):
            raise WorkflowError('loras must be a list of {"name": ..., "strength": ...}')
        model = g.add("LoraLoaderModelOnly", model=g.out(model), lora_name=l["name"],
                      strength_model=float(l.get("strength", 1.0)))
    return model


# ---------------------------------------------------------------- images
def build_image(req, refs, seed):
    model = req.get("model")
    if model == "flux2-dev":
        return _flux2(req, refs, seed)
    if model == "qwen-image-layered":
        return _qwen_layered(req, refs, seed)
    raise WorkflowError(f"unknown image model {model!r}; use one of: flux2-dev, qwen-image-layered")


def _flux2(req, refs, seed):
    """FLUX.2 [dev]. Default = the Turbo LoRA at 8 steps (the setup that worked best for us);
    "turbo": false = the base model at 20 steps. Up to 6 reference images (character sheets, style, objects),
    chained as ReferenceLatent like the template. Without "size", a reference-image job takes the first
    reference's aspect ratio at 1 MP."""
    if not req.get("prompt"):
        raise WorkflowError("prompt is required")
    if len(refs) > MAX_REFS:
        raise WorkflowError(f"at most {MAX_REFS} reference images")
    turbo = req.get("turbo", True)
    steps = int(req.get("steps") or (8 if turbo else 20))
    g = Graph()
    unet = g.add("UNETLoader", unet_name=FLUX2["unet"], weight_dtype="default")
    clip = g.add("CLIPLoader", clip_name=FLUX2["clip"], type="flux2", device="default")
    vae = g.add("VAELoader", vae_name=FLUX2["vae"])
    m = unet
    if turbo:
        m = g.add("LoraLoaderModelOnly", model=g.out(m), lora_name=FLUX2["turbo_lora"], strength_model=1.0)
    m = _loras(g, m, req)
    cond = g.add("CLIPTextEncode", text=req["prompt"], clip=g.out(clip))
    cond = g.add("FluxGuidance", conditioning=g.out(cond), guidance=float(req.get("guidance", 4.0)))
    first_size = None
    for name in refs:
        img = g.add("LoadImage", image=name)
        img = g.add("ImageScaleToTotalPixels", image=g.out(img), upscale_method="area", megapixels=1.0,
                    resolution_steps=1)
        if first_size is None:
            first_size = g.add("GetImageSize", image=g.out(img))
        lat = g.add("VAEEncode", pixels=g.out(img), vae=g.out(vae))
        cond = g.add("ReferenceLatent", conditioning=g.out(cond), latent=g.out(lat))
    if first_size is not None and not req.get("size"):
        w, h = g.out(first_size, 0), g.out(first_size, 1)
    else:
        w, h = _size(req, "1024x1024", 16)
    latent = g.add("EmptyFlux2LatentImage", width=w, height=h, batch_size=1)
    sigmas = g.add("Flux2Scheduler", steps=steps, width=w, height=h)
    guider = g.add("BasicGuider", model=g.out(m), conditioning=g.out(cond))
    noise = g.add("RandomNoise", noise_seed=seed)
    sampler = g.add("KSamplerSelect", sampler_name="euler")
    out = g.add("SamplerCustomAdvanced", noise=g.out(noise), guider=g.out(guider), sampler=g.out(sampler),
                sigmas=g.out(sigmas), latent_image=g.out(latent))
    img = g.add("VAEDecode", samples=g.out(out), vae=g.out(vae))
    g.add("SaveImage", images=g.out(img), filename_prefix="flux2")
    return g.nodes


def _qwen_layered(req, refs, seed):
    """Qwen-Image-Layered: splits an image into `layers` RGBA layers (background first) for parallax.
    Pass the image as "image" (or the first reference image). The optional prompt describes the whole
    picture, including what is hidden behind foreground objects; it does not pick the layers.
    No image = text-to-layers at "size". Template settings: 20 steps, CFG 2.5, 640 px
    (the model card's original 50 steps / CFG 4.0 is ~2.5x slower); "resolution": 1024 for high-res."""
    layers = int(req.get("layers", 4))
    if not 1 <= layers <= 10:
        raise WorkflowError("layers must be 1..10")
    g = Graph()
    unet = g.add("UNETLoader", unet_name=QWEN_LAYERED["unet"], weight_dtype="default")
    m = g.add("ModelSamplingAuraFlow", model=g.out(unet), shift=1.0)
    clip = g.add("CLIPLoader", clip_name=QWEN_LAYERED["clip"], type="qwen_image", device="default")
    vae = g.add("VAELoader", vae_name=QWEN_LAYERED["vae"])
    pos = g.add("CLIPTextEncode", text=req.get("prompt", ""), clip=g.out(clip))
    neg = g.add("CLIPTextEncode", text=req.get("negative_prompt", ""), clip=g.out(clip))
    if refs:
        img = g.add("LoadImage", image=refs[0])
        img = g.add("ImageScaleToMaxDimension", image=g.out(img), upscale_method="lanczos",
                    largest_size=int(req.get("resolution", 640)))
        size = g.add("GetImageSize", image=g.out(img))
        w, h = g.out(size, 0), g.out(size, 1)
        lat = g.add("VAEEncode", pixels=g.out(img), vae=g.out(vae))
        pos = g.add("ReferenceLatent", conditioning=g.out(pos), latent=g.out(lat))
        neg = g.add("ReferenceLatent", conditioning=g.out(neg), latent=g.out(lat))
    else:
        if not req.get("prompt"):
            raise WorkflowError("give an image to split, or a prompt for text-to-layers")
        w, h = _size(req, "640x640", 16)
    latent = g.add("EmptyQwenImageLayeredLatentImage", width=w, height=h, layers=layers, batch_size=1)
    out = g.add("KSampler", model=g.out(m), seed=seed, steps=int(req.get("steps", 20)), cfg=float(req.get("cfg", 2.5)),
                sampler_name="euler", scheduler="simple", positive=g.out(pos), negative=g.out(neg),
                latent_image=g.out(latent), denoise=1.0)
    # Frame 0 is the composite; keep frames 1.. (the layers) as a batch of RGBA images.
    out = g.add("LatentCut", samples=g.out(out), dim="t", index=1, amount=16384)
    out = g.add("LatentCutToBatch", samples=g.out(out), dim="t", slice_size=1)
    img = g.add("VAEDecode", samples=g.out(out), vae=g.out(vae))
    g.add("SaveImage", images=g.out(img), filename_prefix="layers")
    return g.nodes


# ---------------------------------------------------------------- video
def build_video(req, image, seed):
    """LTX-2.5 distilled, two stages as in the template: 8 steps at half resolution, 2x latent upscale,
    3 refinement steps at full resolution, then video + audio decode into one MP4.
    With "image" it animates that first frame (i2v); without, text-to-video.
    "size" must be a multiple of 64 (default 1280x720), "duration" in seconds (default 5), "fps" (default 24).
    The template's optional prompt enhancer (gemma4 e2b) is not downloaded, so prompts go in as written."""
    if req.get("model", "ltx-2.5") != "ltx-2.5":
        raise WorkflowError("unknown video model; use ltx-2.5")
    if not req.get("prompt"):
        raise WorkflowError("prompt is required")
    w, h = _size(req, "1280x720", 64)
    fps = int(req.get("fps", 24))
    duration = float(req.get("duration", 5))
    if not 1 <= duration <= 20:
        raise WorkflowError("duration must be 1..20 seconds")
    # LTX wants 8k+1 frames.
    frames = int(round(duration * fps / 8)) * 8 + 1
    g = Graph()
    unet = g.add("UNETLoader", unet_name=LTX25["unet"], weight_dtype="default")
    clip = g.add("CLIPLoader", clip_name=LTX25["clip"], type="ltxv", device="default")
    vae = g.add("VAELoader", vae_name=LTX25["vae"])
    avae = g.add("VAELoader", vae_name=LTX25["audio_vae"])
    ups = g.add("LatentUpscaleModelLoader", model_name=LTX25["upscaler"])
    pos = g.add("CLIPTextEncode", text=req["prompt"], clip=g.out(clip))
    neg = g.add("CLIPTextEncode", text=req.get("negative_prompt", LTX_NEGATIVE), clip=g.out(clip))
    cond = g.add("LTXVConditioning", positive=g.out(pos), negative=g.out(neg), frame_rate=float(fps))
    guider = g.add("LTXVDualCFGGuider", model=g.out(unet), positive=g.out(cond, 0), negative=g.out(cond, 1),
                   video_cfg=1.0, audio_cfg=1.0)
    sampler = g.add("KSamplerSelect", sampler_name="euler_ancestral")

    first = None
    if image:
        first = g.add("LoadImage", image=image)
        first = g.add("ImageScaleToMaxDimension", image=g.out(first), upscale_method="lanczos", largest_size=1536)
        first = g.add("LTXVPreprocess", image=g.out(first), img_compression=18)

    # Stage 1: half resolution.
    v = g.add("EmptyLTXVLatentVideo", width=w // 2, height=h // 2, length=frames, batch_size=1)
    if first:
        v = g.add("LTXVImgToVideoInplace", vae=g.out(vae), image=g.out(first), latent=g.out(v), strength=0.7,
                  bypass=False)
    a = g.add("LTXVEmptyLatentAudio", frames_number=frames, frame_rate=fps, batch_size=1, audio_vae=g.out(avae))
    av = g.add("LTXVConcatAVLatent", video_latent=g.out(v), audio_latent=g.out(a))
    s1 = g.add("SamplerCustomAdvanced", noise=g.out(g.add("RandomNoise", noise_seed=seed)), guider=g.out(guider),
               sampler=g.out(sampler), sigmas=g.out(g.add("ManualSigmas", sigmas=LTX_SIGMAS_STAGE1)),
               latent_image=g.out(av))
    sep = g.add("LTXVSeparateAVLatent", av_latent=g.out(s1))

    # Stage 2: 2x latent upscale, re-anchor the first frame, refine.
    v = g.add("LTXVLatentUpsampler", samples=g.out(sep, 0), upscale_model=g.out(ups), vae=g.out(vae))
    if first:
        v = g.add("LTXVImgToVideoInplace", vae=g.out(vae), image=g.out(first), latent=g.out(v), strength=1.0,
                  bypass=False)
    av = g.add("LTXVConcatAVLatent", video_latent=g.out(v), audio_latent=g.out(sep, 1))
    s2 = g.add("SamplerCustomAdvanced", noise=g.out(g.add("RandomNoise", noise_seed=42)), guider=g.out(guider),
               sampler=g.out(sampler), sigmas=g.out(g.add("ManualSigmas", sigmas=LTX_SIGMAS_STAGE2)),
               latent_image=g.out(av))
    sep = g.add("LTXVSeparateAVLatent", av_latent=g.out(s2))

    frames_img = g.add("VAEDecodeTiled", samples=g.out(sep, 0), vae=g.out(vae), tile_size=512, overlap=64,
                       temporal_size=64, temporal_overlap=16)
    audio = g.add("LTXVAudioVAEDecode", samples=g.out(sep, 1), audio_vae=g.out(avae))
    video = g.add("CreateVideo", images=g.out(frames_img), fps=float(fps), audio=g.out(audio), bit_depth=8)
    g.add("SaveVideo", video=g.out(video), filename_prefix="video/ltx", format="auto", **{"format.codec": "auto"})
    return g.nodes


# ---------------------------------------------------------------- upscaling
# POST /v1/images/upscale and /v1/videos/upscale (adapter endpoints, reached via /upstream/comfyui_auto/...).
SEEDVR2 = {
    "seedvr2-7b": "seedvr2_7b_fp16.safetensors",              # best quality (images)
    "seedvr2-7b-sharp": "seedvr2_7b_sharp_int8_convrot.safetensors",  # more detail; watch faces
    "seedvr2-3b": "seedvr2_3b_fp16.safetensors",              # faster (video default)
}
SEEDVR2_VAE = "seedvr2_ema_vae_fp16.safetensors"
ESRGAN_ANIME = "RealESRGAN_x4plus_anime_6B.safetensors"
UPSCALE_MODELS = [*SEEDVR2, "flux2-refine", "esrgan-anime"]
VIDEO_UPSCALE_MODELS = list(SEEDVR2)


def _scale(req, default):
    s = float(req.get("scale", default))
    if not 1 < s <= 4:
        raise WorkflowError("scale must be > 1 and <= 4")
    return s


def _seedvr2(g, images, model, scale, req, temporal_size):
    """SeedVR2 one-step restoration, as in the official templates: lanczos resize to the target, encode,
    one euler step at denoise 1, decode, then colour-match to the resized input."""
    unet = g.add("UNETLoader", unet_name=SEEDVR2[model], weight_dtype="default")
    vae = g.add("VAELoader", vae_name=SEEDVR2_VAE)
    resized = g.add("ImageScaleBy", image=images, upscale_method="lanczos", scale_by=scale)
    pre = g.add("SeedVR2Preprocess", resized_images=g.out(resized))
    lat = g.add("VAEEncodeTiled", pixels=g.out(pre), vae=g.out(vae), tile_size=512, overlap=128,
                temporal_size=temporal_size, temporal_overlap=8)
    cond = g.add("SeedVR2Conditioning", model=g.out(unet), vae_conditioning=g.out(lat))
    out = g.add("KSampler", model=g.out(unet), seed=int(req.get("seed", 1)), steps=1, cfg=1.0, sampler_name="euler",
                scheduler="simple", positive=g.out(cond, 0), negative=g.out(cond, 1), latent_image=g.out(lat), denoise=1.0)
    img = g.add("VAEDecodeTiled", samples=g.out(out), vae=g.out(vae), tile_size=512, overlap=128,
                temporal_size=temporal_size, temporal_overlap=8)
    # The templates use "none"; "lab" (the node default) keeps colours closer to the input.
    return g.add("SeedVR2PostProcessing", images=g.out(img), original_resized_images=g.out(resized),
                 color_correction_method=req.get("color_correction", "lab"))


def build_upscale_image(req, image):
    """Upscale one image. Models:
      seedvr2-7b / seedvr2-7b-sharp / seedvr2-3b   one-step diffusion restoration (best general quality)
      flux2-refine   anime-safe: RealESRGAN-anime to the target size, then FLUX.2 Turbo img2img at low
                     denoise (default 0.35), so lines, shading and the character stay FLUX.2's own.
                     Optional "prompt" (describe the image) and "loras" (the character LoRA)
      esrgan-anime   RealESRGAN x4plus anime 6B, then resized to "scale". Instant, no new detail
    "scale" defaults to 2 (max 4)."""
    model = req.get("model", "seedvr2-7b")
    if not image:
        raise WorkflowError("image is required")
    scale = _scale(req, 2)
    g = Graph()
    src = g.add("LoadImage", image=image)
    if model in SEEDVR2:
        img = _seedvr2(g, g.out(src), model, scale, req, temporal_size=4096)
    elif model in ("esrgan-anime", "flux2-refine"):
        gan = g.add("UpscaleModelLoader", model_name=ESRGAN_ANIME)
        img = g.add("ImageUpscaleWithModel", upscale_model=g.out(gan), image=g.out(src))
        img = g.add("ImageScaleBy", image=g.out(img), upscale_method="lanczos", scale_by=scale / 4)
        if model == "flux2-refine":
            img = _flux2_refine(g, img, req)
    else:
        raise WorkflowError(f"unknown upscale model {model!r}; use one of: {', '.join(UPSCALE_MODELS)}")
    g.add("SaveImage", images=g.out(img), filename_prefix="upscale")
    return g.nodes


def _flux2_refine(g, img, req):
    denoise = float(req.get("denoise", 0.35))
    if not 0.05 <= denoise <= 0.8:
        raise WorkflowError("denoise must be 0.05..0.8")
    unet = g.add("UNETLoader", unet_name=FLUX2["unet"], weight_dtype="default")
    clip = g.add("CLIPLoader", clip_name=FLUX2["clip"], type="flux2", device="default")
    vae = g.add("VAELoader", vae_name=FLUX2["vae"])
    m = g.add("LoraLoaderModelOnly", model=g.out(unet), lora_name=FLUX2["turbo_lora"], strength_model=1.0)
    m = _loras(g, m, req)
    cond = g.add("CLIPTextEncode", text=req.get("prompt", ""), clip=g.out(clip))
    cond = g.add("FluxGuidance", conditioning=g.out(cond), guidance=float(req.get("guidance", 4.0)))
    size = g.add("GetImageSize", image=g.out(img))
    lat = g.add("VAEEncode", pixels=g.out(img), vae=g.out(vae))
    sigmas = g.add("Flux2Scheduler", steps=int(req.get("steps", 8)), width=g.out(size, 0), height=g.out(size, 1))
    sigmas = g.add("SplitSigmasDenoise", sigmas=g.out(sigmas), denoise=denoise)
    out = g.add("SamplerCustomAdvanced", noise=g.out(g.add("RandomNoise", noise_seed=int(req.get("seed", 1)))),
                guider=g.out(g.add("BasicGuider", model=g.out(m), conditioning=g.out(cond))),
                sampler=g.out(g.add("KSamplerSelect", sampler_name="euler")), sigmas=g.out(sigmas, 1),
                latent_image=g.out(lat))
    return g.add("VAEDecode", samples=g.out(out), vae=g.out(vae))


def build_upscale_video(req, video):
    """Upscale a video with SeedVR2 (default seedvr2-3b; seedvr2-7b for hero shots), keeping its audio and
    frame rate. "scale" defaults to 2. Use it beyond LTX-2.5's own 720p output (e.g. 720p -> 1440p)."""
    model = req.get("model", "seedvr2-3b")
    if model not in SEEDVR2:
        raise WorkflowError(f"unknown video upscale model {model!r}; use one of: {', '.join(VIDEO_UPSCALE_MODELS)}")
    if not video:
        raise WorkflowError("video is required")
    scale = _scale(req, 2)
    g = Graph()
    parts = g.add("GetVideoComponents", video=g.out(g.add("LoadVideo", file=video)))
    img = _seedvr2(g, g.out(parts, 0), model, scale, req, temporal_size=64)
    v = g.add("CreateVideo", images=g.out(img), fps=g.out(parts, 2), audio=g.out(parts, 1), bit_depth=8)
    g.add("SaveVideo", video=g.out(v), filename_prefix="video/upscale", format="auto", **{"format.codec": "auto"})
    return g.nodes
