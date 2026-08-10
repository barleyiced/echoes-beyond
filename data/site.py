"""Assemble what the browser build needs.

    python -m data.site            build web/static/dist/  (for local testing)
    python -m data.site --site     also assemble site/     (Phase 4)

Two artefacts:

* `python.zip` — the engine, the router, and the built dataset, laid out exactly
  as they sit in the repo so `engine/dataset.py`'s `parent.parent / "data"` and
  `engine/state.py`'s `parent.parent / "runs"` resolve without modification.
* `pyodide/` — the CPython-on-WASM runtime, vendored from the npm package.

Deliberately *not* included: `resolve/` (needs rapidfuzz, which has no Pyodide
build) and `data/fetch.py` / `data/build.py` (need `requests`). The router
imports all three lazily and reports 503, so their absence is a clean
"unavailable" rather than an import crash — see WEB-PLAN.md spikes 0a and 0b.

Archives are written with `zipfile` and explicit forward-slash arcnames. Never
use PowerShell `Compress-Archive` here: it writes backslash separators that
Emscripten's unzip will not split, and the tree arrives as flat files literally
named `engine\\state.py`, which surfaces as an unrelated ModuleNotFoundError.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "web" / "static" / "dist"
# Where the vendored runtime is looked for. `node_modules/pyodide` under the
# repo root is the only supported location: it is gitignored, so it never
# reaches the published tree, and it survives a reboot. An earlier version of
# this list carried a second candidate pointing into a machine-local temp
# directory, which built fine here and would have died on anyone else's
# checkout — and would have died here too the moment the directory was swept.
# The env var is the escape hatch for a system-wide install.
PYODIDE_SRC_CANDIDATES = [
    Path(p) for p in [os.environ.get("PYODIDE_DIR")] if p
] + [ROOT / "node_modules" / "pyodide"]

# Everything the worker imports, and nothing it does not.
#
# `data` is in here for one reason: engine/synergy.py does
# `from data.tags import PATH_AFFINITY`, so a couple of the data modules are
# runtime dependencies rather than build-time ones. The skip list is what makes
# that safe — fetch.py and build.py import `requests`, and icons.py imports it
# transitively, none of which exists in Pyodide.
PY_PACKAGES = ["engine", "web", "data"]
PY_SKIP_NAMES = {
    "app.py",                       # FastAPI shim: needs fastapi, unused here
    "fetch.py", "build.py", "icons.py", "site.py",   # build-time, need requests
    "publish.py",                   # build-time: shells out to git, never runs here
}
DATA_FILES = ["dataset.json", "dataset.db"]

# Pure-Python packages the engine needs, resolved out of Pyodide's own lockfile
# so the filenames are never transcribed.
WHEELS = ["pyyaml"]

# Pyodide files the worker actually loads. The npm package also ships source
# maps and console demos that would triple the payload for nothing.
PYODIDE_FILES = [
    "pyodide.js", "pyodide.mjs", "pyodide.asm.js", "pyodide.asm.mjs",
    "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json",
]


def _dataset_ok() -> dict:
    """Refuse to publish a dataset that does not verify.

    Invariant 8 applies to publishing at least as much as to building: shipping
    silently wrong numbers to other people is the failure this project exists to
    avoid, and a hosted copy is read by people who cannot check it against the
    game themselves.
    """
    src = ROOT / "data" / "dataset.json"
    if not src.exists():
        sys.exit("data/dataset.json is missing. Run:  python -m data.build")

    ds = json.loads(src.read_text(encoding="utf-8"))

    # data.build pulls in `requests` via data.fetch. That is fine here — this
    # script only ever runs locally — but it is exactly why data/build.py is on
    # PY_SKIP_NAMES and never reaches the browser.
    from data.build import verify

    problems = verify(ds)
    if problems:
        print("Dataset failed verification, so nothing was published:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        sys.exit(1)

    return ds.get("meta", {})


def build_python_zip(dest: Path) -> Path:
    meta = _dataset_ok()
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for pkg in PY_PACKAGES:
            for p in sorted((ROOT / pkg).rglob("*.py")):
                if "__pycache__" in p.parts or p.name in PY_SKIP_NAMES:
                    continue
                z.write(p, p.relative_to(ROOT).as_posix())
                written += 1
        for extra in sorted((ROOT / "engine").glob("*.yaml")):
            z.write(extra, extra.relative_to(ROOT).as_posix())
            written += 1
        for name in DATA_FILES:
            src = ROOT / "data" / name
            if not src.exists():
                sys.exit(f"data/{name} is missing. Run:  python -m data.build")
            z.write(src, f"data/{name}")
            written += 1
        # The changelog is served by /api/changelog, which parses the markdown
        # rather than a generated copy of it, so the source has to ship. At the
        # zip root because the archive mirrors the repo and the route resolves
        # it relative to the package parent on both transports.
        changelog = ROOT / "CHANGELOG.md"
        if changelog.exists():
            z.write(changelog, "CHANGELOG.md")
            written += 1

    size = dest.stat().st_size / 1048576
    print(f"  python.zip      {written:>3} files, {size:.1f} MB   "
          f"({meta.get('theme', '?')}, source {str(meta.get('source_sha', '?'))[:7]})")
    return dest


def vendor_pyodide(dest: Path) -> Path:
    src = next((p for p in PYODIDE_SRC_CANDIDATES if p.exists()), None)
    if src is None:
        sys.exit("Pyodide package not found. Run:  npm install pyodide")
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    for name in PYODIDE_FILES:
        f = src / name
        if not f.exists():
            continue                      # the .js/.mjs pair varies by version
        shutil.copy2(f, dest / name)
        total += f.stat().st_size

    # loadPackage() fetches wheels from indexURL, so they have to sit alongside
    # the runtime. Names come from the lockfile rather than being written down:
    # they carry an ABI tag that changes with every Pyodide release.
    lock = json.loads((src / "pyodide-lock.json").read_text(encoding="utf-8"))
    packages = lock["packages"]
    wanted, seen = list(WHEELS), set()
    while wanted:
        name = wanted.pop()
        key = next((k for k in packages if k.lower() == name.lower()), None)
        if key is None or key in seen:
            continue
        seen.add(key)
        wanted.extend(packages[key].get("depends", []))
        whl = src / packages[key]["file_name"]
        if whl.exists():
            shutil.copy2(whl, dest / whl.name)
            total += whl.stat().st_size

    print(f"  pyodide/        {len(list(dest.iterdir())):>3} files, "
          f"{total / 1048576:.1f} MB   (wheels: {', '.join(sorted(seen)) or 'none'})")
    return dest


# --------------------------------------------------------------- the site

SITE = ROOT / "site"

# Injected into <head>. The backend tag is what makes the built site use the
# worker with no query parameter.
#
# The robots block is the *whole* of the site's de-indexing, and it has to be,
# because GitHub Pages serves fixed headers: there is no way to set
# `X-Robots-Tag` on a project site, which is the mechanism a real host would
# use. So the meta tag is not belt-and-braces here, it is the belt.
#
# Each directive earns its place, since `noindex` alone leaves three surfaces:
#   noindex       keep the page out of the index at all
#   nofollow      pass no ranking signal to anything linked from it
#   noarchive     no cached copy served from the crawler's own storage
#   nosnippet     no text excerpt, so the page cannot be read without loading it
#   noimageindex  keep the Path and character icons out of image search
#   notranslate   no "translate this page" offer, which is its own listing
# `max-snippet:-1` style opt-*ins* are deliberately absent.
#
# `googlebot` repeats the set rather than relying on the generic tag. It is
# redundant by the spec and costs one line, and the generic tag is the one a
# future edit is most likely to loosen by accident.
#
# There is deliberately **no robots.txt**. A crawler only reads the one at the
# origin root, `barleyiced.github.io/robots.txt`, which belongs to a separate
# user-site repo and not to this one, so a file shipped here would sit at
# `/echoes-beyond/robots.txt` and be read by nobody. Worse, a root-level
# `Disallow:` covering this path would *hide* the tag above: a crawler told not
# to fetch the page never sees its noindex, and the bare URL can still be
# listed. Crawling allowed plus noindex is the stronger combination, not the
# weaker one.
HEAD_INJECT = """<meta name="du-backend" content="pyodide">
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet, noimageindex, notranslate">
<meta name="googlebot" content="noindex, nofollow, noarchive, nosnippet, noimageindex, notranslate">
<meta name="referrer" content="no-referrer">
<meta name="du-build" content="{build}">
<meta name="du-source" content="{sha}">
<meta name="du-built" content="{built}">
"""


def build_id() -> tuple[str, str]:
    """A short id that changes whenever anything a visitor would see changes.

    Keyed on the pinned game-data revision *and* the built payload, because
    either can move without the other: `du update` re-pins the data, and a code
    change alters the engine without touching the pin. The service worker's
    cache name is derived from this, so getting it wrong means friends keep
    being served last month's numbers with nothing to indicate it — the exact
    silent-staleness failure this project exists to avoid.
    """
    pin = ROOT / "data" / "pinned_revision.json"
    sha = json.loads(pin.read_text(encoding="utf-8"))["sha"] if pin.exists() else "unpinned"

    h = hashlib.sha256(sha.encode())
    for f in [DIST / "python.zip",
              ROOT / "web" / "static" / "app.js",
              ROOT / "web" / "static" / "app.css",
              ROOT / "web" / "static" / "worker.js",
              ROOT / "web" / "static" / "index.html"]:
        if f.exists():
            h.update(f.read_bytes())
    return h.hexdigest()[:12], sha


def _copy_tree(src: Path, dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(src.rglob("*")):
        if p.is_dir() or "__pycache__" in p.parts:
            continue
        out = dest / p.relative_to(src)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, out)
        n += 1
    return n


def build_site() -> Path:
    """Assemble the publishable directory.

    Layout mirrors how the local app serves itself — index.html at the root,
    everything else under static/ — so the same relative references work under
    both, and `du site` cannot produce a tree the local app has never exercised.
    """
    static_src = ROOT / "web" / "static"
    static_out = SITE / "static"
    SITE.mkdir(parents=True, exist_ok=True)

    build, sha = build_id()

    html = (static_src / "index.html").read_text(encoding="utf-8")
    if 'name="du-backend"' not in html:
        html = html.replace(
            "</head>",
            HEAD_INJECT.format(
                build=build, sha=sha[:7],
                # The date the visitor's copy was built. The changelog's top
                # section is what is about to ship, so this is what dates it —
                # a record written after the deploy could only ever describe
                # the build *before* the one being looked at.
                built=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            ) + "</head>", 1)
    (SITE / "index.html").write_text(html, encoding="utf-8")

    n = 0
    for name in ("app.js", "app.css", "worker.js"):
        static_out.mkdir(parents=True, exist_ok=True)
        shutil.copy2(static_src / name, static_out / name)
        n += 1
    n += _copy_tree(static_src / "icons", static_out / "icons")
    n += _copy_tree(DIST, static_out / "dist")

    # The service worker goes at the site root so its scope covers index.html;
    # served from static/ it could only control static/.
    precache = ["./"] + sorted(
        p.relative_to(SITE).as_posix()
        for p in SITE.rglob("*")
        if p.is_file() and p.name != "sw.js"
    )
    sw = (static_src / "sw.js").read_text(encoding="utf-8")
    sw = sw.replace("__BUILD_ID__", build)
    sw = sw.replace("__PRECACHE__", json.dumps(precache, indent=0))
    (SITE / "sw.js").write_text(sw, encoding="utf-8")
    n += 1

    # GitHub Pages runs the tree through Jekyll unless this file is present.
    # Nothing here is a Jekyll source, so the whole pass is wasted work on 17 MB
    # of runtime, and Jekyll drops anything whose name starts with `_` or `.` —
    # a rule that has never bitten this tree and would do so silently if a
    # future Pyodide release shipped such a file. Written after the precache
    # glob on purpose: it is a host directive, not something to cache.
    (SITE / ".nojekyll").write_text("", encoding="utf-8")
    n += 1

    total = sum(p.stat().st_size for p in SITE.rglob("*") if p.is_file())
    print(f"  site/          {n + 1:>4} files, {total / 1048576:.1f} MB   "
          f"build {build}")
    return SITE


def serve(port: int) -> None:
    """Serve the built site/ the way a real host does.

    Not `python -m http.server`: on Windows it answers .mjs with text/plain, and
    a module worker refuses a non-JS MIME type outright. The worker then dies
    with an *empty* error event, so the page reports "worker failed to start"
    with nothing to go on and the whole engine looks broken. Cloudflare Pages
    gets this right; only the local preview needed fixing.
    """
    import functools
    import http.server
    import socketserver

    handler = http.server.SimpleHTTPRequestHandler
    handler.extensions_map.update({
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".json": "application/json",
        ".wasm": "application/wasm",
    })
    socketserver.TCPServer.allow_reuse_address = True
    print(f"Serving {SITE} at http://127.0.0.1:{port}/   (ctrl-c to stop)")
    with socketserver.ThreadingTCPServer(
            ("127.0.0.1", port),
            functools.partial(handler, directory=str(SITE))) as httpd:
        httpd.serve_forever()


def main() -> None:
    if "--serve" in sys.argv:
        i = sys.argv.index("--serve")
        port = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 8080
        if not (SITE / "index.html").exists():
            sys.exit("site/ has not been built yet. Run: du site")
        return serve(port)

    want_site = "--site" in sys.argv

    print(f"Building browser payload into {DIST.relative_to(ROOT)}")
    build_python_zip(DIST / "python.zip")
    vendor_pyodide(DIST / "pyodide")

    if want_site:
        print(f"Assembling {SITE.relative_to(ROOT)}")
        build_site()
        print("\nDone. Publish with:  du publish")
        print("Or preview locally:  python -m data.site --serve 8080")
    else:
        print("\nDone. Open  http://127.0.0.1:8765/?backend=pyodide  to exercise it.")
        print("Add --site to assemble the publishable site/ directory.")


if __name__ == "__main__":
    main()
