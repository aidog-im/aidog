"""mycore.assembly — prompt assembly from the body layer (P4-1b).

The kernel is swappable; the body layer is us (README/design three-layer rule).
This module builds the SYSTEM message stack that `run_turn` receives:

  1. constitution  -> <repo>/overlay/constitution/SOUL.md   (never dropped)
  2. curated memory-> <repo>/overlay/memory/{MEMORY,USER}_curated.md (never dropped)
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
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONSTITUTION = REPO / "overlay" / "constitution" / "SOUL.md"
MEMORY_SLOTS = (REPO / "overlay" / "memory" / "MEMORY_curated.md",
                REPO / "overlay" / "memory" / "USER_curated.md")
MAX_SLOT_CHARS = 8000        # per-file cap (budget discipline, honest truncation)


def _read_slot(path: Path, cap: int = MAX_SLOT_CHARS) -> str | None:
    """Read one body file with a size cap; None = missing/unreadable."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.strip():
        return None
    return text if len(text) <= cap else text[:cap] + f"\n... [truncated at {cap} chars]"


def build_system_stack(constitution_path: Path = CONSTITUTION,
                       memory_paths: tuple[Path, ...] = MEMORY_SLOTS) -> list[dict]:
    """-> system messages ready for the loop. Absent files become notices."""
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

    stack.append({"role": "system",
                  "content": "[body layer] Skills: call the skill_route tool BEFORE reading "
                             "any SKILL.md file; load TOP1 only, max 2 bodies per turn. "
                             "Reply to Owner in Simplified Chinese; code/logs stay English."})
    return stack


def stack_digest(stack: list[dict]) -> str:
    """Short fingerprint of the system stack — for session meta / A/B logs
    (lets two runs prove they used the same body layer without embedding it)."""
    joined = "\n".join(str(m.get("content", "")) for m in stack)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
