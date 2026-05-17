r"""Diagnostic logger for upload pipeline — writes to %TEMP%\panupdate_upload.log."""

import os
import sys
import time
import traceback
from datetime import datetime


_UPLOAD_LOG_PATH = os.path.join(
    os.environ.get("TEMP", os.path.join(os.path.expanduser("~"), "AppData", "Local", "Temp")),
    "panupdate_upload.log",
)


def _log_path() -> str:
    return _UPLOAD_LOG_PATH


def _write_line(line: str) -> None:
    try:
        with open(_UPLOAD_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass  # Never let logging crash the app


def log_upload_event(message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _write_line(f"[{ts}] {message}")


def log_job_start(job_id: str, source_count: int, dest_count: int, task_count: int) -> None:
    _write_line("")
    _write_line(f"{'='*60}")
    _write_line(f"===== 备份开始 (job: {job_id}) =====")
    _write_line(f"===== 源路径: {source_count}  目标: {dest_count}  任务: {task_count} =====")
    _write_line(f"{'='*60}")


def log_task_start(idx: int, total: int, file_name: str, file_size: int, provider: str, remote_dir: str) -> None:
    log_upload_event(
        f"任务 {idx}/{total}: {file_name} ({_fmt_size(file_size)}) -> {provider} ({remote_dir})"
    )


def log_task_result(idx: int, success: bool, error: str = "") -> None:
    if success:
        log_upload_event(f"  ✓ 任务 {idx} 成功")
    else:
        log_upload_event(f"  ✗ 任务 {idx} 失败: {error}")


def log_driver_created(provider: str, success: bool, detail: str = "") -> None:
    status = "OK" if success else "失败"
    extra = f" — {detail}" if detail else ""
    log_upload_event(f"  驱动创建: {provider} → {status}{extra}")


def log_api_call(provider: str, method: str, url: str, status_code: int | None = None, error: str = "") -> None:
    if status_code:
        log_upload_event(f"  API {method} {url} → HTTP {status_code}")
    if error:
        log_upload_event(f"  API 错误: {error}")


def log_exception(context: str, exc: Exception) -> None:
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    tb_str = "".join(tb).rstrip()
    log_upload_event(f"  异常 [{context}]: {tb_str}")


def log_job_end(job_id: str, success: int, fail: int) -> None:
    _write_line(f"{'='*60}")
    _write_line(f"===== 备份完成 (job: {job_id}): 成功 {success}, 失败 {fail} =====")
    _write_line(f"{'='*60}")
    _write_line("")


def _fmt_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    else:
        return f"{size / 1024 / 1024 / 1024:.1f} GB"
