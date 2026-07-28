# test_email_ip_correlation.py
import os

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
