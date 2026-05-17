"""Tests for base driver types and interface contract."""

import pytest
from panupdate.drivers.base import (
    CloudDriver, FileItem, QuotaInfo, AccountInfo,
)
from datetime import datetime


class TestDataClasses:
    """Verify data class creation and defaults."""

    def test_file_item_defaults(self):
        item = FileItem(path="/test", name="file.txt")
        assert item.path == "/test"
        assert item.name == "file.txt"
        assert item.is_dir is False
        assert item.size == 0
        assert item.file_id == ""

    def test_file_item_full(self):
        now = datetime.now()
        item = FileItem(
            path="/test", name="dir", is_dir=True, size=1024,
            created_at=now, updated_at=now, file_id="abc123",
        )
        assert item.is_dir is True
        assert item.file_id == "abc123"

    def test_quota_info_remaining(self):
        q = QuotaInfo(total=100, used=30)
        assert q.remaining == 70

    def test_account_info_defaults(self):
        a = AccountInfo(provider="baidu", account_name="my_baidu")
        assert a.access_token == ""
        assert a.extra == {}

    def test_account_info_extra(self):
        a = AccountInfo(
            provider="baidu", account_name="test",
            access_token="tok1", extra={"uk": 12345},
        )
        assert a.extra["uk"] == 12345


class TestCloudDriverContract:
    """Verify that CloudDriver ABC enforces the interface."""

    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            CloudDriver()  # type: ignore

    def test_subclass_must_implement_all_methods(self):
        class Incomplete(CloudDriver):
            pass
        with pytest.raises(TypeError):
            Incomplete()  # type: ignore

    def test_account_property(self):
        """Subclass with account should set/get correctly."""
        acc = AccountInfo(provider="baidu", account_name="test")

        class Concrete(CloudDriver):
            async def login(self, auth_code): ...
            async def get_auth_url(self): ...
            async def upload_file(self, local_path, remote_dir, progress_callback=None): ...
            async def create_folder(self, remote_path): ...
            async def list_files(self, remote_dir): ...
            async def get_quota(self): ...
            async def refresh_token(self): ...
            async def test_connection(self): ...

        driver = Concrete(account=acc)
        assert driver.account is acc
        assert driver.account.provider == "baidu"

        new_acc = AccountInfo(provider="kuaike", account_name="test2")
        driver.account = new_acc
        assert driver.account.provider == "kuaike"
