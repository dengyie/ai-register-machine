"""Unit tests for node_score dynamic egress weight."""

from __future__ import annotations

import os
import random
from pathlib import Path

import node_score as ns


def setup_function() -> None:
    ns.reset_cache()
    os.environ.pop("NODE_SCORE", None)
    os.environ.pop("NODE_SCORE_ENABLED", None)
    os.environ.pop("NODE_SCORE_PATH", None)
    os.environ.pop("EMAIL_IP_CORRELATION", None)
    ns.set_enabled(None)  # type: ignore[arg-type]
    ns.set_correlation_enabled(None)  # type: ignore[arg-type]


def teardown_function() -> None:
    # Prevent NODE_SCORE_PATH / NODE_SCORE leaking into later modules in the suite.
    ns.reset_cache()
    os.environ.pop("NODE_SCORE", None)
    os.environ.pop("NODE_SCORE_ENABLED", None)
    os.environ.pop("NODE_SCORE_PATH", None)
    os.environ.pop("EMAIL_IP_CORRELATION", None)
    ns.set_enabled(None)  # type: ignore[arg-type]
    ns.set_correlation_enabled(None)  # type: ignore[arg-type]


def test_default_off() -> None:
    ns.reset_cache()
    os.environ.pop("NODE_SCORE", None)
    assert ns.scoring_enabled() is False
    # pick falls back to round-robin
    nodes = ["A", "B", "C"]
    assert ns.pick_next(nodes, "A") == "B"
    print("PASS default off")


def test_env_on_off(tmp_path: Path) -> None:
    ns.reset_cache()
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "scores.json")
    os.environ["NODE_SCORE"] = "1"
    assert ns.scoring_enabled() is True
    os.environ["NODE_SCORE"] = "0"
    ns.reset_cache()
    assert ns.scoring_enabled() is False
    os.environ["NODE_SCORE"] = "true"
    ns.reset_cache()
    assert ns.scoring_enabled() is True
    print("PASS env on/off")


def test_config_key(tmp_path: Path) -> None:
    ns.reset_cache()
    os.environ.pop("NODE_SCORE", None)
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    assert ns.scoring_enabled({"node_score_enabled": True}) is True
    assert ns.scoring_enabled({"node_score_enabled": False}) is False
    # env wins over config
    os.environ["NODE_SCORE"] = "0"
    assert ns.scoring_enabled({"node_score_enabled": True}) is False
    print("PASS config + env precedence")


def test_turnstile_cool_and_pick(tmp_path: Path) -> None:
    ns.reset_cache()
    path = tmp_path / "scores.json"
    os.environ["NODE_SCORE"] = "1"
    os.environ["NODE_SCORE_PATH"] = str(path)
    nodes = ["GOOD", "BAD", "MID"]
    # equal scores → round-robin
    assert ns.pick_next(nodes, "GOOD") == "BAD"
    ns.record("BAD", "turnstile", cfg=None)
    assert ns.is_cooled("BAD") is True
    # BAD cooled → next pick from GOOD/MID, not BAD
    for _ in range(20):
        n = ns.pick_next(nodes, "GOOD", rng=random.Random(0))
        assert n != "BAD", n
    # mint boost GOOD
    ns.record("GOOD", "mint_ok")
    assert ns.get_score("GOOD") > ns.DEFAULT_SCORE
    # persistence
    ns.reset_cache()
    os.environ["NODE_SCORE"] = "1"
    os.environ["NODE_SCORE_PATH"] = str(path)
    assert ns.is_cooled("BAD") is True
    assert ns.get_score("GOOD") > ns.DEFAULT_SCORE
    print("PASS turnstile cool + persist")


def test_record_disabled_noop(tmp_path: Path) -> None:
    ns.reset_cache()
    os.environ["NODE_SCORE"] = "0"
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "x.json")
    out = ns.record("N1", "turnstile")
    assert out.get("ok") is False
    assert not (tmp_path / "x.json").exists()
    print("PASS disabled record noop")


def test_all_cooled_fallback(tmp_path: Path) -> None:
    ns.reset_cache()
    os.environ["NODE_SCORE"] = "1"
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "all.json")
    nodes = ["A", "B"]
    ns.record("A", "turnstile")
    ns.record("B", "turnstile")
    # must still return a node (pool never empty)
    n = ns.pick_next(nodes, "A")
    assert n in nodes
    print("PASS all cooled fallback")


# ---------------------------------------------------------------------------
# §5.F — IP scoring non-regression: correlation must not touch the IP path.
# Constants, record deltas/cooldowns, and the empty store shape are frozen.
# ---------------------------------------------------------------------------

def test_ip_constants_unchanged() -> None:
    """§5.F: the IP-side scoring constants that correlation must not touch."""
    assert ns.DEFAULT_SCORE == 50
    assert ns.MIN_SCORE == 0 and ns.MAX_SCORE == 100
    assert ns.SUCCESS_REG == 3 and ns.SUCCESS_MINT == 5
    assert ns.PENALTY_TURNSTILE == 15
    assert ns.PENALTY_BOOT == 3 and ns.PENALTY_OTHER == 2
    assert ns.COOL_TURNSTILE_S == 20 * 60
    assert ns.COOL_BOOT_S == 5 * 60
    assert ns.COOL_OTHER_S == 2 * 60


def test_ip_record_turnstile_delta_and_cool_unchanged(tmp_path: Path) -> None:
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "ip.json")
    ns.set_enabled(True)
    out = ns.record("n1", "turnstile", cfg={})
    assert out["delta"] == -15
    assert out["score"] == 50 - 15
    assert out["cool_until"] > 0


def test_ip_empty_store_still_version1_nodes_only() -> None:
    s = ns._empty_store()
    assert s == {"version": 1, "nodes": {}}
    assert "domains" not in s and "pairs" not in s


def test_ip_record_disabled_when_correlation_only(tmp_path: Path) -> None:
    """NODE_SCORE off but EMAIL_IP on: IP record still no-op (independent switches)."""
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "ip2.json")
    ns.set_enabled(None)  # NODE_SCORE default off
    ns.set_correlation_enabled(True)  # correlation on
    out = ns.record("n1", "turnstile", cfg={})
    assert out["ok"] is False
    assert ns.record_domain("a.com", "turnstile", cfg={})["ok"] is True


if __name__ == "__main__":
    from pathlib import Path as P
    import tempfile

    setup_function()
    test_default_off()
    with tempfile.TemporaryDirectory() as d:
        setup_function()
        test_env_on_off(P(d))
        setup_function()
        test_config_key(P(d))
        setup_function()
        test_turnstile_cool_and_pick(P(d))
        setup_function()
        test_record_disabled_noop(P(d))
        setup_function()
        test_all_cooled_fallback(P(d))
        setup_function()
        test_ip_constants_unchanged()
        setup_function()
        test_ip_record_turnstile_delta_and_cool_unchanged(P(d))
        setup_function()
        test_ip_empty_store_still_version1_nodes_only()
        setup_function()
        test_ip_record_disabled_when_correlation_only(P(d))
    print("ALL OK")