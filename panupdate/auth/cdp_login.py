"""CDP-based browser login — uses Chrome DevTools Protocol directly.

No WebDriver, no Selenium, no driver downloads. Only requires msedge.exe
which is pre-installed on every Windows 10/11 system.

Instead of guessing exact cookie/localStorage key names (which change
over time), we dump ALL browser storage and search for token-like values.
"""

import json
import os
import secrets
import struct
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable

# ── Constants ────────────────────────────────────────────────────────

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

_DUMP_ALL_JS = r"""
return (function(){
    var result = [];
    try {
        var cookies = document.cookie;
        if (cookies) { result.push('COOKIE_RAW:' + cookies); }
    } catch(e) {}
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

# JS that gets only localStorage + sessionStorage (cookies come from CDP)
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

# ── Data classes ─────────────────────────────────────────────────────

@dataclass
class ProviderLoginConfig:
    provider: str
    login_url: str
    token_js: str
    token_name: str

# ── Helper: extract token from storage dump ──────────────────────────

def _extract_json_token(val: str) -> str | None:
    """If val is JSON, extract a token field from it."""
    try:
        obj = json.loads(val)
        if not isinstance(obj, dict):
            return None
        for field in ("refresh_token", "access_token", "token",
                       "auth_token", "session_token", "jwt"):
            v = obj.get(field, "")
            if isinstance(v, str) and len(v) > 8:
                return v
        data = obj.get("data", {})
        if isinstance(data, dict):
            for field in ("refresh_token", "access_token", "token"):
                v = data.get(field, "")
                if isinstance(v, str) and len(v) > 8:
                    return v
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _extract_token_from_dump(dump: str, provider: str, min_len: int = 8) -> str | None:
    """Parse a storage dump and extract the most likely token.

    Dump format (newline-separated):
      COOKIE_RAW:<raw cookie string>   — from JS document.cookie
      COOKIE:<key>=<value>             — from native get_cookies() (incl. HttpOnly)
      LS:<key>=<value>
      SS:<key>=<value>
    """
    PRIORITY_KEYS = {
        "baidu":    ["BDUSS", "STOKEN"],
        "kuaike":   ["auth_token", "QUARK_PARAM", "ctoken", "token"],
    }

    entries = {}  # key → value (LS overrides SS, cookies are LS)

    for line in dump.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("COOKIE_RAW:"):
            raw = line[len("COOKIE_RAW:"):]
            for part in raw.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if v:
                        entries[k] = v
        elif line.startswith("COOKIE:"):
            rest = line[7:]
            if "=" in rest:
                k, v = rest.split("=", 1)
                entries[k.strip()] = v.strip()
        elif line.startswith("LS_FULL:"):
            # Full localStorage JSON dump — parse and extract key-value pairs
            try:
                ls_obj = json.loads(line[8:])
                if isinstance(ls_obj, dict):
                    for k, v in ls_obj.items():
                        if isinstance(v, str) and v:
                            entries[k] = v
            except Exception:
                pass
        elif line.startswith("LS:") or line.startswith("SS:"):
            rest = line[3:]
            if "=" in rest:
                k, v = rest.split("=", 1)
                entries[k.strip()] = v.strip()

    # 1) Priority-key match for this provider — collect ALL matches
    found = []
    for key in PRIORITY_KEYS.get(provider, []):
        val = entries.get(key, "")
        if val and len(val) > min_len:
            extracted = _extract_json_token(val)
            real_val = extracted if extracted else val
            found.append(f"{key}={real_val}")
    if found:
        return "|".join(found)

    # 2) Any JSON value containing token fields
    for key, val in entries.items():
        if len(val) > min_len:
            extracted = _extract_json_token(val)
            if extracted:
                return extracted

    # 3) Any long value whose key name suggests it's a token.
    # Exact, starts-with, or ends-with match — avoids substring matching
    # that catches CSRF cookies like _tb_token_, XSRF-TOKEN, but still
    # catches real tokens like ctoken, x-token, etc.
    TOKEN_LIKE = ("token", "access_token", "refresh_token", "auth_token",
                   "session_token", "bearer_token", "jwt")
    for key, val in entries.items():
        if len(val) > min_len and not val.startswith("{"):
            kl = key.lower()
            if kl in TOKEN_LIKE:
                return val
            for t in TOKEN_LIKE:
                if kl.startswith(t + "_") or kl.startswith(t + "-"):
                    return val
                if kl.endswith("_" + t) or kl.endswith("-" + t):
                    return val

    return None


# ── Provider configurations ──────────────────────────────────────────

SELENIUM_LOGIN_CONFIGS: dict[str, ProviderLoginConfig] = {
    "baidu": ProviderLoginConfig(
        provider="baidu",
        login_url="https://pan.baidu.com/",
        token_js=_DUMP_ALL_JS,
        token_name="BDUSS",
    ),
    "kuaike": ProviderLoginConfig(
        provider="kuaike",
        login_url="https://pan.quark.cn/",
        token_js=_DUMP_ALL_JS,
        token_name="auth_token",
    ),
}

# ── JS snippets to extract user display name (runs in authenticated browser) ─

_NAME_JS: dict[str, str] = {
    "kuaike": """
        (async () => {
            try {
                const r = await fetch('/account/info', {credentials:'include'});
                const d = await r.json();
                return (d.nickname || d.data?.nickname || '');
            } catch(e) { return ''; }
        })()
    """,
    "baidu": """
        (async () => {
            try {
                const r = await fetch('/rest/2.0/xpan/nas?method=uinfo', {credentials:'include'});
                const d = await r.json();
                return (d.baidu_name || '');
            } catch(e) { return ''; }
        })()
    """,
}


# ── WebSocket client (RFC 6455, stdlib only) ──────────────────────────

_socket_timeout = __import__("socket").timeout


class _WebSocket:

    def __init__(self):
        self._sock = None

    def connect(self, host: str, port: int, path: str, timeout: float = 10.0) -> bool:
        import hashlib
        import base64

        self._sock = __import__("socket").socket()
        self._sock.settimeout(timeout)
        self._sock.connect((host, port))

        key = base64.b64encode(secrets.token_bytes(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        self._sock.sendall(req.encode())
        resp = self._sock.recv(4096).decode(errors="replace")
        if "101" not in resp:
            return False
        expected = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode()).digest()
        ).decode()
        return expected in resp

    def send_text(self, payload: str):
        data = payload.encode("utf-8")
        mask = secrets.token_bytes(4)
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", n))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", n))
        header.extend(mask)
        masked = bytearray(data)
        for i in range(n):
            masked[i] ^= mask[i % 4]
        self._sock.sendall(bytes(header) + bytes(masked))

    def recv_text(self, timeout: float = 5.0) -> str | None:
        self._sock.settimeout(timeout)
        try:
            return self._recv_frame()
        except (_socket_timeout, OSError):
            return None

    def close(self):
        if self._sock:
            try:
                header = bytearray([0x88, 0x80])
                mask = secrets.token_bytes(4)
                header.extend(mask)
                self._sock.sendall(bytes(header))
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _recv_exactly(self, n: int) -> bytes:
        data = bytearray()
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                raise OSError("connection closed")
            data.extend(chunk)
        return bytes(data)

    def _recv_frame(self) -> str | None:
        b = self._recv_exactly(2)
        opcode = b[0] & 0x0F
        masked = (b[1] & 0x80) != 0
        length = b[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exactly(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exactly(8))[0]
        mask_key = self._recv_exactly(4) if masked else b""
        payload = bytearray()
        while len(payload) < length:
            remaining = length - len(payload)
            payload.extend(self._recv_exactly(min(remaining, 65536)))
        if masked and mask_key:
            for i in range(len(payload)):
                payload[i] ^= mask_key[i % 4]
        if opcode == 0x8:
            return None
        if opcode == 0x1:
            return bytes(payload).decode("utf-8", errors="replace")
        if opcode == 0x9:  # ping → pong
            pong = bytearray([0x8A, 0x80])
            m = secrets.token_bytes(4)
            pong.extend(m)
            self._sock.sendall(bytes(pong))
        return self._recv_frame()


# ── CDP client ───────────────────────────────────────────────────────

class CDPClient:

    def __init__(self):
        self._ws = _WebSocket()
        self._msg_id = 0
        self._network_enabled = False

    def connect(self, host: str, port: int, page_id: str) -> bool:
        ok = self._ws.connect(host, port, f"/devtools/page/{page_id}")
        if ok:
            # Enable Network domain for cookie access — must wait for response
            mid = self.send("Network.enable")
            self.recv(mid, timeout=5)
            self._network_enabled = True
        return ok

    def send(self, method: str, params: dict | None = None) -> int:
        self._msg_id += 1
        msg = json.dumps({"id": self._msg_id, "method": method, "params": params or {}})
        self._ws.send_text(msg)
        return self._msg_id

    def recv(self, expected_id: int, timeout: float = 10.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self._ws.recv_text(timeout=max(deadline - time.time(), 0.5))
            if raw is None:
                return {}
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == expected_id:
                return msg
        return {}

    def evaluate(self, expression: str) -> str:
        mid = self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
        })
        result = self.recv(mid, timeout=10)
        res = result.get("result", {})
        if "exceptionDetails" in res:
            return ""
        value = res.get("result", {}).get("value", "")
        return str(value or "").strip()

    def evaluate_await(self, expression: str, timeout: int = 15) -> str:
        """Evaluate an async expression (awaitPromise)."""
        mid = self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        result = self.recv(mid, timeout=timeout)
        res = result.get("result", {})
        if "exceptionDetails" in res:
            return ""
        value = res.get("result", {}).get("value", "")
        return str(value or "").strip()

    def get_cookies(self, urls: list[str] | None = None) -> list[dict]:
        """Get ALL cookies via CDP Network.getCookies (includes HttpOnly).

        Returns list of cookie dicts with keys: name, value, domain, path, etc.
        """
        params = {}
        if urls:
            params["urls"] = urls
        mid = self.send("Network.getCookies", params)
        result = self.recv(mid, timeout=10)
        return result.get("result", {}).get("cookies", [])

    def close(self):
        self._ws.close()


# ── CDP HTTP helpers ─────────────────────────────────────────────────

def _cdp_pages(host: str, port: int) -> list[dict]:
    try:
        req = urllib.request.Request(f"http://{host}:{port}/json")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return []


def _find_msedge() -> str:
    paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    import shutil
    p = shutil.which("msedge")
    if p:
        return p
    raise RuntimeError(
        "未找到 Microsoft Edge 浏览器。\n"
        "请确保 Edge 已安装（Windows 10/11 自带）。"
    )


# ── Status callback type ─────────────────────────────────────────────

StatusCallback = Callable[[str], None]


# ── Fallback token extraction and dump saving ────────────────────────

# Cookie names that are definitely NOT auth tokens (tracking, CSRF, etc.)
_NON_TOKEN_COOKIES = {
    "BAIDUID", "BAIDUID_BFESS", "csrfToken", "csrf_token",
    "newlogin", "login", "lang", "theme", "locale",
    "__utma", "__utmb", "__utmc", "__utmt", "__utmz",
    "_ga", "_gid", "_gat", "_ga_", "Hm_lvt", "Hm_lpvt",
    "PHPSESSID", "JSESSIONID", "buvid3", "buvid4",
    "DedeUserID", "DedeUserID__ckMd5", "bili_jct",
    "sid", "s_vi", "s_ri", "Optimizely",
}


def _fallback_extract(cookies: list[dict], min_len: int = 20) -> str | None:
    """Find the most likely auth token from a cookie list.

    This is the LAST-RESORT fallback when priority key matching fails.
    It includes ALL non-tracking cookies to find any plausible token.
    Longer values score higher; token-like names get a bonus.
    """
    candidates = []
    for ck in cookies:
        name = ck.get("name", "")
        value = ck.get("value", "")
        if not name or not value:
            continue
        if len(value) < min_len:
            continue
        if name in _NON_TOKEN_COOKIES:
            continue
        # Give priority boost for token-like names (substring match is
        # OK here — this is the last resort and we just need ANY token)
        name_lower = name.lower()
        boost = 0
        for kw in ("token", "auth", "session", "access", "bearer",
                    "jwt", "refresh", "key", "secret"):
            if kw in name_lower:
                boost = 1000
                break
        candidates.append((boost + len(value), name, value))

    candidates.sort(reverse=True)
    if candidates:
        _, name, value = candidates[0]
        return value
    return None


def _is_logged_in_url(url: str, provider: str) -> bool:
    """Check if the URL indicates the user is logged in (not on login page)."""
    if not url:
        return False
    logged_in_markers = {
        "baidu":   ("pan.baidu.com/disk",),
        "kuaike":  ("pan.quark.cn/list",),
    }
    markers = logged_in_markers.get(provider, ())
    return any(m in url for m in markers)


def _build_full_cookie_token(confirmed_token: str, all_cookies: list[dict],
                              provider: str) -> str:
    """For cookie-based providers, build a token containing ALL cookies.

    The confirmed_token is used for login validation. For actual API calls,
    the driver needs the complete cookie set. This function combines all
    cookies into a pipe-separated key=value string.
    """
    if provider not in ("kuaike",):
        return confirmed_token
    parts = []
    for ck in sorted(all_cookies, key=lambda c: c.get("name", "")):
        n, v = ck.get("name", ""), ck.get("value", "")
        if n and v and len(v) > 4:
            parts.append(f"{n}={v}")
    return "|".join(parts) if parts else confirmed_token


def _try_extract_name(c, provider: str) -> str:
    """Try to get the user's display name from the authenticated browser page.

    Uses DOM scraping (the page already shows the username after login).
    Falls back to a generic search for any provider without a specific snippet.
    """
    js = _NAME_JS.get(provider, "")
    if not js:
        # Generic DOM scraping fallback
        js = """
            (function(){
                var sel = document.querySelector(
                    '.user-name,.username,.nickname,.name,.display-name,' +
                    '[data-username],[data-nickname],[data-user],' +
                    '.user-info .name,.user-bar .name,.header .user span'
                );
                if (sel) { var t = sel.textContent.trim(); if (t && t.length<60) return t; }
                if (sel) { var t2 = sel.getAttribute('title'); if (t2 && t2.length<60) return t2; }
                return '';
            })()
        """
    try:
        # evaluate_await for async snippets, evaluate for sync (DOM) ones
        if "async" in js:
            name = c.evaluate_await(js, timeout=10)
        else:
            name = c.evaluate(js)
        _diag(f"_try_extract_name({provider}): raw={name!r}")
        if name and 0 < len(name) < 100:
            return name.strip()
    except Exception as e:
        _diag(f"_try_extract_name({provider}) error: {e}")
    return ""


def _save_dump(dump: str, provider: str):
    """Save storage dump to temp file for diagnosis when login fails."""
    if not dump:
        return
    try:
        import tempfile
        path = os.path.join(
            tempfile.gettempdir(),
            f"panupdate_dump_{provider}.txt",
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(dump)
    except Exception:
        pass


# Global diagnostic log for the current login attempt
_diag_lines: list[str] = []


def _diag(msg: str):
    """Append diagnostic message to in-memory log."""
    import datetime
    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    _diag_lines.append(f"[{ts}] {msg}")


def _flush_diag(provider: str):
    """Write diagnostic log to temp file."""
    if not _diag_lines:
        return
    try:
        import tempfile
        path = os.path.join(
            tempfile.gettempdir(),
            f"panupdate_diag_{provider}.txt",
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(_diag_lines))
        _diag_lines.clear()
    except Exception:
        pass


# ── Main login entry point ───────────────────────────────────────────

def run_cdp_login(
    config: ProviderLoginConfig,
    timeout: float = 180.0,
    on_status: StatusCallback | None = None,
) -> str | None:
    """Open Edge via CDP, wait for user to log in, extract token.

    Polls ALL open pages every 2s, dumps all cookies + localStorage +
    sessionStorage, then searches for token-like values using priority
    keys and heuristics.

    Returns token string, or None on timeout / browser close.
    """
    global _diag_lines
    _diag_lines = []

    def _status(msg: str):
        _diag(msg)
        if on_status:
            on_status(msg)

    _diag(f"=== CDP login start: provider={config.provider} ===")
    edge_path = _find_msedge()
    _diag(f"Edge found: {edge_path}")
    port = secrets.randbelow(7000) + 9223
    user_data_dir = os.path.join(
        tempfile.gettempdir(), f"panupdate_cdp_{os.getpid()}"
    )
    _diag(f"Port: {port}, user_data_dir: {user_data_dir}")

    browser = None
    client = None
    browser_exited_cleanly = False  # True if Edge process exited with code 0
    try:
        os.makedirs(user_data_dir, exist_ok=True)
        _diag("user_data_dir created")

        _status("正在启动浏览器...")
        cmd = [
            edge_path,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            config.login_url,
        ]
        _diag(f"Launch cmd: {' '.join(cmd)}")
        browser = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        _diag(f"Browser launched, PID={browser.pid}")

        # Wait for CDP (max 15s)
        page_id = None
        for i in range(30):
            time.sleep(0.5)
            exit_code = browser.poll()
            if exit_code is not None and exit_code != 0:
                _diag(f"Edge exited with code {exit_code}")
                raise RuntimeError(f"Edge 异常退出 (code={exit_code})")
            if exit_code == 0:
                _diag(f"Edge process exited cleanly (code=0) at iter {i}")
                browser_exited_cleanly = True
            pages = _cdp_pages("127.0.0.1", port)
            for p in pages:
                url = p.get("url", "")
                if p.get("type") == "page" and "chrome-extension" not in url:
                    page_id = p["id"]
                    _diag(f"CDP page found: id={page_id[:20]}... url={url[:80]}")
                    break
            if page_id:
                break
        else:
            _diag("CDP timeout after 15s")
            raise RuntimeError("浏览器启动超时，CDP 调试端口未就绪")

        client = CDPClient()
        if not client.connect("127.0.0.1", port, page_id):
            _diag("WebSocket connection failed")
            raise RuntimeError("WebSocket 连接失败")
        _diag("CDP WebSocket connected")

        _status("请在浏览器中登录（扫码或输入密码）...")

        # Poll loop — check ALL pages every 2s
        deadline = time.time() + timeout
        last_dump = ""
        baseline_cookies: dict[str, str] | None = None  # name → value
        baseline_ls_keys: set[str] | None = None  # baseline localStorage/sessionStorage keys
        pending_token: str | None = None
        pending_count: int = 0  # consecutive polls with same token
        poll_n = 0

        while time.time() < deadline:
            time.sleep(2)
            poll_n += 1

            # Only check browser process if it hasn't already exited
            # cleanly (Edge often connects to existing instance, then
            # the Popen process exits but the browser window lives on).
            if not browser_exited_cleanly and browser.poll() is not None:
                _diag(f"Browser crashed at poll {poll_n} (code={browser.poll()})")
                _status("浏览器异常关闭")
                break

            # If browser exited cleanly, check CDP is still reachable
            if browser_exited_cleanly:
                try:
                    pages = _cdp_pages("127.0.0.1", port)
                    if not any(p.get("type") == "page" for p in pages):
                        _diag(f"CDP lost at poll {poll_n} (no pages)")
                        _status("浏览器已关闭")
                        break
                except Exception:
                    _diag(f"CDP unreachable at poll {poll_n}")
                    _status("浏览器已关闭")
                    break

            # Build list of pages to check (primary first, then any others)
            pages_to_try = [(page_id, client)]
            all_pages = _cdp_pages("127.0.0.1", port)
            for p in all_pages:
                pid = p.get("id", "")
                if pid != page_id and p.get("type") == "page":
                    pages_to_try.append((pid, None))

            for pid, existing_client in pages_to_try:
                c = existing_client
                if c is None:
                    c = CDPClient()
                    if not c.connect("127.0.0.1", port, pid):
                        continue

                try:
                    dump_lines = []
                    all_cookies = []

                    # (1) Cookies via CDP Network.getCookies
                    try:
                        all_cookies = c.get_cookies()

                        # First poll: snapshot existing cookies (name → value).
                        if baseline_cookies is None:
                            baseline_cookies = {}
                            for ck in all_cookies:
                                n, v = ck.get("name", ""), ck.get("value", "")
                                if n:
                                    baseline_cookies[n] = v
                            _diag(f"Baseline ({len(baseline_cookies)} cookies): {sorted(baseline_cookies.keys())}")

                        # Include cookies that are NEW or whose VALUE CHANGED
                        for ck in all_cookies:
                            name = ck.get("name", "")
                            value = ck.get("value", "")
                            if name and value:
                                if name not in baseline_cookies or baseline_cookies[name] != value:
                                    dump_lines.append(f"COOKIE:{name}={value}")
                    except Exception:
                        pass

                    # (2) localStorage + sessionStorage via JS (with baseline)
                    raw_ls = ""
                    ls_error = ""
                    try:
                        raw_ls = c.evaluate(_STORAGE_JS) or ""
                    except Exception as e:
                        ls_error = str(e)

                    # Fallback: comprehensive storage scan if _STORAGE_JS returned empty
                    if not raw_ls:
                        # Try direct localStorage getItem for known keys
                        for token_key in ("token", "refresh_token", "access_token",
                                          "auth_token", "session_token", "jwt"):
                            try:
                                direct = c.evaluate(
                                    f"localStorage.getItem('{token_key}')"
                                )
                                if direct and len(direct) > 8:
                                    raw_ls = f"LS:{token_key}={direct}"
                                    break
                            except Exception:
                                pass
                    if not raw_ls:
                        # Try sessionStorage
                        for token_key in ("token", "refresh_token", "access_token"):
                            try:
                                direct = c.evaluate(
                                    f"sessionStorage.getItem('{token_key}')"
                                )
                                if direct and len(direct) > 8:
                                    raw_ls = f"SS:{token_key}={direct}"
                                    break
                            except Exception:
                                pass
                    if not raw_ls:
                        # Full localStorage dump as JSON (catches unknown key names)
                        try:
                            full_ls = c.evaluate(
                                "JSON.stringify(localStorage)"
                            )
                            if full_ls and len(full_ls) > 10:
                                raw_ls = f"LS_FULL:{full_ls}"
                        except Exception:
                            pass

                    if raw_ls:
                        # First poll: capture baseline LS/SS keys and values
                        if baseline_ls_keys is None:
                            baseline_ls_keys = set()
                            for line in raw_ls.split("\n"):
                                line = line.strip()
                                if line.startswith("LS:") or line.startswith("SS:"):
                                    baseline_ls_keys.add(line)
                            _diag(f"LS/SS baseline: {len(baseline_ls_keys)} entries")

                        # Only include LS/SS entries that are NEW or CHANGED
                        filtered_lines = []
                        for line in raw_ls.split("\n"):
                            line = line.strip()
                            if not line:
                                continue
                            if line.startswith("LS:") or line.startswith("SS:"):
                                if line not in baseline_ls_keys:
                                    filtered_lines.append(line)
                            else:
                                filtered_lines.append(line)
                        if filtered_lines:
                            dump_lines.append("\n".join(filtered_lines))
                    elif ls_error:
                        _diag(f"LS eval error: {ls_error}")

                    # Get current URL
                    current_url = ""
                    try:
                        current_url = c.evaluate("window.location.href")
                    except Exception:
                        pass

                    dump = "\n".join(dump_lines)
                    last_dump = dump
                    # Log every poll for debugging
                    cookie_names = [ck.get("name","?") for ck in all_cookies[:20]]
                    _diag(f"Poll {poll_n}: cookies={len(all_cookies)} names={cookie_names}, dump_lines={len(dump_lines)}, url={current_url[:80]}")
                    # Save dump periodically for debugging
                    if poll_n % 5 == 0:
                        _save_dump(dump or f"(empty dump, cookies={len(all_cookies)}, url={current_url[:80]})",
                                   f"{config.provider}_poll{poll_n}")
                        _flush_diag(config.provider)
                    if dump:
                        token = _extract_token_from_dump(dump, config.provider)
                        if not token:
                            new_cookies = [
                                ck for ck in all_cookies
                                if ck.get("name", "") not in baseline_cookies
                                or baseline_cookies.get(ck.get("name", "")) != ck.get("value", "")
                            ]
                            if not token:
                                token = _fallback_extract(new_cookies, min_len=50)
                        if token:
                            base = config.login_url.rstrip("/")
                            cur = current_url.rstrip("/")
                            url_moved = cur and not (
                                cur == base
                                or cur.startswith(base + "?")
                                or cur.startswith(base + "/?")
                            )
                            _diag(f"Poll {poll_n}: token_candidate={token[:40]}... url={current_url[:60]} url_moved={url_moved} pending_count={pending_count}")
                            # Check if user is on a LOGGED-IN page (not still on login page)
                            is_logged_in = _is_logged_in_url(current_url, config.provider)
                            _diag(f"Poll {poll_n}: is_logged_in={is_logged_in}")
                            # Primary path: URL changed → confirm after 2 consecutive polls
                            if url_moved and is_logged_in:
                                if pending_token == token:
                                    _diag("Token CONFIRMED (url_moved), returning")
                                    _status("Token 已确认，正在验证...")
                                    _save_dump(dump, f"{config.provider}_found")
                                    display_name = _try_extract_name(c, config.provider)
                                    _flush_diag(config.provider)
                                    # For cookie-based providers, return ALL cookies
                                    token = _build_full_cookie_token(token, all_cookies, config.provider)
                                    if display_name:
                                        _diag(f"Display name found: {display_name}")
                                        return f"{token}|display_name={display_name}"
                                    return token
                                pending_token = token
                                pending_count = 1
                                _status(f"发现登陆Token ({poll_n * 2}s)…")
                            else:
                                # Fallback: same token seen 3+ times even without URL change
                                # Still require logged-in URL
                                if pending_token == token and is_logged_in:
                                    pending_count += 1
                                    if pending_count >= 3:
                                        _diag(f"Token CONFIRMED (count={pending_count}), returning")
                                        _status("Token 已确认，正在验证...")
                                        _save_dump(dump, f"{config.provider}_found")
                                        display_name = _try_extract_name(c, config.provider)
                                        _flush_diag(config.provider)
                                        token = _build_full_cookie_token(token, all_cookies, config.provider)
                                        if display_name:
                                            _diag(f"Display name found: {display_name}")
                                            return f"{token}|display_name={display_name}"
                                        return token
                                    _status(f"等待Token确认 ({pending_count}/3) ({poll_n * 2}s)…")
                                else:
                                    pending_token = token
                                    pending_count = 1
                                    _status(f"发现Token候选项 ({poll_n * 2}s)…")
                        else:
                            if pending_token:
                                pending_token = None
                                pending_count = 0
                                _status(f"请在浏览器中登录...({poll_n * 2}s)")
                            elif poll_n % 5 == 0:
                                _status(f"请在浏览器中登录...({poll_n * 2}s)")
                    elif poll_n <= 3:
                        _diag(f"Poll {poll_n}: empty dump, url={current_url[:60]}")
                except Exception:
                    pass

                if existing_client is None:
                    c.close()

        # No token found — save dump and diagnostic log
        _diag("No token found after full timeout")
        _save_dump(last_dump, config.provider)
        _flush_diag(config.provider)
        return None

    except Exception as exc:
        _diag(f"EXCEPTION: {exc}")
        _status(f"登录失败: {exc}")
        _flush_diag(config.provider)
        return None

    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass
        if browser:
            try:
                browser.terminate()
                try:
                    browser.wait(5)
                except Exception:
                    pass
            except Exception:
                try:
                    browser.kill()
                except Exception:
                    pass
        try:
            import shutil
            shutil.rmtree(user_data_dir, ignore_errors=True)
        except Exception:
            pass
