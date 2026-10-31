"""mycore.cli — one-shot runner for the dual-brain A/B harness (tests/ab_protocol.md).

    python3 -m mycore.cli "prompt"                     # body layer assembled, live model
    python3 -m mycore.cli --session /tmp/s1.jsonl ...  # persist turns (JSONL)
    python3 -m mycore.cli --no-body "..."              # raw system stack off
    MYCORE_BASE_URL=... MYCORE_MODEL=...               # endpoint overrides

Credential rule (README contract 2): the key is read from the environment
inside the process; argv carries prompts only, never secrets.
Exit codes: 0 = final answer, 1 = model/transport failure, 2 = bad arguments.
English output per repo i18n policy.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .assembly import build_system_stack, stack_digest
from .loop import run_turn
from .model import ChatClient, ModelConfig, ModelError
from .session import Session
from .tools import build_registry

DEFAULT_BUDGET_CHARS = 24000        # ~6k tokens of conversation payload
REPO = Path(__file__).resolve().parent.parent


def resolve_model_config() -> ModelConfig:
    """Endpoint precedence (owner 2026-10-18 user-configurable decision):
    env MYCORE_* > CONSOLE settings (web/settings.json mycore block) > defaults."""
    cfg = ModelConfig()
    try:
        stored = json.loads((REPO / "web" / "settings.json").read_text())
        mc = stored.get("mycore") if isinstance(stored, dict) else None
        if isinstance(mc, dict):
            if isinstance(mc.get("base_url"), str) and mc["base_url"]:
                cfg.base_url = mc["base_url"]
            if isinstance(mc.get("model"), str) and mc["model"]:
                cfg.model = mc["model"]
    except (OSError, ValueError):
        pass
    if os.environ.get("MYCORE_BASE_URL"):
        cfg.base_url = os.environ["MYCORE_BASE_URL"]
    if os.environ.get("MYCORE_MODEL"):
        cfg.model = os.environ["MYCORE_MODEL"]
    return cfg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mycore.cli", description="mycore one-shot runner")
    ap.add_argument("prompt", nargs="?", help="user prompt (one shot)")
    ap.add_argument("--max-iters", type=int, default=25)
    ap.add_argument("--system", default=None, help="extra system message text")
    ap.add_argument("--no-body", action="store_true",
                    help="skip the body layer (constitution/memory assembly)")
    ap.add_argument("--session", default=None,
                    help="JSONL session file: load history, append this turn")
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET_CHARS,
                    help="compaction budget in chars (0 = never compact)")
    ap.add_argument("--quiet", action="store_true", help="print only the final answer")
    args = ap.parse_args(argv)
    prompt = (args.prompt or "").strip() or sys.stdin.read().strip()
    if not prompt:
        print("usage: python3 -m mycore.cli \"prompt\"", file=sys.stderr)
        return 2

    cfg = resolve_model_config()
    client = ChatClient(cfg)

    # ---- message stack: persisted history + body layer + extra system + user ----
    sess: Session | None = None
    messages: list[dict] = []
    if args.session:
        sess = Session(args.session)
        messages = sess.load()
        if not messages and not sess.path.exists():
            sess = Session.create(sess.path, meta={"model": cfg.model})
        elif sess.corrupt and not args.quiet:
            print(f"[mycore] session: skipped {sess.corrupt} corrupt line(s)",
                  file=sys.stderr)

    body: list[dict] = []
    if not args.no_body:
        body = build_system_stack()
    system_msgs = body + ([{"role": "system", "content": args.system}]
                          if args.system else [])
    # Session files never persist system messages (the body stack is rebuilt
    # fresh each run — constitution/memory must reflect the repo, not a stale
    # snapshot). Strip any system lines a legacy file may carry, then prepend.
    history = [m for m in messages if m.get("role") != "system"]
    messages = system_msgs + history
    persist_from = len(messages)          # user prompt must persist with the turn
    messages.append({"role": "user", "content": prompt})

    if not args.quiet:
        print(f"[mycore] {cfg.describe()}", file=sys.stderr)
        print(f"[mycore] body_stack={stack_digest(system_msgs)} "
              f"budget={args.budget or 'off'} session={args.session or 'none'}",
              file=sys.stderr)

    def on_event(kind: str, data: dict) -> None:
        if args.quiet:
            return
        if kind == "tool_call":
            print(f"[tool] {data['name']} {str(data['args'])[:120]}", file=sys.stderr)
        elif kind == "compact":
            print(f"[compact] folded {data['folded']}: {data['from']} -> {data['to']}",
                  file=sys.stderr)
        elif kind == "stop":
            print(f"[stop] {data['reason']} after {data['iterations']} iter(s)",
                  file=sys.stderr)

    start_len = persist_from
    try:
        result = run_turn(messages, client.chat, build_registry(),
                          max_iters=args.max_iters, on_event=on_event,
                          budget_chars=args.budget or None)
    except ModelError as e:
        print(f"model error: {e}", file=sys.stderr)
        return 1

    if sess is not None:
        sess.append_many(messages[start_len:])   # persist this turn's delta
        if not args.quiet:
            print(f"[mycore] session appended -> {sess.path} "
                  f"({sess.size()} bytes total)", file=sys.stderr)

    print(result.text)
    return 0 if result.stop == "final" else 1


if __name__ == "__main__":
    sys.exit(main())
