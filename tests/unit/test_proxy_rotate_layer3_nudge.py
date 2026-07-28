import json
import os

import pytest

proxy_rotate = pytest.importorskip("proxy_rotate")
ns = pytest.importorskip("node_score")


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


def test_registration_domain_hint_roundtrip():
    proxy_rotate.clear_registration_domain()
    proxy_rotate.set_registration_domain("HotMail.com")
    with proxy_rotate.get_rotator()._lock:
        assert proxy_rotate.get_rotator()._registration_domain == "hotmail.com"
    proxy_rotate.clear_registration_domain()
    with proxy_rotate.get_rotator()._lock:
        assert proxy_rotate.get_rotator()._registration_domain == ""


def test_nudge_clash_picks_preferred_when_good_pair(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 1, "nodes": {},
        "pairs": {"a.com|good": {"score": 80, "ok": 2, "cool_until": 0.0}}}),
                 encoding="utf-8")
    os.environ["NODE_SCORE_PATH"] = str(p)
    ns.set_correlation_enabled(True)
    proxy_rotate.set_registration_domain("a.com")

    rot = proxy_rotate.get_rotator()
    switched = {}
    def _fake_switch(api, group, node, *, secret="", flush=True):
        switched["node"] = node
        return {"ok": True}
    monkeypatch.setattr(proxy_rotate, "clash_switch_node", _fake_switch)
    monkeypatch.setattr(proxy_rotate, "clash_list_nodes",
                        lambda *a, **k: (["bad", "good"], "bad", {}))
    monkeypatch.setattr(ns, "scoring_enabled", lambda cfg=None: False)  # force pair path only

    rot.mode = "clash"
    rot._started = True
    rot.clash_setup_done = True
    rot._mint_holds.clear()
    rot.current_label = "bad"
    res = rot._rotate_clash_locked(cfg={"email_ip_correlation": True})
    assert res["rotated"] is True
    assert switched["node"] == "good"
    assert res.get("pick") == "pair_affinity"


def test_nudge_none_falls_back_when_no_pair(tmp_path, monkeypatch):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    ns.set_correlation_enabled(True)
    proxy_rotate.set_registration_domain("a.com")  # NO pair recorded
    rot = proxy_rotate.get_rotator()
    monkeypatch.setattr(proxy_rotate, "clash_switch_node",
                        lambda *a, **k: {"ok": True})
    monkeypatch.setattr(proxy_rotate, "clash_list_nodes",
                        lambda *a, **k: (["x", "y"], "x", {}))
    monkeypatch.setattr(ns, "scoring_enabled", lambda cfg=None: False)
    rot.mode = "clash"
    rot._started = True
    rot.clash_setup_done = True
    rot._mint_holds.clear()
    rot.current_label = "x"
    res = rot._rotate_clash_locked(cfg={"email_ip_correlation": True})
    # no good pair -> round-robin to y (the next node)
    assert res["rotated"] is True
    assert res["node"] == "y"
    assert res.get("reason") != "pair_affinity"


def test_nudge_off_never_fires(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 1, "nodes": {},
        "pairs": {"a.com|good": {"score": 80, "ok": 2}}}), encoding="utf-8")
    os.environ["NODE_SCORE_PATH"] = str(p)
    ns.set_correlation_enabled(False)  # OFF
    proxy_rotate.set_registration_domain("a.com")  # hint set but switch OFF
    rot = proxy_rotate.get_rotator()
    calls = {}
    def _sw(*a, **k):
        calls["node"] = k.get("node") or a[2]
        return {"ok": True}
    monkeypatch.setattr(proxy_rotate, "clash_switch_node", _sw)
    monkeypatch.setattr(proxy_rotate, "clash_list_nodes",
                        lambda *a, **k: (["bad", "good"], "bad", {}))
    monkeypatch.setattr(ns, "scoring_enabled", lambda cfg=None: False)
    rot.mode = "clash"
    rot._started = True
    rot.clash_setup_done = True
    rot._mint_holds.clear()
    rot.current_label = "bad"
    res = rot._rotate_clash_locked(cfg={})
    assert res.get("pick") != "pair_affinity"
