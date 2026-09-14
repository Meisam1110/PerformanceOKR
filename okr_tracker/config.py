"""Where the application keeps its data and how it is configured.

Every setting can come from the environment, so the same package runs as a
personal desktop app (defaults, data under the user's home) and as a small
shared server (explicit data directory, passphrase, bound to an interface).
"""

from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "POLE OKR Tracker"
APP_DIR_NAME = "pole-okr-tracker"


def default_data_dir() -> Path:
    """Per-user data directory, following each platform's convention."""
    override = os.environ.get("OKR_DATA_DIR")
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / APP_DIR_NAME


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    """Resolved runtime configuration."""

    data_dir: Path = field(default_factory=default_data_dir)
    host: str = field(default_factory=lambda: os.environ.get("OKR_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _int("OKR_PORT", 8765))
    read_only: bool = field(default_factory=lambda: _flag("OKR_READ_ONLY"))
    poll_seconds: int = field(default_factory=lambda: _int("OKR_POLL_SECONDS", 15))
    backup_count: int = field(default_factory=lambda: _int("OKR_BACKUP_COUNT", 30))
    passphrase: str | None = field(default_factory=lambda: os.environ.get("OKR_PASSPHRASE") or None)
    secret_key: str = field(
        default_factory=lambda: os.environ.get("OKR_SECRET_KEY") or ""
    )

    @property
    def workspace_path(self) -> Path:
        return self.data_dir / "workspace.json"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "secret.key"

    def resolved_secret_key(self) -> str:
        """A stable session key, generated on first run and kept on disk.

        Without this, sign-in to the passphrase gate would drop every restart.
        """
        if self.secret_key:
            return self.secret_key
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.secret_path.exists():
            existing = self.secret_path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        generated = secrets.token_urlsafe(48)
        self.secret_path.write_text(generated, encoding="utf-8")
        try:
            self.secret_path.chmod(0o600)
        except OSError:
            pass  # Filesystems without POSIX permissions (some Windows setups).
        return generated
