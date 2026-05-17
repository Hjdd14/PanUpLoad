"""Reusable cloud drive status card component."""

import flet as ft


class DriveCard(ft.Container):
    """A card showing one cloud drive's status and quota."""

    def __init__(
        self,
        provider_name: str,
        account_name: str,
        is_connected: bool = False,
        quota_used: int = 0,
        quota_total: int = 0,
    ):
        self.provider_name = provider_name
        self.account_name = account_name
        self.is_connected = is_connected
        self.quota_used = quota_used
        self.quota_total = quota_total

        status_color = ft.Colors.GREEN if is_connected else ft.Colors.GREY
        status_text = "已连接" if is_connected else "未连接"

        quota_str = self._format_quota() if is_connected else "—"

        super().__init__(
            content=ft.ListTile(
                leading=ft.Icon(ft.Icons.CLOUD, color=status_color, size=40),
                title=ft.Text(f"{provider_name} ({account_name})"),
                subtitle=ft.Text(f"状态: {status_text}  |  容量: {quota_str}"),
                trailing=ft.Container(
                    width=12,
                    height=12,
                    border_radius=6,
                    bgcolor=status_color,
                ),
            ),
            border=ft.border.all(1, ft.Colors.OUTLINE),
            border_radius=8,
            padding=5,
            margin=ft.margin.all(4),
        )

    def _format_quota(self) -> str:
        if not self.is_connected or self.quota_total <= 0:
            return "—"
        used_gb = self.quota_used / (1024 ** 3)
        total_gb = self.quota_total / (1024 ** 3)
        pct = self.quota_used / self.quota_total * 100
        return f"{used_gb:.1f} / {total_gb:.1f} GB ({pct:.0f}%)"
