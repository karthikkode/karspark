"""Files: uploads (inputs for jobs) and job outputs, owned by a key, kept for the tier's retention.

Inputs can be given as a `file_id` from POST /v1/files, a karspark file URL, or base64 (data: URLs too).
Arbitrary remote URLs are never fetched (no SSRF).
"""
from __future__ import annotations

import base64
import binascii
import mimetypes
import os
import re
import shutil
import time
from pathlib import Path

from .db import DB
from .jobs import JobError, new_id

MAGIC = [(b"\x89PNG", "image/png", "png"), (b"\xff\xd8\xff", "image/jpeg", "jpg"), (b"RIFF", "image/webp", "webp"),
         (b"GIF8", "image/gif", "gif")]
VIDEO_MAGIC = [(4, b"ftyp", "video/mp4", "mp4"), (0, b"\x1a\x45\xdf\xa3", "video/webm", "webm")]
AUDIO_MAGIC = [(b"ID3", "audio/mpeg", "mp3"), (b"\xff\xfb", "audio/mpeg", "mp3"), (b"\xff\xf3", "audio/mpeg", "mp3"),
               (b"fLaC", "audio/flac", "flac"), (b"OggS", "audio/ogg", "ogg")]
FILE_URL = re.compile(r"(?:^|/)v1/files/(file_[0-9a-f]{16})(?:/content)?$")


def sniff(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"RIFF") and raw[8:12] == b"WAVE":
        return "audio/wav", "wav"
    for sig, mime, ext in MAGIC:
        if raw.startswith(sig):
            if sig == b"RIFF" and raw[8:12] != b"WEBP":
                continue
            return mime, ext
    for sig, mime, ext in AUDIO_MAGIC:
        if raw.startswith(sig):
            return mime, ext
    for off, sig, mime, ext in VIDEO_MAGIC:
        if raw[off:off + len(sig)] == sig:
            return mime, ext
    return "application/octet-stream", "bin"


class Files:
    def __init__(self, db: DB, data_dir: Path, clock=time.time):
        self.db, self.data, self.clock = db, data_dir, clock

    async def save(self, principal, name: str, raw: bytes, job_id=None, mime=None) -> dict:
        limit = principal.tier.max_upload_mb * 1024 * 1024
        if job_id is None and len(raw) > limit:
            raise JobError(f"file larger than your tier's {principal.tier.max_upload_mb} MB limit", 413)
        fid = new_id("file")
        sniffed, ext = sniff(raw[:64])
        mime = mime or (sniffed if sniffed != "application/octet-stream" else
                        mimetypes.guess_type(name)[0] or sniffed)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(name or f"file.{ext}"))[:100] or f"file.{ext}"
        rel = Path("files") / fid / safe
        (self.data / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.data / rel).write_bytes(raw)
        return await self._register(principal, fid, safe, rel, len(raw), mime, job_id)

    async def register_output(self, principal, job_id, path: Path) -> dict:
        """A job output already in the job folder: registered in place, no copy."""
        fid = new_id("file")
        mime = mimetypes.guess_type(path.name)[0] or sniff(path.read_bytes()[:64])[0]
        return await self._register(principal, fid, path.name, path.relative_to(self.data), path.stat().st_size,
                                    mime, job_id)

    async def _register(self, principal, fid, name, rel, size, mime, job_id):
        now = self.clock()
        row = {"id": fid, "key_id": principal.key_id, "job_id": job_id, "name": name, "path": str(rel), "size": size,
               "mime": mime, "created": now, "expires": now + principal.tier.retention_days * 86400}
        await self.db.insert("files", row)
        return row

    async def get(self, principal, fid) -> dict:
        f = await self.db.one("SELECT * FROM files WHERE id = ?", (fid,))
        if not f or (f["key_id"] != principal.key_id and not principal.is_owner):
            raise JobError("no such file", 404)
        return f

    def path(self, f) -> Path:
        return self.data / f["path"]

    async def resolve_input(self, principal, value, dest_dir: Path, stem: str, want="image") -> str:
        """Put one input into a job's in/ folder; returns the file name there."""
        if not isinstance(value, str) or not value:
            raise JobError(f"{stem}: expected a file_id, a karspark file URL or base64")
        m = FILE_URL.search(value) if "/" in value else None
        fid = m.group(1) if m else (value if re.fullmatch(r"file_[0-9a-f]{16}", value) else None)
        dest_dir.mkdir(parents=True, exist_ok=True)
        if fid:
            f = await self.get(principal, fid)
            ext = Path(f["name"]).suffix or ".bin"
            dst = dest_dir / f"{stem}{ext}"
            try:
                os.link(self.path(f), dst)
            except OSError:
                shutil.copyfile(self.path(f), dst)
            raw_head = dst.read_bytes()[:64]
        else:
            if value.startswith(("http://", "https://")):
                raise JobError(f"{stem}: remote URLs aren't fetched; upload with POST /v1/files and pass the file_id")
            b64 = value.split(",", 1)[1] if value.startswith("data:") else value
            try:
                raw = base64.b64decode(b64, validate=False)
            except (binascii.Error, ValueError):
                raise JobError(f"{stem}: not valid base64")
            if len(raw) > principal.tier.max_upload_mb * 1024 * 1024:
                raise JobError(f"{stem}: larger than your tier's upload limit", 413)
            raw_head = raw[:64]
            ext = "." + sniff(raw_head)[1]
            dst = dest_dir / f"{stem}{ext}"
            dst.write_bytes(raw)
        mime = sniff(raw_head)[0]
        if not mime.startswith(want + "/"):
            dst.unlink(missing_ok=True)
            raise JobError(f"{stem}: expected {want}, got {mime}")
        return dst.name

    async def expired(self):
        return await self.db.all("SELECT * FROM files WHERE expires < ?", (self.clock(),))

    async def delete(self, f):
        p = self.path(f)
        p.unlink(missing_ok=True)
        if p.parent.name == f["id"]:
            shutil.rmtree(p.parent, ignore_errors=True)
        await self.db.run("DELETE FROM files WHERE id = ?", (f["id"],))
