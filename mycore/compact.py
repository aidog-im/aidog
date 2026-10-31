"""mycore.compact — simple context compaction (design §14.3: 简版压缩).

Budgeted folding of old turns: when the accumulated message payload exceeds
`budget_chars`, the oldest non-system turns are collapsed into a single
synthetic summary marker; system/constitution messages are NEVER dropped
(README contract 6 — the body layer outlives any kernel swap).

Deliberately dumb (summarize = one fixed line per folded turn): honest,
deterministic, zero dependencies. Smarter summarization can replace the
`fold` step later without changing callers.
English comments per repo i18n policy.
"""
from __future__ import annotations

from .model import first_choice_text

SUMMARY_ROLE = "system"      # folded marker is a system-role note (never re-folded)
KEEP_TAIL_TURNS = 6          # always keep the newest turns intact (dialogue quality)


def measure(messages: list[dict]) -> int:
    """Payload size proxy: sum of content/argument string lengths."""
    total = 0
    for m in messages:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):            # multimodal-ish content blocks
            total += sum(len(str(c)) for c in content)
        for c in (m.get("tool_calls") or []):
            total += len(str(c))
    return total


def _fold_turn(msg: dict) -> dict:
    """One old turn -> one summary line (kept small, kept forever)."""
    role = msg.get("role", "?")
    content = msg.get("content")
    if isinstance(content, list):
        content = " ".join(str(c.get("text", c)) if isinstance(c, dict) else str(c)
                           for c in content)
    content = (content or "").strip()
    calls = msg.get("tool_calls") or []
    if calls:
        names = ", ".join((c.get("function") or {}).get("name", "?") for c in calls)
        return {"role": SUMMARY_ROLE,
                "content": f"[compacted {role} turn: tools used -> {names}]"}
    snippet = content[:160] + ("…" if len(content) > 160 else "")
    return {"role": SUMMARY_ROLE, "content": f"[compacted {role} turn: {snippet}]"}


def _is_marker(m: object) -> bool:
    """True when m is a compaction marker produced by this module."""
    return (isinstance(m, dict) and m.get("role") == SUMMARY_ROLE
            and isinstance(m.get("content"), str)
            and m["content"].startswith("[compacted "))


def compact(messages: list[dict], budget_chars: int,
            keep_tail: int = KEEP_TAIL_TURNS) -> tuple[list[dict], int]:
    """Fold oldest non-system turns until `measure() <= budget_chars`.

    Returns (new_messages, folded_count). Never touches system messages and
    never folds the newest `keep_tail` messages. Deterministic: same input ->
    same output. If the tail alone exceeds the budget, nothing is folded
    (honest: we do not delete live dialogue — the caller may raise the budget
    or the model call will simply be long).
    """
    if budget_chars <= 0:
        raise ValueError("budget_chars must be positive")
    if measure(messages) <= budget_chars:
        return messages, 0

    result = list(messages)
    folded = 0
    while measure(result) > budget_chars:
        # fold the oldest foldable turn: not system, not an already-folded
        # marker, and inside the region kept intact at the tail
        target = None
        for i in range(len(result) - keep_tail):
            m = result[i]
            if not isinstance(m, dict):
                continue
            if m.get("role") == "system":
                continue                                # constitution never folds
            content = m.get("content")
            if isinstance(content, str) and content.startswith("[compacted "):
                continue                                # never re-fold a marker
            target = i
            break
        if target is None:
            break                    # only system/newest remain — honest stop
        result[target] = _fold_turn(result[target])
        folded += 1

    # Consolidation: the markers themselves are content and can outgrow the
    # budget. Merge foldable markers once into a single line (never nested,
    # never touching the live tail). If still over after that, stop honestly:
    # live dialogue is never deleted.
    if measure(result) > budget_chars:
        hi = len(result) - keep_tail
        idxs = [i for i in range(hi) if _is_marker(result[i])]
        if len(idxs) >= 2:
            snippets = []
            for i in idxs:
                s = result[i]["content"]
                s = s[len("[compacted "):-1] if s.endswith("]") else s
                # "user turn: xxx" -> keep the payload part, strip any nesting
                payload = s.split(": ", 1)[1] if ": " in s else s
                snippets.append(payload[len("[compacted "):] if payload.startswith("[compacted ") else payload)
            joined = "; ".join(snippets)
            if len(joined) > 460:
                joined = joined[:460] + "…"
            merged = {"role": SUMMARY_ROLE,
                      "content": "[compacted %d turns: %s]" % (len(idxs), joined)}
            removed = set(idxs[1:])
            result = [merged if i == idxs[0] else m
                      for i, m in enumerate(result) if i not in removed]
            folded += len(idxs) - 1

    return result, folded


def summarize_choice(doc: dict) -> str:
    """One-line summary from a model completion (used by future smarter folding)."""
    return (first_choice_text(doc) or "").strip()[:300]
