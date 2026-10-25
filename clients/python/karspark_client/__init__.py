"""Python client for karspark. docs/integrating.md is the contract; this is a thin, typed wrapper over it.

    from karspark_client import Karspark
    ks = Karspark()                                   # KARSPARK_API_KEY, KARSPARK_BASE_URL
    ks.image("a lighthouse at dusk", size="1216x832").wait().save("out.png")
    print(ks.text("Plan 6 shots for a 30 s teaser about ..."))
"""
from __future__ import annotations

import base64
import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

import httpx

__all__ = ["Karspark", "Job", "KarsparkError", "JobFailed"]
__version__ = "0.1.0"

DEFAULT_BASE_URL = "http://100.101.249.74:8080"
FINAL = ("succeeded", "failed", "cancelled")


class KarsparkError(Exception):
    def __init__(self, status: int, message: str, body: Any = None):
        super().__init__(f"{status}: {message}")
        self.status, self.message, self.body = status, message, body


class JobFailed(KarsparkError):
    def __init__(self, job: "Job"):
        super().__init__(0, f"job {job.id} {job.state}: {job.error}", job.data)
        self.job = job


Input = str | bytes | os.PathLike  # file_id, karspark file URL, data: URL, local path, or raw bytes


class Karspark:
    """One client per project key. Thread-safe (httpx.Client). Retries connection errors and 429/502/503
    with backoff; job submissions carry an Idempotency-Key, so a retry never creates a duplicate."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None, *, timeout: float = 600,
                 retries: int = 5, http: httpx.Client | None = None):
        api_key = api_key or os.environ.get("KARSPARK_API_KEY")
        if not api_key:
            raise ValueError("pass api_key or set KARSPARK_API_KEY")
        self.base_url = (base_url or os.environ.get("KARSPARK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.retries = retries
        self._http = http or httpx.Client(timeout=httpx.Timeout(timeout, connect=15))
        self._headers = {"Authorization": f"Bearer {api_key}", "User-Agent": f"karspark-client/{__version__}"}

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ------------------------------------------------------------ low level
    def request(self, method: str, path: str, *, raw: bool = False, headers: dict | None = None, **kw) -> Any:
        url = path if path.startswith("http") else self.base_url + path
        h = {**self._headers, **(headers or {})}
        for attempt in range(self.retries + 1):
            try:
                r = self._http.request(method, url, headers=h, **kw)
            except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError, httpx.ConnectTimeout):
                if attempt == self.retries:
                    raise
                time.sleep(min(30, 2 ** attempt))
                continue
            if r.status_code in (429, 502, 503) and attempt < self.retries:
                time.sleep(float(r.headers.get("retry-after") or min(30, 2 ** attempt)))
                continue
            if r.status_code >= 400:
                try:
                    body = r.json()
                    msg = body.get("error", {}).get("message") or r.text
                except ValueError:
                    body, msg = None, r.text
                raise KarsparkError(r.status_code, msg, body)
            if raw:
                return r
            return r.json() if r.content else None
        raise AssertionError("unreachable")

    # ------------------------------------------------------------ files
    def upload(self, data: bytes | os.PathLike | str, name: str | None = None) -> str:
        """Upload bytes or a local file; returns a file_id usable as any image/video input."""
        if not isinstance(data, bytes):
            p = Path(data)
            data, name = p.read_bytes(), name or p.name
        r = self.request("POST", "/v1/files", files={"file": (name or "upload.bin", data)})
        return r["file_id"]

    def download(self, file: str | dict, dest: os.PathLike | str) -> Path:
        url = file["url"] if isinstance(file, dict) else (file if "/" in file else f"/v1/files/{file}/content")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.request("GET", url, raw=True).content)
        return dest

    def _input(self, v: Input) -> str:
        if isinstance(v, bytes):
            return self.upload(v)
        if isinstance(v, os.PathLike):
            return self.upload(v)
        if v.startswith(("file_", "/v1/files/", "data:", self.base_url)):
            return v
        if Path(v).exists():
            return self.upload(v)
        raise ValueError(f"input {v[:60]!r} is not a file_id, karspark URL, data: URL or existing path")

    # ------------------------------------------------------------ jobs
    def submit(self, type: str, *, priority: int | None = None, webhook: str | None = None,
               idempotency_key: str | None = None, **params) -> "Job":
        body = {"type": type, **{k: v for k, v in params.items() if v is not None}}
        if priority is not None:
            body["priority"] = priority
        if webhook:
            body["webhook"] = webhook
        for k in ("image", "video", "first_frame", "last_frame", "mask"):
            if k in body:
                body[k] = self._input(body[k])
        for k in ("reference_images", "images"):
            if k in body:
                body[k] = [self._input(v) for v in body[k]]
        key = idempotency_key or uuid.uuid4().hex
        return Job(self, self.request("POST", "/v1/jobs", json=body, headers={"Idempotency-Key": key}))

    def image(self, prompt: str, *, size: str | None = None, reference_images: Iterable[Input] | None = None,
              model: str | None = None, **kw) -> "Job":
        """FLUX.2 (Turbo LoRA, 12 steps). kw: loras, steps, seed, turbo, priority, webhook, idempotency_key."""
        refs = list(reference_images) if reference_images else None
        return self.submit("image", prompt=prompt, size=size, reference_images=refs, model=model, **kw)

    def layers(self, image: Input, *, layers: int = 4, prompt: str | None = None, **kw) -> "Job":
        return self.submit("layers", image=image, layers=layers, prompt=prompt, **kw)

    def video(self, prompt: str, *, first_frame: Input | None = None, last_frame: Input | None = None,
              duration: float = 5, size: str | None = None, audio: str = "none", **kw) -> "Job":
        """LTX-2.5. The frames choose text-to-video, image-to-video or first-to-last. Silent by default;
        audio="sfx" adds sound effects only (never music or voices)."""
        return self.submit("video", prompt=prompt, first_frame=first_frame, last_frame=last_frame,
                           duration=duration, size=size, audio=audio, **kw)

    def upscale_image(self, image: Input, *, model: str | None = None, scale: float = 2, **kw) -> "Job":
        return self.submit("upscale_image", image=image, model=model, scale=scale, **kw)

    def upscale_video(self, video: Input, *, model: str | None = None, scale: float = 2, **kw) -> "Job":
        return self.submit("upscale_video", video=video, model=model, scale=scale, **kw)

    def remove_background(self, image: Input, **kw) -> "Job":
        """BiRefNet cut-out. Outputs: mask (white = subject), cutout (RGBA)."""
        return self.submit("remove_background", image=image, **kw)

    def depth(self, image: Input, *, resolution: int | None = None, **kw) -> "Job":
        """Depth Anything 3. Outputs: depth (near = white), sky (white = sky)."""
        return self.submit("depth", image=image, resolution=resolution, **kw)

    def inpaint(self, image: Input, *, mask: Input | None = None, auto_mask: str | None = None,
                grow: int | None = None, **kw) -> "Job":
        """LaMa fill of mask (white = fill), or auto_mask="foreground" to remove the subject in one job."""
        return self.submit("inpaint", image=image, mask=mask, auto_mask=auto_mask, grow=grow, **kw)

    def text_job(self, prompt: str | None = None, *, messages: list | None = None,
                 images: Iterable[Input] | None = None, json_schema: dict | None = None, **kw) -> "Job":
        """Long text as a pausable job (pause keeps the partial text; resume continues it). images: the creative
        planner can see them. json_schema: the answer is JSON matching it (enforced while decoding)."""
        return self.submit("text", prompt=prompt, messages=messages, images=list(images) if images else None,
                           json_schema=json_schema, **kw)

    def job(self, job_id: str) -> "Job":
        return Job(self, self.request("GET", f"/v1/jobs/{job_id}"))

    def jobs(self, state: str | None = None, type: str | None = None, limit: int = 50, mine: bool = True) -> list["Job"]:
        params = {k: v for k, v in {"state": state, "type": type, "limit": limit, "mine": mine}.items() if v is not None}
        return [Job(self, j) for j in self.request("GET", "/v1/jobs", params=params)["data"]]

    def cancel_jobs(self, state: str | None = None, type: str | None = None) -> Any:
        return self.request("POST", "/v1/jobs/cancel", json={"state": state, "type": type})

    # ------------------------------------------------------------ text
    def text(self, prompt: str | None = None, *, messages: list | None = None, system: str | None = None,
             model: str = "text", max_tokens: int | None = None, priority: int | None = None, **kw) -> str:
        """One chat completion; returns the answer text. For streaming or tools, use the OpenAI SDK with
        base_url=<karspark>/v1 (see docs/integrating.md)."""
        msgs = list(messages or [])
        if system:
            msgs.insert(0, {"role": "system", "content": system})
        if prompt is not None:
            msgs.append({"role": "user", "content": prompt})
        body = {"model": model, "messages": msgs, **kw}
        if max_tokens:
            body["max_tokens"] = max_tokens
        h = {"X-Karspark-Priority": str(priority)} if priority is not None else None
        r = self.request("POST", "/v1/chat/completions", json=body, headers=h)
        return r["choices"][0]["message"].get("content") or ""

    # ------------------------------------------------------------ the box
    def status(self) -> dict:
        return self.request("GET", "/v1/status")

    def queue(self, engine: str | None = None) -> list:
        return self.request("GET", "/v1/queue", params={"engine": engine} if engine else None)["data"]

    def models(self) -> list:
        return self.request("GET", "/v1/models")["data"]

    def mode(self) -> dict:
        return self.request("GET", "/v1/mode")

    def request_mode(self, mode: str, hold_minutes: int | None = None) -> dict:
        body = {"mode": mode, **({"hold_minutes": hold_minutes} if hold_minutes else {})}
        return self.request("POST", "/v1/mode", json=body)

    def usage(self, since: float | None = None) -> dict:
        return self.request("GET", "/v1/usage", params={"since": since} if since else None)


class Job:
    """A snapshot of a job plus its controls. `refresh()` / `wait()` update it in place."""

    def __init__(self, ks: Karspark, data: dict):
        self._ks, self.data = ks, data

    def __getattr__(self, k):
        try:
            return self.data[k]
        except KeyError:
            raise AttributeError(k) from None

    def __repr__(self):
        p = self.data.get("progress") or {}
        step = f" {p.get('step')}/{p.get('total')}" if p.get("total") else ""
        return f"<Job {self.id} {self.kind} {self.state}{step} P{self.priority}>"

    @property
    def done(self) -> bool:
        return self.state in FINAL

    def _set(self, data):
        self.data = data
        return self

    def refresh(self) -> "Job":
        return self._set(self._ks.request("GET", f"/v1/jobs/{self.id}"))

    def wait(self, timeout: float | None = None, *, raise_on_failure: bool = True) -> "Job":
        """Long-poll until the job ends. Waits through system pauses (preemption, mode switch); returns
        early if a user pauses it. Raises JobFailed on failed/cancelled unless raise_on_failure=False."""
        end = None if timeout is None else time.monotonic() + timeout
        while not self.done:
            left = 50 if end is None else min(50, end - time.monotonic())
            if left <= 0:
                raise TimeoutError(f"job {self.id} still {self.state}")
            self._set(self._ks.request("GET", f"/v1/jobs/{self.id}/wait", params={"timeout": round(left, 1)}))
            if self.state == "paused" and self.data.get("paused_by") == "user":
                return self
        if raise_on_failure and self.state != "succeeded":
            raise JobFailed(self)
        return self

    def pause(self) -> "Job":
        return self._set(self._ks.request("POST", f"/v1/jobs/{self.id}/pause"))

    def resume(self) -> "Job":
        return self._set(self._ks.request("POST", f"/v1/jobs/{self.id}/resume"))

    def cancel(self) -> "Job":
        return self._set(self._ks.request("POST", f"/v1/jobs/{self.id}/cancel"))

    def set_priority(self, priority: int) -> "Job":
        return self._set(self._ks.request("PATCH", f"/v1/jobs/{self.id}", json={"priority": priority}))

    def events(self) -> list:
        return self._ks.request("GET", f"/v1/jobs/{self.id}/events")["data"]

    @property
    def text(self) -> str | None:
        """The answer of a finished text job."""
        outs = self.data.get("outputs") or []
        return outs[0].get("text") if outs else None

    def save(self, dest: os.PathLike | str = ".") -> list[Path]:
        """Download the outputs. `dest` is a directory (keeps output names) or, for a single output, a file."""
        if not self.done:
            self.wait()
        outs = [o for o in self.data.get("outputs") or [] if o.get("file_id")]
        dest = Path(dest)
        if len(outs) == 1 and dest.suffix and not dest.is_dir():
            return [self._ks.download(outs[0], dest)]
        return [self._ks.download(o, dest / o["name"]) for o in outs]

    def b64(self, i: int = 0) -> str:
        return base64.b64encode(self._ks.request("GET", self.data["outputs"][i]["url"], raw=True).content).decode()
