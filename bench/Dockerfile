# Benchmark runner. It executes model-written code for grading, so it only ever runs in this container.
FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
COPY sitecustomize.py /usr/local/lib/python3.12/site-packages/sitecustomize.py
COPY tonyd2wild /bench
COPY planner /bench/planner
COPY run.sh /usr/local/bin/ks-bench
RUN chmod +x /usr/local/bin/ks-bench && mkdir -p /bench/results && chmod 777 /bench/results
WORKDIR /bench
ENTRYPOINT ["ks-bench"]
