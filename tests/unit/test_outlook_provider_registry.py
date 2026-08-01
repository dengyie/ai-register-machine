import pytest

from register_core.config.schema import ProviderSpec
from register_core.providers.registry import get_provider


def test_outlook_aliases_resolve_to_the_same_provider():
    providers = [get_provider(name, config={}) for name in ("outlook", "microsoft", "hotmail", "msa")]
    assert {provider.name for provider in providers} == {"outlook"}


def test_outlook_options_are_non_secret_config():
    spec = ProviderSpec(
        name="outlook",
        options={
            "client_id": "synthetic-client-id",
            "captcha_strategy": 2,
            "temp_mail": {"base_url": "https://mail.invalid", "domain": "invalid"},
            "email_suffix": "@outlook.com",
            "bind_recovery_email": False,
            "outlook_auths_dir": "./outlook-auths",
        },
    )
    assert spec.name == "outlook"
    assert spec.options["captcha_strategy"] == 2


def test_outlook_options_reject_inline_secret_values():
    spec = ProviderSpec(name="outlook", options={"password": "synthetic-password"})
    with pytest.raises(ValueError, match="secret"):
        spec.outlook_options()


def test_outlook_options_reject_nested_temp_mail_secret_values():
    spec = ProviderSpec(
        name="outlook",
        options={"temp_mail": {"admin_password": "synthetic-admin"}},
    )
    with pytest.raises(ValueError, match="temp_mail.admin_password"):
        spec.outlook_options()
