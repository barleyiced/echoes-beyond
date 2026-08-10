"""Push the built site/ to the gh-pages branch that GitHub Pages serves.

    python -m data.publish              push site/ to origin gh-pages
    python -m data.publish --dry-run    do everything except the push

Run by `du publish` between the build and the changelog stamp. Replaces the
wrangler leg: the site moved from Cloudflare Pages to
https://barleyiced.github.io/echoes-beyond/ .

Three things about how this is done, all deliberate:

**site/ stays gitignored and main stays source-only.** 17 MB of vendored Pyodide
does not belong in the history of a source branch. `git add --force` is what
gets it past the ignore rule, and it is scoped to the site directory so a stray
force-add can never reach anything else.

**Every publish is a fresh orphan commit, force-pushed.** The branch is built
with plumbing (`read-tree --empty`, `write-tree`, `commit-tree` with no parent)
against a throwaway index file, so the repository index and your working tree
are never touched — you can publish with a dirty tree and lose nothing. It also
means the branch has exactly one commit rather than a growing chain of 17 MB
snapshots, which is the difference between a repository that stays small and one
that has to be rewritten in a year.

**It refuses to push a site that would be indexable.** `verify_site` checks the
built index.html for the noindex directive and the tree for `.nojekyll` before
anything leaves the machine. Both come from `data/site.py`, so the only way they
go missing is an edit that did not mean to remove them, and on GitHub Pages
there is no header to fall back on and no CI to catch it. That is invariant 8
applied to the deploy: refuse to publish rather than publish something wrong.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"

BRANCH = "gh-pages"
REMOTE = "origin"

# What `verify_site` insists on. The robots value is matched loosely, on the two
# directives that carry the weight, so reordering the list in data/site.py does
# not fail the deploy for no reason.
REQUIRED_META = ("noindex", "nofollow")


def _git(*args: str, check: bool = True, env: dict | None = None) -> str:
    """Run git in the repo and return stdout."""
    r = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True, text=True,
        env={**os.environ, **(env or {})},
    )
    if check and r.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r.stdout.strip()


def verify_site() -> str:
    """Refuse to publish a site that is missing its host directives.

    The build id is returned rather than printed so the caller can put it in the
    commit message, which is what makes a deployed commit traceable back to the
    numbers it shipped.
    """
    index = SITE / "index.html"
    if not index.exists():
        sys.exit("site/ has not been built. Run:  du site")

    html = index.read_text(encoding="utf-8")

    robots = next((ln for ln in html.splitlines()
                   if 'name="robots"' in ln), "")
    missing = [d for d in REQUIRED_META if d not in robots]
    if missing:
        sys.exit(
            "site/index.html carries no robots " + ", ".join(missing) + " directive, "
            "so nothing was pushed.\nGitHub Pages cannot set an X-Robots-Tag "
            "header, which makes that meta tag the only thing keeping this site "
            "out of search results. Check HEAD_INJECT in data/site.py.")

    if not (SITE / ".nojekyll").exists():
        sys.exit(
            "site/.nojekyll is missing, so nothing was pushed.\nWithout it "
            "GitHub Pages runs the whole tree through Jekyll, which drops any "
            "file whose name starts with `_` or `.`. Check build_site() in "
            "data/site.py.")

    build = next((ln.split('content="')[1].split('"')[0]
                  for ln in html.splitlines() if 'name="du-build"' in ln), "")
    if not build:
        sys.exit("site/index.html carries no du-build id. Rebuild with:  du site")
    return build


def remote_url() -> str:
    url = _git("remote", "get-url", REMOTE, check=False)
    if not url:
        sys.exit(
            f"No git remote named '{REMOTE}'.\nCreate the repository on GitHub, "
            f"then:\n  git remote add {REMOTE} "
            "https://github.com/barleyiced/echoes-beyond.git")
    return url


def push(build: str, dry_run: bool = False) -> str:
    """Build the orphan commit and push it. Returns the pushed commit sha."""
    url = remote_url()

    # A throwaway index. Without this every add below would stage 17 MB into the
    # real index and leave the working tree looking like a catastrophe.
    fd, tmp_index = tempfile.mkstemp(prefix="du-publish-index-")
    os.close(fd)
    os.unlink(tmp_index)                       # git wants to create it itself
    env = {"GIT_INDEX_FILE": tmp_index}

    try:
        _git("read-tree", "--empty", env=env)
        # --force because site/ is gitignored, and that is the point: the ignore
        # rule is what keeps 17 MB out of main. Scoped to site/ by the -C below.
        add = subprocess.run(
            ["git", "-C", str(SITE), "add", "--force", "--all", "."],
            capture_output=True, text=True,
            env={**os.environ, **env},
        )
        if add.returncode != 0:
            sys.exit("git add failed, so nothing was pushed:\n"
                     + add.stderr.strip())
        # `--prefix` is load-bearing, not tidiness. Git records index paths
        # relative to the repository root, so the entries just added are named
        # `site/index.html` however the add was invoked, and a plain
        # `write-tree` would publish a branch whose root contains a single
        # `site/` directory. The site would then answer on
        # /echoes-beyond/site/ and 404 at the URL everybody has, with the build
        # itself perfectly correct. This extracts the subtree instead.
        tree = _git("write-tree", "--prefix=site/", env=env)

        message = (
            f"Publish build {build}\n\n"
            "Built from site/ by `du publish`. This branch is what GitHub Pages\n"
            "serves and is rewritten in full on every publish, so it carries no\n"
            "history. The source history is on main.\n"
        )
        # No parent: an orphan commit, so the branch never accumulates.
        commit = _git("commit-tree", tree, "-m", message, env=env)

        if dry_run:
            print(f"  dry run: built {commit[:12]} from tree {tree[:12]}, not pushed")
            return commit

        _git("update-ref", f"refs/heads/{BRANCH}", commit)
        print(f"  pushing {commit[:12]} to {url} ({BRANCH})")
        r = subprocess.run(
            ["git", "-C", str(ROOT), "push", "--force", REMOTE,
             f"refs/heads/{BRANCH}:refs/heads/{BRANCH}"],
            text=True,
        )
        if r.returncode != 0:
            sys.exit(f"Push failed, so nothing shipped and CHANGELOG.md was left alone.")
        return commit
    finally:
        if os.path.exists(tmp_index):
            os.unlink(tmp_index)


def main() -> None:
    build = verify_site()
    commit = push(build, dry_run="--dry-run" in sys.argv)
    print(f"  build {build} is on {BRANCH} as {commit[:12]}")
    print("  https://barleyiced.github.io/echoes-beyond/")


if __name__ == "__main__":
    main()
