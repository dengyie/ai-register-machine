"""Control API schema tests for Outlook product acceptance (Task 10)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.control_api.schemas import StartRunRequest


def test_start_run_accepts_outlook():
    request = StartRunRequest(
        product="outlook",
        target=1,
        extra_env={"GROK_REGISTER_OUTLOOK_LIVE": "0"},
    )
    assert request.product == "outlook"


def test_start_run_rejects_unknown_product():
    with pytest.raises(ValidationError):
        StartRunRequest(product="xai")


def test_start_run_default_product_is_grok():
    assert StartRunRequest().product == "grok"


def test_start_run_extra_env_carry_names_not_values_required():
    # extra_env is a str->str map for env NAMES/flags; this test only asserts
    # the type accepts the Outlook live-gate flag name without constraints.
    request = StartRunRequest(product="outlook", extra_env={"GROK_REGISTER_OUTLOOK_LIVE": "1"})
    assert request.extra_env == {"GROK_REGISTER_OUTLOOK_LIVE": "1"}
