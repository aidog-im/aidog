"""mycore.subagent — isolated parallel task delegation (car 2-D6).

The `delegate` tool (registered in tools.py) hands 1..3 independent tasks to
sub-agents that run IN PARALLEL threads with:

  * isolated context — each sub-agent gets a fresh message list (one fixed
    system line + the task); the parent conversation never enters, and the
    sub-agent's transcript never returns — the parent context pays only for
    the condensed per-task block in the delegate result;
  * isolated tools — a read-only default set (read_file/grep/glob/
    http_fetch/skill_route); write-capable tools only when the caller names
    them in `tools` (validated against the parent registry);
  * a recursion guard — `delegate` and `execute_code` can NEVER be granted to
    a sub-agent (no nested delegation, no nested folding; also required
    because execute_code's handler closes over the registry it was built in,
    so re-using it under a subset would leak the parent tool set);
  * the parent turn's model connection — `run_turn` binds chat + budget into
    the registry (loop.py), the delegate handler passes them through; a
    sub-agent never reads credentials itself.

Each task runs through the SAME loop.run_turn (honest stop reasons, bounded
iterations); one task's model failure does not fail its siblings, and no
sub-agent ever raises into the parent. Results are capped per task so the
folded delegate output always fits the tool-output budget.

Stdlib only; English comments (repo i18n policy).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .loop import run_turn
from .tools import ToolError, ToolRegistry

MAX_PARALLEL = 3                    # alignment target: 3 parallel slots
SUB_MAX_ITERS = 12                  # bounded sub-turn (parent loop: 25)
TASK_RESULT_CAP = 6000              # per-task text cap inside the fold
DEFAULT_READ_ONLY = ("read_file", "grep", "glob", "http_fetch", "skill_route")
NEVER_GRANTED = ("delegate", "execute_code")   # recursion / leak guard

SYSTEM_LINE = ("You are an isolated sub-agent of the main assistant. "
               "Complete EXACTLY the task you are given with the tools "
               "provided; do not ask the user questions — make reasonable "
               "assumptions and state them. Reply with the result only: "
               "concise, factual, ready for the parent to use.")


def _select(registry: ToolRegistry, wanted: list[str]) -> list[str]:
    """Resolve the sub-agent tool set: read-only default + caller-named
    extras. Raises ToolError with an honest message on an unknown name or a
    recursion-guarded name (never silently drops what the caller asked for)."""
    available = set(registry.names())
    unknown = [n for n in wanted if n not in available]
    if unknown:
        raise ToolError(f"unknown tool(s): {', '.join(sorted(unknown))}")
    blocked = [n for n in wanted if n in NEVER_GRANTED]
    if blocked:
        raise ToolError("sub-agents cannot be granted: "
                        f"{', '.join(sorted(blocked))} (recursion guard)")
    granted = [n for n in wanted if n not in NEVER_GRANTED]
    return sorted(set(DEFAULT_READ_ONLY) | set(granted))


def _one(task: str, idx: int, total: int, sub_registry: ToolRegistry,
         chat: Callable[..., dict], budget: int | None) -> str:
    """Run one sub-agent turn. Never raises; every outcome is a labelled
    block so the parent sees exactly which task produced what."""
    messages = [{"role": "system", "content": SYSTEM_LINE},
                {"role": "user", "content": task}]
    try:
        res = run_turn(messages, chat, sub_registry,
                       max_iters=SUB_MAX_ITERS, budget_chars=budget)
    except Exception as e:  # noqa: BLE001 — belt: run_turn never raises
        return f"[{idx}/{total}] failed ({type(e).__name__})"
    body = res.text.strip()
    if not body:
        body = f"(no output; stopped: {res.stop})"
    elif res.stop != "final":
        body += f"\n(stop: {res.stop})"
    if len(body) > TASK_RESULT_CAP:
        body = body[:TASK_RESULT_CAP] + f"... [+{len(body) - TASK_RESULT_CAP} chars]"
    return f"[{idx}/{total} · {res.stop} · {res.tool_calls} tool call(s)]\n{body}"


def run_tasks(tasks: list, registry: ToolRegistry, extra_tools: list | None,
              chat: Callable[..., dict] | None, budget: int | None) -> str:
    """Validate + fan out up to MAX_PARALLEL tasks in parallel, fold the
    results. Raises ToolError for caller mistakes (validation), never for a
    sub-agent's runtime outcome."""
    if not isinstance(tasks, list) or not tasks:
        raise ToolError("tasks must be a non-empty array of task strings")
    if len(tasks) > MAX_PARALLEL:
        raise ToolError(f"at most {MAX_PARALLEL} tasks per call "
                        f"(got {len(tasks)})")
    clean: list[str] = []
    for i, t in enumerate(tasks, 1):
        if not isinstance(t, str) or not t.strip():
            raise ToolError(f"task {i} must be a non-empty string")
        clean.append(t)
    if chat is None:
        raise ToolError("no model connection bound to this turn "
                        "(delegate is only available inside a run_turn)")
    if extra_tools is not None and not isinstance(extra_tools, list):
        raise ToolError("tools must be an array of tool names")
    names = _select(registry, [t for t in (extra_tools or [])
                               if isinstance(t, str)])
    non_str = [t for t in (extra_tools or []) if not isinstance(t, str)]
    if non_str:
        raise ToolError("tools entries must be tool-name strings")
    sub_registry = registry.select(names)
    total = len(clean)
    if total == 1:
        return _one(clean[0], 1, 1, sub_registry, chat, budget)
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, total)) as pool:
        parts = list(pool.map(
            lambda it: _one(it[1], it[0], total, sub_registry, chat, budget),
            enumerate(clean, 1)))
    return "\n\n".join(parts)
