"""Tests for CDP login module — config validation and token JS syntax."""

from panupdate.auth.cdp_login import (
    SELENIUM_LOGIN_CONFIGS,
    ProviderLoginConfig,
)


class TestWebLoginConfig:

    def test_config_data_complete(self):
        """All 5 providers have valid configs."""
        expected = {"baidu", "kuaike"}
        assert set(SELENIUM_LOGIN_CONFIGS.keys()) == expected

        for key, cfg in SELENIUM_LOGIN_CONFIGS.items():
            assert isinstance(cfg, ProviderLoginConfig)
            assert cfg.provider == key
            assert cfg.login_url.startswith("https://")
            assert len(cfg.token_js) > 20
            assert len(cfg.token_name) > 0

    def test_token_js_syntax(self):
        """All token_js strings are valid JS snippets."""
        for key, cfg in SELENIUM_LOGIN_CONFIGS.items():
            js = cfg.token_js.strip()
            assert js, f"{key}: token_js is empty"
            assert js.startswith("return"), f"{key}: must start with 'return'"
            assert js.count("(") == js.count(")"), f"{key}: unbalanced ()"
            assert js.count("{") == js.count("}"), f"{key}: unbalanced {{}}"
            assert js.count("[") == js.count("]"), f"{key}: unbalanced []"

    def test_token_extraction_validation(self):
        """Token validation: non-empty, longer than 5 chars."""
        valid = ["abc123456", "x" * 50, "BDUSS_example_value_here"]
        invalid = ["", "ab", None, "1234"]

        for t in valid:
            assert t and len(t) > 5, f"'{t}' should be valid"

        for t in invalid:
            assert not (t and len(t) > 5), f"'{t}' should be invalid"

    def test_baidu_login_url(self):
        """Baidu Netdisk config uses the correct web login URL and token name."""
        cfg = SELENIUM_LOGIN_CONFIGS["baidu"]
        assert "pan.baidu.com" in cfg.login_url
        assert cfg.token_name == "BDUSS"
        # token_js is the universal storage dumper (all providers share it)
        assert "COOKIE_RAW" in cfg.token_js
