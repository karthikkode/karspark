import pytest

from karspark.db import DB
from karspark.keys import Keys, bearer
from karspark.ratelimit import RateLimiter

PEPPER = "p" * 40


@pytest.fixture
async def keys(tmp_path, cfg):
    db = await DB(tmp_path / "k.db").open()
    yield Keys(db, cfg.tiers, PEPPER)
    await db.close()


async def test_create_verify_revoke(keys):
    kid, secret = await keys.create("proj", "standard")
    p = await keys.verify(secret)
    assert p.key_id == kid and p.tier.name == "standard" and not p.is_owner
    assert await keys.verify(secret + "x") is None
    await keys.revoke(kid)
    assert await keys.verify(secret) is None


async def test_import_existing_owner_key(keys):
    kid, s = await keys.create("karthik-coding", "owner", secret="sk-ks-legacy-key-value-1234567890")
    p = await keys.verify("sk-ks-legacy-key-value-1234567890")
    assert p.is_owner and p.can_code and p.is_admin
    rows = await keys.list()
    assert rows[0]["hint"] == "7890" and "hash" not in rows[0]


async def test_priority_ceiling(keys):
    _, s = await keys.create("b", "batch")
    p = await keys.verify(s)
    assert p.effective_priority(0) == 3 and p.effective_priority(None) == 3
    _, s = await keys.create("o", "owner")
    o = await keys.verify(s)
    assert o.effective_priority(0) == 0 and o.effective_priority(None) == 1


def test_bearer():
    assert bearer({"authorization": "Bearer abc"}) == "abc"
    assert bearer({"x-api-key": "k"}) == "k"
    assert bearer({}) is None


async def test_rate_limit(keys, clock):
    _, s = await keys.create("s", "standard")  # rpm 120
    p = await keys.verify(s)
    rl = RateLimiter(clock)
    assert all(rl.check_request(p) == 0 for _ in range(120))
    wait = rl.check_request(p)
    assert 0 < wait <= 1
    clock.advance(1)
    assert rl.check_request(p) == 0
    rl.charge_tokens(p, 600_000)  # tpm 500k: over budget
    assert rl.check_request(p) > 0
