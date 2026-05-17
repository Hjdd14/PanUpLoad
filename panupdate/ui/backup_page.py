"""Backup task page — file selection, target selection, progress display."""

import flet as ft
import json
from pathlib import Path

from panupdate.drivers.base import AccountInfo
from panupdate.storage.db import Database
from panupdate.storage.crypto import CryptoManager
from panupdate.core.backup_engine import BackupEngine, BackupDestination
from panupdate.core.upload_manager import UploadManager
from panupdate.core.file_scanner import FileScanner
from panupdate.utils.upload_logger import log_upload_event, log_driver_created


class BackupPage(ft.Container):
    """Page for creating and monitoring backup jobs."""

    def __init__(self, db: Database, crypto: CryptoManager):
        super().__init__()
        self.db = db
        self.crypto = crypto
        self.expand = True

        # State
        self._selected_paths: list[str] = []
        max_concurrent = int(self.db.get_config("max_concurrent", 3))
        self._engine = BackupEngine(upload_manager=UploadManager(max_concurrent=max_concurrent))
        self._current_job_id: str | None = None
        self._is_running = False

        # UI References
        self._file_list_text = ft.Text("暂无选择文件", italic=True, color=ft.Colors.GREY)
        self._progress_container = ft.Column(spacing=4, visible=False)
        self._overall_progress = ft.ProgressBar(value=0, visible=False)
        self._status_text = ft.Text("")
        self._start_btn = ft.Button("开始备份", icon=ft.Icons.PLAY_ARROW, on_click=self._on_start)
        self._cancel_btn = ft.Button("取消", icon=ft.Icons.STOP, on_click=self._on_cancel, disabled=True)
        self._drive_checkboxes: dict[int, tuple[ft.Checkbox, ft.TextField]] = {}

        self._build_ui()

    def _build_ui(self):
        """Build the complete page UI."""
        self.content = ft.Column(
            controls=[
                ft.Text("备份任务", size=24, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                self._build_source_section(),
                ft.Divider(),
                self._build_destination_section(),
                ft.Divider(),
                self._build_action_bar(),
                ft.Divider(),
                self._build_progress_section(),
            ],
            scroll=ft.ScrollMode.AUTO,
            spacing=10,
        )

    def _build_source_section(self) -> ft.Container:
        return ft.Container(
            content=ft.Column([
                ft.Text("源文件", size=16, weight=ft.FontWeight.BOLD),
                ft.Row([
                    ft.Button("选择文件", icon=ft.Icons.UPLOAD_FILE, on_click=self._on_pick_files),
                    ft.Button("选择文件夹", icon=ft.Icons.FOLDER_OPEN, on_click=self._on_pick_folder),
                ]),
                ft.Container(
                    content=self._file_list_text,
                    padding=10,
                    bgcolor=ft.Colors.SURFACE_CONTAINER,
                    border_radius=8,
                    width=600,
                ),
            ]),
            padding=10,
        )

    def _build_destination_section(self) -> ft.Container:
        self._dest_column = ft.Column([
            ft.Row([
                ft.Text("目标网盘", size=16, weight=ft.FontWeight.BOLD),
                ft.IconButton(
                    icon=ft.Icons.REFRESH,
                    tooltip="刷新账号列表",
                    on_click=lambda _: self.reload_accounts(),
                ),
            ]),
        ])
        self._dest_container = ft.Container(
            content=self._dest_column,
            padding=10,
        )
        self._rebuild_account_list()
        return self._dest_container

    def _rebuild_account_list(self):
        """Rebuild the account checkbox list (keeps header)."""
        accounts = self.db.list_accounts()
        self._drive_checkboxes = {}

        # Remove all items after the header (index 0)
        while len(self._dest_column.controls) > 1:
            self._dest_column.controls.pop()

        if not accounts:
            self._dest_column.controls.append(
                ft.Text("暂无已添加的网盘账号，请先在「账号管理」页面添加", italic=True, color=ft.Colors.GREY)
            )
        else:
            for acc in accounts:
                provider_label = self._provider_label(acc["provider"])
                cb = ft.Checkbox(
                    label=f"{provider_label} ({acc['account_name']})",
                    value=False,
                )
                default_dir = self.db.get_config("default_remote_dir", "/PanUpdate_backup")
                folder_field = ft.TextField(
                    label="目标文件夹",
                    value=default_dir,
                    width=300,
                    hint_text="/PanUpdate_backup",
                )
                self._drive_checkboxes[acc["id"]] = (cb, folder_field)
                self._dest_column.controls.append(
                    ft.Container(
                        content=ft.Column([cb, folder_field]),
                        padding=ft.Padding(left=30, top=0, right=0, bottom=0),
                    )
                )

    def reload_accounts(self):
        """Public method: refresh account list (called from outside or refresh button)."""
        self._rebuild_account_list()
        self.update()

    def _build_action_bar(self) -> ft.Container:
        return ft.Container(
            content=ft.Row(
                controls=[self._start_btn, self._cancel_btn, self._status_text],
                spacing=10,
            ),
            padding=10,
        )

    def _build_progress_section(self) -> ft.Container:
        return ft.Container(
            content=ft.Column([
                ft.Text("传输进度", size=16, weight=ft.FontWeight.BOLD),
                self._overall_progress,
                self._progress_container,
            ]),
            padding=10,
        )

    # --- Event handlers ---

    def _on_pick_files(self, e):
        """Open native file picker dialog."""
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        paths = filedialog.askopenfilenames(title="选择要备份的文件", parent=root)
        root.destroy()
        if paths:
            self._selected_paths = list(paths)
            self._update_file_list()
        else:
            self._status_text.value = "未选择文件"
            self._status_text.color = ft.Colors.ORANGE
            self.update()

    def _on_pick_folder(self, e):
        """Open native folder picker dialog."""
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title="选择要备份的文件夹", parent=root)
        root.destroy()
        if path:
            self._selected_paths = [path]
            self._update_file_list()

    def _update_file_list(self):
        """Update the file list display."""
        if not self._selected_paths:
            self._file_list_text.value = "暂无选择文件"
            self._file_list_text.italic = True
        else:
            lines = []
            for p in self._selected_paths:
                path_obj = Path(p)
                if path_obj.is_dir():
                    file_count = sum(1 for _ in path_obj.rglob("*") if _.is_file())
                    lines.append(f"📁 {path_obj.name}/ ({file_count} 个文件)")
                elif path_obj.is_file():
                    size = path_obj.stat().st_size
                    size_str = self._format_size(size)
                    lines.append(f"📄 {path_obj.name} ({size_str})")
            self._file_list_text.value = "\n".join(lines) if lines else "暂无选择文件"
            self._file_list_text.italic = False

        self._status_text.value = f"已选择 {len(self._selected_paths)} 个路径"
        self._status_text.color = ft.Colors.GREEN
        self.update()

    def _on_start(self, e):
        """Start the backup job."""
        if not self._selected_paths:
            self._status_text.value = "请先选择要备份的文件或文件夹"
            self._status_text.color = ft.Colors.RED
            self.update()
            return

        # Build destinations and drivers
        destinations = []
        drivers: dict[int, object] = {}

        for acc_id, (cb, folder_field) in self._drive_checkboxes.items():
            if not cb.value:
                continue

            row = self.db.get_account(acc_id)
            if not row:
                continue

            try:
                access_token = self.crypto.decrypt(row["access_token_enc"])
                refresh_token_enc = row.get("refresh_token_enc", "")
                refresh_token = (
                    self.crypto.decrypt(refresh_token_enc)
                    if refresh_token_enc and refresh_token_enc != "{}"
                    else ""
                )
                extra_enc = row.get("extra_enc", "")
                extra = (
                    json.loads(self.crypto.decrypt(extra_enc))
                    if extra_enc and extra_enc != "{}"
                    else {}
                )
            except Exception as ex:
                self._status_text.value = f"解密账号 {row['account_name']} 失败: {ex}"
                self._status_text.color = ft.Colors.RED
                self.update()
                continue

            remote_dir = folder_field.value.strip() or "/PanUpdate_backup"

            dest = BackupDestination(
                provider=row["provider"],
                account_name=row["account_name"],
                account_id=row["id"],
                remote_dir=remote_dir,
            )
            destinations.append(dest)

            # Build driver from stored credentials
            driver = self._restore_driver(
                row["provider"],
                row["account_name"],
                access_token,
                refresh_token,
                row["expires_at"],
                extra,
            )
            if driver:
                drivers[acc_id] = driver
                tok_preview = access_token[:20] + "..." if len(access_token) > 20 else access_token
                log_upload_event(
                    f"账号 {row['account_name']} ({row['provider']}): "
                    f"token={tok_preview}, expires={row['expires_at']}, "
                    f"remote_dir={remote_dir}"
                )

        if not drivers:
            self._status_text.value = "请选择至少一个目标网盘"
            self._status_text.color = ft.Colors.RED
            self.update()
            return

        # Create and start job
        job = self._engine.create_job(self._selected_paths, destinations)
        self._current_job_id = job.id
        self._is_running = True

        # Update UI state
        self._start_btn.disabled = True
        self._cancel_btn.disabled = False
        self._status_text.value = "备份进行中..."
        self._status_text.color = ft.Colors.BLUE
        self._overall_progress.visible = True
        self._progress_container.visible = True
        self._progress_container.controls.clear()
        self.update()

        # Start backup in background
        if self.page:
            self.page.run_task(self._run_background_job, job.id, drivers)

    async def _run_background_job(self, job_id: str, drivers: dict):
        """Run backup job and update UI with progress."""
        def on_progress(_jid: str, pct: float):
            self._overall_progress.value = pct
            self._status_text.value = f"备份中... {int(pct * 100)}%"
            self.update()

        job = await self._engine.start_job(job_id, drivers, progress_callback=on_progress)

        self._is_running = False
        self._start_btn.disabled = False
        self._cancel_btn.disabled = True

        # Populate per-file results
        self._progress_container.controls.clear()
        for ti in job.tasks:
            provider_label = self._provider_label(ti.provider)
            if ti.status == "success":
                icon = ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN, size=16)
                text = ft.Text(f"{ti.file_name} → {provider_label}", color=ft.Colors.GREEN)
            else:
                icon = ft.Icon(ft.Icons.ERROR, color=ft.Colors.RED, size=16)
                err_short = ti.error[:80] + "..." if len(ti.error) > 80 else ti.error
                text = ft.Text(
                    f"{ti.file_name} → {provider_label}: {err_short}",
                    color=ft.Colors.RED,
                )
            self._progress_container.controls.append(
                ft.Row([icon, text], spacing=6)
            )

        if job.fail_count == 0:
            self._status_text.value = f"备份完成！成功: {job.success_count} 个文件"
            self._status_text.color = ft.Colors.GREEN
        else:
            self._status_text.value = f"备份完成。成功: {job.success_count}, 失败: {job.fail_count}"
            self._status_text.color = ft.Colors.ORANGE

        self._overall_progress.value = 1.0
        self.update()

    def _on_cancel(self, e):
        """Cancel the current backup job."""
        if self._current_job_id:
            self._engine.cancel_job(self._current_job_id)
            self._status_text.value = "备份已取消"
            self._status_text.color = ft.Colors.ORANGE
            self._start_btn.disabled = False
            self._cancel_btn.disabled = True
            self._is_running = False
            self.update()

    def _restore_driver(self, provider: str, account_name: str,
                        access_token: str, refresh_token: str,
                        expires_at: float, extra: dict):
        """Re-create a CloudDriver instance from stored credentials."""
        account = AccountInfo(
            provider=provider,
            account_name=account_name,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            extra=extra,
        )

        if provider == "baidu":
            from panupdate.drivers.baidu import BaiduDriver
            app_key = self.db.get_config("baidu_app_key", "")
            secret_key = self.db.get_config("baidu_secret_key", "")
            driver = BaiduDriver(app_key, secret_key, account=account)
            if app_key:
                log_driver_created("baidu", True, f"OAuth mode, app_key={app_key[:8]}...")
            else:
                log_driver_created("baidu", True, "Cookie mode (BDUSS)")
            return driver

        elif provider == "kuaike":
            from panupdate.drivers.kuaike import KuaikeDriver
            driver = KuaikeDriver(account=account)
            log_driver_created("kuaike", True)
            return driver

        log_driver_created(provider, False, f"不支持的网盘: {provider}")
        return None

    # --- Helpers ---

    @staticmethod
    def _provider_label(provider: str) -> str:
        labels = {
            "baidu": "百度网盘",
            "kuaike": "夸克云盘",
        }
        return labels.get(provider, provider)

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / 1024 / 1024:.1f} MB"
        else:
            return f"{size / 1024 / 1024 / 1024:.1f} GB"
