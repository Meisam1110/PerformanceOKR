"""Tests for the storage, schema, auth and API layers.

Run with::

    python -m pytest          # if pytest is installed
    python tests/test_backend.py   # plain stdlib runner, no extra dependency
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from okr_tracker import auth  # noqa: E402
from okr_tracker.app import create_app  # noqa: E402
from okr_tracker.config import Settings  # noqa: E402
from okr_tracker.schema import ValidationError, validate  # noqa: E402
from okr_tracker.storage import ConflictError, WorkspaceStore  # noqa: E402


def workspace(revision: str = "rev-1", **overrides: object) -> dict:
    """A minimal document that satisfies the server's structural checks."""
    document = {
        "version": 6,
        "revision": revision,
        "updatedAt": "2026-01-01T00:00:00.000Z",
        "workspaceMode": "work",
        "owners": [{"id": "own-1", "name": {"en": "Ada", "fa": "آدا"}, "teamId": "eng"}],
        "profiles": [
            {"id": "pro-1", "name": {"en": "Admin", "fa": "مدیر"}, "role": "admin",
             "teamId": "", "auth": None}
        ],
        "objectives": [
            {"id": "obj-1", "title": {"en": "Ship it", "fa": ""}, "teamId": "eng",
             "ownerId": "own-1", "startDate": "2026-01-01", "currentEndDate": "2026-03-31",
             "originalEndDate": "2026-03-31", "extensions": [],
             "krs": [{"id": "kr-1", "title": {"en": "Alpha", "fa": ""}, "ownerId": "own-1",
                      "weight": 100, "progress": 0, "type": "percent", "extensions": []}]}
        ],
        "relations": [],
        "audit": [],
        "settings": {"riskDays": 14, "linearRisk": True, "riskTolerance": 15,
                     "riskProgressThreshold": 80},
    }
    document.update(overrides)
    return document


class SchemaTests(unittest.TestCase):
    def test_accepts_a_well_formed_workspace(self):
        document = workspace()
        self.assertIs(validate(document), document)

    def test_rejects_a_future_schema_version(self):
        with self.assertRaisesRegex(ValidationError, "newer than this server"):
            validate(workspace(version=7))

    def test_rejects_an_unmigrated_older_version(self):
        with self.assertRaisesRegex(ValidationError, "must be migrated"):
            validate(workspace(version=5))

    def test_rejects_duplicate_record_ids(self):
        document = workspace()
        document["owners"].append(dict(document["owners"][0]))
        with self.assertRaisesRegex(ValidationError, "duplicate record ids"):
            validate(document)

    def test_rejects_a_workspace_with_no_admin(self):
        document = workspace()
        document["profiles"][0]["role"] = "viewer"
        with self.assertRaisesRegex(ValidationError, "admin profile"):
            validate(document)

    def test_rejects_a_missing_revision(self):
        with self.assertRaisesRegex(ValidationError, "revision"):
            validate(workspace(revision=""))

    def test_rejects_things_that_are_not_workspaces(self):
        for bad in (None, [], "{}", 5, {"version": 6}):
            with self.assertRaises(ValidationError):
                validate(bad)


class StorageTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = WorkspaceStore(Path(self.dir.name) / "workspace.json", backup_count=3)

    def test_reads_empty_before_anything_is_written(self):
        document = self.store.read()
        self.assertFalse(document.exists)
        self.assertEqual(document.revision, "")

    def test_first_write_requires_no_base_revision(self):
        saved = self.store.write(workspace("rev-a"), base_revision=None)
        self.assertEqual(saved.revision, "rev-a")
        self.assertEqual(self.store.read().data["revision"], "rev-a")

    def test_write_rejects_a_stale_base_revision(self):
        self.store.write(workspace("rev-a"), base_revision=None)
        with self.assertRaises(ConflictError) as caught:
            self.store.write(workspace("rev-c"), base_revision="rev-b")
        self.assertEqual(caught.exception.current.revision, "rev-a")
        self.assertEqual(self.store.read().revision, "rev-a", "a conflict must not write")

    def test_sequential_writes_chain_by_revision(self):
        self.store.write(workspace("rev-a"), base_revision=None)
        self.store.write(workspace("rev-b"), base_revision="rev-a")
        self.assertEqual(self.store.read().revision, "rev-b")

    def test_each_write_backs_up_the_outgoing_document(self):
        self.store.write(workspace("rev-a"), base_revision=None)
        self.store.write(workspace("rev-b"), base_revision="rev-a")
        backups = self.store.backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text())["revision"], "rev-a")

    def test_backups_are_pruned_to_the_retention_limit(self):
        previous = None
        for index in range(8):
            revision = f"rev-{index}"
            self.store.write(workspace(revision), base_revision=previous)
            previous = revision
        self.assertEqual(len(self.store.backups()), 3)

    def test_corrupt_workspace_is_reported_not_swallowed(self):
        self.store.path.write_text("{ not json", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "not valid JSON"):
            self.store.read()

    def test_concurrent_writers_do_not_interleave(self):
        import threading

        self.store.write(workspace("rev-0"), base_revision=None)
        wins, losses = [], []

        def attempt(index: int) -> None:
            try:
                self.store.write(workspace(f"rev-{index}"), base_revision="rev-0")
                wins.append(index)
            except ConflictError:
                losses.append(index)

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(1, 9)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(wins), 1, "exactly one writer may win a compare-and-swap")
        self.assertEqual(len(losses), 7)


class AuthTests(unittest.TestCase):
    def test_round_trips_a_password(self):
        block = auth.create_auth("correct horse battery staple")
        self.assertTrue(auth.valid_auth(block))
        self.assertTrue(auth.verify_password(block, "correct horse battery staple"))
        self.assertFalse(auth.verify_password(block, "wrong"))

    def test_matches_the_browser_derivation(self):
        # PBKDF2-HMAC-SHA256, 120000 iterations, 32-byte key, base64 -- the same
        # parameters crypto.subtle.deriveBits uses in the application script.
        salt = "AAAAAAAAAAAAAAAAAAAAAA=="
        expected = auth.password_hash("hunter2", salt, 120_000)
        self.assertEqual(len(expected), 44)
        self.assertTrue(expected.endswith("="))
        block = {"algorithm": "PBKDF2-SHA256", "iterations": 120_000,
                 "salt": salt, "hash": expected}
        self.assertTrue(auth.verify_password(block, "hunter2"))

    def test_rejects_malformed_auth_blocks(self):
        self.assertFalse(auth.valid_auth(None))
        self.assertFalse(auth.valid_auth({"algorithm": "sha1"}))
        self.assertFalse(auth.valid_auth({**auth.create_auth("x"), "iterations": 10}))


class ApiTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.settings = Settings(data_dir=Path(self.dir.name), passphrase=None)
        self.app = create_app(self.settings, TESTING=True)
        self.client = self.app.test_client()

    def put(self, document, base_revision):
        return self.client.put(
            "/api/workspace",
            json={"document": document, "baseRevision": base_revision},
        )

    def test_serves_a_null_workspace_before_first_use(self):
        response = self.client.get("/api/workspace")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json["document"])
        self.assertEqual(response.json["revision"], "")

    def test_save_then_read_back(self):
        self.assertEqual(self.put(workspace("rev-a"), None).status_code, 200)
        response = self.client.get("/api/workspace")
        self.assertEqual(response.json["revision"], "rev-a")
        self.assertEqual(response.json["document"]["objectives"][0]["id"], "obj-1")

    def test_conflicting_save_returns_409_with_the_current_document(self):
        self.put(workspace("rev-a"), None)
        response = self.put(workspace("rev-x"), "rev-stale")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["revision"], "rev-a")
        self.assertEqual(response.json["document"]["revision"], "rev-a")

    def test_invalid_document_is_refused_with_422(self):
        response = self.put({"version": 99}, None)
        self.assertEqual(response.status_code, 422)
        self.assertIn("error", response.json)

    def test_malformed_body_is_refused_with_400(self):
        self.assertEqual(self.client.put("/api/workspace", json={}).status_code, 400)
        self.assertEqual(
            self.put(workspace(), 42).status_code, 400, "baseRevision must be a string"
        )

    def test_index_seeds_the_workspace_into_the_page(self):
        self.put(workspace("rev-a"), None)
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="okrBootstrap"', page)
        self.assertIn("rev-a", page)
        self.assertIn("backend-store.js", page)
        self.assertIn("no-store", self.client.get("/").headers["Cache-Control"])

    def test_bootstrap_carries_the_api_and_artwork_urls(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn('"workspace": "/api/workspace"', page)
        self.assertIn("/static/art/gold.png", page)

    def test_seeded_json_cannot_break_out_of_the_script_tag(self):
        document = workspace("rev-a")
        document["objectives"][0]["title"]["en"] = "</script><script>alert(1)</script>"
        self.put(document, None)
        page = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)", page)
        self.assertIn("\\u003c/script", page)

    def test_export_round_trips_through_import(self):
        self.put(workspace("rev-a"), None)
        exported = self.client.get("/api/workspace/export")
        self.assertEqual(exported.status_code, 200)
        self.assertIn("attachment", exported.headers["Content-Disposition"])

        document = json.loads(exported.get_data(as_text=True))
        document["revision"] = "rev-imported"
        response = self.client.post("/api/workspace/import", json={"document": document})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/workspace").json["revision"], "rev-imported")

    def test_export_before_first_use_is_404(self):
        self.assertEqual(self.client.get("/api/workspace/export").status_code, 404)

    def test_health_reports_the_stored_workspace(self):
        self.put(workspace("rev-a"), None)
        body = self.client.get("/api/health").json
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["workspace"]["exists"])
        self.assertEqual(body["workspace"]["objectives"], 1)


class ReadOnlyTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        settings = Settings(data_dir=Path(self.dir.name), read_only=True)
        self.client = create_app(settings, TESTING=True).test_client()

    def test_reads_are_allowed(self):
        self.assertEqual(self.client.get("/api/workspace").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_writes_are_refused(self):
        response = self.client.put(
            "/api/workspace", json={"document": workspace(), "baseRevision": None}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self.client.post("/api/workspace/import", json=workspace()).status_code, 403
        )


class PassphraseGateTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        settings = Settings(data_dir=Path(self.dir.name), passphrase="open sesame")
        self.client = create_app(settings, TESTING=True).test_client()

    def test_page_redirects_to_the_gate(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/gate", response.headers["Location"])

    def test_api_answers_401_in_json(self):
        response = self.client.get("/api/workspace")
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.json)

    def test_wrong_passphrase_is_refused(self):
        response = self.client.post("/gate", data={"passphrase": "guess"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("not accepted", response.get_data(as_text=True))

    def test_correct_passphrase_opens_the_app(self):
        response = self.client.post("/gate", data={"passphrase": "open sesame"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/api/workspace").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_gate_only_redirects_to_local_paths(self):
        response = self.client.post(
            "/gate?next=https://example.com/phish", data={"passphrase": "open sesame"}
        )
        self.assertNotIn("example.com", response.headers["Location"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
