# Modes

The box has 121 GiB of unified memory. Flash-Next alone takes about 100 GiB and about 6 min to load cold,
so models can't come and go per request. Instead the box runs one **mode**: a fixed set of engines that fit
together (each mode is checked against 115 GiB at deploy).

| mode | engines | text model | for |
|---|---|---|---|
| `coding` (default) | Flash-Next | `qwen3.8-flash` | the owner's coding agent. P0, never interrupted |
| `creative` | planner LLM (`qwen3.8-27b`) + ComfyUI | `qwen3.8-27b` | images, layers, video, upscaling, with an LLM always available to plan |
| `test` | Qwen3-0.6B | `qwen3-0.6b` | integration tests on the box |

`model="text"` always means the current mode's text model, so it never waits for a switch.

## Rules (`config/modes.yaml`, `policy.decide_mode`)

The controller re-evaluates on every scheduler tick, in this order:

1. **Coding request (P0).** A coding key (owner tier) asks for `qwen3.8-flash` in another mode. The box
   switches at once. Running media jobs **pause** (progress kept, `paused_by: system`, they resume by
   themselves later). Text streams get `stream_grace_s` (60 s) to finish.
2. **Coding hold.** Coding stays for `coding_hold_s` (30 min) after the last coding request. Nothing else
   moves the box out during the hold, except an owner's explicit request.
3. **Explicit request** (`POST /v1/mode {mode, hold_minutes?}`, MCP `request_mode`):
   - an **owner** key switches at once (`200` or `202 switching`);
   - any other key gets `202 pending {retry_after, reason}`. It applies by itself once there is no hold or
     pin, nothing is running in the current mode, and nothing is waiting there at the same or a more urgent
     priority than the requester's.
   - `hold_minutes` pins the new mode for that long.
   - Nobody but a coding key can request `coding` (`403`).
4. **Demand.** When the current mode has nothing running or waiting and work is waiting that only another
   mode can serve, the box switches to the mode with the most urgent waiting work (then the most of it).
   Demand never switches *into* coding; only coding requests do.
5. **Idle return.** With no work at all for `idle_return_s` (60 min), return to `default_mode`.

A switch stops the old mode's engines that the new mode doesn't use, waits for memory, then starts the
new ones. Engines that both modes share stay up. An engine that is still loading is waited for, never
restarted. Each switch is logged (`mode_switch_start`, `mode_switch_done` with its duration), and the
measured cost feeds `eta_s` and `GET /v1/mode`'s `switch_costs_s`.

## Seeing it

`GET /v1/mode` (current, since, switching, hold kind and end, pending request, switch costs, idle return),
`GET /v1/status`, MCP `status()`, and the `/ui` dashboard.

## Adding a mode

Add it to `config/modes.yaml` with its engines and text model. Engines in one mode run together, so their
`memory_gib` must sum to ≤ 115. `python -m karspark.config` (run by deploy) checks it.
