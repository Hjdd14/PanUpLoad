"""Account management page — one-click login for all 5 cloud drives.

Uses CDP (Chrome DevTools Protocol) via raw WebSocket to control Edge.
Token extraction uses Network.getCookies CDP command to read ALL cookies
including HttpOnly ones that JS cannot access.
"""

import asyncio
import threading
import flet as ft

from panupdate.storage.db import Database
from panupdate.storage.crypto import CryptoManager
from panupdate.auth.cdp_login import SELENIUM_LOGIN_CONFIGS, run_cdp_login


class LoginPage(ft.Container):

    def __init__(
        self,
        db: Database,
        crypto: CryptoManager,
        on_account_added: ft.OptionalEventCallback = None,
    ):
        super().__init__()
        self.db = db
        self.crypto = crypto
        self.on_account_added = on_account_added
        self.expand = True

        self.providers = {
            "baidu": {"label": "百度网盘"},
            "kuaike": {"label": "夸克云盘"},
        }

        self._login_thread: threading.Thread | None = None
        self._login_result: str | None = None
        self._login_provider: str = ""
        self._status_msg: str = ""

        self._build_ui()

    # ── UI ──────────────────────────────────────────────────────

    def _build_ui(self):
        self.content = ft.Column(
            controls=[
                ft.Text("网盘账号管理", size=24, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                self._build_account_list(),
                ft.Divider(),
                self._build_add_account_section(),
            ],
            scroll=ft.ScrollMode.AUTO,
        )

    def _build_account_list(self) -> ft.Column:
        accounts = self.db.list_accounts()
        items = []
        editing_id = getattr(self, '_editing_account', None)
        for acc in accounts:
            label = self.providers.get(acc["provider"], {}).get("label", acc["provider"])
            aid = acc["id"]
            account_name = acc["account_name"]

            if editing_id == aid:
                # Inline rename: show text field + save/cancel buttons
                name_field = ft.TextField(value=account_name, autofocus=True, width=200)
                items.append(name_field)
                items.append(
                    ft.Row([
                        ft.ElevatedButton("保存", on_click=lambda _, f=name_field, a=aid: self._do_rename(a, f.value)),
                        ft.ElevatedButton("取消", on_click=lambda _: self._cancel_rename()),
                    ])
                )
            else:
                items.append(
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.CLOUD),
                        title=ft.Text(f"{label} — {account_name}"),
                        subtitle=ft.Text(f"{label}"),
                        trailing=ft.IconButton(
                            icon=ft.Icons.DELETE,
                            tooltip="删除账号",
                            on_click=lambda _, a=aid: self._delete_account(a),
                        ),
                    )
                )
                items.append(
                    ft.ElevatedButton(
                        f"✏️ 修改昵称：{account_name}",
                        on_click=lambda _, a=aid: self._start_rename(a),
                    )
                )
        if not items:
            items.append(ft.Text("暂无已添加的账号", italic=True, color=ft.Colors.GREY))
        return ft.Column(items, spacing=2)

    def _start_rename(self, account_id: int):
        self._editing_account = account_id
        self._build_ui()
        self.update()

    def _cancel_rename(self):
        self._editing_account = None
        self._build_ui()
        self.update()

    def _do_rename(self, account_id: int, new_name: str):
        new_name = new_name.strip()
        if new_name:
            self.db.update_account_name(account_id, new_name)
        self._editing_account = None
        self._build_ui()
        self.update()

    def _build_add_account_section(self) -> ft.Container:
        self._provider_dropdown = ft.Dropdown(
            label="选择网盘",
            options=[
                ft.dropdown.Option(key=k, text=v["label"])
                for k, v in self.providers.items()
            ],
            width=300,
        )
        self._status_text = ft.Text("", size=12)
        self._progress_bar = ft.ProgressBar(visible=False)
        self._login_btn = ft.Button(
            "开始登录", icon=ft.Icons.LOGIN, on_click=self._on_start_login
        )
        self._manual_visible = False
        self._manual_field = ft.TextField(
            label="Token / 授权码", hint_text="手动粘贴 Token", width=400,
        )
        self._manual_section = ft.Column(
            controls=[
                ft.Text("高级：手动输入 Token", size=14, weight=ft.FontWeight.BOLD),
                ft.Text("如果自动登录失败，可在此手动粘贴：", size=12, color=ft.Colors.GREY),
                ft.Row([
                    self._manual_field,
                    ft.Button("添加", icon=ft.Icons.ADD_CIRCLE,
                              on_click=self._on_manual_add),
                ]),
            ],
            visible=False,
        )
        return ft.Container(
            content=ft.Column([
                ft.Text("添加新账号", size=18, weight=ft.FontWeight.BOLD),
                ft.Text("点击按钮后弹出登录窗口，登录完成后自动获取 Token",
                        size=12, color=ft.Colors.GREY),
                self._provider_dropdown,
                self._login_btn,
                self._progress_bar,
                self._status_text,
                ft.Divider(),
                ft.Button(
                    "高级：手动输入 Token",
                    icon=ft.Icons.KEYBOARD_ARROW_DOWN,
                    on_click=self._toggle_manual,
                ),
                self._manual_section,
            ]),
            padding=10,
        )

    def _toggle_manual(self, e):
        self._manual_visible = not self._manual_visible
        self._manual_section.visible = self._manual_visible
        e.control.icon = (
            ft.Icons.KEYBOARD_ARROW_UP if self._manual_visible
            else ft.Icons.KEYBOARD_ARROW_DOWN
        )
        self.update()

    # ── Login flow — threading + page.run_task polling ──────────

    def _on_start_login(self, e):
        provider = self._provider_dropdown.value
        if not provider:
            self._status_text.value = "请先选择网盘类型"
            self._status_text.color = ft.Colors.RED
            self.update()
            return

        config = SELENIUM_LOGIN_CONFIGS.get(provider)
        if not config:
            self._status_text.value = f"不支持的网盘: {provider}"
            self._status_text.color = ft.Colors.RED
            self.update()
            return

        self._login_provider = provider
        self._login_result = None
        self._status_msg = ""

        self._status_text.value = "正在启动浏览器..."
        self._status_text.color = ft.Colors.BLUE
        self._login_btn.disabled = True
        self._progress_bar.visible = True
        self.update()

        # Run CDP login in background thread
        self._login_thread = threading.Thread(
            target=self._run_cdp_in_thread,
            args=(config,),
            daemon=True,
        )
        self._login_thread.start()

        if self.page:
            self.page.run_task(self._poll_login_status)

    def _run_cdp_in_thread(self, config):
        """Run blocking CDP login in background thread."""
        def on_status(msg: str):
            self._status_msg = msg

        self._login_result = run_cdp_login(config, timeout=180.0, on_status=on_status)

    async def _poll_login_status(self):
        """Async polling — updates UI and checks for completion."""
        while self._login_thread and self._login_thread.is_alive():
            if self._status_msg and self._status_msg != self._status_text.value:
                self._status_text.value = self._status_msg
                self.update()
            await asyncio.sleep(0.5)

        # Thread done — process result
        token = self._login_result

        if token is None:
            # If CDP reported a specific error, show it instead of generic timeout
            if self._status_msg and ("失败" in self._status_msg or "异常" in self._status_msg):
                self._status_text.value = self._status_msg
                self._status_text.color = ft.Colors.RED
            else:
                self._status_text.value = (
                    "登录未完成或超时。请重试，或使用「高级：手动输入 Token」"
                )
                self._status_text.color = ft.Colors.ORANGE
            self._reset_login_state()
            return

        self._status_text.value = "正在验证 Token..."
        self.update()

        # Extract display_name from CDP token if present (format: "token|display_name=XXX")
        display_name = ""
        clean_token = token
        if "|display_name=" in token:
            parts = token.split("|display_name=")
            clean_token = parts[0]
            display_name = parts[1] if len(parts) > 1 else ""

        driver = self._make_driver(self._login_provider)
        if driver is None:
            self._reset_login_state()
            return

        try:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, self._validate_token, driver, clean_token)
        except Exception as ex:
            self._status_text.value = f"Token 验证失败: {ex}"
            self._status_text.color = ft.Colors.RED
            self._reset_login_state()
            return

        # Override account name with CDP-extracted display name
        if display_name:
            info.account_name = display_name

        self.db.save_account(info, self.crypto.encrypt)
        self._status_text.value = f"{self.providers[self._login_provider]['label']} 登录成功！"
        self._status_text.color = ft.Colors.GREEN

        self._reset_login_state()
        if self.on_account_added:
            self.on_account_added()
        self._build_ui()
        self.update()

    def _validate_token(self, driver, token):
        loop = asyncio.new_event_loop()
        try:
            info = loop.run_until_complete(driver.login(token))
            loop.run_until_complete(driver.close())
            return info
        finally:
            loop.close()

    # ── Manual fallback ─────────────────────────────────────────

    def _on_manual_add(self, e):
        provider = self._provider_dropdown.value
        auth_code = self._manual_field.value.strip()
        if not provider or not auth_code:
            self._status_text.value = "请选择网盘并输入 Token"
            self._status_text.color = ft.Colors.RED
            self.update()
            return
        driver = self._make_driver(provider)
        if driver is None:
            return
        self._status_text.value = "正在登录..."
        self._status_text.color = ft.Colors.BLUE
        self.update()
        threading.Thread(
            target=self._run_manual_thread, args=(driver, auth_code, provider),
            daemon=True,
        ).start()

    def _run_manual_thread(self, driver, auth_code, provider):
        try:
            loop = asyncio.new_event_loop()
            info = loop.run_until_complete(driver.login(auth_code))
            loop.run_until_complete(driver.close())
            loop.close()
            self.db.save_account(info, self.crypto.encrypt)
            self._status_text.value = f"{self.providers[provider]['label']} 账号添加成功"
            self._status_text.color = ft.Colors.GREEN
            self._manual_field.value = ""
        except Exception as ex:
            self._status_text.value = f"登录失败: {ex}"
            self._status_text.color = ft.Colors.RED
        if self.on_account_added:
            self.on_account_added()
        self._build_ui()
        self.update()

    # ── Helpers ─────────────────────────────────────────────────

    def _reset_login_state(self):
        self._login_btn.disabled = False
        self._progress_bar.visible = False
        self.update()

    def _make_driver(self, provider: str):
        if provider == "baidu":
            from panupdate.drivers.baidu import BaiduDriver
            return BaiduDriver()
        elif provider == "kuaike":
            from panupdate.drivers.kuaike import KuaikeDriver
            return KuaikeDriver()
        return None

    def _delete_account(self, account_id: int):
        self.db.delete_account(account_id)
        self._build_ui()
        self.update()
