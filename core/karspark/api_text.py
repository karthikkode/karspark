"""Text: the OpenAI and Anthropic APIs, proxied to whichever engine serves the model in the current mode.

  POST /v1/chat/completions, /v1/completions, /v1/responses   (OpenAI SDKs, opencode)
  POST /v1/messages                                             (Anthropic SDKs, Claude Code style agents)

`model: "text"` means the text model of the current mode and never waits for a switch. Naming a model
loaded in another mode waits for that mode (coding models: coding keys only, P0). Priority comes from the
`X-Karspark-Priority` header (or a `priority` field), capped by the key's tier.

Streams start at once: while a request waits for a slot or a mode switch, and whenever the engine is
silent for `keepalive_s` (tool-call parsers buffer), the client gets keepalive pings (an SSE comment for
OpenAI, a `ping` event for Anthropic), so no client times out. A client that disconnects frees its slot
and the engine aborts the request.

Core adds no `max_tokens`: a request runs until the model stops or its context is full. A stream that
starts repeating itself is ended with an error event (loopguard.py).
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .api_common import err, log_info, principal
from .jobs import JobError
from .loopguard import LoopGuard
from .service import parse_priority

router = APIRouter()
LOG_TEXT_CAP = 200_000


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, p=Depends(principal)):
    return await proxy(request, p, "/v1/chat/completions", "openai")


@router.post("/v1/completions")
async def completions(request: Request, p=Depends(principal)):
    return await proxy(request, p, "/v1/completions", "openai")


@router.post("/v1/responses")
async def responses(request: Request, p=Depends(principal)):
    return await proxy(request, p, "/v1/responses", "openai")


@router.post("/v1/messages")
async def messages(request: Request, p=Depends(principal)):
    return await proxy(request, p, "/v1/messages", "anthropic")


@router.post("/v1/messages/count_tokens")
async def count_tokens(request: Request, p=Depends(principal)):
    return await proxy(request, p, "/v1/messages/count_tokens", "anthropic")


def _ping(flavor):
    return b"event: ping\ndata: {\"type\": \"ping\"}\n\n" if flavor == "anthropic" else b": keepalive\n\n"


def _sse_error(flavor, message, code=503):
    if flavor == "anthropic":
        return ("event: error\ndata: " + json.dumps({"type": "error", "error": {"type": "overloaded_error" if code == 503
                                                                                 else "api_error", "message": message}})
                + "\n\n").encode()
    return ("data: " + json.dumps({"error": {"message": message, "type": "server_error", "code": code}}) + "\n\n").encode()


async def proxy(request: Request, p, path, flavor):
    k = request.app.state.k
    info = log_info(request)
    try:
        body = await request.json()
    except ValueError:
        return err(400, "body must be JSON")
    info["request_body"] = json.dumps(body, ensure_ascii=False)[:1 << 20]
    try:
        model, coding = k.sched.resolve_text_model(p, body.get("model") or "text")
        prio_req = parse_priority(request.headers.get("x-karspark-priority") or body.pop("priority", None))
    except JobError as e:
        return err(e.status, str(e))
    body.pop("priority", None)
    prio = 0 if coding else p.effective_priority(prio_req)
    info.update(model=model, priority_req=prio_req, priority=prio, stream=int(bool(body.get("stream"))))
    sid = "str_" + uuid.uuid4().hex[:12]
    t0 = time.perf_counter()
    if not body.get("stream"):
        return await _unary(request, k, p, path, flavor, body, model, prio, coding, sid, info, t0)
    return StreamingResponse(_stream(request, k, p, path, flavor, body, model, prio, coding, sid, info, t0),
                             media_type="text/event-stream", headers={"Cache-Control": "no-cache",
                                                                      "X-Karspark-Priority": f"P{prio}"})


def _prepare(body, eng, path):
    body = dict(body)   # no max_tokens added: the model runs until it stops or the context is full
    body["model"] = eng.served_name or body.get("model")
    return body


async def _unary(request, k, p, path, flavor, body, model, prio, coding, sid, info, t0):
    eng, s = await k.sched.acquire_text(p, model, prio, sid, coding)
    info["queue_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    try:
        t1 = time.perf_counter()
        r = await k.http.post(eng.base_url + path, json=_prepare(body, eng, path),
                              timeout=httpx.Timeout(30, read=1800))
        info["gen_ms"] = round((time.perf_counter() - t1) * 1000, 1)
    except httpx.TimeoutException:
        return err(504, "no answer within 30 min; use stream=true or a text job (POST /v1/jobs) for long output",
                   "server_error")
    except httpx.HTTPError as e:
        return err(502, f"text engine error: {e!r}", "server_error")
    finally:
        k.sched.release_text(s)
    try:
        data = r.json()
    except ValueError:
        return err(502, f"text engine returned {r.status_code}: {r.text[:500]}", "server_error")
    usage = data.get("usage") or {}
    tin = usage.get("prompt_tokens", usage.get("input_tokens"))
    tout = usage.get("completion_tokens", usage.get("output_tokens"))
    info.update(tokens_in=tin, tokens_out=tout)
    k.ratelimit.charge_tokens(p, (tin or 0) + (tout or 0))
    if isinstance(data, dict) and "model" in data:
        data["model"] = model if model != "text" else data["model"]
    return JSONResponse(data, status_code=r.status_code, headers={"X-Karspark-Priority": f"P{prio}"})


async def _stream(request, k, p, path, flavor, body, model, prio, coding, sid, info, t0):
    keepalive = k.cfg.sched.keepalive_s
    acquire = asyncio.create_task(k.sched.acquire_text(p, model, prio, sid, coding))
    s = None
    reader = None
    text_parts, size = [], 0
    usage, chunks, ttft = {}, 0, None
    guard = LoopGuard()
    try:
        while not acquire.done():  # waiting for a slot or a mode switch: keep the client alive
            done, _ = await asyncio.wait({acquire}, timeout=keepalive)
            if not done:
                yield _ping(flavor)
        eng, s = acquire.result()
        info["queue_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        q: asyncio.Queue = asyncio.Queue(maxsize=256)

        async def read():
            try:
                async with k.http.stream("POST", eng.base_url + path, json=_prepare(body, eng, path),
                                         timeout=httpx.Timeout(30, read=1800)) as r:
                    if r.status_code != 200:
                        await q.put(("error", (r.status_code, (await r.aread()).decode(errors="replace")[:2000])))
                        return
                    async for line in r.aiter_lines():
                        await q.put(("line", line))
                await q.put(("end", None))
            except httpx.HTTPError as e:
                await q.put(("error", (502, repr(e))))

        reader = asyncio.create_task(read())
        t1 = time.perf_counter()
        while True:
            try:
                kind, val = await asyncio.wait_for(q.get(), keepalive)
            except asyncio.TimeoutError:
                yield _ping(flavor)
                continue
            if kind == "end":
                break
            if kind == "error":
                code, msg = val
                info["error"] = f"engine {code}: {msg[:500]}"
                yield _sse_error(flavor, f"text engine error ({code}): {msg}", code)
                break
            line = val
            yield (line + "\n").encode()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            try:
                ev = json.loads(line[6:])
            except ValueError:
                continue
            delta = _delta_text(ev)
            if delta:
                chunks += 1
                s.tokens = chunks
                if ttft is None:
                    ttft = time.perf_counter() - t1
                if size < LOG_TEXT_CAP:
                    text_parts.append(delta)
                    size += len(delta)
                if guard.feed(delta):
                    info["error"] = guard.reason()
                    yield _sse_error(flavor, guard.reason(), 500)
                    break           # the finally closes the upstream stream: vLLM aborts the request
            u = _usage(ev)
            if u:
                usage = {**usage, **{key: v for key, v in u.items() if v is not None}}
        info["gen_ms"] = round((time.perf_counter() - t1) * 1000, 1)
    except asyncio.CancelledError:
        info["error"] = "client disconnected"
        raise
    finally:
        if reader:
            reader.cancel()  # closes the upstream stream: vLLM aborts the request
        if not acquire.done():
            acquire.cancel()
        if s:
            k.sched.release_text(s)
        tin = usage.get("prompt_tokens", usage.get("input_tokens"))
        tout = usage.get("completion_tokens", usage.get("output_tokens")) or chunks
        info.update(tokens_in=tin, tokens_out=tout, ttft_ms=round(ttft * 1000, 1) if ttft is not None else None)
        info["response_body"] = json.dumps({"text": "".join(text_parts), "usage": usage}, ensure_ascii=False)
        k.ratelimit.charge_tokens(p, (tin or 0) + (tout or 0))


def _delta_text(ev):
    if ev.get("type") == "content_block_delta":  # anthropic
        d = ev.get("delta") or {}
        return d.get("text") or d.get("thinking") or d.get("partial_json")
    if ev.get("type", "").startswith("response.") and ev.get("delta"):  # openai responses
        return ev["delta"] if isinstance(ev["delta"], str) else None
    out = ""
    for ch in ev.get("choices") or []:
        d = ch.get("delta") or {}
        out += (d.get("content") or "") + (d.get("reasoning_content") or d.get("reasoning") or "") + (ch.get("text") or "")
        for tc in d.get("tool_calls") or []:
            out += (tc.get("function") or {}).get("arguments") or ""
    return out


def _usage(ev):
    if ev.get("usage"):
        return ev["usage"]
    if ev.get("type") == "message_delta" and ev.get("usage"):
        return ev["usage"]
    if ev.get("type") == "message_start":
        return (ev.get("message") or {}).get("usage")
    if ev.get("type") == "response.completed":
        return (ev.get("response") or {}).get("usage")
    return None
