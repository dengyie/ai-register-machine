from register_core.providers.outlook_browser import OutlookBrowserConfig


def test_outlook_config_defaults_to_manual_captcha_gate():
    config = OutlookBrowserConfig.from_options({})
    assert config.captcha_strategy == 2
    assert config.email_suffix == "@outlook.com"
    assert config.locale == "zh-CN"
    assert config.timezone_id == "UTC"


def test_outlook_config_rejects_secret_values_before_normalization():
    import pytest

    with pytest.raises(ValueError, match="secret"):
        OutlookBrowserConfig.from_options({"password": "must-not-be-read"})
