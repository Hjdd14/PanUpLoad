"""Tests for BackupEngine."""

import pytest
import asyncio
import tempfile
from pathlib import Path

from panupdate.core.backup_engine import (
    BackupEngine, BackupDestination, BackupJob, UploadTaskInfo,
)
from panupdate.core.upload_manager import UploadManager
from panupdate.drivers.base import CloudDriver, AccountInfo
from panupdate.storage.db import Database


class MockDriver(CloudDriver):
    """Simple mock driver for testing."""
    def __init__(self, should_fail: bool = False, delay: float = 0.0):
        super().__init__()
        self.should_fail = should_fail
        self.delay = delay

    def get_auth_url(self): return "http://mock"
    async def login(self, code): return AccountInfo(provider="mock", account_name="m")

    async def upload_file(self, local_path, remote_dir, progress_callback=None):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError("Upload failed")
        if progress_callback:
            progress_callback(100, 100)
        return "file_mock"

    async def create_folder(self, remote_path): return True
    async def list_files(self, remote_dir): return []
    async def get_quota(self):
        from panupdate.drivers.base import QuotaInfo
        return QuotaInfo()
    async def refresh_token(self): return ""
    async def test_connection(self): return True


class TestBackupEngine:
    @pytest.fixture
    def engine(self):
        return BackupEngine()

    @pytest.fixture
    def temp_files(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "f1.txt").write_text("file one")
            (p / "f2.txt").write_text("file two")
            yield p

    def test_create_job(self, engine, temp_files):
        dests = [BackupDestination("baidu", "my_baidu", 1, "/backup")]
        job = engine.create_job(
            source_paths=[str(temp_files / "f1.txt")],
            destinations=dests,
        )
        assert job.id is not None
        assert job.status == "pending"
        assert job.total_tasks == 0

    def test_list_jobs(self, engine, temp_files):
        dests = [BackupDestination("baidu", "my_baidu", 1, "/backup")]
        engine.create_job([str(temp_files / "f1.txt")], dests)
        engine.create_job([str(temp_files / "f2.txt")], dests)
        assert len(engine.list_jobs()) == 2

    @pytest.mark.asyncio
    async def test_start_job_success(self, engine, temp_files):
        dests = [BackupDestination("baidu", "my_baidu", 1, "/backup")]
        job = engine.create_job(
            source_paths=[str(temp_files / "f1.txt"), str(temp_files / "f2.txt")],
            destinations=dests,
        )
        drivers = {1: MockDriver()}
        result = await engine.start_job(job.id, drivers)
        assert result.status == "completed"
        assert result.success_count == 2
        assert result.fail_count == 0
        assert result.completed_tasks == 2

    @pytest.mark.asyncio
    async def test_start_job_partial_failure(self, engine, temp_files):
        """One file succeeds, one fails."""
        dests = [BackupDestination("baidu", "my_baidu", 1, "/backup")]
        dests2 = [BackupDestination("kuaike", "my_kuaike", 2, "/backup")]
        job = engine.create_job(
            source_paths=[str(temp_files / "f1.txt")],
            destinations=[dests[0], dests2[0]],
        )
        drivers = {1: MockDriver(), 2: MockDriver(should_fail=True)}
        result = await engine.start_job(job.id, drivers)
        assert result.completed_tasks == 2
        assert result.success_count == 1
        assert result.fail_count == 1

    @pytest.mark.asyncio
    async def test_start_job_no_driver(self, engine, temp_files):
        """Destination has no matching driver in the drivers dict."""
        dests = [BackupDestination("baidu", "my_baidu", 1, "/backup")]
        job = engine.create_job(
            source_paths=[str(temp_files / "f1.txt")],
            destinations=dests,
        )
        # No driver for account_id=1
        result = await engine.start_job(job.id, {})
        assert result.status == "completed"
        assert result.fail_count == 1

    @pytest.mark.asyncio
    async def test_progress_callback(self, engine, temp_files):
        dests = [BackupDestination("baidu", "my_baidu", 1, "/backup")]
        job = engine.create_job(
            source_paths=[str(temp_files / "f1.txt")],
            destinations=dests,
        )
        progress_log = []

        def on_progress(jid: str, pct: float):
            progress_log.append(pct)

        await engine.start_job(job.id, {1: MockDriver()}, on_progress)
        assert len(progress_log) >= 2
        assert progress_log[-1] == 1.0

    def test_cancel_job(self, engine, temp_files):
        dests = [BackupDestination("baidu", "my_baidu", 1, "/backup")]
        job = engine.create_job([str(temp_files / "f1.txt")], dests)
        assert engine.cancel_job(job.id) is True
        assert engine.get_job(job.id).status == "cancelled"
        assert engine.cancel_job("nonexistent") is False

    def test_get_progress(self, engine, temp_files):
        dests = [BackupDestination("baidu", "my_baidu", 1, "/backup")]
        job = engine.create_job([str(temp_files / "f1.txt")], dests)
        assert engine.get_progress(job.id) == 0.0  # no tasks yet
        assert engine.get_progress("nonexistent") == 0.0

    def test_backup_job_dataclass(self):
        job = BackupJob(id="j1", source_paths=[], destinations=[])
        assert job.status == "pending"
        assert job.total_tasks == 0
        assert job.tasks == []

    def test_upload_task_info_dataclass(self):
        t = UploadTaskInfo(
            source_path="/a.txt", file_name="a.txt",
            file_size=100, provider="baidu", remote_dir="/backup",
        )
        assert t.status == "pending"
        assert t.progress == 0.0

    @pytest.mark.asyncio
    async def test_db_persistence_integration(self, temp_files):
        """Verifies BackupEngine persists jobs when db is passed."""
        data_dir = tempfile.mkdtemp()
        db = Database(data_dir)
        db.initialize()

        engine = BackupEngine(db=db)
        dests = [BackupDestination("baidu", "my_baidu", 1, "/backup")]
        job = engine.create_job(
            source_paths=[str(temp_files / "f1.txt")],
            destinations=dests,
        )

        # 1. Job is persisted in DB
        db_job = db.get_job(job.id)
        assert db_job is not None
        assert db_job["source_paths"] == [str(temp_files / "f1.txt")]
        assert db_job["status"] == "pending"

        # 2. Start job, tasks are persisted
        result = await engine.start_job(job.id, {1: MockDriver()})
        assert result.status == "completed"

        tasks = db.get_tasks_for_job(job.id)
        assert len(tasks) == 1
        assert tasks[0]["status"] == "success"
        assert tasks[0]["file_name"] == "f1.txt"

        # 3. Job status is updated in DB
        db_job = db.get_job(job.id)
        assert db_job["status"] == "completed"
        assert db_job["success_count"] == 1
        assert db_job["completed_at"] is not None
