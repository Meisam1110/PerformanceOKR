"""JSON API for the workspace document.

The client speaks the same optimistic-concurrency protocol it used against
``localStorage``: read a document, note its ``revision``, and send the next
version back together with the revision it was based on. A mismatch means
somebody else saved first, and the response carries their document so the
session can resynchronise instead of clobbering it.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request, session

from .schema import ValidationError, validate
from .storage import ConflictError, WorkspaceStore

api = Blueprint("api", __name__, url_prefix="/api")


def store() -> WorkspaceStore:
    return current_app.extensions["okr_store"]


def _error(message: str, status: int, **extra: Any) -> tuple[Response, int]:
    return jsonify({"error": message, **extra}), status


def _guard_writes() -> tuple[Response, int] | None:
    if current_app.config.get("OKR_READ_ONLY"):
        return _error("This workspace is served read-only.", 403)
    return None


@api.before_request
def require_gate() -> tuple[Response, int] | None:
    """Enforce the optional server passphrase on every API route."""
    if not current_app.config.get("OKR_PASSPHRASE_HASH"):
        return None
    if session.get("okr_gate") is True:
        return None
    return _error("Sign in to this server first.", 401)


@api.get("/health")
def health() -> Response:
    document = store().read()
    return jsonify(
        {
            "status": "ok",
            "readOnly": bool(current_app.config.get("OKR_READ_ONLY")),
            "workspace": {
                "exists": document.exists,
                "revision": document.revision,
                "updatedAt": (document.data or {}).get("updatedAt"),
                "objectives": len((document.data or {}).get("objectives") or []),
            },
            "backups": len(store().backups()),
        }
    )


@api.get("/workspace")
def workspace() -> Response:
    """Current document and its revision. ``document`` is null on a fresh install."""
    document = store().read()
    return jsonify({"document": document.data, "revision": document.revision})


@api.put("/workspace")
def save_workspace() -> Response | tuple[Response, int]:
    """Compare-and-swap the document against the revision the client last saw."""
    denied = _guard_writes()
    if denied:
        return denied

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or "document" not in payload:
        return _error("Expected a JSON body of {document, baseRevision}.", 400)

    base_revision = payload.get("baseRevision")
    if base_revision is not None and not isinstance(base_revision, str):
        return _error("'baseRevision' must be a string or null.", 400)

    try:
        document = validate(payload["document"])
    except ValidationError as error:
        return _error(str(error), 422)

    try:
        saved = store().write(document, base_revision=base_revision)
    except ConflictError as conflict:
        return (
            jsonify(
                {
                    "error": "The workspace was saved by someone else.",
                    "document": conflict.current.data,
                    "revision": conflict.current.revision,
                }
            ),
            409,
        )

    return jsonify({"revision": saved.revision, "updatedAt": document.get("updatedAt")})


@api.get("/workspace/export")
def export_workspace() -> Response | tuple[Response, int]:
    """Download the document as the same JSON file the app imports."""
    document = store().read()
    if not document.exists:
        return _error("There is no workspace to export yet.", 404)
    body = json.dumps(document.data, ensure_ascii=False, indent=1)
    response = Response(body, mimetype="application/json; charset=utf-8")
    filename = f"POLE_OKR_{date.today().isoformat()}.json"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@api.post("/workspace/import")
def import_workspace() -> Response | tuple[Response, int]:
    """Replace the document wholesale. The outgoing one is backed up first."""
    denied = _guard_writes()
    if denied:
        return denied

    payload = request.get_json(silent=True)
    document = payload.get("document") if isinstance(payload, dict) else payload
    try:
        document = validate(document)
    except ValidationError as error:
        return _error(str(error), 422)

    saved = store().overwrite(document)
    return jsonify({"revision": saved.revision, "imported": True})


@api.get("/backups")
def list_backups() -> Response:
    """Backups retained by the store, newest first."""
    return jsonify(
        {
            "backups": [
                {
                    "name": path.name,
                    "bytes": path.stat().st_size,
                    "modified": path.stat().st_mtime,
                }
                for path in store().backups()
            ]
        }
    )
