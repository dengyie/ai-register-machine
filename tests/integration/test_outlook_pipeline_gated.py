"""End-to-end mocked acceptance for the Outlook pipeline (Task 11).

Live-browser behaviour is NOT exercised here — this test is mock-only and is
explicitly skipped when ``GROK_REGISTER_OUTLOOK_LIVE=1`` (the live browser
tests live in their own separately-gated module). The contract pinned here:

1. The pipeline's ``inject_attempt_proxy`` shallow-copies ``extra`` and sets
   ``extra["proxy"]``; the value reaches the provider's ``register_one``.
2. A provider that writes a 0600 Outlook auth artifact and returns a
   ``RegisterResult`` with ``secret_kind="refresh_token"`` is carried
   through unchanged.
3. The public result view (``to_public_dict``) redacts the password, the
   refresh-token secret, and the proxy value — none leak into public dicts,
   even though the on-disk private artifact retains the secret.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from register_core.contracts import RegisterResult
from register_core.pipeline import Pipeline

pytestmark = pytest.mark.skipif(
    os.environ.get("GROK_REGISTER_OUTLOOK_LIVE") == "1",
    reason="this acceptance test is mock-only; live browser tests are separately gated",
)


def test_mock_outlook_pipeline_carries_proxy_and_redacts_public_result(
    monkeypatch, tmp_path
):
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        "register_core.util.proxy.preflight_nodes_for_register",
        lambda extra=None, *, log_fn=None: dict(extra or {}),
    )
    monkeypatch.setattr(
        "register_core.util.proxy.inject_attempt_proxy",
        lambda extra=None, *, log_fn=None: {**(extra or {}), "proxy": "synthetic-proxy"},
    )

    class FakeOutlookProvider:
        name = "outlook"

        def register_one(self, *, email_source, extra):
            seen["proxy"] = extra["proxy"]
            path = tmp_path / "outlook_auths" / "outlook-user.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "email": "user@outlook.com",
                        "password": "synthetic-password",
                        "client_id": "synthetic-client-id",
                        "refresh_token": "synthetic-refresh-token",
                        "bound": True,
                    }
                ),
                encoding="utf-8",
            )
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            return RegisterResult(
                ok=True,
                provider=self.name,
                email="user@outlook.com",
                password="synthetic-password",
                secret="synthetic-refresh-token",
                secret_kind="refresh_token",
                artifacts={
                    "outlook_auth_path": str(path),
                    "bound": True,
                    "recovery_email": "r@invalid",
                },
            )

    emitted: list[RegisterResult] = []
    stats = Pipeline(
        FakeOutlookProvider(),
        fail_fast=False,
        on_result=emitted.append,
    ).run(count=1, extra={})

    assert stats.results and stats.results[0].ok is True
    assert seen == {"proxy": "synthetic-proxy"}
    path = tmp_path / "outlook_auths" / "outlook-user.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    private = json.loads(path.read_text(encoding="utf-8"))
    assert private["refresh_token"] == "synthetic-refresh-token"

    public = emitted[0].to_public_dict()
    public_json = json.dumps(public)
    assert public["artifacts"]["outlook_auth_path"] == str(path)
    assert public["artifacts"]["bound"] is True
    assert public["artifacts"]["recovery_email"] == "r@invalid"
    assert "synthetic-password" not in public_json
    assert "synthetic-refresh-token" not in public_json
    assert "synthetic-proxy" not in public_json
