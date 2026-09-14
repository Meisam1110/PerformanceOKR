"""Tests for what makes the portable, shared deployment work.

Three things carry the weight of "copy the folder to a shared drive and
everyone runs it":

* the ini file beside the app decides where data lives, so a shared copy
  points everyone at one workspace with no per-user setup;
* the file lock serialises writers across processes and machines, so two
  people saving at once cannot lose an edit;
* running without a console does not silence errors.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from okr_tracker import config, headless  # noqa: E402
from okr_tracker.filelock import FileLock, LockTimeout  # noqa: E402
from okr_tracker.storage import ConflictError, WorkspaceStore  # noqa: E402
from tests.test_backend import workspace  # noqa: E402


class ConfigFileTests(unittest.TestCase):
    """okr-tracker.ini is how a shared copy configures itself."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.original = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.original)))
        for key in list(os.environ):
            if key.startswith("OKR_"):
                del os.environ[key]

    def _load(self, text: str | None) -> object:
        """Reload the config module against a freshly written ini file."""
        path = self.root / config.CONFIG_NAME
        if text is None:
            path.unlink(missing_ok=True)
            os.environ.pop("OKR_CONFIG", None)
        else:
            path.write_text(text, encoding="utf-8")
            os.environ["OKR_CONFIG"] = str(path)
        return importlib.reload(config)

    def tearDown(self):
        os.environ.pop("OKR_CONFIG", None)
        importlib.reload(config)

    def test_reads_settings_from_the_ini_file(self):
        module = self._load(
            "[tracker]\nport = 9100\nhost = 0.0.0.0\npoll_seconds = 45\n"
            "backup_count = 7\nread_only = true\n"
        )
        settings = module.Settings()
        self.assertEqual(settings.port, 9100)
        self.assertEqual(settings.host, "0.0.0.0")
        self.assertEqual(settings.poll_seconds, 45)
        self.assertEqual(settings.backup_count, 7)
        self.assertTrue(settings.read_only)

    def test_relative_data_dir_stays_inside_the_app_folder(self):
        """The portable default: the workspace travels with the folder."""
        module = self._load("[tracker]\ndata_dir = data\n")
        expected = (module.app_dir() / "data").resolve()
        self.assertEqual(module.Settings().data_dir, expected)

    def test_absolute_data_dir_is_used_as_given(self):
        target = self.root / "elsewhere"
        module = self._load(f"[tracker]\ndata_dir = {target}\n")
        self.assertEqual(module.Settings().data_dir, target)

    def test_environment_overrides_the_file(self):
        os.environ["OKR_PORT"] = "9999"
        module = self._load("[tracker]\nport = 9100\n")
        self.assertEqual(module.Settings().port, 9999)

    def test_falls_back_to_the_per_user_location_without_a_data_dir(self):
        """Commenting out data_dir gives each person a private workspace."""
        module = self._load("[tracker]\nport = 9100\n")
        self.assertIn(module.APP_DIR_NAME, str(module.Settings().data_dir))

    def test_a_damaged_ini_does_not_stop_the_app(self):
        module = self._load("this is not an ini file at all {{{")
        self.assertEqual(module.Settings().port, 8765, "falls back to defaults")

    def test_no_ini_file_is_fine(self):
        module = self._load(None)
        self.assertEqual(module.Settings().port, 8765)


class FileLockTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "workspace.json.lock"

    def test_can_be_taken_and_released(self):
        with FileLock(self.path):
            pass
        with FileLock(self.path):
            pass

    def test_blocks_a_second_holder_until_the_first_releases(self):
        order: list[str] = []

        def second():
            with FileLock(self.path, timeout=10):
                order.append("second")

        with FileLock(self.path):
            worker = threading.Thread(target=second)
            worker.start()
            worker.join(timeout=0.5)
            order.append("first")
        worker.join(timeout=10)

        self.assertEqual(order, ["first", "second"], "the second writer must wait")

    def test_gives_up_rather_than_hanging_forever(self):
        blocker = FileLock(self.path)
        blocker.__enter__()
        self.addCleanup(blocker.__exit__, None, None, None)

        # The contender runs in a separate process, where the lock genuinely
        # competes rather than relying on same-process semantics.
        script = (
            f"import sys; sys.path.insert(0, {str(ROOT)!r});"
            "from okr_tracker.filelock import FileLock, LockTimeout;"
            f"\ntry:\n FileLock({str(self.path)!r}, timeout=0.3).__enter__()\n"
            " print('took')\nexcept LockTimeout:\n print('timeout')"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(result.stdout.strip(), "timeout", result.stderr)


class SharedFolderTests(unittest.TestCase):
    """The shared-drive deployment, exercised with real separate processes."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.workspace_path = Path(self.dir.name) / "workspace.json"
        WorkspaceStore(self.workspace_path).write(workspace("rev-0"), base_revision=None)

    def _contend(self, count: int) -> list[str]:
        script = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from tests.test_backend import workspace
from okr_tracker.storage import WorkspaceStore, ConflictError
store = WorkspaceStore({str(self.workspace_path)!r})
try:
    store.write(workspace("rev-winner-" + sys.argv[1]), base_revision="rev-0")
    print("won")
except ConflictError:
    print("lost")
"""
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", script, str(index)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for index in range(count)
        ]
        return [process.communicate(timeout=120)[0].strip() for process in processes]

    def test_only_one_of_many_machines_wins_a_simultaneous_save(self):
        results = self._contend(8)
        self.assertEqual(
            results.count("won"), 1,
            f"exactly one writer may win; got {results}",
        )
        self.assertEqual(results.count("lost"), 7)

    def test_the_losers_did_not_corrupt_the_document(self):
        self._contend(8)
        stored = json.loads(self.workspace_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["version"], 6)
        self.assertTrue(stored["revision"].startswith("rev-winner-"))

    def test_a_second_reader_sees_what_the_first_wrote(self):
        """Two app instances on one folder are looking at one workspace."""
        first = WorkspaceStore(self.workspace_path)
        second = WorkspaceStore(self.workspace_path)
        first.write(workspace("rev-shared"), base_revision="rev-0")
        self.assertEqual(second.read().revision, "rev-shared")

    def test_a_stale_writer_is_refused_not_merged(self):
        first = WorkspaceStore(self.workspace_path)
        second = WorkspaceStore(self.workspace_path)
        first.write(workspace("rev-a"), base_revision="rev-0")
        with self.assertRaises(ConflictError):
            second.write(workspace("rev-b"), base_revision="rev-0")


class HeadlessTests(unittest.TestCase):
    """pythonw.exe has no stdout; printing must not become a crash."""

    def test_detects_a_present_console(self):
        self.assertFalse(headless.headless())

    def test_capture_and_redirect_write_a_log(self):
        with tempfile.TemporaryDirectory() as workdir:
            data = Path(workdir)
            script = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
sys.stdout = None
sys.stderr = None
from okr_tracker import headless
from pathlib import Path
headless.capture()
print("a line before the log exists")
headless.redirect(Path({str(data)!r}))
print("a line after")
"""
            result = subprocess.run(
                [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            log = (data / headless.LOG_NAME).read_text(encoding="utf-8")
            self.assertIn("a line before the log exists", log)
            self.assertIn("a line after", log)

    def test_redirect_survives_an_unwritable_directory(self):
        script = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
sys.stdout = None
sys.stderr = None
from okr_tracker import headless
from pathlib import Path
headless.capture()
print("buffered")
headless.redirect(Path("/proc/nowhere/data"))
print("still fine")
"""
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_cli_runs_with_no_stdout_at_all(self):
        """The real entry point, started the way pythonw.exe starts it."""
        with tempfile.TemporaryDirectory() as workdir:
            script = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
sys.stdout = None
sys.stderr = None
from okr_tracker.__main__ import main
raise SystemExit(main(["where"]))
"""
            result = subprocess.run(
                [sys.executable, "-c", script],
                env={**os.environ, "OKR_DATA_DIR": workdir},
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            log = Path(workdir) / headless.LOG_NAME
            self.assertTrue(log.exists(), "output should have gone to the log file")
            self.assertIn("data directory", log.read_text(encoding="utf-8"))


class PortableBuildTests(unittest.TestCase):
    """The assembly step, without the network download."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import build_portable

        self.builder = build_portable
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.bundle = Path(self.dir.name) / "bundle"
        self.bundle.mkdir()

    def test_copies_the_whole_application_including_its_assets(self):
        self.builder.copy_application(self.bundle)
        package = self.bundle / "okr_tracker"
        self.assertTrue((package / "__main__.py").exists())
        self.assertTrue((package / "templates" / "index.html").exists())
        self.assertTrue((package / "static" / "js" / "app.js").exists())
        self.assertTrue((package / "static" / "art" / "gold.png").exists())
        self.assertFalse(
            list(package.rglob("__pycache__")), "caches should not be shipped"
        )

    def test_writes_a_launcher_that_avoids_the_unc_trap(self):
        self.builder.write_support_files(self.bundle)
        launcher = (self.bundle / "OKR Tracker.bat").read_text(encoding="utf-8")
        commands = [
            line for line in launcher.splitlines()
            if line.strip() and not line.strip().lower().startswith(("rem ", "rem\t"))
        ]
        self.assertTrue(any(line.startswith("pushd ") for line in commands))
        self.assertFalse(
            [line for line in commands if line.lstrip().lower().startswith("cd /d")],
            "cd /d silently fails on the UNC path of a network share",
        )
        self.assertIn("pythonw.exe", launcher, "no console window for the normal start")

    def test_ships_a_config_that_shares_the_workspace(self):
        self.builder.write_support_files(self.bundle)
        ini = (self.bundle / "okr-tracker.ini").read_text(encoding="utf-8")
        self.assertIn("data_dir = data", ini)
        self.assertTrue((self.bundle / "data").is_dir())

    def test_the_bundled_config_really_resolves_into_the_folder(self):
        """Read the shipped ini with the real config module, not by eye."""
        self.builder.write_support_files(self.bundle)
        self.builder.copy_application(self.bundle)
        script = f"""
import sys, os
sys.path.insert(0, {str(self.bundle)!r})
os.environ["OKR_CONFIG"] = {str(self.bundle / "okr-tracker.ini")!r}
for key in list(os.environ):
    if key.startswith("OKR_") and key != "OKR_CONFIG":
        del os.environ[key]
from okr_tracker.config import Settings
print(Settings().data_dir)
"""
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            Path(result.stdout.strip()), (self.bundle / "data").resolve(),
            "a copied folder must keep its data inside itself",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
