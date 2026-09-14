"""Split the single-file OKR Tracker HTML into servable front-end assets.

The upstream deliverable is one ~6.5 MB HTML file that keeps its whole
workspace in ``localStorage``. This script is the build step that turns it into
the static half of the Flask application:

* ``<style>``            -> ``okr_tracker/static/css/app.css``
* ``<script>``           -> ``okr_tracker/static/js/app.js``
* ``ART_ASSETS`` base64  -> ``okr_tracker/static/art/<name>.png`` (+ URL map)
* ``<body>`` markup      -> ``okr_tracker/templates/index.html``

It also rewrites every ``localStorage.`` call site in the application script to
``okrStore.`` so the storage shim in ``static/js/backend-store.js`` can serve
the same synchronous interface while mirroring writes to the Python backend.
The application logic itself is never touched.

Re-run it whenever ``vendor/OKR_Tracker.source.html`` is replaced with a newer
build of the tracker:

    python tools/build_frontend.py
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "vendor" / "OKR_Tracker.source.html"
STATIC = ROOT / "okr_tracker" / "static"
TEMPLATES = ROOT / "okr_tracker" / "templates"

# Content-Security-Policy for the served app. The original file allowed only
# 'self' for connect-src implicitly via default-src; we keep that (the API lives
# on the same origin) and drop the data:/blob: image allowance down to what the
# extracted build actually needs.
CSP = (
    "default-src 'self' blob:; "
    "img-src 'self' data: blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'"
)

TEMPLATE = """<!doctype html>
<html lang="en" dir="ltr" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#08111f">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<title>POLE OKR Tracker</title>
<link rel="icon" href="{{{{ url_for('static', filename='art/favicon.svg') }}}}">
<link rel="stylesheet" href="{{{{ url_for('static', filename='css/app.css') }}}}">
<link rel="stylesheet" href="{{{{ url_for('static', filename='css/backend.css') }}}}">
</head>
<body>
{body}
<!-- Server-rendered bootstrap: API endpoints, artwork URLs, and the workspace
     itself. The application's load() is synchronous and runs on first paint, so
     the document has to be in the page before app.js is. This is a data block
     rather than an inline script so the page keeps a strict script-src. -->
<script type="application/json" id="okrBootstrap">{{{{ bootstrap_json|safe }}}}</script>
<script src="{{{{ url_for('static', filename='js/backend-store.js') }}}}"></script>
<script src="{{{{ url_for('static', filename='js/app.js') }}}}"></script>
</body>
</html>
"""

FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<rect width="32" height="32" rx="7" fill="#08111f"/>
<path d="M8 21V11h4.6a3.2 3.2 0 0 1 0 6.4H11V21H8Zm3-6.1h1.6a1 1 0 0 0 0-2H11v2Z" fill="#ffd75e"/>
<circle cx="21.5" cy="16" r="4.6" fill="none" stroke="#80baff" stroke-width="2.6"/>
</svg>
"""


def fail(message: str) -> "NoReturn":  # type: ignore[valid-type]
    print(f"build_frontend: {message}", file=sys.stderr)
    raise SystemExit(1)


def extract(html: str, tag: str) -> tuple[str, int, int]:
    """Return the inner text of the first ``<tag>`` plus its outer span."""
    open_match = re.search(rf"<{tag}[^>]*>", html)
    if not open_match:
        fail(f"no <{tag}> element found in the source HTML")
    close = html.find(f"</{tag}>", open_match.end())
    if close < 0:
        fail(f"unterminated <{tag}> element in the source HTML")
    return html[open_match.end() : close], open_match.start(), close + len(tag) + 3


def split_art(script: str, art_dir: Path) -> tuple[str, dict[str, str]]:
    """Write the embedded base64 artwork to PNG files.

    Returns the script with the ``ART_ASSETS`` literal replaced by a reference
    to the URL map injected by the template, plus the filename map.
    """
    match = re.search(r"^const ART_ASSETS=(\{.*\});$", script, re.MULTILINE)
    if not match:
        print("build_frontend: no ART_ASSETS literal found; leaving script as is")
        return script, {}

    assets = json.loads(match.group(1))
    art_dir.mkdir(parents=True, exist_ok=True)
    names: dict[str, str] = {}
    for name, data_url in assets.items():
        header, _, payload = data_url.partition(",")
        suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(
            header.partition(";")[0].removeprefix("data:"), "png"
        )
        filename = f"{name}.{suffix}"
        (art_dir / filename).write_bytes(base64.b64decode(payload))
        names[name] = filename

    replacement = (
        "const ART_ASSETS=Object.freeze("
        "Object.assign({none:''},(globalThis.OKR_BACKEND&&OKR_BACKEND.art)||{}));"
    )
    return script[: match.start()] + replacement + script[match.end() :], names


#: Interface strings that stop being true once the workspace lives on a server
#: rather than in the browser. Each entry is ``key: (english, persian)`` and is
#: matched against the application's ``STRINGS`` table. Wording that is still
#: accurate -- file import/export, per-device appearance, WebCrypto support --
#: is deliberately left alone.
STRING_OVERRIDES: dict[str, tuple[str, str]] = {
    "localSaveHint": (
        "Data is saved on the server running this app, which also keeps backups.",
        "داده روی سروری که این برنامه را اجرا می‌کند ذخیره می‌شود و پشتیبان‌ها خودکار نگه‌داری می‌شوند.",
    ),
    "savedBrowser": ("Saved {date}", "ذخیره‌شده: {date}"),
    "storageError": (
        "The change was not saved because the server did not accept it. "
        "Keep a JSON backup.",
        "تغییر ذخیره نشد زیرا سرور آن را نپذیرفت. پشتیبان JSON نگه دارید.",
    ),
    "conflict": (
        "Someone else saved newer data. Close this form and reload before editing.",
        "کاربر دیگری داده جدیدتری ذخیره کرده است. فرم را ببندید و پیش از ویرایش بازخوانی کنید.",
    ),
    "reloadBrowser": ("Reload from server", "بازخوانی از سرور"),
}


def js_string(value: str) -> str:
    """Quote a string the way the application's STRINGS table does."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def patch_strings(script: str) -> str:
    """Correct the interface wording that assumed browser-only storage."""
    applied = 0
    for key, (english, persian) in STRING_OVERRIDES.items():
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_]){re.escape(key)}:\['(?:[^'\\]|\\.)*','(?:[^'\\]|\\.)*'\]"
        )
        replacement = f"{key}:[{js_string(english)},{js_string(persian)}]"
        script, count = pattern.subn(lambda _: replacement, script, count=1)
        if count:
            applied += 1
        else:
            print(f"build_frontend: warning: string {key!r} not found; wording left as is")
    print(f"build_frontend: rewrote {applied}/{len(STRING_OVERRIDES)} storage-related strings")
    return script


def patch_storage_calls(script: str) -> str:
    """Point the app's synchronous storage calls at the backend-backed shim."""
    patched, count = re.subn(r"\blocalStorage\.", "okrStore.", script)
    if not count:
        fail("found no localStorage call sites to redirect; is this the right file?")
    print(f"build_frontend: redirected {count} localStorage call sites to okrStore")
    return patched


def build(source: Path) -> None:
    html = source.read_text(encoding="utf-8")

    css, _, _ = extract(html, "style")
    script, script_start, script_end = extract(html, "script")

    body_open = re.search(r"<body[^>]*>", html)
    if not body_open:
        fail("no <body> element found in the source HTML")
    body = html[body_open.end() : script_start].strip()

    trailing = html[script_end:]
    if re.sub(r"</(body|html)>|\s", "", trailing):
        fail("unexpected markup after the application script; build aborted")

    script, art_names = split_art(script, STATIC / "art")
    script = patch_strings(script)
    script = patch_storage_calls(script)

    (STATIC / "css").mkdir(parents=True, exist_ok=True)
    (STATIC / "js").mkdir(parents=True, exist_ok=True)
    TEMPLATES.mkdir(parents=True, exist_ok=True)

    (STATIC / "css" / "app.css").write_text(css.strip() + "\n", encoding="utf-8")
    (STATIC / "js" / "app.js").write_text(script.strip() + "\n", encoding="utf-8")
    (STATIC / "art" / "favicon.svg").write_text(FAVICON, encoding="utf-8")

    STATIC.joinpath("art", "manifest.json").write_text(
        json.dumps(art_names, indent=2) + "\n", encoding="utf-8"
    )
    TEMPLATES.joinpath("index.html").write_text(
        TEMPLATE.format(csp=CSP, body=body), encoding="utf-8"
    )

    print(f"build_frontend: css      {len(css):>9,} bytes")
    print(f"build_frontend: app.js   {len(script):>9,} bytes")
    for name, filename in art_names.items():
        size = (STATIC / "art" / filename).stat().st_size
        print(f"build_frontend: art      {filename:<12} {size:>9,} bytes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source", nargs="?", type=Path, default=SOURCE,
        help="single-file OKR Tracker HTML to split (default: vendor/OKR_Tracker.source.html)",
    )
    args = parser.parse_args()
    if not args.source.exists():
        fail(f"source file not found: {args.source}")
    build(args.source)


if __name__ == "__main__":
    main()
