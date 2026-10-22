# Engine: hf-cli (model downloads)

Image `karspark/hf-cli:2.2.0`: `python:3.12-slim` (pinned by digest) + `huggingface_hub==2.2.0`, built from
the [Dockerfile](Dockerfile) by `scripts/build-images.sh`. Used only by `scripts/fetch-model.sh`. It runs as
uid 1000 with `~/models` mounted, so nothing gets installed on the host.

Always download with a pinned revision. `HF_HUB_DISABLE_XET=1` is baked in, because Xet transfers stall on
the Spark's Wi-Fi.
