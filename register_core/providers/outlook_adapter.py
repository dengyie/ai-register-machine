"""Outlook RegisterProvider — gated browser-backed foundation (Task 5).

Live browser execution requires GROK_REGISTER_OUTLOOK_LIVE=1.
Tasks 6–9 wire captcha bridge, recovery, OAuth, and sink orchestration.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from register_core.contracts import RegisterResult


class OutlookProvider:
    name = "outlook"

    def __init__(self, *, config: dict[str, Any] | None = None, **options: Any) -> None:
        self.config = {**(config or {}), **options}

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
                return RegisterResult(
                    ok=False,
                    provider=self.name,
                    error=str(exc),
                    error_kind="provider",
                    secret_kind="none",
                )
        return RegisterResult(
            ok=False,
            provider=self.name,
            error="Outlook provider cannot run synchronously inside an active event loop",
            error_kind="provider",
            secret_kind="none",
        )

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
        # Tasks 6–9 provide the concrete bridge, recovery, OAuth, and sink dependencies.
        # Config normalization is available for later orchestration:
        # OutlookBrowserConfig.from_options(self.config)
        _ = email_source
        _ = proxy
        return RegisterResult(
            ok=False,
            provider=self.name,
            error="outlook_components_unavailable",
            error_kind="provider",
            secret_kind="none",
        )
