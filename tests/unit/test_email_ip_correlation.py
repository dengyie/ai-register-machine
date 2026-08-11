# test_email_ip_correlation.py
import os

import pytest

import node_score as ns


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


def test_correlation_disabled_by_default():
    assert ns.correlation_enabled() is False


def test_correlation_env_enables():
    os.environ["EMAIL_IP_CORRELATION"] = "1"
    assert ns.correlation_enabled() is True


def test_correlation_env_empty_or_falsy_off():
    os.environ["EMAIL_IP_CORRELATION"] = ""  # empty != unset; stays OFF
    assert ns.correlation_enabled() is False
    os.environ["EMAIL_IP_CORRELATION"] = "0"
    assert ns.correlation_enabled() is False
    os.environ["EMAIL_IP_CORRELATION"] = "false"
    assert ns.correlation_enabled() is False


def test_correlation_cfg_enables():
    assert ns.correlation_enabled({"email_ip_correlation": True}) is True


def test_correlation_cfg_falsy_string_off():
    # a non-empty-but-falsy string must NOT pass bool-truthiness as True
    assert ns.correlation_enabled({"email_ip_correlation": "false"}) is False


def test_correlation_override_enables():
    ns.set_correlation_enabled(True)
    assert ns.correlation_enabled() is True


def test_correlation_env_beats_override_and_cfg():
    os.environ["EMAIL_IP_CORRELATION"] = "0"  # env wins -> OFF even if override ON
    ns.set_correlation_enabled(True)
    assert ns.correlation_enabled() is False
    os.environ.pop("EMAIL_IP_CORRELATION", None)
    assert ns.correlation_enabled() is True  # now override takes effect


def test_empty_store_has_no_domains_pairs_keys(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    store = ns._empty_store()
    assert "domains" not in store
    assert "pairs" not in store


def _seed_domains_store(tmp_path, domains):
    """Write a node_scores.json with a domains block for read tests."""
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    import json
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 1, "nodes": {}, "domains": domains}),
                 encoding="utf-8")
    return p


def test_domain_score_unknown_returns_default(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    assert ns.get_domain_score("nope.example") == ns.DEFAULT_SCORE


def test_domain_score_known(tmp_path):
    _seed_domains_store(tmp_path, {"a.com": {"score": 73, "cool_until": 0.0,
                                              "ok": 2, "fail_ts": 0}})
    # reads are gated by the correlation switch (DEFAULT_SCORE when OFF)
    ns.set_correlation_enabled(True)
    assert ns.get_domain_score("a.com") == 73


def test_record_domain_disabled_noop(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    import json
    ns.set_correlation_enabled(False)  # OFF must not touch store
    out = ns.record_domain("a.com", "turnstile")
    assert out["ok"] is False
    assert out["reason"] == "disabled"
    # store file should NOT be created by a disabled write
    assert not (tmp_path / "s.json").exists()


def test_record_domain_turnstile_pens_and_writes_domains_key(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    ns.set_correlation_enabled(True)
    out = ns.record_domain("a.com", "turnstile")
    assert out["ok"] is True
    assert out["domain"] == "a.com"
    assert out["score"] == ns.DEFAULT_SCORE - ns.PENALTY_TURNSTILE
    import json
    data = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert "domains" in data and "a.com" in data["domains"]
    assert data["domains"]["a.com"]["score"] == out["score"]
    assert data["domains"]["a.com"]["cool_until"] > 0
    # IP nodes dict still empty (cross-dimension isolation)
    assert data["nodes"] == {}


def test_record_domain_reg_ok_clears_cool(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    import json, time
    _seed_domains_store(
        tmp_path,
        {"a.com": {"score": 35, "cool_until": time.time() + 999, "ok": 0,
                    "fail_ts": 1, "fail_boot": 0, "updated": 0}},
    )
    ns.set_correlation_enabled(True)
    out = ns.record_domain("a.com", "reg_ok")
    assert out["ok"] is True
    assert out["score"] == 35 + ns.SUCCESS_REG
    data = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert data["domains"]["a.com"]["cool_until"] == 0.0
    assert data["domains"]["a.com"]["ok"] == 1


def test_kind_effect_pure_no_mutate():
    entry = {"score": 50, "ok": 0, "fail_ts": 0, "fail_boot": 0,
             "cool_until": 0.0}
    snap = dict(entry)
    d, c = ns._kind_effect("turnstile", entry)
    assert d == -ns.PENALTY_TURNSTILE
    assert c == ns.COOL_TURNSTILE_S
    # entry untouched
    assert entry == snap


def test_record_domain_repairs_corrupt_domains_key(tmp_path):
    import json
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"version": 1, "nodes": {"x": {"score": 80}},
                            "domains": "NOT-A-DICT"}), encoding="utf-8")
    os.environ["NODE_SCORE_PATH"] = str(p)
    ns.set_correlation_enabled(True)
    out = ns.record_domain("a.com", "reg_ok")
    assert out["ok"] is True
    data = json.loads(p.read_text(encoding="utf-8"))
    # IP untouched
    assert data["nodes"] == {"x": {"score": 80}}
    # corrupt domains replaced with a dict holding the new entry
    assert isinstance(data["domains"], dict) and "a.com" in data["domains"]


def test_record_domain_none_kind_does_not_raise(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "n.json")
    ns.set_correlation_enabled(True)
    out = ns.record_domain("a.com", None)  # must not AttributeError
    assert out["ok"] is True
    # None kind falls to OTHER in _kind_effect
    import json
    data = json.loads((tmp_path / "n.json").read_text(encoding="utf-8"))
    assert data["domains"]["a.com"]["score"] == ns.DEFAULT_SCORE - ns.PENALTY_OTHER


def test_record_domain_double_cool_on_second_consecutive_turnstile(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "dc.json")
    ns.set_correlation_enabled(True)
    first = ns.record_domain("a.com", "turnstile")
    assert first["ok"] is True
    second = ns.record_domain("a.com", "turnstile")
    assert second["ok"] is True
    import json, time
    data = json.loads((tmp_path / "dc.json").read_text(encoding="utf-8"))
    dom = data["domains"]["a.com"]
    assert dom["fail_ts"] == 2
    # doubled == 2 x COOL_TURNSTILE_S beyond now (within epsilon)
    assert dom["cool_until"] > time.time() + ns.COOL_TURNSTILE_S + 60


def test_get_pair_disabled_returns_empty(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    assert ns.get_pair("a.com", "n1") == {}


def test_record_pair_disabled_noop_no_file(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    out = ns.record_pair("a.com", "n1", "turnstile")
    assert out["ok"] is False
    assert not (tmp_path / "s.json").exists()


def test_record_pair_ok_creates_pairs_and_rewards(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    ns.set_correlation_enabled(True)
    out = ns.record_pair("a.com", "n1", "mint_ok")
    assert out["ok"] is True
    assert out["pair"] == "a.com|n1"
    assert out["score"] == ns.DEFAULT_SCORE + ns.SUCCESS_MINT
    import json
    data = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert "pairs" in data and "a.com|n1" in data["pairs"]
    assert data["nodes"] == {} and data.get("domains", {}) == {}


def test_pair_is_cooled_respects_switch(tmp_path):
    import json, time
    p = tmp_path / "s.json"
    p.write_text(json.dumps({
        "version": 1, "nodes": {},
        "pairs": {"a.com|n1": {"score": 40, "cool_until": time.time() + 600,
                                "ok": 0, "fail_ts": 1}},
    }), encoding="utf-8")
    os.environ["NODE_SCORE_PATH"] = str(p)
    # OFF -> never cooled (correlation not active)
    assert ns.pair_is_cooled("a.com", "n1") is False
    ns.set_correlation_enabled(True)
    assert ns.pair_is_cooled("a.com", "n1") is True
    assert ns.pair_is_cooled("a.com", "n1", now=time.time() + 99999) is False


def test_record_pair_fail_then_cool_and_recover(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    ns.set_correlation_enabled(True)
    ns.record_pair("a.com", "n1", "turnstile")
    assert ns.pair_is_cooled("a.com", "n1") is True
    # a success clears the cool
    ns.record_pair("a.com", "n1", "reg_ok")
    assert ns.pair_is_cooled("a.com", "n1") is False


def test_corrupt_pairs_key_degrades_to_empty(tmp_path):
    import json
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 1, "nodes": {"x": {"score": 80}},
                            "pairs": "NOT-A-DICT"}), encoding="utf-8")
    os.environ["NODE_SCORE_PATH"] = str(p)
    ns.set_correlation_enabled(True)
    # reads degrade cleanly
    assert ns.get_pair("a.com", "n1") == {}
    assert ns.pair_is_cooled("a.com", "n1") is False
    # and a fresh write repairs the key without dropping nodes
    out = ns.record_pair("a.com", "n1", "reg_ok")
    assert out["ok"] is True
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["nodes"] == {"x": {"score": 80}}
    assert "a.com|n1" in data["pairs"]


def test_domain_weights_disabled_uniform(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    _seed_domains_store(tmp_path, {"a.com": {"score": 90}, "b.com": {"score": 10}})
    ns.set_correlation_enabled(False)
    w = ns.domain_weights(["a.com", "b.com"])
    assert w == [1.0, 1.0]


def test_domain_weights_single_domain_uniform():
    ns.set_correlation_enabled(True)
    assert ns.domain_weights(["only.com"]) == [1.0]
    assert ns.domain_weights([]) == []


def test_domain_weights_skews_by_score(tmp_path):
    _seed_domains_store(tmp_path, {"a.com": {"score": 90, "ok": 1},
                                    "b.com": {"score": 10, "ok": 0}})
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    ns.set_correlation_enabled(True)
    w = ns.domain_weights(["a.com", "b.com"])
    assert w[0] > w[1]
    assert w[0] == pytest.approx(91.0)
    assert w[1] == pytest.approx(11.0)


def test_domain_weights_unknown_domain_gets_default():
    ns.set_correlation_enabled(True)
    w = ns.domain_weights(["never.example", "also.unknown"])
    assert w[0] == w[1] == pytest.approx(ns.DEFAULT_SCORE + 1.0)


def test_preferred_node_returns_none_when_disabled(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    ns.set_correlation_enabled(False)
    assert ns.preferred_node_for("a.com", ["n1", "n2"], "n1") is None


def test_preferred_node_picks_good_uncooled_not_now(tmp_path):
    import json
    _seed_domains_store(tmp_path, {})
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 1, "nodes": {},
        "pairs": {"a.com|n1": {"score": 80, "ok": 2, "cool_until": 0.0},
                  "a.com|n2": {"score": 70, "ok": 1, "cool_until": 0.0}}}),
                 encoding="utf-8")
    os.environ["NODE_SCORE_PATH"] = str(p)
    ns.set_correlation_enabled(True)
    # now=n1 -> must avoid n1, pick n2 (best remaining good node)
    assert ns.preferred_node_for("a.com", ["n1", "n2", "n3"], "n1") == "n2"
    # now=n2 -> pick n1 (highest-scoring good node != now)
    assert ns.preferred_node_for("a.com", ["n1", "n2", "n3"], "n2") == "n1"


def test_preferred_node_none_when_all_cooled(tmp_path):
    import json, time
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 1, "nodes": {},
        "pairs": {"a.com|n1": {"score": 80, "ok": 2,
                                "cool_until": time.time() + 600}}}),
                 encoding="utf-8")
    os.environ["NODE_SCORE_PATH"] = str(p)
    ns.set_correlation_enabled(True)
    assert ns.preferred_node_for("a.com", ["n1", "n2"], "n2") is None


def test_preferred_node_none_when_no_pair():
    ns.set_correlation_enabled(True)
    assert ns.preferred_node_for("a.com", ["n1", "n2"], "n1") is None
