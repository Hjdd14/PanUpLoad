"""Tests for BaiduNetdisk driver (mocked HTTP)."""

import re

import pytest
import httpx
from panupdate.drivers.baidu import BaiduDriver
from panupdate.drivers.base import AccountInfo


class TestBaiduDriverAuth:
    """Test OAuth flow and token management."""

    AUTH_URL_PREFIX = "https://openapi.baidu.com/oauth/2.0/authorize?"

    def test_get_auth_url(self):
        driver = BaiduDriver(app_key="test_key", secret_key="test_secret")
        url = driver.get_auth_url()
        assert url.startswith(self.AUTH_URL_PREFIX)
        assert "client_id=test_key" in url
        assert "response_type=code" in url

    @pytest.mark.asyncio
    async def test_login(self, httpx_mock):
        httpx_mock.add_response(
            url="https://openapi.baidu.com/oauth/2.0/token",
            method="POST",
            json={
                "access_token": "acc_tok",
                "refresh_token": "ref_tok",
                "expires_in": 2592000,
                "uid": "12345",
                "scope": "basic,netdisk",
            },
        )
        driver = BaiduDriver(app_key="key", secret_key="secret")
        account = await driver.login("auth_code_123")

        assert account.provider == "baidu"
        assert account.access_token == "acc_tok"
        assert account.refresh_token == "ref_tok"
        assert account.extra["uid"] == "12345"

    @pytest.mark.asyncio
    async def test_refresh_token(self, httpx_mock):
        httpx_mock.add_response(
            url="https://openapi.baidu.com/oauth/2.0/token",
            method="POST",
            json={
                "access_token": "new_acc",
                "refresh_token": "new_ref",
                "expires_in": 2592000,
            },
        )
        account = AccountInfo(
            provider="baidu", account_name="test",
            access_token="old", refresh_token="old_ref",
            expires_at=0,  # expired
        )
        driver = BaiduDriver(app_key="key", secret_key="secret", account=account)
        new_tok = await driver.refresh_token()
        assert new_tok == "new_acc"
        assert driver.account.access_token == "new_acc"

    @pytest.mark.asyncio
    async def test_upload_file(self, httpx_mock, tmp_path):
        httpx_mock.add_response(
            url="https://pan.baidu.com/rest/2.0/xpan/file?method=create&access_token=tok",
            method="POST",
            json={"errno": 0},
        )
        httpx_mock.add_response(
            url=re.compile(r".*method=upload.*"),
            method="POST",
            json={"errno": 0, "fs_id": 98765},
        )

        account = AccountInfo(
            provider="baidu", account_name="test",
            access_token="tok", refresh_token="",
            expires_at=9999999999,
        )
        driver = BaiduDriver(app_key="key", secret_key="secret", account=account)

        test_file = tmp_path / "hello.txt"
        test_file.write_text("Hello PanUpdate")

        file_id = await driver.upload_file(str(test_file), "/apps/backup")
        assert file_id == "98765"

    @pytest.mark.asyncio
    async def test_list_files(self, httpx_mock):
        httpx_mock.add_response(
            url="https://pan.baidu.com/rest/2.0/xpan/file?method=list&access_token=tok&dir=/apps",
            method="GET",
            json={
                "list": [
                    {"path": "/apps/f1.txt", "filename": "f1.txt",
                     "isdir": 0, "size": 100, "fs_id": 1},
                    {"path": "/apps/sub", "filename": "sub",
                     "isdir": 1, "size": 0, "fs_id": 2},
                ]
            },
        )
        account = AccountInfo(
            provider="baidu", account_name="test",
            access_token="tok", refresh_token="", expires_at=9999999999,
        )
        driver = BaiduDriver(app_key="key", secret_key="secret", account=account)
        items = await driver.list_files("/apps")
        assert len(items) == 2
        assert items[0].name == "f1.txt"
        assert items[0].is_dir is False
        assert items[1].name == "sub"
        assert items[1].is_dir is True

    @pytest.mark.asyncio
    async def test_get_quota(self, httpx_mock):
        httpx_mock.add_response(
            url="https://pan.baidu.com/api/quota?access_token=tok",
            method="GET",
            json={"total": 1099511627776, "used": 536870912000},
        )
        account = AccountInfo(
            provider="baidu", account_name="test",
            access_token="tok", refresh_token="", expires_at=9999999999,
        )
        driver = BaiduDriver(app_key="key", secret_key="secret", account=account)
        quota = await driver.get_quota()
        assert quota.total == 1099511627776
        assert quota.used == 536870912000
        assert quota.remaining == quota.total - quota.used

    @pytest.mark.asyncio
    async def test_connection_ok(self, httpx_mock):
        httpx_mock.add_response(
            url="https://pan.baidu.com/api/quota?access_token=tok",
            method="GET", json={"total": 100, "used": 10},
        )
        account = AccountInfo(
            provider="baidu", account_name="test",
            access_token="tok", refresh_token="", expires_at=9999999999,
        )
        driver = BaiduDriver(app_key="key", secret_key="secret", account=account)
        assert await driver.test_connection() is True

    @pytest.mark.asyncio
    async def test_connection_fail(self, httpx_mock):
        httpx_mock.add_response(
            url="https://pan.baidu.com/api/quota?access_token=tok",
            method="GET", status_code=401,
        )
        account = AccountInfo(
            provider="baidu", account_name="test",
            access_token="tok", refresh_token="", expires_at=9999999999,
        )
        driver = BaiduDriver(app_key="key", secret_key="secret", account=account)
        assert await driver.test_connection() is False

    @pytest.mark.asyncio
    async def test_upload_without_login_raises(self):
        driver = BaiduDriver(app_key="key", secret_key="secret")
        with pytest.raises(ValueError, match="Not logged in"):
            await driver.upload_file("x.txt", "/")


class TestBaiduDriverWebMode:
    """Test cookie-mode (web API) upload flow aligned with browser behavior."""

    BAIDU_TOKEN_URL = "https://pan.baidu.com/api/gettemplatevariable"
    BD_TOKEN_BODY = {"errno": 0, "result": {"bdstoken": "test_bdstoken_123"}}

    def _mock_bdstoken(self, httpx_mock):
        httpx_mock.add_response(
            url=f"{self.BAIDU_TOKEN_URL}?fields=%5B%22bdstoken%22%5D",
            method="GET",
            json=self.BD_TOKEN_BODY,
        )

    def _mock_mkdir_chain(self, httpx_mock, folder_exists: bool = False):
        """Mock the mkdir flow: filemetas check → create folder."""
        httpx_mock.add_response(
            url=re.compile(r".*/api/filemetas\?.*"),
            method="GET",
            json={"info": [{"errno": 0}] if folder_exists else [{"errno": -9}]},
        )
        if not folder_exists:
            httpx_mock.add_response(
                url=re.compile(r".*/api/create\?a=commit.*"),
                method="POST",
                json={"errno": 0},
            )

    def _mk_account(self) -> AccountInfo:
        return AccountInfo(
            provider="baidu", account_name="web_user",
            access_token="BDUSS=fake_bduss|STOKEN=fake_stoken",
            refresh_token="", expires_at=9999999999,
        )

    @pytest.mark.asyncio
    async def test_web_upload(self, httpx_mock, tmp_path):
        """Full cookie-mode upload: mkdir → precreate → superfile2 → commit."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello Baidu Web")

        self._mock_bdstoken(httpx_mock)
        self._mock_mkdir_chain(httpx_mock, folder_exists=False)

        # precreate mock (is_reusable in case called multiple times)
        httpx_mock.add_response(
            url=re.compile(r".*/api/precreate\?.*"),
            method="POST",
            json={
                "errno": 0,
                "uploadid": "N1-upload-123",
                "rapid_upload": 0,
                "info": {"fs_id": 88888},
            },
            is_reusable=True,
        )
        # superfile2 mock
        httpx_mock.add_response(
            url=re.compile(r".*/rest/2\.0/pcs/superfile2"),
            method="POST",
            json={"errno": 0},
        )
        # commit (create) mock
        httpx_mock.add_response(
            url=re.compile(r".*/api/create\?bdstoken.*"),
            method="POST",
            json={"errno": 0, "info": {"fs_id": 88888}},
        )

        account = self._mk_account()
        driver = BaiduDriver(account=account)
        file_id = await driver.upload_file(str(test_file), "/backup")
        assert file_id == "88888"

    @pytest.mark.asyncio
    async def test_web_upload_rapid(self, httpx_mock, tmp_path):
        """Cookie-mode upload with rapid_upload (dedup)."""
        test_file = tmp_path / "rapid.txt"
        test_file.write_text("Already exists")

        self._mock_bdstoken(httpx_mock)
        self._mock_mkdir_chain(httpx_mock, folder_exists=True)

        httpx_mock.add_response(
            url=re.compile(r".*/api/precreate\?.*"),
            method="POST",
            json={
                "errno": 0,
                "rapid_upload": 1,
                "info": {"fs_id": 99999},
            },
        )

        account = self._mk_account()
        driver = BaiduDriver(account=account)
        file_id = await driver.upload_file(str(test_file), "/backup")
        assert file_id == "99999"

    @pytest.mark.asyncio
    async def test_web_mkdir(self, httpx_mock):
        """Cookie-mode mkdir via /api/create?a=commit."""
        self._mock_bdstoken(httpx_mock)
        self._mock_mkdir_chain(httpx_mock, folder_exists=False)

        account = self._mk_account()
        driver = BaiduDriver(account=account)
        result = await driver.create_folder("/PanUpdate_backup")
        assert result is True

    @pytest.mark.asyncio
    async def test_web_mkdir_already_exists(self, httpx_mock):
        """Cookie-mode mkdir when folder already exists (found via filemetas)."""
        self._mock_bdstoken(httpx_mock)
        self._mock_mkdir_chain(httpx_mock, folder_exists=True)

        account = self._mk_account()
        driver = BaiduDriver(account=account)
        result = await driver.create_folder("/already_there")
        assert result is True

    @pytest.mark.asyncio
    async def test_web_list_files(self, httpx_mock):
        """Cookie-mode file listing via /api/list."""
        self._mock_bdstoken(httpx_mock)

        httpx_mock.add_response(
            url=re.compile(r".*/api/list\?.*"),
            method="GET",
            json={
                "list": [
                    {"path": "/a.txt", "server_filename": "a.txt",
                     "isdir": 0, "size": 100, "fs_id": 1},
                ]
            },
        )

        account = self._mk_account()
        driver = BaiduDriver(account=account)
        items = await driver.list_files("/")
        assert len(items) == 1
        assert items[0].name == "a.txt"

    @pytest.mark.asyncio
    async def test_web_connection_ok(self, httpx_mock):
        """Cookie-mode test_connection."""
        httpx_mock.add_response(
            url="https://pan.baidu.com/api/quota",
            method="GET",
            json={"total": 100, "used": 10},
        )

        account = self._mk_account()
        driver = BaiduDriver(account=account)
        assert await driver.test_connection() is True

    @pytest.mark.asyncio
    async def test_web_upload_without_login_raises(self):
        """Cookie mode without login should raise."""
        driver = BaiduDriver()
        with pytest.raises(ValueError, match="Not logged in"):
            await driver.upload_file("x.txt", "/")
