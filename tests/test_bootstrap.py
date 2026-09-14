"""Tests for the first-run dependency setup.

The install path is the first thing a new user touches, so its decisions are
worth pinning down: when it does nothing, when it refuses, and that it really
can build a working environment from a Python that has no Flask.

The one test that runs pip is skipped unless ``OKR_TEST_BOOTSTRAP=1`` is set,
because it needs the network and takes a few seconds.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from okr_tracker import bootstrap  # noqa: E402


class DetectionTests(unittest.TestCase):
    def test_dependencies_are_available_in_this_environment(self):
        # The rest of the suite could not run otherwise, so this also documents
        # what dependencies_available() is actually asserting.
        self.assertTrue(bootstrap.dependencies_available())

    def test_venv_python_follows_the_platform_layout(self):
        venv = Path("/somewhere/.venv")
        interpreter = bootstrap.venv_python(venv)
        if os.name == "nt":
            self.assertEqual(interpreter, venv / "Scripts" / "python.exe")
        else:
            self.assertEqual(interpreter, venv / "bin" / "python")

    def test_environment_ready_is_false_for_a_missing_interpreter(self):
        self.assertFalse(bootstrap.environment_ready(Path("/nonexistent/python")))

    def test_environment_ready_is_true_for_this_interpreter(self):
        self.assertTrue(bootstrap.environment_ready(Path(sys.executable)))


class GuardTests(unittest.TestCase):
    """ensure_dependencies must not act when it should not."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)

    def test_does_nothing_when_dependencies_are_present(self):
        bootstrap.ensure_dependencies(self.root)
        self.assertFalse(
            (self.root / bootstrap.VENV_DIR).exists(),
            "no environment should be built when one is not needed",
        )

    def _without_dependencies(self):
        """Patch detection to report missing dependencies."""
        original = bootstrap.dependencies_available
        bootstrap.dependencies_available = lambda: False
        self.addCleanup(setattr, bootstrap, "dependencies_available", original)

    def _with_env(self, **values: str):
        original = {key: os.environ.get(key) for key in values}

        def restore():
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        os.environ.update(values)
        self.addCleanup(restore)

    def test_refuses_rather_than_installing_when_opted_out(self):
        self._without_dependencies()
        self._with_env(**{bootstrap.DISABLE_FLAG: "1"})
        with self.assertRaises(bootstrap.BootstrapError) as caught:
            bootstrap.ensure_dependencies(self.root)
        self.assertIn(bootstrap.DISABLE_FLAG, str(caught.exception))
        self.assertIn("requirements.txt", str(caught.exception))
        self.assertFalse((self.root / bootstrap.VENV_DIR).exists())

    def test_does_not_loop_when_the_restart_still_lacks_dependencies(self):
        self._without_dependencies()
        self._with_env(**{bootstrap.REENTRY_FLAG: "1"})
        with self.assertRaises(bootstrap.BootstrapError) as caught:
            bootstrap.ensure_dependencies(self.root)
        self.assertIn("still missing", str(caught.exception))
        self.assertFalse(
            (self.root / bootstrap.VENV_DIR).exists(),
            "a second attempt must not rebuild the environment",
        )

    def test_skips_entirely_in_a_frozen_build(self):
        self._without_dependencies()
        original = bootstrap.is_frozen
        bootstrap.is_frozen = lambda: True
        self.addCleanup(setattr, bootstrap, "is_frozen", original)
        bootstrap.ensure_dependencies(self.root)
        self.assertFalse((self.root / bootstrap.VENV_DIR).exists())

    def test_reports_a_failure_to_create_an_environment(self):
        with self.assertRaises(bootstrap.BootstrapError):
            # A path that cannot be created: venv has nowhere to write.
            bootstrap.create_venv(Path("/proc/nonexistent/.venv"))


@unittest.skipUnless(
    os.environ.get("OKR_TEST_BOOTSTRAP") == "1",
    "set OKR_TEST_BOOTSTRAP=1 to run the install test (needs network)",
)
class LiveInstallTests(unittest.TestCase):
    """The real thing: a Python with no Flask ends up serving the app."""

    def test_run_py_installs_and_starts_from_a_bare_interpreter(self):
        with tempfile.TemporaryDirectory() as workdir:
            data = Path(workdir) / "data"
            # Hide site-packages so run.py genuinely cannot import Flask, then
            # let it bootstrap and answer a CLI command from inside its venv.
            script = (
                "import sys;"
                "sys.path[:] = [p for p in sys.path if 'packages' not in p];"
                "sys.argv = ['run.py', 'where'];"
                "exec(compile(open('run.py').read(), 'run.py', 'exec'),"
                " {'__file__': 'run.py', '__name__': '__main__'})"
            )
            completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=str(ROOT),
                env={**os.environ, "OKR_DATA_DIR": str(data)},
                capture_output=True,
                text=True,
                timeout=600,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("data directory", completed.stdout)
            self.assertTrue(
                bootstrap.environment_ready(
                    bootstrap.venv_python(ROOT / bootstrap.VENV_DIR)
                ),
                "the bootstrapped environment must be able to serve",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
