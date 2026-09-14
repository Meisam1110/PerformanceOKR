"""Durable storage for the single workspace document.

The tracker keeps its whole state in one JSON document and guards concurrent
edits with a ``revision`` string that the client regenerates on every write.
This module keeps that model and adds what a browser tab could not: durability,
atomic replacement, timestamped backups, and a lock so two writers cannot
interleave a read-modify-write.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .filelock import FileLock


class ConflictError(RuntimeError):
    """Raised when a write is based on a revision the store has moved past."""

    def __init__(self, current: "Document") -> None:
        super().__init__("workspace revision conflict")
        self.current = current


@dataclass(frozen=True)
class Document:
    """A stored workspace plus the revision it was saved under."""

    data: dict[str, Any] | None
    revision: str

    @property
    def exists(self) -> bool:
        return self.data is not None


EMPTY = Document(data=None, revision="")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class WorkspaceStore:
    """A JSON document on disk, replaced atomically and backed up per write.

    ``path`` holds the live document. Each successful write first copies the
    outgoing document into ``backups/`` so a bad import or a client bug is
    always recoverable, then renames a fully written temporary file over the
    live one -- an interrupted write can never leave a truncated workspace.
    """

    def __init__(self, path: str | os.PathLike[str], *, backup_count: int = 30) -> None:
        self.path = Path(path)
        self.backup_dir = self.path.parent / "backups"
        self.backup_count = max(0, backup_count)
        # Two locks, two scopes: the threading lock serialises this process's
        # own request threads, the file lock serialises other processes and
        # other machines sharing the same folder over the network.
        self._lock = threading.RLock()
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _exclusive(self) -> FileLock:
        return FileLock(self.lock_path)

    # ---------------------------------------------------------------- read

    def read(self) -> Document:
        """Return the stored document, or ``EMPTY`` if nothing is stored yet."""
        with self._lock:
            return self._read_unlocked()

    def _read_unlocked(self) -> Document:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return EMPTY
        if not raw.strip():
            return EMPTY
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"{self.path} is not valid JSON ({error}). "
                f"A recent copy may be available in {self.backup_dir}."
            ) from error
        if not isinstance(data, dict):
            raise RuntimeError(f"{self.path} does not contain a workspace object")
        return Document(data=data, revision=str(data.get("revision") or ""))

    # --------------------------------------------------------------- write

    def write(self, data: dict[str, Any], *, base_revision: str | None) -> Document:
        """Replace the document, but only if it still sits at ``base_revision``.

        ``base_revision`` is the revision the caller last saw. ``None`` means
        "only if nothing is stored yet" -- the first write of a fresh install.
        A mismatch raises :class:`ConflictError` carrying the current document,
        which the client uses to resynchronise.
        """
        with self._lock, self._exclusive():
            current = self._read_unlocked()
            expected = (base_revision or "") if base_revision is not None else ""
            if current.revision != expected:
                raise ConflictError(current)
            self._backup_unlocked(current)
            self._replace_unlocked(data)
            return Document(data=data, revision=str(data.get("revision") or ""))

    def overwrite(self, data: dict[str, Any]) -> Document:
        """Replace the document unconditionally (import and restore paths)."""
        with self._lock, self._exclusive():
            self._backup_unlocked(self._read_unlocked())
            self._replace_unlocked(data)
            return Document(data=data, revision=str(data.get("revision") or ""))

    def _replace_unlocked(self, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent,
            prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
        )
        try:
            with handle as out:
                out.write(payload)
                out.flush()
                os.fsync(out.fileno())
            os.replace(handle.name, self.path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise

    # -------------------------------------------------------------- backup

    def _backup_unlocked(self, document: Document) -> None:
        if not document.exists or self.backup_count == 0:
            return
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        target = self.backup_dir / f"workspace-{_utc_stamp()}.json"
        suffix = 1
        while target.exists():
            suffix += 1
            target = self.backup_dir / f"workspace-{_utc_stamp()}-{suffix}.json"
        shutil.copy2(self.path, target)
        self._prune_unlocked()

    def _prune_unlocked(self) -> None:
        backups = sorted(self.backup_dir.glob("workspace-*.json"))
        for stale in backups[: max(0, len(backups) - self.backup_count)]:
            stale.unlink(missing_ok=True)

    def backups(self) -> list[Path]:
        """Newest-first list of retained backups."""
        if not self.backup_dir.exists():
            return []
        return sorted(self.backup_dir.glob("workspace-*.json"), reverse=True)
