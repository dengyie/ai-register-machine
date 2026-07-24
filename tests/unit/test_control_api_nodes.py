"""Catalog + Clash node ops for control plane."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from apps.control_api import nodes_ops


def test_catalog_add_list_delete(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("REGISTER_NODES_FILE", raising=False)
    monkeypatch.delenv("NODES_FILE", raising=False)

    added = nodes_ops.add_catalog_node(
        tmp_path,
        url="http://user:pass@10.0.0.1:8080",
        label="lab1",
        tags=["dc"],
        tier=0,
    )
    assert added["ok"] is True
    nid = added["node"]["id"]

    listing = nodes_ops.list_catalog(tmp_path, page_size=0)
    assert listing["total"] == 1
    assert listing["enabled"] == 1
    assert listing["nodes"][0]["label"] == "lab1"
    assert listing["nodes"][0]["tier"] == 0

    # duplicate rejected
    dup = nodes_ops.add_catalog_node(tmp_path, url="http://user:pass@10.0.0.1:8080")
    assert dup["ok"] is False
    assert dup["error"] == "duplicate"

    toggled = nodes_ops.set_catalog_enabled(tmp_path, nid, False)
    assert toggled["ok"] is True
    assert toggled["node"]["enabled"] is False

    deleted = nodes_ops.delete_catalog_node(tmp_path, nid)
    assert deleted["ok"] is True
    assert deleted["total"] == 0


def test_catalog_filter_and_paginate(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("REGISTER_NODES_FILE", raising=False)
    monkeypatch.delenv("NODES_FILE", raising=False)
    for i in range(5):
        nodes_ops.add_catalog_node(
            tmp_path,
            url=f"http://10.0.0.{i}:8080",
            label=f"node-{i}",
            tags=["dc"] if i % 2 == 0 else ["res"],
            tier=0 if i % 2 == 0 else 1,
        )
    page1 = nodes_ops.list_catalog(tmp_path, page=1, page_size=2, sort="label")
    assert page1["total"] == 5
    assert page1["filtered"] == 5
    assert page1["page"] == 1
    assert page1["page_size"] == 2
    assert page1["pages"] == 3
    assert len(page1["nodes"]) == 2
    assert page1["nodes"][0]["label"] == "node-0"

    q = nodes_ops.list_catalog(tmp_path, q="node-3", page_size=50)
    assert q["filtered"] == 1
    assert q["nodes"][0]["label"] == "node-3"

    tier1 = nodes_ops.list_catalog(tmp_path, tier=1, page_size=50)
    assert tier1["filtered"] == 2
    assert all(n["tier"] == 1 for n in tier1["nodes"])


def test_test_catalog_nodes_persists_probe(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    nodes_ops.add_catalog_node(tmp_path, url="socks5://1.2.3.4:1080", label="p1")

    def fake_probe(n, timeout=12.0):
        n.last_ok = True
        n.last_ms = 42
        n.last_ip = "9.9.9.9"
        n.last_error = ""
        return {"ok": True, "id": n.id, "ms": 42, "ip": "9.9.9.9"}

    monkeypatch.setattr(nodes_ops, "probe_node", fake_probe)
    out = nodes_ops.test_catalog_nodes(tmp_path, limit=10)
    assert out["tested"] == 1
    assert out["healthy"] == 1
    listing = nodes_ops.list_catalog(tmp_path)
    assert listing["healthy"] == 1
    assert listing["nodes"][0]["health"] == "ok"
    assert listing["nodes"][0]["last_ms"] == 42


def test_list_clash_nodes_parses_groups_and_scores(tmp_path: Path, monkeypatch):
    scores = {
        "nodes": {
            "HK-01": {"score": 1.25, "cool_until": None, "success": 3, "fail": 0},
        }
    }
    (tmp_path / "output").mkdir()
    score_path = tmp_path / "output" / "node_scores.json"
    score_path.write_text(json.dumps(scores), encoding="utf-8")
    # Hermetic: suite may leave NODE_SCORE_PATH pointing elsewhere.
    monkeypatch.setenv("NODE_SCORE_PATH", str(score_path))

    def fake_req(path, *, secret, method="GET", body=None, timeout=8.0):
        assert path == "/proxies"
        return {
            "proxies": {
                "🎯Grok注册": {
                    "type": "Selector",
                    "now": "HK-01",
                    "all": ["HK-01", "US-02"],
                },
                "HK-01": {
                    "type": "ss",
                    "udp": True,
                    "history": [{"delay": 88}],
                },
                "US-02": {
                    "type": "vmess",
                    "history": [{"delay": 0}],
                },
                "DIRECT": {"type": "Direct"},
            }
        }

    monkeypatch.setattr(nodes_ops, "_clash_request", fake_req)
    monkeypatch.setattr(nodes_ops, "_clash_secret", lambda root: "sec")
    out = nodes_ops.list_clash_nodes(tmp_path)
    assert out["ok"] is True
    assert out["leaf_count"] == 2
    leaves = {x["name"]: x for x in out["leaves"]}
    assert leaves["HK-01"]["health"] == "ok"
    assert leaves["HK-01"]["priority_score"] == 1.25
    assert leaves["HK-01"]["in_register_pool"] is True
    assert leaves["US-02"]["health"] == "fail"
    assert any(g["name"] == "🎯Grok注册" and g["register_relevant"] for g in out["groups"])


def test_test_clash_nodes_delay(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        nodes_ops,
        "list_clash_nodes",
        lambda root: {
            "ok": True,
            "leaves": [
                {"name": "A", "in_register_pool": True},
                {"name": "B", "in_register_pool": False},
            ],
        },
    )
    monkeypatch.setattr(nodes_ops, "_clash_secret", lambda root: "s")

    def fake_req(path, *, secret, method="GET", body=None, timeout=8.0):
        if path.startswith("/proxies/A/delay"):
            return {"delay": 120}
        if path.startswith("/proxies/B/delay"):
            return {"delay": 0}
        return {}

    monkeypatch.setattr(nodes_ops, "_clash_request", fake_req)
    out = nodes_ops.test_clash_nodes(tmp_path, names=["A", "B"])
    assert out["tested"] == 2
    assert out["healthy"] == 1
    by = {r["name"]: r for r in out["results"]}
    assert by["A"]["ok"] is True
    assert by["A"]["delay_ms"] == 120
    assert by["B"]["ok"] is False


def test_parse_subscription_base64_uri_and_meta_filter():
    import base64

    lines = "\n".join(
        [
            "ss://YWVzLTI1Ni1nY206cGFzcw@1.2.3.4:8388#HK-01",
            "vless://11111111-1111-1111-1111-111111111111@5.6.7.8:443?type=ws&security=tls&sni=a.com#US-02",
            "hysteria2://secret@9.9.9.9:443?sni=hy.example&insecure=1#HY-03",
            "ss://YWVzLTI1Ni1nY206cGFzcw@1.2.3.4:8389#剩余流量：99GB",
            "ss://YWVzLTI1Ni1nY206cGFzcw@1.2.3.4:8390#套餐到期：2099",
        ]
    )
    body = base64.b64encode(lines.encode("utf-8")).decode("ascii")
    proxies, info = nodes_ops.parse_subscription_proxies(body, source="test", prefix="SUB")
    assert info["base64"] is True
    assert info["format"] == "uri_list"
    assert info["skipped_meta"] >= 2
    assert info["imported"] == 3
    names = [p["name"] for p in proxies]
    assert all(n.startswith("SUB-") for n in names)
    types = {p["type"] for p in proxies}
    assert "ss" in types
    assert "vless" in types
    assert "hysteria2" in types


def test_merge_proxies_replace_prefix(tmp_path: Path, monkeypatch):
    yaml = nodes_ops._require_yaml()
    cfg = tmp_path / "config.yaml"
    data = {
        "proxies": [
            {"name": "KEEP-1", "type": "ss", "server": "1.1.1.1", "port": 1, "cipher": "aes-256-gcm", "password": "x"},
            {"name": "SUB-old", "type": "ss", "server": "2.2.2.2", "port": 2, "cipher": "aes-256-gcm", "password": "x"},
        ],
        "proxy-groups": [
            {"name": "🎯Grok注册", "type": "select", "proxies": ["KEEP-1", "SUB-old", "DIRECT"]},
        ],
    }
    cfg.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    new_proxies = [
        {"name": "SUB-new", "type": "ss", "server": "3.3.3.3", "port": 3, "cipher": "aes-256-gcm", "password": "y"},
    ]
    out = nodes_ops.merge_proxies_into_clash_config(
        cfg,
        new_proxies,
        groups=["🎯Grok注册"],
        mode="replace_prefix",
        prefix="SUB",
    )
    assert out["ok"] is True
    assert out["removed_prefix"] == 1
    assert out["added"] == 1
    loaded = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    names = [p["name"] for p in loaded["proxies"]]
    assert "KEEP-1" in names
    assert "SUB-new" in names
    assert "SUB-old" not in names
    group = next(g for g in loaded["proxy-groups"] if g["name"] == "🎯Grok注册")
    assert "SUB-new" in group["proxies"]
    assert "SUB-old" not in group["proxies"]
    assert "KEEP-1" in group["proxies"]


def test_import_clash_subscription_dry_run(tmp_path: Path, monkeypatch):
    import base64

    body = base64.b64encode(
        b"ss://YWVzLTI1Ni1nY206cGFzcw@1.2.3.4:8388#HK-dry\n"
    ).decode("ascii")
    monkeypatch.setattr(
        nodes_ops,
        "fetch_subscription_body",
        lambda url, timeout=25.0: (body, {"http_status": 200, "bytes": len(body)}),
    )
    monkeypatch.setattr(nodes_ops, "clash_config_paths", lambda: [])
    out = nodes_ops.import_clash_subscription(
        tmp_path,
        url="https://example.com/sub/abc",
        group="🎯Grok注册",
        prefix="SUB",
        mode="merge",
        dry_run=True,
        reload=False,
    )
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert out["parse"]["imported"] == 1
    assert "预检成功" in out["message"]


def test_import_clash_subscription_fetch_error(tmp_path: Path, monkeypatch):
    def boom(url, timeout=25.0):
        raise RuntimeError("fetch failed: timeout")

    monkeypatch.setattr(nodes_ops, "fetch_subscription_body", boom)
    out = nodes_ops.import_clash_subscription(
        tmp_path,
        url="https://example.com/sub/bad",
        dry_run=True,
    )
    assert out["ok"] is False
    assert out["stage"] == "fetch"
    assert out["error"] == "fetch_failed"


def test_remove_proxies_by_prefix_from_config(tmp_path: Path):
    yaml = nodes_ops._require_yaml()
    cfg = tmp_path / "config.yaml"
    data = {
        "proxies": [
            {"name": "KEEP-1", "type": "ss", "server": "1.1.1.1", "port": 1, "cipher": "aes-256-gcm", "password": "x"},
            {"name": "YF-dead", "type": "ss", "server": "2.2.2.2", "port": 2, "cipher": "aes-256-gcm", "password": "x"},
            {"name": "YF", "type": "ss", "server": "3.3.3.3", "port": 3, "cipher": "aes-256-gcm", "password": "x"},
            {"name": "YFAKE-1", "type": "ss", "server": "4.4.4.4", "port": 4, "cipher": "aes-256-gcm", "password": "x"},
        ],
        "proxy-groups": [
            {
                "name": "🎯Grok注册",
                "type": "select",
                "proxies": ["KEEP-1", "YF-dead", "YF", "YFAKE-1", "DIRECT"],
            },
        ],
    }
    cfg.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    out = nodes_ops.remove_proxies_by_prefix_from_config(cfg, prefix="YF")
    assert out["ok"] is True
    assert out["removed"] == 2
    loaded = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    names = [p["name"] for p in loaded["proxies"]]
    assert "KEEP-1" in names
    assert "YFAKE-1" in names
    assert "YF-dead" not in names
    assert "YF" not in names
    group = next(g for g in loaded["proxy-groups"] if g["name"] == "🎯Grok注册")
    assert "YF-dead" not in group["proxies"]
    assert "KEEP-1" in group["proxies"]
    assert "YFAKE-1" in group["proxies"]


def test_delete_clash_prefix_dry_run_and_write(tmp_path: Path, monkeypatch):
    yaml = nodes_ops._require_yaml()
    cfg = tmp_path / "config.yaml"
    data = {
        "proxies": [
            {"name": "KEEP-1", "type": "ss", "server": "1.1.1.1", "port": 1, "cipher": "aes-256-gcm", "password": "x"},
            {"name": "BP-1", "type": "ss", "server": "2.2.2.2", "port": 2, "cipher": "aes-256-gcm", "password": "x"},
        ],
        "proxy-groups": [
            {"name": "🎯Grok注册", "type": "select", "proxies": ["KEEP-1", "BP-1"]},
        ],
    }
    cfg.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(nodes_ops, "clash_config_paths", lambda: [cfg])

    dry = nodes_ops.delete_clash_prefix(tmp_path, prefix="BP", dry_run=True, reload=False)
    assert dry["ok"] is True
    assert dry["dry_run"] is True
    assert dry["would_remove"] == 1
    # file untouched
    assert "BP-1" in [p["name"] for p in yaml.safe_load(cfg.read_text())["proxies"]]

    real = nodes_ops.delete_clash_prefix(tmp_path, prefix="BP", dry_run=False, reload=False)
    assert real["ok"] is True
    assert real["removed"] == 1
    loaded = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    names = [p["name"] for p in loaded["proxies"]]
    assert "BP-1" not in names
    assert "KEEP-1" in names


def test_delete_clash_prefix_protected_without_force(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(nodes_ops, "clash_config_paths", lambda: [tmp_path / "config.yaml"])
    out = nodes_ops.delete_clash_prefix(tmp_path, prefix="GVPS", dry_run=False, force=False)
    assert out["ok"] is False
    assert out["error"] == "protected_prefix"


def test_prune_clash_unhealthy_dry_run(tmp_path: Path, monkeypatch):
    yaml = nodes_ops._require_yaml()
    cfg = tmp_path / "config.yaml"
    data = {
        "proxies": [
            {"name": "A-1", "type": "ss", "server": "1.1.1.1", "port": 1, "cipher": "aes-256-gcm", "password": "x"},
            {"name": "A-2", "type": "ss", "server": "2.2.2.2", "port": 2, "cipher": "aes-256-gcm", "password": "x"},
        ],
        "proxy-groups": [
            {"name": "🎯Grok注册", "type": "select", "proxies": ["A-1", "A-2", "DIRECT"]},
        ],
    }
    cfg.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(nodes_ops, "clash_config_paths", lambda: [cfg])

    def fake_test(root, *, names=None, timeout_ms=5000, limit=40, url=None):
        return {
            "ok": True,
            "tested": 2,
            "healthy": 1,
            "results": [
                {"name": "A-1", "ok": True, "delay_ms": 100},
                {"name": "A-2", "ok": False, "delay_ms": None, "error": "timeout"},
            ],
        }

    monkeypatch.setattr(nodes_ops, "test_clash_nodes", fake_test)
    out = nodes_ops.prune_clash_unhealthy(
        tmp_path,
        prefix="A",
        dry_run=True,
        reload=False,
        delete_defs=True,
    )
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert out["unhealthy"] == 1
    assert out["would_remove"] == 1
    # still present
    names = [p["name"] for p in yaml.safe_load(cfg.read_text())["proxies"]]
    assert "A-2" in names

    real = nodes_ops.prune_clash_unhealthy(
        tmp_path,
        prefix="A",
        dry_run=False,
        reload=False,
        delete_defs=True,
    )
    assert real["ok"] is True
    assert real["removed"] == 1
    names2 = [p["name"] for p in yaml.safe_load(cfg.read_text())["proxies"]]
    assert "A-1" in names2
    assert "A-2" not in names2
