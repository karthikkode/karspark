#!/usr/bin/env python3
"""Offline check of the adapter's graphs with ComfyUI's own validator: node types, input names and types,
links, and that every model file name resolves under ~/models. Loads no weights and never touches the GPU,
so it is safe to run while another model owns the Spark.

  scripts/validate-comfy-graphs.sh   (copies the working tree to the Spark and runs this in the image)
"""
import asyncio
import os
import subprocess
import sys

sys.path.insert(0, "/opt/comfyui")
# Appended, not prepended: the adapter has its own server.py, which must not shadow ComfyUI's.
sys.path.append(os.environ.get("ADAPTER_DIR", "/opt/adapter"))
sys.argv = [sys.argv[0], "--cpu", "--extra-model-paths-config", "/opt/comfyui/extra_model_paths.yaml"]

import comfy.options  # noqa: E402
comfy.options.enable_args_parsing()
from comfy.cli_args import args  # noqa: E402,F401  (parses the argv above)
import folder_paths  # noqa: E402
import utils.extra_config  # noqa: E402

utils.extra_config.load_extra_path_config("/opt/comfyui/extra_model_paths.yaml")
# The adapter's layout: ComfyUI's input folder is the jobs folder, inputs live in <job>/in/.
IN = "/tmp/jobs/j1/in"
os.makedirs(IN, exist_ok=True)
folder_paths.set_input_directory("/tmp/jobs")

import execution  # noqa: E402
import nodes  # noqa: E402
import workflows  # noqa: E402
from PIL import Image  # noqa: E402

sys.path.append(os.environ.get("NODES_PARENT", "/opt"))
import karspark_nodes  # noqa: E402

for name in ("ref1.png", "ref2.png"):
    Image.new("RGB", (832, 1216), "white").save(f"{IN}/{name}")
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360:d=1:r=24", "-f", "lavfi",
                "-i", "sine=d=1", "-shortest", f"{IN}/clip.mp4"], check=True)

R1, R2, CLIP = "j1/in/ref1.png", "j1/in/ref2.png", "j1/in/clip.mp4"
CASES = [
    ("flux2 text-to-image", "image", {"prompt": "a cat", "size": "1024x1536"}, {}),
    ("flux2 2 refs (turbo)", "image", {"prompt": "the girl from image 1 in the outfit from image 2"},
     {"reference_images": [R1, R2]}),
    ("flux2 base 20 steps", "image", {"prompt": "a cat", "turbo": False}, {"reference_images": [R1]}),
    ("flux3-klein text-to-image", "image", {"model": "flux3-klein", "prompt": "a cat", "size": "1280x704"}, {}),
    ("flux3-klein 2 refs", "image", {"model": "flux3-klein", "prompt": "the girl from image 1 in image 2"},
     {"reference_images": [R1, R2]}),
    ("qwen image->layers", "layers", {"layers": 4}, {"image": R1}),
    ("qwen text->layers", "layers", {"prompt": "a cafe", "size": "640x640"}, {}),
    ("ltx t2v (silent)", "video", {"prompt": "a train crossing a bridge at dusk", "size": "1280x704"}, {}),
    ("ltx i2v (sfx)", "video", {"prompt": "she turns and smiles", "audio": "sfx"}, {"first_frame": R1}),
    ("ltx first+last frame", "video", {"prompt": "she walks to the door"}, {"first_frame": R1, "last_frame": R2}),
    ("upscale seedvr2-7b", "upscale_image", {"scale": 4}, {"image": R1}),
    ("upscale seedvr2-7b-sharp", "upscale_image", {"model": "seedvr2-7b-sharp"}, {"image": R1}),
    ("upscale flux2-refine", "upscale_image", {"model": "flux2-refine", "prompt": "anime girl"}, {"image": R1}),
    ("upscale esrgan-anime", "upscale_image", {"model": "esrgan-anime", "scale": 4}, {"image": R1}),
    ("video upscale seedvr2-3b", "upscale_video", {}, {"video": CLIP}),
    ("video upscale seedvr2-7b", "upscale_video", {"model": "seedvr2-7b", "scale": 1.5}, {"video": CLIP}),
    ("video upscale esrgan-anime x3", "upscale_video", {"model": "esrgan-anime", "scale": 3}, {"video": CLIP}),
    ("video upscale esrgan-animevideo x4", "upscale_video", {"model": "esrgan-animevideo", "scale": 4}, {"video": CLIP}),
    ("remove background birefnet", "remove_background", {}, {"image": R1}),
    ("depth da3-mono-large", "depth", {"resolution": 504}, {"image": R1}),
    ("inpaint lama, mask image", "inpaint", {"grow": 8}, {"image": R1, "mask": R2}),
    ("inpaint lama, auto foreground", "inpaint", {"auto_mask": "foreground"}, {"image": R1}),
]
SPEECH_CASES = [   # (label, params, inputs, should be rejected)
    ("speech narrator", {"text": "Once upon a time."}, {}, False),
    ("speech own voice", {"text": "Hello.", "lang": "en"}, {"voice": "j1/in/voice.wav"}, False),
    ("speech no text", {"text": " "}, {}, True),
    ("speech unknown voice", {"text": "Hi.", "voice": "nobody"}, {}, True),
]
CTX = {"job_dir": "/tmp/jobs/j1", "run_id": "check", "out": "j1/out"}


async def main():
    await nodes.init_extra_nodes(init_custom_nodes=False, init_api_nodes=False)
    nodes.NODE_CLASS_MAPPINGS.update(karspark_nodes.NODE_CLASS_MAPPINGS)
    bad = 0
    for label, kind, params, inputs in CASES:
        graph = workflows.build(kind, {**params, "seed": 1}, inputs, CTX)
        ok, err, outputs, node_errors = (await execution.validate_prompt("check", graph, None))[:4]
        print(f"{'OK  ' if ok else 'FAIL'} {label:<24} {len(graph)} nodes")
        if not ok:
            bad += 1
            print("     ", err)
            for nid, e in node_errors.items():
                print("     ", nid, graph[nid]["class_type"], e.get("errors"))
    # speech is not a ComfyUI graph: check the spec the adapter hands to its IndexTTS worker
    for label, params, inputs, want_error in SPEECH_CASES:
        try:
            spec = workflows.build("speech", {**params, "seed": 1}, inputs, CTX)
            ok = not want_error and spec["speech"] and spec["text"] and spec["voice"].endswith(".wav")
        except workflows.WorkflowError:
            ok = want_error
        print(f"{'OK  ' if ok else 'FAIL'} {label:<24} spec")
        bad += not ok
    sys.exit(1 if bad else 0)


asyncio.run(main())
