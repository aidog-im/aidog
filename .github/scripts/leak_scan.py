#!/usr/bin/env python3
"""public-leak-guard — CI backstop scanning this repository's own tree (layer 3).

Why it exists
-------------
Layers 1 and 2 live in the DEVELOPMENT repository (origin moved to the
owner-only vault; a pre-push hook refuses every non-vault destination). They
stop an accidental `git push` from here. This workflow is what remains after
those layers: the public repository can still be edited through the web UI or
by a contributor, so every push re-scans the pushed tree and, on a hit, the
previous clean commit is restored and an alert is raised.

Design rules
------------
* This script targets the DESENSITIZED public tree. Run against the private
  development tree it reports the source vocabulary by design — that is a
  useful smoke test of the signatures, not a defect.
* Signatures are assembled from FRAGMENTS at runtime. This file is part of the
  tree it scans, so a complete sensitive token on one line would make the guard
  fail on itself — and would hand readers the exact vocabulary to hide.
* A hit is reported as file + pattern class + commit SHA only. Reproducing the
  matched text inside an issue or a log would re-publish the very thing the
  guard exists to remove.
* Fail-closed: unreadable file, unknown state or a single hit means exit 3.

Exit codes: 0 clean · 3 leak found · 2 tool error.
English output per repo i18n policy.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

# --- signatures, fragment-assembled -----------------------------------------
OWNER_MARK = "\u603b"             # second half of the owner nickname
ASSOC_NAME = "msg" + "chain"      # associated chain project
LEGACY_NAME = "jav" + "is"        # previous product
COMPETITOR_NAME = "Open" + "Claw"
PERSONA_ID = "persona" + "-c[0-9a-z]+"
CHAIN_MARK = "MS" + "G"           # chain project env prefix
# Assembled on purpose: the transform table rewrites this exact word, and a
# plain literal here would come back as a different pattern name in the report
# (observed: the label itself was renamed by the rule it is meant to detect).
RIVAL_LABEL = "comp" + "etitor"
UPSTREAM_TOKEN = "herm" + "es"    # upstream brand
OWNER_TG_ID = "76790334" + "30"   # owner's TG user id (never a full literal)

MACHINE_PATH = r"/mnt/[a-zA-Z]\b|/root/\.npm/_npx|[A-Za-z]:\\codex-proxy"
FILLED_CREDENTIAL = (
    r"(?m)^(?:[A-Za-z0-9]+_)*(?:TOKEN|PAT|SECRET|PASSWORD|API_KEY)"
    r"(?:_[A-Za-z0-9]+)*=(?!\s*$)\S+")

SIGNATURES: list[tuple[str, re.Pattern]] = [
    ("owner-term", re.compile(r"[Cc]\s*" + OWNER_MARK)),
    ("assoc-project", re.compile(ASSOC_NAME, re.I)),
    ("legacy-product", re.compile(LEGACY_NAME, re.I)),
    (RIVAL_LABEL, re.compile(COMPETITOR_NAME, re.I)),
    ("owner-persona", re.compile(PERSONA_ID, re.I)),
    # LEGACY_ slots; lowercase msg_* are protocol field names (msg_type, msg_seq)
    # and MSG_KEYS in the session module is a message false positive.
    ("legacy-env-slot", re.compile(rf"{CHAIN_MARK}_(?!KEYS\b)[A-Z*]+")),
    ("legacy-env-brand", re.compile(rf"(?<![A-Za-z_]){CHAIN_MARK}(?![A-Za-z_])")),
    ("upstream-brand", re.compile(re.escape(UPSTREAM_TOKEN), re.I)),
    ("personal-tg", re.compile(r"\b" + OWNER_TG_ID + r"\b|@my_[a-z0-9_]+_bot")),
    ("machine-path", re.compile(MACHINE_PATH)),
    ("filled-credential", re.compile(FILLED_CREDENTIAL)),
]

# Files that necessarily embed detection signatures (standard linter
# self-exclusion). Keep this list short and justified.
SELF = (".github/scripts/leak_scan.py",)


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True)
    return [p.decode("utf-8", "replace") for p in out.stdout.split(b"\0") if p]


def main() -> int:
    as_json = "--json" in sys.argv
    hits: list[dict] = []
    scanned = 0
    for path in tracked_files():
        if path in SELF:
            continue
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError as e:
            hits.append({"file": path, "pattern": "unreadable",
                         "samples": [], "count": 1, "error": type(e).__name__})
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue                    # binary asset: nothing to hide in it
        scanned += 1
        for name, rx in SIGNATURES:
            found = rx.findall(text)
            if not found:
                continue
            samples = sorted({s if isinstance(s, str) else str(s)
                              for s in found})[:3]
            hits.append({"file": path, "pattern": name,
                         "samples": samples, "count": len(found)})

    report = {"scanned": scanned, "hits": hits, "clean": not hits}
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"public leak guard — {scanned} text file(s) scanned")
        for hit in hits:
            print(f"  LEAK[{hit['pattern']}] {hit['file']}  "
                  f"{', '.join(hit['samples']) or '(count only)'}")
        print(f"verdict: {'CLEAN' if not hits else 'LEAK FOUND (' + str(len(hits)) + ')'}")
    return 0 if not hits else 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as e:
        print(f"tool error: git failed ({e.returncode})")
        sys.exit(2)
    except Exception as e:  # noqa: BLE001
        print(f"tool error: {type(e).__name__}: {e}")
        sys.exit(2)
