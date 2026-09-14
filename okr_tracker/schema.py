"""Structural checks for the workspace document.

The browser application owns the OKR business rules (pace, risk, gate
arithmetic, extension legality) and enforces them before it ever writes. The
server deliberately does not restate those -- two copies of the same rule drift
apart, and the one on the server would win silently.

What the server does own is the shape of the document it is asked to persist:
enough that a truncated upload, a file from a different tool, or a future schema
version cannot land in the store and take the workspace down on next load.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 6

#: Top-level keys the application reads back out of a stored workspace.
REQUIRED_KEYS = ("version", "objectives", "owners", "profiles", "settings")

LIST_KEYS = ("objectives", "owners", "profiles", "audit", "relations")


class ValidationError(ValueError):
    """Raised when a document is not a workspace this server will store."""


def _require(condition: object, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _collect_ids(document: dict[str, Any]) -> list[str]:
    """Every id that the client's own duplicate check covers."""
    ids: list[str] = []
    for key in ("profiles", "owners"):
        ids += [str(row.get("id", "")) for row in document.get(key) or []]
    for relation in document.get("relations") or []:
        ids.append(str(relation.get("id", "")))
    for objective in document.get("objectives") or []:
        ids.append(str(objective.get("id", "")))
        for extension in objective.get("extensions") or []:
            ids.append(str(extension.get("id", "")))
        for kr in objective.get("krs") or []:
            ids.append(str(kr.get("id", "")))
            for extension in kr.get("extensions") or []:
                ids.append(str(extension.get("id", "")))
    return ids


def validate(document: Any) -> dict[str, Any]:
    """Return ``document`` if it is a storable workspace, else raise.

    Mirrors the client's ``validate()`` only where a failure would corrupt the
    store: version, required containers, and unique record ids.
    """
    _require(isinstance(document, dict), "workspace must be a JSON object")

    version = document.get("version")
    _require(isinstance(version, int), "workspace 'version' must be an integer")
    _require(
        version <= SCHEMA_VERSION,
        f"workspace version {version} is newer than this server supports "
        f"(version {SCHEMA_VERSION}); upgrade the application first",
    )
    _require(
        version == SCHEMA_VERSION,
        f"workspace version {version} must be migrated to version "
        f"{SCHEMA_VERSION} by the application before it can be saved",
    )

    for key in REQUIRED_KEYS:
        _require(key in document, f"workspace is missing '{key}'")

    for key in LIST_KEYS:
        value = document.get(key)
        if value is None and key in ("audit", "relations"):
            continue
        _require(isinstance(value, list), f"workspace '{key}' must be a list")

    _require(isinstance(document.get("settings"), dict), "workspace 'settings' must be an object")

    for objective in document["objectives"]:
        _require(isinstance(objective, dict), "each objective must be an object")
        _require(
            isinstance(objective.get("krs"), list),
            f"objective {objective.get('id')!r} must carry a 'krs' list",
        )

    _require(
        any((profile or {}).get("role") == "admin" for profile in document["profiles"]),
        "workspace must keep at least one admin profile",
    )

    ids = _collect_ids(document)
    _require(all(ids), "every record needs a non-empty id")
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    _require(not duplicates, f"duplicate record ids: {', '.join(duplicates[:5])}")

    revision = document.get("revision")
    _require(
        isinstance(revision, str) and revision.strip(),
        "workspace 'revision' must be a non-empty string",
    )

    return document
