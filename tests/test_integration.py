"""Integration tests for storage + crypto + DB working together."""

import pytest
import tempfile
from panupdate.storage.crypto import CryptoManager
from panupdate.storage.db import Database
from panupdate.drivers.base import AccountInfo


class TestStorageIntegration:
    """Crypto + DB end-to-end integration."""

    @pytest.fixture
    def env(self):
        data_dir = tempfile.mkdtemp()
        crypto = CryptoManager(data_dir)
        crypto.initialize()
        db = Database(data_dir)
        db.initialize()
        return crypto, db

    def test_save_and_decrypt_account(self, env):
        crypto, db = env
        info = AccountInfo(
            provider="baidu", account_name="test_user",
            access_token="secret_token_123!@#",
            refresh_token="refresh_456$%^",
            expires_at=9999999999,
            extra={"uid": 88888},
        )
        aid = db.save_account(info, crypto.encrypt)
        row = db.get_account(aid)
        assert row is not None

        # Verify tokens are encrypted (not plaintext)
        assert row["access_token_enc"] != info.access_token
        assert "secret_token" not in row["access_token_enc"]

        # Verify we can decrypt them
        decrypted_at = crypto.decrypt(row["access_token_enc"])
        assert decrypted_at == info.access_token
        decrypted_rt = crypto.decrypt(row["refresh_token_enc"])
        assert decrypted_rt == info.refresh_token

    def test_multiple_accounts(self, env):
        crypto, db = env
        for i in range(3):
            info = AccountInfo(
                provider="baidu", account_name=f"user_{i}",
                access_token=f"tok_{i}",
            )
            db.save_account(info, crypto.encrypt)
        assert db.count_accounts() == 3
        accounts = db.list_accounts()
        names = {a["account_name"] for a in accounts}
        assert names == {"user_0", "user_1", "user_2"}
