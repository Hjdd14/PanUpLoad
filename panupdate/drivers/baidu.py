"""Baidu Netdisk driver — OAuth (PCS API) and Cookie (web API) modes."""

import asyncio
import base64
import hashlib
import os
import re
import time
import json
from urllib.parse import urlencode, quote

import httpx

from panupdate.drivers.base import (
    CloudDriver, AccountInfo, FileItem, QuotaInfo,
)
from panupdate.utils.retry import async_retry
from panupdate.utils.upload_logger import log_upload_event, log_exception


BAIDU_OAUTH_AUTHORIZE = "https://openapi.baidu.com/oauth/2.0/authorize"
BAIDU_OAUTH_TOKEN = "https://openapi.baidu.com/oauth/2.0/token"
BAIDU_PCS_API = "https://pan.baidu.com/rest/2.0/xpan/file"
BAIDU_QUOTA_API = "https://pan.baidu.com/api/quota"

# Web upload API (aligned with browser behavior)
BAIDU_HOME = "https://pan.baidu.com/"
BAIDU_BDSTOKEN_API = "https://pan.baidu.com/api/gettemplatevariable"
BAIDU_PRECREATE = "https://pan.baidu.com/api/precreate"
BAIDU_SUPERFILE2 = "https://pan.baidu.com/rest/2.0/pcs/superfile2"
BAIDU_CREATE = "https://pan.baidu.com/api/create"
BAIDU_FILE_META = "https://pan.baidu.com/api/filemetas"

# Common query params present in all browser web API calls
BAIDU_APP_ID = "250528"
BAIDU_WEB_PARAMS = f"app_id={BAIDU_APP_ID}&channel=chunlei&web=1&clienttype=0"

BLOCK_SIZE = 4 * 1024 * 1024  # 4 MB (Baidu server constraint)
UPLOAD_CONCURRENCY = 3        # max concurrent block uploads


class BaiduDriver(CloudDriver):

    # Per-path locks to prevent concurrent mkdir from creating
    # auto-renamed duplicate folders (Baidu adds _2026... suffix).
    _mkdir_locks: dict[str, asyncio.Lock] = {}

    def __init__(self, app_key: str = "", secret_key: str = "",
                 account: AccountInfo | None = None):
        super().__init__(account)
        self._app_key = app_key
        self._secret_key = secret_key
        self._http = httpx.AsyncClient(timeout=60.0)
        self._bdstoken: str | None = None  # cached CSRF token for web API

    @property
    def _is_cookie_mode(self) -> bool:
        return not self._app_key

    @staticmethod
    def _safe_json(resp, label: str = "") -> dict:
        """Parse JSON response body, return empty dict on failure."""
        try:
            return resp.json()
        except Exception:
            log_upload_event(f"  JSON 解析失败 [{label}]: {resp.text[:200]}")
            return {}

    # ── auth helpers ────────────────────────────────────────────────

    def _parse_cookies(self) -> dict[str, str]:
        """Parse the stored token into individual cookies.

        Token format: 'BDUSS=xxx|STOKEN=yyy' (CDP extracts multiple cookies).
        Falls back to treating the whole token as BDUSS if no '|' separator.
        """
        tok = self._ensure_token()
        cookies = {}
        if "|" in tok:
            for part in tok.split("|"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    cookies[k] = v
        if not cookies:
            cookies["BDUSS"] = tok
        return cookies

    def _build_cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self._parse_cookies().items())

    def _web_headers(self) -> dict:
        return {
            "Cookie": self._build_cookie_header(),
            "Referer": "https://pan.baidu.com/disk/home",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
            ),
        }

    def _tok_preview(self) -> str:
        tok = self._ensure_token()
        if "|" in tok:
            parts = []
            for p in tok.split("|"):
                if "=" in p:
                    k, v = p.split("=", 1)
                    parts.append(f"{k}={v[:8]}...")
                else:
                    parts.append(p[:20] + "...")
            return ", ".join(parts)
        return tok[:20] + "..." if len(tok) > 20 else tok

    async def _get_bdstoken(self) -> str:
        """Fetch and cache the bdstoken (CSRF token) needed for web API calls."""
        if self._bdstoken:
            return self._bdstoken

        headers = self._web_headers()

        # Try multiple methods to get bdstoken
        for method in ("api_json", "api_plain", "homepage"):
            try:
                if method == "api_json":
                    log_upload_event("  获取 bdstoken [api_json]...")
                    resp = await self._http.get(
                        BAIDU_BDSTOKEN_API,
                        params={"fields": '["bdstoken"]'},
                        headers=headers,
                    )
                elif method == "api_plain":
                    log_upload_event("  获取 bdstoken [api_plain]...")
                    resp = await self._http.get(
                        BAIDU_BDSTOKEN_API,
                        params={"fields": "bdstoken"},
                        headers=headers,
                    )
                else:
                    log_upload_event("  获取 bdstoken [homepage]...")
                    resp = await self._http.get(
                        BAIDU_HOME, headers=headers, follow_redirects=True,
                    )

                log_upload_event(f"  bdstoken 响应: HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    log_upload_event(f"  响应体: {resp.text[:200]}")
                    resp.raise_for_status()

                token = self._parse_bdstoken(resp, method)
                if token:
                    self._bdstoken = token
                    log_upload_event(f"  bdstoken 获取成功: {token[:8]}...")
                    return token
            except Exception as e:
                log_upload_event(f"  {method} 失败: {e}")

        raise RuntimeError("无法获取 bdstoken，BDUSS 可能已过期")

    def _parse_bdstoken(self, resp, method: str) -> str:
        """Extract bdstoken from API JSON or HTML response."""
        if method in ("api_json", "api_plain"):
            data = resp.json()
            log_upload_event(f"  bdstoken 原始响应: {json.dumps(data, ensure_ascii=False)[:400]}")
            result = data.get("result", data)
            # Handle list result (fields=["bdstoken"] → result=["value"])
            if isinstance(result, list):
                for item in result:
                    if isinstance(item, dict):
                        token = item.get("bdstoken", "")
                    else:
                        token = str(item)
                    if token and len(token) > 8:
                        return token
            # Handle dict result
            if isinstance(result, dict):
                token = result.get("bdstoken", "")
                if token:
                    return token
            # Fallback: search entire response for token-like field
            raw = json.dumps(data)
            match = re.search(r'"bdstoken"\s*:\s*"([^"]{8,})"', raw)
            if match:
                return match.group(1)

        elif method == "homepage":
            text = resp.text
            log_upload_event(f"  homepage 长度: {len(text)} chars, 含 bdstoken: {'bdstoken' in text}")
            # Multiple regex patterns
            for pat in (
                r'"bdstoken"\s*:\s*"([^"]+)"',
                r'bdstoken["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                r'yunData\.bdstoken\s*=\s*"([^"]+)"',
                r'window\.bdstoken\s*=\s*"([^"]+)"',
            ):
                match = re.search(pat, text)
                if match:
                    return match.group(1)

        return ""

    def _compute_block_list(self, file_path: str) -> list[str]:
        blocks = []
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(BLOCK_SIZE)
                if not chunk:
                    break
                blocks.append(hashlib.md5(chunk).hexdigest())
        return blocks

    @staticmethod
    def _get_pcs_host(uploadid: str) -> str | None:
        """Return the PCS upload server URL for superfile2.

        The uploadid encodes the internal PCS server IP (base64).
        218.x.x.x IPs are public China Unicom addresses reachable via HTTP.
        Returns None if the IP cannot be decoded.
        """
        if "-" not in uploadid:
            return None
        try:
            encoded = uploadid.split("-", 1)[1]
            decoded = base64.b64decode(encoded).decode("ascii")
            ip = decoded.split(":")[0]
            parts = ip.split(".")
            if len(parts) == 4 and all(p.isdigit() for p in parts):
                return f"http://{ip}"
        except Exception:
            pass
        return None

    # ── public interface ────────────────────────────────────────────

    async def close(self):
        await self._http.aclose()

    def get_auth_url(self) -> str:
        params = {
            "response_type": "code",
            "client_id": self._app_key,
            "redirect_uri": "oob",
            "scope": "basic,netdisk",
        }
        return f"{BAIDU_OAUTH_AUTHORIZE}?{urlencode(params)}"

    async def login(self, auth_code: str) -> AccountInfo:
        if not self._app_key:
            # Set account first so _web_headers() can use it
            account = AccountInfo(
                provider="baidu",
                account_name="baidu_user",
                access_token=auth_code,
                refresh_token="",
                expires_at=time.time() + 86400 * 30,
            )
            self._account = account

            # Fetch real user name from Baidu API
            try:
                headers = self._web_headers()
                resp = await self._http.get(
                    "https://pan.baidu.com/rest/2.0/xpan/nas",
                    params={"method": "uinfo"},
                    headers=headers,
                )
                data = resp.json()
                if data.get("errno") == 0:
                    name = data.get("baidu_name", "") or ""
                    if name:
                        account.account_name = name
            except Exception:
                pass

            return account

        data = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "client_id": self._app_key,
            "client_secret": self._secret_key,
            "redirect_uri": "oob",
        }
        resp = await self._http.post(BAIDU_OAUTH_TOKEN, data=data)
        resp.raise_for_status()
        result = resp.json()
        account = AccountInfo(
            provider="baidu",
            account_name=f"baidu_user_{result.get('uid', 'unknown')}",
            access_token=result.get("access_token", ""),
            refresh_token=result.get("refresh_token", ""),
            expires_at=time.time() + result.get("expires_in", 2592000),
            extra={"uid": result.get("uid", ""), "scope": result.get("scope", "")},
        )
        self._account = account
        return account

    async def refresh_token(self) -> str:
        if not self._account or not self._account.refresh_token:
            raise ValueError("No refresh token available")
        if self._is_cookie_mode:
            return self._account.access_token
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self._account.refresh_token,
            "client_id": self._app_key,
            "client_secret": self._secret_key,
        }
        resp = await self._http.post(BAIDU_OAUTH_TOKEN, data=data)
        resp.raise_for_status()
        result = resp.json()
        self._account.access_token = result.get("access_token", "")
        self._account.refresh_token = result.get("refresh_token", self._account.refresh_token)
        self._account.expires_at = time.time() + result.get("expires_in", 2592000)
        return self._account.access_token

    @async_retry(max_attempts=3, delay=1.0, exceptions=(httpx.HTTPStatusError,))
    async def upload_file(
        self, local_path: str, remote_dir: str, progress_callback=None,
    ) -> str:
        if self._is_cookie_mode:
            return await self._upload_web(local_path, remote_dir, progress_callback)
        return await self._upload_oauth(local_path, remote_dir, progress_callback)

    async def _upload_web(self, local_path: str, remote_dir: str,
                          progress_callback=None) -> str:
        """Upload via Baidu web API matching browser behavior.

        Flow: precreate → superfile2 (PCS CDN) → create (commit).

        precreate/commit use pan.baidu.com web API (bdstoken auth).
        superfile2 uses d.pcs.baidu.com PCS REST API (cookie auth, no bdstoken).
        A dp-logid ties the requests together.
        """
        tok = self._ensure_token()
        file_name = os.path.basename(local_path)
        remote_path = f"{remote_dir.rstrip('/')}/{file_name}"
        file_size = os.path.getsize(local_path)
        local_mtime = int(os.path.getmtime(local_path))

        log_upload_event(f"  Baidu [Web] 上传: {file_name} ({file_size} bytes) -> {remote_path}")
        log_upload_event(f"  Cookies: {self._tok_preview()}")

        await self._mkdir_web(remote_dir)

        bdstoken = await self._get_bdstoken()
        block_list = self._compute_block_list(local_path)
        block_list_str = json.dumps(block_list, ensure_ascii=False)

        # dp-logid links precreate→superfile2→create (browser uses this)
        import random as _random
        dp_logid = str(int(time.time() * 1000000)) + str(_random.randint(10000, 99999))

        headers = self._web_headers()
        common_params = f"bdstoken={bdstoken}&{BAIDU_WEB_PARAMS}&dp-logid={dp_logid}"

        # ── Step 1: precreate ──────────────────────────────────────────
        precreate_body = {
            "path": remote_path,
            "autoinit": "1",
            "block_list": block_list_str,
            "target_path": "/",
            "local_mtime": str(local_mtime),
        }
        log_upload_event(f"  precreate: {remote_path}")
        resp = await self._http.post(
            f"{BAIDU_PRECREATE}?{common_params}",
            data=precreate_body,
            headers=headers,
        )
        log_upload_event(f"  precreate 响应: HTTP {resp.status_code}")
        body_text = resp.text[:500]
        log_upload_event(f"  precreate 响应体: {body_text}")
        if resp.status_code >= 400:
            resp.raise_for_status()
        pc = self._safe_json(resp, "precreate")
        pc_errno = pc.get("errno", -1)
        if pc_errno != 0:
            raise Exception(f"precreate errno={pc_errno}: {pc}")

        uploadid = pc.get("uploadid", "")
        if pc.get("rapid_upload", 0) == 1 or not uploadid:
            fs_id = str(pc.get("info", {}).get("fs_id", ""))
            log_upload_event(f"  秒传成功 fs_id={fs_id}")
            if progress_callback:
                progress_callback(file_size, file_size)
            return fs_id

        log_upload_event(f"  uploadid={uploadid}")

        # ── Step 2: superfile2 (upload binary to PCS CDN) ─────────────
        # Each block (4MB chunk) is uploaded independently. Blocks are
        # uploaded concurrently with a shared HTTP client for connection
        # pooling. The server accepts out-of-order blocks (keyed by partseq).
        num_blocks = len(block_list)
        log_upload_event(f"  superfile2 上传 {num_blocks} 块并发 (并发 {UPLOAD_CONCURRENCY})...")

        sem = asyncio.Semaphore(UPLOAD_CONCURRENCY)
        progress_lock = asyncio.Lock()
        total_uploaded = 0
        block_errors: list[Exception] = []

        async def _upload_one_block(block_idx: int) -> None:
            nonlocal total_uploaded
            with open(local_path, "rb") as f:
                f.seek(block_idx * BLOCK_SIZE)
                block_data = f.read(BLOCK_SIZE)

            sf2_params = {
                "method": "upload",
                "app_id": BAIDU_APP_ID,
                "channel": "chunlei",
                "web": "1",
                "clienttype": "0",
                "path": remote_path,
                "uploadid": uploadid,
                "uploadsign": "0",
                "partseq": str(block_idx),
                "dp-logid": dp_logid,
            }

            sf2_url = "https://d.pcs.baidu.com/rest/2.0/pcs/superfile2"
            cookie = self._build_cookie_header()

            async with sem:
                resp = await oss_client.post(
                    sf2_url, params=sf2_params,
                    files={"file": (file_name, block_data, "application/octet-stream")},
                    headers={"Accept-Encoding": "identity", "Cookie": cookie},
                )

            log_upload_event(f"  superfile2 块 {block_idx}: HTTP {resp.status_code}")
            if resp.status_code >= 400:
                log_upload_event(f"  superfile2 响应体: {resp.content[:500]!r}")
                resp.raise_for_status()
            sf2_body = self._safe_json(resp, "superfile2")
            log_upload_event(f"  superfile2 body: {json.dumps(sf2_body, ensure_ascii=False)[:300]}")

            async with progress_lock:
                total_uploaded += len(block_data)
                if progress_callback:
                    progress_callback(total_uploaded, file_size)

        # Shared HTTP client with connection pooling for all block uploads
        oss_client = httpx.AsyncClient(timeout=600.0)
        try:
            tasks = [asyncio.create_task(_upload_one_block(i)) for i in range(num_blocks)]
            for task in tasks:
                try:
                    await task
                except Exception as e:
                    block_errors.append(e)
        finally:
            await oss_client.aclose()

        if block_errors:
            raise Exception(f"块上传失败: {len(block_errors)}/{num_blocks} 个块出错")

        # ── Step 3: create (commit) ────────────────────────────────────
        # The browser calls this "create" — it finalizes the upload.
        # Use /api/create with uploadid and block_list.
        create_body = {
            "path": remote_path,
            "isdir": "0",
            "size": str(file_size),
            "block_list": block_list_str,
            "uploadid": uploadid,
            "rtype": "1",
        }
        log_upload_event(f"  create (commit): {remote_path}")
        resp = await self._http.post(
            f"{BAIDU_CREATE}?{common_params}",
            data=create_body,
            headers=headers,
        )
        log_upload_event(f"  create 响应: HTTP {resp.status_code}")
        commit_text = resp.text[:500]
        log_upload_event(f"  create 响应体: {commit_text}")
        if resp.status_code >= 400:
            resp.raise_for_status()
        cr = self._safe_json(resp, "create")
        cr_errno = cr.get("errno", -1)
        if cr_errno == 31030:
            # File already exists — not an error
            log_upload_event(f"  文件已存在 (31030)")
        elif cr_errno != 0:
            raise Exception(f"create errno={cr_errno}: {cr}")

        fs_id = str(cr.get("info", {}).get("fs_id", "")) or str(cr.get("fs_id", ""))
        log_upload_event(f"  Baidu [Web] 上传成功 fs_id={fs_id}")
        return fs_id

    async def _upload_oauth(self, local_path: str, remote_dir: str,
                            progress_callback=None) -> str:
        """Upload via PCS API (OAuth mode)."""
        tok = self._ensure_token()
        file_name = os.path.basename(local_path)
        remote_path = f"{remote_dir.rstrip('/')}/{file_name}"
        file_size = os.path.getsize(local_path)

        log_upload_event(f"  Baidu [OAuth] 上传: {file_name} ({file_size} bytes) -> {remote_path}")

        await self.create_folder(remote_dir)

        with open(local_path, "rb") as f:
            data = f.read()

        params = {"method": "upload", "access_token": tok, "path": remote_path}
        files = {"file": (file_name, data, "application/octet-stream")}

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "POST", BAIDU_PCS_API, params=params, files=files,
            ) as resp:
                log_upload_event(f"  PCS API 响应: HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")[:300]
                    log_upload_event(f"  响应体: {body}")
                resp.raise_for_status()
                result = await resp.aread()

        info = json.loads(result)
        errno = info.get("errno", -1)
        if errno != 0:
            raise Exception(f"Baidu API errno={errno}: {info}")
        fs_id = str(info.get("fs_id", ""))
        log_upload_event(f"  Baidu [OAuth] 上传成功 fs_id={fs_id}")
        return fs_id

    async def _mkdir_web(self, remote_dir: str) -> str:
        """Ensure a remote directory path exists using browser-aligned API.

        Uses /api/create?a=commit (the same endpoint the browser uses
        when clicking "New Folder" on pan.baidu.com).

        Serializes per-path to prevent concurrent tasks from creating
        auto-renamed duplicates (_20260517_...).
        """
        dir_path = remote_dir.rstrip("/")
        if not dir_path or dir_path == "/":
            return "0"

        bdstoken = await self._get_bdstoken()
        headers = self._web_headers()

        parts = [p for p in dir_path.split("/") if p]
        built = ""
        for part in parts:
            built = f"{built}/{part}"

            # Serialize folder creation to avoid auto-rename race
            lock = BaiduDriver._mkdir_locks.setdefault(built, asyncio.Lock())
            async with lock:
                # Double-check after acquiring the lock — another task
                # may have created it while we were waiting.
                try:
                    existing = await self._find_item_web(built, bdstoken, headers)
                    if existing:
                        log_upload_event(f"  mkdir {built}: 已存在")
                        continue
                except Exception:
                    pass

                # Create folder matching browser behavior:
                # POST /api/create?a=commit&bdstoken=...&clienttype=0&app_id=250528&web=1
                # Form: path=/PanUpdate_backup  isdir=1  block_list=[]
                mkdir_url = (
                    f"{BAIDU_CREATE}?a=commit&bdstoken={bdstoken}&{BAIDU_WEB_PARAMS}"
                )
                mkdir_body = {
                    "path": built,
                    "isdir": "1",
                    "block_list": "[]",
                }
                try:
                    resp = await self._http.post(
                        mkdir_url,
                        data=mkdir_body,
                        headers=headers,
                    )
                    body_text = resp.text[:500]
                    log_upload_event(f"  mkdir {built}: HTTP {resp.status_code}")
                    log_upload_event(f"  mkdir 响应: {body_text}")
                    if resp.status_code == 200:
                        body = self._safe_json(resp, "mkdir")
                        errno = body.get("errno", -1)
                        if errno == 0:
                            log_upload_event(f"  mkdir {built}: 已创建")
                        elif errno == 31030:
                            log_upload_event(f"  mkdir {built}: 已存在 (31030)")
                        else:
                            log_upload_event(f"  mkdir {built}: errno={errno}")
                    else:
                        log_upload_event(f"  mkdir {built} 失败: {resp.text[:200]}")
                except Exception as e:
                    log_upload_event(f"  mkdir {built} 异常: {e}")

        return "0"

    async def _find_item_web(self, path: str, bdstoken: str,
                             headers: dict) -> bool:
        """Check if a file/folder exists at the given path via filemetas API."""
        try:
            resp = await self._http.get(
                f"{BAIDU_FILE_META}?bdstoken={bdstoken}&{BAIDU_WEB_PARAMS}",
                params={"target": json.dumps([path]), "dlink": "1"},
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                info = data.get("info", [])
                if info and info[0].get("errno") == 0:
                    return True
        except Exception:
            pass
        return False

    async def create_folder(self, remote_path: str) -> bool:
        if self._is_cookie_mode:
            try:
                await self._mkdir_web(remote_path)
                return True
            except Exception:
                return False

        tok = self._ensure_token()
        params = {"method": "create", "access_token": tok}
        body = {"path": remote_path.rstrip("/"), "size": 0, "isdir": 1}
        resp = await self._http.post(
            BAIDU_PCS_API, params=params, json=body,
        )
        resp.raise_for_status()
        result = resp.json()
        return result.get("errno") == 0

    async def list_files(self, remote_dir: str) -> list[FileItem]:
        if self._is_cookie_mode:
            return await self._list_files_web(remote_dir)

        tok = self._ensure_token()
        params = {"method": "list", "access_token": tok, "dir": remote_dir}
        resp = await self._http.get(BAIDU_PCS_API, params=params)
        resp.raise_for_status()
        result = resp.json()
        items = []
        for entry in result.get("list", []):
            items.append(FileItem(
                path=entry.get("path", ""),
                name=entry.get("filename", ""),
                is_dir=entry.get("isdir", 0) == 1,
                size=entry.get("size", 0),
                file_id=str(entry.get("fs_id", "")),
            ))
        return items

    async def _list_files_web(self, remote_dir: str) -> list[FileItem]:
        """List files using web API (cookie mode)."""
        bdstoken = await self._get_bdstoken()
        headers = self._web_headers()
        params = {
            "bdstoken": bdstoken,
            "dir": remote_dir,
            "order": "time",
            "desc": "1",
            "num": "100",
            "page": "1",
        }
        resp = await self._http.get(
            "https://pan.baidu.com/api/list",
            params=params, headers=headers,
        )
        resp.raise_for_status()
        result = resp.json()
        items = []
        for entry in result.get("list", []):
            items.append(FileItem(
                path=entry.get("path", ""),
                name=entry.get("server_filename", ""),
                is_dir=entry.get("isdir", 0) == 1,
                size=int(entry.get("size", 0)),
                file_id=str(entry.get("fs_id", "")),
            ))
        return items

    async def get_quota(self) -> QuotaInfo:
        tok = self._ensure_token()
        if self._is_cookie_mode:
            headers = self._web_headers()
            resp = await self._http.get(BAIDU_QUOTA_API, headers=headers)
        else:
            params = {"access_token": tok}
            resp = await self._http.get(BAIDU_QUOTA_API, params=params)
        resp.raise_for_status()
        result = resp.json()
        return QuotaInfo(
            total=result.get("total", 0),
            used=result.get("used", 0),
        )

    async def test_connection(self) -> bool:
        try:
            await self.get_quota()
            return True
        except Exception:
            return False

    def _ensure_token(self) -> str:
        if not self._account or not self._account.access_token:
            raise ValueError("Not logged in. Call login() first.")
        if self._is_cookie_mode:
            return self._account.access_token
        if time.time() >= self._account.expires_at and self._account.refresh_token:
            import asyncio
            return asyncio.run(self.refresh_token())
        return self._account.access_token
