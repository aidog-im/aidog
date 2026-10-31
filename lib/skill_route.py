#!/usr/bin/env python3
"""skill_route — deterministic skill pre-router (TOP3 before any SKILL.md body load).

Why this exists (feature spec: docs/features/memory_and_routing.md):
  1. Skill frontmatter descriptions stay resident in the system prompt as cheap
     triggers; a body (SKILL.md) is worth reading only once a skill is chosen.
  2. Vector memory answers "what do I remember", never "which skill do I load".
  3. This router adds the deterministic layer that survives model swaps and
     cold starts: reproducible token matching instead of model intuition.

Vendoring note (P4): the transition-era single implementation lived in a
sibling project; this copy is genericized — no machine paths, no cross-project
exec, English output, skill roots resolved from flags/env/config/overlay.

Usage:
  python3 lib/skill_route.py "task description"
  python3 lib/skill_route.py --json "task description"
  python3 lib/skill_route.py --dirs /path/a:/path/b "task description"
  python3 lib/skill_route.py                 # no args -> read stdin

Skill roots, in resolution order (deduplicated, existing dirs only):
  1. --dirs (os.pathsep-separated)
  2. AIDOG_SKILL_DIRS env var (os.pathsep-separated)
  3. SKILL_DIRS in deploy/memory_sources.local (gitignored machine config,
     os.pathsep-separated — P5-4, same local-config layer as memory)
  4. <repo>/overlay/skills (repo-embedded product body-layer default)
  5. profile config.yaml `external_dirs:` — LEGACY transition step, read only
     while deploy/upstream_shim.env exists (deleted in the P5-5 same-car swap;
     absent shim = skipped, product behavior unaffected)

Matching algorithm (stdlib only, deterministic):
  - corpus per skill = name + frontmatter description (fallback: H1 + first
    prose paragraph) + directory name
  - tokens = ASCII stems (strip plural/-ing/-ed) + CJK bigrams (single char
    kept as-is for one-char runs)
  - score = ASCII hit x2 + CJK bigram hit x1 + description whole-phrase (>=2
    chars) hit x2 + name/directory word hit x3
  - output TOP3: name | matched-token reasons | load verdict (TOP1 required)

Exit codes: 0 = hits, 3 = no hits (not an error), 2 = bad arguments.
English output per repo i18n policy.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SKILLS_DIR = REPO / "overlay" / "skills"

ASCII_SUFFIX = ("ing", "ed", "es", "s")
STOP_ASCII = {"the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "is", "with"}


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def parse_frontmatter(text: str) -> tuple[str | None, str | None]:
    """-> (name, description) from YAML-ish frontmatter; (None, None) if absent."""
    if not text.startswith("---"):
        return None, None
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    if not m:
        return None, None
    name = desc = None
    for line in m.group(1).splitlines():
        mn = re.match(r"^name:\s*(.*)$", line.strip())
        if mn:
            name = mn.group(1).strip().strip("'\"")
        md = re.match(r"^description:\s*(.*)$", line.strip())
        if md:
            desc = md.group(1).strip().strip("'\"")
    return name, desc


def fallback_desc(text: str) -> str:
    """No frontmatter -> H1 + first prose line (cap 200 chars)."""
    lines = [l for l in text.splitlines() if l.strip()]
    head = lines[0].lstrip("# ").strip() if lines else ""
    body = ""
    for l in lines[1:]:
        s = l.lstrip("> ").strip()
        if s and not s.startswith("#"):
            body = s
            break
    return (head + " | " + body)[:200]


def stem(word: str) -> str:
    w = word.lower()
    for suf in ASCII_SUFFIX:
        if len(w) > 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def tokenize(text: str) -> tuple[set[str], set[str]]:
    """-> (ascii_stems, cjk_bigrams)"""
    ascii_words = {stem(w) for w in re.findall(r"[A-Za-z][A-Za-z0-9_]+", text)} - STOP_ASCII
    bigrams: set[str] = set()
    for run in re.findall(r"[一-鿿]+", text):
        if len(run) == 1:
            bigrams.add(run)
        else:
            bigrams.update(run[i:i + 2] for i in range(len(run) - 1))
    return ascii_words, bigrams


def cjk_runs(text: str) -> list[str]:
    return re.findall(r"[一-鿿]{2,}", text)


def local_config_dirs() -> list[str]:
    """Machine-local skill dirs: SKILL_DIRS in deploy/memory_sources.local
    (gitignored, os.pathsep-separated). Fail-soft: missing file/key -> []."""
    try:
        text = (REPO / "deploy" / "memory_sources.local").read_text(encoding="utf-8")
    except OSError:
        return []
    m = re.search(r"(?m)^\s*SKILL_DIRS\s*=\s*(.+)$", text)
    if not m:
        return []
    value = m.group(1).split("#", 1)[0].strip()  # tolerate trailing comments
    return [p.strip() for p in value.split(os.pathsep) if p.strip()][:16]


def profile_config_dirs() -> list[str]:
    """LEGACY transition step: read `external_dirs:` from the profile config
    (path derived from deploy/upstream_shim.env — no literal machine path).
    Honored ONLY while that shim file exists; P5-5 deletes it and this returns
    []. Any failure yields no dirs; the product path never depends on this file."""
    cfg = Path(os.devnull)
    try:
        shim = (REPO / "deploy" / "upstream_shim.env").read_text()
    except OSError:
        return []
    m = re.search(r'(?m)^export AIDOG_PROFILE_DIR="([^"]+)"$', shim)
    if not m:
        return []
    cfg = Path(m.group(1)) / "config.yaml"
    text = read_text(cfg)
    if text is None:
        return []
    dirs: list[str] = []
    in_ext = False
    for ln in text.splitlines():
        s = ln.strip()
        if not in_ext:
            if s == "external_dirs:":
                in_ext = True
            continue
        if s.startswith("- "):
            dirs.append(s[2:].strip())
        elif s and not s.startswith("#"):
            break
    return dirs[:8]


def skill_roots(dirs_flag: str | None) -> list[Path]:
    """Roots in the documented order: flag > env > local config > repo default
    > legacy profile config (shim-gated). Deduped, existing dirs only."""
    raw: list[str] = []
    if dirs_flag:
        raw += [p for p in dirs_flag.split(os.pathsep) if p]
    env = os.environ.get("AIDOG_SKILL_DIRS", "")
    raw += [p for p in env.split(os.pathsep) if p]
    raw += local_config_dirs()
    roots: list[Path] = []
    for p in raw:
        try:
            cand = Path(p).expanduser()
        except (OSError, ValueError):
            continue
        if cand.is_dir() and cand not in roots:
            roots.append(cand)
    # repo-embedded default comes before the legacy profile step (P5-4 chain)
    if DEFAULT_SKILLS_DIR.is_dir() and DEFAULT_SKILLS_DIR not in roots:
        roots.append(DEFAULT_SKILLS_DIR)
    for p in profile_config_dirs():
        try:
            cand = Path(p).expanduser()
        except (OSError, ValueError):
            continue
        if cand.is_dir() and cand not in roots:
            roots.append(cand)
    return roots


def load_skills(roots: list[Path]) -> list[dict]:
    out: list[dict] = []
    for root in roots:
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            text = read_text(child / "SKILL.md")
            if text is None:
                continue
            name, desc = parse_frontmatter(text)
            if not desc:
                desc = fallback_desc(text)
            name = name or child.name
            corpus = f"{name} {child.name} {desc}"
            a, c = tokenize(corpus)
            out.append({
                "dir": child.name, "name": name, "desc": desc,
                "ascii": a | {stem(t) for t in re.split(r"[-_/]", child.name)},
                "bigrams": c,
                "runs": set(cjk_runs(corpus)),
            })
    return out


def route(query: str, roots: list[Path], topn: int = 3) -> list[dict]:
    qa, qc = tokenize(query)
    q_runs = cjk_runs(query)
    scored = []
    for s in load_skills(roots):
        matched_ascii = sorted(qa & s["ascii"])
        matched_bi = sorted(qc & s["bigrams"])
        matched_phrase = sorted(r for r in q_runs if r in s["desc"] or r in s["name"])
        name_hit = bool({stem(t) for t in re.split(r"[-_/]", s["name"])} & qa)
        score = (2 * len(matched_ascii) + 1 * len(matched_bi)
                 + 2 * len(matched_phrase) + (3 if name_hit else 0))
        if score <= 0:
            continue
        reasons = []
        if matched_phrase:
            reasons.append('phrase "%s"' % " / ".join(matched_phrase[:3]))
        if matched_ascii:
            reasons.append("stem %s" % "/".join(matched_ascii[:4]))
        if matched_bi:
            reasons.append('desc contains "%s"' % "/".join(matched_bi[:4]))
        if name_hit:
            reasons.append("name match")
        scored.append({"name": s["name"], "dir": s["dir"], "score": score,
                       "reason": "; ".join(reasons), "desc": s["desc"]})
    scored.sort(key=lambda x: (-x["score"], x["name"]))
    return scored[:topn]


def main() -> int:
    ap = argparse.ArgumentParser(description="deterministic skill pre-router (TOP3)")
    ap.add_argument("query", nargs="?", help="task description")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--topn", type=int, default=3)
    ap.add_argument("--dirs", default=None,
                    help="skill roots, %s-separated (overrides env/config)" % os.pathsep)
    args = ap.parse_args()
    q = (args.query or "").strip() or sys.stdin.read().strip()
    if not q:
        print("usage: skill_route.py \"task description\"", file=sys.stderr)
        return 2
    roots = skill_roots(args.dirs)
    hits = route(q, roots, args.topn)
    if args.json:
        print(json.dumps({"query": q, "roots": [str(r) for r in roots],
                          "hits": hits, "top1_required": bool(hits)},
                         ensure_ascii=False, indent=2))
        return 0 if hits else 3
    print("task: %s" % q)
    if not hits:
        print("no matching skill — use the generic procedure")
        return 3
    for i, h in enumerate(hits, 1):
        verdict = "YES (TOP1, must load)" if i == 1 else "only if closely related (max 1 more)"
        print("%d. %s | matched: %s | load: %s" % (i, h["name"], h["reason"], verdict))
    return 0


if __name__ == "__main__":
    sys.exit(main())
