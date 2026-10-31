#!/usr/bin/env python3
"""P4 router vendoring test — lib/skill_route.py contract + ranking sanity.

Runs offline (no network), touches no credentials. Exit 0 = all assertions passed.
Contracts locked here (regression wall for the publish-gate precondition):
  - exit codes: 0 = hits, 3 = no hits, 2 = bad args
  - --json emits {query, roots, hits, top1_required} (English reasons)
  - determinism: same query twice -> identical JSON
  - ranking sanity on a fixture skill dir (TOP1 = phrase/name-heavy skill)
  - no cross-project exec: wrapper resolves its own directory only
  - frontmatter compat (car D3): quoted name/description, |/> block scalars,
    multi-line quoted description, unknown/nested fields tolerated, absent /
    unterminated frontmatter -> H1+prose fallback, --json extra fields
    (license/allowed-tools/version; present-only, existing keys untouched)
English output per repo i18n policy.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROUTER = REPO / "lib" / "skill_route.py"
WRAPPER = REPO / "lib" / "skill_route.sh"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


def run(args: list[str], stdin: str = "") -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(ROUTER), *args],
                       input=stdin, capture_output=True, text=True, timeout=30)
    return p.returncode, p.stdout


def make_fixture(root: Path) -> Path:
    """Two synthetic skills whose frontmatter drives a predictable ranking."""
    a = root / "tone-audit"
    a.mkdir(parents=True)
    (a / "SKILL.md").write_text(
        "---\n"
        "name: tone-audit\n"
        "description: 发推语气检查 and tone lint for public posts\n"  # i18n-allow: zh routing input
        "---\n\n# tone-audit\nbody\n", encoding="utf-8")
    b = root / "mail-box"
    b.mkdir(parents=True)
    (b / "SKILL.md").write_text(
        "---\n"
        "name: mail-box\n"
        "description: inbox triage for reader mail\n"
        "---\n\n# mail-box\nbody\n", encoding="utf-8")
    return root


def make_fixture_frontmatter(root: Path) -> Path:
    """Frontmatter-compat fixture (car D3). Directory names deliberately
    differ from `name:` values so quote-stripping vs dir-name fallback is
    observable in --json output."""
    q = root / "quote-dir"
    q.mkdir(parents=True)
    (q / "SKILL.md").write_text(
        "---\n"
        'name: "quoted-skill"\n'
        "description: 'verbatim yaml: changelog drafting for release notes'\n"
        "license: Apache-2.0\n"
        "allowed-tools: Read, Grep\n"
        "version: 3.1\n"
        "unknown-field: should be tolerated\n"
        "metadata:\n"
        "  nested-key: ignored\n"
        "---\n\n# quoted-skill\nbody\n", encoding="utf-8")

    b = root / "block-dir"
    b.mkdir(parents=True)
    (b / "SKILL.md").write_text(
        "---\n"
        "name: block-skill\n"
        "description: |-\n"
        "  multi line release notes summary\n"
        "  second line mentions changelog archiving\n"
        "tags: also-tolerated\n"
        "---\n\n# block-skill\nbody\n", encoding="utf-8")

    f = root / "fold-dir"
    f.mkdir(parents=True)
    (f / "SKILL.md").write_text(
        "---\n"
        "name: fold-skill\n"
        "description: >\n"
        "  folded widget inventory spans\n"
        "  across two physical lines\n"
        "---\n\n# fold-skill\nbody\n", encoding="utf-8")

    m = root / "flow-dir"
    m.mkdir(parents=True)
    (m / "SKILL.md").write_text(
        "---\n"
        "name: flow-skill\n"
        'description: "flow quoted desc starts\n'
        '  continues onto second line"\n'
        "---\n\n# flow-skill\nbody\n", encoding="utf-8")

    n = root / "no-fm-dir"
    n.mkdir(parents=True)
    (n / "SKILL.md").write_text(
        "# no-fm-heading\nfirst prose line about quokka surveys\nbody continues\n",
        encoding="utf-8")

    o = root / "open-fm-dir"
    o.mkdir(parents=True)
    (o / "SKILL.md").write_text(
        "---\n\n# open-fm-heading\nprose about wombat census counts\n",
        encoding="utf-8")
    return root


def main() -> int:
    check("router file present", ROUTER.is_file())
    check("wrapper file present", WRAPPER.is_file())

    # --- frontmatter compatibility (car D3) ---
    with tempfile.TemporaryDirectory() as td:
        fxf = make_fixture_frontmatter(Path(td))

        def jhits(query: str) -> tuple[int, dict, list]:
            rc, out = run(["--json", "--dirs", str(fxf), query])
            try:
                doc = json.loads(out)
            except ValueError:
                doc = {}
            return rc, doc, (doc.get("hits") or [])

        rc, doc, hits = jhits("changelog drafting release notes")
        h0 = hits[0] if hits else {}
        check("fm: quoted fixture exits 0", rc == 0, f"rc={rc}")
        check("fm: TOP1 name is quote-stripped", h0.get("name") == "quoted-skill",
              str(h0)[:120])
        check("fm: single-quoted description stripped",
              h0.get("desc") == "verbatim yaml: changelog drafting for release notes",
              repr(h0.get("desc"))[:120])
        check("fm: license surfaced in --json", h0.get("license") == "Apache-2.0",
              str(sorted(h0))[:120])
        check("fm: allowed-tools surfaced in --json",
              h0.get("allowed-tools") == "Read, Grep", str(sorted(h0))[:120])
        check("fm: version surfaced in --json", h0.get("version") == "3.1",
              str(sorted(h0))[:120])
        leaked = {"unknown-field", "metadata", "nested-key"} & set(h0)
        check("fm: unknown/nested keys never leak into hits", not leaked,
              str(sorted(leaked)))

        rc, doc, hits = jhits("changelog archiving")
        h0 = hits[0] if hits else {}
        check("fm: block-scalar fixture exits 0", rc == 0, f"rc={rc}")
        check("fm: TOP1 = block-skill", h0.get("name") == "block-skill",
              str(h0)[:120])
        desc = h0.get("desc") or ""
        check("fm: multi-line description kept verbatim",
              "\n" in desc and "second line mentions changelog archiving" in desc,
              repr(desc)[:120])
        check("fm: absent license omitted from hit", "license" not in h0,
              str(sorted(h0))[:120])

        rc, doc, hits = jhits("folded widget inventory")
        h0 = hits[0] if hits else {}
        check("fm: folded scalar joins lines into one",
              rc == 0 and h0.get("name") == "fold-skill"
              and h0.get("desc") == "folded widget inventory spans across two physical lines"
              and "\n" not in (h0.get("desc") or ""), repr(h0.get("desc"))[:120])

        rc, doc, hits = jhits("flow quoted desc")
        h0 = hits[0] if hits else {}
        check("fm: multi-line quoted description folds to one line",
              rc == 0 and h0.get("name") == "flow-skill"
              and h0.get("desc") == "flow quoted desc starts continues onto second line",
              repr(h0.get("desc"))[:120])

        rc, doc, hits = jhits("quokka surveys")
        h0 = hits[0] if hits else {}
        check("fm: no-frontmatter -> H1+prose fallback",
              rc == 0 and h0.get("name") == "no-fm-dir"
              and (h0.get("desc") or "").startswith("no-fm-heading | first prose line about quokka"),
              str(h0)[:120])

        rc, doc, hits = jhits("wombat census")
        h0 = hits[0] if hits else {}
        check("fm: unterminated frontmatter -> dir name + prose fallback",
              rc == 0 and h0.get("name") == "open-fm-dir"
              and "prose about wombat census counts" in (h0.get("desc") or ""),
              str(h0)[:120])

    # --- exit code contract ---
    with tempfile.TemporaryDirectory() as td:
        fx = make_fixture(Path(td))
        rc, out = run(["--dirs", str(fx), "发推语气检查"])  # i18n-allow: zh routing input
        check("hit query -> exit 0", rc == 0, f"rc={rc}")
        check("hit query -> TOP1 tone-audit", "tone-audit" in out.splitlines()[1] if out else False,
              out[:120])

        rc, out = run(["--dirs", str(fx), "quantum chromodynamics lattice"])
        check("no-hit query -> exit 3", rc == 3, f"rc={rc}")
        check("no-hit prints generic-procedure line", "generic procedure" in out)

        rc, _ = run([], stdin="")
        check("empty input -> exit 2", rc == 2, f"rc={rc}")

        # --- json contract + determinism ---
        rc, out1 = run(["--json", "--dirs", str(fx), "发推语气检查"])  # i18n-allow: zh routing input
        rc2, out2 = run(["--json", "--dirs", str(fx), "发推语气检查"])  # i18n-allow: zh routing input
        ok_json = False
        try:
            doc = json.loads(out1)
            ok_json = (isinstance(doc.get("hits"), list) and "top1_required" in doc
                       and "roots" in doc and doc.get("query") == "发推语气检查")  # i18n-allow: zh routing input
            ok_json = ok_json and all("reason" in h for h in doc["hits"])
        except ValueError:
            doc = {}
        check("--json shape", ok_json, out1[:120])
        check("deterministic ranking", out1 == out2 and rc == rc2 == 0)

        # English reason labels (i18n policy); matched tokens may be CJK because
        # the query itself may be Chinese — only the label wording must be English.
        labels = ("phrase ", "stem ", "desc contains ", "name match")
        segs = [s.strip() for h in doc.get("hits", [])
                for s in h.get("reason", "").split(";") if s.strip()]
        labels_ok = bool(segs) and all(s.startswith(labels) for s in segs)
        check("reason labels are English", labels_ok,
              "; ".join(segs)[:80])

    # --- wrapper: self-derived path, no machine/cross-project exec ---
    wsrc = WRAPPER.read_text(encoding="utf-8")
    check("wrapper has no machine path", "/mnt/" not in wsrc and "/root/" not in wsrc)
    check("wrapper resolves own dir", 'HERE="$(cd "$(dirname' in wsrc)
    check("wrapper execs sibling python", '"$HERE/skill_route.py"' in wsrc)
    # Detection patterns embedded as regexes (same convention as
    # deploy/check_publish.py): the pattern source itself never matches the
    # machine-path signature it hunts for.
    machine_path = re.compile(r"/mnt/[a-zA-Z]/TEST")
    check("router source has no machine path",
          not machine_path.search(ROUTER.read_text(encoding="utf-8")))

    # --- profile-config discovery must fail soft (shim may be absent/changed) ---
    rc, out = run(["--json", '{"never valid as query via flag": 1}'])
    check("bad invocation still exits cleanly (0/2/3 only)", rc in (0, 2, 3), f"rc={rc}")

    print(f"\n{len(PASS)} pass · {len(FAIL)} fail")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
