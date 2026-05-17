"""Windows DPAPI-based encryption for local credential storage.

Uses CryptProtectData / CryptUnprotectData from Windows Data Protection API
to bind the encryption key to the current Windows user account.
"""

import ctypes
import ctypes.wintypes
from cryptography.fernet import Fernet
import os


# --- DPAPI bindings ---
_LOCAL_FREE = ctypes.windll.kernel32.LocalFree
_CRYPT_PROTECT_DATA = ctypes.windll.crypt32.CryptProtectData
_CRYPT_UNPROTECT_DATA = ctypes.windll.crypt32.CryptUnprotectData

# CRYPTPROTECT_UI_FORBIDDEN = 0x01 — no UI prompt
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi_encrypt(plain_bytes: bytes) -> bytes:
    """Encrypt bytes using DPAPI, bound to current Windows user."""
    blob_in = _DataBlob(len(plain_bytes), ctypes.cast(
        ctypes.create_string_buffer(plain_bytes), ctypes.POINTER(ctypes.c_byte)))
    blob_out = _DataBlob()

    if not _CRYPT_PROTECT_DATA(
        ctypes.byref(blob_in), None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
    ):
        raise OSError("DPAPI encryption failed")

    result = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    _LOCAL_FREE(blob_out.pbData)
    return result


def _dpapi_decrypt(cipher_bytes: bytes) -> bytes:
    """Decrypt bytes using DPAPI."""
    blob_in = _DataBlob(len(cipher_bytes), ctypes.cast(
        ctypes.create_string_buffer(cipher_bytes), ctypes.POINTER(ctypes.c_byte)))
    blob_out = _DataBlob()

    if not _CRYPT_UNPROTECT_DATA(
        ctypes.byref(blob_in), None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
    ):
        raise OSError("DPAPI decryption failed")

    result = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    _LOCAL_FREE(blob_out.pbData)
    return result


_KEY_FILE = "master_key.enc"


class CryptoManager:
    """Manages encryption of sensitive data using DPAPI-protected keys."""

    def __init__(self, data_dir: str):
        self._data_dir = data_dir
        self._fernet: Fernet | None = None

    @property
    def is_initialized(self) -> bool:
        return self._fernet is not None

    def initialize(self) -> None:
        """Load or create the DPAPI-protected encryption key."""
        key_path = os.path.join(self._data_dir, _KEY_FILE)
        os.makedirs(self._data_dir, exist_ok=True)

        if os.path.exists(key_path):
            with open(key_path, "rb") as f:
                encrypted_key = f.read()
            raw_key = _dpapi_decrypt(encrypted_key)
        else:
            raw_key = Fernet.generate_key()
            encrypted_key = _dpapi_encrypt(raw_key)
            with open(key_path, "wb") as f:
                f.write(encrypted_key)

        self._fernet = Fernet(raw_key)

    def encrypt(self, plain_text: str) -> str:
        """Encrypt a string. Returns base64-encoded cipher text."""
        if not self._fernet:
            raise RuntimeError("CryptoManager not initialized. Call initialize() first.")
        return self._fernet.encrypt(plain_text.encode("utf-8")).decode("utf-8")

    def decrypt(self, cipher_text: str) -> str:
        """Decrypt a base64-encoded cipher text string."""
        if not self._fernet:
            raise RuntimeError("CryptoManager not initialized. Call initialize() first.")
        return self._fernet.decrypt(cipher_text.encode("utf-8")).decode("utf-8")
