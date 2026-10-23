#!/usr/bin/env python3
"""Offline check of the adapter's graphs with ComfyUI's own validator: node types, input names and types,
links, and that every model file name resolves under ~/models. Loads no weights and never touches the GPU,
so it is safe to run while another model owns the Spark.

  scripts/validate-comfy-graphs.sh   (copies the working tree to the Spark and runs this in the image)
"""
import asyncio
import os
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
os.makedirs("/tmp/comfy-in", exist_ok=True)
folder_paths.set_input_directory("/tmp/comfy-in")

import execution  # noqa: E402
import nodes  # noqa: E402
import workflows  # noqa: E402
from PIL import Image  # noqa: E402

for name in ("ref1.png", "ref2.png"):
    Image.new("RGB", (832, 1216), "white").save(f"/tmp/comfy-in/{name}")

CASES = [
    ("flux2 text-to-image", "image", {"model": "flux2-dev", "prompt": "a cat", "size": "1024x1536"}, []),
    ("flux2 2 refs (turbo)", "image", {"model": "flux2-dev", "prompt": "the girl from image 1 in the outfit from image 2"},
     ["ref1.png", "ref2.png"]),
    ("flux2 base 20 steps", "image", {"model": "flux2-dev", "prompt": "a cat", "turbo": False}, ["ref1.png"]),
    ("qwen image->layers", "image", {"model": "qwen-image-layered", "layers": 4}, ["ref1.png"]),
    ("qwen text->layers", "image", {"model": "qwen-image-layered", "prompt": "a cafe", "size": "640x640"}, []),
    ("ltx-2.5 i2v", "video", {"prompt": "she turns and smiles", "duration": 5}, "ref1.png"),
    ("ltx-2.5 t2v", "video", {"prompt": "a train crossing a bridge at dusk", "size": "1280x704"}, None),
]


async def main():
    await nodes.init_extra_nodes(init_custom_nodes=False, init_api_nodes=False)
    bad = 0
    for label, kind, req, inp in CASES:
        graph = workflows.build_image(req, inp, 1) if kind == "image" else workflows.build_video(req, inp, 1)
        ok, err, outputs, node_errors = (await execution.validate_prompt("check", graph, None))[:4]
        print(f"{'OK  ' if ok else 'FAIL'} {label:<24} {len(graph)} nodes")
        if not ok:
            bad += 1
            print("     ", err)
            for nid, e in node_errors.items():
                print("     ", nid, graph[nid]["class_type"], e.get("errors"))
    sys.exit(1 if bad else 0)


asyncio.run(main())
