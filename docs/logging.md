# Logging

Every request is logged by `core/karspark/auditlog.py` into `~/data/logs.db` (SQLite WAL), through an async
batched writer, so logging never slows a request. Every response carries `X-Request-Id`, the row's key.

## Tables

| table | one row per | contents |
|---|---|---|
| `requests` | HTTP or MCP call | request id, time, key, project, tier, IP, method, path, MCP tool, model, mode, requested and effective priority, status, error, total / queue / TTFT / generation ms, tokens in/out, bytes in/out, job id, stream |
| `bodies` | request | full request and response bodies (≤ 1 MiB each, longer ones truncated and flagged). Uploads and media outputs are not stored inline, only file ids. Streams store the assembled text and usage |
| `job_events` | job state change | state, actor (key or `system`), reason (e.g. "preempted by job_…, P0"), data |
| `scheduler_events` | scheduler action | `mode_request`, `mode_switch_start`, `mode_switch_done` (with seconds), `preempt`, `rate_limited`, `engine_failed`, `key_created`, `key_imported`, `error` |
| `engine_events` | engine change | state changes, `adopted`, `loaded` (with load seconds), errors |
| `resource_samples` | 10 s | memory used and available, GPU utilization, temperature, power |

Not logged: `/health`, `/metrics`, `/ui/static`. API keys are never logged (only the key id).

## Retention

`config/scheduler.yaml`: bodies 30 days (`bodies_days`), everything else 365 days (`meta_days`). An hourly
maintenance task trims the logs and deletes expired files and job folders (per tier `retention_days`).

## Reading it

| who | how |
|---|---|
| any key | `GET /v1/usage?since=` (its own requests, tokens, jobs); `GET /v1/jobs/{id}/events` |
| owner | `GET /admin/logs/requests?key=&model=&since=&status=&path=&limit=`, `GET /admin/logs/requests/{id}` (with bodies), `GET /admin/logs/scheduler?since=` |
| owner | MCP `recent_requests`, `usage` per project |
| owner | `GET /metrics` (Prometheus: mode, engines, jobs by state and kind, streams, resources) |
| any key | `/ui` dashboard (asks for the key; owner keys see every project) |
| on the box | `sqlite3 ~/data/logs.db` (read-only: `sqlite3 -readonly`) |
