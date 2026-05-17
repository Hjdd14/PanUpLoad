"""Tests for 夸克云盘 driver (mocked HTTP)."""

import re

import pytest
from panupdate.drivers.kuaike import KuaikeDriver
from panupdate.drivers.base import AccountInfo


class TestKuaikeDriver:
    def test_get_auth_url(self):
        driver = KuaikeDriver()
        url = driver.get_auth_url()
        assert "quark.cn" in url

    @pytest.mark.asyncio
    async def test_login(self):
        driver = KuaikeDriver()
        account = await driver.login("my_quark_token")
        assert account.provider == "kuaike"
        assert account.access_token == "my_quark_token"

    @pytest.mark.asyncio
    async def test_upload_file(self, httpx_mock, tmp_path):
        test_file = tmp_path / "hello.txt"
        test_file.write_text("Hello Quark")

        # Mock: list root on pan.quark.cn (folder not found)
        httpx_mock.add_response(
            url=re.compile(r".*pan\.quark\.cn/1/clouddrive/file/sort.*"),
            method="GET", json={"data": {"list": []}},
        )
        # Mock: create folder (may be attempted if folder not found)
        httpx_mock.add_response(
            url=re.compile(r".*drive-pc\.quark\.cn/1/clouddrive/file\?.*"),
            method="POST", json={"data": {"file_id": "folder_1"}},
        )
        # Mock: upload/pre
        httpx_mock.add_response(
            url=re.compile(r".*drive-pc\.quark\.cn/1/clouddrive/file/upload/pre.*"),
            method="POST",
            json={"status": 200, "data": {"fid": "file_quark", "bucket": "ul-sz", "obj_key": "path/to/file", "task_id": "t1", "auth_info": "auth123", "upload_id": "u1", "callback": {"cb": "1"}}},
        )
        # Mock: update/hash
        httpx_mock.add_response(
            url=re.compile(r".*drive-pc\.quark\.cn/1/clouddrive/file/update/hash.*"),
            method="POST", json={"status": 200},
        )
        # Mock: upload/auth (reusable)
        httpx_mock.add_response(
            url=re.compile(r".*drive-pc\.quark\.cn/1/clouddrive/file/upload/auth.*"),
            method="POST", json={"status": 200, "data": {"auth_key": "key123"}},
            is_reusable=True,
        )
        # Mock: PUT to OSS
        httpx_mock.add_response(
            url=re.compile(r".*oss-cn-shenzhen.aliyuncs.com.*"),
            method="PUT", headers={"etag": '"abc"'},
        )
        # Mock: POST to OSS
        httpx_mock.add_response(
            url=re.compile(r".*oss-cn-shenzhen.aliyuncs.com.*"),
            method="POST",
        )
        # Mock: upload/finish
        httpx_mock.add_response(
            url=re.compile(r".*drive-pc\.quark\.cn/1/clouddrive/file/upload/finish.*"),
            method="POST", json={"status": 200},
        )

        account = AccountInfo(
            provider="kuaike", account_name="test",
            access_token="tok", refresh_token="", expires_at=9999999999,
        )
        driver = KuaikeDriver(account=account)
        file_id = await driver.upload_file(str(test_file), "/backup")
        assert file_id == "file_quark"

    @pytest.mark.asyncio
    async def test_list_files(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(r".*pan\.quark\.cn/1/clouddrive/file/sort.*"),
            method="GET",
            json={"data": {"list": [
                {"file_name": "doc.txt", "dir": False, "size": 400, "file_type": 1, "file_id": "f1"},
                {"file_name": "sub", "dir": True, "size": 0, "file_type": 0, "file_id": "f2"},
            ]}},
        )
        account = AccountInfo(
            provider="kuaike", account_name="test",
            access_token="tok", refresh_token="", expires_at=9999999999,
        )
        driver = KuaikeDriver(account=account)
        items = await driver.list_files("/")
        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_create_folder(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(r".*pan\.quark\.cn/1/clouddrive/file/sort.*"),
            method="GET", json={"data": {"list": [{"file_name": "new_folder", "dir": True, "file_type": 0, "file_id": "existing_f"}]}},
        )
        account = AccountInfo(
            provider="kuaike", account_name="test",
            access_token="tok", refresh_token="", expires_at=9999999999,
        )
        driver = KuaikeDriver(account=account)
        assert await driver.create_folder("/new_folder") is True

    @pytest.mark.asyncio
    async def test_test_connection_ok(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(r".*pan\.quark\.cn/1/clouddrive/file/sort.*"),
            method="GET", json={"data": {"list": []}},
        )
        account = AccountInfo(
            provider="kuaike", account_name="test",
            access_token="tok", refresh_token="", expires_at=9999999999,
        )
        driver = KuaikeDriver(account=account)
        assert await driver.test_connection() is True

    @pytest.mark.asyncio
    async def test_upload_without_login_raises(self):
        driver = KuaikeDriver()
        with pytest.raises(ValueError, match="Not logged in"):
            await driver.upload_file("x.txt", "/")
