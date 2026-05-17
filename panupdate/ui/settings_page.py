"""Settings page — configure cloud drive API keys and app preferences."""

import flet as ft

from panupdate.storage.db import Database
from panupdate.storage.crypto import CryptoManager


class SettingsPage(ft.Container):
    """Page for configuring app settings and API credentials."""

    def __init__(self, db: Database, crypto: CryptoManager):
        super().__init__()
        self.db = db
        self.crypto = crypto
        self.expand = True
        self._status_text = ft.Text("", size=12)
        self._build_ui()

    def _build_ui(self):
        # Load current values
        baidu_app_key = self.db.get_config("baidu_app_key", "")
        baidu_secret_key = self.db.get_config("baidu_secret_key", "")
        max_concurrent = str(self.db.get_config("max_concurrent", 3))
        default_remote_dir = self.db.get_config("default_remote_dir", "/PanUpdate_backup")

        self._baidu_app_key_field = ft.TextField(
            label="百度网盘 App Key (API Key)",
            value=baidu_app_key,
            hint_text="从百度网盘开放平台获取",
            password=True,
            can_reveal_password=True,
            width=450,
        )
        self._baidu_secret_key_field = ft.TextField(
            label="百度网盘 Secret Key",
            value=baidu_secret_key,
            hint_text="从百度网盘开放平台获取",
            password=True,
            can_reveal_password=True,
            width=450,
        )

        self._max_concurrent_field = ft.Dropdown(
            label="最大同时上传数",
            options=[
                ft.dropdown.Option(key="1", text="1 个"),
                ft.dropdown.Option(key="2", text="2 个"),
                ft.dropdown.Option(key="3", text="3 个（推荐）"),
                ft.dropdown.Option(key="5", text="5 个"),
                ft.dropdown.Option(key="8", text="8 个"),
            ],
            value=max_concurrent,
            width=250,
        )

        self._default_remote_dir_field = ft.TextField(
            label="默认备份目录",
            value=default_remote_dir,
            hint_text="/PanUpdate_backup",
            width=350,
        )

        self.content = ft.Column(
            controls=[
                ft.Text("设置", size=24, weight=ft.FontWeight.BOLD),
                ft.Divider(),

                ft.Text("百度网盘 API 凭据（可选）", size=16, weight=ft.FontWeight.BOLD),
                ft.Text("新版已支持浏览器自动登录，无需配置此项。仅在需要 OAuth 高级登录时填写。",
                        size=12, color=ft.Colors.GREY),
                self._baidu_app_key_field,
                self._baidu_secret_key_field,
                ft.Divider(),

                ft.Text("上传设置", size=16, weight=ft.FontWeight.BOLD),
                self._max_concurrent_field,
                self._default_remote_dir_field,
                ft.Divider(),

                ft.Row([
                    ft.Button("保存设置", icon=ft.Icons.SAVE, on_click=self._on_save),
                    self._status_text,
                ], spacing=12),
            ],
            scroll=ft.ScrollMode.AUTO,
            spacing=8,
        )

    def _on_save(self, e):
        try:
            self.db.set_config("baidu_app_key", self._baidu_app_key_field.value.strip())
            self.db.set_config("baidu_secret_key", self._baidu_secret_key_field.value.strip())
            self.db.set_config("max_concurrent", int(self._max_concurrent_field.value))
            self.db.set_config("default_remote_dir", self._default_remote_dir_field.value.strip() or "/PanUpdate_backup")

            self._status_text.value = "设置已保存"
            self._status_text.color = ft.Colors.GREEN
        except Exception as ex:
            self._status_text.value = f"保存失败: {ex}"
            self._status_text.color = ft.Colors.RED

        self.update()
