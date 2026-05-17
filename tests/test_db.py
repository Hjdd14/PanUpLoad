"""Tests for the SQLite database module."""

import pytest
import tempfile
from panupdate.storage.db import Database
from panupdate.drivers.base import AccountInfo


class FakeEncryptor:
    """Reversible fake encryptor for testing (identity with prefix)."""
    def encrypt(self, s: str) -> str:
        return f"enc:{s}"
    def __call__(self, s: str) -> str:
        return self.encrypt(s)


def decrypt(s: str) -> str:
    return s[4:] if s.startswith("enc:") else s


@pytest.fixture
def db():
    data_dir = tempfile.mkdtemp()
    d = Database(data_dir)
    d.initialize()
    yield d


class TestDatabase:
    def test_initialize_creates_tables(self, db):
        db.initialize()  # idempotent
        assert db.count_accounts() == 0

    def test_save_and_list_accounts(self, db):
        enc = FakeEncryptor()
        info = AccountInfo(
            provider="baidu", account_name="my_pan",
            access_token="tok123", refresh_token="ref456",
            expires_at=9999999999.0,
        )
        db.save_account(info, enc.encrypt)
        accounts = db.list_accounts()
        assert len(accounts) == 1
        assert accounts[0]["provider"] == "baidu"
        assert accounts[0]["account_name"] == "my_pan"

    def test_save_twice_updates(self, db):
        enc = FakeEncryptor()
        info = AccountInfo(
            provider="baidu", account_name="my_pan",
            access_token="first", refresh_token="",
        )
        db.save_account(info, enc.encrypt)
        # Same token → re-login, updates
        db.save_account(info, enc.encrypt)
        assert db.count_accounts() == 1
        # Different token → new account with suffix
        info2 = AccountInfo(
            provider="baidu", account_name="my_pan",
            access_token="second", refresh_token="",
        )
        db.save_account(info2, enc.encrypt)
        assert db.count_accounts() == 2

    def test_get_account(self, db):
        enc = FakeEncryptor()
        info = AccountInfo(
            provider="kuaike", account_name="my_kuaike",
            access_token="atok", refresh_token="rftok",
        )
        aid = db.save_account(info, enc.encrypt)
        row = db.get_account(aid)
        assert row is not None
        assert row["provider"] == "kuaike"
        assert decrypt(row["access_token_enc"]) == "atok"

    def test_delete_account(self, db):
        enc = FakeEncryptor()
        aid = db.save_account(
            AccountInfo(provider="test", account_name="del"), enc.encrypt,
        )
        assert db.delete_account(aid) is True
        assert db.count_accounts() == 0

    def test_config_roundtrip(self, db):
        db.set_config("theme", "dark")
        assert db.get_config("theme") == "dark"
        assert db.get_config("nonexistent", 42) == 42

    # --- Job / Task CRUD tests ---

    def test_save_and_get_job(self, db):
        db.save_job("job1", ["/a.txt", "/b.txt"])
        job = db.get_job("job1")
        assert job is not None
        assert job["source_paths"] == ["/a.txt", "/b.txt"]
        assert job["status"] == "pending"

    def test_update_job(self, db):
        db.save_job("job1", ["/a.txt"])
        db.update_job("job1", status="completed", success_count=1)
        job = db.get_job("job1")
        assert job["status"] == "completed"
        assert job["success_count"] == 1

    def test_list_jobs(self, db):
        db.save_job("job1", ["/a.txt"])
        db.save_job("job2", ["/b.txt"])
        jobs = db.list_jobs()
        assert len(jobs) == 2

    def test_save_and_get_tasks(self, db):
        db.save_job("job1", ["/a.txt"])
        tid = db.save_task("job1", "/a.txt", "a.txt", 100, "baidu", "/backup")
        assert tid is not None
        tasks = db.get_tasks_for_job("job1")
        assert len(tasks) == 1
        assert tasks[0]["file_name"] == "a.txt"

    def test_update_task_status(self, db):
        db.save_job("job1", ["/a.txt"])
        tid = db.save_task("job1", "/a.txt", "a.txt", 100, "baidu", "/backup")
        db.update_task_status(tid, "success", progress=1.0, file_id="fid123")
        tasks = db.get_tasks_for_job("job1")
        assert tasks[0]["status"] == "success"
        assert tasks[0]["progress"] == 1.0
        assert tasks[0]["file_id"] == "fid123"
