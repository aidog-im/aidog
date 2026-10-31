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
                      operator opts a directory in explicitly.
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
CONSTITUTION = REPO / "overlay" / "constitution" / "SOUL.md"
MEMORY_SLOTS = (REPO / "overlay" / "memory" / "MEMORY_curated.md",
                REPO / "overlay" / "memory" / "USER_curated.md")
LEARNED_MEMORY = REPO / "overlay" / "memory" / "EVOLUTION_learned.md"
MAX_SLOT_CHARS = 8000        # per-file cap (budget discipline, honest truncation)

# Project context files (car 1-D1): injected only from explicitly configured
# directories — see resolve_context_dirs(). Order = injection priority.
CONTEXT_FILE_NAMES = ("AGENTS.md", "CLAUDE.md", "AGENTS.local.md")
CONTEXT_DIRS_ENV = "AIDOG_CONTEXT_DIRS"      # os.pathsep-separated dirs
CONTEXT_SETTINGS_KEY = "context_dirs"        # settings mycore block (same form)
SETTINGS_FILE = REPO / "web" / "settings.json"


def _read_slot(path: Path, cap: int = MAX_SLOT_CHARS) -> str | None:
    """Read one body file with a size cap; None = missing/unreadable."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.strip():
        return None
    return text if len(text) <= cap else text[:cap] + f"\n... [truncated at {cap} chars]"


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
            stored = json.loads((settings_path or SETTINGS_FILE).read_text(encoding="utf-8"))
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


def build_system_stack(constitution_path: Path = CONSTITUTION,
                       memory_paths: tuple[Path, ...] = MEMORY_SLOTS,
                       learned_path: Path | None = LEARNED_MEMORY,
                       context_dirs: tuple[Path, ...] = ()) -> list[dict]:
    """-> system messages ready for the loop. Absent files become notices
    (the optional learned slot and the configured context dirs are the silent
    exceptions — see module doc). context_dirs defaults to OFF so every
    existing caller keeps today's stack shape; turn paths that opt in pass
    resolve_context_dirs()."""
    stack: list[dict] = []

    cons = _read_slot(constitution_path)
    if cons is None:
        stack.append({"role": "system",
                      "content": "[body layer] constitution file missing — "
                                 "operating without SOUL.md; reload it before sensitive actions."})
    else:
        stack.append({"role": "system", "content": cons})

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
                          "content": f"[project context] {d / name}:\n{text}"})

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
