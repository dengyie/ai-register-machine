# free Build chat mid-tier probe (tebi CPA) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Product chat gate (`chat_ok` / inject) probes free Build through existing tebi CLIProxyAPI when safe; otherwise hybrid keeps direct entitlement stamps. Auth files still store cli-chat-proxy `base_url`.

**Architecture:** Add a `ProbeTransport` abstraction in `cpa_xai/probe.py` with `direct_bearer` (current) and `cpa_openai` (CPA OpenAI-compatible base + CPA API key). `probe_models` / `probe_mini_response` / `probe_chat_with_retries` take an optional transport. Because CPA pool routing may not pin a just-minted credential, default product mode is **hybrid**: entitlement gate remains direct-bearer with the mint token; optional post-path `probe_via_cpa_ok` is observational only and never replaces `chat_ok` for inject. Full `cpa` gate mode is only enabled when `cpa_probe_credential_pin` is configured and non-empty.

**Tech Stack:** Python 3 stdlib (`urllib`), existing `cpa_xai` package, config.json / env secrets, unit tests via `test_cpa_chat_entitlement_gate.py` style (no pytest required).

## Global Constraints

- Hard gate stays: `cpa_probe_chat` / `cpa_probe_chat_required` default true; 403 → `entitlement_denied`; no remint spin; no inject without `chat_ok=true`.
- Auth JSON `base_url` remains `https://cli-chat-proxy.grok.com/v1` (or config `cpa_base_url` upstream). Never write public CPA URL into auth files.
- Do not deploy standalone grokbuild-proxy; mid-tier = tebi CPA only.
- Do not change OAuth mint (PKCE/device), remint skip-denied, or ledger shape.
- No real secrets in git; config.example only placeholders + comments.
- Do not commit `_live_activation_test.py`.
- Classify CPA API-key 401/403 as ops/config (`auth_or_protocol` / non-entitlement), not `entitlement_denied`.
- If pin unavailable, **forbid** defaulting product gate to unpinned `cpa` (spec §6.2 / §9).

## File structure

| File | Responsibility |
|---|---|
| `cpa_xai/probe.py` | Transport types + direct/CPA HTTP; reuse `classify_chat_probe` |
| `cpa_xai/mint.py` | Pass probe transport options into probe helpers |
| `cpa_export.py` | Resolve probe config from cfg/env; wire mint + optional hybrid CPA smoke |
| `scripts/backfill_chat_stamps.py` | Same transport resolution as mint |
| `config.example.json` | Document new keys (placeholders only) |
| `test_cpa_chat_entitlement_gate.py` | Transport + hybrid + gate regression tests |
| (optional later) Obsidian free Build note | ops note mid-tier = tebi CPA — Manual/docs, not code gate |

---

### Task 1: Probe transport abstraction + CPA HTTP path

**Files:**
- Modify: `cpa_xai/probe.py`
- Test: `test_cpa_chat_entitlement_gate.py`

**Interfaces:**
- Consumes: existing `classify_chat_probe`, `_opener`, `DEFAULT_CLIENT_HEADERS`, `DEFAULT_BASE_URL`
- Produces:
  - `ProbeTransport` TypedDict / dataclass fields:
    - `mode: str` — `"direct"` | `"cpa"`
    - `base_url: str`
    - `api_key: str` — CPA gateway key when mode=`cpa`; empty for direct
    - `credential_pin: str` — optional header value for per-auth routing (email or auth filename); empty = unpinned
    - `pin_header: str` — header name if pin used; default `"X-CPA-Credential"` (overridable via config later)
  - `resolve_probe_transport(...)` pure helper (may live in probe.py or export; prefer probe.py for reuse)
  - `probe_models(..., transport: ProbeTransport | None = None)`
  - `probe_mini_response(..., transport: ProbeTransport | None = None)`
  - `probe_chat_with_retries(..., transport: ProbeTransport | None = None)`
  - `is_cpa_gateway_auth_error(result) -> bool` helper for failure mapping

- [ ] **Step 1: Write the failing tests**

Append to `test_cpa_chat_entitlement_gate.py`:

```python
def test_probe_transport_direct_uses_bearer_and_cli_headers() -> None:
    probe = _load("cpa_xai.probe_transport", ROOT / "cpa_xai" / "probe.py")
    assert hasattr(probe, "build_probe_transport")
    assert hasattr(probe, "probe_request_headers")
    t = probe.build_probe_transport(
        via="direct",
        upstream_base_url="https://cli-chat-proxy.grok.com/v1",
        cpa_base_url="https://cpa.example/v1",
        cpa_api_key="sk-cpa",
        access_token="tok-xai",
    )
    assert t["mode"] == "direct"
    assert t["base_url"].rstrip("/") == "https://cli-chat-proxy.grok.com/v1"
    h = probe.probe_request_headers(t, access_token="tok-xai")
    assert h["Authorization"] == "Bearer tok-xai"
    assert "x-grok-client-identifier" in h
    assert h.get("x-grok-client-identifier") == "grok-pager"
    print("PASS direct transport headers")


def test_probe_transport_cpa_uses_api_key_not_xai_token() -> None:
    probe = _load("cpa_xai.probe_transport2", ROOT / "cpa_xai" / "probe.py")
    t = probe.build_probe_transport(
        via="cpa",
        upstream_base_url="https://cli-chat-proxy.grok.com/v1",
        cpa_base_url="https://cpa.mangoq.ccwu.cc/v1",
        cpa_api_key="sk-cpa-key",
        access_token="tok-xai-should-not-be-auth",
        credential_pin="xai-user@example.com.json",
        pin_header="X-CPA-Credential",
    )
    assert t["mode"] == "cpa"
    assert t["base_url"].endswith("/v1") or "cpa.mangoq" in t["base_url"]
    h = probe.probe_request_headers(t, access_token="tok-xai-should-not-be-auth")
    assert h["Authorization"] == "Bearer sk-cpa-key"
    assert h.get("X-CPA-Credential") == "xai-user@example.com.json"
    # Must NOT send xAI access_token as Authorization when mode=cpa
    assert "tok-xai" not in h["Authorization"]
    print("PASS cpa transport headers")


def test_build_probe_transport_rejects_unpinned_cpa_as_gate_mode() -> None:
    """Spec §6: unpinned cpa must not be used as entitlement gate without hybrid."""
    probe = _load("cpa_xai.probe_transport3", ROOT / "cpa_xai" / "probe.py")
    # Prefer: resolve_gate_transport returns hybrid policy when via=cpa but no pin
    policy = probe.resolve_gate_probe_policy(
        via="cpa",
        cpa_base_url="https://cpa.example/v1",
        cpa_api_key="sk",
        credential_pin="",  # missing
        allow_unpinned_cpa_gate=False,
    )
    assert policy["gate_via"] == "direct"
    assert policy["cpa_smoke"] is True  # optional observational path allowed
    assert policy["reason"] in ("unpinned_cpa_hybrid", "hybrid")
    print("PASS unpinned cpa → hybrid policy")


def test_cpa_gateway_401_not_entitlement() -> None:
    probe = _load("cpa_xai.probe_cls_gw", ROOT / "cpa_xai" / "probe.py")
    out = {
        "ok": False,
        "status": 401,
        "error": "invalid api key",
        "transport_mode": "cpa",
        "error_code": "unauthorized",
    }
    # Either classify_chat_probe with transport_mode, or post-classify remap
    cls = probe.classify_chat_probe(out)
    if out.get("transport_mode") == "cpa" and out.get("status") in (401, 403):
        # gateway layer: not account entitlement
        remapped = probe.remap_cpa_gateway_failure(out, cls)
        assert remapped["entitlement_denied"] is False
        assert remapped["reason"] in ("auth_or_protocol", "cpa_gateway_auth")
    else:
        remapped = probe.remap_cpa_gateway_failure(out, cls)
        assert remapped["entitlement_denied"] is False
    print("PASS cpa gateway 401 not entitlement")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/mango/project/claude-project/grok-register
.venv/bin/python -u test_cpa_chat_entitlement_gate.py
```
Expected: FAIL / AttributeError on missing `build_probe_transport` / `resolve_gate_probe_policy` (or only old tests pass if new tests not yet invoked — ensure `main` calls the new tests).

- [ ] **Step 3: Implement transport helpers in `cpa_xai/probe.py`**

Add near top (after imports):

```python
from typing import Any, Mapping, MutableMapping

# Product default: never use unpinned CPA pool as chat entitlement truth.
DEFAULT_CPA_PIN_HEADER = "X-CPA-Credential"


def build_probe_transport(
    *,
    via: str,
    upstream_base_url: str = DEFAULT_BASE_URL,
    cpa_base_url: str = "",
    cpa_api_key: str = "",
    access_token: str = "",
    credential_pin: str = "",
    pin_header: str = DEFAULT_CPA_PIN_HEADER,
) -> dict[str, Any]:
    mode = (via or "direct").strip().lower()
    if mode not in {"direct", "cpa"}:
        mode = "direct"
    if mode == "cpa":
        base = (cpa_base_url or "").strip().rstrip("/")
        if not base:
            # Fail closed to direct if misconfigured
            mode = "direct"
            base = (upstream_base_url or DEFAULT_BASE_URL).rstrip("/")
        return {
            "mode": mode,
            "base_url": base,
            "api_key": (cpa_api_key or "").strip(),
            "credential_pin": (credential_pin or "").strip(),
            "pin_header": (pin_header or DEFAULT_CPA_PIN_HEADER).strip(),
            "upstream_base_url": (upstream_base_url or DEFAULT_BASE_URL).rstrip("/"),
        }
    return {
        "mode": "direct",
        "base_url": (upstream_base_url or DEFAULT_BASE_URL).rstrip("/"),
        "api_key": "",
        "credential_pin": "",
        "pin_header": "",
        "upstream_base_url": (upstream_base_url or DEFAULT_BASE_URL).rstrip("/"),
    }


def resolve_gate_probe_policy(
    *,
    via: str,
    cpa_base_url: str = "",
    cpa_api_key: str = "",
    credential_pin: str = "",
    allow_unpinned_cpa_gate: bool = False,
) -> dict[str, Any]:
    """Decide which path stamps chat_ok (gate) vs optional CPA smoke.

    Spec: unpinned CPA must not stamp chat_ok. Hybrid keeps gate=direct.
    """
    v = (via or "direct").strip().lower()
    if v not in {"direct", "cpa", "hybrid"}:
        v = "direct"
    pin = (credential_pin or "").strip()
    has_cpa = bool((cpa_base_url or "").strip() and (cpa_api_key or "").strip())

    if v == "direct" or not has_cpa:
        return {
            "gate_via": "direct",
            "cpa_smoke": False,
            "reason": "direct" if v == "direct" else "cpa_config_missing",
        }
    if v == "hybrid":
        return {"gate_via": "direct", "cpa_smoke": has_cpa, "reason": "hybrid"}
    # via == cpa
    if pin or allow_unpinned_cpa_gate:
        return {
            "gate_via": "cpa",
            "cpa_smoke": False,
            "reason": "cpa_pinned" if pin else "cpa_unpinned_allowed",
        }
    return {"gate_via": "direct", "cpa_smoke": has_cpa, "reason": "unpinned_cpa_hybrid"}


def probe_request_headers(
    transport: Mapping[str, Any],
    *,
    access_token: str,
) -> dict[str, str]:
    mode = str(transport.get("mode") or "direct")
    if mode == "cpa":
        key = str(transport.get("api_key") or "").strip()
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        pin = str(transport.get("credential_pin") or "").strip()
        pin_h = str(transport.get("pin_header") or DEFAULT_CPA_PIN_HEADER).strip()
        if pin and pin_h:
            headers[pin_h] = pin
        return headers
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        **DEFAULT_CLIENT_HEADERS,
    }


def remap_cpa_gateway_failure(
    raw: Mapping[str, Any],
    classified: Mapping[str, Any],
) -> dict[str, Any]:
    """CPA API-key / gateway failures must not stamp entitlement_denied."""
    out = dict(classified)
    if str(raw.get("transport_mode") or "") != "cpa":
        return out
    status = int(raw.get("status") or 0)
    if status in (401, 403) and not raw.get("upstream_entitlement"):
        # Treat as gateway/config unless body clearly is free-Build permission-denied
        err = str(raw.get("error") or "").lower()
        if "permission" in err and "denied" in err and "console.x.ai" in err:
            out["upstream_entitlement"] = True
            return out
        if status == 401 or "api key" in err or "unauthorized" in err or "invalid key" in err:
            out["entitlement_denied"] = False
            out["retryable"] = False
            out["reason"] = "cpa_gateway_auth"
            out["error_code"] = str(raw.get("error_code") or status)
            return out
        # Ambiguous CPA 403: still do not mark entitlement without clear body
        if status == 403 and "permission" not in err:
            out["entitlement_denied"] = False
            out["retryable"] = True
            out["reason"] = "cpa_gateway_error"
    return out
```

Update `probe_models` and `probe_mini_response` signatures to accept optional `transport: dict | None = None`. When `transport` is None, behave exactly as today (direct + `base_url` arg + DEFAULT_CLIENT_HEADERS).

Core change pattern for `probe_mini_response`:

```python
def probe_mini_response(
    access_token: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 60.0,
    proxy: str | None = None,
    transport: dict[str, Any] | None = None,
) -> dict[str, Any]:
    t = transport or build_probe_transport(
        via="direct",
        upstream_base_url=base_url,
        access_token=access_token,
    )
    base = str(t.get("base_url") or base_url).rstrip("/")
    url = f"{base}/responses"
    payload = {
        "model": "grok-4.5",
        "stream": False,
        "input": "Reply with exactly MINT_OK",
        "reasoning": {"effort": "low"},
    }
    headers = probe_request_headers(t, access_token=access_token)
    # ensure Content-Type for POST
    headers.setdefault("Content-Type", "application/json")
    opener = _opener(proxy)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            texts: list[str] = []
            for item in body.get("output") or []:
                if item.get("type") == "message":
                    for c in item.get("content") or []:
                        if c.get("type") == "output_text":
                            texts.append(c.get("text") or "")
            out = {
                "ok": True,
                "status": getattr(resp, "status", 200),
                "model": body.get("model"),
                "text": "\n".join(texts),
                "usage": body.get("usage"),
                "transport_mode": t.get("mode") or "direct",
            }
            cls = classify_chat_probe(out)
            out.update(cls)
            return out
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:800]
        out = {
            "ok": False,
            "status": e.code,
            "error": err_body,
            "error_code": _extract_error_code(err_body),
            "transport_mode": t.get("mode") or "direct",
        }
        cls = classify_chat_probe(out)
        out.update(remap_cpa_gateway_failure(out, cls))
        return out
    except Exception as e:  # noqa: BLE001
        out = {
            "ok": False,
            "status": 0,
            "error": str(e),
            "transport_mode": t.get("mode") or "direct",
        }
        out.update(classify_chat_probe(out))
        return out
```

Mirror the same `transport` / `transport_mode` / `remap_cpa_gateway_failure` pattern in `probe_models` (GET `/models`).

Update `probe_chat_with_retries` to pass `transport=` through to `probe_mini_response`.

- [ ] **Step 4: Wire new tests into main() and run**

Ensure bottom of `test_cpa_chat_entitlement_gate.py` `main` calls the four new tests. Run:

```bash
.venv/bin/python -u test_cpa_chat_entitlement_gate.py
```

Expected: ALL PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add cpa_xai/probe.py test_cpa_chat_entitlement_gate.py
git commit -m "feat(cpa): probe transport direct vs CPA with hybrid gate policy"
```

---

### Task 2: Config surface + mint/export/backfill wiring

**Files:**
- Modify: `cpa_xai/mint.py`
- Modify: `cpa_export.py`
- Modify: `scripts/backfill_chat_stamps.py`
- Modify: `config.example.json`
- Test: `test_cpa_chat_entitlement_gate.py`

**Interfaces:**
- Consumes: `build_probe_transport`, `resolve_gate_probe_policy`, `probe_*` with `transport=`
- Produces:
  - Config keys (example.json + runtime):
    - `cpa_probe_via`: `"direct"` | `"cpa"` | `"hybrid"` — **default `"hybrid"`** until pin proven in ops
    - `cpa_probe_base_url`: e.g. `https://cpa.mangoq.ccwu.cc/v1` or tunnel `http://127.0.0.1:8317/v1`
    - `cpa_probe_api_key`: empty in example; prefer env `CPA_PROBE_API_KEY`
    - `cpa_probe_credential_pin_mode`: `"none"` | `"email"` | `"auth_filename"` — default `"auth_filename"` when via=cpa
    - `cpa_probe_pin_header`: default `X-CPA-Credential`
    - `cpa_probe_allow_unpinned_cpa_gate`: default `false` (safety)
  - `mint_and_export(..., probe_via=..., cpa_probe_base_url=..., cpa_probe_api_key=..., probe_credential_pin=..., ...)`
  - Result fields: existing stamps + optional `probe_via_cpa_ok: bool | None`, `probe_gate_via: str`, `probe_policy_reason: str`

- [ ] **Step 1: Failing tests for config wiring + hybrid result fields**

```python
def test_config_example_documents_mid_tier_probe_keys() -> None:
    raw = (ROOT / "config.example.json").read_text(encoding="utf-8")
    assert "cpa_probe_via" in raw
    assert "cpa_probe_base_url" in raw
    assert "cpa_probe_api_key" in raw
    assert "cpa_probe_credential_pin_mode" in raw
    # base_url semantics unchanged
    assert "cli-chat-proxy.grok.com" in raw
    print("PASS config.example mid-tier keys")


def test_mint_passes_transport_kwargs_signature() -> None:
    src = (ROOT / "cpa_xai" / "mint.py").read_text(encoding="utf-8")
    assert "probe_via" in src
    assert "build_probe_transport" in src or "resolve_gate_probe_policy" in src
    assert "probe_via_cpa_ok" in src or "cpa_smoke" in src
    # Must not rewrite base_url to CPA public host for auth write
    assert "build_cpa_xai_auth" in src
    print("PASS mint transport kwargs signature")


def test_export_resolves_env_api_key() -> None:
    src = (ROOT / "cpa_export.py").read_text(encoding="utf-8")
    assert "cpa_probe_via" in src
    assert "CPA_PROBE_API_KEY" in src
    assert "resolve_gate_probe_policy" in src
    print("PASS export mid-tier resolve")
```

- [ ] **Step 2: Run to verify fail**

```bash
.venv/bin/python -u -c "
import test_cpa_chat_entitlement_gate as t
t.test_config_example_documents_mid_tier_probe_keys()
"
```
Expected: AssertionError on missing keys.

- [ ] **Step 3: Extend `config.example.json`**

After `cpa_probe_chat_required` block, insert:

```json
  "// cpa_probe_via": "chat gate transport: direct=本地直连 cli-chat-proxy（回归默认路径）；cpa=经 tebi CPA（需 credential pin，否则自动 hybrid）；hybrid=门禁仍 direct，成功后可选 CPA smoke 写 probe_via_cpa_ok（不替代 chat_ok）。生产在 pin 能力确认前推荐 hybrid。",
  "cpa_probe_via": "hybrid",
  "// cpa_probe_base_url": "CPA OpenAI-compatible base（含 /v1）。公网 https://cpa.mangoq.ccwu.cc/v1 或与 inject 一致的 tunnel/内网 http://127.0.0.1:8317/v1。不要写进 xai-*.json base_url。",
  "cpa_probe_base_url": "",
  "// cpa_probe_api_key": "调用 CPA 的 API key（不是 xAI access_token）。优先 env CPA_PROBE_API_KEY；禁止提交真实 key。",
  "cpa_probe_api_key": "",
  "// cpa_probe_credential_pin_mode": "cpa 门禁钉凭据：none|email|auth_filename。无 pin 且 via=cpa 时强制 hybrid。",
  "cpa_probe_credential_pin_mode": "auth_filename",
  "// cpa_probe_pin_header": "钉凭据请求头名；取决于 tebi CLIProxyAPI 版本是否支持。不支持则保持 hybrid。",
  "cpa_probe_pin_header": "X-CPA-Credential",
  "// cpa_probe_allow_unpinned_cpa_gate": "危险开关：true 允许无 pin 的 CPA 池结果盖 chat_ok（假阳/假阴）。默认 false。",
  "cpa_probe_allow_unpinned_cpa_gate": false,
```

- [ ] **Step 4: Wire `mint_and_export`**

Add kwargs (defaults preserve current behavior):

```python
    probe_via: str = "direct",
    cpa_probe_base_url: str = "",
    cpa_probe_api_key: str = "",
    probe_credential_pin: str = "",
    probe_pin_header: str = "X-CPA-Credential",
    allow_unpinned_cpa_gate: bool = False,
```

In the probe section after write:

```python
    from .probe import (
        apply_chat_probe_to_result,
        build_probe_transport,
        probe_chat_with_retries,
        probe_models,
        resolve_gate_probe_policy,
    )

    policy = resolve_gate_probe_policy(
        via=probe_via,
        cpa_base_url=cpa_probe_base_url,
        cpa_api_key=cpa_probe_api_key,
        credential_pin=probe_credential_pin,
        allow_unpinned_cpa_gate=allow_unpinned_cpa_gate,
    )
    result["probe_gate_via"] = policy["gate_via"]
    result["probe_policy_reason"] = policy["reason"]
    gate_transport = build_probe_transport(
        via=policy["gate_via"],
        upstream_base_url=base_url,
        cpa_base_url=cpa_probe_base_url,
        cpa_api_key=cpa_probe_api_key,
        access_token=tokens["access_token"],
        credential_pin=probe_credential_pin,
        pin_header=probe_pin_header,
    )
    log(
        f"probe policy gate_via={policy['gate_via']} cpa_smoke={policy['cpa_smoke']} "
        f"reason={policy['reason']}"
    )

    run_models = bool(probe or probe_chat)
    if run_models:
        pr = probe_models(
            tokens["access_token"],
            base_url=base_url,
            proxy=resolved or None,
            transport=gate_transport,
        )
        # ... existing models handling ...
        if probe_chat and pr.get("has_grok_45"):
            ch = probe_chat_with_retries(
                tokens["access_token"],
                base_url=base_url,
                proxy=resolved or None,
                max_attempts=3,
                log=log,
                transport=gate_transport,
            )
            apply_chat_probe_to_result(result, ch)
            # ... existing entitlement log ...

    # Optional observational CPA smoke — NEVER sets chat_ok by itself
    if policy.get("cpa_smoke") and result.get("chat_ok") is True:
        smoke_t = build_probe_transport(
            via="cpa",
            upstream_base_url=base_url,
            cpa_base_url=cpa_probe_base_url,
            cpa_api_key=cpa_probe_api_key,
            access_token=tokens["access_token"],
            credential_pin=probe_credential_pin,
            pin_header=probe_pin_header,
        )
        try:
            smoke = probe_mini_response(
                tokens["access_token"],
                base_url=base_url,
                proxy=resolved or None,
                transport=smoke_t,
            )
            result["probe_via_cpa_ok"] = bool(smoke.get("ok"))
            result["probe_via_cpa"] = smoke
            log(f"cpa smoke ok={smoke.get('ok')} status={smoke.get('status')}")
        except Exception as e:  # noqa: BLE001
            result["probe_via_cpa_ok"] = False
            result["probe_via_cpa_error"] = str(e)[:200]
```

Important: `build_cpa_xai_auth(..., base_url=base_url)` keeps using upstream `cpa_base_url` config (cli-chat-proxy), **not** `cpa_probe_base_url`.

- [ ] **Step 5: Wire `cpa_export.py` config resolution**

Near other probe flags (~852):

```python
    probe_via = str(cfg.get("cpa_probe_via") or "hybrid").strip().lower() or "hybrid"
    cpa_probe_base_url = (
        str(cfg.get("cpa_probe_base_url") or os.environ.get("CPA_PROBE_BASE_URL") or "")
        .strip()
    )
    cpa_probe_api_key = (
        str(
            cfg.get("cpa_probe_api_key")
            or os.environ.get("CPA_PROBE_API_KEY")
            or ""
        ).strip()
    )
    pin_mode = str(cfg.get("cpa_probe_credential_pin_mode") or "auth_filename").strip().lower()
    pin_header = str(cfg.get("cpa_probe_pin_header") or "X-CPA-Credential").strip()
    allow_unpinned = _config_bool(cfg.get("cpa_probe_allow_unpinned_cpa_gate"), default=False)

    # pin value computed after path known — pass email/filename into mint
    # For mint call, precompute from email:
    from cpa_xai.schema import credential_file_name
    if pin_mode == "email":
        probe_pin = email
    elif pin_mode == "auth_filename":
        probe_pin = credential_file_name(email)  # existing helper if present; else f"xai-{email}.json"
    else:
        probe_pin = ""

    result = mint_and_export(
        ...
        probe_via=probe_via,
        cpa_probe_base_url=cpa_probe_base_url,
        cpa_probe_api_key=cpa_probe_api_key,
        probe_credential_pin=probe_pin,
        probe_pin_header=pin_header,
        allow_unpinned_cpa_gate=allow_unpinned,
    )
```

If `credential_file_name` is not exported, use:

```python
def _auth_filename_for_email(email: str) -> str:
    # Mirror writer/schema naming: xai-<sanitized-email>.json
    from cpa_xai.schema import credential_file_name
    return credential_file_name(email)
```

Verify `schema.credential_file_name` exists (it is imported in `__init__.py`). Use it.

- [ ] **Step 6: Wire `scripts/backfill_chat_stamps.py`**

In `_probe_and_stamp` and `main`, read same config keys; build gate transport via `resolve_gate_probe_policy` + `build_probe_transport`; pass `transport=` to `probe_models` / `probe_chat_with_retries`. Default via from config (`hybrid`). Add CLI:

```python
ap.add_argument("--probe-via", default="", help="Override cpa_probe_via: direct|cpa|hybrid")
```

Resolve: `via = args.probe_via or cfg.get("cpa_probe_via") or "hybrid"`.

- [ ] **Step 7: Run full unit suite for CPA gate**

```bash
.venv/bin/python -u test_cpa_chat_entitlement_gate.py
.venv/bin/python -u test_cpa_one_click_chain.py
.venv/bin/python -u test_cpa_remote_inject.py
.venv/bin/python -m py_compile cpa_xai/probe.py cpa_xai/mint.py cpa_export.py scripts/backfill_chat_stamps.py
```

Expected: ALL PASS.

- [ ] **Step 8: Commit**

```bash
git add cpa_xai/mint.py cpa_export.py scripts/backfill_chat_stamps.py config.example.json test_cpa_chat_entitlement_gate.py
git commit -m "feat(cpa): wire hybrid/CPA mid-tier probe config into mint export backfill"
```

---

### Task 3: Inject gate regression + docs acceptance checks

**Files:**
- Modify: `test_cpa_chat_entitlement_gate.py` (inject still requires `chat_ok`; CPA smoke alone insufficient)
- Modify: `cpa_xai/writer.py` only if optional stamp field `probe_via_cpa_ok` should persist (recommended: stamp if present, never required for inject)

**Interfaces:**
- Consumes: `evaluate_remote_inject_gate` / `stamp_auth_chat_fields`
- Produces: stamp may include `probe_via_cpa_ok` when present; inject gate ignores it

- [ ] **Step 1: Write failing inject regression**

```python
def test_inject_gate_ignores_probe_via_cpa_ok_alone() -> None:
    """chat_ok remains sole product gate; CPA smoke cannot unlock inject."""
    export = _load("cpa_export_gate", ROOT / "cpa_export.py")
    # denied account with smoke true must still block
    r = {
        "ok": False,
        "chat_ok": False,
        "entitlement_denied": True,
        "probe_via_cpa_ok": True,
        "path": "/tmp/xai-x.json",
        "email": "x@e.com",
    }
    gate = export.evaluate_remote_inject_gate(
        r,
        {
            "cpa_remote_inject": True,
            "cpa_remote_inject_require_chat_ok": True,
            "cpa_probe_chat": True,
        },
    )
    assert gate.get("allow") is False

    # chat_ok true may allow even if smoke false/missing
    r2 = {
        "ok": True,
        "chat_ok": True,
        "entitlement_denied": False,
        "probe_via_cpa_ok": False,
        "path": "/tmp/xai-y.json",
        "email": "y@e.com",
    }
    gate2 = export.evaluate_remote_inject_gate(
        r2,
        {
            "cpa_remote_inject": True,
            "cpa_remote_inject_require_chat_ok": True,
            "cpa_probe_chat": True,
        },
    )
    assert gate2.get("allow") is True
    print("PASS inject gate ignores cpa smoke")
```

- [ ] **Step 2: Run — should pass if evaluate_remote_inject_gate only checks chat_ok (likely already)**

If it fails because `ok` false blocks, adjust fixture `ok=True`/`chat_ok=False` combo to match real gate function semantics — read `evaluate_remote_inject_gate` and assert the actual `chat_ok is True` requirement still holds.

- [ ] **Step 3: Optional stamp field in writer**

In `build_chat_stamp_from_result` / `stamp_auth_chat_fields`, if `result` has `probe_via_cpa_ok` key, copy it into stamp dict. Do **not** require it for completeness. Do **not** let missing `probe_via_cpa_ok` fail stamp.

```python
    if "probe_via_cpa_ok" in r:
        stamp["probe_via_cpa_ok"] = bool(r.get("probe_via_cpa_ok"))
    if r.get("probe_gate_via"):
        stamp["probe_gate_via"] = str(r.get("probe_gate_via"))
```

- [ ] **Step 4: Source-level acceptance checks**

```python
def test_auth_write_base_url_not_cpa_public() -> None:
    mint_src = (ROOT / "cpa_xai" / "mint.py").read_text(encoding="utf-8")
    # build_cpa_xai_auth must use base_url param (upstream), not cpa_probe_base_url
    assert "build_cpa_xai_auth(" in mint_src
    assert "base_url=base_url" in mint_src
    assert "base_url=cpa_probe_base_url" not in mint_src
    print("PASS auth write base_url remains upstream")
```

- [ ] **Step 5: Full verification**

```bash
.venv/bin/python -u test_cpa_chat_entitlement_gate.py
.venv/bin/python -u test_cpa_one_click_chain.py
.venv/bin/python -u test_cpa_remote_inject.py
.venv/bin/python -u test_cpa_pkce_mint.py
.venv/bin/python -c "from cpa_xai.probe import build_probe_transport, resolve_gate_probe_policy; print(resolve_gate_probe_policy(via='cpa', cpa_base_url='http://x/v1', cpa_api_key='k', credential_pin=''))"
```

Expected: policy `gate_via=direct`, `cpa_smoke=True`, `reason=unpinned_cpa_hybrid`.

- [ ] **Step 6: Commit**

```bash
git add cpa_xai/writer.py test_cpa_chat_entitlement_gate.py
git commit -m "test(cpa): inject hard gate ignores CPA smoke; stamp optional probe_via_cpa_ok"
```

---

### Task 4: Manual-required pin capability check (ops, not product code)

**Files:**
- Create (optional ops note only if writing vault): none required in repo for milestone close
- Or append short section to plan completion summary

- [ ] **Step 1: Read-only check tebi CPA for credential pin**

On operator machine (not required for unit green):

```bash
# Prefer tunnel consistent with inject
ssh tebi-tunnel 'curl -sS http://127.0.0.1:8317/v1/models -H "Authorization: Bearer $CPA_API_KEY" | head -c 200'
# Document whether any header selects auth file (search CPA docs / binary help)
ssh tebi-tunnel 'cli-proxy-api --help 2>/dev/null | head -50 || true'
```

Record outcome in commit message or ops note:
- If pin header exists → ops may set `cpa_probe_via=cpa` + pin mode after smoke.
- If not → keep `hybrid` (code default).

- [ ] **Step 2: No code change if pin absent**

Do not invent fake pin behavior. Default hybrid already implements safe path.

- [ ] **Step 3: Milestone stop**

Do **not** auto-deploy to pxed in this plan unless user explicitly asks after green tests. Deploy remains Manual-required follow-up:
- tar/scp registerer code to `/personal/grok-register`
- set `CPA_PROBE_API_KEY` on pxed env / config.json (not git)
- small backfill smoke with `--probe-via hybrid`

---

## Self-review (plan vs spec)

| Spec section | Task coverage |
|---|---|
| §2 Goals mid-tier via tebi CPA | Task 1–2 transport + config |
| §2 auth base_url stays cli-chat-proxy | Task 2 mint write; Task 3 assert |
| §2 hard gate kept | Task 3 inject regression |
| §2 debug direct switch | `cpa_probe_via=direct` |
| §3 non-goals (no new proxy, no soft gate, no OAuth change) | Global constraints; tasks avoid |
| §6 pinning + hybrid fallback | Task 1 `resolve_gate_probe_policy` |
| §6.2 forbid live-inject-to-probe loop | No inject-before-probe in code |
| §7 config surface | Task 2 example.json + export |
| §8 touch map | Tasks 1–3 |
| §9 failure mapping gateway vs entitlement | Task 1 `remap_cpa_gateway_failure` |
| §11 acceptance | Task 3 verification commands |
| §13 open questions | Task 4 manual pin check; default hybrid + tunnel preference documented in config comments |

Placeholder scan: none. Types: `build_probe_transport` / `resolve_gate_probe_policy` consistent across tasks.

## Default freeze (implementation decisions)

| Decision | Value | Why |
|---|---|---|
| Default `cpa_probe_via` | `hybrid` | Pin capability unconfirmed; safe for chat_ok truth |
| Default pin mode | `auth_filename` | Stable id for future pin header |
| Env for key | `CPA_PROBE_API_KEY` | Avoid secrets in git |
| Smoke field | `probe_via_cpa_ok` optional stamp | Observability only |
| Gate on smoke | never | Spec §6 / §11 |

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-16-cpa-mid-tier-probe.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks  
2. **Inline Execution** — execute tasks in this session with checkpoints  

**Which approach?**
