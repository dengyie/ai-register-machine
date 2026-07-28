"""Dynamic egress node scoring for Clash/list rotation.

Goal
----
Prefer nodes that recently passed Turnstile / produced disk product; cool nodes
that hit Turnstile token_len=0 or browser_boot. Pure local state file — never
blocks registration; any IO/parse error → no-op / round-robin fallback.

Does **not** change mint product contract or CPA inject.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from pathlib import Path
from typing import Any, Optional

# Defaults (overridable via env)
DEFAULT_SCORE = 50
MIN_SCORE = 0
MAX_SCORE = 100
SUCCESS_REG = 3  # past Turnstile / profile submit
SUCCESS_MINT = 5  # complete xai-*.json product
PENALTY_TURNSTILE = 15
PENALTY_BOOT = 3
PENALTY_OTHER = 2
COOL_TURNSTILE_S = 20 * 60
COOL_BOOT_S = 5 * 60
COOL_OTHER_S = 2 * 60

_DEFAULT_PATH = "output/node_scores.json"
_lock = threading.RLock()
_store: dict[str, Any] | None = None
_path: Path | None = None
_enabled: bool | None = None
_corr_enabled = None  # test/programmatic override for EMAIL_IP_CORRELATION


def _truthy(val: Any, default: bool = True) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if not s:
        return default
    return s in {"1", "true", "yes", "y", "on"}


def scoring_enabled(cfg: dict | None = None) -> bool:
    """Dynamic weight switch (default **off** — zero impact on rotate).

    Priority:
      1. env NODE_SCORE / NODE_SCORE_ENABLED (1|true|on / 0|false|off)
      2. config node_score_enabled
      3. set_enabled() test override
      4. default False (round-robin unchanged)
    """
    for key in ("NODE_SCORE", "NODE_SCORE_ENABLED"):
        env = os.environ.get(key)
        if env is not None and str(env).strip() != "":
            return _truthy(env, default=False)
    if isinstance(cfg, dict) and "node_score_enabled" in cfg:
        return _truthy(cfg.get("node_score_enabled"), default=False)
    global _enabled
    if _enabled is not None:
        return _enabled
    return False


def set_enabled(flag: bool | None) -> None:
    """Test helper. Pass None to clear override."""
    global _enabled
    _enabled = None if flag is None else bool(flag)


def set_correlation_enabled(value):
    """Override the correlation master switch (None clears the override)."""
    global _corr_enabled
    _corr_enabled = value


def correlation_enabled(cfg=None):
    """Master switch for email×IP correlation (layers ①②③). Default OFF.

    Precedence mirrors scoring_enabled:
    env EMAIL_IP_CORRELATION -> cfg email_ip_correlation -> _corr_enabled -> False.
    """
    env = os.environ.get("EMAIL_IP_CORRELATION")
    if env is not None and str(env).strip() != "":
        return _truthy(env, default=False)
    if isinstance(cfg, dict) and "email_ip_correlation" in cfg:
        return _truthy(cfg.get("email_ip_correlation"), default=False)
    if _corr_enabled is not None:
        return bool(_corr_enabled)
    return False


def score_path(cfg: dict | None = None) -> Path:
    raw = ""
    if isinstance(cfg, dict):
        raw = str(cfg.get("node_score_path") or "").strip()
    if not raw:
        raw = os.environ.get("NODE_SCORE_PATH") or _DEFAULT_PATH
    p = Path(raw).expanduser()
    if not p.is_absolute():
        # Prefer process cwd (pxed /personal/grok-register); fall back to package root.
        p = Path.cwd() / p
    return p


def _empty_store() -> dict[str, Any]:
    return {"version": 1, "nodes": {}}


def _load(path: Path) -> dict[str, Any]:
    try:
        if not path.is_file():
            return _empty_store()
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_store()
        nodes = data.get("nodes")
        if not isinstance(nodes, dict):
            data["nodes"] = {}
        data.setdefault("version", 1)
        return data
    except Exception:
        return _empty_store()


def _save(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
    except Exception:
        # Never break registration on score write failure.
        pass


def _get_store(cfg: dict | None = None) -> tuple[Path, dict[str, Any]]:
    global _store, _path
    path = score_path(cfg)
    with _lock:
        if _store is None or _path != path:
            _path = path
            _store = _load(path)
        return path, _store


def reset_cache() -> None:
    """Test helper: drop in-memory cache."""
    global _store, _path, _enabled
    with _lock:
        _store = None
        _path = None
        _enabled = None


def _node_entry(store: dict[str, Any], name: str) -> dict[str, Any]:
    nodes = store.setdefault("nodes", {})
    ent = nodes.get(name)
    if not isinstance(ent, dict):
        ent = {
            "score": DEFAULT_SCORE,
            "cool_until": 0.0,
            "ok": 0,
            "fail_ts": 0,
            "fail_boot": 0,
            "updated": 0.0,
        }
        nodes[name] = ent
    ent.setdefault("score", DEFAULT_SCORE)
    ent.setdefault("cool_until", 0.0)
    ent.setdefault("ok", 0)
    ent.setdefault("fail_ts", 0)
    ent.setdefault("fail_boot", 0)
    return ent


def is_cooled(name: str, *, now: float | None = None, cfg: dict | None = None) -> bool:
    if not name or not scoring_enabled(cfg):
        return False
    _, store = _get_store(cfg)
    ent = _node_entry(store, name)
    t = time.time() if now is None else now
    try:
        return float(ent.get("cool_until") or 0) > t
    except Exception:
        return False


def get_score(name: str, *, cfg: dict | None = None) -> int:
    if not name:
        return DEFAULT_SCORE
    _, store = _get_store(cfg)
    ent = _node_entry(store, name)
    try:
        return int(ent.get("score") or DEFAULT_SCORE)
    except Exception:
        return DEFAULT_SCORE


def _clamp(score: int) -> int:
    return max(MIN_SCORE, min(MAX_SCORE, int(score)))


def record(
    name: str,
    kind: str,
    *,
    cfg: dict | None = None,
    log: Any = None,
) -> dict[str, Any]:
    """Record an outcome for *name*.

    kind:
      reg_ok | mint_ok | turnstile | browser_boot | other_fail
    """
    if not name or not scoring_enabled(cfg):
        return {"ok": False, "reason": "disabled_or_empty"}
    path, store = _get_store(cfg)
    with _lock:
        ent = _node_entry(store, name)
        now = time.time()
        kind_l = str(kind or "").strip().lower()
        delta = 0
        cool = 0.0
        if kind_l in {"mint_ok", "mint_success", "product_ok"}:
            delta = SUCCESS_MINT
            ent["ok"] = int(ent.get("ok") or 0) + 1
            # success clears cool
            ent["cool_until"] = 0.0
        elif kind_l in {"reg_ok", "register_ok", "turnstile_pass"}:
            delta = SUCCESS_REG
            ent["ok"] = int(ent.get("ok") or 0) + 1
            ent["cool_until"] = 0.0
        elif kind_l in {"turnstile", "turnstile_fail", "cf", "token_len_0"}:
            delta = -PENALTY_TURNSTILE
            ent["fail_ts"] = int(ent.get("fail_ts") or 0) + 1
            cool = COOL_TURNSTILE_S
            # second consecutive-ish fail: longer cool
            if int(ent.get("fail_ts") or 0) >= 2 and int(ent.get("ok") or 0) == 0:
                cool = COOL_TURNSTILE_S * 2
        elif kind_l in {"browser_boot", "boot", "connection"}:
            delta = -PENALTY_BOOT
            ent["fail_boot"] = int(ent.get("fail_boot") or 0) + 1
            cool = COOL_BOOT_S
        else:
            delta = -PENALTY_OTHER
            cool = COOL_OTHER_S

        ent["score"] = _clamp(int(ent.get("score") or DEFAULT_SCORE) + delta)
        if cool > 0:
            ent["cool_until"] = max(float(ent.get("cool_until") or 0), now + cool)
        ent["updated"] = now
        ent["last_kind"] = kind_l
        _save(path, store)
        out = {
            "ok": True,
            "node": name,
            "kind": kind_l,
            "score": ent["score"],
            "cool_until": ent.get("cool_until") or 0,
            "delta": delta,
        }
    if log:
        try:
            cool_left = max(0, int(float(out["cool_until"]) - time.time()))
            log(
                f"[node_score] {name!r} kind={kind_l} delta={delta} "
                f"score={out['score']} cool_s={cool_left}"
            )
        except Exception:
            pass
    return out


def _round_robin(nodes: list[str], now: str) -> str:
    try:
        idx = nodes.index(now)
        return nodes[(idx + 1) % len(nodes)]
    except ValueError:
        return nodes[0]


def pick_next(
    nodes: list[str],
    now: str,
    *,
    cfg: dict | None = None,
    rng: Optional[random.Random] = None,
) -> str:
    """Choose next leaf from *nodes*, preferring high score / non-cooled.

    Guarantees: returns a member of *nodes* (or *now* if nodes empty).
    If all cooled → ignore cool (never empty the pool).
    Prefer != now when multiple candidates exist.
    When scores are equal and nothing cooled → deterministic round-robin
    (same as pre-score behaviour; keeps tests + pin-less rotate stable).
    """
    if not nodes:
        return now or ""
    if len(nodes) == 1:
        return nodes[0]
    if not scoring_enabled(cfg):
        return _round_robin(nodes, now)

    t = time.time()
    active = [n for n in nodes if not is_cooled(n, now=t, cfg=cfg)]
    cooled_out = len(active) < len(nodes)
    if not active:
        active = list(nodes)
        cooled_out = False

    scores = [get_score(n, cfg=cfg) for n in active]
    # No signal yet → behave like classic rotate.
    if not cooled_out and len(set(scores)) <= 1:
        return _round_robin(nodes, now)

    candidates = [n for n in active if n != now] or list(active)
    weights: list[float] = []
    for n in candidates:
        # +1 so score 0 still has a chance; cooled already filtered when possible
        w = float(get_score(n, cfg=cfg)) + 1.0
        weights.append(max(0.1, w))

    r = rng if rng is not None else random
    try:
        return r.choices(candidates, weights=weights, k=1)[0]
    except Exception:
        return _round_robin(nodes, now)


def current_label_from_rotate_result(result: dict | None) -> str:
    if not isinstance(result, dict):
        return ""
    for k in ("node", "label", "current_label"):
        v = result.get(k)
        if v and str(v).strip() and str(v).strip() not in {"-", "(none)", "off"}:
            return str(v).strip()
    return ""


def _kind_effect(kind: str, entry: dict) -> tuple[int, float]:
    """Pure kind -> (delta, cool_seconds). Does NOT mutate *entry*.

    Same mapping as IP ``record``; extracted so domain/pair recorders stay in
    sync with IP-side semantics without touching IP ``record`` (intentional
    duplication — protects the §5.F IP non-regression guard).
    """
    kind_l = str(kind or "").strip().lower()
    if kind_l in {"mint_ok", "mint_success", "product_ok"}:
        return SUCCESS_MINT, 0.0
    if kind_l in {"reg_ok", "register_ok", "turnstile_pass"}:
        return SUCCESS_REG, 0.0
    if kind_l in {"turnstile", "turnstile_fail", "cf", "token_len_0"}:
        cool = COOL_TURNSTILE_S
        if int(entry.get("fail_ts") or 0) + 1 >= 2 and int(entry.get("ok") or 0) == 0:
            cool = COOL_TURNSTILE_S * 2
        return -PENALTY_TURNSTILE, cool
    if kind_l in {"browser_boot", "boot", "connection"}:
        return -PENALTY_BOOT, COOL_BOOT_S
    return -PENALTY_OTHER, COOL_OTHER_S


def _domain_entry(store: dict, domain: str) -> dict:
    domains = store.get("domains")
    if not isinstance(domains, dict):
        domains = {}
        store["domains"] = domains
    ent = domains.get(domain)
    if not isinstance(ent, dict):
        ent = {"score": DEFAULT_SCORE, "cool_until": 0.0, "ok": 0,
               "fail_ts": 0, "fail_boot": 0, "updated": 0.0}
        domains[domain] = ent
    for k, d in (("score", DEFAULT_SCORE), ("cool_until", 0.0), ("ok", 0),
                 ("fail_ts", 0), ("fail_boot", 0), ("updated", 0.0)):
        ent.setdefault(k, d)
    return ent


def get_domain_score(domain: str, *, cfg: dict | None = None) -> int:
    if not domain or not correlation_enabled(cfg):
        return DEFAULT_SCORE
    try:
        _, store = _get_store(cfg)
        domains = store.get("domains")
        if not isinstance(domains, dict):
            return DEFAULT_SCORE
        ent = domains.get(str(domain).strip().lower())
        if not isinstance(ent, dict):
            return DEFAULT_SCORE
        return int(ent.get("score") or DEFAULT_SCORE)
    except Exception:
        return DEFAULT_SCORE


def record_domain(domain: str, kind: str, *, cfg: dict | None = None,
                  log: Any = None) -> dict[str, Any]:
    if not domain or not correlation_enabled(cfg):
        return {"ok": False, "reason": "disabled"}
    domain = str(domain).strip().lower()
    if not domain:
        return {"ok": False, "reason": "disabled"}
    kind_l = str(kind or "").strip().lower()
    path, store = _get_store(cfg)
    with _lock:
        ent = _domain_entry(store, domain)
        now = time.time()
        delta, cool = _kind_effect(kind_l, ent)
        if delta > 0:
            ent["ok"] = int(ent.get("ok") or 0) + 1
            ent["cool_until"] = 0.0
        elif kind_l in {"turnstile", "turnstile_fail", "cf", "token_len_0"}:
            ent["fail_ts"] = int(ent.get("fail_ts") or 0) + 1
        elif kind_l in {"browser_boot", "boot", "connection"}:
            ent["fail_boot"] = int(ent.get("fail_boot") or 0) + 1
        ent["score"] = _clamp(int(ent.get("score") or DEFAULT_SCORE) + delta)
        if cool > 0:
            ent["cool_until"] = max(float(ent.get("cool_until") or 0), now + cool)
        ent["updated"] = now
        ent["last_kind"] = kind_l
        _save(path, store)
        out = {"ok": True, "domain": domain, "kind": kind_l,
               "score": ent["score"], "cool_until": ent.get("cool_until") or 0,
               "delta": delta}
    if log:
        try:
            log(f"[domain_score] {domain!r} kind={kind_l} delta={delta} "
                f"score={out['score']}")
        except Exception:
            pass
    return out
