"""First-run dependency setup, so installing is just running the app.

The tracker needs Flask and waitress. Asking someone to create a virtual
environment and run pip before they can open their own OKRs is a poor trade for
two pure-Python packages, so on first run the launcher builds a private virtual
environment beside the application, installs the dependencies into it, and
restarts itself inside it. Every later run finds the environment already there
and starts immediately.

Nothing is installed into the system Python, and nothing happens at all when the
dependencies are already importable -- a system install, an activated virtual
environment, `uv run`, or a PyInstaller build all skip this entirely.

Set ``OKR_NO_BOOTSTRAP=1`` to turn it off and manage dependencies yourself.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: Set on the re-executed process so a failed install cannot loop forever.
REENTRY_FLAG = "OKR_BOOTSTRAP_REENTRY"

#: Opt out entirely (packagers, CI, anyone managing their own environment).
DISABLE_FLAG = "OKR_NO_BOOTSTRAP"

VENV_DIR = ".venv"

REQUIREMENTS = ("Flask>=3.0,<4.0", "waitress>=3.0,<4.0")


class BootstrapError(RuntimeError):
    """Raised when dependencies are missing and cannot be installed."""


def dependencies_available() -> bool:
    """True when the app can import everything it needs to serve."""
    try:
        import flask  # noqa: F401
        import waitress  # noqa: F401
    except ImportError:
        return False
    return True


def is_frozen() -> bool:
    """True inside a PyInstaller build, where dependencies are already bundled."""
    return getattr(sys, "frozen", False)


def venv_python(venv: Path) -> Path:
    """The interpreter inside ``venv``, by platform layout."""
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _run(command: list[str], *, step: str) -> None:
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as error:
        raise BootstrapError(f"could not {step}: {error}") from error
    except subprocess.CalledProcessError as error:
        raise BootstrapError(f"could not {step} (exit status {error.returncode})") from error


def environment_ready(interpreter: Path) -> bool:
    """True when ``interpreter`` can already import the runtime dependencies.

    Checked before installing so a warm start costs one fast subprocess rather
    than a pip run (and a network round trip) every time.
    """
    try:
        completed = subprocess.run(
            [str(interpreter), "-c", "import flask, waitress"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def create_venv(venv: Path) -> Path:
    """Create ``venv`` if it is not already usable, and return its interpreter."""
    interpreter = venv_python(venv)
    if interpreter.exists():
        return interpreter

    print(f"Setting up {venv} (first run only)...")
    # --copies avoids symlinks, which break when the folder is moved or synced.
    _run(
        [sys.executable, "-m", "venv", "--copies", str(venv)],
        step="create a virtual environment",
    )
    if not interpreter.exists():
        raise BootstrapError(f"virtual environment created but {interpreter} is missing")
    return interpreter


def install_dependencies(interpreter: Path, requirements: Path | None) -> None:
    """Install the runtime dependencies into ``interpreter``'s environment."""
    print("Installing Flask and waitress...")
    _run(
        [str(interpreter), "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
        step="update pip",
    )
    if requirements and requirements.exists():
        target = ["-r", str(requirements)]
    else:
        target = list(REQUIREMENTS)
    _run(
        [str(interpreter), "-m", "pip", "install", "--quiet", *target],
        step="install dependencies",
    )


def _guidance(root: Path) -> str:
    activate = (
        f"{root / VENV_DIR / 'Scripts' / 'activate.bat'}"
        if os.name == "nt"
        else f"source {root / VENV_DIR / 'bin' / 'activate'}"
    )
    return (
        "Install them yourself with:\n"
        f"    {sys.executable} -m venv {root / VENV_DIR}\n"
        f"    {activate}\n"
        f"    python -m pip install -r {root / 'requirements.txt'}\n"
        "    python run.py"
    )


def ensure_dependencies(root: Path, argv: list[str] | None = None) -> None:
    """Make Flask and waitress importable, restarting this process if needed.

    Returns normally when the current interpreter can already serve. Otherwise
    it builds ``<root>/.venv``, installs into it, and replaces this process with
    the same command running on that interpreter -- so this never returns.
    """
    if dependencies_available() or is_frozen():
        return

    if os.environ.get(DISABLE_FLAG):
        raise BootstrapError(
            "Flask and waitress are not installed, and "
            f"{DISABLE_FLAG} is set.\n\n{_guidance(root)}"
        )

    if os.environ.get(REENTRY_FLAG):
        # We already restarted once and the packages are still missing:
        # restarting again would just loop.
        raise BootstrapError(
            "dependencies are still missing after the automatic install.\n\n"
            f"{_guidance(root)}"
        )

    venv = root / VENV_DIR
    interpreter = create_venv(venv)
    if not environment_ready(interpreter):
        install_dependencies(interpreter, root / "requirements.txt")
        print("Starting...\n")

    command = [str(interpreter), *(argv if argv is not None else sys.argv)]
    environment = {**os.environ, REENTRY_FLAG: "1"}

    if os.name == "nt":
        # execv on Windows detaches from the console and loses Ctrl+C, so run
        # the child to completion and pass its exit status on.
        raise SystemExit(subprocess.run(command, env=environment).returncode)
    os.execve(interpreter, command, environment)
