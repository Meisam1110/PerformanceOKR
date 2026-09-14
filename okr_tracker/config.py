"""Where the application keeps its data and how it is configured.

Settings come from three places, each overriding the one before it:

1. ``okr-tracker.ini`` sitting next to the application. This is how a shared
   deployment ships its own configuration -- copy the folder to a network share
   with the data directory already set, and everyone who runs it lands on the
   same workspace with nothing to configure.
2. Environment variables (``OKR_DATA_DIR``, ``OKR_PORT``, ...).
3. Command line flags.

Relative paths in the ini file resolve against the folder holding it, so the
whole application folder stays portable: move it, and the settings still point
where they should.
"""

from __future__ import annotations

import configparser
import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "POLE OKR Tracker"
APP_DIR_NAME = "pole-okr-tracker"

CONFIG_NAME = "okr-tracker.ini"
CONFIG_SECTION = "tracker"


def app_dir() -> Path:
    """The folder the application was started from.

    For a portable copy this is the folder holding the launcher and the ini
    file, which is what relative settings are measured against.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def config_path() -> Path | None:
    """The ini file to read, if there is one.

    ``OKR_CONFIG`` names one explicitly; otherwise it is ``okr-tracker.ini``
    beside the application.
    """
    override = os.environ.get("OKR_CONFIG")
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.exists() else None
    candidate = app_dir() / CONFIG_NAME
    return candidate if candidate.exists() else None


def _read_config() -> dict[str, str]:
    path = config_path()
    if not path:
        return {}
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error) as error:
        print(f"Ignoring {path}: {error}", file=sys.stderr)
        return {}
    if not parser.has_section(CONFIG_SECTION):
        return {}
    return {key.lower(): value.strip() for key, value in parser.items(CONFIG_SECTION)}


#: Read once: the file is read at startup and not watched.
_CONFIG = _read_config()


def _setting(name: str) -> str | None:
    """Look a setting up by its ini key, then its environment variable."""
    value = os.environ.get(f"OKR_{name.upper()}")
    if value is not None and value != "":
        return value
    value = _CONFIG.get(name.lower())
    return value if value else None


def _flag(name: str, default: bool = False) -> bool:
    raw = _setting(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        raw = _setting(name)
        return int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


def default_data_dir() -> Path:
    """Where the workspace lives.

    A configured path wins; a relative one is measured from the application
    folder so a portable copy carries its data with it. Otherwise this falls
    back to the per-user location each platform expects.
    """
    configured = _setting("data_dir")
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else (app_dir() / path).resolve()

    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / APP_DIR_NAME


@dataclass
class Settings:
    """Resolved runtime configuration."""

    data_dir: Path = field(default_factory=default_data_dir)
    host: str = field(default_factory=lambda: _setting("host") or "127.0.0.1")
    port: int = field(default_factory=lambda: _int("port", 8765))
    read_only: bool = field(default_factory=lambda: _flag("read_only"))
    poll_seconds: int = field(default_factory=lambda: _int("poll_seconds", 15))
    backup_count: int = field(default_factory=lambda: _int("backup_count", 30))
    passphrase: str | None = field(default_factory=lambda: _setting("passphrase"))
    secret_key: str = field(default_factory=lambda: _setting("secret_key") or "")

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
