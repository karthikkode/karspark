# karspark gateway: llama-swap + the docker CLI. It starts backends as sibling containers through the
# host's Docker socket, so the gateway itself carries no engine, CUDA or Python.
# The llama-swap tarball is fetched on the laptop by scripts/build-images.sh (GitHub downloads crawl at
# ~30 KB/s from the Spark) and verified again here against the pinned checksum.
FROM docker:29.0-cli@sha256:858bb1e05af16840f5a55143c3e5e14073891fbc92f2c1f6f38dd9c5f2cca03c
COPY .dl/llama-swap.tar.gz /tmp/ls.tgz
RUN echo "3401097bb426f99a4de191cc599d53acaee0152ce63d6ca15fb0a6098ea80280  /tmp/ls.tgz" | sha256sum -c - \
 && tar -xzf /tmp/ls.tgz -C /usr/local/bin llama-swap && rm /tmp/ls.tgz && llama-swap -version
COPY ks-docker /usr/local/bin/ks-docker
RUN chmod +x /usr/local/bin/ks-docker
ENTRYPOINT ["llama-swap"]
