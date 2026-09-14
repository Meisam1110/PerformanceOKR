"""End-to-end checks: the real application, in a real browser, on the backend.

The unit tests cover the server in isolation. What they cannot show is the part
that actually carries risk -- that the tracker's synchronous ``load()`` and
``persist()`` are satisfied by the ``okrStore`` shim, that a first run seeds
itself onto the server, and that a conflicting write from elsewhere reaches the
running page.

These need Playwright and a Chromium build::

    python -m pip install playwright && python -m playwright install chromium
    python tests/test_end_to_end.py

They skip themselves (exit 0) when Playwright or a browser is unavailable, so a
plain checkout without browser tooling still passes its test suite.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright
except ImportError:  # pragma: no cover - optional tooling
    sync_playwright = None
    PlaywrightError = Exception

from okr_tracker.app import create_app  # noqa: E402
from okr_tracker.config import Settings  # noqa: E402

#: Set when the bundled Chromium is not where this Playwright build expects it.
CHROME_PATH = os.environ.get("OKR_TEST_CHROMIUM")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class LiveServer:
    """The real WSGI app on a real port, in a background thread."""

    def __init__(self, data_dir: Path, **settings_overrides):
        self.port = free_port()
        self.settings = Settings(data_dir=data_dir, port=self.port, **settings_overrides)
        self.app = create_app(self.settings)
        self.url = f"http://127.0.0.1:{self.port}"
        self._server = None
        self._thread = None

    def __enter__(self) -> "LiveServer":
        from werkzeug.serving import make_server

        self._server = make_server("127.0.0.1", self.port, self.app, threaded=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        return self

    def __exit__(self, *exc_info) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)

    def workspace(self) -> dict | None:
        path = self.settings.workspace_path
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


def browser_available() -> str | None:
    """Return a skip reason, or None when a browser can actually be launched."""
    if sync_playwright is None:
        return "playwright is not installed"
    try:
        with sync_playwright() as play:
            launch = {"executable_path": CHROME_PATH} if CHROME_PATH else {}
            play.chromium.launch(**launch).close()
    except PlaywrightError as error:
        return f"chromium is unavailable: {str(error).splitlines()[0]}"
    return None


SKIP_REASON = browser_available()


@unittest.skipIf(SKIP_REASON, SKIP_REASON or "")
class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.play = sync_playwright().start()
        self.addCleanup(self.play.stop)
        launch = {"executable_path": CHROME_PATH} if CHROME_PATH else {}
        self.browser = self.play.chromium.launch(**launch)
        self.addCleanup(self.browser.close)

    def open_page(self, server: LiveServer):
        """Open the app and wait for it to finish its first render."""
        page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        self.errors: list[str] = []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.on(
            "console",
            lambda message: self.errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.goto(server.url, wait_until="networkidle")
        page.wait_for_selector("#app .entry-shell, #app .sidebar", timeout=15_000)
        return page

    def assert_no_page_errors(self):
        # Favicon/asset noise is not a failure; script errors are.
        real = [e for e in self.errors if "favicon" not in e.lower()]
        self.assertEqual(real, [], f"the page reported script errors: {real}")

    def test_first_run_seeds_the_workspace_onto_the_server(self):
        with LiveServer(Path(self.dir.name)) as server:
            self.assertIsNone(server.workspace(), "nothing stored before first visit")
            page = self.open_page(server)
            page.wait_for_function(
                "() => okrStore.pendingWrites === 0 && okrStore.getItem("
                "'pole.okr.workspace.v4') !== null",
                timeout=15_000,
            )
            page.wait_for_timeout(500)

            stored = server.workspace()
            self.assertIsNotNone(stored, "the first load must persist a workspace")
            self.assertEqual(stored["version"], 6)
            self.assertTrue(stored["revision"], "a stored workspace carries a revision")
            self.assertTrue(stored["objectives"], "the sample workspace has objectives")
            self.assert_no_page_errors()

    def test_reload_reads_the_workspace_back_from_the_server(self):
        with LiveServer(Path(self.dir.name)) as server:
            page = self.open_page(server)
            page.wait_for_function("() => okrStore.pendingWrites === 0", timeout=15_000)
            page.wait_for_timeout(500)
            first = server.workspace()
            page.close()

            # A second visit must adopt the stored document, not re-seed a new one.
            page = self.open_page(server)
            seeded = page.evaluate(
                "() => JSON.parse(okrStore.getItem('pole.okr.workspace.v4')).revision"
            )
            self.assertEqual(seeded, first["revision"])
            self.assertEqual(server.workspace()["revision"], first["revision"])
            self.assert_no_page_errors()

    def test_a_write_reaches_the_server_and_bumps_the_revision(self):
        with LiveServer(Path(self.dir.name)) as server:
            page = self.open_page(server)
            page.wait_for_function("() => okrStore.pendingWrites === 0", timeout=15_000)
            page.wait_for_timeout(500)
            before = server.workspace()

            # A direct store write, to isolate the shim's transport from the
            # application logic. test_signing_in_and_editing_persists_to_disk
            # covers the same path driven through the real interface.
            saved = page.evaluate(
                """() => {
                    const text = okrStore.getItem('pole.okr.workspace.v4');
                    const next = JSON.parse(text);
                    next.revision = 'rev-e2e-' + Date.now();
                    next.updatedAt = new Date().toISOString();
                    next.settings.riskDays = 21;
                    okrStore.setItem('pole.okr.workspace.v4', JSON.stringify(next));
                    return next.revision;
                }"""
            )
            page.wait_for_function("() => okrStore.pendingWrites === 0", timeout=10_000)
            page.wait_for_timeout(300)

            after = server.workspace()
            self.assertEqual(after["revision"], saved)
            self.assertNotEqual(after["revision"], before["revision"])
            self.assertEqual(after["settings"]["riskDays"], 21)
            self.assert_no_page_errors()

    def test_a_stale_write_is_rejected_and_the_page_resynchronises(self):
        with LiveServer(Path(self.dir.name)) as server:
            page = self.open_page(server)
            page.wait_for_function("() => okrStore.pendingWrites === 0", timeout=15_000)
            page.wait_for_timeout(500)

            # Somebody else saves first, straight against the API.
            current = server.workspace()
            other = dict(current, revision="rev-from-another-user", updatedAt="2026-02-02T00:00:00Z")
            import urllib.request

            request = urllib.request.Request(
                f"{server.url}/api/workspace",
                data=json.dumps({"document": other, "baseRevision": current["revision"]}).encode(),
                headers={"Content-Type": "application/json"},
                method="PUT",
            )
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 200)

            # The page still believes it holds the latest revision; its write
            # must lose, and it must pick up the other user's document.
            page.evaluate(
                """() => {
                    const next = JSON.parse(okrStore.getItem('pole.okr.workspace.v4'));
                    next.revision = 'rev-should-lose';
                    okrStore.setItem('pole.okr.workspace.v4', JSON.stringify(next));
                }"""
            )
            page.wait_for_function("() => okrStore.pendingWrites === 0", timeout=10_000)
            page.wait_for_function(
                "() => JSON.parse(okrStore.getItem('pole.okr.workspace.v4')).revision"
                " === 'rev-from-another-user'",
                timeout=10_000,
            )

            self.assertEqual(
                server.workspace()["revision"],
                "rev-from-another-user",
                "a losing write must not overwrite the winner",
            )

    def test_a_burst_of_writes_chains_instead_of_racing(self):
        with LiveServer(Path(self.dir.name)) as server:
            page = self.open_page(server)
            page.wait_for_function("() => okrStore.pendingWrites === 0", timeout=15_000)

            last = page.evaluate(
                """() => {
                    let revision;
                    for (let i = 0; i < 5; i += 1) {
                        const next = JSON.parse(okrStore.getItem('pole.okr.workspace.v4'));
                        next.revision = revision = 'rev-burst-' + i;
                        next.settings.riskDays = 10 + i;
                        okrStore.setItem('pole.okr.workspace.v4', JSON.stringify(next));
                    }
                    return revision;
                }"""
            )
            page.wait_for_function("() => okrStore.pendingWrites === 0", timeout=15_000)
            page.wait_for_timeout(400)

            stored = server.workspace()
            self.assertEqual(stored["revision"], last, "the last write must win")
            self.assertEqual(stored["settings"]["riskDays"], 14)
            self.assert_no_page_errors()

    def test_the_session_keeps_saving_after_the_server_is_unreachable(self):
        """An unreachable server must not silently break every later save.

        A failed write leaves the page's document ahead of the server's, so
        without a fallback every subsequent write would be based on a revision
        the server never saw -- and would fail too, with no sign to the user.
        """
        with LiveServer(Path(self.dir.name)) as server:
            page = self.open_page(server)
            page.wait_for_function("() => okrStore.pendingWrites === 0", timeout=15_000)
            page.wait_for_timeout(500)
            confirmed = server.workspace()["revision"]

            page.route("**/api/workspace", lambda route: route.abort())
            page.evaluate(
                """() => {
                    const next = JSON.parse(okrStore.getItem('pole.okr.workspace.v4'));
                    next.revision = 'rev-while-offline';
                    okrStore.setItem('pole.okr.workspace.v4', JSON.stringify(next));
                }"""
            )
            page.wait_for_function("() => okrStore.pendingWrites === 0", timeout=20_000)
            page.wait_for_selector("#okrBackendStatus[data-tone=warn]", timeout=5_000)
            self.assertIn("Offline", page.inner_text("#okrBackendStatus"))
            self.assertEqual(
                server.workspace()["revision"], confirmed, "nothing reached the server"
            )
            self.assertEqual(
                page.evaluate(
                    "() => JSON.parse(okrStore.getItem('pole.okr.workspace.v4')).revision"
                ),
                confirmed,
                "the page must fall back to the last confirmed document",
            )

            # Back online, the very next edit has to save.
            page.unroute("**/api/workspace")
            page.evaluate(
                """() => {
                    const next = JSON.parse(okrStore.getItem('pole.okr.workspace.v4'));
                    next.revision = 'rev-after-recovery';
                    okrStore.setItem('pole.okr.workspace.v4', JSON.stringify(next));
                }"""
            )
            page.wait_for_function("() => okrStore.pendingWrites === 0", timeout=15_000)
            page.wait_for_timeout(400)
            self.assertEqual(server.workspace()["revision"], "rev-after-recovery")

    def test_read_only_mode_refuses_the_write_and_tells_the_user(self):
        with LiveServer(Path(self.dir.name)) as seeding:
            page = self.open_page(seeding)
            page.wait_for_function("() => okrStore.pendingWrites === 0", timeout=15_000)
            page.wait_for_timeout(500)
            page.close()

        with LiveServer(Path(self.dir.name), read_only=True) as server:
            before = server.workspace()
            page = self.open_page(server)
            page.evaluate(
                """() => {
                    const next = JSON.parse(okrStore.getItem('pole.okr.workspace.v4'));
                    next.revision = 'rev-read-only-attempt';
                    okrStore.setItem('pole.okr.workspace.v4', JSON.stringify(next));
                }"""
            )
            page.wait_for_function("() => okrStore.pendingWrites === 0", timeout=10_000)
            page.wait_for_selector("#okrBackendStatus[data-tone=warn]", timeout=5_000)
            self.assertIn("Read-only", page.inner_text("#okrBackendStatus"))
            self.assertEqual(server.workspace()["revision"], before["revision"])

    def test_signing_in_and_editing_persists_to_disk(self):
        """The whole stack, driven the way a person drives it.

        Signs in through the app's own entry screen with the administrator's
        initial password, changes a tracking preference, and checks the change
        reached the server's workspace file.
        """
        with LiveServer(Path(self.dir.name)) as server:
            page = self.open_page(server)
            page.wait_for_function("() => okrStore.pendingWrites === 0", timeout=15_000)

            page.fill("#entryPassword", "PoleAdmin!2026")
            page.click('form[data-kind="sign-in"] button[type="submit"]')
            page.wait_for_selector("#app .sidebar", timeout=15_000)

            page.click('.nav-btn[data-view="admin"]')
            page.click('[data-action="admin-tab"][data-tab="preferences"]')
            page.wait_for_selector("#riskDays", timeout=10_000)
            page.fill("#riskDays", "21")
            page.click('form[data-kind="settings"] button[type="submit"]')

            page.wait_for_function("() => okrStore.pendingWrites === 0", timeout=10_000)
            page.wait_for_timeout(400)

            stored = server.workspace()
            self.assertEqual(
                stored["settings"]["riskDays"], 21,
                "an edit made in the interface must reach the server",
            )
            self.assertTrue(
                any(event.get("key") for event in stored["audit"]),
                "the workspace audit trail is persisted too",
            )
            self.assert_no_page_errors()

    def test_a_password_set_from_python_is_accepted_by_the_browser(self):
        """Proof that okr_tracker.auth derives the same hash WebCrypto does.

        The CLI's reset-password is only useful if the browser accepts what
        Python wrote, so this signs in with a password that never existed on
        the client side.
        """
        from okr_tracker import auth
        from okr_tracker.storage import WorkspaceStore

        with LiveServer(Path(self.dir.name)) as server:
            page = self.open_page(server)
            page.wait_for_function("() => okrStore.pendingWrites === 0", timeout=15_000)
            page.wait_for_timeout(500)
            page.close()

            store = WorkspaceStore(server.settings.workspace_path)
            document = store.read().data
            admin = next(p for p in document["profiles"] if p["role"] == "admin")
            admin["auth"] = auth.create_auth("SetFromPython!2026", now="2026-01-01T00:00:00Z")
            document["revision"] = "rev-password-from-python"
            store.overwrite(document)

        with LiveServer(Path(self.dir.name)) as server:
            page = self.open_page(server)
            page.fill("#entryPassword", "PoleAdmin!2026")
            page.click('form[data-kind="sign-in"] button[type="submit"]')
            page.wait_for_timeout(2_000)
            self.assertEqual(
                page.locator("#app .sidebar").count(), 0,
                "the replaced password must no longer work",
            )

            page.fill("#entryPassword", "SetFromPython!2026")
            page.click('form[data-kind="sign-in"] button[type="submit"]')
            page.wait_for_selector("#app .sidebar", timeout=15_000)

    def test_the_application_renders_its_real_interface(self):
        """A smoke test that the extracted CSS, JS and artwork all still load."""
        with LiveServer(Path(self.dir.name)) as server:
            page = self.open_page(server)
            page.wait_for_timeout(800)

            self.assertTrue(page.locator("#app").inner_text().strip())
            # The stylesheet is really applied, not just requested.
            background = page.evaluate(
                "() => getComputedStyle(document.body).backgroundColor"
            )
            self.assertNotIn(background, ("", "rgba(0, 0, 0, 0)"))
            # Artwork resolves to a served file rather than a data: URL.
            art = page.evaluate("() => OKR_BACKEND.art.gold")
            self.assertIn("/static/art/gold.png", art)
            self.assertEqual(
                page.request.get(server.url + art).status, 200, "artwork must be served"
            )
            self.assert_no_page_errors()


if __name__ == "__main__":
    if SKIP_REASON:
        print(f"end-to-end tests skipped: {SKIP_REASON}")
    unittest.main(verbosity=2)
