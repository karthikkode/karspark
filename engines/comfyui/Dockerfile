# ComfyUI engine (FLUX.2, Qwen-Image-Layered, LTX-2.5, SeedVR2) + IndexTTS-2.5 speech + the karspark job adapter
# and pausable sampler nodes.
# CUDA comes from the PyTorch cu130 wheels; the driver from the NVIDIA container runtime.
FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 git \
 && rm -rf /var/lib/apt/lists/*
# torch 2.13 + cu130 is what ran FLUX.2 on this GB10 before the wipe. pip picks the matching vision/audio.
RUN pip install --no-cache-dir torch==2.13.0 torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130
# Triton compiles its CUDA helper with a C compiler at first use (torch 2.13 runs Triton ops in the text
# encoders). Its own layer after torch so the torch layer stays cached.
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && rm -rf /var/lib/apt/lists/*
# ComfyUI source at a pinned tag, fetched on the laptop by scripts/build-images.sh (GitHub crawls from the Spark).
COPY .dl/comfyui.tar.gz /tmp/comfyui.tar.gz
RUN mkdir /opt/comfyui && tar -xzf /tmp/comfyui.tar.gz -C /opt/comfyui --strip-components=1 && rm /tmp/comfyui.tar.gz \
 && pip install --no-cache-dir -r /opt/comfyui/requirements.txt \
 && pip freeze > /opt/pip-freeze.txt
# IndexTTS-2.5 (speech) in its own venv: it pins transformers 4.52 and ComfyUI runs 5.x. The venv sees the
# system site packages, so it reuses the torch 2.13 cu130 above. Sources at a pinned commit, fetched on the
# laptop like ComfyUI's. The import at the end fails the build if a dependency is missing.
COPY .dl/indextts.tar.gz /tmp/indextts.tar.gz
COPY tts/requirements.txt /opt/tts/requirements.txt
RUN mkdir /opt/indextts && tar -xzf /tmp/indextts.tar.gz -C /opt/indextts --strip-components=1 && rm /tmp/indextts.tar.gz \
 && python3 -m venv --system-site-packages /opt/tts/venv \
 && /opt/tts/venv/bin/pip install --no-cache-dir -r /opt/tts/requirements.txt \
 && cd /opt/indextts && PYTHONPATH=/opt/indextts /opt/tts/venv/bin/python -c "import indextts.infer_v2_5, wetext" \
 && /opt/tts/venv/bin/pip freeze > /opt/tts/pip-freeze.txt
COPY extra_model_paths.yaml /opt/comfyui/extra_model_paths.yaml
COPY adapter /opt/adapter
COPY tts/worker.py /opt/tts/worker.py
COPY karspark_nodes /opt/comfyui/custom_nodes/karspark_nodes
ENV PYTHONUNBUFFERED=1 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True HF_HUB_OFFLINE=1
WORKDIR /opt/comfyui
ENTRYPOINT ["python3", "/opt/adapter/server.py"]
