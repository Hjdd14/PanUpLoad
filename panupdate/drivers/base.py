"""Abstract base class for all cloud drive implementations."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class FileItem:
    """Represents a file or folder on the remote cloud drive."""
    path: str
    name: str
    is_dir: bool = False
    size: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    file_id: str = ""


@dataclass
class QuotaInfo:
    """Cloud drive storage quota information."""
    total: int = 0          # Total bytes
    used: int = 0           # Used bytes

    @property
    def remaining(self) -> int:
        return self.total - self.used


@dataclass
class AccountInfo:
    """Stored account credentials for a cloud drive."""
    provider: str                    # e.g. "baidu", "kuaike"
    account_name: str                # User-visible name
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0          # Unix timestamp
    extra: dict = field(default_factory=dict)  # Provider-specific data


class CloudDriver(ABC):
    """Abstract interface that every cloud drive provider must implement."""

    def __init__(self, account: AccountInfo | None = None):
        self._account = account

    @property
    def account(self) -> AccountInfo | None:
        return self._account

    @account.setter
    def account(self, value: AccountInfo | None) -> None:
        self._account = value

    @abstractmethod
    async def login(self, auth_code: str) -> AccountInfo:
        """Exchange an OAuth authorization code for tokens.

        Returns an AccountInfo populated with access_token, refresh_token, etc.
        """
        ...

    @abstractmethod
    async def get_auth_url(self) -> str:
        """Return the OAuth authorization URL the user must visit."""
        ...

    @abstractmethod
    async def upload_file(
        self, local_path: str, remote_dir: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> str:
        """Upload a local file to the specified remote directory.

        Returns the file_id of the uploaded file.
        progress_callback(bytes_uploaded, total_bytes) is called periodically.
        """
        ...

    @abstractmethod
    async def create_folder(self, remote_path: str) -> bool:
        """Create a folder on the remote drive. Returns True on success."""
        ...

    @abstractmethod
    async def list_files(self, remote_dir: str) -> list[FileItem]:
        """List files and folders in the specified remote directory."""
        ...

    @abstractmethod
    async def get_quota(self) -> QuotaInfo:
        """Get storage quota information."""
        ...

    @abstractmethod
    async def refresh_token(self) -> str:
        """Refresh the access token. Returns the new access_token string."""
        ...

    @abstractmethod
    async def test_connection(self) -> bool:
        """Test if the current account credentials are valid."""
        ...
