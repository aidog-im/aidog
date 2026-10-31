"""mycore.assembly — prompt assembly from the body layer (P4-1b).

The kernel is swappable; the body layer is us (README/design three-layer rule).
This module builds the SYSTEM message stack that `run_turn` receives:

  1. constitution  -> <repo>/overlay/constitution/SOUL.md   (never dropped)
  2. curated memory-> <repo>/overlay/memory/{MEMORY,USER}_curated.md (never dropped)
  2b. learned slot-> <repo>/overlay/memory/EVOLUTION_learned.md — OPTIONAL:
                      appended only when present, silently skipped when absent
                      (the institutional learn loop owns the file; a missing
                      one is the normal fresh-install state, never an error)
  2c. project ctx  -> AGENTS.md / CLAUDE.md / AGENTS.local.md from the
                      CONFIGURED context dirs (env AIDOG_CONTEXT_DIRS or
                      CONSOLE settings mycore.context_dirs, os pathsep list).
                      OFF by default (no config = no change to the stack);
                      present-only per file like the learned slot — a project
                      without these files is the normal state. The repo's own
                      root AGENTS.md is deliberately NOT auto-discovered: the
                      operator opts a directory in explicitly. Line-based
                      '@<path>' references inside these files expand to the
                      referenced file's content (depth 1, deny/missing/limit
                      markers, MAX_CTX_REFS bound) — see _expand_context_refs.
  3. skill hints   -> skill descriptions stay OUT of the prompt by design; the
                      loop calls the skill_route tool instead (lazy bodies,
                      docs/features/memory_and_routing.md)

Any missing/oversized body file degrades honestly: the missing piece is
replaced by a one-line notice in the stack — assembly never fabricates content
and never raises for absent files.
English comments per repo i18n policy.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Car F (§13.1): every body/settings path below is resolved at CALL time from
# the active profile — never cached at import, so AIDOG_PROFILE flips (and the
# offline suite's env save/restore) always win.
from lib import profile_paths as pp  # noqa: E402

MAX_SLOT_CHARS = 8000        # per-file cap (budget discipline, honest truncation)
MAX_CTX_REFS = 16            # @reference bound per context file (abuse/budget cap)

# Project context files (car 1-D1): injected only from explicitly configured
# directories — see resolve_context_dirs(). Order = injection priority.
CONTEXT_FILE_NAMES = ("AGENTS.md", "CLAUDE.md", "AGENTS.local.md")
CONTEXT_DIRS_ENV = "AIDOG_CONTEXT_DIRS"      # os.pathsep-separated dirs
CONTEXT_SETTINGS_KEY = "context_dirs"        # settings mycore block (same form)

# "unspecified" marker for build_system_stack: distinguishes a caller that
# never mentioned the learned slot from an explicit learned_path=None (= off,
# unchanged contract). Paths themselves are resolved via pp at call time.
_AUTO = object()


def _read_slot(path: Path, cap: int = MAX_SLOT_CHARS) -> str | None:
    """Read one body file with a size cap; None = missing/unreadable."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.strip():
        return None
    return text if len(text) <= cap else text[:cap] + f"\n... [truncated at {cap} chars]"


def _expand_context_refs(text: str, base: Path) -> str:
    """Expand LINE-BASED '@' references in one project context file.

    Grammar: a line whose stripped form is '@<path>' with a non-empty <path>
    references a file resolved relative to `base` — the directory of the
    context file that contains the line. Any other line passes through
    untouched, so a file with no such line comes out BYTE-IDENTICAL.

    Boundaries (credential discipline): the resolved target must stay inside
    `base` (Path.resolve() + is_relative_to — symlink-safe); a traversal or an
    absolute path escaping the dir becomes the visible
    '[context @ref denied: <raw line>]' marker. Missing/empty/unreadable
    targets — and directories — become '[context @ref missing: <raw line>]'.
    Expansion is DEPTH 1: '@' lines inside referenced content stay literal
    (no recursion). At most MAX_CTX_REFS references are expanded per context
    file; further reference lines become '[context @ref limit: <raw line>]'.
    Reads reuse _read_slot (same per-file cap). Never raises; used only for
    configured project context — constitution/memory/learned slots untouched.
    """
    try:
        base_res = base.resolve()
    except OSError:
        base_res = base
    out: list[str] = []
    used = 0
    for line in text.split("\n"):
        stripped = line.strip()
        ref = stripped[1:].strip() if stripped.startswith("@") else ""
        if not ref:                       # plain line, or '@' with empty rest
            out.append(line)
            continue
        if used >= MAX_CTX_REFS:
            out.append(f"[context @ref limit: {line}]")
            continue
        used += 1
        try:
            target = (base_res / ref).resolve()
        except (OSError, ValueError):
            out.append(f"[context @ref denied: {line}]")
            continue
        if not target.is_relative_to(base_res):   # traversal / absolute outside
            out.append(f"[context @ref denied: {line}]")
            continue
        content = _read_slot(target)      # depth 1: nested '@' lines NOT expanded
        out.append(content if content is not None
                    else f"[context @ref missing: {line}]")
    return "\n".join(out)


def resolve_context_dirs(settings_path: Path | None = None) -> tuple[Path, ...]:
    """Configured project-context dirs (car 1-D1), precedence env > settings >
    off. Form: one string, dirs separated by os.pathsep. Configured dirs that
    do not exist are dropped silently (present-only — a settings edit takes
    effect on the next turn, no error). Never raises: unreadable/invalid
    settings = feature off."""
    raw = os.environ.get(CONTEXT_DIRS_ENV)
    if not (isinstance(raw, str) and raw.strip()):
        raw = None
    if raw is None:
        try:
            stored = json.loads(
                (settings_path or pp.settings_path(REPO)).read_text(
                    encoding="utf-8"))
            mc = stored.get("mycore") if isinstance(stored, dict) else None
            cand = mc.get(CONTEXT_SETTINGS_KEY) if isinstance(mc, dict) else None
            raw = cand if isinstance(cand, str) and cand.strip() else None
        except (OSError, ValueError, AttributeError):
            raw = None
    if not isinstance(raw, str):
        return ()
    dirs: list[Path] = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        try:
            p = Path(part).expanduser()
            if p not in dirs:
                dirs.append(p)
        except (OSError, ValueError):
            continue
    return tuple(d for d in dirs if d.is_dir())


# ------------------------------------------------------------------ rules --
# Car 7 (D11 obedience): the operator's rules & forbidden-zones object. Grammar
# lives HERE (single source of truth for the console write path and the
# stack builder); the priority paragraph is model-visible and written out
# verbatim so a conflict is disclosed, never silently resolved.
DEFAULT_OBEDIENCE = {"rules": [], "forbidden": []}
OBEDIENCE_MAX_ENTRIES = 64          # per list (abuse/budget cap)
OBEDIENCE_MAX_CHARS = 500           # per entry (single line)


def validate_obedience(doc: object) -> tuple[dict, list[str]]:
    """Grammar for the settings `obedience` block -> (clean, errors).
    Fail-closed: wrong shape, non-string entries, overlong/multiline text,
    duplicates or too many entries are all reported; the offending VALUE is
    never echoed (an operator rule can quote anything)."""
    if not isinstance(doc, dict):
        return {}, ["obedience must be an object"]
    errors: list[str] = []
    clean: dict = {}
    for key in doc:
        if key not in DEFAULT_OBEDIENCE:
            errors.append(f"unknown obedience key: {key}")
    for key in ("rules", "forbidden"):
        if key not in doc:
            continue
        val = doc[key]
        if isinstance(val, bool) or not isinstance(val, list):
            errors.append(f"obedience.{key} must be a list of strings")
            continue
        if len(val) > OBEDIENCE_MAX_ENTRIES:
            errors.append(
                f"obedience.{key} holds at most {OBEDIENCE_MAX_ENTRIES} entries")
            continue
        bad = None
        seen: set = set()
        for item in val:
            if not isinstance(item, str) or not item.strip() \
                    or item != item.strip() \
                    or len(item) > OBEDIENCE_MAX_CHARS \
                    or any(ord(c) < 32 or ord(c) == 127 for c in item):
                bad = "entry"
                break
            if item in seen:
                bad = "duplicate"
                break
            seen.add(item)
        if bad == "duplicate":
            errors.append(f"obedience.{key} has a duplicate entry")
            continue
        if bad is not None:
            errors.append(
                f"obedience.{key} entries must be non-empty single-line "
                f"strings of at most {OBEDIENCE_MAX_CHARS} chars "
                f"(trimmed, no control characters)")
            continue
        clean[key] = list(val)
    return clean, errors


def resolve_user_rules(settings_path: Path | None = None) -> dict | None:
    """The stored `obedience` block with at least one entry -> dict, else
    None (absent/empty/invalid = the pre-car-7 stack, byte-for-byte). Reads
    the RAW settings file at call time (profile seam, never cached) and
    re-validates, so a hand-edited file can only shrink what is injected."""
    try:
        stored = json.loads(
            (settings_path or pp.settings_path(REPO)).read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(stored, dict):
        return None
    clean, errors = validate_obedience(stored.get("obedience"))
    if errors and not clean:
        return None
    rules = clean.get("rules") or []
    forbidden = clean.get("forbidden") or []
    if not rules and not forbidden:
        return None
    return {"rules": rules, "forbidden": forbidden}


def _obedience_message(rules: dict) -> str:
    """The injected system message (car 7 / D11 constitution_over_rules):
    priority order stated EXACTLY as ruled — constitution > these user rules
    > skills/learned > model discretion — plus the mandatory conflict
    disclosure the model must make in its reply."""
    lines = [
        "[user rules — priority: BELOW the constitution, ABOVE skills/learned]",
        "",
        "Fixed priority order (never re-rank):",
        "1) constitution (SOUL.md) — its hard rules always win;",
        "2) these user rules and forbidden zones;",
        "3) skills and learned notes;",
        "4) your own judgment.",
        "Conflict rule: on ANY collision the constitution wins. You must say "
        "so explicitly in the same reply — name the constitution rule you "
        "applied AND the user rule you did not (template: 'following the "
        "constitution, rule N; your rule \'...\' was not applied'). Never "
        "resolve a conflict silently.",
    ]
    if rules.get("rules"):
        lines.append("")
        lines.append("User rules:")
        lines.extend(f"- {r}" for r in rules["rules"])
    if rules.get("forbidden"):
        lines.append("")
        lines.append("Forbidden zones (never do these, no matter who asks):")
        lines.extend(f"- {f}" for f in rules["forbidden"])
    return "\n".join(lines)


def build_system_stack(constitution_path: Path | None = None,
                       memory_paths: tuple[Path, ...] | None = None,
                       learned_path: Path | None | object = _AUTO,
                       context_dirs: tuple[Path, ...] = (),
                       user_rules: dict | None = None) -> list[dict]:
    """-> system messages ready for the loop. Absent files become notices
    (the optional learned slot and the configured context dirs are the silent
    exceptions — see module doc). context_dirs defaults to OFF so every
    existing caller keeps today's stack shape; turn paths that opt in pass
    resolve_context_dirs().

    Body-layer defaults (car F) resolve the ACTIVE profile at call time:
    None -> pp.constitution_path()/pp.memory_slots(); the learned slot keeps
    its old default (read when present) via the _AUTO sentinel, and an
    explicit learned_path=None still disables it. Explicit paths (every test
    and every caller that passes them) behave exactly as before.

    `user_rules` (car 7 / D11) defaults to OFF so every existing caller and
    test keeps today's stack shape byte-for-byte; production turn paths pass
    resolve_user_rules(). When a non-empty block is given it is injected
    IMMEDIATELY after the constitution (priority order is part of the
    message), never anywhere else."""
    constitution_path = constitution_path or pp.constitution_path(REPO)
    if memory_paths is None:
        memory_paths = pp.memory_slots(REPO)
    if learned_path is _AUTO:
        learned_path = pp.learned_path(REPO)
    stack: list[dict] = []

    cons = _read_slot(constitution_path)
    if cons is None:
        stack.append({"role": "system",
                      "content": "[body layer] constitution file missing — "
                                 "operating without SOUL.md; reload it before sensitive actions."})
    else:
        stack.append({"role": "system", "content": cons})

    # Car 7 (D11): operator rules sit directly after the constitution —
    # the message itself states the fixed priority order, so position and
    # wording agree. Empty/off -> not appended (pre-car-7 stack unchanged).
    if user_rules:
        stack.append({"role": "system",
                      "content": _obedience_message(user_rules)})

    for p in memory_paths:
        text = _read_slot(p)
        if text is None:
            stack.append({"role": "system",
                          "content": f"[body layer] memory slot missing: {p.name} — "
                                     "treat personal context as unknown, ask instead of guessing."})
        else:
            stack.append({"role": "system", "content": text})

    if learned_path is not None:
        learned = _read_slot(learned_path)
        if learned is not None:                # present-only: absent = normal
            stack.append({"role": "system", "content": learned})

    # 2c. configured project context — present-only per file (a missing
    # AGENTS.md/CLAUDE.md is the normal state, never a notice), injected
    # before the skill hint so that hint stays the last entry.
    for d in context_dirs:
        for name in CONTEXT_FILE_NAMES:
            text = _read_slot(d / name)
            if text is None:
                continue
            stack.append({"role": "system",
                          "content": f"[project context] {d / name}:\n"
                                     f"{_expand_context_refs(text, d)}"})

    stack.append({"role": "system",
                  "content": "[body layer] Skills: call the skill_route tool BEFORE reading "
                             "any SKILL.md file; load TOP1 only, max 2 bodies per turn. "
                             "Mirror the user's language in conversation; user-facing "
                             "strings follow the active locale pack (default en); "
                             "code/logs stay English."})
    return stack


def stack_digest(stack: list[dict]) -> str:
    """Short fingerprint of the system stack — for session meta / A/B logs
    (lets two runs prove they used the same body layer without embedding it)."""
    joined = "\n".join(str(m.get("content", "")) for m in stack)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
