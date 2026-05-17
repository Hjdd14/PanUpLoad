"""Backup job orchestration engine."""

import uuid
import time
import asyncio
from dataclasses import dataclass, field
from collections.abc import Callable

from panupdate.core.file_scanner import FileScanner, FileInfo
from panupdate.core.upload_manager import UploadManager, UploadResult
from panupdate.drivers.base import CloudDriver, AccountInfo
from panupdate.storage.db import Database
from panupdate.storage.crypto import CryptoManager
from panupdate.utils.upload_logger import log_job_start, log_job_end, log_task_start, log_task_result


@dataclass
class BackupDestination:
    """A target cloud drive and folder for backup."""
    provider: str
    account_name: str
    account_id: int
    remote_dir: str


@dataclass
class UploadTaskInfo:
    """A single file-to-destination upload task within a job."""
    source_path: str
    file_name: str
    file_size: int
    provider: str
    remote_dir: str
    account_id: int = 0  # distinguishes multiple accounts of the same provider
    status: str = "pending"  # pending | running | success | failed | cancelled
    error: str = ""
    file_id: str = ""
    progress: float = 0.0
    db_task_id: int = 0


@dataclass
class BackupJob:
    """A backup operation: one or more source files to one or more destinations."""
    id: str
    source_paths: list[str]
    destinations: list[BackupDestination]
    status: str = "pending"  # pending | running | completed | failed | cancelled
    total_tasks: int = 0
    completed_tasks: int = 0
    success_count: int = 0
    fail_count: int = 0
    created_at: float = 0.0
    completed_at: float | None = None
    tasks: list[UploadTaskInfo] = field(default_factory=list)


class BackupEngine:
    """Orchestrates backup jobs from source selection to completion."""

    def __init__(
        self,
        upload_manager: UploadManager | None = None,
        file_scanner: FileScanner | None = None,
        db: Database | None = None,
    ):
        self._upload_manager = upload_manager or UploadManager()
        self._file_scanner = file_scanner or FileScanner()
        self._db = db
        self._jobs: dict[str, BackupJob] = {}
        self._progress_callbacks: dict[str, Callable[[str, float], None]] = {}

    def create_job(
        self,
        source_paths: list[str],
        destinations: list[BackupDestination],
    ) -> BackupJob:
        """Create a backup job (does NOT start it)."""
        job_id = uuid.uuid4().hex[:12]
        now = time.time()

        job = BackupJob(
            id=job_id,
            source_paths=source_paths,
            destinations=destinations,
            created_at=now,
        )
        self._jobs[job_id] = job
        if self._db:
            self._db.save_job(job_id, source_paths)
        return job

    def _build_tasks(self, job: BackupJob) -> list[UploadTaskInfo]:
        """Scan source paths and create one UploadTaskInfo per file per destination."""
        from pathlib import Path as _Path
        all_files = self._file_scanner.scan_paths(job.source_paths)
        tasks: list[UploadTaskInfo] = []

        for fi in all_files:
            for dest in job.destinations:
                remote_dir = dest.remote_dir
                if fi.relative_path:
                    # Preserve folder structure: compute base folder name
                    # from the file path and relative_path.
                    # e.g. path=D:/Homework/MM/FM/file.pdf, rel=FM/file.pdf
                    #   → base=MM, subdir=MM/FM
                    full_parts = _Path(fi.path).parts
                    rel_parts = _Path(fi.relative_path).parts
                    if len(full_parts) > len(rel_parts):
                        base_name = full_parts[-len(rel_parts) - 1]
                        rel_dir = _Path(fi.relative_path).parent
                        if rel_dir.parts and str(rel_dir) != ".":
                            remote_dir = f"{dest.remote_dir.rstrip('/')}/{base_name}/{rel_dir.as_posix()}"
                        else:
                            remote_dir = f"{dest.remote_dir.rstrip('/')}/{base_name}"
                tasks.append(UploadTaskInfo(
                    source_path=fi.path,
                    file_name=fi.name,
                    file_size=fi.size,
                    provider=dest.provider,
                    remote_dir=remote_dir,
                    account_id=dest.account_id,
                ))

        return tasks

    async def start_job(
        self,
        job_id: str,
        drivers: dict[int, CloudDriver],
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> BackupJob:
        """Start a backup job by dispatching all upload tasks.

        Args:
            job_id: ID from create_job().
            drivers: Mapping of account_id -> CloudDriver instance.
            progress_callback: Called with (job_id, overall_progress 0.0-1.0).

        Returns:
            The completed BackupJob with task results.
        """
        job = self._jobs.get(job_id)
        if not job:
            raise ValueError(f"Job not found: {job_id}")

        job.status = "running"
        job.tasks = self._build_tasks(job)
        job.total_tasks = len(job.tasks)

        log_job_start(job_id, len(job.source_paths), len(job.destinations), job.total_tasks)

        if self._db:
            self._db.update_job(job_id, total_tasks=job.total_tasks)
            for task_info in job.tasks:
                task_info.db_task_id = self._db.save_task(
                    job_id, task_info.source_path, task_info.file_name,
                    task_info.file_size, task_info.provider, task_info.remote_dir,
                )

        if not job.tasks:
            job.status = "completed"
            job.completed_at = time.time()
            if self._db:
                self._db.update_job(job_id, status="completed", completed_at=job.completed_at)
            return job

        if progress_callback:
            self._progress_callbacks[job_id] = progress_callback

        # Create and await all upload tasks
        upload_coros = []
        for task_info in job.tasks:
            # Find the driver for this task's destination
            dest = self._find_destination(job, task_info.account_id)
            if not dest or dest.account_id not in drivers:
                task_info.status = "failed"
                task_info.error = f"No driver for {task_info.provider}"
                job.fail_count += 1
                log_task_result(job.fail_count, False, task_info.error)
                if self._db and task_info.db_task_id:
                    self._db.update_task_status(
                        task_info.db_task_id, "failed",
                        error=task_info.error,
                    )
                continue

            driver = drivers[dest.account_id]

            task_idx = job.completed_tasks + job.success_count + job.fail_count + 1
            log_task_start(task_idx, job.total_tasks, task_info.file_name,
                           task_info.file_size, task_info.provider, task_info.remote_dir)

            async def _do_upload(ti=task_info, drv=driver):
                ti.status = "running"
                result = await self._upload_manager.upload(
                    task_id=f"{job_id}_{ti.source_path}_{ti.provider}",
                    driver=drv,
                    local_path=ti.source_path,
                    remote_dir=ti.remote_dir,
                    progress_callback=lambda tid, p: self._on_task_progress(job_id, p),
                )
                ti.status = "success" if result.success else "failed"
                ti.file_id = result.file_id
                ti.error = result.error_message
                if result.success:
                    job.success_count += 1
                else:
                    job.fail_count += 1
                job.completed_tasks += 1
                log_task_result(task_idx, result.success, result.error_message)
                self._notify_progress(job_id)
                if self._db and ti.db_task_id:
                    self._db.update_task_status(
                        ti.db_task_id, ti.status,
                        progress=1.0 if ti.status == "success" else 0.0,
                        error=ti.error, file_id=ti.file_id,
                    )

            upload_coros.append(_do_upload())

        await asyncio.gather(*upload_coros, return_exceptions=True)

        job.status = "completed"
        job.completed_at = time.time()

        log_job_end(job_id, job.success_count, job.fail_count)

        if progress_callback:
            progress_callback(job_id, 1.0)

        if self._db:
            self._db.update_job(
                job_id,
                status=job.status,
                completed_tasks=job.completed_tasks,
                success_count=job.success_count,
                fail_count=job.fail_count,
                completed_at=job.completed_at,
            )

        return job

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a running backup job."""
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.status = "cancelled"
        job.completed_at = time.time()
        if self._db:
            self._db.update_job(job_id, status="cancelled", completed_at=job.completed_at)
        return True

    def get_job(self, job_id: str) -> BackupJob | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[BackupJob]:
        return list(self._jobs.values())

    def get_progress(self, job_id: str) -> float:
        """Get overall progress for a job (0.0-1.0)."""
        job = self._jobs.get(job_id)
        if not job or job.total_tasks == 0:
            return 0.0
        return job.completed_tasks / job.total_tasks

    def _find_destination(self, job: BackupJob, account_id: int) -> BackupDestination | None:
        for d in job.destinations:
            if d.account_id == account_id:
                return d
        return None

    def _on_task_progress(self, job_id: str, task_progress: float) -> None:
        """Called by UploadManager when a single task reports progress."""
        self._notify_progress(job_id)

    def _notify_progress(self, job_id: str) -> None:
        """Notify the progress callback with overall job progress."""
        callback = self._progress_callbacks.get(job_id)
        if callback:
            callback(job_id, self.get_progress(job_id))
