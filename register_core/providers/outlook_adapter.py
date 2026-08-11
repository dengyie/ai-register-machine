"""Outlook RegisterProvider — gated browser-backed registration orchestration.

Live browser execution requires GROK_REGISTER_OUTLOOK_LIVE=1.

The five-segment orchestration (register → captcha → mailbox/recovery →
OAuth → private artifact sink) lives in ``_register_one_async``. The per-account
refresh token is persisted to a private 0600 JSON file and never reaches logs,
``artifacts`` metadata, or the public ``RegisterResult`` view.

Adapted from daimon3332/OutlookRegister (MIT):
https://github.com/daimon3332/OutlookRegister
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import tempfile
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from register_core.contracts import RegisterResult, normalize_error_kind
from register_core.email.sources.temp_mail import TempMailSource
from register_core.providers.outlook_browser import (
    OutlookBrowser,
    OutlookBrowserConfig,
    OutlookRegistrationFlow,
)
from register_core.providers.outlook_captcha import OutlookCaptchaBridge
from register_core.providers.outlook_oauth import OAuthStateMachine, OutlookOAuthConfig
from register_core.providers.outlook_recovery import bind_recovery_email
from register_core.providers.outlook_identity import resolve_egress_identity

# How long the headed browser stays open waiting for the operator to solve the
# captcha by hand. FunCaptcha is a few clicks; 10 minutes is generous without
# leaving a headed browser parked forever on an abandoned run.
_MANUAL_HANDOFF_TIMEOUT_S = 600.0
_MANUAL_HANDOFF_POLL_S = 2.0


async def manual_handoff_wait(page: Any, *, timeout_s: float, poll_s: float = _MANUAL_HANDOFF_POLL_S) -> bool:
    """Poll until both captcha iframe variants detach (operator solved it).

    Outlook shows the FunCaptcha either as ``iframe#enforcementFrame`` or as
    ``iframe[title="验证质询"]``; when the human completes the challenge the
    SPA tears the frame down and advances. Returns True once neither frame is
    present, False after ``timeout_s`` seconds.

    Only meaningful with a headed browser — in headless mode there is nobody
    to solve it, so callers should skip the wait entirely.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        enforcement = await page.locator("iframe#enforcementFrame").count()
        challenge = await page.locator('iframe[title="验证质询"]').count()
        if not enforcement and not challenge:
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(poll_s)


class OutlookProvider:
    name = "outlook"

    def __init__(self, *, config: dict[str, Any] | None = None, **options: Any) -> None:
        self.config = {**(config or {}), **options}
        # The auths directory is taken from explicit config first; otherwise we
        # honor the same OUTLOOK_AUTHS_DIR env the supervisor count block and the
        # control-api list route use, so write/count/list stay aligned on one
        # directory. Defaults to "outlook_auths" across all three surfaces.
        self._auths_dir = str(
            self.config.get("outlook_auths_dir")
            or os.environ.get("OUTLOOK_AUTHS_DIR")
            or "outlook_auths"
        )

    def register_one(self, *, email_source=None, extra=None) -> RegisterResult:
        if os.environ.get("GROK_REGISTER_OUTLOOK_LIVE") != "1":
            return RegisterResult(
                ok=False,
                provider=self.name,
                error=(
                    "Outlook live gate is disabled; set GROK_REGISTER_OUTLOOK_LIVE=1 "
                    "for an authorized test"
                ),
                error_kind="provider",
                secret_kind="none",
            )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                return asyncio.run(
                    self._register_one_async(
                        email_source=email_source,
                        extra=extra or {},
                    )
                )
            except RuntimeError as exc:
                # RuntimeError bubbles from the browser/registry flows as a
                # terminal provider failure. patchright/Chromium launch
                # exceptions can embed the configured proxy URL — including
                # credentials — into the exception text; never surface raw.
                return RegisterResult(
                    ok=False,
                    provider=self.name,
                    error=self._scrub_proxy_creds(str(exc), extra or {}),
                    error_kind="provider",
                    secret_kind="none",
                )
            except ValueError as exc:
                # ValueError surfaces a TERMINAL token rejection only when it
                # carries the sentinel the OAuth state machine raises for a
                # burnt/missing refresh token (outlook_oauth.py raises
                # "missing_refresh_token"). Map exactly that to "token" so the
                # pipeline does not retry a burnt code and does not abort the
                # batch. Any other ValueError — e.g. a config-validation failure
                # raised by schema.outlook_options (bad captcha_strategy,
                # inline-secret rejection) — is a provider/config problem, not
                # a token problem; classify it as "provider" so operators see
                # the real bucket, not a false token terminal.
                # RegisterCoreError subclasses (MailMissError, FailFastError,
                # ProviderError) are NOT ValueError and propagate to the
                # pipeline's typed handlers (mail_miss retry, fatal stop,
                # provider terminal) — do NOT catch them here.
                text = str(exc)
                kind = (
                    "token"
                    if "missing_refresh_token" in text
                    else "provider"
                )
                return RegisterResult(
                    ok=False,
                    provider=self.name,
                    error=self._scrub_proxy_creds(text, extra or {}),
                    error_kind=kind,
                    secret_kind="none",
                )
        return RegisterResult(
            ok=False,
            provider=self.name,
            error="Outlook provider cannot run synchronously inside an active event loop",
            error_kind="provider",
            secret_kind="none",
        )

    # Credentials inside a proxy URL must never reach RegisterResult.error,
    # even if a browser library (patchright/Chromium) embeds the configured
    # proxy string into an exception message. Strip userinfo before storing;
    # keep host/port/scheme for operator diagnosis.
    _CREDS_RE = re.compile(r"://[^@/\s:]+:[^@/\s]+@")

    @staticmethod
    def _scrub_proxy_creds(text: str, extra: dict[str, Any]) -> str:
        if not text:
            return text
        cleaned = OutlookProvider._CREDS_RE.sub("://<redacted>@", text)
        # Also redact raw proxy value when it appears verbatim (whole-URL form).
        raw = extra.get("proxy") if isinstance(extra, dict) else None
        if isinstance(raw, str) and raw.strip() and raw in text:
            cleaned = cleaned.replace(raw, "<redacted-proxy>")
        return cleaned

    def _captcha_bridge(self) -> OutlookCaptchaBridge:
        """Injectable bridge seam for orchestration."""
        injected = self.config.get("captcha_bridge")
        if injected is not None:
            return injected
        return OutlookCaptchaBridge()

    # ---------------- pure helpers (tested without a browser) ----------------

    def _generate_account_email(self, suffix: str) -> str:
        local = "orx" + secrets.token_hex(6)
        return f"{local}{suffix}"

    def _generate_password(self) -> str:
        return secrets.token_urlsafe(18)

    def _failure(
        self, error_kind: str, error: str, artifacts: dict[str, Any] | None = None
    ) -> RegisterResult:
        return RegisterResult(
            ok=False,
            provider=self.name,
            error=error,
            error_kind=normalize_error_kind(error_kind),
            secret_kind="none",
            artifacts=dict(artifacts or {}),
        )

    _FILENAME_SAFE = re.compile(r"[^a-z0-9]")

    def _write_outlook_artifact(
        self,
        *,
        email: str,
        password: str,
        client_id: str,
        refresh_token: str,
        recovery_email: str,
        bound: bool,
        created_at: datetime,
    ) -> str:
        """Persist one account's private material to a 0600 JSON file.

        Returns the absolute path written. Never logs any value. The directory
        is created if missing; the write is atomic within the same directory so
        a partial write never leaves a readable artifact behind.
        """
        directory = Path(self._auths_dir)
        directory.mkdir(parents=True, exist_ok=True)
        # Tighten the auths dir to 0700 to match the 0600 per-file secret
        # discipline — default umask leaves new dirs world-readable otherwise.
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        sanitized = self._FILENAME_SAFE.sub("-", (email or "").lower().strip())
        sanitized = sanitized.strip("-") or "account"
        stamp = created_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base_name = f"outlook-{sanitized}-{stamp}"

        path = directory / f"{base_name}.json"
        suffix_n = 1
        while path.exists():
            path = directory / f"{base_name}-{suffix_n}.json"
            suffix_n += 1

        payload = {
            "email": email,
            "password": password,
            "client_id": client_id,
            "refresh_token": refresh_token,
            "recovery_email": recovery_email,
            "bound": bound,
            "created_at": created_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        }
        body = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

        fd, tmp_name = tempfile.mkstemp(
            prefix=".outlook-", suffix=".tmp", dir=str(directory)
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, path)
        except Exception:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise
        return str(path)

    # ---------------- five-segment orchestration ----------------

    async def _register_one_async(self, *, email_source, extra):
        # Proxy handoff boundary: only pipeline-injected extra["proxy"], never env discovery.
        # Non-string / blank / whitespace must fail as proxy before any browser work.
        raw_proxy = (extra or {}).get("proxy")
        if not isinstance(raw_proxy, str) or not raw_proxy.strip():
            return RegisterResult(
                ok=False,
                provider=self.name,
                error="missing attempt proxy",
                error_kind="proxy",
                secret_kind="none",
            )
        proxy = raw_proxy.strip()
        # email_source is intentionally unused — Outlook derives its own address
        # from email_suffix and never borrows the pipeline's pooled mailbox.
        _ = email_source

        # Align the browser identity (locale/timezone_id/geolocation) with the
        # actual registration egress by probing ipinfo.io /json *through this
        # proxy*. Profile-authored identity keys always win; the resolver only
        # fills in keys the profile left unset, so an explicit override beats the
        # auto-derived value. The proxy URL is never carried outside this lookup.
        resolved = await asyncio.to_thread(resolve_egress_identity, proxy)
        merged_options = dict(self.config)
        for key, value in (resolved or {}).items():
            if key not in merged_options or merged_options.get(key) in (None, ""):
                merged_options[key] = value
        config = OutlookBrowserConfig.from_options(merged_options)
        flow = OutlookRegistrationFlow()
        captcha_bridge = self._captcha_bridge()
        oauth = OAuthStateMachine(config=OutlookOAuthConfig(client_id=config.client_id))
        temp_source = (
            TempMailSource.from_options(config.temp_mail or {})
            if config.bind_recovery_email
            else None
        )
        recovery: Any = None
        success = False
        manual_captcha_solved = False
        try:
            email = self._generate_account_email(config.email_suffix)
            password = self._generate_password()
            async with OutlookBrowser(config).open(proxy=proxy) as session:
                registration = await flow.register(
                    session.page,
                    email,
                    password,
                    captcha_strategy=config.captcha_strategy,
                )
                if registration.fun_captcha_seen or (
                    registration.captcha_frame_seen and config.captcha_strategy == 2
                ):
                    # Strategy 2 = manual handoff. Headless: nobody can solve it —
                    # return immediately so the batch moves on. Headed: keep the
                    # browser open and wait for the operator to solve the captcha
                    # in the window; on success fall through to OAuth.
                    if config.headless:
                        error = (
                            "fun_captcha"
                            if registration.fun_captcha_seen
                            else "manual_handoff"
                        )
                        return self._failure(
                            "captcha", error, {"outlook_steps": ["register"]}
                        )
                    shield = (
                        "enforcementFrame (FunCaptcha)"
                        if registration.fun_captcha_seen
                        else 'hold (iframe[title="验证质询"])'
                    )
                    print(
                        f"[outlook] captcha shown — shield={shield}; "
                        "solve it in the browser window; "
                        f"waiting up to {int(_MANUAL_HANDOFF_TIMEOUT_S)}s "
                        f"(account: {email})",
                        flush=True,
                    )
                    cleared = await manual_handoff_wait(
                        session.page, timeout_s=_MANUAL_HANDOFF_TIMEOUT_S
                    )
                    if not cleared:
                        return self._failure(
                            "captcha",
                            "manual_handoff_timeout",
                            {"outlook_steps": ["register", "manual_captcha"]},
                        )
                    print(
                        "[outlook] captcha cleared — continuing to OAuth",
                        flush=True,
                    )
                    manual_captcha_solved = True
                elif registration.captcha_frame_seen:
                    captcha = await captcha_bridge.solve(
                        page=session.page,
                        browser=session.browser,
                        page_url=str(getattr(session.page, "url", "") or ""),
                        timeout_ms=30_000,
                    )
                    if not captcha.ok:
                        return self._failure(
                            captcha.error_kind, captcha.error, captcha.metadata
                        )
                elif not registration.ok:
                    return self._failure(
                        registration.error_kind,
                        registration.error,
                        {"outlook_steps": ["register"]},
                    )

                recovery = (
                    await bind_recovery_email(
                        session.page, temp_source, timeout_seconds=90
                    )
                    if config.bind_recovery_email and temp_source is not None
                    else None
                )
                token = await oauth.run(
                    session.page,
                    email,
                    password,
                    proxy=proxy,
                    recovery_session=recovery,
                )
                if not token.ok:
                    return self._failure(
                        token.error_kind,
                        token.state,
                        {"outlook_steps": [token.state]},
                    )

                artifact_path = self._write_outlook_artifact(
                    email=email,
                    password=password,
                    client_id=config.client_id,
                    refresh_token=token.refresh_token or "",
                    recovery_email=getattr(recovery.mailbox, "address", "")
                    if recovery
                    else "",
                    bound=bool(recovery and recovery.bound),
                    created_at=datetime.now(timezone.utc),
                )
                success = True
                return RegisterResult(
                    ok=True,
                    provider=self.name,
                    email=email,
                    password=password,
                    secret=token.refresh_token or "",
                    secret_kind="refresh_token",
                    artifacts={
                        "outlook_auth_path": artifact_path,
                        "recovery_email": getattr(recovery.mailbox, "address", "")
                        if recovery
                        else "",
                        "bound": bool(recovery and recovery.bound),
                        "outlook_steps": (
                            ["register", "manual_captcha", "oauth"]
                            if manual_captcha_solved
                            else ["register", "captcha", "mailbox", "oauth"]
                        ),
                    },
                )
        finally:
            if recovery is not None:
                try:
                    recovery.source.release(recovery.mailbox, success=success)
                except Exception:
                    pass
