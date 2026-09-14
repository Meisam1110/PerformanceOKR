"""Flask application factory for the OKR Tracker."""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from flask import (
    Flask, Response, redirect, render_template, render_template_string,
    request, session, url_for,
)
from markupsafe import Markup

from . import auth
from .api import api
from .config import Settings
from .storage import WorkspaceStore

#: The key the application script passes to okrStore for the workspace. It is
#: the original localStorage key, kept so an existing single-file workspace in
#: the same browser is still recognised by the legacy-import path.
WORKSPACE_KEY = "pole.okr.workspace.v4"


def package_root() -> Path:
    """Where the templates and static files actually live.

    In a PyInstaller bundle the package is unpacked under ``sys._MEIPASS``
    rather than sitting next to this module, and Flask needs absolute paths to
    find the interface at all.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "okr_tracker"
    return Path(__file__).resolve().parent


ART_MANIFEST = package_root() / "static" / "art" / "manifest.json"


@lru_cache(maxsize=1)
def art_manifest() -> dict[str, str]:
    """Artwork name -> filename, written by tools/build_frontend.py."""
    try:
        return json.loads(ART_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def script_json(payload: object) -> Markup:
    """Serialise for a ``<script>`` data block.

    Escaping ``<``, ``>`` and ``&`` means no value in the workspace can close
    the element early, whatever a user typed into an objective title.
    """
    return Markup(
        json.dumps(payload, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


GATE_TEMPLATE = """<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in &middot; POLE OKR Tracker</title>
<style>
 :root{color-scheme:dark;--bg:#08111f;--surface:#101c2e;--text:#e8eefb;--muted:#9fb0c9;
  --line:#25344b;--accent:#ffd75e;--danger:#ff98a5}
 body{margin:0;min-height:100dvh;display:grid;place-items:center;background:var(--bg);
  color:var(--text);font:15px/1.6 system-ui,-apple-system,Segoe UI,sans-serif;padding:24px}
 form{width:min(400px,100%);background:var(--surface);border:1px solid var(--line);
  border-radius:20px;padding:32px}
 h1{margin:0 0 8px;font-size:22px}
 p{margin:0 0 24px;color:var(--muted);font-size:14px}
 label{display:block;margin-bottom:8px;font-size:14px;font-weight:600}
 input{width:100%;box-sizing:border-box;min-height:48px;padding:0 14px;border-radius:12px;
  border:1px solid var(--line);background:var(--bg);color:var(--text);font:inherit}
 button{margin-top:20px;width:100%;min-height:48px;border:0;border-radius:12px;
  background:var(--accent);color:#241c00;font:inherit;font-weight:700;cursor:pointer}
 .error{margin:16px 0 0;color:var(--danger);font-size:14px}
</style>
</head>
<body>
<form method="post">
 <h1>POLE OKR Tracker</h1>
 <p>This server is protected by a passphrase.</p>
 <label for="passphrase">Server passphrase</label>
 <input id="passphrase" name="passphrase" type="password" autocomplete="current-password"
        autofocus required>
 <button type="submit">Sign in</button>
 {% if error %}<p class="error" role="alert">{{ error }}</p>{% endif %}
</form>
</body>
</html>
"""


def create_app(settings: Settings | None = None, **overrides: Any) -> Flask:
    """Build the application. ``overrides`` set Flask config directly (tests)."""
    settings = settings or Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    root = package_root()
    app = Flask(
        __name__,
        template_folder=str(root / "templates"),
        static_folder=str(root / "static"),
    )
    app.config.update(
        SECRET_KEY=settings.resolved_secret_key(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        JSON_SORT_KEYS=False,
        OKR_READ_ONLY=settings.read_only,
        OKR_POLL_SECONDS=settings.poll_seconds,
        OKR_PASSPHRASE_HASH=(
            auth.make_gate_secret(settings.passphrase) if settings.passphrase else None
        ),
    )
    app.config.update(overrides)

    app.extensions["okr_store"] = WorkspaceStore(
        settings.workspace_path, backup_count=settings.backup_count
    )
    app.extensions["okr_settings"] = settings
    app.register_blueprint(api)

    @app.before_request
    def require_gate() -> Response | None:
        """Send anonymous visitors to the gate when a passphrase is set.

        The API blueprint runs its own check and answers 401 in JSON; this one
        only covers the page routes, so a browser gets a form rather than a
        redirect it cannot act on.
        """
        if not app.config.get("OKR_PASSPHRASE_HASH"):
            return None
        if session.get("okr_gate") is True or request.endpoint in {"gate", "static"}:
            return None
        if request.blueprint == "api":
            return None
        return redirect(url_for("gate", next=request.path))

    @app.route("/gate", methods=["GET", "POST"])
    def gate() -> Response | str:
        if not app.config.get("OKR_PASSPHRASE_HASH"):
            return redirect(url_for("index"))
        error = None
        if request.method == "POST":
            if auth.check_gate(app.config, request.form.get("passphrase", "")):
                session["okr_gate"] = True
                session.permanent = True
                target = request.args.get("next") or url_for("index")
                # Only ever bounce back to a path on this server.
                if not target.startswith("/") or target.startswith("//"):
                    target = url_for("index")
                return redirect(target)
            error = "That passphrase was not accepted."
        return render_template_string(GATE_TEMPLATE, error=error)

    @app.post("/sign-out")
    def sign_out() -> Response:
        session.clear()
        return redirect(url_for("index"))

    @app.get("/")
    def index() -> str:
        """Serve the application with the current workspace seeded into the page."""
        document = app.extensions["okr_store"].read()
        bootstrap = {
            "api": {
                "workspace": url_for("api.workspace"),
                "export": url_for("api.export_workspace"),
            },
            "keys": {"workspace": WORKSPACE_KEY},
            "art": {
                name: url_for("static", filename=f"art/{filename}")
                for name, filename in art_manifest().items()
            },
            "pollSeconds": app.config["OKR_POLL_SECONDS"],
            "readOnly": bool(app.config["OKR_READ_ONLY"]),
            "workspace": document.data,
        }
        return render_template("index.html", bootstrap_json=script_json(bootstrap))

    @app.after_request
    def no_store(response: Response) -> Response:
        """The page embeds live workspace data; never let a cache hold it."""
        if request.endpoint == "index":
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response

    return app
