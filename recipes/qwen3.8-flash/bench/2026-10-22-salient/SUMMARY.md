| streams | per-stream decode (median) | total throughput (median batch) | prose per-stream | code per-stream | TTFT median | auto-score |
|---|---|---|---|---|---|---|
| 1 | 70.6 tok/s | 70.6 tok/s | 41.8 | 79.0 | 0.22 s | 0.824 |
| 2 | 48.2 tok/s | 65.5 tok/s | 32.7 | 50.3 | 0.3 s | 0.85 |
| 4 | 34.8 tok/s | 69.3 tok/s | 30.8 | 37.5 | 0.4 s | 0.854 |
| 6 | 27.4 tok/s | 87.5 tok/s | 20.4 | 29.9 | 0.47 s | 0.855 |
| 8 | 25.8 tok/s | 100.4 tok/s | 19.5 | 26.4 | 0.55 s | 0.859 |

Long-prompt needle test (cold prefill):
- run 1: prompt_tokens=14134 ttft=5.7s total=5.7s prefill~2479 tok/s finish=stop answer='PELICAN-7' OK
- run 1: prompt_tokens=56389 ttft=21.4s total=21.4s prefill~2641 tok/s finish=stop answer='PELICAN-7' OK
- run 1: prompt_tokens=112738 ttft=41.8s total=41.8s prefill~2698 tok/s finish=stop answer='PELICAN-7' OK
