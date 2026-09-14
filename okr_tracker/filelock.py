"""An advisory lock that holds across processes and machines.

``threading.Lock`` only serialises writers inside one process. When the
workspace lives on a shared folder and several people run their own copy of the
app against it, the read-check-write of a compare-and-swap can interleave
between machines: two writers both read revision A, both decide they are
current, and the second silently overwrites the first.

This wraps the operating system's byte-range locks -- ``fcntl.flock`` on Unix,
``msvcrt.locking`` on Windows -- which SMB and NFS honour between clients, so
the same serialisation holds across the network.

If the filesystem does not support locking at all, the lock degrades to a no-op
and says so once rather than making the app unusable. That is the pre-existing
level of safety, not a new risk.
"""

from __future__ import annotations

import os
import sys
import time
import warnings
from pathlib import Path
from types import TracebackType

if sys.platform == "win32":  # pragma: no cover - platform specific
    import msvcrt
else:
    import fcntl

#: How long to wait for another writer before giving up.
DEFAULT_TIMEOUT = 20.0

_POLL_SECONDS = 0.05

#: Warn at most once per process; a share without locking will hit this on
#: every single write otherwise.
_warned = False


class LockTimeout(TimeoutError):
    """Raised when another writer held the lock for longer than the timeout."""


def _acquire_once(handle) -> bool:
    """Try to take the lock without blocking. False if someone else holds it."""
    try:
        if sys.platform == "win32":  # pragma: no cover - platform specific
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _release(handle) -> None:
    try:
        if sys.platform == "win32":  # pragma: no cover - platform specific
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass  # Releasing a lock we no longer hold is not worth failing over.


def _unsupported(error: OSError) -> None:
    global _warned
    if not _warned:
        _warned = True
        warnings.warn(
            f"This filesystem does not support file locking ({error}). "
            "Concurrent writes from several machines are not fully protected; "
            "prefer running one shared server over pointing several copies of "
            "the app at the same folder.",
            RuntimeWarning,
            stacklevel=3,
        )


class FileLock:
    """Hold an exclusive lock on ``path`` for the duration of a ``with`` block.

    The lock file is separate from the document it guards, so taking the lock
    never truncates or touches the data, and a crash leaves nothing to clean up
    -- the operating system drops the lock when the process goes away.
    """

    def __init__(self, path: str | os.PathLike[str], *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.path = Path(path)
        self.timeout = timeout
        self._handle = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Opened read-write without truncating: Windows needs a writable
            # handle to lock a byte range, and the file must have that byte.
            self._handle = open(self.path, "a+b")
            if self._handle.tell() == 0:
                self._handle.write(b"\0")
                self._handle.flush()
            self._handle.seek(0)
        except OSError as error:
            _unsupported(error)
            self._handle = None
            return self

        deadline = time.monotonic() + self.timeout
        while True:
            try:
                if _acquire_once(self._handle):
                    return self
            except ValueError as error:  # pragma: no cover - odd platforms
                _unsupported(OSError(str(error)))
                return self

            if time.monotonic() >= deadline:
                self._close()
                raise LockTimeout(
                    f"another writer has held {self.path} for over "
                    f"{self.timeout:g}s; is a copy of the app stuck?"
                )
            time.sleep(_POLL_SECONDS)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._close()

    def _close(self) -> None:
        if self._handle is None:
            return
        _release(self._handle)
        try:
            self._handle.close()
        except OSError:
            pass
        self._handle = None
