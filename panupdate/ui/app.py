"""Main Flet application entry — tab-based layout."""

import flet as ft

from panupdate.storage.db import Database
from panupdate.storage.crypto import CryptoManager
from panupdate.utils.logger import setup_logger, get_logger
from panupdate.ui.login_page import LoginPage
from panupdate.ui.backup_page import BackupPage
from panupdate.ui.settings_page import SettingsPage


class PanUpLoadApp:
    """Main application controller."""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.db = Database(data_dir)
        self.crypto = CryptoManager(data_dir)
        self._pages = {}
        self._page_ref = None

    def run(self, page: ft.Page) -> None:
        """Flet entry point — called by ft.app(target=...)."""
        self._page_ref = page
        page.title = "PanUpLoad — 多网盘备份工具"
        page.theme_mode = ft.ThemeMode.SYSTEM
        page.window.width = 900
        page.window.height = 700
        page.window.min_width = 700
        page.window.min_height = 500

        # Initialize logger
        setup_logger(self.data_dir)
        log = get_logger()
        log.info("PanUpLoad starting")

        # Initialize storage
        self.crypto.initialize()
        self.db.initialize()

        # Build pages
        login_page = LoginPage(self.db, self.crypto, on_account_added=self._refresh)
        backup_page = BackupPage(self.db, self.crypto)
        settings_page = SettingsPage(self.db, self.crypto)

        self._pages = {
            "login": login_page,
            "backup": backup_page,
            "settings": settings_page,
        }

        self._tabs = ft.Tabs(
            length=3,
            selected_index=0,
            animation_duration=300,
            expand=True,
            content=ft.Column(
                expand=True,
                spacing=0,
                controls=[
                    ft.TabBar(
                        tabs=[
                            ft.Tab(label="账号管理", icon=ft.Icons.ACCOUNT_CIRCLE),
                            ft.Tab(label="备份任务", icon=ft.Icons.BACKUP),
                            ft.Tab(label="设置", icon=ft.Icons.SETTINGS),
                        ],
                    ),
                    ft.TabBarView(
                        expand=True,
                        controls=[
                            login_page,
                            backup_page,
                            settings_page,
                        ],
                    ),
                ],
            ),
        )

        page.add(self._tabs)

    def _refresh(self):
        """Refresh current view after data changes."""
        if "backup" in self._pages:
            self._pages["backup"].reload_accounts()
        if self._page_ref:
            self._page_ref.update()
