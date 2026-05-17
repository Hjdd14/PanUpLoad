"""夸克云盘 (Quark Cloud) driver — aligned with QuarkPan open-source tool."""

import hashlib
import time
import os
from datetime import datetime, timezone
import base64 as _base64
import json as _json

import httpx

from panupdate.drivers.base import (
    CloudDriver, AccountInfo, FileItem, QuotaInfo,
)
from panupdate.utils.upload_logger import log_upload_event, log_exception


QUARK_BASE = "https://drive-pc.quark.cn/1/clouddrive"
QUARK_PAN_SORT = "https://pan.quark.cn/1/clouddrive/file/sort"
QUARK_FILE_CREATE = QUARK_BASE + "/file"
QUARK_UPLOAD_PRE = QUARK_BASE + "/file/upload/pre"
QUARK_UPLOAD_AUTH = QUARK_BASE + "/file/upload/auth"
QUARK_UPDATE_HASH = QUARK_BASE + "/file/update/hash"
QUARK_UPLOAD_FINISH = QUARK_BASE + "/file/upload/finish"

DRIVE_PARAMS = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}


class KuaikeDriver(CloudDriver):
    """夸克云盘 driver — based on QuarkPan open-source implementation."""

    def __init__(self, account: AccountInfo | None = None):
        super().__init__(account)
        self._http = httpx.AsyncClient(timeout=60.0)

    async def close(self):
        await self._http.aclose()

    def get_auth_url(self) -> str:
        return "https://pan.quark.cn/"

    async def login(self, token: str) -> AccountInfo:
        account = AccountInfo(
            provider="kuaike",
            account_name="quark_user",
            access_token=token,
            refresh_token="",
            expires_at=time.time() + 86400 * 7,
        )
        self._account = account

        # Fetch real user name from Quark API
        try:
            cookie_str = self._build_cookie(token)
            resp = await self._http.get(
                "https://pan.quark.cn/account/info",
                headers={
                    "Cookie": cookie_str,
                    "Referer": "https://pan.quark.cn/",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                name = data.get("nickname", "") or data.get("data", {}).get("nickname", "")
                if name:
                    account.account_name = name
        except Exception:
            pass

        return account

    async def refresh_token(self) -> str:
        if not self._account:
            raise ValueError("Not logged in")
        return self._account.access_token

    def _build_cookie(self, token: str) -> str:
        """Convert pipe-separated key=value pairs to Cookie header."""
        if "|" in token:
            parts = []
            for p in token.split("|"):
                if "=" in p:
                    parts.append(p.strip())
            return "; ".join(parts) if parts else token
        return token

    def _headers(self, token: str) -> dict:
        return {
            "Content-Type": "application/json",
            "Cookie": self._build_cookie(token),
            "Origin": "https://pan.quark.cn",
            "Referer": "https://pan.quark.cn/",
        }

    # ── upload_file (based on QuarkPan) ──────────────────────────────

    async def upload_file(
        self, local_path: str, remote_dir: str, progress_callback=None,
    ) -> str:
        tok = self._ensure_token()
        file_name = os.path.basename(local_path)
        file_size = os.path.getsize(local_path)
        log_upload_event(f"  Kuaike 上传开始: {file_name} ({file_size} bytes)")

        parent_id = await self._ensure_folder_path(remote_dir, tok)

        import mimetypes as _mime
        mime_type = _mime.guess_type(file_name)[0] or "application/octet-stream"

        # Compute file hashes
        md5_hash, sha1_hash = self._hash_file(local_path)

        # Step 1: upload/pre
        now_ms = int(time.time() * 1000)
        pre_data = {
            "ccp_hash_update": True,
            "parallel_upload": True,
            "pdir_fid": parent_id,
            "dir_name": "",
            "size": file_size,
            "file_name": file_name,
            "format_type": mime_type,
            "l_updated_at": now_ms,
            "l_created_at": now_ms,
        }
        headers = self._headers(tok)
        resp = await self._http.post(
            QUARK_UPLOAD_PRE, headers=headers, json=pre_data, params=DRIVE_PARAMS,
        )
        log_upload_event(f"  upload/pre: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            log_upload_event(f"  响应体: {resp.text[:300]}")
        resp.raise_for_status()
        pre = resp.json()
        pre_d = pre.get("data", pre)
        task_id = pre_d["task_id"]
        auth_info = pre_d.get("auth_info", "")
        upload_id = pre_d.get("upload_id", "")
        obj_key = pre_d.get("obj_key", "")
        bucket = pre_d.get("bucket", "ul-zb")
        callback_info = pre_d.get("callback", {})

        # Step 2: update file hash
        hash_data = {"task_id": task_id, "md5": md5_hash, "sha1": sha1_hash}
        resp = await self._http.post(
            QUARK_UPDATE_HASH, headers=headers, json=hash_data, params=DRIVE_PARAMS,
        )
        log_upload_event(f"  update/hash: HTTP {resp.status_code}")
        resp.raise_for_status()

        # Step 3: get upload auth (single part)
        oss_date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        auth_meta = (
            f"PUT\n\n{mime_type}\n{oss_date}\n"
            f"x-oss-date:{oss_date}\n"
            f"x-oss-user-agent:aliyun-sdk-js/1.0.0 Chrome 148.0.0.0 on Windows 10 64-bit\n"
            f"/{bucket}/{obj_key}?partNumber=1&uploadId={upload_id}"
        )
        auth_data = {
            "task_id": task_id,
            "auth_info": auth_info,
            "auth_meta": auth_meta,
        }
        resp = await self._http.post(
            QUARK_UPLOAD_AUTH, headers=headers, json=auth_data, params=DRIVE_PARAMS,
        )
        log_upload_event(f"  upload/auth: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            log_upload_event(f"  响应体: {resp.text[:300]}")
        resp.raise_for_status()
        auth_resp = resp.json()
        if auth_resp.get("code", 0) != 0:
            raise Exception(f"upload auth failed: {auth_resp.get('message')}")
        auth_key = auth_resp.get("data", {}).get("auth_key", "")

        # Step 4: PUT file to OSS (try multiple URL formats)
        oss_urls = [
            f"https://{bucket}.oss-cn-shenzhen.aliyuncs.com/{obj_key}?partNumber=1&uploadId={upload_id}",
            f"https://{bucket}.pds.quark.cn/{obj_key}?partNumber=1&uploadId={upload_id}",
            f"http://pds.quark.cn/{obj_key}?partNumber=1&uploadId={upload_id}",
        ]
        oss_url = oss_urls[0]
        oss_headers = {
            "Content-Type": mime_type,
            "x-oss-date": oss_date,
            "x-oss-user-agent": "aliyun-sdk-js/1.0.0 Chrome 148.0.0.0 on Windows 10 64-bit",
        }
        if auth_key:
            oss_headers["authorization"] = auth_key

        with open(local_path, "rb") as f:
            file_data = f.read()
        # Try multiple OSS URL formats
        oss_success = False
        etag = ""
        for oss_url in oss_urls:
            log_upload_event(f"  PUT OSS: {oss_url[:80]}...")
            async with httpx.AsyncClient(timeout=600.0, verify=False) as client:
                resp = await client.put(oss_url, content=file_data, headers=oss_headers)
                log_upload_event(f"  PUT: HTTP {resp.status_code}")
                if resp.status_code == 200:
                    etag = resp.headers.get("etag", "").strip('"')
                    oss_success = True
                    break
                log_upload_event(f"  PUT 响应体: {resp.text[:200]}")
        if not oss_success:
            raise Exception("OSS PUT failed with all URL formats")

        # Step 5: POST-complete auth (with XML)
        xml_data = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<CompleteMultipartUpload>\n'
            f'<Part>\n<PartNumber>1</PartNumber>\n<ETag>"{etag}"</ETag>\n</Part>\n'
            '</CompleteMultipartUpload>'
        )
        xml_md5 = _base64.b64encode(hashlib.md5(xml_data.encode()).digest()).decode()
        cb_b64 = _base64.b64encode(
            _json.dumps(callback_info, separators=(",", ":")).encode()
        ).decode()
        post_meta = (
            f"POST\n{xml_md5}\napplication/xml\n{oss_date}\n"
            f"x-oss-callback:{cb_b64}\n"
            f"x-oss-date:{oss_date}\n"
            f"x-oss-user-agent:aliyun-sdk-js/1.0.0 Chrome 148.0.0.0 on Windows 10 64-bit\n"
            f"/{bucket}/{obj_key}?uploadId={upload_id}"
        )
        post_auth_data = {
            "task_id": task_id,
            "auth_info": auth_info,
            "auth_meta": post_meta,
        }
        resp = await self._http.post(
            QUARK_UPLOAD_AUTH, headers=headers, json=post_auth_data, params=DRIVE_PARAMS,
        )
        log_upload_event(f"  complete/auth: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            log_upload_event(f"  响应体: {resp.text[:300]}")
        resp.raise_for_status()
        post_auth = resp.json()
        if not post_auth.get("status"):
            # POST-complete auth failure is not fatal — file may still upload
            log_upload_event(f"  complete/auth warning: {post_auth.get('message', '')}")
        post_auth_key = post_auth.get("data", {}).get("auth_key", "")
        post_upload_url = oss_url.replace("?partNumber=1&", "?")
        post_headers = {
            "Content-Type": "application/xml",
            "x-oss-date": oss_date,
            "x-oss-user-agent": "aliyun-sdk-js/1.0.0 Chrome 148.0.0.0 on Windows 10 64-bit",
            "x-oss-callback": cb_b64,
            "Content-MD5": xml_md5,
        }
        if post_auth_key:
            post_headers["authorization"] = post_auth_key

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(post_upload_url, content=xml_data, headers=post_headers)
            log_upload_event(f"  POST complete: HTTP {resp.status_code}")
            if resp.status_code not in (200, 203):
                log_upload_event(f"  POST complete body: {resp.text[:300]}")

        # Step 7: finish upload
        finish_data = {"task_id": task_id, "obj_key": obj_key}
        resp = await self._http.post(
            QUARK_UPLOAD_FINISH, headers=headers, json=finish_data, params=DRIVE_PARAMS,
        )
        log_upload_event(f"  upload/finish: HTTP {resp.status_code}")
        resp.raise_for_status()

        if progress_callback:
            progress_callback(file_size, file_size)

        fid = str(pre_d.get("fid", task_id))
        log_upload_event(f"  Kuaike 上传成功 fid={fid}")
        return fid

    @staticmethod
    def _hash_file(path: str):
        md5_h = hashlib.md5()
        sha1_h = hashlib.sha1()
        with open(path, "rb") as f:
            while chunk := f.read(8192):
                md5_h.update(chunk)
                sha1_h.update(chunk)
        return md5_h.hexdigest(), sha1_h.hexdigest()

    # ── folder operations ─────────────────────────────────────────────

    async def create_folder(self, remote_path: str) -> bool:
        try:
            await self._ensure_folder_path(
                remote_path, self._ensure_token(),
            )
            return True
        except Exception:
            return False

    async def list_files(self, remote_dir: str) -> list[FileItem]:
        tok = self._ensure_token()
        parent_id = await self._get_folder_id(remote_dir, tok)
        headers = self._headers(tok)
        params = {"page": 1, "size": 100, "pdir_fid": parent_id}
        params.update(DRIVE_PARAMS)
        resp = await self._http.get(QUARK_PAN_SORT, headers=headers, params=params)
        resp.raise_for_status()
        result = resp.json()
        items = []
        for entry in result.get("data", {}).get("list", []):
            items.append(FileItem(
                path="",
                name=entry.get("file_name", ""),
                is_dir=entry.get("dir", False) or entry.get("file_type", 1) == 0,
                size=int(entry.get("size", 0)),
                file_id=str(entry.get("file_id", "")),
            ))
        return items

    async def get_quota(self) -> QuotaInfo:
        return QuotaInfo()

    async def test_connection(self) -> bool:
        try:
            await self.list_files("/")
            return True
        except Exception:
            return False

    # ── helpers ───────────────────────────────────────────────────────

    def _ensure_token(self) -> str:
        if not self._account or not self._account.access_token:
            raise ValueError("Not logged in. Call login() first.")
        return self._account.access_token

    async def _ensure_folder_path(self, remote_path: str, tok: str) -> str:
        """Walk path, try to create missing folders. Falls back to parent on failure."""
        parent_id = "0"
        parts = [p for p in remote_path.strip("/").split("/") if p]
        for part in parts:
            existing = await self._find_folder(parent_id, part, tok)
            if existing:
                parent_id = existing
                continue
            # Try to create folder with QuarkPan parameters
            headers = self._headers(tok)
            data = {
                "pdir_fid": parent_id,
                "file_name": part,
                "dir_init_lock": False,
                "dir_path": "",
            }
            try:
                resp = await self._http.post(
                    QUARK_FILE_CREATE, headers=headers, json=data, params=DRIVE_PARAMS,
                )
                log_upload_event(f"  创建文件夹 {part}: HTTP {resp.status_code}")
                if resp.status_code == 200:
                    result = resp.json()
                    log_upload_event(f"  创建响应: {_json.dumps(result, ensure_ascii=False)[:300]}")
                    # Try multiple possible response fields for the new folder ID
                    d = result.get("data", result)
                    new_id = str(d.get("file_id", "") or d.get("fid", "") or
                                result.get("file_id", "") or "")
                    if new_id:
                        parent_id = new_id
                        log_upload_event(f"  已创建 {part}, id={parent_id}")
                        continue
                    else:
                        log_upload_event(f"  无法获取新文件夹ID, 响应keys: {list(result.keys())}")
            except Exception as e:
                log_upload_event(f"  创建文件夹异常: {e}")
            # Creation failed — retry find (may have been created by concurrent task)
            retry = await self._find_folder(parent_id, part, tok)
            if retry:
                log_upload_event(f"  重试找到文件夹 {part}, id={retry}")
                parent_id = retry
                continue
            log_upload_event(f"  文件夹 {part} 创建失败，回退到父级: {parent_id}")
            return parent_id
        return parent_id

    async def _get_folder_id(self, remote_path: str, tok: str) -> str:
        return await self._ensure_folder_path(remote_path, tok)

    async def _find_folder(self, parent_id: str, name: str, tok: str) -> str | None:
        headers = self._headers(tok)
        params = {"page": 1, "size": 100, "pdir_fid": parent_id}
        params.update(DRIVE_PARAMS)
        resp = await self._http.get(QUARK_PAN_SORT, headers=headers, params=params)
        log_upload_event(f"  GET file/sort({parent_id}): HTTP {resp.status_code}")
        if resp.status_code >= 400:
            log_upload_event(f"  响应体: {resp.text[:200]}")
        resp.raise_for_status()
        result = resp.json()
        entries = result.get("data", {}).get("list", [])
        log_upload_event(f"  file/sort 找到 {len(entries)} 条, 查找 '{name}'")
        if entries:
            log_upload_event(f"  第一条keys: {list(entries[0].keys())}")
        for entry in entries:
            if entry.get("file_name") == name and (
                entry.get("dir", False) or entry.get("file_type", 0) == 0
            ):
                fid = str(entry.get("file_id", "") or entry.get("fid", "") or
                         entry.get("id", "") or entry.get("fileId", ""))
                log_upload_event(f"  找到文件夹 {name}, id={fid}")
                if fid:
                    return fid
        return None

    def _params(self, token: str, **extra) -> dict:
        return {**extra}
