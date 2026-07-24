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
    ns.set_enabled(None)  # type: ignore[arg-type]


def teardown_function() -> None:
    # Prevent NODE_SCORE_PATH / NODE_SCORE leaking into later modules in the suite.
    ns.reset_cache()
    os.environ.pop("NODE_SCORE", None)
    os.environ.pop("NODE_SCORE_ENABLED", None)
    os.environ.pop("NODE_SCORE_PATH", None)
    ns.set_enabled(None)  # type: ignore[arg-type]


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
    print("ALL OK")
