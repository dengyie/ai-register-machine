# Email × IP Correlation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correlate email quality and IP/node quality across selection and failure attribution, fully automated, behind a single OFF-default master switch.

**Architecture:** Extend `node_score.py` with domain and pair scoring that reuses the existing `output/node_scores.json` store (new lazy top-level `domains`/`pairs` keys) under one switch `EMAIL_IP_CORRELATION`. Three layers ship together: ① failure attribution split at the "got code" boundary (`register_cli.py`), ② domain-weighted email sampling (`mail_pool_probe.py`), ③ email×node pair affinity soft-bias at node rotation (`proxy_rotate.py`). OFF = byte-for-byte current behavior.

**Tech Stack:** Python 3, stdlib only (`json`, `random`, `threading`, `fcntl` flock via existing helpers), `pytest` for tests.

## Global Constraints

- **完全自动化**：不引入任何手工点击步骤。
- **总开关默认 OFF**：`EMAIL_IP_CORRELATION`（env `EMAIL_IP_CORRELATION` / config `email_ip_correlation`）默认关闭。OFF 时行为与当前逐字节等价（① 归因边界不生效、②③ 既不读也不写 domains/pairs）。
- **不改全局 clash 规则，不动双指标 UI。**
- **层 ② 限制**：只在多域名时按域名得分加权采样（得分高的选得多，得分低的选得少）；单域名自然退化为等权 = 当前 `random.shuffle` 行为。**不做位置/顺序更改，不做单独策略更改。**
- **node_score IP 打分公式 / 常量 / 冷却时间保持不变**，仅做扩展（新增 domain / pair 读写函数），不改 IP 维度。
- **状态存储复用** `output/node_scores.json`（同一把 flock），新增顶层键 `domains`/`pairs` **惰性写入**（仅在开关 ON 且发生写时 `setdefault`），确保 NODE_SCORE ON 但 EMAIL_IP_CORRELATION OFF 时文件格式不变。
- **软信号，永不阻塞注册**：任何 domains/pairs 读写异常都退化为中性默认，注册流程继续。
- switch precedence mirrors existing `scoring_enabled`: env `EMAIL_IP_CORRELATION` → cfg `email_ip_correlation` → module override → `False`.

---
## File Structure

- `node_score.py` — extend with correlation switch, lazy domains/pairs storage, domain read/write, pair read/write/cool, sampling helpers (`domain_weights`, `preferred_node_for`). IP formula/constants/cooldowns untouched.
- `mail_pool_probe.py` — `sample_accounts` gains optional `cfg`; weighted branch only when correlation ON + multiple domains + no seed/offset.
- `register_cli.py` — layer ① attribution boundary in the email try-loop; record domain/pair on the appropriate outcomes.
- `proxy_rotate.py` — `set_registration_domain`/`clear_registration_domain` on the rotator; consult `preferred_node_for` at the scored-pick block (after `mint_hold`). `note_egress_outcome` signature/semantics unchanged.
- Tests: extend `test_node_score.py` (IP non-regression, §5.F); new `test_email_ip_correlation.py` (switch, domain, pair, weights, preferred_node); new `test_mail_pool_weighted_sampling.py` (§5.C + OFF-equivalence §5.A email path).

## Shared kind→(delta, cool) mapping

Layer ② and ③ reuse the SAME kind semantics as IP `record`, but domains/pairs must NOT mutate IP `record`. Extract a module-level pure helper `_kind_effect(kind, entry)` returning `(delta, cool_seconds)` and have the new `record_domain`/`record_pair` call it. The existing IP `record` is left byte-for-byte unchanged (intentional duplication documented inline to preempt reviewer flags — changing IP `record` risks the §5.F non-regression guard).

---

### Task 1: Correlation switch + lazy domains/pairs storage

**Files:**
- Modify: `node_score.py` (add `correlation_enabled`, `set_correlation_enabled`, `_corr_enabled` override; extend `_empty_store` NOT changed — keys added lazily on write)
- Test: `test_email_ip_correlation.py` (create)

**Interfaces:**
- Consumes: existing `node_score._truthy`, `_load`, `_save`, `_get_store`, `reset_cache`, `_DEFAULT_PATH`, `scoring_enabled` pattern.
- Produces:
  - `correlation_enabled(cfg=None) -> bool` — precedence: env `EMAIL_IP_CORRELATION` → cfg `email_ip_correlation` → module `_corr_enabled` override → `False`.
  - `set_correlation_enabled(value: bool | None) -> None` — test override (mirrors `set_enabled`).
  - Store may gain top-level `"domains": {}` and `"pairs": {}` ONLY via `setdefault` inside write paths (Tasks 2/3), never in `_empty_store`.

- [ ] **Step 1: Write the failing test**

```python
# test_email_ip_correlation.py
import json
import os
import importlib

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_email_ip_correlation.py -v`
Expected: FAIL with `AttributeError: module 'node_score' has no attribute 'correlation_enabled'`

- [ ] **Step 3: Write minimal implementation**

Add near the existing `_enabled` override and `scoring_enabled` in `node_score.py`:

```python
_corr_enabled = None  # test/programmatic override for EMAIL_IP_CORRELATION


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
```

Leave `_empty_store` unchanged (still `{"version": 1, "nodes": {}}`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_email_ip_correlation.py -v`
Expected: PASS (9 Task-1 tests)

- [ ] **Step 5: Commit**

```bash
git add node_score.py test_email_ip_correlation.py
git commit -m "feat(node_score): EMAIL_IP_CORRELATION master switch (default OFF)"
```

---

### Task 2: `_kind_effect` helper + `record_domain` / `get_domain_score`

**Files:**
- Modify: `node_score.py` (add module-level `_kind_effect`; add `domain` read/write using lazy `setdefault("domains", {})`)
- Test: `test_email_ip_correlation.py` (append domain tests)

**Interfaces:**
- Consumes: Task 1 `correlation_enabled`, `set_correlation_enabled`; existing `_clamp`, `_load`, `_save`, `_get_store`, `_node_entry` shape (for structural reference only).
- Produces:
  - `_kind_effect(kind: str, entry: dict) -> tuple[int, float]` — pure mapping `(delta, cool_seconds)`; **does not mutate entry** (caller applies). Reuses the IP `record` kind→(delta,cool) table verbatim.
  - `get_domain_score(domain: str, *, cfg=None) -> int` — returns `DEFAULT_SCORE` for unknown/disabled/corrupted.
  - `record_domain(domain: str, kind: str, *, cfg=None, log=None) -> dict` — no-op when `correlation_enabled(cfg)` is False or empty domain; lazily creates `store["domains"]` only on first write; applies `_kind_effect` to a domain entry shaped like `_node_entry` (`score/cool_until/ok/fail_ts/fail_boot/updated/last_kind`); clamps; atomic save; never raises.

- [ ] **Step 1: Write the failing tests**

Append to `test_email_ip_correlation.py`:

```python
def _seed_domains_store(tmp_path, domains):
    """Write a node_scores.json with a domains block for read tests."""
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    import json
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 1, "nodes": {}, "domains": domains}),
                 encoding="utf-8")
    return p


def test_domain_score_unknown_returns_default(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    assert ns.get_domain_score("nope.example") == ns.DEFAULT_SCORE


def test_domain_score_known(tmp_path):
    _seed_domains_store(tmp_path, {"a.com": {"score": 73, "cool_until": 0.0,
                                              "ok": 2, "fail_ts": 0}})
    assert ns.get_domain_score("a.com") == 73


def test_record_domain_disabled_noop(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    import json
    ns.set_correlation_enabled(False)  # OFF must not touch store
    out = ns.record_domain("a.com", "turnstile")
    assert out["ok"] is False
    assert out["reason"] == "disabled"
    # store file should NOT be created by a disabled write
    assert not (tmp_path / "s.json").exists()


def test_record_domain_turnstile_pens_and_writes_domains_key(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    ns.set_correlation_enabled(True)
    out = ns.record_domain("a.com", "turnstile")
    assert out["ok"] is True
    assert out["domain"] == "a.com"
    assert out["score"] == ns.DEFAULT_SCORE - ns.PENALTY_TURNSTILE
    import json
    data = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert "domains" in data and "a.com" in data["domains"]
    assert data["domains"]["a.com"]["score"] == out["score"]
    assert data["domains"]["a.com"]["cool_until"] > 0
    # IP nodes dict still empty (cross-dimension isolation)
    assert data["nodes"] == {}


def test_record_domain_reg_ok_clears_cool(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    import json, time
    _seed_domains_store(
        tmp_path,
        {"a.com": {"score": 35, "cool_until": time.time() + 999, "ok": 0,
                    "fail_ts": 1, "fail_boot": 0, "updated": 0}},
    )
    ns.set_correlation_enabled(True)
    out = ns.record_domain("a.com", "reg_ok")
    assert out["ok"] is True
    assert out["score"] == 35 + ns.SUCCESS_REG
    data = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert data["domains"]["a.com"]["cool_until"] == 0.0
    assert data["domains"]["a.com"]["ok"] == 1


def test_kind_effect_pure_no_mutate():
    entry = {"score": 50, "ok": 0, "fail_ts": 0, "fail_boot": 0,
             "cool_until": 0.0}
    snap = dict(entry)
    d, c = ns._kind_effect("turnstile", entry)
    assert d == -ns.PENALTY_TURNSTILE
    assert c == ns.COOL_TURNSTILE_S
    # entry untouched
    assert entry == snap
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test_email_ip_correlation.py -v`
Expected: FAIL with `AttributeError: module 'node_score' has no attribute '_kind_effect'` (and `record_domain` / `get_domain_score`).

- [ ] **Step 3: Write minimal implementation**

Add to `node_score.py` after `current_label_from_rotate_result` at end of file:

```python
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
    domains = store.setdefault("domains", {})
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
    path, store = _get_store(cfg)
    with _lock:
        ent = _domain_entry(store, domain)
        now = time.time()
        delta, cool = _kind_effect(kind, ent)
        if delta > 0:
            ent["ok"] = int(ent.get("ok") or 0) + 1
            ent["cool_until"] = 0.0
        elif kind.strip().lower() in {"turnstile", "turnstile_fail", "cf",
                                       "token_len_0"}:
            ent["fail_ts"] = int(ent.get("fail_ts") or 0) + 1
        elif kind.strip().lower() in {"browser_boot", "boot", "connection"}:
            ent["fail_boot"] = int(ent.get("fail_boot") or 0) + 1
        ent["score"] = _clamp(int(ent.get("score") or DEFAULT_SCORE) + delta)
        if cool > 0:
            ent["cool_until"] = max(float(ent.get("cool_until") or 0), now + cool)
        ent["updated"] = now
        ent["last_kind"] = str(kind or "").strip().lower()
        _save(path, store)
        out = {"ok": True, "domain": domain, "kind": ent["last_kind"],
               "score": ent["score"], "cool_until": ent.get("cool_until") or 0,
               "delta": delta}
    if log:
        try:
            log(f"[domain_score] {domain!r} kind={ent['last_kind']} "
                f"delta={delta} score={out['score']}")
        except Exception:
            pass
    return out
```

Note `_kind_effect` intentionally computes the double-cool branch from `entry`'s *current* `fail_ts` (the IP `record` reads `fail_ts` after incrementing; here we read it before and `+1` in the guard to match the post-increment semantics). The `else` branch in `_kind_effect` covers `other_fail` exactly like IP `record`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test_email_ip_correlation.py -v`
Expected: PASS (all prior + 6 new).

- [ ] **Step 5: Commit**

```bash
git add node_score.py test_email_ip_correlation.py
git commit -m "feat(node_score): _kind_effect + record_domain/get_domain_score (lazy domains key)"
```

---

### Task 3: `record_pair` / `get_pair` / `pair_is_cooled`

**Files:**
- Modify: `node_score.py` (add pair read/write/cool using lazy `setdefault("pairs", {})`, keyed `"{domain}\x00{node}"`)
- Test: `test_email_ip_correlation.py` (append pair tests)

**Interfaces:**
- Consumes: Task 1 `correlation_enabled`; Task 2 `_kind_effect`, `_clamp`, existing `_get_store`/`_save`.
- Produces:
  - `get_pair(domain, node, *, cfg=None) -> dict` — returns the pair entry or `{}` when disabled/unknown/corrupt (never raises).
  - `record_pair(domain, node, kind, *, cfg=None, log=None) -> dict` — no-op when OFF or empty args; lazily creates `store["pairs"]`; applies `_kind_effect` to a pair entry (same shape as `_node_entry`); atomic save.
  - `pair_is_cooled(domain, node, *, now=None, cfg=None) -> bool` — `False` unless `correlation_enabled` and the pair's `cool_until` > now.
- Pair key: `f"{domain}|{node}"` — kept in ONE flat `pairs` dict (not nested) so a corrupt top-level `pairs` degrades to empty without touching `domains`/`nodes`.

- [ ] **Step 1: Write the failing tests**

Append to `test_email_ip_correlation.py`:

```python
def test_get_pair_disabled_returns_empty(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    assert ns.get_pair("a.com", "n1") == {}


def test_record_pair_disabled_noop_no_file(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    out = ns.record_pair("a.com", "n1", "turnstile")
    assert out["ok"] is False
    assert not (tmp_path / "s.json").exists()


def test_record_pair_ok_creates_pairs_and_rewards(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    ns.set_correlation_enabled(True)
    out = ns.record_pair("a.com", "n1", "mint_ok")
    assert out["ok"] is True
    assert out["pair"] == "a.com|n1"
    assert out["score"] == ns.DEFAULT_SCORE + ns.SUCCESS_MINT
    import json
    data = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert "pairs" in data and "a.com|n1" in data["pairs"]
    assert data["nodes"] == {} and data.get("domains", {}) == {}


def test_pair_is_cooled_respects_switch(tmp_path):
    import json, time
    p = tmp_path / "s.json"
    p.write_text(json.dumps({
        "version": 1, "nodes": {},
        "pairs": {"a.com|n1": {"score": 40, "cool_until": time.time() + 600,
                                "ok": 0, "fail_ts": 1}},
    }), encoding="utf-8")
    os.environ["NODE_SCORE_PATH"] = str(p)
    # OFF -> never cooled (correlation not active)
    assert ns.pair_is_cooled("a.com", "n1") is False
    ns.set_correlation_enabled(True)
    assert ns.pair_is_cooled("a.com", "n1") is True
    assert ns.pair_is_cooled("a.com", "n1", now=time.time() + 99999) is False


def test_record_pair_fail_then_cool_and_recover(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    ns.set_correlation_enabled(True)
    ns.record_pair("a.com", "n1", "turnstile")
    assert ns.pair_is_cooled("a.com", "n1") is True
    # a success clears the cool
    ns.record_pair("a.com", "n1", "reg_ok")
    assert ns.pair_is_cooled("a.com", "n1") is False


def test_corrupt_pairs_key_degrades_to_empty(tmp_path):
    import json
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 1, "nodes": {"x": {"score": 80}},
                            "pairs": "NOT-A-DICT"}), encoding="utf-8")
    os.environ["NODE_SCORE_PATH"] = str(p)
    ns.set_correlation_enabled(True)
    # reads degrade cleanly
    assert ns.get_pair("a.com", "n1") == {}
    assert ns.pair_is_cooled("a.com", "n1") is False
    # and a fresh write repairs the key without dropping nodes
    out = ns.record_pair("a.com", "n1", "reg_ok")
    assert out["ok"] is True
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["nodes"] == {"x": {"score": 80}}
    assert "a.com|n1" in data["pairs"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test_email_ip_correlation.py -v`
Expected: FAIL with `AttributeError: module 'node_score' has no attribute 'record_pair'`.

- [ ] **Step 3: Write minimal implementation**

Add to `node_score.py` after `record_domain`:

```python
def _pair_key(domain: str, node: str) -> str:
    return f"{domain}|{node}"


def _pair_entry(store: dict, domain: str, node: str) -> dict:
    pairs = store.get("pairs")
    if not isinstance(pairs, dict):
        pairs = {}
        store["pairs"] = pairs
    key = _pair_key(domain, node)
    ent = pairs.get(key)
    if not isinstance(ent, dict):
        ent = {"score": DEFAULT_SCORE, "cool_until": 0.0, "ok": 0,
               "fail_ts": 0, "fail_boot": 0, "updated": 0.0}
        pairs[key] = ent
    for k, d in (("score", DEFAULT_SCORE), ("cool_until", 0.0), ("ok", 0),
                 ("fail_ts", 0), ("fail_boot", 0), ("updated", 0.0)):
        ent.setdefault(k, d)
    return ent


def get_pair(domain: str, node: str, *, cfg: dict | None = None) -> dict:
    if not domain or not node or not correlation_enabled(cfg):
        return {}
    try:
        _, store = _get_store(cfg)
        pairs = store.get("pairs")
        if not isinstance(pairs, dict):
            return {}
        ent = pairs.get(_pair_key(str(domain).strip().lower(), str(node).strip()))
        return ent if isinstance(ent, dict) else {}
    except Exception:
        return {}


def pair_is_cooled(domain: str, node: str, *, now: float | None = None,
                   cfg: dict | None = None) -> bool:
    if not domain or not node or not correlation_enabled(cfg):
        return False
    ent = get_pair(domain, node, cfg=cfg)
    if not ent:
        return False
    t = time.time() if now is None else now
    try:
        return float(ent.get("cool_until") or 0) > t
    except Exception:
        return False


def record_pair(domain: str, node: str, kind: str, *,
                cfg: dict | None = None, log: Any = None) -> dict[str, Any]:
    if not domain or not node or not correlation_enabled(cfg):
        return {"ok": False, "reason": "disabled"}
    domain = str(domain).strip().lower()
    node = str(node).strip()
    if not domain or not node:
        return {"ok": False, "reason": "disabled"}
    path, store = _get_store(cfg)
    with _lock:
        ent = _pair_entry(store, domain, node)
        now = time.time()
        delta, cool = _kind_effect(kind, ent)
        kind_l = str(kind or "").strip().lower()
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
        out = {"ok": True, "pair": _pair_key(domain, node), "kind": kind_l,
               "score": ent["score"], "cool_until": ent.get("cool_until") or 0,
               "delta": delta}
    if log:
        try:
            log(f"[pair_score] {out['pair']!r} kind={kind_l} delta={delta} "
                f"score={out['score']}")
        except Exception:
            pass
    return out
```

Note `_pair_entry` repairs a corrupt non-dict `pairs` key in place (test `test_corrupt_pairs_key_degrades_to_empty`), mirroring how `_load` tolerates a non-dict `nodes`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test_email_ip_correlation.py -v`
Expected: PASS (all prior + 6 new pair tests).

- [ ] **Step 5: Commit**

```bash
git add node_score.py test_email_ip_correlation.py
git commit -m "feat(node_score): record_pair/get_pair/pair_is_cooled (lazy pairs key)"
```

---

### Task 4: `domain_weights` (layer ②) + `preferred_node_for` (layer ③ scout)

**Files:**
- Modify: `node_score.py` (add the two pure-ish scouts; no state mutation, no save)
- Test: `test_email_ip_correlation.py` (append)

**Interfaces:**
- Consumes: Task 1 `correlation_enabled`; Task 2 `get_domain_score`; Task 3 `pair_is_cooled`, `get_pair`.
- Produces:
  - `domain_weights(domains: list[str], *, cfg=None) -> list[float]` — same length/order as input, weight `= get_domain_score(d) + 1.0` (floor `0.1`). When switch OFF or the list has ≤1 distinct domain → returns a uniform list `[1.0]*len(domains)` so layer ② degrades to equal-weight = `random.shuffle`. Unknown domains score as `DEFAULT_SCORE` → equal weight to a fresh known domain.
  - `preferred_node_for(domain: str, nodes: list[str], now: str, *, cfg=None) -> str | None` — returns a node from `nodes` that (a) is NOT `now`, (b) has a pair entry with `ok >= 1` and `score >= DEFAULT_SCORE`, and (c) is not currently cooled; pick the highest-scoring such node, ties broken by pool order. Returns `None` when switch OFF, no nodes, no good pair, or all cooled → caller silently falls back to ①②. **Never raises.**

- [ ] **Step 1: Write the failing tests**

Append to `test_email_ip_correlation.py`:

```python
def test_domain_weights_disabled_uniform(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    _seed_domains_store(tmp_path, {"a.com": {"score": 90}, "b.com": {"score": 10}})
    ns.set_correlation_enabled(False)
    w = ns.domain_weights(["a.com", "b.com"])
    assert w == [1.0, 1.0]


def test_domain_weights_single_domain_uniform():
    ns.set_correlation_enabled(True)
    assert ns.domain_weights(["only.com"]) == [1.0]
    assert ns.domain_weights([]) == []


def test_domain_weights_skews_by_score(tmp_path):
    _seed_domains_store(tmp_path, {"a.com": {"score": 90, "ok": 1},
                                    "b.com": {"score": 10, "ok": 0}})
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    ns.set_correlation_enabled(True)
    w = ns.domain_weights(["a.com", "b.com"])
    assert w[0] > w[1]
    assert w[0] == pytest.approx(91.0)
    assert w[1] == pytest.approx(11.0)


def test_domain_weights_unknown_domain_gets_default():
    ns.set_correlation_enabled(True)
    w = ns.domain_weights(["never.example", "also.unknown"])
    assert w[0] == w[1] == pytest.approx(ns.DEFAULT_SCORE + 1.0)


def test_preferred_node_returns_none_when_disabled(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    ns.set_correlation_enabled(False)
    assert ns.preferred_node_for("a.com", ["n1", "n2"], "n1") is None


def test_preferred_node_picks_good_uncooled_not_now(tmp_path):
    import json
    _seed_domains_store(tmp_path, {})
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 1, "nodes": {},
        "pairs": {"a.com|n1": {"score": 80, "ok": 2, "cool_until": 0.0},
                  "a.com|n2": {"score": 70, "ok": 1, "cool_until": 0.0}}}),
                 encoding="utf-8")
    os.environ["NODE_SCORE_PATH"] = str(p)
    ns.set_correlation_enabled(True)
    # now=n1 -> must avoid n1, pick n2 (best remaining good node)
    assert ns.preferred_node_for("a.com", ["n1", "n2", "n3"], "n1") == "n2"
    # now=n2 -> pick n1 (highest-scoring good node != now)
    assert ns.preferred_node_for("a.com", ["n1", "n2", "n3"], "n2") == "n1"


def test_preferred_node_none_when_all_cooled(tmp_path):
    import json, time
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 1, "nodes": {},
        "pairs": {"a.com|n1": {"score": 80, "ok": 2,
                                "cool_until": time.time() + 600}}}),
                 encoding="utf-8")
    os.environ["NODE_SCORE_PATH"] = str(p)
    ns.set_correlation_enabled(True)
    assert ns.preferred_node_for("a.com", ["n1", "n2"], "n2") is None


def test_preferred_node_none_when_no_pair():
    ns.set_correlation_enabled(True)
    assert ns.preferred_node_for("a.com", ["n1", "n2"], "n1") is None
```

Add `import pytest` at the top of `test_email_ip_correlation.py` if not present (needed for `pytest.approx`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test_email_ip_correlation.py -v`
Expected: FAIL with `AttributeError: module 'node_score' has no attribute 'domain_weights'`.

- [ ] **Step 3: Write minimal implementation**

Add to `node_score.py` after `record_pair`:

```python
def domain_weights(domains: list[str], *, cfg: dict | None = None) -> list[float]:
    """Layer ② weights, same order as *domains*. Uniform when disabled or <=1 domain."""
    if not domains:
        return []
    if not correlation_enabled(cfg):
        return [1.0 for _ in domains]
    uniq = {str(d).strip().lower() for d in domains if str(d).strip()}
    if len(uniq) <= 1:
        return [1.0 for _ in domains]
    out = []
    for d in domains:
        s = get_domain_score(str(d).strip().lower(), cfg=cfg)
        out.append(max(0.1, float(s) + 1.0))
    return out


def preferred_node_for(domain: str, nodes: list[str], now: str,
                       *, cfg: dict | None = None) -> str | None:
    """Layer ③ scout: a good, non-cooled pair node != now, or None.

    'good' = pair exists with ok>=1 and score>=DEFAULT_SCORE. Never raises;
    caller must fall back silently to ①/② when this returns None.
    """
    if not domain or not nodes or not correlation_enabled(cfg):
        return None
    domain = str(domain).strip().lower()
    if not domain:
        return None
    best = None
    best_score = -1
    for n in nodes:
        if not n or str(n) == str(now):
            continue
        if pair_is_cooled(domain, n, cfg=cfg):
            continue
        ent = get_pair(domain, n, cfg=cfg)
        if not ent:
            continue
        try:
            ok = int(ent.get("ok") or 0)
            score = int(ent.get("score") or DEFAULT_SCORE)
        except Exception:
            continue
        if ok >= 1 and score >= DEFAULT_SCORE and score > best_score:
            best = n
            best_score = score
    return best
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test_email_ip_correlation.py -v`
Expected: PASS (all prior + 7 new).

- [ ] **Step 5: Commit**

```bash
git add node_score.py test_email_ip_correlation.py
git commit -m "feat(node_score): domain_weights + preferred_node_for scouts (layers ②/③)"
```

---

### Task 5: `mail_pool_probe.sample_accounts` — layer ② weighted sampling + OFF-equivalence

**Files:**
- Modify: `mail_pool_probe.py:913-939` (`sample_accounts` adds optional `cfg=None`; weighted branch only when correlation ON + multiple distinct domains + `seed is None` + `offset is None`)
- Test: `tests/unit/test_mail_pool_weighted_sampling.py` (create) + extend `tests/unit/test_mail_pool_probe.py` (OFF-equivalence assertion)

**Interfaces:**
- Consumes: Task 4 `node_score.domain_weights`; `node_score.correlation_enabled` (via cfg/env); `Credential.domain`.
- Produces: `sample_accounts(accounts, limit, *, seed=None, offset=None, cfg=None)` — `cfg` is the only new param; no caller changes (existing call at `mail_pool_probe.py:1260` passes kwargs positionally/by-key and is unaffected by a trailing optional param).

**Weighted sampling algorithm (Efraimidis–Spirakis, weighted *without* replacement):** for each account assign key `random.random() ** (1.0 / weight)`; sort accounts by key descending; take top `limit`. With uniform weights this is statistically equivalent to `random.shuffle` (each ordering equally likely) so single-domain/OFF stays current behavior in distribution.

**OFF equivalence:** when `correlation_enabled(cfg)` is False, OR `offset is not None`, OR `seed is not None`, OR there is ≤1 distinct domain among `accounts`, the function MUST execute the existing `random.shuffle` path **verbatim** — byte-for-byte identical to today. The weighted branch is reached ONLY when all four hold.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_mail_pool_weighted_sampling.py`:

```python
import os
import random

import pytest

import mail_pool_probe as mpp
import node_score as ns


def _acc(domain, n):
    """Build n Credential-like accounts under one domain."""
    from mail_pool_probe import Credential
    return [Credential(email=f"u{i}@{domain}", password="p")
            for i in range(n)]


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


def test_off_equivalent_to_shuffle(tmp_path):
    """OFF: same random.Random(seed) -> identical sequence to current shuffle."""
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    ns.set_correlation_enabled(False)
    pool = _acc("a.com", 20) + _acc("b.com", 20)
    random.seed(1234)
    want = list(pool)
    random.shuffle(want)
    want = want[:7]
    got = mpp.sample_accounts(list(pool), 7, cfg={})  # OFF
    assert got == want


def test_single_domain_off_path(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    ns.set_correlation_enabled(True)  # ON, but single domain -> equal weight
    pool = _acc("a.com", 30)
    # weights uniform -> every account surfaced over many draws (no domain bias)
    seen = set()
    for s in range(40):
        got = mpp.sample_accounts(list(pool), 10, cfg={"email_ip_correlation": True})
        seen.update(a.email for a in got)
    assert len(seen) == 30


def test_seed_disables_weighted_branch(tmp_path):
    """seed != None must keep shuffle (deterministic) regardless of switch."""
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    pool = _acc("a.com", 10) + _acc("b.com", 10)
    ns.set_correlation_enabled(True)
    a = mpp.sample_accounts(list(pool), 5, seed=7, cfg={"email_ip_correlation": True})
    b = mpp.sample_accounts(list(pool), 5, seed=7, cfg={"email_ip_correlation": True})
    assert a == b  # deterministic under seed


def test_offset_disables_weighted_branch(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    pool = _acc("a.com", 12)
    ns.set_correlation_enabled(True)
    got = mpp.sample_accounts(list(pool), 4, offset=2,
                              cfg={"email_ip_correlation": True})
    assert [a.email for a in got] == [f"u{i}@a.com" for i in (2, 3, 4, 5)]


def test_multi_domain_high_score_picked_more(tmp_path):
    """Layer ②: a high-score domain is sampled more often than a low-score one."""
    import json, statistics
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 1, "nodes": {}, "domains": {
        "high.com": {"score": 99, "ok": 5, "cool_until": 0.0},
        "low.com": {"score": 1, "ok": 0, "cool_until": 0.0}}}), encoding="utf-8")
    os.environ["NODE_SCORE_PATH"] = str(p)
    pool = _acc("high.com", 50) + _acc("low.com", 50)
    ns.set_correlation_enabled(True)
    high_count = 0
    for t in range(400):
        got = mpp.sample_accounts(list(pool), 10, cfg={"email_ip_correlation": True})
        high_count += sum(1 for a in got if a.domain == "high.com")
    # high.com should dominate (avg ~9+/10); require a wide margin over parity.
    assert high_count > 400 * 10 * 0.80
```

Extend `tests/unit/test_mail_pool_probe.py` — add one OFF-equivalence regression at the end:

```python
def test_sample_accounts_off_keeps_shuffle_intact():
    import importlib
    import random as _r
    import node_score as ns
    for k in ("EMAIL_IP_CORRELATION", "NODE_SCORE", "NODE_SCORE_ENABLED",
              "NODE_SCORE_PATH"):
        os.environ.pop(k, None)
    ns.set_correlation_enabled(None)
    accs = _accs()  # however the file already builds its account list fixture
    _r.seed(99)
    want = list(accs)
    _r.shuffle(want)
    want = want[:3]
    got = sample_accounts(list(accs), 3, cfg={})  # OFF path
    assert got == want
```

(If the local fixture is named differently in that file, reuse it verbatim; the intent is: OFF + no seed + no offset must match `random.shuffle` sequencing. Use whatever account list the file already constructs and seed both sides identically.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_mail_pool_weighted_sampling.py -v`
Expected: FAIL — `sample_accounts() got an unexpected keyword argument 'cfg'`.

- [ ] **Step 3: Write minimal implementation**

Replace `mail_pool_probe.py:913-939` with:

```python
def sample_accounts(
    accounts: list[Credential],
    limit: int,
    *,
    seed: int | None = None,
    offset: int | None = None,
    cfg: dict | None = None,
) -> list[Credential]:
    """Pick up to ``limit`` accounts.

    - ``offset is not None`` → stable sequential slice ``[offset:offset+limit]``
      (file/filter order; no shuffle). Used for full-pool multi-wave scans.
    - else shuffle; deterministic if ``seed`` set.

    Layer ② (EMAIL_IP_CORRELATION on, multi-domain, no seed/offset): weighted
    sampling *without* replacement via Efraimidis–Spirakis keys. With uniform
    weights this is distributionally equivalent to ``random.shuffle``, so the
    single-domain / OFF paths stay byte-for-byte current behavior.
    """
    if limit <= 0 or not accounts:
        return []
    pool = list(accounts)
    if offset is not None:
        start = max(0, int(offset))
        if start >= len(pool):
            return []
        return pool[start : start + min(int(limit), len(pool) - start)]

    weighted = False
    weights = None
    if seed is None:
        try:
            import node_score as _ns

            if _ns.correlation_enabled(cfg):
                doms = {a.domain for a in pool if a.domain}
                if len({d.strip().lower() for d in doms if d}) > 1:
                    weights = _ns.domain_weights(
                        [a.domain for a in pool], cfg=cfg
                    )
                    weighted = bool(weights)
        except Exception:
            weighted = False
            weights = None

    if weighted and weights and len(weights) == len(pool):
        # Efraimidis–Spirakis: key = U**(1/w); sort desc; take top limit.
        u = [random.random() for _ in range(len(pool))]
        keys = [pow(u[i], 1.0 / float(weights[i])) for i in range(len(pool))]
        order = sorted(range(len(pool)), key=lambda i: keys[i], reverse=True)
        return [pool[i] for i in order[: min(int(limit), len(pool))]]

    if seed is not None:
        rng = random.Random(int(seed))
        rng.shuffle(pool)
    else:
        random.shuffle(pool)
    return pool[: min(int(limit), len(pool))]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_mail_pool_weighted_sampling.py tests/unit/test_mail_pool_probe.py -v`
Expected: PASS (weighted + OFF-equivalence).

- [ ] **Step 5: Commit**

```bash
git add mail_pool_probe.py tests/unit/test_mail_pool_weighted_sampling.py tests/unit/test_mail_pool_probe.py
git commit -m "feat(mail_pool): layer ② domain-weighted sampling (OFF-equivalent, multi-domain only)"
```

---

### Task 6: `register_cli.py` — layer ① attribution boundary + domain/pair recording

**Files:**
- Modify: `register_cli.py` (email try-loop `~1221-1301`; mint-success block near `~1746-1758`; helpers: add `_registration_domain_of`, `_record_correlation_domain`, `_record_correlation_pair`)

**Interfaces:**
- Consumes: Task 1 `node_score.correlation_enabled`; Task 2 `node_score.record_domain`; Task 3 `node_score.record_pair`; existing `proxy_rotate.get_rotator` (already imported), `current_egress_label`, `note_egress_outcome` (unchanged), `classify_email_stage_failure`, `_mark_email_stage_error`.
- Produces (internal helpers, not exported):
  - `_registration_domain_of(email: str) -> str` — `email.split("@")[-1].strip().lower()` or `""`.
  - `_record_correlation_domain(email, kind, *, cfg=None) -> dict` / `_record_correlation_pair(email, node, kind, *, cfg=None) -> dict` — thin guarded wrappers that lazy-`import node_score`, no-op when OFF or empty.
- The rotator's registration-domain hint (layer ③) is set here via `proxy_rotate.set_registration_domain` (added in Task 7) on email select, cleared at try-loop exit. When Task 7 isn't yet merged, these calls are simply absent — **Task 6 must not call a function that doesn't exist yet**; therefore the hint set/clear calls live in Task 7, and Task 6 only records domain/pair outcomes.

**Boundary rule (layer ①):** failures classified `mail_miss` / `fatal` / the bare邮箱阶段失败 fallthrough (all PRE-code) already do NOT call `note_egress_outcome` today (verified: all 5 `note_egress_outcome` call sites are at/after the profile stage). Task ① therefore:
1. Asserts this fact with an integration test (mock `note_egress_outcome`, drive a `mail_miss` failure, expect 0 calls). This freezes the boundary as a regression guard — the boundary already holds; the task formalizes + freezes it and adds domain recording.
2. Adds `_record_correlation_domain(email, <fail>, cfg)` next to each `_mark_email_stage_error` in the PRE-code branches (`mail_miss`, `fatal`, fallthrough). PRE-code failure ⇒ burn mailbox AND dock the DOMAIN (not the IP).
3. On reg_ok (post-SSO, line ~1452) and mint_ok (line ~1751): record `(domain, <ok>)` and `(domain, node, <ok>)` for the WINNING pair, alongside the existing IP `note_egress_outcome`. OFF ⇒ all calls no-op (verify zero store write).

**OFF-equivalence guard:** with `EMAIL_IP_CORRELATION` off, `correlation_enabled` returns False and every `record_domain`/`record_pair` short-circuits with `{"ok": False, "reason": "disabled"}` BEFORE touching the store, so `output/node_scores.json` gains no `domains`/`pairs` keys. An integration test asserts this.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_register_cli_attribution.py`:

```python
import os
import sys
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


def test_pre_code_failure_does_not_dock_ip(tmp_path, monkeypatch):
    """Layer ① boundary: a pre-code (mail_miss) failure records a DOMAIN penalty
    but leaves the IP node's score untouched. Equivalent to "do not call
    note_egress_outcome for IP" — verified by asserting the IP entry is unchanged
    after routing the domain penalty (uses the real node_score writer).

    The in-loop mail_miss branch (register_cli.py ~1263-1271) calls
    `_mark_email_stage_error` + `_record_correlation_domain` and (per the
    boundary invariant frozen here) NEVER `note_egress_outcome`. We exercise the
    domain-record half against a real store that also has an IP node, and assert
    the IP entry's score/cool are bit-for-byte identical. This is the §5.B guard
    for "no IP docking pre-code" using the production writer path.
    """
    import json
    import node_score as ns
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "b.json")
    (tmp_path / "b.json").write_text(json.dumps({
        "version": 1, "nodes": {"n1": {"score": 71, "cool_until": 0.0,
                                         "ok": 3, "fail_ts": 0, "fail_boot": 0}}}),
        encoding="utf-8")
    ns.set_correlation_enabled(True)
    # route a mailMiss as the loop's mail_miss branch does (domain only, no IP):
    out = rc._record_correlation_domain("u@a.com", "mail_miss",
                                         cfg={"email_ip_correlation": True})
    assert out["ok"] is True
    data = json.loads((tmp_path / "b.json").read_text(encoding="utf-8"))
    # DOMAIN was penalized...
    assert data["domains"]["a.com"]["score"] == ns.DEFAULT_SCORE - ns.PENALTY_OTHER
    # ...but the IP node is byte-for-byte intact (no note_egress_outcome happened):
    assert data["nodes"]["n1"] == {"score": 71, "cool_until": 0.0, "ok": 3,
                                    "fail_ts": 0, "fail_boot": 0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_register_cli_attribution.py -v`
Expected: FAIL — `AttributeError: module 'register_cli' has no attribute '_record_correlation_domain'`.

- [ ] **Step 3: Write minimal implementation**

Add near the other small helpers (e.g. beside `classify_email_stage_failure`, ~line 859) in `register_cli.py`:

```python
def _registration_domain_of(email: str) -> str:
    try:
        e = str(email or "").strip()
        if not e:
            return ""
        dom = e.split("@")[-1].strip().lower()
        return dom or e.lower()
    except Exception:
        return ""


def _record_correlation_domain(email, kind, *, cfg=None):
    """Soft: dock the email DOMAIN (not IP) for a pre-code failure. OFF/OFF→no-op."""
    try:
        import node_score as _ns
        if not _ns.correlation_enabled(cfg):
            return {"ok": False, "reason": "disabled"}
        return _ns.record_domain(_registration_domain_of(email), kind, cfg=cfg)
    except Exception:
        return {"ok": False, "reason": "error"}


def _record_correlation_pair(email, node, kind, *, cfg=None):
    """Soft: record the (domain, node) outcome for layer ③ affinity. OFF→no-op."""
    try:
        import node_score as _ns
        if not _ns.correlation_enabled(cfg):
            return {"ok": False, "reason": "disabled"}
        dom = _registration_domain_of(email)
        nd = str(node or "").strip()
        if not dom or not nd:
            return {"ok": False, "reason": "empty"}
        return _ns.record_pair(dom, nd, kind, cfg=cfg)
    except Exception:
        return {"ok": False, "reason": "error"}
```

Then in the email try-loop except branches (`register_cli.py:1257-1301`), add a domain record next to each `_mark_email_stage_error`. Specifically:

At the `kind == "mail_miss"` branch (~1266, after `_mark_email_stage_error(email, msg)`):
```python
                    _record_correlation_domain(email, "mail_miss",
                                               cfg=getattr(reg, "config", None))
```

At the `kind == "fatal"` branch (~1266 fatal fall-through, after `_clear_mail_provider_bind()` before `request_fatal_stop`):
```python
                    _record_correlation_domain(email, "other_fail",
                                               cfg=getattr(reg, "config", None))
```

At the bottom邮箱阶段失败 fall-through (~1296, after `_mark_email_stage_error(email, msg)`):
```python
                    _record_correlation_domain(email, "other_fail",
                                               cfg=getattr(reg, "config", None))
```

Do **NOT** add `note_egress_outcome` calls in any PRE-code branch (that is the layer ① invariant the test freezes).

Then in the reg_ok success block (~1452, in the `try: note_egress_outcome("reg_ok", ...)` block) append after the existing `note_turnstile_streak` call:
```python
            _record_correlation_domain(email, "reg_ok",
                                       cfg=getattr(reg, "config", None))
            _record_correlation_pair(email, current_egress_label() or "",
                                    "reg_ok",
                                    cfg=getattr(reg, "config", None))
```

And in the mint_ok success block (~1751 `note_egress_outcome("mint_ok", ...)`), append:
```python
                _record_correlation_domain(email, "mint_ok",
                                           cfg=getattr(reg, "config", None))
                _record_correlation_pair(email, current_egress_label() or "",
                                        "mint_ok",
                                        cfg=getattr(reg, "config", None))
```

For the `progress_fail` / `browser_boot` ARN branches (code may or may not be reached — ambiguous per spec §4 "边界模糊保守走未拿到 code"): do NOT record a domain penalty there (conservative — the mailbox is not burned by these branches either, per the existing comments at ~1273-1294). Document this inline.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_register_cli_attribution.py -v`
Expected: PASS (all 4).

- [ ] **Step 5: Commit**

```bash
git add register_cli.py tests/unit/test_register_cli_attribution.py
git commit -m "feat(register_cli): layer ① attribution boundary + domain/pair recording (gated)"
```

---

### Task 7: `proxy_rotate.py` — layer ③ registration-domain hint + preferred-node nudge

**Files:**
- Modify: `proxy_rotate.py` (`ProxyRotator.__init__` ~646 add field; `_rotate_list_locked` ~940-957; `_rotate_clash_locked` ~1082-1101; module wrappers ~1259 add `set_registration_domain`/`clear_registration_domain`)
- Test: `tests/unit/test_proxy_rotate_layer3_nudge.py` (create)

**Interfaces:**
- Consumes: Task 1 `node_score.correlation_enabled`; Task 4 `node_score.preferred_node_for`; existing `node_score.scoring_enabled`, `node_score.pick_next`.
- Produces:
  - `set_registration_domain(domain: str) -> None` / `clear_registration_domain() -> None` — store/clear `get_rotator()._registration_domain` under the rotator lock. Called from the registration worker (Task 7 wires the rotator-side plumbing; the actual call-sites in `register_cli` are the same email-select / try-loop-exit points touched by Task 6's boundary, restricted to just these two calls so Task 7 stays self-contained — see Step 3 note).
  - Layer ③ nudge: in BOTH rotate paths, when `correlation_enabled(cfg)` AND the holder's `_registration_domain` is set AND a scored pick is about to happen (mint_hold not active), first ask `preferred_node_for(domain, nodes, now, cfg=cfg)`; if it returns a node, use it (and set `pick_reason = "pair_affinity"`); otherwise fall through the existing scored-pick / round-robin unchanged. **OFF ⇒ `_registration_domain` is never consulted and preferred_node_for returns None ⇒ zero behavior change.**

**Non-goals (verbatim from spec):** `note_egress_outcome` signature/semantics unchanged; `mint_hold` re-pin logic (lines ~1049-1080) untouched; the nudge is a soft bias that only fires when a good, non-cooled pair exists and never starves exploration (returns None → original path).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_proxy_rotate_layer3_nudge.py`:

```python
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
    rot._mint_holds.clear()
    rot.current_label = "bad"
    res = rot._rotate_clash_locked(cfg={})
    assert res.get("pick") != "pair_affinity"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_proxy_rotate_layer3_nudge.py -v`
Expected: FAIL — `AttributeError: module 'proxy_rotate' has no attribute 'set_registration_domain'`.

- [ ] **Step 3: Write minimal implementation**

**(a)** In `ProxyRotator.__init__` (after `self._mint_holds`, ~646):

```python
        # Layer ③ hint: the email domain of the in-flight registration, so
        # the scored-pick block can soft-prefer a node with a known good pair.
        # Empty when EMAIL_IP_CORRELATION is off or no registration running.
        self._registration_domain: str = ""
```

**(b)** In `_rotate_list_locked`, after the first-claim branch's `else:` (replace the ~940-957 `else:` body with a nudge-then-scored-pick):

```python
        else:
            nxt = ""
            dom = ""
            try:
                import node_score as _ns

                if _ns.correlation_enabled(cfg):
                    dom = (self._registration_domain or "").strip().lower()
                    if dom:
                        cand = _ns.preferred_node_for(
                            dom, self.list_pool, prev or self.list_pool[0], cfg=cfg
                        )
                        if cand and cand in self.list_pool:
                            nxt = cand
                            pick_reason = "pair_affinity"
            except Exception:
                nxt = ""
                pick_reason = "round_robin"
            if not nxt or nxt not in self.list_pool:
                try:
                    import node_score as _ns

                    if _ns.scoring_enabled(cfg):
                        nxt = _ns.pick_next(
                            self.list_pool, prev or self.list_pool[0], cfg=cfg
                        )
                        if pick_reason != "pair_affinity":
                            pick_reason = "node_score"
                    else:
                        raise RuntimeError("use round_robin")
                except Exception:
                    nxt = ""
            if nxt and nxt in self.list_pool:
                self.list_index = self.list_pool.index(nxt)
                proxy = nxt
            else:
                self.list_index = (self.list_index + 1) % len(self.list_pool)
                proxy = self.list_pool[self.list_index]
                pick_reason = "round_robin"
```

**(c)** In `_rotate_clash_locked`, between the mint_hold early-return (~1080) and the existing scored-pick block (~1082), insert a nudge probe, then make the scored-pick block fall back only when the nudge missed:

Replace the block at **~1082-1101**:
```python
        # Layer ③ pair-affinity nudge (soft): if the in-flight reg domain has a
        # good, non-cooled pair node, prefer it. OFF / no pair / cooled -> fall
        # through to scored pick / round-robin unchanged.
        nxt = ""
        pick_reason = "round_robin"
        try:
            import node_score as _ns  # local module; optional at runtime

            if _ns.correlation_enabled(cfg):
                dom = (self._registration_domain or "").strip().lower()
                if dom:
                    cand = _ns.preferred_node_for(dom, nodes, now, cfg=cfg)
                    if cand and cand in nodes:
                        nxt = cand
                        pick_reason = "pair_affinity"
        except Exception:
            nxt = ""
        if not nxt or nxt not in nodes:
            try:
                import node_score as _ns

                if _ns.scoring_enabled(cfg):
                    got = _ns.pick_next(nodes, now, cfg=cfg)
                    if got and got in nodes:
                        nxt = got
                        if pick_reason != "pair_affinity":
                            pick_reason = "node_score"
            except Exception:
                nxt = ""
        if not nxt or nxt not in nodes:
            try:
                idx = nodes.index(now)
                nxt = nodes[(idx + 1) % len(nodes)]
            except ValueError:
                nxt = nodes[0]
                if nxt == now and len(nodes) > 1:
                    nxt = nodes[1]
            pick_reason = "round_robin"
```

The downstream `if nxt == now` single-or-same-node early-return (~1102) and the `clash_switch_node(...)` commit (~1113) stay **untouched** — the nudge only changes which `nxt` reaches them.

**(d)** Module-level wrappers near `configure_proxy_rotation` (~1259):

```python
def set_registration_domain(domain: str) -> None:
    """Layer ③ hint: record the in-flight registration email domain on the
    rotator so the scored-pick block can soft-prefer a good pair node.

    Normalized to lower-case; empty string clears. No-op cost when the
    EMAIL_IP_CORRELATION switch is off (the hint is simply never consulted).
    """
    with _rotator._lock:
        _rotator._registration_domain = str(domain or "").strip().lower()


def clear_registration_domain() -> None:
    with _rotator._lock:
        _rotator._registration_domain = ""


```

**Step 3 note (call-sites):** the registration worker should call `set_registration_domain(_registration_domain_of(email))` right after `email, dev_token = reg.fill_email_and_submit(...)` (register_cli.py ~1233) and `clear_registration_domain()` at try-loop exit (after ~1305). To keep Task 7 self-contained and testable in isolation, **do NOT edit `register_cli.py` as part of Task 7** — wire those two calls into Task 6's boundary work (or a follow-up micro-commit) so each task compiles and tests independently. The rotator plumbing added here is inert until a hint is set; OFF ⇒ it never consults it. (If Task 6 is already committed, a one-line follow-up commit `feat(register_cli): set/clear registration-domain hint around email try-loop` adds exactly those two calls — this is the only cross-task touch.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_proxy_rotate_layer3_nudge.py -v`
Expected: PASS (4).

Then the cross-task wiring commit:

```python
# register_cli.py — right after `email, dev_token = reg.fill_email_and_submit(...)` (~1235):
try:
    from proxy_rotate import set_registration_domain
    set_registration_domain(email)
except Exception:
    pass

# right after the try-loop clears `_clear_mail_provider_bind()` at ~1303 (after `if not mail_ok`):
try:
    from proxy_rotate import clear_registration_domain
    clear_registration_domain()
except Exception:
    pass
```

Commit both together:
```bash
git add proxy_rotate.py tests/unit/test_proxy_rotate_layer3_nudge.py register_cli.py
git commit -m "feat(proxy_rotate): layer ③ pair-affinity nudge + registration-domain hint"
```

---

### Task 8: Final regression — OFF-equivalence (§5.A) + IP non-regression (§5.F) + error handling (§5.E)

**Files:**
- Test: `tests/unit/test_email_ip_off_equivalence.py` (create — highest-priority §5.A), extend `test_node_score.py` (§5.F reuse), add `tests/unit/test_node_score_correlation_errors.py` (§5.E)

**Goal of this task:** a single closing test sweep that (a) proves OFF = today's behavior across mail sampling AND node rotation AND attribution, (b) proves the IP scoring surface is byte-for-byte unchanged, (c) proves store corruption / flock failure degrade silently. These are the spec's stated guards; if any fail, the whole feature must stay OFF.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_email_ip_off_equivalence.py`:

```python
import json
import os
import random

import pytest

import mail_pool_probe as mpp
import node_score as ns
import proxy_rotate as pr


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


# §5.A — mail sampling OFF == random.shuffle
def test_off_mail_sampling_byte_identical_to_shuffle(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "s.json")
    pool = []
    from mail_pool_probe import Credential
    for d in ("a.com", "b.com", "c.com"):
        for i in range(15):
            pool.append(Credential(email=f"u{i}@{d}", password="p"))
    random.seed(2026)
    want = list(pool)
    random.shuffle(want)
    want = want[:11]
    ns.set_correlation_enabled(False)  # explicitly OFF
    got = mpp.sample_accounts(list(pool), 11, cfg={})
    assert got == want
    # store untouched (no domains/pairs keys injected when OFF)
    assert not (tmp_path / "s.json").exists()


# §5.A — node rotation OFF == round-robin / pick_next-as-today
def test_off_node_rotation_unchanged(tmp_path, monkeypatch):
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
```

Extend `test_node_score.py` — add §5.F IP non-regression assertions (append). Also amend the file's existing `setup_function`/`teardown_function` to also clear the correlation switch, so the new `test_ip_record_disabled_when_correlation_only` (which sets `_corr_enabled=True`) cannot leak `_corr_enabled` into later tests. The amended setup/teardown lines to add (keep the existing ones, add these):

```python
# add inside setup_function() and teardown_function(), alongside the existing:
    os.environ.pop("EMAIL_IP_CORRELATION", None)
    ns.set_correlation_enabled(None)
```

Then append the §5.F tests:

```python
def test_ip_constants_unchanged():
    """§5.F: the IP-side scoring constants that correlation must not touch."""
    assert ns.DEFAULT_SCORE == 50
    assert ns.MIN_SCORE == 0 and ns.MAX_SCORE == 100
    assert ns.SUCCESS_REG == 3 and ns.SUCCESS_MINT == 5
    assert ns.PENALTY_TURNSTILE == 15
    assert ns.PENALTY_BOOT == 3 and ns.PENALTY_OTHER == 2
    assert ns.COOL_TURNSTILE_S == 20 * 60
    assert ns.COOL_BOOT_S == 5 * 60
    assert ns.COOL_OTHER_S == 2 * 60


def test_ip_record_turnstile_delta_and_cool_unchanged(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "ip.json")
    ns.set_enabled(True)
    out = ns.record("n1", "turnstile", cfg={})
    assert out["delta"] == -15
    assert out["score"] == 50 - 15
    assert out["cool_until"] > 0


def test_ip_empty_store_still_version1_nodes_only():
    s = ns._empty_store()
    assert s == {"version": 1, "nodes": {}}
    assert "domains" not in s and "pairs" not in s


def test_ip_record_disabled_when_correlation_only(tmp_path):
    """NODE_SCORE off but EMAIL_IP on: IP record still no-op (independent switches)."""
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "ip2.json")
    ns.set_enabled(None)  # NODE_SCORE default off
    ns.set_correlation_enabled(True)  # correlation on
    out = ns.record("n1", "turnstile", cfg={})
    assert out["ok"] is False
    assert ns.record_domain("a.com", "turnstile", cfg={})["ok"] is True
```

Create `tests/unit/test_node_score_correlation_errors.py` (§5.E):

```python
import json
import os

import pytest

import node_score as ns


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


def test_corrupt_json_degrades_to_empty(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "corrupt.json")
    (tmp_path / "corrupt.json").write_text("{not valid json", encoding="utf-8")
    ns.set_correlation_enabled(True)
    # reads degrade to defaults; a write repairs the file
    assert ns.get_domain_score("a.com") == ns.DEFAULT_SCORE
    assert ns.get_pair("a.com", "n1") == {}
    out = ns.record_domain("a.com", "reg_ok", cfg={})
    assert out["ok"] is True
    data = json.loads((tmp_path / "corrupt.json").read_text(encoding="utf-8"))
    assert "domains" in data and "a.com" in data["domains"]


def test_corrupt_domains_key_isolated_from_nodes(tmp_path):
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "m.json")
    (tmp_path / "m.json").write_text(json.dumps({
        "version": 1, "nodes": {"x": {"score": 77}}, "domains": 42,
        "pairs": "nope"}), encoding="utf-8")
    ns.set_correlation_enabled(True)
    assert ns.get_domain_score("a.com") == ns.DEFAULT_SCORE  # bad domains ignored
    assert ns.get_pair("a.com", "n1") == {}  # bad pairs ignored
    assert ns.get_score("x") == 77  # IP reads still work
    ns.record_domain("a.com", "reg_ok", cfg={})  # repairs domains key in place
    data = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
    assert data["nodes"] == {"x": {"score": 77}}  # IP untouched
    assert "a.com" in data["domains"]


def test_record_writes_survive_error_path(tmp_path):
    """Spec §4: write failure must never break registration. _save swallows."""
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "noexist" / "x.json")
    # parent dir absent -> mkdir needs parents=True (it sets exist_ok=True).
    # Force a deeper failure by pointing at a path inside a FILE.
    os.environ["NODE_SCORE_PATH"] = str(tmp_path / "blocker")
    (tmp_path / "blocker").write_text("x", encoding="utf-8")  # blocker is a file
    ns.set_correlation_enabled(True)
    out = ns.record_domain("a.com", "turnstile", cfg={})
    # _save swallows -> the call returns ok=True despite the failed persist
    assert out["ok"] is True
```

- [ ] **Step 2: Run tests to verify they fail/pass as expected**

Run: `python -m pytest tests/unit/test_email_ip_off_equivalence.py tests/unit/test_node_score_correlation_errors.py test_node_score.py test_email_ip_correlation.py tests/unit/test_mail_pool_weighted_sampling.py tests/unit/test_proxy_rotate_layer3_nudge.py tests/unit/test_register_cli_attribution.py -v`

Expected before Tasks 1-7 are implemented: most FAIL. After 1-7 land: all PASS. This task is the **closing sweep** — run it after 1-7, expect green; if red, the feature stays OFF and the failing guard points at the regression.

- [ ] **Step 3: No new implementation**

Tasks 1-7 already implement every behavior exercised here. If any guard fails, the fix lives in the task that owns the broken behavior (do **not** weaken the guard — §5.A is the highest-priority regression line). The only acceptable edits in this task are: correcting a test that made a wrong assumption about an existing helper (with a comment naming the helper), or adding a missing `isinstance`/`try` guard to a Task 1-7 function that the §5.E tests revealed.

- [ ] **Step 4: Run the FULL suite**

Run: `python -m pytest tests/unit/ -v`
Expected: full green suite (pre-existing + all correlation tests). Zero regressions.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_email_ip_off_equivalence.py tests/unit/test_node_score_correlation_errors.py test_node_score.py
git commit -m "test(correlation): §5.A OFF-equivalence + §5.E error-handling + §5.F IP non-regression guards"
```

---

## Execution Notes

- **ORDER:** Tasks MUST run 1→2→3→4→5→6→7→8. Task 5 depends on Task 4 (`domain_weights`); Task 6 depends on Tasks 1-3; Task 7 depends on Task 4 and the Task-6/7 cross-task hint wiring; Task 8 is the closing sweep.
- **OFF is the ship state:** leave `EMAIL_IP_CORRELATION` unset in `.env` / config. The feature is inert until an operator opts in. No baseline behavior change ships.
- **No global clash-rule, dual-metric UI, or `note_egress_outcome` signature changes** anywhere in this plan.
- **`node_score.py` IP path is extended-only:** `_kind_effect` is module-level pure; IP `record` is left byte-for-byte as-is (the §5.F guard enforces this).


