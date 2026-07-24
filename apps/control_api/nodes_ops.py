"""Node catalog + Clash leaf ops for the control plane UI."""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from register_core.nodes.catalog import load_nodes, save_nodes
from register_core.nodes.health import probe_node
from register_core.nodes.models import Node, node_from_dict


def catalog_path(root: Path) -> Path:
    env = (
        os.environ.get("REGISTER_NODES_FILE")
        or os.environ.get("NODES_FILE")
        or ""
    ).strip()
    if env:
        p = Path(os.path.expanduser(env))
        if not p.is_absolute():
            p = root / p
        return p
    for name in ("nodes.json", "nodes.txt", "nodes.list", "proxy_list.txt"):
        p = root / name
        if p.is_file():
            return p
    return root / "nodes.json"


def _load_scores(root: Path) -> dict[str, Any]:
    path = Path(
        os.environ.get("NODE_SCORE_PATH")
        or str(root / "output" / "node_scores.json")
    )
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    nodes = data.get("nodes") if isinstance(data, dict) else None
    return nodes if isinstance(nodes, dict) else {}


def _public_node(n: Node, scores: dict[str, Any] | None = None) -> dict[str, Any]:
    d = n.to_public_dict()
    d["tier"] = int(getattr(n, "tier", 0) or 0)
    d["attempt_count"] = int(getattr(n, "attempt_count", 0) or 0)
    d["success_count"] = int(getattr(n, "success_count", 0) or 0)
    d["disallow_count"] = int(getattr(n, "disallow_count", 0) or 0)
    try:
        d["quality_score"] = round(float(n.quality_score()), 3)
    except Exception:
        d["quality_score"] = None
    # score store may key by clash leaf name / label
    sc = scores or {}
    for key in (n.label, n.id, getattr(n, "url", "")):
        if key and key in sc and isinstance(sc[key], dict):
            ent = sc[key]
            d["priority_score"] = ent.get("score")
            d["cool_until"] = ent.get("cool_until") or ent.get("cooldown_until")
            d["score_meta"] = {
                k: ent.get(k)
                for k in ("score", "success", "fail", "cool_until", "last_reason")
                if k in ent
            }
            break
    else:
        d["priority_score"] = d.get("quality_score")
    # health badge
    if n.last_ok is True:
        d["health"] = "ok"
    elif n.last_ok is False:
        d["health"] = "fail"
    else:
        d["health"] = "unknown"
    return d


def list_catalog(
    root: Path,
    *,
    q: str = "",
    health: str = "",
    tier: str | int | None = None,
    enabled: str | bool | None = None,
    page: int = 1,
    page_size: int = 50,
    sort: str = "priority",
) -> dict[str, Any]:
    """List catalog nodes with optional filter/pagination.

    Without filters page defaults still return a page slice so UIs don't
    render 5k rows at once. Pass page_size<=0 to get the full list (tests).
    """
    path = catalog_path(root)
    nodes = load_nodes(path)
    scores = _load_scores(root)
    public = [_public_node(n, scores) for n in nodes]
    ok_n = sum(1 for n in nodes if n.last_ok is True and n.enabled)
    en_n = sum(1 for n in nodes if n.enabled)
    fail_n = sum(1 for n in nodes if n.last_ok is False)
    unknown_n = sum(1 for n in nodes if n.last_ok is None)

    filtered = public
    q_norm = (q or "").strip().lower()
    if q_norm:
        filtered = [
            n
            for n in filtered
            if q_norm in (n.get("label") or "").lower()
            or q_norm in (n.get("id") or "").lower()
            or q_norm in " ".join(n.get("tags") or []).lower()
            or q_norm in (n.get("last_ip") or "").lower()
        ]
    h = (health or "").strip().lower()
    if h in {"ok", "fail", "unknown"}:
        filtered = [n for n in filtered if (n.get("health") or "unknown") == h]
    if tier is not None and str(tier).strip() != "":
        try:
            t = int(tier)
            filtered = [n for n in filtered if int(n.get("tier") or 0) == t]
        except (TypeError, ValueError):
            pass
    if enabled is not None and str(enabled).strip() != "":
        want = str(enabled).strip().lower() in {"1", "true", "yes", "on"}
        filtered = [n for n in filtered if bool(n.get("enabled")) is want]

    sort_key = (sort or "priority").strip().lower()
    if sort_key == "ms":
        filtered = sorted(
            filtered,
            key=lambda n: (
                n.get("last_ms") is None,
                n.get("last_ms") if n.get("last_ms") is not None else 10**9,
            ),
        )
    elif sort_key == "label":
        filtered = sorted(filtered, key=lambda n: (n.get("label") or n.get("id") or "").lower())
    else:
        # priority: healthy first, then higher score, then lower latency
        def _prio(n: dict[str, Any]) -> tuple:
            health_rank = {"ok": 0, "unknown": 1, "fail": 2}.get(n.get("health") or "unknown", 1)
            score = n.get("priority_score")
            try:
                score_v = -float(score) if score is not None else 0.0
            except (TypeError, ValueError):
                score_v = 0.0
            ms = n.get("last_ms")
            ms_v = int(ms) if isinstance(ms, (int, float)) else 10**9
            return (health_rank, score_v, ms_v, (n.get("label") or ""))

        filtered = sorted(filtered, key=_prio)

    total_filtered = len(filtered)
    try:
        page_i = max(1, int(page or 1))
    except (TypeError, ValueError):
        page_i = 1
    try:
        size_i = int(page_size if page_size is not None else 50)
    except (TypeError, ValueError):
        size_i = 50
    if size_i <= 0:
        page_nodes = filtered
        pages = 1 if total_filtered else 0
        page_i = 1
        size_i = total_filtered or 0
    else:
        size_i = min(max(size_i, 1), 500)
        pages = max(1, (total_filtered + size_i - 1) // size_i) if total_filtered else 0
        if pages and page_i > pages:
            page_i = pages
        start = (page_i - 1) * size_i
        page_nodes = filtered[start : start + size_i]

    return {
        "path": str(path),
        "exists": path.is_file(),
        "total": len(nodes),
        "enabled": en_n,
        "healthy": ok_n,
        "fail": fail_n,
        "unknown": unknown_n,
        "filtered": total_filtered,
        "page": page_i,
        "page_size": size_i,
        "pages": pages,
        "nodes": page_nodes,
    }


def add_catalog_node(
    root: Path,
    *,
    url: str,
    label: str = "",
    tags: list[str] | None = None,
    tier: int = 0,
    enabled: bool = True,
) -> dict[str, Any]:
    url = (url or "").strip()
    if not url:
        raise ValueError("url required")
    path = catalog_path(root)
    nodes = load_nodes(path)
    if any(n.url == url for n in nodes):
        return {"ok": False, "error": "duplicate", "path": str(path)}
    node = Node(
        url=url,
        label=label or "",
        tags=list(tags or []),
        tier=int(tier or 0),
        enabled=bool(enabled),
    )
    nodes.append(node)
    save_nodes(nodes, path)
    return {"ok": True, "path": str(path), "node": _public_node(node)}


def delete_catalog_node(root: Path, node_id: str) -> dict[str, Any]:
    node_id = (node_id or "").strip()
    if not node_id:
        raise ValueError("id required")
    path = catalog_path(root)
    nodes = load_nodes(path)
    before = len(nodes)
    kept = [n for n in nodes if n.id != node_id and n.label != node_id]
    # also allow delete by exact url match id confusion
    if len(kept) == before:
        kept = [n for n in nodes if n.url != node_id]
    if len(kept) == before:
        return {"ok": False, "error": "not_found", "id": node_id, "path": str(path)}
    save_nodes(kept, path)
    return {"ok": True, "removed": before - len(kept), "path": str(path), "total": len(kept)}


def set_catalog_enabled(root: Path, node_id: str, enabled: bool) -> dict[str, Any]:
    path = catalog_path(root)
    nodes = load_nodes(path)
    hit = None
    for n in nodes:
        if n.id == node_id or n.label == node_id:
            n.enabled = bool(enabled)
            hit = n
            break
    if hit is None:
        return {"ok": False, "error": "not_found", "id": node_id}
    save_nodes(nodes, path)
    return {"ok": True, "node": _public_node(hit)}


def test_catalog_nodes(
    root: Path,
    *,
    ids: list[str] | None = None,
    timeout: float = 12.0,
    limit: int = 50,
) -> dict[str, Any]:
    path = catalog_path(root)
    nodes = load_nodes(path)
    if ids:
        idset = set(ids)
        targets = [n for n in nodes if n.id in idset or n.label in idset]
    else:
        targets = [n for n in nodes if n.enabled][: max(1, int(limit))]
    results: list[dict[str, Any]] = []
    for n in targets:
        results.append(probe_node(n, timeout=float(timeout)))
    # persist health stamps
    try:
        save_nodes(nodes, path)
    except Exception:
        pass
    ok_n = sum(1 for r in results if r.get("ok"))
    return {
        "ok": ok_n > 0 or not results,
        "tested": len(results),
        "healthy": ok_n,
        "results": results,
        "path": str(path),
    }


# ── Clash / mihomo controller leaves ──────────────────────────────────────

_GROUP_TYPES = {
    "Selector",
    "URLTest",
    "Fallback",
    "LoadBalance",
    "Relay",
    "Direct",
    "Reject",
    "Compatible",
    "Pass",
    "PassRule",
    "RejectDrop",
}

REGISTER_GROUPS = ("🎯Grok注册", "♻️Grok优选", "PROXY", "🔰ChatGPT", "GROK-REG")


def _clash_api() -> str:
    return (os.environ.get("CLASH_API") or "http://127.0.0.1:9090").rstrip("/")


def _clash_secret(root: Path) -> str:
    env = (os.environ.get("CLASH_SECRET") or "").strip()
    if env:
        return env
    clash_dir = Path(os.environ.get("CLASH_DIR") or "/personal/clash")
    for cand in (
        clash_dir / ".controller-secret",
        root / ".clash_secret",
        Path("/personal/clash/.controller-secret"),
    ):
        if cand.is_file():
            s = cand.read_text(encoding="utf-8").strip()
            if s:
                return s
    cfg = Path(os.environ.get("CLASH_CONFIG") or (clash_dir / "config.yaml"))
    if cfg.is_file():
        text = cfg.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'^secret:\s*["\']?([^"\'\n]+)', text, re.M)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return ""


def _clash_request(
    path: str,
    *,
    secret: str,
    method: str = "GET",
    body: dict | None = None,
    timeout: float = 8.0,
) -> Any:
    import urllib.error
    import urllib.request

    url = f"{_clash_api()}{path}"
    data = None
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    if body is not None:
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            detail = str(e)
        raise RuntimeError(f"clash HTTP {e.code}: {detail}") from e
    except Exception as e:
        raise RuntimeError(f"clash unreachable: {type(e).__name__}: {e}") from e


def list_clash_nodes(root: Path) -> dict[str, Any]:
    secret = _clash_secret(root)
    try:
        data = _clash_request("/proxies", secret=secret)
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "api": _clash_api(),
            "leaves": [],
            "groups": [],
        }
    proxies = data.get("proxies") if isinstance(data, dict) else {}
    if not isinstance(proxies, dict):
        return {"ok": False, "error": "bad proxies map", "leaves": [], "groups": []}

    scores = _load_scores(root)
    leaves: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    for name, info in proxies.items():
        if not isinstance(info, dict):
            continue
        t = str(info.get("type") or "")
        if t in _GROUP_TYPES:
            all_names = list(info.get("all") or [])
            groups.append(
                {
                    "name": name,
                    "type": t,
                    "now": info.get("now") or "",
                    "count": len(all_names),
                    "register_relevant": name in REGISTER_GROUPS
                    or "Grok" in name
                    or "GROK" in name
                    or name in {"PROXY"},
                }
            )
            continue
        if name in ("DIRECT", "REJECT", "PASS", "COMPATIBLE"):
            continue
        hist = info.get("history") or []
        last_delay = None
        last_ok = None
        if isinstance(hist, list) and hist:
            last = hist[-1] if isinstance(hist[-1], dict) else {}
            d = last.get("delay")
            if isinstance(d, int):
                last_delay = d
                last_ok = d > 0
        sc = scores.get(name) if isinstance(scores.get(name), dict) else {}
        leaves.append(
            {
                "name": name,
                "type": t,
                "udp": bool(info.get("udp")),
                "last_delay_ms": last_delay,
                "last_ok": last_ok,
                "health": "ok" if last_ok is True else ("fail" if last_ok is False else "unknown"),
                "priority_score": sc.get("score"),
                "cool_until": sc.get("cool_until") or sc.get("cooldown_until"),
                "score_meta": sc or None,
            }
        )

    # mark membership in register groups
    group_members: dict[str, set[str]] = {}
    for g in groups:
        info = proxies.get(g["name"]) or {}
        group_members[g["name"]] = set(info.get("all") or [])
    for leaf in leaves:
        mem = [g for g, members in group_members.items() if leaf["name"] in members]
        leaf["groups"] = mem
        leaf["in_register_pool"] = any(
            g in REGISTER_GROUPS or "Grok" in g or "GROK" in g for g in mem
        )

    leaves.sort(key=lambda x: (0 if x.get("in_register_pool") else 1, x["name"]))
    groups.sort(key=lambda x: (0 if x.get("register_relevant") else 1, x["name"]))
    return {
        "ok": True,
        "api": _clash_api(),
        "secret_configured": bool(secret),
        "leaf_count": len(leaves),
        "group_count": len(groups),
        "leaves": leaves,
        "groups": groups,
        "register_groups": list(REGISTER_GROUPS),
    }


def test_clash_nodes(
    root: Path,
    *,
    names: list[str] | None = None,
    timeout_ms: int = 5000,
    limit: int = 40,
    url: str = "http://www.gstatic.com/generate_204",
    workers: int = 24,
) -> dict[str, Any]:
    """Delay-test Clash leaves.

    Probes run in a small thread pool — serial tests of 100+ nodes easily exceed
    Cloudflare's ~100s origin timeout (HTTP 524) when the console is proxied.
    """
    import urllib.parse

    secret = _clash_secret(root)
    listing = list_clash_nodes(root)
    if not listing.get("ok"):
        return listing
    if names:
        targets = list(names)
    else:
        # Prefer register-pool leaves, then others
        leaves = listing.get("leaves") or []
        pool = [x["name"] for x in leaves if x.get("in_register_pool")]
        rest = [x["name"] for x in leaves if not x.get("in_register_pool")]
        targets = (pool + rest)[: max(1, int(limit))]

    req_timeout = max(3.0, float(timeout_ms) / 1000.0 + 2.0)
    t_all = time.time()

    def _one(name: str) -> dict[str, Any]:
        q = urllib.parse.urlencode({"timeout": int(timeout_ms), "url": url})
        path = f"/proxies/{urllib.parse.quote(name, safe='')}/delay?{q}"
        t0 = time.time()
        try:
            data = _clash_request(path, secret=secret, timeout=req_timeout)
            delay = data.get("delay") if isinstance(data, dict) else None
            ok = isinstance(delay, int) and delay > 0
            return {
                "name": name,
                "ok": ok,
                "delay_ms": delay if ok else None,
                "error": "" if ok else f"no_delay:{data!r}"[:160],
                "ms": int((time.time() - t0) * 1000),
            }
        except Exception as e:
            return {
                "name": name,
                "ok": False,
                "delay_ms": None,
                "error": str(e)[:160],
                "ms": int((time.time() - t0) * 1000),
            }

    results: list[dict[str, Any]] = []
    if not targets:
        results = []
    elif len(targets) == 1:
        results = [_one(targets[0])]
    else:
        n_workers = max(1, min(int(workers) if workers else 24, len(targets), 32))
        by_name: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futs = {ex.submit(_one, n): n for n in targets}
            for fut in as_completed(futs):
                r = fut.result()
                by_name[str(r.get("name") or "")] = r
        # preserve request order for stable UI / tests
        results = [by_name[n] for n in targets if n in by_name]

    ok_n = sum(1 for r in results if r.get("ok"))
    return {
        "ok": True,
        "tested": len(results),
        "healthy": ok_n,
        "results": results,
        "api": _clash_api(),
        "workers": min(int(workers) if workers else 24, max(1, len(targets)), 32) if targets else 0,
        "ms": int((time.time() - t_all) * 1000),
    }


# ── Clash subscription URL → selected pool ───────────────────────────────────

# Groups the UI may target for subscription import (register-relevant first).
IMPORTABLE_GROUPS = (
    "🎯Grok注册",
    "♻️Grok优选",
    "PROXY",
    "🔰ChatGPT",
    "GROK-REG",
)

_META_NAME_MARKERS = (
    "剩余流量",
    "距离下次",
    "套餐到期",
    "官网",
    "到期",
    "流量",
    "重置",
    "订阅",
    "expire",
    "traffic",
    "reset",
    "website",
    "官网地址",
)


def _clash_dir() -> Path:
    return Path(os.environ.get("CLASH_DIR") or "/personal/clash")


def clash_config_paths() -> list[Path]:
    """Configs that should receive imported proxies.

    Default write set is live config + mac-merged source only (fast path).
    Set CLASH_IMPORT_ALL_CONFIGS=1 to also touch mac-sync / grok-register mirrors.
    """
    clash_dir = _clash_dir()
    env_cfg = (os.environ.get("CLASH_CONFIG") or "").strip()
    env_merged = (os.environ.get("CLASH_MERGED") or "").strip()
    all_cfgs = (os.environ.get("CLASH_IMPORT_ALL_CONFIGS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    cands: list[Path] = []
    if env_cfg:
        cands.append(Path(env_cfg))
    if env_merged:
        cands.append(Path(env_merged))
    names = ["config.yaml", "config.mac-merged.yaml"]
    if all_cfgs:
        names.extend(["config.mac-sync.yaml", "config.grok-register.yaml"])
    for name in names:
        cands.append(clash_dir / name)
    out: list[Path] = []
    seen: set[str] = set()
    for p in cands:
        try:
            key = str(p.resolve()) if p.exists() else str(p)
        except Exception:
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_file():
            out.append(p)
    return out


def _require_yaml():
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML required for Clash subscription import") from exc
    return yaml


def _is_meta_proxy_name(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return True
    for m in _META_NAME_MARKERS:
        if m.lower() in n:
            return True
    return False


def _sanitize_proxy_name(name: str, *, prefix: str, used: set[str], idx: int) -> str:
    base = re.sub(r"\s+", " ", (name or "").strip())
    base = base.replace("\n", " ").replace("\r", "")
    if not base:
        base = f"node-{idx}"
    if prefix:
        # avoid double-prefix
        if not base.startswith(f"{prefix}-") and not base.startswith(prefix):
            base = f"{prefix}-{base}"
    # clash name practical limit
    if len(base) > 64:
        base = base[:64].rstrip()
    candidate = base
    n = 2
    while candidate in used:
        suffix = f"#{n}"
        candidate = (base[: max(1, 64 - len(suffix))] + suffix)
        n += 1
    used.add(candidate)
    return candidate


def _normalize_imported_proxy(
    proxy: dict[str, Any],
    *,
    prefix: str,
    used_names: set[str],
    idx: int,
) -> dict[str, Any] | None:
    if not isinstance(proxy, dict):
        return None
    name = str(proxy.get("name") or "").strip()
    ptype = str(proxy.get("type") or "").strip().lower()
    server = str(proxy.get("server") or "").strip()
    if not ptype or not server:
        return None
    if _is_meta_proxy_name(name):
        return None
    out = dict(proxy)
    out["name"] = _sanitize_proxy_name(name or f"{ptype}-{server}", prefix=prefix, used=used_names, idx=idx)
    out["type"] = ptype
    return out


def _clash_mixed_proxy_url() -> str | None:
    """HTTP proxy URL for mihomo mixed-port (default 127.0.0.1:7897)."""
    raw = (os.environ.get("CLASH_MIXED_PROXY") or "").strip()
    if raw:
        return raw
    host = (os.environ.get("CLASH_MIXED_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = (os.environ.get("CLASH_MIXED_PORT") or os.environ.get("MIXED_PORT") or "7897").strip()
    try:
        p = int(port)
    except Exception:
        p = 7897
    if p <= 0:
        return None
    return f"http://{host}:{p}"


def fetch_subscription_body(url: str, *, timeout: float = 25.0) -> tuple[str, dict[str, Any]]:
    """Fetch subscription URL; return (text, meta). Raises RuntimeError on hard fail.

    Attempts (proxy-first — pxed airport hosts usually need Clash egress):
      1) Clash mixed-port proxy
      2) proxy + unverified SSL (incomplete airport chains)
      3) short direct HTTPS (fallback only; avoids 20s hang when direct is dead)
      4) short direct + unverified SSL

    Each attempt records ms in meta.attempts for ops timing.
    """
    import ssl
    import urllib.error
    import urllib.request

    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        raise RuntimeError("url must start with http:// or https://")
    headers = {
        "User-Agent": "ClashMeta/1.18 (grok-register control plane)",
        "Accept": "*/*",
    }
    req = urllib.request.Request(u, headers=headers, method="GET")
    proxy_url = _clash_mixed_proxy_url()
    # Prefer proxy path; keep direct short so a dead WAN doesn't burn ~20s twice.
    total_timeout = max(5.0, float(timeout))
    proxy_timeout = min(total_timeout, 15.0)
    direct_timeout = min(3.0, total_timeout)
    attempts: list[tuple[str, Any, Any, float]] = []
    if proxy_url:
        attempts.append(("proxy", proxy_url, None, proxy_timeout))
        attempts.append(
            ("proxy_insecure", proxy_url, ssl._create_unverified_context(), proxy_timeout)
        )
    attempts.append(("direct", None, None, direct_timeout))
    attempts.append(
        ("direct_insecure", None, ssl._create_unverified_context(), direct_timeout)
    )

    last_err: Exception | None = None
    used_via = "direct"
    ssl_insecure = False
    raw = b""
    ctype = ""
    status = 0
    userinfo = ""
    attempt_log: list[dict[str, Any]] = []
    t_fetch0 = time.time()

    for via, proxy, ctx, to in attempts:
        t_a = time.time()
        try:
            handlers: list[Any] = []
            if proxy:
                handlers.append(
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy})
                )
            if ctx is not None:
                handlers.append(urllib.request.HTTPSHandler(context=ctx))
            if handlers:
                opener = urllib.request.build_opener(*handlers)
                resp_cm = opener.open(req, timeout=float(to))
            else:
                resp_cm = urllib.request.urlopen(req, timeout=float(to))
            with resp_cm as resp:
                raw = resp.read()
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
                status = getattr(resp, "status", 200) or 200
                userinfo = resp.headers.get("subscription-userinfo") or ""
            used_via = via
            ssl_insecure = "insecure" in via
            last_err = None
            attempt_log.append(
                {
                    "via": via,
                    "ok": True,
                    "ms": int((time.time() - t_a) * 1000),
                    "timeout": to,
                    "bytes": len(raw),
                }
            )
            break
        except urllib.error.HTTPError as e:
            # HTTP error still means we reached the host; surface immediately
            try:
                detail = e.read()[:200]
            except Exception:
                detail = b""
            attempt_log.append(
                {
                    "via": via,
                    "ok": False,
                    "ms": int((time.time() - t_a) * 1000),
                    "timeout": to,
                    "error": f"HTTP {e.code}",
                }
            )
            raise RuntimeError(f"fetch HTTP {e.code}: {detail!r}") from e
        except Exception as e:
            last_err = e
            attempt_log.append(
                {
                    "via": via,
                    "ok": False,
                    "ms": int((time.time() - t_a) * 1000),
                    "timeout": to,
                    "error": f"{type(e).__name__}: {e}"[:160],
                }
            )
            continue

    if last_err is not None:
        raise RuntimeError(f"fetch failed: {type(last_err).__name__}: {last_err}") from last_err

    # try utf-8 then latin-1 fallback for opaque base64 bodies
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")
    meta = {
        "http_status": int(status),
        "content_type": ctype,
        "bytes": len(raw),
        "subscription_userinfo": userinfo,
        "ssl_insecure": ssl_insecure,
        "via": used_via,
        "proxy": proxy_url if used_via.startswith("proxy") else None,
        "ms": int((time.time() - t_fetch0) * 1000),
        "attempts": attempt_log,
    }
    return text, meta


def parse_subscription_proxies(
    body: str,
    *,
    source: str = "",
    prefix: str = "SUB",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse subscription body → clash proxy dicts (meta rows dropped)."""
    from register_core.nodes.convert.parsers import ParseError, decode_subscription_text, parse_text

    decoded, was_b64 = decode_subscription_text(body)
    try:
        fmt, raw_proxies = parse_text(decoded if decoded else body, source=source or "subscription")
    except ParseError as e:
        raise RuntimeError(f"parse failed ({e.format}): {e}") from e

    used: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    skipped_meta = 0
    skipped_bad = 0
    for i, p in enumerate(raw_proxies):
        if not isinstance(p, dict):
            skipped_bad += 1
            continue
        if _is_meta_proxy_name(str(p.get("name") or "")):
            skipped_meta += 1
            continue
        norm = _normalize_imported_proxy(p, prefix=prefix, used_names=used, idx=i + 1)
        if norm is None:
            skipped_bad += 1
            continue
        cleaned.append(norm)
    info = {
        "format": fmt,
        "base64": was_b64,
        "parsed_raw": len(raw_proxies),
        "imported": len(cleaned),
        "skipped_meta": skipped_meta,
        "skipped_bad": skipped_bad,
        "sample_names": [p["name"] for p in cleaned[:8]],
    }
    if not cleaned:
        raise RuntimeError(
            f"no importable proxies after filter (raw={len(raw_proxies)} "
            f"meta={skipped_meta} bad={skipped_bad} format={fmt})"
        )
    return cleaned, info


def _backup_and_dump_yaml(cfg_path: Path, text: str, data: dict, header_note: str) -> Path:
    yaml = _require_yaml()
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    bak = cfg_path.with_suffix(cfg_path.suffix + f".pre-subimport-{ts}")
    bak.write_text(text, encoding="utf-8")

    class NoAliasDumper(yaml.SafeDumper):
        def ignore_aliases(self, data):  # type: ignore[no-untyped-def]
            return True

    header = ""
    if text.lstrip().startswith("#"):
        lines = text.splitlines(keepends=True)
        buf: list[str] = []
        for ln in lines:
            if ln.startswith("#") or ln.strip() == "":
                buf.append(ln)
                if len(buf) > 24:
                    break
            else:
                break
        header = "".join(buf)
        if header and not header.endswith("\n"):
            header += "\n"
        header += f"# {header_note}\n"

    body = yaml.dump(
        data,
        Dumper=NoAliasDumper,
        allow_unicode=True,
        sort_keys=False,
        width=120,
        default_flow_style=False,
    )
    cfg_path.write_text(header + body, encoding="utf-8")
    return bak


def merge_proxies_into_clash_config(
    cfg_path: Path,
    proxies: list[dict[str, Any]],
    *,
    groups: list[str],
    mode: str = "merge",
    prefix: str = "SUB",
) -> dict[str, Any]:
    """Merge proxies into one clash yaml; update selected group leaf lists.

    mode:
      - merge: add/replace by name; append missing names into selected groups
      - replace_prefix: drop existing proxies whose name starts with ``prefix-``
        then merge (useful for re-import of same airport)
    """
    yaml = _require_yaml()
    if not cfg_path.is_file():
        return {"ok": False, "path": str(cfg_path), "error": "config_not_found"}
    text = cfg_path.read_text(encoding="utf-8", errors="replace")
    try:
        data = yaml.safe_load(text)
    except Exception as e:
        return {"ok": False, "path": str(cfg_path), "error": f"yaml_load: {e}"}
    if not isinstance(data, dict):
        return {"ok": False, "path": str(cfg_path), "error": "yaml_root_not_mapping"}

    existing = data.get("proxies")
    if existing is None:
        existing = []
        data["proxies"] = existing
    if not isinstance(existing, list):
        return {"ok": False, "path": str(cfg_path), "error": "proxies_not_list"}

    mode_n = (mode or "merge").strip().lower()
    pref = (prefix or "").strip()
    removed_prefix = 0
    if mode_n == "replace_prefix" and pref:
        kept = []
        for p in existing:
            if isinstance(p, dict) and str(p.get("name") or "").startswith(f"{pref}-"):
                removed_prefix += 1
                continue
            kept.append(p)
        existing = kept
        data["proxies"] = existing

    by_name: dict[str, int] = {}
    for i, p in enumerate(existing):
        if isinstance(p, dict) and p.get("name"):
            by_name[str(p["name"])] = i

    added = 0
    replaced = 0
    new_names: list[str] = []
    for p in proxies:
        name = str(p.get("name") or "")
        if not name:
            continue
        new_names.append(name)
        if name in by_name:
            existing[by_name[name]] = p
            replaced += 1
        else:
            by_name[name] = len(existing)
            existing.append(p)
            added += 1

    # update groups
    want_groups = [g for g in (groups or []) if str(g).strip()]
    group_updates: list[dict[str, Any]] = []
    pgroups = data.get("proxy-groups") or []
    if not isinstance(pgroups, list):
        pgroups = []
        data["proxy-groups"] = pgroups
    existing_group_names = {
        str(g.get("name")) for g in pgroups if isinstance(g, dict) and g.get("name")
    }
    for gname in want_groups:
        g = next((x for x in pgroups if isinstance(x, dict) and x.get("name") == gname), None)
        if g is None:
            # create selector group if missing
            g = {"name": gname, "type": "select", "proxies": list(new_names) or ["DIRECT"]}
            pgroups.append(g)
            group_updates.append(
                {"name": gname, "created": True, "before": 0, "after": len(g["proxies"])}
            )
            continue
        old = list(g.get("proxies") or [])
        # keep order: existing first, then append new names not present
        seen = set(old)
        merged = list(old)
        for n in new_names:
            if n not in seen:
                merged.append(n)
                seen.add(n)
        # for replace_prefix also strip old prefix members not re-imported
        if mode_n == "replace_prefix" and pref:
            keep_new = set(new_names)
            merged = [
                n
                for n in merged
                if not (isinstance(n, str) and n.startswith(f"{pref}-") and n not in keep_new)
            ]
            # ensure all new names present
            for n in new_names:
                if n not in merged:
                    merged.append(n)
        if not merged:
            merged = ["DIRECT"]
        g["proxies"] = merged
        group_updates.append(
            {
                "name": gname,
                "created": False,
                "before": len(old),
                "after": len(merged),
            }
        )

    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    note = (
        f"sub-import {ts} added={added} replaced={replaced} "
        f"removed_prefix={removed_prefix} groups={','.join(want_groups) or '-'}"
    )
    bak = _backup_and_dump_yaml(cfg_path, text, data, note)
    return {
        "ok": True,
        "path": str(cfg_path),
        "backup": str(bak),
        "proxies_before": len(by_name) - added,  # approximate
        "proxies_after": len(existing),
        "added": added,
        "replaced": replaced,
        "removed_prefix": removed_prefix,
        "group_updates": group_updates,
        "existing_groups": sorted(existing_group_names),
    }


def _reload_clash_config(root: Path, cfg_path: Path) -> dict[str, Any]:
    """Ask mihomo to reload config file (PUT /configs?force=true)."""
    import urllib.parse

    secret = _clash_secret(root)
    # mihomo expects {"path": "..."}; force re-read
    path_q = urllib.parse.urlencode({"force": "true"})
    try:
        _clash_request(
            f"/configs?{path_q}",
            secret=secret,
            method="PUT",
            body={"path": str(cfg_path)},
            timeout=20.0,
        )
        return {"ok": True, "reloaded": True, "path": str(cfg_path)}
    except Exception as e:
        return {"ok": False, "reloaded": False, "path": str(cfg_path), "error": str(e)[:240]}


def import_clash_subscription(
    root: Path,
    *,
    url: str,
    group: str = "🎯Grok注册",
    groups: list[str] | None = None,
    prefix: str = "SUB",
    mode: str = "merge",
    dry_run: bool = False,
    reload: bool = True,
    timeout: float = 25.0,
    max_proxies: int = 400,
    include_listing: bool = False,
) -> dict[str, Any]:
    """Fetch subscription URL, parse nodes, merge into clash config + selected pool.

    Returns structured ok/fail feedback for the console UI. Stage timings land in
    ``timings`` and ops logs so hung imports are diagnosable.
    """
    t0 = time.time()
    timings: dict[str, int] = {}
    group_n = (group or "").strip() or "🎯Grok注册"
    target_groups = list(groups) if groups else [group_n]
    target_groups = [g.strip() for g in target_groups if str(g).strip()]
    if not target_groups:
        target_groups = ["🎯Grok注册"]
    pref = (prefix or "SUB").strip() or "SUB"
    mode_n = (mode or "merge").strip().lower()
    if mode_n not in {"merge", "replace_prefix"}:
        return {
            "ok": False,
            "error": "invalid_mode",
            "detail": "mode must be merge|replace_prefix",
            "timings": timings,
            "ms": int((time.time() - t0) * 1000),
        }

    # 1) fetch
    t_stage = time.time()
    try:
        body, fetch_meta = fetch_subscription_body(url, timeout=timeout)
    except Exception as e:
        timings["fetch_ms"] = int((time.time() - t_stage) * 1000)
        timings["total_ms"] = int((time.time() - t0) * 1000)
        print(
            f"[control_api] clash-import FAIL stage=fetch ms={timings['fetch_ms']} "
            f"err={type(e).__name__}: {str(e)[:160]}",
            flush=True,
        )
        return {
            "ok": False,
            "stage": "fetch",
            "error": "fetch_failed",
            "detail": str(e)[:300],
            "url": (url or "")[:200],
            "timings": timings,
            "ms": timings["total_ms"],
        }
    timings["fetch_ms"] = int((time.time() - t_stage) * 1000)
    print(
        f"[control_api] clash-import fetch via={fetch_meta.get('via')} "
        f"bytes={fetch_meta.get('bytes')} ms={timings['fetch_ms']}",
        flush=True,
    )

    # 2) parse
    t_stage = time.time()
    try:
        proxies, parse_info = parse_subscription_proxies(body, source=url, prefix=pref)
    except Exception as e:
        timings["parse_ms"] = int((time.time() - t_stage) * 1000)
        timings["total_ms"] = int((time.time() - t0) * 1000)
        print(
            f"[control_api] clash-import FAIL stage=parse ms={timings['parse_ms']} "
            f"err={type(e).__name__}: {str(e)[:160]}",
            flush=True,
        )
        return {
            "ok": False,
            "stage": "parse",
            "error": "parse_failed",
            "detail": str(e)[:300],
            "fetch": fetch_meta,
            "url": (url or "")[:200],
            "timings": timings,
            "ms": timings["total_ms"],
        }
    timings["parse_ms"] = int((time.time() - t_stage) * 1000)
    print(
        f"[control_api] clash-import parse imported={parse_info.get('imported')} "
        f"format={parse_info.get('format')} ms={timings['parse_ms']}",
        flush=True,
    )

    if len(proxies) > int(max_proxies):
        proxies = proxies[: int(max_proxies)]
        parse_info = dict(parse_info)
        parse_info["truncated"] = True
        parse_info["imported"] = len(proxies)

    cfg_paths = clash_config_paths()
    if not cfg_paths and not dry_run:
        timings["total_ms"] = int((time.time() - t0) * 1000)
        return {
            "ok": False,
            "stage": "config",
            "error": "clash_config_not_found",
            "detail": f"no config under { _clash_dir() } (set CLASH_DIR / CLASH_CONFIG)",
            "fetch": fetch_meta,
            "parse": parse_info,
            "timings": timings,
            "ms": timings["total_ms"],
        }

    if dry_run:
        # list which groups exist in first config if any
        group_preview: list[str] = []
        if cfg_paths:
            try:
                yaml = _require_yaml()
                data = yaml.safe_load(cfg_paths[0].read_text(encoding="utf-8", errors="replace")) or {}
                group_preview = [
                    str(g.get("name"))
                    for g in (data.get("proxy-groups") or [])
                    if isinstance(g, dict) and g.get("name")
                ]
            except Exception:
                group_preview = []
        timings["total_ms"] = int((time.time() - t0) * 1000)
        print(
            f"[control_api] clash-import dry_run ok imported={parse_info.get('imported')} "
            f"would_write={len(cfg_paths)} total_ms={timings['total_ms']}",
            flush=True,
        )
        return {
            "ok": True,
            "dry_run": True,
            "stage": "dry_run",
            "url": (url or "")[:200],
            "groups": target_groups,
            "prefix": pref,
            "mode": mode_n,
            "fetch": fetch_meta,
            "parse": parse_info,
            "would_write": [str(p) for p in cfg_paths],
            "available_groups": group_preview,
            "importable_groups": list(IMPORTABLE_GROUPS),
            "message": (
                f"预检成功：解析 {parse_info.get('imported')} 个节点"
                f"（format={parse_info.get('format')}"
                f"{', base64' if parse_info.get('base64') else ''}），"
                f"将写入池子 {', '.join(target_groups)}；未改配置"
                f" · {timings['total_ms']}ms"
            ),
            "timings": timings,
            "ms": timings["total_ms"],
        }

    # 3) write — only live + mac-merged by default (see clash_config_paths)
    t_stage = time.time()
    writes: list[dict[str, Any]] = []
    for p in cfg_paths:
        # always write live config.yaml; also mirror into mac-merged if present
        # so next start-clash-for-grok.sh keeps the import
        writes.append(
            merge_proxies_into_clash_config(
                p,
                proxies,
                groups=target_groups,
                mode=mode_n,
                prefix=pref,
            )
        )
    timings["write_ms"] = int((time.time() - t_stage) * 1000)
    print(
        f"[control_api] clash-import write files={len(writes)} "
        f"ok={sum(1 for w in writes if w.get('ok'))} ms={timings['write_ms']}",
        flush=True,
    )

    ok_writes = [w for w in writes if w.get("ok")]
    if not ok_writes:
        timings["total_ms"] = int((time.time() - t0) * 1000)
        return {
            "ok": False,
            "stage": "write",
            "error": "write_failed",
            "detail": "all config writes failed",
            "writes": writes,
            "fetch": fetch_meta,
            "parse": parse_info,
            "timings": timings,
            "ms": timings["total_ms"],
        }

    reload_info: dict[str, Any] | None = None
    if reload:
        t_stage = time.time()
        # reload the live config.yaml if present, else first successful write
        live = next((Path(w["path"]) for w in ok_writes if Path(w["path"]).name == "config.yaml"), None)
        if live is None:
            live = Path(ok_writes[0]["path"])
        reload_info = _reload_clash_config(root, live)
        timings["reload_ms"] = int((time.time() - t_stage) * 1000)
        print(
            f"[control_api] clash-import reload ok={reload_info.get('ok')} "
            f"ms={timings['reload_ms']}",
            flush=True,
        )

    # post-import listing is optional — Clash group walk can be slow and the UI
    # already refreshes nodes separately after success.
    listing = None
    if include_listing:
        t_stage = time.time()
        try:
            listing = list_clash_nodes(root)
        except Exception:
            listing = None
        timings["listing_ms"] = int((time.time() - t_stage) * 1000)

    # still ok if write succeeded but reload failed — report clearly
    write_ok = bool(ok_writes)
    reload_ok = True if not reload else bool((reload_info or {}).get("ok"))
    timings["total_ms"] = int((time.time() - t0) * 1000)
    msg_parts = [
        f"成功导入 {parse_info.get('imported')} 个节点" if write_ok else "导入失败",
        f"池子={','.join(target_groups)}",
        f"mode={mode_n}",
        f"prefix={pref}",
    ]
    if write_ok:
        w0 = ok_writes[0]
        msg_parts.append(f"added={w0.get('added')} replaced={w0.get('replaced')}")
    if reload:
        msg_parts.append("reload=ok" if reload_ok else f"reload=fail:{(reload_info or {}).get('error', '')[:80]}")
    msg_parts.append(f"{timings['total_ms']}ms")

    print(
        f"[control_api] clash-import done ok={write_ok} imported={parse_info.get('imported')} "
        f"fetch={timings.get('fetch_ms')} parse={timings.get('parse_ms')} "
        f"write={timings.get('write_ms')} reload={timings.get('reload_ms')} "
        f"total={timings['total_ms']}",
        flush=True,
    )

    return {
        "ok": write_ok,  # file write is the durable success criterion
        "stage": "done" if write_ok else "write",
        "message": " · ".join(msg_parts),
        "url": (url or "")[:200],
        "groups": target_groups,
        "prefix": pref,
        "mode": mode_n,
        "fetch": fetch_meta,
        "parse": parse_info,
        "writes": writes,
        "reload": reload_info,
        "reload_ok": reload_ok,
        "leaf_count_after": (listing or {}).get("leaf_count") if isinstance(listing, dict) else None,
        "importable_groups": list(IMPORTABLE_GROUPS),
        "timings": timings,
        "ms": timings["total_ms"],
        "warning": None
        if reload_ok
        else "配置已写入磁盘，但 mihomo 热重载失败；可手动 bash start-clash-for-grok.sh（注意会短暂中断出口）",
    }


# ── Clash prune / delete by prefix ───────────────────────────────────────────

# Prefixes that require force=true to delete (infra / rarely accidental).
PROTECTED_CLASH_PREFIXES = frozenset({"GVPS", "DIRECT", "REJECT"})


def _normalize_prefix(prefix: str) -> str:
    return re.sub(r"\s+", "", (prefix or "").strip())


def _name_matches_prefix(name: str, prefix: str) -> bool:
    """True if leaf name is exactly prefix or starts with ``prefix-``."""
    n = str(name or "")
    p = _normalize_prefix(prefix)
    if not p or not n:
        return False
    return n == p or n.startswith(f"{p}-")


def remove_proxies_by_prefix_from_config(
    cfg_path: Path,
    *,
    prefix: str,
) -> dict[str, Any]:
    """Drop all proxies (and group refs) whose name matches prefix / prefix-*.

    Does not reload mihomo — caller should reload once after all writes.
    """
    yaml = _require_yaml()
    pref = _normalize_prefix(prefix)
    if not pref:
        return {"ok": False, "path": str(cfg_path), "error": "empty_prefix"}
    if not cfg_path.is_file():
        return {"ok": False, "path": str(cfg_path), "error": "config_not_found"}
    text = cfg_path.read_text(encoding="utf-8", errors="replace")
    try:
        data = yaml.safe_load(text)
    except Exception as e:
        return {"ok": False, "path": str(cfg_path), "error": f"yaml_load: {e}"}
    if not isinstance(data, dict):
        return {"ok": False, "path": str(cfg_path), "error": "yaml_root_not_mapping"}

    existing = data.get("proxies")
    if existing is None:
        existing = []
        data["proxies"] = existing
    if not isinstance(existing, list):
        return {"ok": False, "path": str(cfg_path), "error": "proxies_not_list"}

    kept: list[Any] = []
    removed_names: list[str] = []
    for p in existing:
        if isinstance(p, dict):
            n = str(p.get("name") or "")
            if _name_matches_prefix(n, pref):
                removed_names.append(n)
                continue
        kept.append(p)
    data["proxies"] = kept
    removed_set = set(removed_names)

    group_updates: list[dict[str, Any]] = []
    pgroups = data.get("proxy-groups") or []
    if isinstance(pgroups, list):
        for g in pgroups:
            if not isinstance(g, dict):
                continue
            old = list(g.get("proxies") or [])
            if not old:
                continue
            new = [n for n in old if n not in removed_set]
            if len(new) != len(old):
                if not new:
                    new = ["DIRECT"]
                g["proxies"] = new
                group_updates.append(
                    {
                        "name": str(g.get("name") or ""),
                        "before": len(old),
                        "after": len(new),
                        "stripped": len(old) - len(new),
                    }
                )

    note = (
        f"clash-delete-prefix {pref} removed={len(removed_names)} "
        f"groups_touched={len(group_updates)}"
    )
    bak = _backup_and_dump_yaml(cfg_path, text, data, note)
    return {
        "ok": True,
        "path": str(cfg_path),
        "backup": str(bak),
        "prefix": pref,
        "removed": len(removed_names),
        "removed_names": removed_names[:80],
        "proxies_after": len(kept),
        "group_updates": group_updates,
    }


def remove_named_proxies_from_config(
    cfg_path: Path,
    *,
    names: list[str],
) -> dict[str, Any]:
    """Drop exact proxy names from proxies list + all group memberships."""
    yaml = _require_yaml()
    want = {str(n).strip() for n in (names or []) if str(n).strip()}
    if not want:
        return {"ok": False, "path": str(cfg_path), "error": "empty_names"}
    if not cfg_path.is_file():
        return {"ok": False, "path": str(cfg_path), "error": "config_not_found"}
    text = cfg_path.read_text(encoding="utf-8", errors="replace")
    try:
        data = yaml.safe_load(text)
    except Exception as e:
        return {"ok": False, "path": str(cfg_path), "error": f"yaml_load: {e}"}
    if not isinstance(data, dict):
        return {"ok": False, "path": str(cfg_path), "error": "yaml_root_not_mapping"}

    existing = data.get("proxies")
    if not isinstance(existing, list):
        existing = []
        data["proxies"] = existing
    kept: list[Any] = []
    removed_names: list[str] = []
    for p in existing:
        if isinstance(p, dict):
            n = str(p.get("name") or "")
            if n in want:
                removed_names.append(n)
                continue
        kept.append(p)
    data["proxies"] = kept
    removed_set = set(removed_names)

    group_updates: list[dict[str, Any]] = []
    pgroups = data.get("proxy-groups") or []
    if isinstance(pgroups, list):
        for g in pgroups:
            if not isinstance(g, dict):
                continue
            old = list(g.get("proxies") or [])
            if not old:
                continue
            new = [n for n in old if n not in removed_set]
            if len(new) != len(old):
                if not new:
                    new = ["DIRECT"]
                g["proxies"] = new
                group_updates.append(
                    {
                        "name": str(g.get("name") or ""),
                        "before": len(old),
                        "after": len(new),
                        "stripped": len(old) - len(new),
                    }
                )

    note = f"clash-delete-names removed={len(removed_names)}"
    bak = _backup_and_dump_yaml(cfg_path, text, data, note)
    return {
        "ok": True,
        "path": str(cfg_path),
        "backup": str(bak),
        "removed": len(removed_names),
        "removed_names": removed_names[:80],
        "proxies_after": len(kept),
        "group_updates": group_updates,
    }


def delete_clash_prefix(
    root: Path,
    *,
    prefix: str,
    dry_run: bool = False,
    reload: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """Delete all Clash proxies matching prefix / prefix-* from write configs."""
    t0 = time.time()
    pref = _normalize_prefix(prefix)
    if not pref:
        return {
            "ok": False,
            "error": "empty_prefix",
            "detail": "prefix required (e.g. YF, BP, SUB)",
            "ms": int((time.time() - t0) * 1000),
        }
    if len(pref) > 32:
        return {
            "ok": False,
            "error": "prefix_too_long",
            "detail": "prefix max 32 chars",
            "ms": int((time.time() - t0) * 1000),
        }
    if pref.upper() in PROTECTED_CLASH_PREFIXES and not force:
        return {
            "ok": False,
            "error": "protected_prefix",
            "detail": f"prefix {pref} is protected; pass force=true to delete",
            "prefix": pref,
            "ms": int((time.time() - t0) * 1000),
        }

    cfg_paths = clash_config_paths()
    if not cfg_paths:
        return {
            "ok": False,
            "error": "clash_config_not_found",
            "detail": f"no config under {_clash_dir()}",
            "prefix": pref,
            "ms": int((time.time() - t0) * 1000),
        }

    # preview counts from first config
    yaml = _require_yaml()
    preview_names: list[str] = []
    try:
        data0 = yaml.safe_load(cfg_paths[0].read_text(encoding="utf-8", errors="replace")) or {}
        for p in data0.get("proxies") or []:
            if isinstance(p, dict):
                n = str(p.get("name") or "")
                if _name_matches_prefix(n, pref):
                    preview_names.append(n)
    except Exception:
        preview_names = []

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "prefix": pref,
            "would_remove": len(preview_names),
            "sample": preview_names[:40],
            "would_write": [str(p) for p in cfg_paths],
            "message": f"预检：将删除 prefix={pref} 共 {len(preview_names)} 个节点（未改配置）",
            "ms": int((time.time() - t0) * 1000),
        }

    if not preview_names:
        return {
            "ok": True,
            "prefix": pref,
            "removed": 0,
            "writes": [],
            "reload": None,
            "message": f"无匹配节点 prefix={pref}",
            "ms": int((time.time() - t0) * 1000),
        }

    writes = [remove_proxies_by_prefix_from_config(p, prefix=pref) for p in cfg_paths]
    ok_writes = [w for w in writes if w.get("ok")]
    reload_info: dict[str, Any] | None = None
    if reload and ok_writes:
        live = next(
            (Path(w["path"]) for w in ok_writes if Path(w["path"]).name == "config.yaml"),
            Path(ok_writes[0]["path"]),
        )
        reload_info = _reload_clash_config(root, live)
    removed = max((int(w.get("removed") or 0) for w in ok_writes), default=0)
    print(
        f"[control_api] clash-delete-prefix prefix={pref} removed={removed} "
        f"files={len(ok_writes)} reload={bool((reload_info or {}).get('ok'))}",
        flush=True,
    )
    return {
        "ok": bool(ok_writes),
        "prefix": pref,
        "removed": removed,
        "sample": preview_names[:40],
        "writes": writes,
        "reload": reload_info,
        "reload_ok": True if not reload else bool((reload_info or {}).get("ok")),
        "message": f"已删除 prefix={pref} · {removed} 节点"
        + ("" if not reload else (" · reload=ok" if (reload_info or {}).get("ok") else " · reload=fail")),
        "ms": int((time.time() - t0) * 1000),
    }


def prune_clash_unhealthy(
    root: Path,
    *,
    prefix: str = "",
    group: str = "🎯Grok注册",
    timeout_ms: int = 4500,
    limit: int = 200,
    dry_run: bool = False,
    reload: bool = True,
    delete_defs: bool = True,
) -> dict[str, Any]:
    """Delay-test candidates and remove failing ones from configs.

    - If ``prefix`` set: only that prefix / prefix-*.
    - Else: leaf members of ``group`` that look like imported nodes
      (contain ``-`` and not DIRECT/REJECT).
    - ``delete_defs``: also drop proxy definitions (not only group membership).
    """
    t0 = time.time()
    pref = _normalize_prefix(prefix)
    group_n = (group or "").strip() or "🎯Grok注册"
    cfg_paths = clash_config_paths()
    if not cfg_paths:
        return {
            "ok": False,
            "error": "clash_config_not_found",
            "ms": int((time.time() - t0) * 1000),
        }

    yaml = _require_yaml()
    primary = cfg_paths[0]
    try:
        data0 = yaml.safe_load(primary.read_text(encoding="utf-8", errors="replace")) or {}
    except Exception as e:
        return {
            "ok": False,
            "error": f"yaml_load: {e}",
            "ms": int((time.time() - t0) * 1000),
        }

    candidates: list[str] = []
    if pref:
        for p in data0.get("proxies") or []:
            if isinstance(p, dict):
                n = str(p.get("name") or "")
                if _name_matches_prefix(n, pref):
                    candidates.append(n)
    else:
        g = next(
            (
                x
                for x in (data0.get("proxy-groups") or [])
                if isinstance(x, dict) and str(x.get("name") or "") == group_n
            ),
            None,
        )
        members = list((g or {}).get("proxies") or [])
        skip = {"DIRECT", "REJECT", "REJECT-DROP", "PASS"}
        for n in members:
            if not isinstance(n, str) or n in skip:
                continue
            # skip nested group refs without '-' that are pure emoji groups occasionally
            if n.startswith("♻️") or n.startswith("🚀") or n.startswith("🎯"):
                continue
            candidates.append(n)

    # de-dupe preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for n in candidates:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    candidates = uniq[: max(1, int(limit))]

    if not candidates:
        return {
            "ok": True,
            "dry_run": dry_run,
            "tested": 0,
            "unhealthy": 0,
            "healthy": 0,
            "removed": 0,
            "message": "无可测节点",
            "ms": int((time.time() - t0) * 1000),
        }

    # probe
    tr = test_clash_nodes(
        root,
        names=candidates,
        timeout_ms=int(timeout_ms),
        limit=len(candidates),
    )
    results = tr.get("results") or []
    unhealthy = [r["name"] for r in results if not r.get("ok")]
    healthy = [r["name"] for r in results if r.get("ok")]

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "prefix": pref or None,
            "group": group_n,
            "tested": len(results),
            "healthy": len(healthy),
            "unhealthy": len(unhealthy),
            "unhealthy_sample": unhealthy[:40],
            "healthy_sample": healthy[:20],
            "would_remove": len(unhealthy),
            "delete_defs": delete_defs,
            "message": (
                f"预检测活 {len(results)} · 通 {len(healthy)} · 不通 {len(unhealthy)}"
                f"（将{'删除定义' if delete_defs else '仅踢出组'}；未改配置）"
            ),
            "ms": int((time.time() - t0) * 1000),
        }

    if not unhealthy:
        return {
            "ok": True,
            "prefix": pref or None,
            "group": group_n,
            "tested": len(results),
            "healthy": len(healthy),
            "unhealthy": 0,
            "removed": 0,
            "message": f"测活 {len(results)} 全通，无需清理",
            "ms": int((time.time() - t0) * 1000),
        }

    writes: list[dict[str, Any]] = []
    if delete_defs:
        writes = [remove_named_proxies_from_config(p, names=unhealthy) for p in cfg_paths]
    else:
        # membership-only: strip from all groups but keep proxy defs
        for cfg in cfg_paths:
            text = cfg.read_text(encoding="utf-8", errors="replace")
            try:
                data = yaml.safe_load(text) or {}
            except Exception as e:
                writes.append({"ok": False, "path": str(cfg), "error": str(e)})
                continue
            dead = set(unhealthy)
            touched = 0
            for g in data.get("proxy-groups") or []:
                if not isinstance(g, dict):
                    continue
                old = list(g.get("proxies") or [])
                new = [n for n in old if n not in dead]
                if len(new) != len(old):
                    g["proxies"] = new or ["DIRECT"]
                    touched += 1
            bak = _backup_and_dump_yaml(
                cfg, text, data, f"clash-prune-unhealthy membership-only n={len(unhealthy)}"
            )
            writes.append(
                {
                    "ok": True,
                    "path": str(cfg),
                    "backup": str(bak),
                    "groups_touched": touched,
                    "removed": len(unhealthy),
                }
            )

    ok_writes = [w for w in writes if w.get("ok")]
    reload_info: dict[str, Any] | None = None
    if reload and ok_writes:
        live = next(
            (Path(w["path"]) for w in ok_writes if Path(w["path"]).name == "config.yaml"),
            Path(ok_writes[0]["path"]),
        )
        reload_info = _reload_clash_config(root, live)

    print(
        f"[control_api] clash-prune-unhealthy tested={len(results)} "
        f"dead={len(unhealthy)} delete_defs={delete_defs} "
        f"reload={bool((reload_info or {}).get('ok'))}",
        flush=True,
    )
    return {
        "ok": bool(ok_writes),
        "prefix": pref or None,
        "group": group_n,
        "tested": len(results),
        "healthy": len(healthy),
        "unhealthy": len(unhealthy),
        "unhealthy_sample": unhealthy[:40],
        "removed": len(unhealthy) if ok_writes else 0,
        "delete_defs": delete_defs,
        "writes": writes,
        "reload": reload_info,
        "reload_ok": True if not reload else bool((reload_info or {}).get("ok")),
        "message": (
            f"测活 {len(results)} · 删不通 {len(unhealthy)}"
            + ("" if not reload else (" · reload=ok" if (reload_info or {}).get("ok") else " · reload=fail"))
        ),
        "ms": int((time.time() - t0) * 1000),
    }
