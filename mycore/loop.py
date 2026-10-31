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


# Car 5-D7 item 4 (recall before re-send): the marker rebuilt after every fold.
# Its prefix must stay distinct from compact's "[compacted " markers.
RECALL_MARKER = "[recalled "


def _latest_task(messages: list[dict]) -> str:
    """The live task line = the NEWEST user message (never folded: it lives in
    the kept tail). Bounded, plain text — the recall query, never re-sent."""
    for m in reversed(messages):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str) and c.strip():
            return c[:600]
        if isinstance(c, list):                    # multimodal-ish content blocks
            parts = [str(b.get("text", "")) for b in c
                     if isinstance(b, dict) and b.get("type") == "text"]
            if parts and "".join(parts).strip():
                return " ".join(parts)[:600]
    return ""


def _upsert_recall(messages: list[dict], content: str | None) -> None:
    """Install / refresh / drop the single `[recalled ...]` system marker.
    Never stacked (one marker maximum), placed right after the stable system
    block so the stable prefix keeps its byte order, system role so `compact`
    never folds it and every caller's `role != "system"` save/load filter never
    persists it (recall is re-derived per fold, not accumulated state)."""
    idx = None
    for i, m in enumerate(messages):
        if (isinstance(m, dict) and m.get("role") == "system"
                and isinstance(m.get("content"), str)
                and m["content"].startswith(RECALL_MARKER)):
            idx = i
            break
    if content is None:
        if idx is not None:
            del messages[idx]
        return
    if idx is not None:
        messages[idx] = {"role": "system", "content": content}
        return
    pos = next((i for i, m in enumerate(messages)
                if isinstance(m, dict) and m.get("role") != "system"),
               len(messages))
    messages.insert(pos, {"role": "system", "content": content})


def _recall_into(messages: list[dict], dropped: list[dict]) -> None:
    """Car 5-D7 item 4: after a fold, re-inject ONLY the FTS5-hit snippets of
    the just-folded turns that match the live task (plus where each came from),
    instead of re-sending the whole old dialogue. Nothing to recall -> the
    marker is removed, so an empty turn carries no extra token at all."""
    from .session import RECALL_LIMIT, recall_snippets
    task = _latest_task(messages)
    if not dropped or not task:
        _upsert_recall(messages, None)
        return
    doc = recall_snippets(dropped, task, limit=RECALL_LIMIT)
    hits = [h for h in (doc.get("hits") or [])
            if isinstance(h, dict) and str(h.get("text") or "").strip()]
    if not hits:
        _upsert_recall(messages, None)
        return
    lines = [f"[recalled {len(hits)} snippet(s) from folded history — method: "
             f"{doc.get('method', 'none')}, matched {doc.get('probes', 0)} "
             f"task term(s)]"]
    for h in hits:
        text = " ".join(str(h.get("text") or "").split())
        lines.append(f"- ({h.get('role') or '?'}) {text}")
    _upsert_recall(messages, "\n".join(lines))


class StopRequested(Exception):
    """Car 7 (D11 STOP): raised from the delta path when the operator's stop
    landed mid-stream. Caught in run_turn BEFORE the generic failure handler
    (which would mislabel it a model error) — model.py only catches
    ModelError, so it propagates cleanly through chat()."""


def run_turn(messages: list[dict], chat: ChatFn, registry: ToolRegistry,
             max_iters: int = DEFAULT_MAX_ITERS,
             on_event: Callable[[str, dict], None] | None = None,
             budget_chars: int | None = None,
             on_delta: Callable[[str], None] | None = None,
             budget_tokens: int | None = None,
             should_stop: Callable[[], bool] | None = None) -> TurnResult:
    """Drive one user turn to completion. Mutates `messages` in place (the
    caller owns session state); never raises for model/tool failures.

    `chat(**kwargs)` receives messages= and tools= and returns an OpenAI-style
    completion dict. `on_event(kind, data)` is an optional observer for
    logging/UI (kinds: model_call, tool_call, tool_result, compact, stop).
    `budget_chars`, when set, compacts `messages` in place before every model
    call (system constitution and newest turns survive — compact.py contract).
    `budget_tokens` (car 5-D7) adds a SECOND, token-denominated trigger: None
    derives it as `budget_chars // 4` (the "~6k tokens" intent the char budget
    always documented), 0 disables it. It only fires on a REAL chars->tokens
    ratio observed from this turn's own `usage.prompt_tokens` — before any
    observation the token rule is mathematically identical to the char rule,
    so an uncalibrated turn behaves exactly as it did before this car.
    `on_delta(text)`, when set, is forwarded to chat() as a live text preview
    per model call — it is attached ONLY when set, so a chat callable that
    does not know about streaming keeps working unchanged (and the final
    authoritative text always comes back through TurnResult).
    `should_stop` (car 7, D11 STOP), when set, is polled at three points:
    the top of every iteration, between a tool-call answer and its dispatch,
    and on every streamed delta. Any hit aborts the turn with
    stop="aborted" and an honest assistant note (never a fake failure):
    "[mycore: turn stopped by the operator (STOP) ...]". None (default)
    keeps today's behavior byte-for-byte.
    """
    emit = on_event or (lambda kind, data: None)
    tool_log: list[dict] = []
    tools = registry.schema()
    iters = 0
    total_calls = 0
    from .compact import (chars_for_tokens, compact,  # local import: optional path
                          measure)
    # Car 5-D7 item 2: token-denominated fold trigger + its calibration.
    if budget_tokens is None:
        token_budget = budget_chars // 4 if budget_chars else None
    else:
        token_budget = budget_tokens if budget_tokens > 0 else None
    ratio: float | None = None           # observed chars->tokens for THIS turn
    obs: tuple[int, int] | None = None   # (chars, prompt_tokens) of last call
    # Car 2-D6: expose this turn's chat + budget to meta-tools (delegate)
    # through the registry — the sub-agent reuses the SAME model connection,
    # never a credential of its own. Written before the first model call so
    # a tool dispatched in iteration 1 already sees a bound turn.
    bind = getattr(registry, "bind_turn", None)
    if callable(bind):
        bind(chat, budget_chars)

    def stop(reason: str, text: str) -> TurnResult:
        emit("stop", {"reason": reason, "iterations": iters})
        return TurnResult(text=text, iterations=iters, tool_calls=total_calls,
                          stop=reason, tool_log=tool_log)

    for it in range(1, max_iters + 1):
        iters = it
        # Car 7 (D11): STOP checked at the iteration top — a hit means the
        # model was never called for this iteration.
        if should_stop is not None and should_stop():
            note = (f"[mycore: turn stopped by the operator (STOP) after "
                    f"{max(0, it - 1)} completed iteration(s)]")
            messages.append({"role": "assistant", "content": note})
            return stop("aborted", note)
        chars_before = measure(messages)
        if budget_chars:
            eff_budget = budget_chars
            by = "chars"
            est: int | None = None
            if token_budget and ratio is not None:
                est = int(chars_before * ratio + 0.5)
                if est > token_budget:
                    # the token rule is stricter than the char rule here (a
                    # denser language/model) — fold to its char equivalent
                    by = "tokens"
                    eff_budget = min(budget_chars,
                                     chars_for_tokens(token_budget, ratio))
            if by == "tokens" or chars_before > budget_chars:
                pre = [m for m in messages if m.get("role") != "system"]
                before = len(messages)
                messages[:], folded = compact(messages, eff_budget)
                if folded:
                    now_ids = {id(m) for m in messages}
                    dropped = [m for m in pre if id(m) not in now_ids]
                    emit("compact", {"folded": folded, "from": before,
                                     "to": len(messages), "by": by,
                                     "budget_chars": eff_budget,
                                     "est_tokens": est,
                                     "ratio": (None if ratio is None
                                               else round(ratio, 4))})
                    # item 4: re-inject only what matches the live task
                    _recall_into(messages, dropped)
        emit("model_call", {"iteration": it})
        chat_kw: dict = {"messages": messages, "tools": tools}
        if on_delta:
            if should_stop is not None:
                # Mid-stream abort: every delta re-polls the flag (the caller
                # throttles the underlying state read); StopRequested is
                # caught immediately around chat() below.
                def _guarded_delta(piece: str) -> None:
                    if should_stop():
                        raise StopRequested()
                    on_delta(piece)
                chat_kw["on_delta"] = _guarded_delta
            else:
                chat_kw["on_delta"] = on_delta
        try:
            doc = chat(**chat_kw)
        except StopRequested:
            note = ("[mycore: turn stopped by the operator (STOP) "
                    "mid-stream]")
            messages.append({"role": "assistant", "content": note})
            return stop("aborted", note)
        except Exception as e:  # noqa: BLE001 — contract: honest stop, no raise
            from .model import ModelError as _ModelError
            # ModelError text is vetted (status codes / class names / endpoint
            # urls — never keys), and the failover chain annotates its final
            # failure with what was tried, so surfacing it is both safe and
            # necessary; any other exception keeps to the class name so
            # arbitrary text never reaches a reply.
            detail = f" — {e}" if isinstance(e, _ModelError) and str(e) else ""
            note = (f"[mycore: model call failed ({type(e).__name__}){detail}; "
                    f"turn stopped after {it - 1} iteration(s)]")
            messages.append({"role": "assistant", "content": note})
            return stop("model_error", note)

        from .model import first_choice_text, tool_calls as extract_calls
        # Car 5-D7 item 2 calibration: the provider's OWN prompt_tokens for the
        # payload we just sent. Two observations give the real chars->tokens
        # slope of this turn's messages (the tool schema is constant within a
        # turn, so it cancels out); one observation is not a slope, so the
        # ratio stays None and the fold rule stays exactly as it was.
        usage = doc.get("usage") if isinstance(doc, dict) else None
        if isinstance(usage, dict):
            tok = next((usage[k] for k in ("prompt_tokens", "input_tokens")
                        if type(usage.get(k)) is int), None)
            if tok is not None:
                if obs is not None:
                    dc = chars_before - obs[0]
                    dt = tok - obs[1]
                    if dc >= 400 and dt > 0:
                        ratio = max(0.05, min(3.0, dt / dc))
                obs = (chars_before, tok)
        calls = extract_calls(doc)
        text = first_choice_text(doc)

        if not calls:
            final = text or ""
            messages.append({"role": "assistant", "content": final})
            return stop("final", final)

        # Car 7 (D11): STOP between the model's tool-call answer and the
        # dispatch — the tools never run and no dangling tool_calls message
        # is persisted; the note says exactly that.
        if should_stop is not None and should_stop():
            note = ("[mycore: turn stopped by the operator (STOP) before "
                    f"tool dispatch ({len(calls)} call(s) skipped)]")
            messages.append({"role": "assistant", "content": note})
            return stop("aborted", note)

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
