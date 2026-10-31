#!/usr/bin/env python3
"""Source language lint — no CJK outside the locale packs (regression wall).

Repo i18n policy (AGENTS.md): product source code, comments, docstrings and
log messages are English; only locale-pack VALUES may be non-English. This
test makes that rule executable.

Scans: every `*.py` / `*.sh` / `*.js` file under the repo root (worktree,
tracked or not), skipping the allowlisted paths below.

A violation is any CJK ideograph (U+4E00-U+9FFF) on a scanned line, except:
  - allowlisted paths — locale packs (`overlay/locales/`), archives
    (`tests/runs/`), docs (`docs/`), fixtures (`tests/ab_prompts.json`);
  - lines carrying the inline marker `i18n-allow` (deliberate Chinese input
    data inside a test — e.g. the Chinese routing query that proves the
    router handles zh text; see tests/skill_route_p4.py).

It also validates locale-pack key parity through lib/i18n.validate(), so a
pack that drifts from the default pack fails the same way a CJK leak does.

Usage: python3 tests/source_lang_check.py
Exit 0 = clean · 1 = violations or locale drift.
English output per repo i18n policy.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) in sys.path:
    sys.path.remove(str(REPO))
sys.path.insert(0, str(REPO))

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
SCAN_SUFFIXES = (".py", ".sh", ".js")
INLINE_MARKER = "i18n-allow"

# Directory names never scanned (runtime state, caches, vendored blobs).
# Nested sibling repos moved into this directory (owner decree 2026-10-28) hold
# their own CJK and upstream-brand material BY DESIGN — they are independent git
# repos and must stay invisible to this lint exactly as .gitignore keeps them
# out of git; without these entries the lint would walk into them.
SKIP_DIR_NAMES = {".git", "__pycache__", "node_modules", ".venv", "venv",
                  "data", "tmp", ".screenshots", ".voice", ".pi",
                  "aidog-internal", "aidog-skills", "aidog-public",
                  "_public_meta"}
# Path prefixes exempt from the scan (relative, POSIX-style).
ALLOW_PREFIXES = ("overlay/locales/", "tests/runs/", "docs/")
# Data files named explicitly for documentation (outside SCAN_SUFFIXES today,
# so they are never scanned — listed so the exemption is policy, not luck).
ALLOW_FILES = ("tests/ab_prompts.json",)


def rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def is_allowed(relative: str) -> bool:
    return relative in ALLOW_FILES or relative.startswith(ALLOW_PREFIXES)


def iter_source_files():
    for path in sorted(REPO.rglob("*")):
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
            continue
        relative = rel(path)
        parts = Path(relative).parts
        if any(p in SKIP_DIR_NAMES for p in parts):
            continue
        if is_allowed(relative):
            continue
        yield relative, path


def scan_cjk() -> tuple[list[str], int]:
    """-> (violations as 'file:line: text', files scanned)."""
    violations: list[str] = []
    scanned = 0
    for relative, path in iter_source_files():
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            violations.append(f"{relative}: 0: unreadable ({type(e).__name__})")
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if not CJK_RE.search(line):
                continue
            if INLINE_MARKER in line:
                continue                     # deliberate zh input data
            violations.append(f"{relative}:{n}: {line.strip()[:120]}")
    return violations, scanned


def main() -> int:
    from lib import i18n

    violations, scanned = scan_cjk()
    rep = i18n.validate()

    for v in violations:
        print(f"CJK   {v}")
    if not rep["ok"]:
        for e in rep["errors"]:
            print(f"PACK  {e}")
        for code in rep["missing_pack"]:
            print(f"PACK  missing default pack {code}.json")
        for code, keys in rep["missing_keys"].items():
            print(f"PACK  {code}: missing {len(keys)} key(s): {', '.join(keys[:8])}")
        for code, keys in rep["extra_keys"].items():
            print(f"PACK  {code}: extra {len(keys)} key(s): {', '.join(keys[:8])}")

    ok = not violations and rep["ok"]
    counts = ", ".join(f"{c}={n}" for c, n in rep["counts"].items()) or "no packs"
    print(f"{'OK' if ok else 'FAIL'} — source CJK violations: {len(violations)} "
          f"(files scanned: {scanned}); locale packs: {counts}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
