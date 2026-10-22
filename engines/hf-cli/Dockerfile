# Model downloader. Runs as the host user so weights land owned by karthik.
FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
RUN pip install --no-cache-dir huggingface_hub==2.2.0 hf_xet==1.7.0
# Files over the plain-HTTP size limit (e.g. Flash-Next's 53.7 GB PLE table) must use Xet.
# High-performance mode is what reached the full line rate on the Spark's Wi-Fi.
# Set HF_HUB_DISABLE_XET=1 per run to force plain HTTP for smaller files if Xet ever stalls.
ENV HOME=/tmp HF_HUB_DISABLE_TELEMETRY=1 HF_XET_HIGH_PERFORMANCE=1
ENTRYPOINT ["hf"]
