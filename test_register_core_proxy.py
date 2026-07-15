#!/usr/bin/env python3
"""Offline tests: self-controlled proxy list for register_core (no Clash)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from register_core.util import proxy as core_proxy


class TestRotationConfig(unittest.TestCase):
    def setUp(self) -> None:
        core_proxy.reset_rotation_for_tests()
        # Isolate env
        self._env_backup = {
            k: os.environ.pop(k, None)
            for k in (
                "CHATGPT_PROXY_LIST",
                "PROXY_LIST",
                "PROXY_POOL",
                "CHATGPT_PROXY_ROTATE_MODE",
                "PROXY_ROTATE_MODE",
                "CHATGPT_PROXY",
                "MIMO_PROXY",
                "https_proxy",
                "HTTPS_PROXY",
                "http_proxy",
                "HTTP_PROXY",
                "CHATGPT_PROXY_ROTATE_EVERY",
                "PROXY_ROTATE_EVERY",
            )
        }

    def tearDown(self) -> None:
        core_proxy.reset_rotation_for_tests()
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_proxy_list_auto_selects_list_mode(self) -> None:
        cfg = core_proxy.rotation_config_from_env_and_extra(
            {"proxy_list": "http://a:1,http://b:2"}
        )
        self.assertEqual(cfg["proxy_rotate_mode"], "list")
        self.assertEqual(cfg["proxy_list"], "http://a:1,http://b:2")

    def test_env_proxy_list_auto_list(self) -> None:
        os.environ["PROXY_LIST"] = "http://a:1\nhttp://b:2"
        cfg = core_proxy.rotation_config_from_env_and_extra({})
        self.assertEqual(cfg["proxy_rotate_mode"], "list")

    def test_explicit_off_keeps_off_even_with_list(self) -> None:
        cfg = core_proxy.rotation_config_from_env_and_extra(
            {
                "proxy_rotate_mode": "off",
                "proxy_list": "http://a:1,http://b:2",
                "proxy": "http://127.0.0.1:7897",
            }
        )
        self.assertEqual(cfg["proxy_rotate_mode"], "off")

    def test_resolve_attempt_rotates_list(self) -> None:
        proxies: list[str] = []
        extra = {
            "proxy_list": "http://a:1,http://b:2,http://c:3",
            "proxy_rotate_every": 1,
            "proxy_rotate_on_start": True,
        }
        for _ in range(3):
            p, info = core_proxy.resolve_attempt_proxy(extra)
            proxies.append(p)
            self.assertEqual(info.get("mode"), "list")
        # Round-robin through pool (start on first, then advance each due)
        self.assertEqual(proxies[0], "http://a:1")
        self.assertEqual(proxies[1], "http://b:2")
        self.assertEqual(proxies[2], "http://c:3")

    def test_inject_attempt_proxy_sets_extra_proxy(self) -> None:
        extra = core_proxy.inject_attempt_proxy(
            {"proxy_list": "http://only:9", "proxy_rotate_on_start": True}
        )
        self.assertEqual(extra.get("proxy"), "http://only:9")
        self.assertEqual(extra.get("_proxy_rotate", {}).get("mode"), "list")

    def test_pipeline_passes_rotated_proxy_to_provider(self) -> None:
        from register_core.contracts import RegisterResult
        from register_core.pipeline import Pipeline
        from register_core.verify.noop import NoopVerifier

        seen: list[str] = []

        class StubProvider:
            name = "stub"

            def register_one(self, *, email_source=None, extra=None):
                seen.append(str((extra or {}).get("proxy") or ""))
                return RegisterResult(
                    ok=False,
                    provider=self.name,
                    error="stop",
                    error_kind="other",
                    secret_kind="none",
                )

        pipe = Pipeline(
            StubProvider(),
            email_source=None,
            verifier=NoopVerifier(),
            fail_fast=False,
        )
        extra = {
            "proxy_list": "http://n1:1,http://n2:2",
            "proxy_rotate_every": 1,
            "proxy_rotate_on_start": True,
        }
        stats = pipe.run(2, extra=extra)
        self.assertEqual(stats.fail, 2)
        self.assertEqual(seen, ["http://n1:1", "http://n2:2"])


if __name__ == "__main__":
    unittest.main()
