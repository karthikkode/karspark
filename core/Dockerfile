# karspark-core: the only user-facing service (:8080). Python + the docker CLI (it starts engines as sibling
# containers through the host's Docker socket). No CUDA: nvidia-smi comes from the NVIDIA runtime
# (--gpus all, NVIDIA_DRIVER_CAPABILITIES=utility) for the resource samples only.
FROM docker:29.0-cli@sha256:858bb1e05af16840f5a55143c3e5e14073891fbc92f2c1f6f38dd9c5f2cca03c AS dockercli
FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
WORKDIR /app
# Exported from uv.lock with hashes (scripts/build-images.sh regenerates it).
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.txt && pip freeze > /app/pip-freeze.txt
COPY karspark /app/karspark
# The ComfyUI adapter's graph builder, used to validate media params at submit time (one source of truth).
COPY .dl/workflows.py /app/karspark/_workflows.py
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app
ENTRYPOINT ["python", "-m", "uvicorn", "karspark.app:app", "--host", "0.0.0.0", "--port", "8080", \
            "--no-access-log", "--timeout-graceful-shutdown", "20"]
