# POLE OKR Tracker

The POLE OKR workspace, packaged as a downloadable application with a Python
backend. The interface is the original tracker; what changed is where the data
lives. Instead of a 6.5 MB HTML file keeping everything in one browser's
`localStorage`, the workspace is a JSON document owned by a local Flask server —
so it survives a cleared cache, is backed up on every save, can be opened from
more than one browser or machine, and can be managed from a terminal.

![Overview](docs/overview.png)

## Run it

```bash
python -m pip install -r requirements.txt
python run.py
```

The app starts on <http://127.0.0.1:8765> and opens in your browser. The first
run creates a sample workspace; sign in as **POLE administrator** with the
initial password shown on the entry screen, then change it under
*Administration → Working profiles*.

Nothing else needs installing: Flask and waitress are the only dependencies, and
both are pure Python.

## Where your data goes

| What | Where |
| --- | --- |
| Workspace | `<data dir>/workspace.json` |
| Backups | `<data dir>/backups/` (the last 30 saves) |
| Session key | `<data dir>/secret.key` |

`<data dir>` follows each platform's convention — `~/.local/share/pole-okr-tracker`
on Linux, `~/Library/Application Support/pole-okr-tracker` on macOS,
`%APPDATA%\pole-okr-tracker` on Windows. `python -m okr_tracker where` prints the
exact paths and a summary of what is stored. Override it with `--data-dir` or
`OKR_DATA_DIR` — pointing it at a synced folder is a reasonable way to carry a
workspace between machines.

The data directory is deliberately outside the application, so upgrading or
replacing the app never touches a workspace.

## Command line

```bash
python -m okr_tracker                    # serve (same as run.py)
python -m okr_tracker where              # data locations and workspace summary
python -m okr_tracker profiles           # profiles and whether passwords are still initial
python -m okr_tracker export backup.json # write the workspace out
python -m okr_tracker import backup.json # replace it (the outgoing one is backed up)
python -m okr_tracker reset-password --profile "POLE administrator"
```

`reset-password` exists because profile passwords are hashed inside the
workspace document; if the administrator password is lost there is otherwise no
way back in. It writes the same PBKDF2-SHA256 block the browser writes, which
the end-to-end tests verify by signing in with a password only Python has seen.

### Serving options

```bash
python run.py --port 9000
python run.py --no-browser
python run.py --read-only                      # serve a workspace nobody can change
python run.py --host 0.0.0.0 --passphrase "…"  # share it on your network
```

Every flag has an environment variable: `OKR_HOST`, `OKR_PORT`, `OKR_DATA_DIR`,
`OKR_READ_ONLY`, `OKR_PASSPHRASE`, `OKR_POLL_SECONDS`, `OKR_BACKUP_COUNT`,
`OKR_SECRET_KEY`.

## Sharing a workspace

Bind to a reachable interface and set a passphrase:

```bash
python run.py --host 0.0.0.0 --passphrase "something long"
```

Everyone who opens the address gets the same workspace. Concurrent edits are
safe: each save carries the revision it was based on, and the server refuses one
built on a revision it has moved past. The losing browser picks up the winning
document and shows the tracker's own "someone else saved newer data" notice
rather than overwriting the change. Browsers also poll every 15 seconds
(`OKR_POLL_SECONDS`, `0` disables it) so a second tab notices edits without a
reload.

**Understand the trust model before you do this.** The passphrase is a real
boundary — without it nothing is served. The *in-app* roles (admin, editor,
viewer) are not: they are enforced in the browser, and the API accepts any valid
workspace from anyone past the passphrase. That is fine for a team that trusts
each other and wants an audit trail of who changed what; it is not a substitute
for per-user server accounts. Run it on a trusted network, and use
`--read-only` when you only want to publish a view.

## HTTP API

All routes are JSON, on the same origin as the app.

| Route | Purpose |
| --- | --- |
| `GET /api/health` | server state and workspace summary |
| `GET /api/workspace` | `{document, revision}`; `document` is `null` before first use |
| `PUT /api/workspace` | `{document, baseRevision}` → `200`, or `409` with the current document |
| `GET /api/workspace/export` | download the workspace as a JSON file |
| `POST /api/workspace/import` | replace the workspace wholesale |
| `GET /api/backups` | retained backups, newest first |

A `PUT` whose `baseRevision` does not match what is stored writes nothing and
returns `409` together with the document that won, which is exactly what the
browser needs to resynchronise.

## How it fits together

The tracker's storage calls are synchronous — `load()` runs during the first
paint and `persist()` expects an answer immediately — so the backend is reached
through a shim rather than by rewriting the application:

```
okr_tracker/static/js/app.js          the original tracker, logic untouched
       │  localStorage.getItem/setItem  →  okrStore.getItem/setItem
       ▼
okr_tracker/static/js/backend-store.js  in-memory mirror + background sync
       │  GET/PUT /api/workspace
       ▼
okr_tracker/api.py → storage.py         compare-and-swap, atomic write, backup
       ▼
<data dir>/workspace.json
```

The shim keeps a mirror of the document so reads answer instantly, seeded by the
server into the page itself (so the very first `load()` has real data, with no
round trip). Writes update the mirror and go to the server in a serialised
queue. When the server rejects a write, or a poll finds a newer revision, the
shim adopts the server's document and fires a `storage` event — the same signal
the tracker already listens for to handle a second browser tab. Conflicts
between two users therefore take a code path the application already had.

`tools/build_frontend.py` is the build step. It splits the single-file HTML into
`static/css/app.css`, `static/js/app.js`, three artwork PNGs and a template;
redirects the 12 `localStorage` call sites to `okrStore`; and corrects the five
interface strings that claimed data lives in the browser. Re-run it after
dropping a newer tracker build into `vendor/`:

```bash
python tools/build_frontend.py
```

Pulling the artwork out of the HTML is also what makes the app quick to load.
Against the original 6.5 MB single file, a page load now costs ~310 KB of HTML,
CSS and JavaScript plus the workspace itself; the 4.7 MB of PNGs are separate
files, so the browser caches them and fetches only the one the current theme
uses.

## Building a standalone executable

For people who should not have to install Python at all:

```bash
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller packaging/okr-tracker.spec
```

`dist/okr-tracker` (`.exe` on Windows) is a single file that runs the server and
opens the browser. PyInstaller does not cross-compile, so build on the platform
you are shipping to. Workspaces still live in the user's data directory, so the
executable can be replaced freely.

## Tests

```bash
python tests/run_all.py
```

36 backend tests cover the store's compare-and-swap, atomic writes, backup
retention, concurrent writers, schema validation, the API, read-only mode and
the passphrase gate.

10 end-to-end tests drive the real application in Chromium against a real
server: first-run seeding, reload, a UI-driven sign-in and edit reaching disk, a
rejected stale write, a burst of rapid writes, recovery after the server becomes
unreachable, read-only refusal, and the Python/WebCrypto password compatibility
described above. They need Playwright and skip themselves cleanly when it is
unavailable:

```bash
python -m pip install playwright && python -m playwright install chromium
```

## Layout

```
run.py                     launcher
requirements.txt
pyproject.toml             installs an `okr-tracker` command
okr_tracker/
  __main__.py              CLI: serve, where, profiles, export, import, reset-password
  app.py                   Flask factory, page route, passphrase gate
  api.py                   JSON API
  storage.py               atomic JSON document store with backups
  schema.py                structural validation
  auth.py                  PBKDF2 (browser-compatible) and the server gate
  config.py                settings and per-platform data directory
  static/, templates/      built by tools/build_frontend.py
tools/build_frontend.py    the build step
packaging/okr-tracker.spec PyInstaller spec
vendor/                    the original single-file HTML, kept as the build input
tests/
```
