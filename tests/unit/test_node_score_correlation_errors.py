"""§5.E — node_score correlation error handling.

Store corruption and persist failure must degrade silently to neutral defaults
and never break registration. These are the spec's error-isolation guards.
"""

import json
import os

import pytest

import node_score as ns


def _reset():
    for k in ("EMAIL_IP_CORRELATION", "NODE_SCORE", "NODE_SCORE_ENABLED",
              "NODE_SCORE_PATH"):
        os.environ.pop(k, None)
    ns.reset_cache()
    ns.set_enabled(None)
    ns.set_correlation_enabled(None)


def setup_function(_):
    _reset()


def teardown_function(_):
    _reset()


def test_corrupt_json_degrades_to_empty(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "corrupt.json")
    (tmp_path / "corrupt.json").write_text("{not valid json", encoding="utf-8")
    ns.set_correlation_enabled(True)
    # reads degrade to defaults; a write repairs the file
    assert ns.get_domain_score("a.com") == ns.DEFAULT_SCORE
    assert ns.get_pair("a.com", "n1") == {}
    out = ns.record_domain("a.com", "reg_ok", cfg={})
    assert out["ok"] is True
    data = json.loads((tmp_path / "corrupt.json").read_text(encoding="utf-8"))
    assert "domains" in data and "a.com" in data["domains"]


def test_corrupt_domains_key_isolated_from_nodes(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "m.json")
    (tmp_path / "m.json").write_text(json.dumps({
        "version": 1, "nodes": {"x": {"score": 77}}, "domains": 42,
        "pairs": "nope"}), encoding="utf-8")
    ns.set_correlation_enabled(True)
    assert ns.get_domain_score("a.com") == ns.DEFAULT_SCORE  # bad domains ignored
    assert ns.get_pair("a.com", "n1") == {}  # bad pairs ignored
    assert ns.get_score("x") == 77  # IP reads still work
    ns.record_domain("a.com", "reg_ok", cfg={})  # repairs domains key in place
    data = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
    # IP untouched: the x entry survives with its score unchanged. _save
    # re-normalizes every node entry via _get_store's setdefault defaults (see
    # node_score._get/_ensure_node_entry — it fills cool_until/ok/fail_ts/etc,
    # so the field set grows but the score does not), so assert only the
    # invariant correlation must hold: score == 77 and exactly one IP node.
    assert set(data["nodes"]) == {"x"}
    assert data["nodes"]["x"]["score"] == 77
    assert "a.com" in data["domains"]


def test_record_writes_survive_error_path(tmp_path):
    """Spec §4: write failure must never break registration. _save swallows.

    Pointing NODE_SCORE_PATH inside a FILE (so path.parent is a file, not a
    directory) makes the persist raise; node_score._save catches Exception and
    swallows (see node_score._save except branch), so record_domain still
    returns ok=True — registration never sees the failure.
    """
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "blocker" / "x.json")
    (tmp_path / "blocker").write_text("x", encoding="utf-8")  # blocker is a file
    ns.set_correlation_enabled(True)
    out = ns.record_domain("a.com", "turnstile", cfg={})
    # _save swallows -> the call returns ok=True despite the failed persist
    assert out["ok"] is True
    assert not (tmp_path / "blocker" / "x.json").exists()
