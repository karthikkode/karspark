"""API keys: each project gets its own key with a tier (config/tiers.yaml).

Keys are random 256-bit tokens (`ks-...`), so they are stored as HMAC-SHA256 with a server-side pepper
(KARSPARK_KEY_PEPPER in the env file) rather than a slow password hash; lookup is one indexed query.
The secret is shown once, at creation. The key that predates karspark-core is imported as-is.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass, field

from .config import Tier
from .db import DB

SCOPES = ("coding", "admin")  # extra powers on top of the tier; owner tier implies both


@dataclass(frozen=True)
class Principal:
    key_id: str
    name: str
    tier: Tier
    scopes: frozenset = field(default_factory=frozenset)

    @property
    def is_owner(self) -> bool:
        return self.tier.name == "owner"

    @property
    def can_code(self) -> bool:
        return self.tier.coding or "coding" in self.scopes

    @property
    def is_admin(self) -> bool:
        return self.is_owner or "admin" in self.scopes

    def effective_priority(self, requested: int | None) -> int:
        """Lower number = more urgent. Never above the tier's ceiling."""
        p = self.tier.default if requested is None else int(requested)
        return min(3, max(p, self.tier.ceiling))


class Keys:
    def __init__(self, db: DB, tiers: dict[str, Tier], pepper: str):
        if len(pepper) < 32:
            raise ValueError("KARSPARK_KEY_PEPPER must be at least 32 characters")
        self.db, self.tiers, self.pepper = db, tiers, pepper.encode()
        self._cache: dict[str, tuple[float, Principal | None]] = {}

    def _hash(self, key: str) -> str:
        return hmac.new(self.pepper, key.encode(), hashlib.sha256).hexdigest()

    async def create(self, name: str, tier: str, scopes=(), secret: str | None = None) -> tuple[str, str]:
        """Returns (key_id, secret). Pass `secret` to import an existing key."""
        if tier not in self.tiers:
            raise ValueError(f"unknown tier {tier}")
        bad = set(scopes) - set(SCOPES)
        if bad:
            raise ValueError(f"unknown scopes {sorted(bad)}")
        secret = secret or "ks-" + secrets.token_urlsafe(32)
        key_id = "key_" + uuid.uuid4().hex[:12]
        await self.db.insert("keys", {"key_id": key_id, "name": name, "tier": tier, "hash": self._hash(secret),
                                      "hint": secret[-4:], "scopes": list(scopes), "created": time.time()})
        return key_id, secret

    async def rotate(self, key_id: str) -> str:
        secret = "ks-" + secrets.token_urlsafe(32)
        await self.db.update("keys", "key_id", key_id, {"hash": self._hash(secret), "hint": secret[-4:]})
        self._cache.clear()
        return secret

    async def revoke(self, key_id: str):
        await self.db.update("keys", "key_id", key_id, {"revoked": time.time()})
        self._cache.clear()

    async def set_tier(self, key_id: str, tier: str):
        if tier not in self.tiers:
            raise ValueError(f"unknown tier {tier}")
        await self.db.update("keys", "key_id", key_id, {"tier": tier})
        self._cache.clear()

    async def list(self):
        return await self.db.all("SELECT key_id, name, tier, hint, scopes, created, revoked, last_used FROM keys "
                                 "ORDER BY created")

    async def get(self, key_id: str) -> Principal | None:
        r = await self.db.one("SELECT * FROM keys WHERE key_id = ? AND revoked IS NULL", (key_id,))
        return self._principal(r) if r else None

    def _principal(self, r) -> Principal | None:
        tier = self.tiers.get(r["tier"])
        if tier is None:
            return None
        return Principal(key_id=r["key_id"], name=r["name"], tier=tier, scopes=frozenset(r["scopes"]))

    async def verify(self, secret: str | None) -> Principal | None:
        if not secret:
            return None
        h = self._hash(secret)
        now = time.time()
        hit = self._cache.get(h)
        if hit and now - hit[0] < 30:
            return hit[1]
        r = await self.db.one("SELECT * FROM keys WHERE hash = ? AND revoked IS NULL", (h,))
        p = self._principal(r) if r else None
        if p:
            await self.db.update("keys", "key_id", p.key_id, {"last_used": now})
        self._cache[h] = (now, p)
        return p


def bearer(headers) -> str | None:
    """The key from `Authorization: Bearer`, `x-api-key` (Anthropic SDKs) or `api-key`."""
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return headers.get("x-api-key") or headers.get("api-key") or None
