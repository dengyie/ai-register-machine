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
    # success payload carries the pick under "pick" (not "reason")
    assert res.get("pick") == "round_robin"


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
    # 3-node pool so the RR successor ("other") differs from the pair node
    # ("good") — otherwise both strategies land on the same node and the test
    # cannot tell a silent pair-preference from round-robin.
    monkeypatch.setattr(proxy_rotate, "clash_list_nodes",
                        lambda *a, **k: (["bad", "other", "good"], "bad", {}))
    monkeypatch.setattr(ns, "scoring_enabled", lambda cfg=None: False)
    rot.mode = "clash"
    rot._started = True
    rot.clash_setup_done = True
    rot._mint_holds.clear()
    rot.current_label = "bad"
    res = rot._rotate_clash_locked(cfg={})
    assert res.get("pick") != "pair_affinity"
    # OFF must round-robin off "bad" to "other", never the good-pair "good".
    assert res["node"] == "other"
    assert calls["node"] == "other"


def test_nudge_list_picks_preferred_when_good_pair(tmp_path, monkeypatch):
    """Layer ③ list path: a good, non-cooled pair node is preferred over RR."""
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 1, "nodes": {},
        "pairs": {"a.com|good": {"score": 80, "ok": 2, "cool_until": 0.0}}}),
                 encoding="utf-8")
    os.environ["NODE_SCORE_PATH"] = str(p)
    ns.set_correlation_enabled(True)
    proxy_rotate.set_registration_domain("a.com")
    rot = proxy_rotate.get_rotator()
    rot.mode = "list"
    rot._started = True
    rot.accounts_on_current = 1  # skip first-claim branch
    rot._mint_holds.clear()
    rot.list_pool = ["bad", "other", "good"]
    rot.list_index = 0
    rot.current_proxy = "bad"  # prev / "now"
    monkeypatch.setattr(ns, "scoring_enabled", lambda cfg=None: False)
    res = rot._rotate_list_locked(cfg={"email_ip_correlation": True})
    assert res["rotated"] is True
    # "good" is the pair node; RR successor off "bad" would be "other".
    assert res["proxy"] == "good"
    assert res["pick"] == "pair_affinity"
    assert rot.list_index == 2


def test_nudge_list_off_round_robins_unchanged(tmp_path, monkeypatch):
    """OFF: the restructured list scored-pick block must keep RR behavior.

    scoring_enabled False + correlation OFF -> raise RuntimeError inside the
    scored-pick try -> caught -> nxt stays "" -> RR advances one off prev.
    """
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    ns.set_correlation_enabled(False)
    proxy_rotate.set_registration_domain("a.com")  # hint set but OFF
    rot = proxy_rotate.get_rotator()
    rot.mode = "list"
    rot._started = True
    rot.accounts_on_current = 1
    rot._mint_holds.clear()
    rot.list_pool = ["n0", "n1", "n2"]
    rot.list_index = 1           # invariant: tracks current_proxy
    rot.current_proxy = "n1"     # prev -> RR successor is n2 (index 2)
    monkeypatch.setattr(ns, "scoring_enabled", lambda cfg=None: False)
    res = rot._rotate_list_locked(cfg={})
    assert res["proxy"] == "n2"
    assert res["pick"] == "round_robin"
    assert rot.list_index == 2
    # and a leaked hint was never consulted (no pair_affinity)
    assert res.get("pick") != "pair_affinity"
