"""Tests for OAuth callback server."""

import asyncio
import httpx
import pytest

from panupdate.auth.oauth_server import OAuthCallbackServer


class TestOAuthCallbackServer:

    @pytest.mark.asyncio
    async def test_starts_on_random_port(self):
        server = OAuthCallbackServer()
        await server.start()
        try:
            assert server.port > 0
            assert server.redirect_uri == f"http://127.0.0.1:{server.port}/callback"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_callback_captures_code(self):
        server = OAuthCallbackServer()
        await server.start()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"http://127.0.0.1:{server.port}/callback?code=test_auth_code_123"
                )
                assert resp.status_code == 200
                assert "授权成功" in resp.text

            code = await server.wait_for_code(timeout=1.0)
            assert code == "test_auth_code_123"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_callback_without_code(self):
        server = OAuthCallbackServer()
        await server.start()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"http://127.0.0.1:{server.port}/callback"
                )
                assert resp.status_code == 200

            code = await server.wait_for_code(timeout=0.5)
            assert code is None
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_wait_for_code_timeout(self):
        server = OAuthCallbackServer()
        await server.start()
        try:
            code = await server.wait_for_code(timeout=0.1)
            assert code is None
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_not_found_for_other_paths(self):
        server = OAuthCallbackServer()
        await server.start()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"http://127.0.0.1:{server.port}/other"
                )
                assert resp.status_code == 404
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_redirect_uri_format(self):
        server = OAuthCallbackServer()
        await server.start()
        try:
            uri = server.redirect_uri
            assert uri.startswith("http://127.0.0.1:")
            assert uri.endswith("/callback")
        finally:
            await server.stop()
