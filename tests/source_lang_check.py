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
# `vendor` = third-party code shipped byte-exact (web/static/vendor/): it is
# not our source, it carries the upstream's own message tables — which may
# legitimately hold non-English strings — and the publish leak scan still
# runs over it like any other tracked file.
# Nested sibling repos moved into this directory (owner decree 2026-10-28) hold
# their own CJK and upstream-brand material BY DESIGN — they are independent git
# repos and must stay invisible to this lint exactly as .gitignore keeps them
# out of git; without these entries the lint would walk into them.
SKIP_DIR_NAMES = {".git", "__pycache__", "node_modules", ".venv", "venv",
                  "data", "tmp", ".screenshots", ".voice", ".pi",
                  "aidog-internal", "aidog-skills", "aidog-public",
                  "_public_meta", "vendor"}
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


# --- P11 (round C): the two CJK surfaces the source scan cannot see --------
# Locale packs are exempt from scan_cjk() by design (they ARE the zh text),
# which is exactly how a Chinese value survived in the English pack until
# round A. Both legs below are walls against that class of regression.

def scan_en_pack_cjk() -> list[str]:
    """overlay/locales/en.json values must contain no CJK (P11)."""
    import json
    rel_path = "overlay/locales/en.json"
    try:
        doc = json.loads((REPO / "overlay" / "locales" / "en.json")
                         .read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"{rel_path}: unreadable ({type(e).__name__}: {e})"]
    if not isinstance(doc, dict):
        return [f"{rel_path}: top level must be an object"]
    return [f"{rel_path}: {k}: {str(v)[:90]}"
            for k, v in doc.items()
            if isinstance(v, str) and CJK_RE.search(v)]


def scan_defaults_cjk() -> list[str]:
    """The shipped DEFAULT_SETTINGS literals must contain no CJK (P11).

    AST-based on purpose: no import of the server (zero side effects) and the
    check keeps its promise even if a future refactor exempts these files from
    the source scan. Values that are *references* (``DEFAULT_REPORTING`` and
    friends) are covered line by line by scan_cjk() already — this leg owns
    every string LITERAL inside the defaults expression.
    """
    import ast
    out: list[str] = []
    for rel_path in ("web/server.py",):
        p = REPO / rel_path
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as e:
            out.append(f"{rel_path}: defaults scan failed ({type(e).__name__}: {e})")
            continue
        found = False
        for node in ast.walk(tree):
            targets = []
            value = None
            if isinstance(node, ast.Assign):        # DEFAULT_SETTINGS = {...}
                targets = [t for t in node.targets if isinstance(t, ast.Name)]
                value = node.value
            elif isinstance(node, ast.AnnAssign):   # DEFAULT_SETTINGS: dict = {...}
                if isinstance(node.target, ast.Name):
                    targets = [node.target]
                value = node.value
            if not any(t.id == "DEFAULT_SETTINGS" for t in targets):
                continue
            found = True
            if value is None:
                out.append(f"{rel_path}: DEFAULT_SETTINGS has no value")
                continue
            for sub in ast.walk(value):
                if (isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                        and CJK_RE.search(sub.value)):
                    out.append(f"{rel_path}: DEFAULT_SETTINGS literal: "
                               f"{sub.value[:90]}")
        if not found:
            out.append(f"{rel_path}: DEFAULT_SETTINGS not found (P11 expectation)")
    return out


def main() -> int:
    from lib import i18n

    violations, scanned = scan_cjk()
    rep = i18n.validate()
    en_pack_cjk = scan_en_pack_cjk()      # P11 leg 1: the English pack
    defaults_cjk = scan_defaults_cjk()    # P11 leg 2: shipped defaults (AST)

    for v in violations:
        print(f"CJK   {v}")
    for v in en_pack_cjk:
        print(f"CJKEN {v}")
    for v in defaults_cjk:
        print(f"CJKDF {v}")
    if not rep["ok"]:
        for e in rep["errors"]:
            print(f"PACK  {e}")
        for code in rep["missing_pack"]:
            print(f"PACK  missing default pack {code}.json")
        for code, keys in rep["missing_keys"].items():
            print(f"PACK  {code}: missing {len(keys)} key(s): {', '.join(keys[:8])}")
        for code, keys in rep["extra_keys"].items():
            print(f"PACK  {code}: extra {len(keys)} key(s): {', '.join(keys[:8])}")

    ok = (not violations and not en_pack_cjk and not defaults_cjk
          and rep["ok"])
    counts = ", ".join(f"{c}={n}" for c, n in rep["counts"].items()) or "no packs"
    print(f"{'OK' if ok else 'FAIL'} — source CJK violations: {len(violations)} "
          f"(files scanned: {scanned}); locale packs: {counts}; "
          f"P11 en-pack CJK: {len(en_pack_cjk)}, defaults CJK: {len(defaults_cjk)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
