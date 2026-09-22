"""typesafe.ai Stytch magic-link + console API-key protocol.

Ported from Futureppo/typesafe_register (Apache-2.0) as an in-process flow.
Mailbox comes from register_core EmailSource — do not use the original
512/256 farm defaults or TEMPMAIL placeholders.

Success = this-run API key. Never treat historical accounts.json as ok.
"""

from __future__ import annotations

import html
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from register_core.decode.extract import extract_typesafe_magic_link
from register_core.errors import MailMissError

from .constants import (
    CONSOLE_BASE_URL,
    CONSOLE_DEPLOYMENT_ID,
    DEFAULT_API_KEY_NAME,
    DEFAULT_TIMEOUT,
    LOGIN_PAGE_URL,
)

LogFn = Callable[[str], None]
_deployment_drift_logged = False


class TypesafeRegisterError(RuntimeError):
    """Single-attempt registration failure (caller maps to RegisterResult)."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "provider",
        step: str = "",
        steps: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.step = step or ""
        self.steps: dict[str, Any] = dict(steps or {})


@dataclass
class TypesafeResult:
    ok: bool
    email: str = ""
    api_key: str = ""
    api_key_id: str = ""
    user_id: str = ""
    organization_id: str = ""
    organization_name: str = ""
    magic_link: str = ""
    error: str = ""
    error_kind: str = ""
    fail_step: str = ""
    steps: dict[str, Any] = field(default_factory=dict)
    deployment_id: str = ""
    deployment_mismatch: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "email": self.email,
            "api_key": _preview(self.api_key),
            "api_key_id": self.api_key_id,
            "user_id": self.user_id,
            "organization_id": self.organization_id,
            "error": self.error,
            "error_kind": self.error_kind,
            "fail_step": self.fail_step,
            "step_keys": sorted(self.steps.keys()),
            "deployment_mismatch": self.deployment_mismatch,
        }


def _preview(value: str) -> str:
    s = value or ""
    if not s:
        return ""
    if len(s) <= 8:
        return "***"
    return f"{s[:4]}…{s[-4:]}(len={len(s)})"


def _action_snippet(page: str, limit: int = 400) -> str:
    """Short $ACTION window for session failures — not the full login HTML."""
    raw = str(page or "")
    if not raw:
        return ""
    idx = raw.find("$ACTION_")
    if idx < 0:
        idx = raw.find('type="email"')
    if idx < 0:
        return raw[:limit]
    start = max(0, idx - 80)
    return raw[start : start + limit]


def parse_login_action(page: str) -> dict[str, str]:
    """Parse Next.js $ACTION_* blob + action id from the console login HTML."""
    raw = str(page or "")
    if not raw.strip():
        raise TypesafeRegisterError("login page empty", kind="session", step="login_action")
    pos = raw.find('type="email"')
    window = raw[:pos] if pos >= 0 else raw
    indexes = re.findall(r"\\?\$ACTION_(\d+):0", window)
    if not indexes:
        indexes = re.findall(r"\$ACTION_(\d+):0", window)
    if not indexes:
        raise TypesafeRegisterError(
            "login page missing $ACTION index",
            kind="session",
            step="login_action",
            steps={"login_action": {"ok": False, "html_snippet": _action_snippet(raw)}},
        )
    index = indexes[-1]
    ref_m = re.search(rf'\\?\$ACTION_{re.escape(index)}:0" value="([^"]+)"', raw)
    if not ref_m:
        ref_m = re.search(rf'\$ACTION_{re.escape(index)}:0" value="([^"]+)"', raw)
    if not ref_m:
        raise TypesafeRegisterError(
            "login page missing $ACTION ref",
            kind="session",
            step="login_action",
            steps={"login_action": {"ok": False, "html_snippet": _action_snippet(raw)}},
        )
    try:
        ref = json.loads(html.unescape(ref_m.group(1)))
    except Exception as exc:
        raise TypesafeRegisterError(
            f"login action ref json: {exc}",
            kind="session",
            step="login_action",
        ) from exc
    action_id = str((ref or {}).get("id") or "").strip()
    if not action_id:
        raise TypesafeRegisterError(
            "login action id empty",
            kind="session",
            step="login_action",
        )
    blob_m = re.search(rf'\\?\$ACTION_{re.escape(index)}:2" value="([^"]+)"', raw)
    if not blob_m:
        blob_m = re.search(rf'\$ACTION_{re.escape(index)}:2" value="([^"]+)"', raw)
    if not blob_m:
        raise TypesafeRegisterError(
            "login page missing encrypted action blob",
            kind="session",
            step="login_action",
            steps={"login_action": {"ok": False, "html_snippet": _action_snippet(raw)}},
        )
    return {
        "action_id": action_id,
        "index": index,
        "blob": html.unescape(blob_m.group(1)),
    }


def parse_deployment_id(page: str) -> str:
    m = re.search(r"dpl=([0-9a-f]{20,})", str(page or ""))
    return m.group(1) if m else ""


def extract_magic_link(blob: str, subject: str = "") -> dict[str, str]:
    """Return {public_token, token, magic_link} or empty dict."""
    hay = "\n".join([str(subject or ""), str(blob or "")])
    parsed = extract_typesafe_magic_link(hay)
    if not parsed:
        return {}
    return parsed


def _usable_login_action(action: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the action dict only when send_magic_link can POST it."""
    if not isinstance(action, dict):
        return None
    if not str(action.get("action_id") or "").strip():
        return None
    if not str(action.get("index") or "").strip():
        return None
    if not str(action.get("blob") or "").strip():
        return None
    return action


def _classify_login_http(status: int, *, step: str, label: str) -> None:
    """5xx / no status = network; 4xx = session. Do not parse error bodies as login HTML."""
    if status <= 0:
        raise TypesafeRegisterError(f"{label} no http status", kind="network", step=step)
    if status >= 500:
        raise TypesafeRegisterError(f"{label} HTTP {status}", kind="network", step=step)
    if status >= 400:
        raise TypesafeRegisterError(f"{label} HTTP {status}", kind="session", step=step)


def _fetch_login_page_action(
    session: Any,
    *,
    log: LogFn | None = None,
    step: str = "login_action",
    label: str = "login GET",
    emit_status: bool = False,
) -> tuple[dict[str, Any], int]:
    emit = log or (lambda _m: None)
    resp, err = _get(session, LOGIN_PAGE_URL, headers={"accept": "text/html,application/xhtml+xml"})
    if resp is None:
        raise TypesafeRegisterError(f"{label} failed: {err}", kind="network", step=step)
    status = int(getattr(resp, "status_code", 0) or 0)
    if emit_status:
        emit(f"{step} status={status}")
    _classify_login_http(status, step=step, label=label)
    page = getattr(resp, "text", "") or ""
    dpl = parse_deployment_id(page)
    if dpl and dpl != CONSOLE_DEPLOYMENT_ID:
        # Once per process. n=100 otherwise logs the same drift on every GET.
        global _deployment_drift_logged
        if not _deployment_drift_logged:
            _deployment_drift_logged = True
            emit(f"console deployment changed {CONSOLE_DEPLOYMENT_ID} -> {dpl}")
    action = parse_login_action(page)
    action["deployment_id"] = dpl
    action["deployment_mismatch"] = bool(dpl and dpl != CONSOLE_DEPLOYMENT_ID)
    return action, status


def probe_console(
    session: Any | None = None,
    *,
    proxy: str = "",
    log: LogFn | None = None,
) -> dict[str, Any]:
    """GET + parse the login page *before* mailbox allocate.

    Clash/list preflight is skipped for ``egress=clash``. A dead mixed port
    would otherwise burn a tinyhost address on the first login GET. HTTP 5xx
    is ``network``; 4xx is ``session``; parse failure is ``session``. The
    parsed ``action`` is returned so ``register_one`` can POST without a
    second login GET after allocate.
    """
    from .session import create_session

    sess = session if session is not None else create_session(proxy)
    action, status = _fetch_login_page_action(
        sess,
        log=log,
        step="console_probe",
        label="console probe",
        emit_status=True,
    )
    return {
        "ok": True,
        "status": status,
        "index": action.get("index"),
        "deployment_id": action.get("deployment_id") or "",
        "url": LOGIN_PAGE_URL,
        "action": {
            "action_id": action["action_id"],
            "index": action["index"],
            "blob": action["blob"],
            "deployment_id": str(action.get("deployment_id") or ""),
            "deployment_mismatch": bool(action.get("deployment_mismatch")),
        },
    }


def login_action(session: Any, *, log: LogFn | None = None) -> dict[str, Any]:
    action, _status = _fetch_login_page_action(
        session,
        log=log,
        step="login_action",
        label="login GET",
        emit_status=False,
    )
    return action


def send_magic_link(
    session: Any,
    email: str,
    *,
    action: dict[str, Any] | None = None,
    log: LogFn | None = None,
) -> dict[str, str]:
    emit = log or (lambda _m: None)
    last_status = 0
    last_redirect = ""
    for refresh in (False, True):
        current = action if (action and not refresh) else login_action(session, log=emit)
        action = current
        boundary = "----WebKitFormBoundary" + uuid.uuid4().hex[:16]
        idx = current["index"]
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"1\"\r\n\r\n{current['blob']}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"_{idx}_email\"\r\n\r\n{email}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"0\"\r\n\r\n[\"$@1\",\"$K{idx}\"]\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        resp, err = _post(
            session,
            LOGIN_PAGE_URL,
            data=body,
            headers={
                "content-type": f"multipart/form-data; boundary={boundary}",
                "next-action": current["action_id"],
                "accept": "text/x-component",
                "origin": CONSOLE_BASE_URL,
                "referer": LOGIN_PAGE_URL,
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
            },
        )
        if resp is None:
            raise TypesafeRegisterError(
                f"send magic link failed: {err}",
                kind="network",
                step="send_magic_link",
            )
        last_status = int(getattr(resp, "status_code", 0) or 0)
        last_redirect = str(resp.headers.get("x-action-redirect", "") or "")
        if last_status == 200 and "sent=true" in last_redirect:
            return {
                "action_id": current["action_id"],
                "form_index": idx,
                "x_action_redirect": last_redirect,
                "deployment_id": str(current.get("deployment_id") or ""),
                "deployment_mismatch": bool(current.get("deployment_mismatch")),
            }
        emit(f"send_magic_link retry refresh={refresh} status={last_status}")
    raise TypesafeRegisterError(
        f"send magic link failed: HTTP {last_status} redirect={last_redirect!r}",
        kind="session",
        step="send_magic_link",
    )


def auth_callback(session: Any, token: str) -> dict[str, Any]:
    token = (token or "").strip()
    if not token:
        raise TypesafeRegisterError("empty magic-link token", kind="oauth_callback", step="callback")
    resp, err = _post(
        session,
        f"{CONSOLE_BASE_URL}/api/auth/callback",
        json={"token": token, "tokenType": "magic_links", "preferredOrgId": None},
        headers={
            "content-type": "application/json",
            "accept": "*/*",
            "origin": CONSOLE_BASE_URL,
            "referer": f"{CONSOLE_BASE_URL}/auth/callback?stytch_token_type=magic_links",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
        },
    )
    if resp is None:
        raise TypesafeRegisterError(f"auth callback failed: {err}", kind="network", step="callback")
    status = int(getattr(resp, "status_code", 0) or 0)
    if status >= 400:
        raise TypesafeRegisterError(
            f"auth callback HTTP {status}",
            kind="oauth_callback",
            step="callback",
        )
    from .session import response_json

    data = response_json(resp)
    if not data:
        raise TypesafeRegisterError("auth callback empty json", kind="oauth_callback", step="callback")
    return data


def create_api_key(session: Any, name: str | None = None) -> dict[str, Any]:
    key_name = (name or os.environ.get("TYPESAFE_API_KEY_NAME") or DEFAULT_API_KEY_NAME).strip()
    resp, err = _post(
        session,
        f"{CONSOLE_BASE_URL}/api/api-keys",
        json={"name": key_name},
        headers={
            "content-type": "application/json",
            "accept": "*/*",
            "origin": CONSOLE_BASE_URL,
            "referer": f"{CONSOLE_BASE_URL}/keys",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
        },
    )
    if resp is None:
        raise TypesafeRegisterError(f"create api key failed: {err}", kind="network", step="api_key")
    status = int(getattr(resp, "status_code", 0) or 0)
    if status >= 400:
        raise TypesafeRegisterError(
            f"create api key HTTP {status}",
            kind="token",
            step="api_key",
        )
    from .session import response_json

    data = response_json(resp)
    if not str(data.get("api_key") or "").strip():
        raise TypesafeRegisterError(
            "create api key missing api_key field",
            kind="token",
            step="api_key",
        )
    return data


def save_result(result: TypesafeResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except Exception:
        pass
    payload = {
        "ok": result.ok,
        "email": result.email,
        "api_key": result.api_key,
        "api_key_id": result.api_key_id,
        "user_id": result.user_id,
        "organization_id": result.organization_id,
        "organization_name": result.organization_name,
        "provider": "typesafe",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except Exception:
        pass


def register_one(
    *,
    email: str,
    proxy: str = "",
    magic_link_provider: Callable[[], str],
    log: LogFn | None = None,
    api_key_name: str | None = None,
    session: Any | None = None,
    action: dict[str, Any] | None = None,
) -> TypesafeResult:
    """Run login → send link → wait mail → callback → mint API key.

    ``action`` is the console-probe parse (same session). When usable, skip
    the post-allocate login GET so a transient 5xx cannot burn the mailbox.
    ``send_magic_link`` still re-GETs on POST failure.
    """
    from .session import create_session

    emit = log or (lambda _m: None)
    steps: dict[str, Any] = {}
    sess = session if session is not None else create_session(proxy)
    result = TypesafeResult(ok=False, email=email)

    try:
        emit(f"login_action email={email}")
        reused = _usable_login_action(action)
        if reused is not None:
            action = reused
            emit("login_action reused from console_probe")
        else:
            action = login_action(sess, log=emit)
        result.deployment_id = str(action.get("deployment_id") or "")
        result.deployment_mismatch = bool(action.get("deployment_mismatch"))
        steps["login_action"] = {
            "ok": True,
            "index": action.get("index"),
            "deployment_mismatch": result.deployment_mismatch,
            "reused_probe": reused is not None,
        }

        emit("send_magic_link")
        sent = send_magic_link(sess, email, action=action, log=emit)
        steps["send_magic_link"] = {
            "ok": True,
            "redirect": sent.get("x_action_redirect", ""),
        }

        emit("wait_magic_link")
        raw_link = str(magic_link_provider() or "").strip()
        parsed = extract_magic_link(raw_link)
        if not parsed:
            raise TypesafeRegisterError(
                "magic link missing or malformed",
                kind="mail_miss",
                step="wait_magic_link",
                steps=steps,
            )
        result.magic_link = parsed["magic_link"]
        steps["wait_magic_link"] = {"ok": True, "has_token": True}

        emit("auth_callback")
        cb = auth_callback(sess, parsed["token"])
        result.user_id = str(cb.get("userId") or "")
        org = ((cb.get("org_memberships") or [{}])[0].get("org") or {})
        result.organization_id = str(org.get("id") or cb.get("selectedOrgId") or "")
        result.organization_name = str(org.get("name") or "")
        steps["callback"] = {
            "ok": True,
            "user_id": result.user_id,
            "organization_id": result.organization_id,
        }

        emit("create_api_key")
        key = create_api_key(sess, api_key_name)
        result.api_key = str(key.get("api_key") or "").strip()
        result.api_key_id = str(key.get("id") or "")
        steps["api_key"] = {"ok": True, "api_key_id": result.api_key_id}
        if not result.api_key:
            raise TypesafeRegisterError(
                "missing api_key",
                kind="token",
                step="api_key",
                steps=steps,
            )
        result.ok = True
        result.steps = steps
        return result
    except TypesafeRegisterError as exc:
        if not exc.steps:
            exc.steps = steps
        result.error = str(exc)
        result.error_kind = exc.kind
        result.fail_step = exc.step
        result.steps = exc.steps or steps
        raise
    except MailMissError as exc:
        wrapped = TypesafeRegisterError(
            str(exc) or "magic link timeout",
            kind="mail_miss",
            step="wait_magic_link",
            steps=steps,
        )
        wrapped.__cause__ = exc
        raise wrapped from exc
    except Exception as exc:
        raise TypesafeRegisterError(
            f"unexpected: {exc}",
            kind="provider",
            step=result.fail_step or "register_one",
            steps=steps,
        ) from exc


def _get(session: Any, url: str, **kwargs: Any) -> tuple[Any | None, str]:
    from .session import request_with_retry

    return request_with_retry(session, "GET", url, timeout=DEFAULT_TIMEOUT, **kwargs)


def _post(session: Any, url: str, **kwargs: Any) -> tuple[Any | None, str]:
    from .session import request_with_retry

    return request_with_retry(session, "POST", url, timeout=DEFAULT_TIMEOUT, **kwargs)
