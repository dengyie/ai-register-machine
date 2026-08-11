"""Mint egress hold: keep clash leaf pinned to registration node during mint."""
from __future__ import annotations

import proxy_rotate as pr


class _FakeRotatorAPI:
    """Minimal stubs for clash_list_nodes / clash_switch_node via monkeypatch."""

    def __init__(self, nodes: list[str], now: str) -> None:
        self.nodes = list(nodes)
        self.now = now
        self.switches: list[str] = []

    def list_nodes(self, *a, **k):
        return list(self.nodes), self.now, {}

    def switch(self, api, group, node, **k):
        self.switches.append(node)
        self.now = node


def test_acquire_release_blocks_rotate(monkeypatch) -> None:
    fake = _FakeRotatorAPI(
        ["node-A", "node-B", "node-C"],
        now="node-A",
    )
    rot = pr.ProxyRotator()
    rot.mode = "clash"
    rot.clash_setup_done = True
    rot.clash_group = "🎯Grok注册"
    rot.clash_flush = False
    rot.current_label = "node-A"
    rot._started = True
    rot.accounts_on_current = 1

    monkeypatch.setattr(pr, "clash_list_nodes", fake.list_nodes)
    monkeypatch.setattr(pr, "clash_switch_node", fake.switch)

    hold = rot.acquire_mint_egress("node-A")
    assert hold.get("ok") is True
    assert hold.get("holds", {}).get("node-A") == 1

    # force rotate must stay on held leaf
    r = rot.maybe_rotate(force=True)
    assert r.get("reason") == "mint_hold"
    assert r.get("node") == "node-A"
    assert fake.switches == []  # already on A

    rel = rot.release_mint_egress("node-A")
    assert rel.get("holds") == {}

    # after release, force rotate advances
    r2 = rot.maybe_rotate(force=True)
    assert r2.get("rotated") is True
    assert r2.get("node") in {"node-B", "node-C"}
    assert fake.now == r2.get("node")


def test_acquire_switches_to_reg_leaf(monkeypatch) -> None:
    fake = _FakeRotatorAPI(["node-A", "node-B"], now="node-B")
    rot = pr.ProxyRotator()
    rot.mode = "clash"
    rot.clash_setup_done = True
    rot.clash_group = "🎯Grok注册"
    rot.clash_flush = False
    rot.current_label = "node-B"

    monkeypatch.setattr(pr, "clash_list_nodes", fake.list_nodes)
    monkeypatch.setattr(pr, "clash_switch_node", fake.switch)

    hold = rot.acquire_mint_egress("node-A")
    assert hold.get("ok") is True
    assert hold.get("pinned") is True
    assert fake.switches == ["node-A"]
    assert rot.current_label == "node-A"
    assert fake.now == "node-A"

    rot.release_mint_egress("node-A")


def test_refcount_multi_hold(monkeypatch) -> None:
    fake = _FakeRotatorAPI(["node-A", "node-B"], now="node-A")
    rot = pr.ProxyRotator()
    rot.mode = "clash"
    rot.clash_setup_done = True
    rot.clash_group = "G"
    rot.clash_flush = False

    monkeypatch.setattr(pr, "clash_list_nodes", fake.list_nodes)
    monkeypatch.setattr(pr, "clash_switch_node", fake.switch)

    rot.acquire_mint_egress("node-A")
    rot.acquire_mint_egress("node-A")
    assert rot.status()["mint_holds"]["node-A"] == 2
    rot.release_mint_egress("node-A")
    assert rot.status()["mint_holds"]["node-A"] == 1
    r = rot.maybe_rotate(force=True)
    assert r.get("reason") == "mint_hold"
    rot.release_mint_egress("node-A")
    assert rot.status()["mint_holds"] == {}


def test_mode_list_is_noop() -> None:
    rot = pr.ProxyRotator()
    rot.mode = "list"
    hold = rot.acquire_mint_egress("http://1.2.3.4:8080")
    assert hold.get("ok") is True
    assert hold.get("pinned") is False
    assert hold.get("reason") == "mode_not_clash"
