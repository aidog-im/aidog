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
  - frontmatter parse = conservative YAML subset (quoted scalars incl.
    multi-line quoted values, |/> block scalars; unknown/nested/malformed
    lines tolerated, never fatal); optional license/allowed-tools/version
    surface in --json hits (present-only)
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


# --- frontmatter: conservative YAML subset, aligned to the SKILL.md + YAML ---
# --- frontmatter convention (anthropics/skills / agentskills.io; field list ---
# --- sourced from the in-repo skills-ecosystem survey under docs/research/, ---
# --- skills section B). Tolerance contract: unknown keys, nested-looking ---
# --- indents and malformed lines are skipped, never fatal; ---
# --- absent/unterminated frontmatter -> {}. --------------------------------

FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.S)
KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+):[ \t]*(.*)$")
BLOCK_RE = re.compile(r"^[|>][+-]?[0-9]*$")
QUOTED_RE = re.compile(r"^([\"'])(.*?)\1[ \t]*(?:#.*)?$")

# Optional frontmatter fields surfaced in --json hits: emitted only when the
# skill declares a non-empty value; never removes or renames existing keys.
JSON_EXTRA_FIELDS = ("license", "allowed-tools", "version")


def _fold_quoted(lines: list[str]) -> str:
    """YAML flow-scalar folding: a break between text folds to a space, a
    blank line stays a break; trailing breaks are clipped."""
    out: list[str] = []
    buf: list[str] = []
    for ln in lines:
        if ln.strip():
            buf.append(ln.strip())
        else:
            if buf:
                out.append(" ".join(buf))
                buf = []
            out.append("\n")
    if buf:
        out.append(" ".join(buf))
    return "".join(out).rstrip("\n")


def _block_scalar(style: str, lines_: list[str]) -> str:
    """Literal (|) keeps line breaks; folded (>) joins text lines with
    spaces. Indentation = min indent of non-blank lines; trailing blanks
    clipped (YAML clip semantics)."""
    nonblank = [ln for ln in lines_ if ln.strip()]
    if not nonblank:
        return ""
    indent = min(len(ln) - len(ln.lstrip()) for ln in nonblank)
    content = [ln[indent:] if ln.strip() else "" for ln in lines_]
    if style == "|":
        return "\n".join(content).rstrip("\n")
    return _fold_quoted(content)


def _unquote(quote: str, body: str) -> str:
    if quote == "'":
        return body.replace("''", "'")
    return re.sub(r'\\(["\\])', r"\1", body)


def _plain_scalar(raw: str) -> str:
    """Unquoted value: YAML comment rule — a '#' preceded by whitespace ends
    the scalar; a leading '#' means an empty (null) value. Otherwise verbatim."""
    if raw.startswith("#"):
        return ""
    cm = re.search(r"\s#", raw)
    return raw[: cm.start()].rstrip() if cm else raw


def _read_multiline_quoted(lines: list[str], start: int, quote: str,
                            head: str) -> tuple[str, int]:
    """Quoted scalar opened on the key line but not closed there: consume the
    following indented/blank lines as one flow scalar (multi-line quoted
    description). -> (value, index of the last consumed line)."""
    pieces = [head]
    last = start
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if not ln.strip():
            pieces.append("")
            last = j
            continue
        if not ln[0].isspace():
            break  # unterminated: stop, the next line is parsed as a key
        s = ln.strip()
        pos = s.find(quote)
        if pos >= 0:
            pieces.append(s[:pos])
            return _unquote(quote, _fold_quoted(pieces)), j
        pieces.append(s)
        last = j
    return _unquote(quote, _fold_quoted(pieces)), last


def _parse_frontmatter_fields(text: str) -> dict[str, str]:
    """-> flat {key: value} map of the frontmatter; {} if absent/unterminated."""
    if not text.startswith("---"):
        return {}
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    lines = m.group(1).splitlines()
    fields: dict[str, str] = {}
    i = 0
    while i < len(lines):
        km = KEY_RE.match(lines[i])
        if not km:  # blanks, comments, nested indents, garbage -> tolerated
            i += 1
            continue
        key, rest = km.group(1), km.group(2).strip()
        if BLOCK_RE.match(rest):
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j][0].isspace()):
                j += 1
            block = lines[i + 1:j]
            while block and not block[-1].strip():
                block.pop()
            fields[key] = _block_scalar(rest[0], block)
            i = j - 1
        elif rest[:1] in ("'", '"') and not QUOTED_RE.match(rest):
            value, last = _read_multiline_quoted(lines, i, rest[0], rest[1:])
            fields[key] = value
            i = last
        else:
            qm = QUOTED_RE.match(rest)
            fields[key] = _unquote(qm.group(1), qm.group(2)) if qm else _plain_scalar(rest)
        i += 1
    return fields


def parse_frontmatter_fields(text: str) -> dict[str, str]:
    """Best-effort frontmatter parse -> flat {key: value} (YAML subset above).
    Fail-soft by contract: an unexpected shape degrades to {} so a malformed
    skill file can never crash the router (routing falls back to H1+prose)."""
    try:
        return _parse_frontmatter_fields(text)
    except Exception:  # noqa: BLE001 — frontmatter tolerance is the contract
        return {}


def parse_frontmatter(text: str) -> tuple[str | None, str | None]:
    """-> (name, description) from YAML-ish frontmatter; (None, None) if absent."""
    fields = parse_frontmatter_fields(text)
    return fields.get("name") or None, fields.get("description") or None


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
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            bigrams.add(run)
        else:
            bigrams.update(run[i:i + 2] for i in range(len(run) - 1))
    return ascii_words, bigrams


def cjk_runs(text: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fff]{2,}", text)


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
            fields = parse_frontmatter_fields(text)
            desc = fields.get("description") or None
            if not desc:
                desc = fallback_desc(text)
            name = fields.get("name") or child.name
            corpus = f"{name} {child.name} {desc}"
            a, c = tokenize(corpus)
            out.append({
                "dir": child.name, "name": name, "desc": desc,
                "extra": {k: fields[k] for k in JSON_EXTRA_FIELDS if fields.get(k)},
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
        entry = {"name": s["name"], "dir": s["dir"], "score": score,
                 "reason": "; ".join(reasons), "desc": s["desc"]}
        entry.update(s.get("extra") or {})  # optional frontmatter fields,
        scored.append(entry)                # present-only (license etc.)
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
