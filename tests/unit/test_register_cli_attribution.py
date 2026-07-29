import os
from unittest.mock import patch

import pytest

# register_cli is heavy; import lazily and tolerate optional deps.
rc = pytest.importorskip("register_cli")


def _reset():
    for k in ("EMAIL_IP_CORRELATION", "NODE_SCORE", "NODE_SCORE_ENABLED",
              "NODE_SCORE_PATH"):
        os.environ.pop(k, None)
    try:
        import node_score as ns
        ns.reset_cache()
        ns.set_enabled(None)
        ns.set_correlation_enabled(None)
    except Exception:
        pass


def setup_function(_):
    _reset()


def teardown_function(_):
    _reset()


def test_record_correlation_domain_off_is_noop(tmp_path, monkeypatch):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    os.environ["EMAIL_IP_CORRELATION"] = "0"
    out = rc._record_correlation_domain("u@a.com", "turnstile",
                                         cfg={})
    assert out["ok"] is False
    assert not (tmp_path / "s.json").exists()


def test_record_correlation_domain_on_writes(tmp_path):
    import json
    import node_score as ns
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    ns.set_correlation_enabled(True)
    out = rc._record_correlation_domain("u@a.com", "turnstile",
                                         cfg={"email_ip_correlation": True})
    assert out["ok"] is True
    data = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert "a.com" in data["domains"]


def test_registration_domain_of_parsing():
    assert rc._registration_domain_of("User@HotMail.COM") == "hotmail.com"
    assert rc._registration_domain_of("") == ""
    assert rc._registration_domain_of("noatsign") == "noatsign"


def test_pre_code_failure_does_not_dock_ip(tmp_path):
    """Layer ① boundary: a pre-code (mail_miss) failure records a DOMAIN penalty
    but leaves the IP node's score untouched. Equivalent to "do not call
    note_egress_outcome for IP" — verified by asserting the IP entry is unchanged
    after routing the domain penalty (uses the real node_score writer).
    """
    import json
    import node_score as ns
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "b.json")
    (tmp_path / "b.json").write_text(json.dumps({
        "version": 1, "nodes": {"n1": {"score": 71, "cool_until": 0.0,
                                         "ok": 3, "fail_ts": 0, "fail_boot": 0}}}),
        encoding="utf-8")
    ns.set_correlation_enabled(True)
    out = rc._record_correlation_domain("u@a.com", "mail_miss",
                                         cfg={"email_ip_correlation": True})
    assert out["ok"] is True
    data = json.loads((tmp_path / "b.json").read_text(encoding="utf-8"))
    # DOMAIN was penalized...
    assert data["domains"]["a.com"]["score"] == ns.DEFAULT_SCORE - ns.PENALTY_OTHER
    # ...but the IP node is byte-for-byte intact (no note_egress_outcome happened):
    assert data["nodes"]["n1"] == {"score": 71, "cool_until": 0.0, "ok": 3,
                                    "fail_ts": 0, "fail_boot": 0}
