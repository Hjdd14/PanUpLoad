"""Browser-based auto login — opens Edge, user logs in, auto-extracts Token.

Uses CDP (Chrome DevTools Protocol) with Network.getCookies for reliable
cookie extraction (including HttpOnly cookies).

This module is kept for backwards compatibility.
The implementation is in cdp_login.py.
"""

from panupdate.auth.cdp_login import (
    ProviderLoginConfig,
    SELENIUM_LOGIN_CONFIGS,
    run_cdp_login,
)

run_selenium_login = run_cdp_login
