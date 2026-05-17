"""Tests for UploadManager."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from panupdate.core.upload_manager import UploadManager, UploadResult
from panupdate.drivers.base import CloudDriver, AccountInfo


class MockDriver(CloudDriver):
    """Minimal CloudDriver implementation for testing."""

    def __init__(self, should_fail: bool = False, delay: float = 0.0):
        super().__init__()
        self.should_fail = should_fail
        self.delay = delay
        self.upload_calls = []

    def get_auth_url(self) -> str:
        return "http://mock"

    async def login(self, auth_code: str) -> AccountInfo:
        return AccountInfo(provider="mock", account_name="mock")

    async def upload_file(self, local_path, remote_dir, progress_callback=None):
        self.upload_calls.append((local_path, remote_dir))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError("Upload failed")
        if progress_callback:
            progress_callback(100, 100)
        return "file_123"

    async def create_folder(self, remote_path):
        return True

    async def list_files(self, remote_dir):
        return []

    async def get_quota(self):
        from panupdate.drivers.base import QuotaInfo
        return QuotaInfo()

    async def refresh_token(self):
        return ""

    async def test_connection(self):
        return True


class TestUploadManager:
    @pytest.mark.asyncio
    async def test_upload_success(self):
        mgr = UploadManager(max_concurrent=3)
        driver = MockDriver()
        result = await mgr.upload("task1", driver, "/local/file.txt", "/remote")
        assert result.success is True
        assert result.file_id == "file_123"
        assert result.task_id == "task1"

    @pytest.mark.asyncio
    async def test_upload_failure(self):
        mgr = UploadManager()
        driver = MockDriver(should_fail=True)
        result = await mgr.upload("task2", driver, "/local/file.txt", "/remote")
        assert result.success is False
        assert "Upload failed" in result.error_message

    @pytest.mark.asyncio
    async def test_progress_callback(self):
        mgr = UploadManager()
        driver = MockDriver()
        progress_log = []

        def on_progress(tid: str, pct: float):
            progress_log.append((tid, pct))

        await mgr.upload("task3", driver, "/local/f.txt", "/remote", on_progress)
        # Should receive 0.0 at start and 1.0 at end (and anything in between)
        assert len(progress_log) >= 2
        assert progress_log[0] == ("task3", 0.0)
        assert progress_log[-1] == ("task3", 1.0)

    @pytest.mark.asyncio
    async def test_concurrency_limit(self):
        """Verify semaphore limits concurrent uploads."""
        mgr = UploadManager(max_concurrent=2)
        drivers = [MockDriver(delay=0.3) for _ in range(4)]

        progress = {"active": 0, "max_active": 0}

        def make_callback(i):
            def on_progress(tid: str, pct: float):
                if pct == 0.0:
                    progress["active"] += 1
                    progress["max_active"] = max(
                        progress["max_active"], progress["active"]
                    )
                elif pct == 1.0:
                    progress["active"] -= 1
            return on_progress

        async def tracked_upload(driver, i):
            await mgr.upload(
                f"task_{i}", driver, f"/local/f{i}.txt", "/remote",
                progress_callback=make_callback(i),
            )

        await asyncio.gather(*[tracked_upload(d, i) for i, d in enumerate(drivers)])
        # Max concurrent should not exceed 2
        assert progress["max_active"] <= 2

    @pytest.mark.asyncio
    async def test_cancel_single_task(self):
        mgr = UploadManager()
        driver = MockDriver(delay=0.5)

        async def slow_upload():
            return await mgr.upload("cancel_me", driver, "/local/f.txt", "/remote")

        task = asyncio.create_task(slow_upload())
        await asyncio.sleep(0.05)  # Let upload start

        cancelled = await mgr.cancel("cancel_me")
        assert cancelled is True

        result = await task
        assert result.success is False

    @pytest.mark.asyncio
    async def test_cancel_all(self):
        mgr = UploadManager(max_concurrent=5)
        drivers = [MockDriver(delay=0.5) for _ in range(3)]

        tasks = []
        for i, d in enumerate(drivers):
            tasks.append(asyncio.create_task(
                mgr.upload(f"c{i}", d, f"/local/f{i}.txt", "/remote")
            ))

        await asyncio.sleep(0.05)
        await mgr.cancel_all()

        results = await asyncio.gather(*tasks, return_exceptions=True)
        # All should be cancelled (failures)
        for r in results:
            assert isinstance(r, UploadResult)
            assert r.success is False

    @pytest.mark.asyncio
    async def test_active_count(self):
        mgr = UploadManager(max_concurrent=5)
        driver = MockDriver(delay=0.3)
        assert mgr.active_count == 0

        async def upload_delayed():
            return await mgr.upload("ac1", driver, "/local/f.txt", "/remote")

        task = asyncio.create_task(upload_delayed())
        await asyncio.sleep(0.05)
        assert mgr.active_count == 1
        await task
        assert mgr.active_count == 0
