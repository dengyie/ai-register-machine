import os
import random

import pytest

import mail_pool_probe as mpp
import node_score as ns


def _acc(domain, n):
    """Build n Credential-like accounts under one domain."""
    from mail_pool_probe import Credential
    return [Credential(email=f"u{i}@{domain}", password="p",
                       client_id="c", refresh_token="r")
            for i in range(n)]


def _reset():
    ns.reset_cache()
    for k in ("NODE_SCORE", "NODE_SCORE_ENABLED", "NODE_SCORE_PATH",
              "EMAIL_IP_CORRELATION"):
        os.environ.pop(k, None)
    ns.set_enabled(None)
    ns.set_correlation_enabled(None)


def setup_function(_):
    _reset()


def teardown_function(_):
    _reset()


def test_off_equivalent_to_shuffle(tmp_path):
    """OFF: same random.Random(seed) -> identical sequence to current shuffle."""
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    ns.set_correlation_enabled(False)
    pool = _acc("a.com", 20) + _acc("b.com", 20)
    random.seed(1234)
    want = list(pool)
    random.shuffle(want)
    want = want[:7]
    # Reseed so sample_accounts starts from the same global RNG state as want.
    random.seed(1234)
    got = mpp.sample_accounts(list(pool), 7, cfg={})  # OFF
    assert got == want


def test_single_domain_off_path(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    ns.set_correlation_enabled(True)  # ON, but single domain -> equal weight
    pool = _acc("a.com", 30)
    # weights uniform -> every account surfaced over many draws (no domain bias)
    seen = set()
    for s in range(40):
        got = mpp.sample_accounts(list(pool), 10, cfg={"email_ip_correlation": True})
        seen.update(a.email for a in got)
    assert len(seen) == 30


def test_seed_disables_weighted_branch(tmp_path):
    """seed != None must keep shuffle (deterministic) regardless of switch."""
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    pool = _acc("a.com", 10) + _acc("b.com", 10)
    ns.set_correlation_enabled(True)
    a = mpp.sample_accounts(list(pool), 5, seed=7, cfg={"email_ip_correlation": True})
    b = mpp.sample_accounts(list(pool), 5, seed=7, cfg={"email_ip_correlation": True})
    assert a == b  # deterministic under seed


def test_offset_disables_weighted_branch(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    pool = _acc("a.com", 12)
    ns.set_correlation_enabled(True)
    got = mpp.sample_accounts(list(pool), 4, offset=2,
                              cfg={"email_ip_correlation": True})
    assert [a.email for a in got] == [f"u{i}@a.com" for i in (2, 3, 4, 5)]


def test_multi_domain_high_score_picked_more(tmp_path):
    """Layer ②: a high-score domain is sampled more often than a low-score one."""
    import json, statistics
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 1, "nodes": {}, "domains": {
        "high.com": {"score": 99, "ok": 5, "cool_until": 0.0},
        "low.com": {"score": 1, "ok": 0, "cool_until": 0.0}}}), encoding="utf-8")
    os.environ["NODE_SCORE_PATH"] = str(p)
    pool = _acc("high.com", 50) + _acc("low.com", 50)
    ns.set_correlation_enabled(True)
    high_count = 0
    for t in range(400):
        got = mpp.sample_accounts(list(pool), 10, cfg={"email_ip_correlation": True})
        high_count += sum(1 for a in got if a.domain == "high.com")
    # high.com should dominate (avg ~9+/10); require a wide margin over parity.
    assert high_count > 400 * 10 * 0.80
