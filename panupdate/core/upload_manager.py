"""Concurrent upload manager with progress tracking and cancellation."""

import asyncio
from dataclasses import dataclass, field
from collections.abc import Callable

from panupdate.drivers.base import CloudDriver
from panupdate.utils.upload_logger import log_exception


@dataclass
class UploadResult:
    """Result of a single file upload attempt."""
    task_id: str
    success: bool
    file_id: str = ""
    error_message: str = ""


class UploadManager:
    """Manages concurrent uploads with a configurable concurrency limit."""

    def __init__(self, max_concurrent: int = 3):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()

    async def upload(
        self,
        task_id: str,
        driver: CloudDriver,
        local_path: str,
        remote_dir: str,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> UploadResult:
        """Upload a single file, respecting concurrency limits.

        Args:
            task_id: Unique identifier for this upload task.
            driver: CloudDriver instance to use for upload.
            local_path: Path to the local file.
            remote_dir: Remote directory to upload to.
            progress_callback: Called with (task_id, progress 0.0-1.0).

        Returns:
            UploadResult indicating success or failure.
        """
        if task_id in self._cancelled:
            return UploadResult(task_id=task_id, success=False, error_message="Cancelled")

        async def _do_upload() -> UploadResult:
            async with self._semaphore:
                if task_id in self._cancelled:
                    return UploadResult(task_id=task_id, success=False, error_message="Cancelled")

                try:
                    # Progress callback at start
                    if progress_callback:
                        progress_callback(task_id, 0.0)

                    file_id = await driver.upload_file(
                        local_path,
                        remote_dir,
                        progress_callback=lambda b, t: self._on_progress(
                            task_id, b, t, progress_callback
                        ),
                    )

                    # Progress callback at completion
                    if progress_callback:
                        progress_callback(task_id, 1.0)

                    return UploadResult(task_id=task_id, success=True, file_id=file_id)

                except asyncio.CancelledError:
                    return UploadResult(task_id=task_id, success=False, error_message="Cancelled")
                except Exception as e:
                    log_exception(f"upload {local_path}", e)
                    return UploadResult(task_id=task_id, success=False, error_message=str(e))

        task = asyncio.create_task(_do_upload())
        self._active_tasks[task_id] = task

        try:
            result = await task
            return result
        finally:
            self._active_tasks.pop(task_id, None)

    def _on_progress(
        self,
        task_id: str,
        bytes_done: int,
        bytes_total: int,
        callback: Callable[[str, float], None] | None,
    ) -> None:
        if callback and bytes_total > 0:
            callback(task_id, bytes_done / bytes_total)

    async def cancel(self, task_id: str) -> bool:
        """Cancel a specific upload task. Returns True if task existed."""
        self._cancelled.add(task_id)
        task = self._active_tasks.get(task_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    async def cancel_all(self) -> None:
        """Cancel all active upload tasks."""
        for task_id in list(self._active_tasks.keys()):
            await self.cancel(task_id)

    @property
    def active_count(self) -> int:
        return len(self._active_tasks)
