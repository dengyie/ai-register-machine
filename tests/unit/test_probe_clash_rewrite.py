#!/usr/bin/env python3
"""Offline checks for scripts/probe_clash_nodes.py rewrite_config_groups.

Locks in the curated-select preservation: select-type register groups keep
their curated member list (e.g. the 17-node per-exit-IP trim), stripping only
leaves flagged dead in every round, preserving order. url-test groups keep the
full healthy-first pool. Non-register groups are never touched.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "probe_clash_nodes", ROOT / "scripts" / "probe_clash_nodes.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["probe_clash_nodes"] = mod
    spec.loader.exec_module(mod)
    return mod


def _call_rewrite(mod, groups_yaml: str, healthy, preferred_first=None):
    """Rewrite against an in-memory yaml text; return the rewritten data dict."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "config.yaml"
        p.write_text(groups_yaml, encoding="utf-8")

        captured = {}

        def fake_dump(cfg_path, text, data, note):  # type: ignore[no-untyped-def]
            captured["data"] = data
            return cfg_path  # pretend a backup path was returned

        saved = mod._backup_and_dump
        mod._backup_and_dump = fake_dump
        try:
            mod.rewrite_config_groups(p, list(healthy), list(preferred_first or []))
        finally:
            mod._backup_and_dump = saved
    assert "data" in captured, "rewrite_config_groups never called _backup_and_dump"
    return captured["data"]


_GROUPS = """
proxy-groups:
  - name: 🎯Grok注册
    type: select
    proxies: [GVPS-VLESS-CF-LSJ, GL-Fast-B1-6, GL-Balancer-B1-1]
  - name: ♻️Grok优选
    type: url-test
    proxies: [GVPS-VLESS-CF-LSJ, GL-Fast-B1-6]
  - name: PROXY
    type: select
    proxies: [SUB-DEAD-X, SUB-US-Dedicated-B1-2]
  - name: 🔰ChatGPT
    type: url-test
    proxies: [GVPS-VLESS-CF-LSJ]
  - name: OTHER-SERVICE
    type: select
    proxies: [SHOULD-STAY, ALSO-STAY]
"""


def test_select_group_keeps_curated_healthy_preserving_order() -> None:
    mod = _load()
    data = _call_rewrite(
        mod,
        _GROUPS,
        healthy=[
            "GL-Balancer-B1-1",
            "GVPS-VLESS-CF-LSJ",
            "GL-Fast-B1-6",
            "SUB-US-Dedicated-B1-2",
        ],
    )
    groups = {g["name"]: g for g in data["proxy-groups"]}
    reg = groups["🎯Grok注册"]["proxies"]
    # curated list, dead stripped, original order retained (NOT resorted by healthy order)
    assert reg == [
        "GVPS-VLESS-CF-LSJ",
        "GL-Fast-B1-6",
        "GL-Balancer-B1-1",
    ], reg

    # PROXY select: dead SUB-DEAD-X stripped but healthy curated member stays
    assert groups["PROXY"]["proxies"] == ["SUB-US-Dedicated-B1-2"], groups["PROXY"]["proxies"]

    # non-register group untouched
    assert groups["OTHER-SERVICE"]["proxies"] == ["SHOULD-STAY", "ALSO-STAY"]
    print("PASS select_group_keeps_curated_healthy_preserving_order")


def test_select_group_all_curated_dead_falls_back_to_healthy_pool() -> None:
    mod = _load()
    data = _call_rewrite(
        mod,
        _GROUPS,
        healthy=["FRESH-OTHER", "SUB-US-Dedicated-B1-2"],
        preferred_first=["FRESH-OTHER"],
    )
    groups = {g["name"]: g for g in data["proxy-groups"]}
    reg = groups["🎯Grok注册"]["proxies"]
    # everything curated was dead -> ordered healthy pool (preferred first)
    assert reg == ["FRESH-OTHER", "SUB-US-Dedicated-B1-2"], reg
    print("PASS select_group_all_curated_dead_falls_back_to_healthy_pool")


def test_url_test_group_uses_healthy_first_pool() -> None:
    mod = _load()
    data = _call_rewrite(
        mod,
        _GROUPS,
        healthy=["GL-Fast-B1-6", "FRESH-OTHER"],
        preferred_first=["FRESH-OTHER"],
    )
    groups = {g["name"]: g for g in data["proxy-groups"]}
    # url-test: preferred_first front, then remaining healthy
    assert groups["♻️Grok优选"]["proxies"] == [
        "FRESH-OTHER",
        "GL-Fast-B1-6",
    ], groups["♻️Grok优选"]["proxies"]
    assert groups["🔰ChatGPT"]["proxies"] == [
        "FRESH-OTHER",
        "GL-Fast-B1-6",
    ], groups["🔰ChatGPT"]["proxies"]
    print("PASS url_test_group_uses_healthy_first_pool")


def test_select_fallback_reappends_preferred_markers_when_in_old() -> None:
    mod = _load()
    yaml_txt = """
proxy-groups:
  - name: 🎯Grok注册
    type: select
    proxies: [DEAD-1, ♻️Grok优选, DIRECT]
"""
    data = _call_rewrite(mod, yaml_txt, healthy=["FRESH-OTHER"])
    groups = {g["name"]: g for g in data["proxy-groups"]}
    reg = groups["🎯Grok注册"]["proxies"]
    # fallback: ordered healthy pool + ♻️Grok优选/DIRECT preserved from old
    assert reg == ["FRESH-OTHER", "♻️Grok优选", "DIRECT"], reg
    print("PASS select_fallback_reappends_preferred_markers_when_in_old")


if __name__ == "__main__":
    test_select_group_keeps_curated_healthy_preserving_order()
    test_select_group_all_curated_dead_falls_back_to_healthy_pool()
    test_url_test_group_uses_healthy_first_pool()
    test_select_fallback_reappends_preferred_markers_when_in_old()
    print("\nALL PROBE_REWRITE TESTS PASSED")