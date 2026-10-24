# host/: the only files installed on the Spark outside Docker

Everything else runs in containers (docs/standard.md). These are host settings that a container can't make.
Each one is installed by the owner with sudo; agents hand over the commands and never install them.

| file | what | installed |
|---|---|---|
| `gb10-clock-cap.service` | `nvidia-smi -lgc 300,2200` at boot: GPU clock capped at 2200 MHz (stock 2418) | pending (2026-10-24) |

## gb10-clock-cap.service

Why: the box hard-powered-off twice in 34 hours (2026-10-23 16:05, 2026-10-24 01:49), the second time on
clip 16 of an LTX video run. It left no log: no panic, no Xid and no thermal warning. The 1 s telemetry's
last row read GPU 82 C, 70 W and 96 % util, with the hottest ACPI zone at 93 C (peak that night 97.5 C).
That matches the known GB10 failure under sustained full load. The community fix is this clock cap, which
cuts peak power and the time spent at or above 95 C. It costs about 5 % on LLM decode and 6-9 % on video.
It is a workaround: core also holds media until the box cools (`config/scheduler.yaml` `thermal`).

```bash
scp host/gb10-clock-cap.service spark:/tmp/
ssh -t spark 'sudo install -m 644 /tmp/gb10-clock-cap.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now gb10-clock-cap && rm /tmp/gb10-clock-cap.service'
ssh spark 'nvidia-smi -q -d CLOCK | grep -A2 -i "clocks event\|locked"; nvidia-smi --query-gpu=clocks.max.sm,clocks.sm --format=csv'
```

Revert: `ssh -t spark 'sudo systemctl disable --now gb10-clock-cap'` (ExecStop restores the stock clocks).
If it powers off again with the cap, step down to 2100, then 2000, and open an NVIDIA/MSI support case
with `~/dgx-spark-crash/collect-support-bundle.sh`.
