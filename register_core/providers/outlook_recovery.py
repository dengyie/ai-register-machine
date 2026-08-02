"""Outlook recovery-email binding and login proof reuse.

Adapted from daimon3332/OutlookRegister (MIT):
https://github.com/daimon3332/OutlookRegister

One RecoverySession owns exactly one throwaway mailbox for the lifetime of an
Outlook account: ``bind_recovery_email`` allocates it once on the "protect your
account" page, and ``verify_bound_email_on_login`` reuses that same mailbox
whenever a later login (or the OAuth consent hop) asks for proof. A session that
is already bound never allocates a second address.

Security: stores no raw message content, no mailbox JWT, and no page text in
errors. Browser interaction only happens through the injected page object.
"""

from __future__ import annotations

from typing import Any

from register_core.contracts import Mailbox
from register_core.errors import MailMissError, ProviderError

# Microsoft account selectors (stable across the zh-CN and en-US flows).
EMAIL_INPUT = "#EmailAddress"
OTT_INPUT = "#iOttText"
CODE_CELLS = tuple(f"#codeEntry-{index}" for index in range(6))
PASSKEY_BACK = "#idBtn_Back"
PRIMARY_BUTTON = "#iNext, #idSIButton9, button[type=submit]"

_PROOF_SELECTORS = (OTT_INPUT, *CODE_CELLS)


class RecoverySession:
    """A bound throwaway mailbox reused for every later proof challenge."""

    def __init__(self, mailbox: Mailbox | Any, source: Any, bound: bool = False) -> None:
        self.mailbox = mailbox
        self.source = source
        self.bound = bool(bound)
        self.meta: dict[str, Any] = {}

    def redact(self) -> dict[str, Any]:
        address = getattr(self.mailbox, "address", "")
        return {
            "bound": self.bound,
            "address": address,
            "source": getattr(self.source, "name", ""),
        }


async def _visible(page: Any, selector: str) -> bool:
    """True only when the page really exposes a visible node for ``selector``."""
    locator = getattr(page, "locator", None)
    if locator is None:
        return False
    try:
        return bool(await locator(selector).is_visible())
    except Exception:
        return False


async def _fill(page: Any, selector: str, value: str) -> None:
    await page.locator(selector).fill(value)


async def _dismiss_passkey(page: Any) -> None:
    """Skip the "use a passkey instead" interstitial when it is offered."""
    if await _visible(page, PASSKEY_BACK):
        try:
            await page.locator(PASSKEY_BACK).click()
        except Exception:
            return


async def _submit(page: Any) -> None:
    try:
        await page.locator(PRIMARY_BUTTON).first.click()
    except Exception as exc:
        raise ProviderError("recovery_submit_failed") from exc


async def _poll_code(session: RecoverySession, timeout_seconds: int) -> str:
    used = set(session.meta.get("used_codes") or ())
    try:
        otp = session.source.poll_otp(
            session.mailbox,
            timeout_s=float(timeout_seconds),
            poll_interval_s=3,
            used_codes=used,
            sender_hint="account-security-noreply",
        )
    except MailMissError:
        # mail_miss is the pipeline's soft-retry signal, NOT a terminal
        # provider error. The pipeline (register_core/pipeline.py) maps
        # MailMissError -> error_kind="mail_miss" + continue (retry) and
        # anything else (including a wrapped ProviderError) -> "provider"
        # terminal. Wrapping here would silently drop the mail-miss retry
        # contract. Propagate the typed error so the pipeline keeps soft-retry.
        raise
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError("recovery_poll_failed") from exc
    code = str(getattr(otp, "code", "") or "")
    if len(code) != 6 or not code.isdigit():
        raise ProviderError("otp_invalid")
    used.add(code)
    session.meta["used_codes"] = used
    return code


async def _enter_code(page: Any, code: str) -> None:
    """Fill either the single OTT box or the six split code cells."""
    if await _visible(page, OTT_INPUT):
        await _fill(page, OTT_INPUT, code)
        await _submit(page)
        return
    if await _visible(page, CODE_CELLS[0]):
        for cell, digit in zip(CODE_CELLS, code):
            await _fill(page, cell, digit)
        await _submit(page)
        return
    raise ProviderError("otp_invalid")


async def bind_recovery_email(
    page: Any,
    source: Any,
    *,
    timeout_seconds: int = 90,
) -> RecoverySession | None:
    """Bind one fresh recovery address on the protect-account page.

    Returns None when the page is not asking to add recovery info, so callers can
    treat "nothing to bind" as a normal registration path rather than a failure.
    """
    await _dismiss_passkey(page)
    if not await _visible(page, EMAIL_INPUT):
        return None

    mailbox = _allocate(source)
    session = RecoverySession(mailbox=mailbox, source=source, bound=False)
    # Own the allocated mailbox across the bind: if fill/submit/poll/enter
    # raises AFTER allocate, the adapter never receives ``session`` and so its
    # own finally cannot release it — release here with success=False so the
    # source still learns the address did not bind (even if the current CF
    # TempMail release is a no-op, this preserves the release contract for any
    # future source that tracks quotas / return-to-pool).
    try:
        address = str(getattr(mailbox, "address", "") or "")
        if not address:
            raise ProviderError("recovery_allocate_failed")
        try:
            await _fill(page, EMAIL_INPUT, address)
        except Exception as exc:
            raise ProviderError("recovery_fill_failed") from exc
        await _submit(page)
        code = await _poll_code(session, timeout_seconds)
        await _enter_code(page, code)
    except BaseException:
        _release(source, mailbox, success=False)
        raise
    session.bound = True
    return session


def _allocate(source: Any):
    try:
        return source.allocate()
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError("recovery_allocate_failed") from exc


def _release(source: Any, mailbox: Any, *, success: bool) -> None:
    try:
        source.release(mailbox, success=success)
    except Exception:
        # A release failure never re-escalates: the bind already did not
        # complete; we must not mask the original error.
        pass


async def verify_bound_email_on_login(
    page: Any,
    session: RecoverySession,
    *,
    timeout_seconds: int = 90,
) -> bool:
    """Answer a login-time proof challenge with the already-bound mailbox.

    Returns False when the page is not requesting proof — notably for an already
    bound session, where no new address is ever allocated.
    """
    await _dismiss_passkey(page)
    if not await _is_proof_requested(page):
        return False

    if not session.bound:
        raise ProviderError("recovery_not_bound")

    code = await _poll_code(session, timeout_seconds)
    await _enter_code(page, code)
    return True


async def _is_proof_requested(page: Any) -> bool:
    for selector in _PROOF_SELECTORS:
        if await _visible(page, selector):
            return True
    return False
