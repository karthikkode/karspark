# Engine: hf-cli (model downloads)

Image `karspark/hf-cli:2.2.0-xet1.7.0`: `python:3.12-slim` (pinned by digest) + `huggingface_hub==2.2.0` + `hf_xet==1.7.0`, built from
the [Dockerfile](Dockerfile) by `scripts/build-images.sh`. Used only by `scripts/fetch-model.sh`. It runs as
uid 1000 with `~/models` mounted, so nothing gets installed on the host.

Always download with a pinned revision. Xet runs in high-performance mode. Hugging Face refuses plain-HTTP
downloads of very large files (Flash-Next's 53.7 GB PLE table), so Xet can't simply be switched off.
