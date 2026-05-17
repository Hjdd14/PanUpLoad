"""Tests for DPAPI-based crypto module."""

import pytest
import tempfile
import os
from panupdate.storage.crypto import CryptoManager


class TestCryptoManager:
    """Integration tests with real DPAPI (requires Windows)."""

    @pytest.fixture
    def crypto(self):
        data_dir = tempfile.mkdtemp()
        mgr = CryptoManager(data_dir)
        mgr.initialize()
        yield mgr

    def test_initialize_creates_key_file(self):
        data_dir = tempfile.mkdtemp()
        mgr = CryptoManager(data_dir)
        assert not mgr.is_initialized
        mgr.initialize()
        assert mgr.is_initialized
        key_file = os.path.join(data_dir, "master_key.enc")
        assert os.path.exists(key_file)

    def test_encrypt_decrypt_roundtrip(self, crypto):
        original = "Hello, PanUpdate! 你好"
        encrypted = crypto.encrypt(original)
        assert encrypted != original
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == original

    def test_reinitialize_loads_existing_key(self):
        data_dir = tempfile.mkdtemp()
        mgr1 = CryptoManager(data_dir)
        mgr1.initialize()
        ct = mgr1.encrypt("persistent data")

        mgr2 = CryptoManager(data_dir)
        mgr2.initialize()
        assert mgr2.decrypt(ct) == "persistent data"

    def test_encrypt_without_initialize_raises(self):
        mgr = CryptoManager(tempfile.mkdtemp())
        with pytest.raises(RuntimeError, match="not initialized"):
            mgr.encrypt("test")

    def test_decrypt_with_wrong_key_fails(self):
        data_dir1 = tempfile.mkdtemp()
        data_dir2 = tempfile.mkdtemp()
        mgr1 = CryptoManager(data_dir1)
        mgr1.initialize()
        ct = mgr1.encrypt("secret")

        mgr2 = CryptoManager(data_dir2)
        mgr2.initialize()
        with pytest.raises(Exception):  # InvalidToken
            mgr2.decrypt(ct)
