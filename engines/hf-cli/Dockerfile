# Model downloader. Runs as the host user so weights land owned by karthik.
FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
RUN pip install --no-cache-dir huggingface_hub==2.2.0
# Xet transfers stall on the Spark's Wi-Fi; plain HTTP resumes cleanly.
ENV HF_HUB_DISABLE_XET=1 HOME=/tmp HF_HUB_DISABLE_TELEMETRY=1
ENTRYPOINT ["hf"]
