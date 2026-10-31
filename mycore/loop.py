"""mycore.loop — the agent loop (design §14.3: model-agnostic function-calling).

One turn of work:

    user message -> [model] -> tool_calls? -> dispatch -> results -> [model] ...
    ... until the model returns final text (or max_iters stops it honestly)

Depends only on the README contracts: a `chat(messages, tools) -> dict` callable
(any provider adapter or test fake) and a ToolRegistry whose dispatch never
raises. No network, no credentials, no provider specifics live here.
English comments per repo i18n policy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .tools import ToolRegistry

ChatFn = Callable[..., dict]          # same shape as ChatClient.chat
DEFAULT_MAX_ITERS = 25


@dataclass
class TurnResult:
    """Outcome of one user turn, honestly reporting how it ended."""
    text: str                         # final assistant text (may be "" on hard stop)
    iterations: int                   # model calls made
    tool_calls: int                   # tool dispatches performed
    stop: str                         # "final" | "max_iters" | "model_error"
    tool_log: list[dict] = field(default_factory=list)   # {name, ok, preview}


def _tool_result_message(call_id: str, name: str, result: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id or name,
            "name": name, "content": result}


def run_turn(messages: list[dict], chat: ChatFn, registry: ToolRegistry,
             max_iters: int = DEFAULT_MAX_ITERS,
             on_event: Callable[[str, dict], None] | None = None,
             budget_chars: int | None = None) -> TurnResult:
    """Drive one user turn to completion. Mutates `messages` in place (the
    caller owns session state); never raises for model/tool failures.

    `chat(**kwargs)` receives messages= and tools= and returns an OpenAI-style
    completion dict. `on_event(kind, data)` is an optional observer for
    logging/UI (kinds: model_call, tool_call, tool_result, compact, stop).
    `budget_chars`, when set, compacts `messages` in place before every model
    call (system constitution and newest turns survive — compact.py contract).
    """
    emit = on_event or (lambda kind, data: None)
    tool_log: list[dict] = []
    tools = registry.schema()
    iters = 0
    total_calls = 0

    def stop(reason: str, text: str) -> TurnResult:
        emit("stop", {"reason": reason, "iterations": iters})
        return TurnResult(text=text, iterations=iters, tool_calls=total_calls,
                          stop=reason, tool_log=tool_log)

    for it in range(1, max_iters + 1):
        iters = it
        if budget_chars:
            from .compact import compact, measure   # local import: optional path
            if measure(messages) > budget_chars:
                before = len(messages)
                messages[:], folded = compact(messages, budget_chars)
                if folded:
                    emit("compact", {"folded": folded, "from": before,
                                     "to": len(messages)})
        emit("model_call", {"iteration": it})
        try:
            doc = chat(messages=messages, tools=tools)
        except Exception as e:  # noqa: BLE001 — contract: honest stop, no raise
            note = (f"[mycore: model call failed ({type(e).__name__}); "
                    f"turn stopped after {it - 1} iteration(s)]")
            messages.append({"role": "assistant", "content": note})
            return stop("model_error", note)

        from .model import first_choice_text, tool_calls as extract_calls
        calls = extract_calls(doc)
        text = first_choice_text(doc)

        if not calls:
            final = text or ""
            messages.append({"role": "assistant", "content": final})
            return stop("final", final)

        # Model asked for tools: record its message (content may accompany calls),
        # dispatch each call (dispatch never raises), feed results back.
        messages.append(doc["choices"][0]["message"])
        for call in calls:
            total_calls += 1
            emit("tool_call", {"name": call["name"], "args": call["arguments"]})
            result = registry.dispatch(call["name"], call["arguments"])
            ok = not result.startswith("ERROR:")
            preview = result if len(result) <= 160 else result[:160] + "…"
            tool_log.append({"name": call["name"], "ok": ok, "preview": preview})
            emit("tool_result", {"name": call["name"], "ok": ok})
            messages.append(_tool_result_message(call["id"], call["name"], result))

    note = (f"[mycore: stopped after {max_iters} iterations without a final "
            f"answer; {total_calls} tool call(s) executed]")
    messages.append({"role": "assistant", "content": note})
    return stop("max_iters", note)
