"""Layered multi-provider account register framework.

Layers (dependency direction top → bottom):

  hub / CLI / --profile (register.v1)
    → config/*       (profile loader → job + composite mail)
    → pipeline (orchestrator)
      → providers/*   (product signup: chatgpt, grok, mimo, …)
      → mailbox/*     (allocate / release only)
      → decode/*      (wait_otp only)
      → email/*       (compat + CompositeEmailSource)
      → verify/*      (post-signup capability probe)
      → sink/*        (persist accounts/keys)
      → contracts + errors (shared types)

Providers keep their own browser stack (Python/Drission vs Node/Playwright).
This package only standardizes interfaces and orchestration.
"""

from .contracts import (
    Mailbox,
    OtpCode,
    RegisterJob,
    RegisterResult,
    VerifyResult,
)
from .errors import (
    CaptchaError,
    FailFastError,
    MailMissError,
    ProviderError,
    VerifyError,
)

__all__ = [
    "Mailbox",
    "OtpCode",
    "RegisterJob",
    "RegisterResult",
    "VerifyResult",
    "CaptchaError",
    "FailFastError",
    "MailMissError",
    "ProviderError",
    "VerifyError",
]
