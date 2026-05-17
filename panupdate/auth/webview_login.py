"""Embedded browser login via pywebview — runs in a multiprocessing subprocess.

Avoids the "pywebview must run on main thread" conflict with Flet by
running in a completely separate process. Token is extracted via JS
storage dump and sent back to the main process via Queue.

This module is designed to work as the target of multiprocessing.Process.
All imports are deferred to avoid loading heavy deps in the parent process.
"""

import time
from multiprocessing import Queue

# JS that dumps localStorage + sessionStorage only (no cookies — those
# we get via the native get_cookies() API to bypass HttpOnly restrictions).
_STORAGE_JS = r"""
return (function(){
    var result = [];
    try {
        for (var i = 0; i < localStorage.length; i++) {
            var k = localStorage.key(i);
            result.push('LS:' + k + '=' + localStorage.getItem(k));
        }
    } catch(e) {}
    try {
        for (var i = 0; i < sessionStorage.length; i++) {
            var k = sessionStorage.key(i);
            result.push('SS:' + k + '=' + sessionStorage.getItem(k));
        }
    } catch(e) {}
    return result.join('\n');
})()
"""


def run_webview_login(config_dict: dict, result_queue: Queue, timeout: float = 180.0):
    """Run in subprocess: open webview window, poll for token, return via Queue.

    Args:
        config_dict: Dict with keys: provider, login_url, token_name
        result_queue: multiprocessing.Queue to send result back.
        timeout: Max seconds to wait for login.
    """
    import webview
    import threading

    from panupdate.auth.cdp_login import _extract_token_from_dump

    provider = config_dict["provider"]
    login_url = config_dict["login_url"]
    token_found = [None]  # list for nonlocal mutation

    def poll_token():
        """Poll for token every 2s — runs in background thread via webview.start(func=).

        Uses TWO data sources:
        1. window.get_cookies() → native WebView2 API, gets ALL cookies
           INCLUDING HttpOnly ones (which JS document.cookie cannot see).
        2. window.evaluate_js(_STORAGE_JS) → localStorage + sessionStorage.
        """
        deadline = time.time() + timeout
        while time.time() < deadline and token_found[0] is None:
            time.sleep(2)
            try:
                w = webview.windows[0]
                dump_lines = []

                # (1) Native cookies — bypasses HttpOnly restriction
                try:
                    cookies = w.get_cookies()
                    if cookies:
                        for c in cookies:
                            name = c.get("name", "")
                            value = c.get("value", "")
                            if name and value:
                                dump_lines.append(f"COOKIE:{name}={value}")
                except Exception:
                    pass

                # (2) JS-accessible localStorage + sessionStorage
                try:
                    ls_dump = w.evaluate_js(_STORAGE_JS)
                    if ls_dump:
                        dump_lines.append(ls_dump)
                except Exception:
                    pass

                dump = "\n".join(dump_lines)
                if dump:
                    token = _extract_token_from_dump(dump, provider)
                    if token:
                        token_found[0] = token
                        w.destroy()
            except Exception:
                pass

    # Create window (must be before start())
    webview.create_window(
        title=f"PanUpLoad — 登录 {provider}",
        url=login_url,
        width=480,
        height=680,
        confirm_close=False,
    )

    # start() blocks until window is closed (by poll_token or user)
    webview.start(gui=None, func=poll_token)

    result_queue.put(token_found[0])
