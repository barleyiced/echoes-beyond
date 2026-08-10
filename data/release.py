"""Stamp CHANGELOG.md after a deploy actually succeeded.

    python -m data.release <build-id>

Run by `du publish` *after* wrangler reports success, never at build time. The
distinction matters: `du site` builds without publishing, so a record written by
the build would claim a deploy that never happened — and the whole point of the
file is that somebody else can trust it.

What it does not do is decide what the live site says. The site renders the top
`## Unreleased` section under the build id and date baked into its own
index.html, so the deployed copy always describes itself. This only retitles
that section in the working tree and opens a fresh one, which is bookkeeping for
the *next* publish.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"

UNRELEASED = "## Unreleased"


def stamp(build: str, today: str | None = None) -> str:
    """Retitle the unreleased section and start a new one. Returns a summary."""
    if not CHANGELOG.exists():
        return "no CHANGELOG.md, nothing stamped"

    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = CHANGELOG.read_text(encoding="utf-8").splitlines()

    # Matched as a *heading line*, never as a substring. The file's own preamble
    # explains the convention and therefore contains the words `## Unreleased`
    # in prose; a `str.partition` found that first and rewrote the explanation
    # instead of the section. Caught only by running this against the real file.
    start = next((i for i, ln in enumerate(lines) if ln.strip() == UNRELEASED), None)
    if start is None:
        return "no '## Unreleased' heading, nothing stamped"

    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("## ")), len(lines))
    body = lines[start + 1:end]
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()

    if not body:
        # Publishing with nothing written down is allowed — a rebuild against
        # new game data is a real change with no code behind it — but it has to
        # say so rather than shipping an empty heading somebody must guess at.
        body = ["- Rebuilt against the current pinned game data. No behaviour changes."]

    stamped = [UNRELEASED, "", f"## {today} · build {build}", ""] + body + [""]
    CHANGELOG.write_text(
        "\n".join(lines[:start] + stamped + lines[end:]).rstrip() + "\n",
        encoding="utf-8")
    return f"stamped {today} · build {build}"


def main() -> None:
    # The build id is computed here rather than passed in by du.bat. Shelling
    # out for it meant a `for /f "usebackq"` whose command started with a quoted
    # path, which is precisely the shape cmd's strip-the-outer-quotes rule
    # mangles — and a mangled id would stamp a release nobody can match to a
    # build. One fewer moving part in the one script that must never half-work.
    build = sys.argv[1] if len(sys.argv) > 1 else ""
    if not build:
        from data.site import build_id
        build = build_id()[0]
    print("  " + stamp(build))


if __name__ == "__main__":
    main()
