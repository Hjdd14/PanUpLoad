"""Local async HTTP server for OAuth2 authorization-code callback capture.

Starts on a random available port on 127.0.0.1. The OAuth provider redirects
the user back to http://127.0.0.1:<port>/callback?code=<auth_code>. The
server captures the code and returns a success page to the user.
"""

import asyncio
import socket
from urllib.parse import urlparse, parse_qs


_SUCCESS_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>登录成功</title>
<style>
  body { font-family: 'Segoe UI', sans-serif; display: flex; justify-content: center;
         align-items: center; height: 100vh; margin: 0; background: #f0fdf4; }
  .card { background: white; padding: 40px 60px; border-radius: 12px;
          box-shadow: 0 4px 24px rgba(0,0,0,.08); text-align: center; }
  h1 { color: #16a34a; margin: 0 0 8px; font-size: 24px; }
  p { color: #666; margin: 0; }
</style></head>
<body>
<div class="card"><h1>授权成功</h1><p>请返回 PanUpLoad 继续操作，此窗口可以关闭。</p></div>
</body></html>"""


class OAuthCallbackServer:
    """One-shot async HTTP server that captures an OAuth authorization code."""

    def __init__(self):
        self._server: asyncio.AbstractServer | None = None
        self._auth_code: str | None = None
        self._code_event = asyncio.Event()
        self._port: int = 0

    @property
    def port(self) -> int:
        return self._port

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self._port}/callback"

    async def start(self) -> None:
        """Start the server on a random available port."""
        self._port = _find_free_port()
        self._server = await asyncio.start_server(
            self._handle_request, "127.0.0.1", self._port
        )

    async def wait_for_code(self, timeout: float = 120.0) -> str | None:
        """Wait until a code is captured or timeout expires. Returns code or None."""
        try:
            await asyncio.wait_for(self._code_event.wait(), timeout=timeout)
            return self._auth_code
        except asyncio.TimeoutError:
            return None

    async def stop(self) -> None:
        """Shut down the server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request_line = await reader.readline()
        parts = request_line.decode("utf-8", errors="replace").split()
        if len(parts) < 2:
            writer.close()
            return

        method, path = parts[0], parts[1]
        parsed = urlparse(path)

        if method == "GET" and parsed.path == "/callback":
            qs = parse_qs(parsed.query)
            codes = qs.get("code", [])
            if codes:
                self._auth_code = codes[0]
                self._code_event.set()

            body = _SUCCESS_HTML.encode("utf-8")
            writer.write(
                f"HTTP/1.1 200 OK\r\n"
                f"Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n\r\n".encode()
                + body
            )
        else:
            writer.write(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")

        await writer.drain()
        writer.close()


def _find_free_port() -> int:
    """Return an available port number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
