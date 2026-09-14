"""Command line entry point: serve the tracker, and a few admin utilities.

    python -m okr_tracker                 # serve on http://127.0.0.1:8765 and open it
    python -m okr_tracker --host 0.0.0.0 --passphrase secret
    python -m okr_tracker reset-password --profile "Ada Lovelace"
    python -m okr_tracker export backup.json
    python -m okr_tracker where
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, auth
from .config import APP_NAME, Settings
from .schema import ValidationError, validate
from .storage import WorkspaceStore


def _text(value: object) -> str:
    """Profile and owner names are ``{en, fa}`` pairs; show the English side."""
    if isinstance(value, dict):
        return str(value.get("en") or value.get("fa") or "")
    return str(value or "")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ------------------------------------------------------------------- serve


def _serve(settings: Settings, *, open_browser: bool, debug: bool) -> int:
    try:
        from .app import create_app
    except ImportError as error:  # pragma: no cover - environment problem
        print(
            f"{error}\n\nInstall the dependencies first:\n"
            f"    python -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    app = create_app(settings)
    url = f"http://{'127.0.0.1' if settings.host in ('0.0.0.0', '::') else settings.host}:{settings.port}/"

    print(f"{APP_NAME} {__version__}")
    print(f"  workspace  {settings.workspace_path}")
    print(f"  backups    {settings.workspace_path.parent / 'backups'}")
    print(f"  serving    {url}")
    if settings.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"  listening  {settings.host}:{settings.port} (reachable from your network)")
        if not settings.passphrase:
            print("  warning    no --passphrase set: anyone on this network can edit the workspace")
    if settings.read_only:
        print("  mode       read-only (the API refuses writes)")
    print("\nPress Ctrl+C to stop.\n")

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        if debug:
            app.run(host=settings.host, port=settings.port, debug=True)
        else:
            from waitress import serve as waitress_serve

            waitress_serve(
                app, host=settings.host, port=settings.port, threads=8, ident=APP_NAME
            )
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


# ------------------------------------------------------------ admin commands


def _store(settings: Settings) -> WorkspaceStore:
    return WorkspaceStore(settings.workspace_path, backup_count=settings.backup_count)


def _cmd_where(settings: Settings) -> int:
    store = _store(settings)
    document = store.read()
    print(f"data directory  {settings.data_dir}")
    print(f"workspace       {settings.workspace_path}")
    print(f"  exists        {document.exists}")
    if document.exists:
        data = document.data or {}
        print(f"  revision      {document.revision}")
        print(f"  updated       {data.get('updatedAt') or 'unknown'}")
        print(f"  objectives    {len(data.get('objectives') or [])}")
        print(f"  profiles      {len(data.get('profiles') or [])}")
    print(f"backups         {len(store.backups())} retained in {store.backup_dir}")
    return 0


def _cmd_export(settings: Settings, target: Path) -> int:
    document = _store(settings).read()
    if not document.exists:
        print("There is no workspace to export yet.", file=sys.stderr)
        return 1
    target.write_text(
        json.dumps(document.data, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"Exported revision {document.revision} to {target}")
    return 0


def _cmd_import(settings: Settings, source: Path) -> int:
    try:
        document = validate(json.loads(source.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        print(f"Could not read {source}: {error}", file=sys.stderr)
        return 1
    except ValidationError as error:
        print(f"{source} is not a workspace this server will store: {error}", file=sys.stderr)
        return 1

    store = _store(settings)
    if store.read().exists:
        answer = input("This replaces the current workspace (a backup is kept). Continue? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("Cancelled.")
            return 1
    saved = store.overwrite(document)
    print(f"Imported revision {saved.revision} into {settings.workspace_path}")
    return 0


def _cmd_profiles(settings: Settings) -> int:
    document = _store(settings).read()
    if not document.exists:
        print("There is no workspace yet. Start the app once to create one.", file=sys.stderr)
        return 1
    profiles = (document.data or {}).get("profiles") or []
    width = max((len(_text(p.get("name"))) for p in profiles), default=4)
    print(f"{'NAME'.ljust(width)}  {'ROLE'.ljust(7)}  PASSWORD")
    for profile in profiles:
        block = profile.get("auth")
        if not auth.valid_auth(block):
            state = "not set"
        elif block.get("isInitial"):
            state = "initial (unchanged)"
        else:
            state = f"set {block.get('updatedAt') or ''}".strip()
        print(f"{_text(profile.get('name')).ljust(width)}  {str(profile.get('role')).ljust(7)}  {state}")
    return 0


def _cmd_reset_password(settings: Settings, name: str, password: str | None) -> int:
    store = _store(settings)
    document = store.read()
    if not document.exists:
        print("There is no workspace yet.", file=sys.stderr)
        return 1

    data = document.data or {}
    needle = name.strip().casefold()
    matches = [
        p for p in data.get("profiles") or []
        if _text(p.get("name")).casefold() == needle or str(p.get("id")) == name
    ]
    if not matches:
        print(f"No profile matches {name!r}. Run 'profiles' to list them.", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"{name!r} matches {len(matches)} profiles; use the profile id.", file=sys.stderr)
        return 1

    if password is None:
        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Repeat password: "):
            print("Passwords did not match.", file=sys.stderr)
            return 1
    if len(password) < 8:
        print("Choose a password of at least 8 characters.", file=sys.stderr)
        return 1

    profile = matches[0]
    profile["auth"] = auth.create_auth(password, initial=False, now=_now())
    data["revision"] = f"rev-cli-{_now()}"
    data["updatedAt"] = _now()

    try:
        validate(data)
    except ValidationError as error:  # pragma: no cover - defensive
        print(f"Refusing to save: {error}", file=sys.stderr)
        return 1

    store.overwrite(data)
    print(f"Password reset for {_text(profile.get('name'))}. Reload any open browser tab.")
    return 0


# ------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="okr-tracker",
        description=f"{APP_NAME} - serve the OKR workspace and manage its data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--data-dir", type=Path, help="where the workspace is stored")

    serve_group = parser.add_argument_group("serving")
    serve_group.add_argument("--host", help="interface to bind (default 127.0.0.1)")
    serve_group.add_argument("--port", type=int, help="port to bind (default 8765)")
    serve_group.add_argument("--passphrase", help="require this passphrase to reach the app")
    serve_group.add_argument("--read-only", action="store_true", help="serve without write access")
    serve_group.add_argument("--no-browser", action="store_true", help="do not open a browser")
    serve_group.add_argument("--debug", action="store_true", help="Flask reloader and tracebacks")

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="serve the application (the default)")
    sub.add_parser("where", help="show data locations and workspace summary")
    sub.add_parser("profiles", help="list workspace profiles and password state")

    export = sub.add_parser("export", help="write the workspace to a JSON file")
    export.add_argument("target", type=Path)

    import_ = sub.add_parser("import", help="replace the workspace from a JSON file")
    import_.add_argument("source", type=Path)

    reset = sub.add_parser("reset-password", help="set a profile password from the terminal")
    reset.add_argument("--profile", required=True, help="profile name or id")
    reset.add_argument("--password", help="read from a prompt when omitted")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    settings = Settings()
    if args.data_dir:
        settings.data_dir = args.data_dir.expanduser()
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    if args.passphrase:
        settings.passphrase = args.passphrase
    if args.read_only:
        settings.read_only = True

    command = args.command or "serve"
    if command == "serve":
        return _serve(settings, open_browser=not args.no_browser, debug=args.debug)
    if command == "where":
        return _cmd_where(settings)
    if command == "profiles":
        return _cmd_profiles(settings)
    if command == "export":
        return _cmd_export(settings, args.target)
    if command == "import":
        return _cmd_import(settings, args.source)
    if command == "reset-password":
        return _cmd_reset_password(settings, args.profile, args.password)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
