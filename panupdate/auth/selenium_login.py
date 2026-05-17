"""Browser login via Selenium + CDP — reliable Edge automation with full cookie access.

Selenium 4 manages Edge WebDriver lifecycle (auto-download, version matching,
launch, cleanup). Token extraction uses two data sources:

1. CDP Network.getCookies — gets ALL cookies including HttpOnly (JS invisible)
2. JS execute_script — gets localStorage + sessionStorage

This combines Selenium's mature browser automation with CDP's unrestricted
cookie access. No raw WebSocket, no hand-written CDP client.
"""

import time
from typing import Callable

from selenium import webdriver
from selenium.webdriver.edge.options import Options

from panupdate.auth.cdp_login import (
    SELENIUM_LOGIN_CONFIGS,
    ProviderLoginConfig,
    _STORAGE_JS,
    _extract_token_from_dump,
)

StatusCallback = Callable[[str], None]


def run_selenium_login(
    config: ProviderLoginConfig,
    timeout: float = 180.0,
    on_status: StatusCallback | None = None,
) -> str | None:
    """Open Edge via Selenium, wait for user login, extract token via CDP.

    Uses Selenium 4 built-in selenium-manager for driver auto-discovery
    (no external webdriver-manager needed). Extracts cookies via CDP
    Network.getCookies which bypasses HttpOnly restrictions.

    Returns token string, or None on timeout / browser close.
    """
    def _status(msg: str):
        if on_status:
            on_status(msg)

    opts = Options()
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])

    driver = None
    try:
        _status("正在启动浏览器...")
        driver = webdriver.Edge(options=opts)
        driver.get(config.login_url)

        _status("请在浏览器中登录（扫码或输入密码）...")

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2)

            # Check browser still alive
            try:
                driver.current_url
            except Exception:
                _status("浏览器已关闭")
                break

            dump_lines = []

            # (1) CDP Network.getCookies — ALL cookies including HttpOnly
            try:
                result = driver.execute_cdp_cmd("Network.getCookies", {})
                for ck in result.get("cookies", []):
                    name = ck.get("name", "")
                    value = ck.get("value", "")
                    if name and value:
                        dump_lines.append(f"COOKIE:{name}={value}")
            except Exception:
                pass

            # (2) localStorage + sessionStorage via JS
            try:
                ls_dump = driver.execute_script(_STORAGE_JS)
                if ls_dump:
                    dump_lines.append(ls_dump)
            except Exception:
                pass

            dump = "\n".join(dump_lines)
            if dump:
                token = _extract_token_from_dump(dump, config.provider)
                if token:
                    return token

        return None

    except Exception as exc:
        _status(f"登录失败: {exc}")
        return None

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
