#!/usr/bin/env python3
"""aidog_update — version check + upgrade for I'm AiDog (owner decree: we cut
every release; users are notified in-console and upgrade with one command).

Mechanism (two-stage, confidential -> public; flip executed 2026-10-27):
  - NOW (public repo): origin is a PUBLIC GitHub repo; the release manifest is
    read from `GET /repos/{owner}/{repo}/contents/release.json` — anonymous
    when no token is set, authenticated via slot `AIDOG_GH_PAT` (legacy
    fallback `GITHUB_PAT`) when present — value never echoed, never in argv.
  - Anonymous reads work because GitHub allows unauthenticated contents reads
    on public repositories (rate-limited; the PAT only raises the budget).
  - owner/repo is derived from `git remote get-url origin` — no config to edit
    when the channel changes.

Commands:
  python3 deploy/aidog_update.py check     # installed vs latest (exit 0 = current,
                                            # 10 = update available, 1 = error)
  python3 deploy/aidog_update.py apply     # ff-only pull + restart runtime sessions
  python3 deploy/aidog_update.py check --json

Semantics: `apply` refuses to run on a dirty worktree (uncommitted local changes
would be preserved by ff-only anyway, but the operator should decide first).
English comments per repo i18n policy.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SECRETS = REPO / "secrets.env"
VERSION_FILE = REPO / "VERSION"
MANIFEST_FILE = REPO / "release.json"
MANIFEST_PATH = "release.json"


def installed_version() -> str:
    try:
        return VERSION_FILE.read_text().strip()
    except OSError:
        return "0.0.0"


def env_slot(name: str) -> str | None:
    try:
        m = re.search(rf"(?m)^{re.escape(name)}=(\S[^\r\n]*)$", SECRETS.read_text())
    except OSError:
        return None
    return m.group(1).strip() if m else None


def semver(v: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", v)
    return tuple(int(p) for p in parts[:3]) or (0, 0, 0)


def newer(a: str, b: str) -> bool:
    """True when a > b (stable release ordering; pre-release tags ignored)."""
    return semver(a) > semver(b)


def origin_repo() -> tuple[str, str] | None:
    """(owner, repo) from `git remote get-url origin`, https or ssh form."""
    try:
        url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=REPO,
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$", url)
    return (m.group(1), m.group(2)) if m else None


def fetch_manifest() -> dict:
    """Latest release.json from origin (authenticated only when needed)."""
    repo = origin_repo()
    if not repo:
        raise RuntimeError("no origin remote configured")
    owner, name = repo
    url = f"https://api.github.com/repos/{owner}/{name}/contents/{MANIFEST_PATH}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "aidog-update"})
    pat = env_slot("AIDOG_GH_PAT") or env_slot("GITHUB_PAT")
    if pat:
        req.add_header("Authorization", f"Bearer {pat}")
    last: Exception | None = None
    for attempt in (0, 1):  # fresh-process / egress flakes: retry once (observed P3)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                doc = json.loads(r.read().decode())
            break
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt == 0:
                time.sleep(0.8)
            else:
                raise last from None
    import base64
    content = doc.get("content") or ""
    decoded = base64.b64decode(content or "").decode("utf-8")
    manifest = json.loads(decoded)
    if not isinstance(manifest, dict):
        raise RuntimeError("manifest is not an object")
    return manifest


def check(doc: bool = False) -> int:
    installed = installed_version()
    try:
        remote = fetch_manifest()
    except Exception as e:  # noqa: BLE001
        if doc:
            print(json.dumps({"installed": installed, "latest": None,
                              "update_available": None, "error": type(e).__name__}))
        else:
            print(f"check failed: {type(e).__name__}: {e}")
        return 1
    latest = str(remote.get("version") or "0.0.0")
    available = newer(latest, installed)
    if doc:
        print(json.dumps({"installed": installed, "latest": latest,
                          "update_available": available,
                          "date": remote.get("date"), "notes": remote.get("notes")}))
    else:
        if available:
            print(f"UPDATE AVAILABLE: {installed} -> {latest} "
                  f"({remote.get('date', '')}) {remote.get('notes', '')}")
        else:
            print(f"up to date: {installed} (origin: {latest})")
    return 10 if available else 0


def _git(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True,
                          text=True, timeout=120, env=env)


def apply() -> int:
    # Credential discipline: token lives ONLY in the child env; the inline
    # helper body references $AIDOG_GH_PAT (no value in argv, no helper bypass
    # from gh — GIT_CONFIG_* neutralized per pitfall log).
    env = dict(os.environ)
    pat = env_slot("AIDOG_GH_PAT") or env_slot("GITHUB_PAT")
    if pat:
        env["AIDOG_GH_PAT"] = pat
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    helper = '-c', ('credential.helper=!f(){ test "$1" = get && '
                    'echo "username=x-access-token" && '
                    'echo "password=$AIDOG_GH_PAT"; }')
    git_env = ["git", "-C", str(REPO), helper[0], helper[1]]

    dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        print("refusing: worktree has uncommitted changes "
              f"({len(dirty.splitlines())} entries) — commit or stash first")
        return 1
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip() or "main"
    fetch = subprocess.run([*git_env, "fetch", "origin", branch],
                           capture_output=True, text=True, env=env, timeout=180)
    if fetch.returncode != 0:
        print(f"fetch failed: {(fetch.stderr or fetch.stdout).strip()[:200]}")
        return 1
    ff = subprocess.run([*git_env, "merge", "--ff-only", f"origin/{branch}"],
                        capture_output=True, text=True, env=env, timeout=120)
    print((ff.stdout or ff.stderr).strip()[:300])
    if ff.returncode != 0:
        return 1
    print(f"code updated -> {installed_version()} "
          "(origin release.json must be committed together with VERSION bump)")
    # Restart runtime sessions so the new code is live (idempotent script).
    rc = subprocess.run(["bash", str(REPO / "deploy/boot_services.sh")]).returncode
    if rc == 0:
        print("runtime sessions restarted: all green")
    else:
        print("code updated but restart reported failures — run boot_services.sh status")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    cmd = args[0] if args else "check"
    if cmd == "check":
        return check(doc=as_json)
    if cmd == "apply":
        return apply()
    print("usage: aidog_update.py [check|apply] [--json]")
    return 2


if __name__ == "__main__":
    sys.exit(main())
