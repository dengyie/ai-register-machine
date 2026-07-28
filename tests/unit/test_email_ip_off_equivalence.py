"""§5.A — OFF-equivalence closing sweep (highest-priority regression line).

EMAIL_IP_CORRELATION OFF must equal today's behavior across:
  * mail sampling (sample_accounts == random.shuffle),
  * node rotation (pick_next == round-robin as today),
  * attribution (no domains/pairs keys ever written under OFF).

These guards are the feature's ship gate; if any fails, the feature stays OFF.
"""

import json
import os
import random

import pytest

import mail_pool_probe as mpp
import node_score as ns
import proxy_rotate as pr  # noqa: F401  (import-safety + parity)


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


# §5.A — mail sampling OFF == random.shuffle (byte-identical)
def test_off_mail_sampling_byte_identical_to_shuffle(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    pool = []
    for d in ("a.com", "b.com", "c.com"):
        for i in range(15):
            pool.append(mpp.Credential(
                email=f"u{i}@{d}", password="p",
                client_id="c", refresh_token="r"))
    # The OFF branch calls random.shuffle(pool) and slices. To compare
    # byte-identically we reseed to the SAME state just before both shuffles:
    # sample_accounts consumes the module RNG once via random.shuffle, so we
    # reseed identically before each of the two shuffles (see mail_pool_probe
    # sample_accounts OFF path — verbatim random.shuffle + slice).
    random.seed(2026)
    want = list(pool)
    random.shuffle(want)
    want = want[:11]
    random.seed(2026)
    ns.set_correlation_enabled(False)  # explicitly OFF
    got = mpp.sample_accounts(list(pool), 11, cfg={})
    assert got == want
    # store untouched (no domains/pairs keys injected when OFF)
    assert not (tmp_path / "s.json").exists()


# §5.A — node rotation OFF == round-robin / pick_next-as-today
def test_off_node_rotation_unchanged(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    # OFF both switches -> pick_next == _round_robin
    nodes = ["n0", "n1", "n2", "n3"]
    assert ns.pick_next(nodes, "n0", cfg={}) == "n1"
    assert ns.pick_next(nodes, "n2", cfg={}) == "n3"


# §5.A — attribution boundary OFF: pre-code failure still does NOT dock IP
def test_off_no_domains_or_pairs_written_via_records(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    # EMAIL_IP_CORRELATION OFF but NODE_SCORE ON: store must stay IP-only.
    os.environ["NODE_SCORE"] = "1"
    ns.set_correlation_enabled(False)
    ns.record("n1", "reg_ok", cfg={})  # IP record works
    assert ns.record_domain("a.com", "turnstile", cfg={})["ok"] is False
    assert ns.record_pair("a.com", "n1", "turnstile", cfg={})["ok"] is False
    data = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert "n1" in data["nodes"]
    assert "domains" not in data  # lazy: never written under OFF
    assert "pairs" not in data
