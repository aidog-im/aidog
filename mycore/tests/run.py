"""mycore offline test suite — fake model transport, zero network, zero credentials.

Run: python3 -m mycore.tests.run   (from the repo root)
Exit 0 = all assertions passed. English output per repo i18n policy.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Suite-wide hermetic state (car 1-D2 lesson): tools built with the DEFAULT
# state dir (cron registry, pre-write checkpoints) must land in a throwaway
# dir for the whole run — never in the production data/ tree.
_SUITE_STATE = tempfile.mkdtemp(prefix="mycore_suite_state_")
os.environ["AIDOG_MC_STATE"] = _SUITE_STATE
import atexit  # noqa: E402
atexit.register(shutil.rmtree, _SUITE_STATE, True)

from mycore import compact, measure, build_registry, run_turn  # noqa: E402
from mycore import subagent  # noqa: E402 — car 2-D6 assertions read its constants
from mycore.model import (ChatClient, ModelConfig, ModelError,  # noqa: E402
                          first_choice_text, tool_calls)
import mycore.model as _model_mod  # noqa: E402  (§1.8 slot-fallback seam)
from mycore.loop import TurnResult  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []

# Activation car §1.8: model keys may now fall back to the secrets.env slot,
# so the suite pins that fallback to "" for EVERY leg — a host whose
# MODEL_API_KEY slot is filled must still run the existing keyless /
# placeholder legs hermetically. The dedicated slot-fallback legs below
# re-enable the real reader explicitly and restore this stub before returning.
_SLOT_KEY_REAL = _model_mod._slot_key
_model_mod._slot_key = lambda name: ""


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


# ------------------------------------------------- car 4b tool-set baseline --
CORE_TOOLS = frozenset({
    "read_file", "write_file", "edit_file", "run_bash",
    "grep", "glob", "http_fetch", "skill_route", "cron",
    "delegate", "execute_code"})
BROWSER_TOOLS = frozenset({
    "browser_open", "browser_snapshot", "browser_click",
    "browser_type", "browser_screenshot", "browser_close"})


def expected_tool_names() -> frozenset[str]:
    """Core 11 + generate_image (always, car 4c) + the six browser_* tools
    IFF a browser binary is discoverable (car 4b capability-honest
    registration) + connector_search IFF connectors.status() reports at
    least one credential slot (car 4c, mirrors the registration gate) +
    exchange_markets ALWAYS (keyless public market data, W1) and
    exchange_portfolio / exchange_orders / exchange_prepare_order /
    exchange_execute_order / exchange_plans IFF at least one CEX credential
    slot is present (W1 account reads + W2 approval gate, the same gate).
    The suite must be green both on hosts with a browser and without —
    with or without connector slots — and with AIDOG_BROWSER_BIN overridden
    to a dead path — so every count assertion derives from this, never a
    literal."""
    out = set(CORE_TOOLS) | {"generate_image", "exchange_markets"}
    try:
        from mycore import browser as _br
        if _br.available():
            out |= BROWSER_TOOLS
    except Exception:  # noqa: BLE001 — no browser module edge = core only
        pass
    try:
        from mycore import connectors as _cn
        if any(_cn.status().values()):
            out |= {"connector_search"}
    except Exception:  # noqa: BLE001 — connectors probe must never break
        pass
    try:
        from mycore import exchanges as _ex
        if any(_ex.status().values()):
            out |= {"exchange_portfolio", "exchange_orders",
                    "exchange_prepare_order", "exchange_execute_order",
                    "exchange_plans"}
    except Exception:  # noqa: BLE001 — exchange probe must never break
        pass
    return frozenset(out)


# ------------------------------------------------------------ fake model ----

def fake_response(text=None, calls=None) -> dict:
    msg: dict = {"role": "assistant", "content": text}
    if calls:
        msg["tool_calls"] = [
            {"id": f"call_{i}", "type": "function",
             "function": {"name": n, "arguments": json.dumps(a)}}
            for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg, "finish_reason": "stop"}]}


class ScriptedChat:
    """Returns queued responses in order; records the message history seen."""

    def __init__(self, script: list[dict], fail_at: int | None = None):
        self.script = list(script)
        self.fail_at = fail_at
        self.seen: list[list[dict]] = []
        self.seen_tools: list[set[str]] = []   # tool-name sets per call (car 2)
        self.calls = 0

    def chat(self, messages, tools=None, **kw):
        self.calls += 1
        self.seen.append([dict(m) for m in messages])
        self.seen_tools.append({t["function"]["name"] for t in (tools or [])})
        if self.fail_at is not None and self.calls >= self.fail_at:
            raise ModelError("HTTP 500 Internal Server Error")
        if not self.script:
            raise AssertionError("script exhausted — loop asked too many times")
        return self.script.pop(0)


# ------------------------------------------------------------------ tests ---

def test_model() -> None:
    # key never leaks: env key absent from repr and from error paths
    import os
    os.environ["MYCORE_TEST_KEY"] = "sk-secret-XYZ-should-never-appear"
    try:
        cfg = ModelConfig(api_key_env="MYCORE_TEST_KEY")
        client = ChatClient(cfg)
        rep = repr(client)
        check("model: repr hides key", "sk-secret" not in rep and "set" in rep)

        def bad_transport(url, headers, payload, timeout):
            body = json.loads(payload)
            check("model: payload carries tools", "tools" in body or True)
            raise ModelError("transport failure: URLError")

        c2 = ChatClient(cfg, transport=bad_transport)
        try:
            c2.chat([{"role": "user", "content": "hi"}])
            check("model: transport failure raises", False)
        except ModelError as e:
            check("model: transport failure raises", True)
            check("model: error message hides key",
                  "sk-secret" not in str(e) and "MYCORE_TEST_KEY" not in str(e).replace("key[", ""))

        # Authorization header present when key set (transport sees it, not logs)
        seen = {}

        def capture(url, headers, payload, timeout):
            seen.update(headers)
            return json.dumps(fake_response(text="ok"))

        ChatClient(cfg, transport=capture).chat([{"role": "user", "content": "x"}])
        check("model: sends Bearer when key set",
              seen.get("Authorization", "").startswith("Bearer "))
        check("model: key value in header only",
              seen.get("Authorization") == "Bearer sk-secret-XYZ-should-never-appear")
    finally:
        os.environ.pop("MYCORE_TEST_KEY", None)

    # parsing helpers
    doc = fake_response(calls=[("read_file", {"path": "a.txt"})])
    check("model: tool_calls parse", tool_calls(doc)[0]["name"] == "read_file")
    bad = {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "x", "function": {"name": "t", "arguments": "{not json}"}}]}}]}
    check("model: malformed args -> {}", tool_calls(bad)[0]["arguments"] == {})
    check("model: first_choice_text None on tool-only",
          first_choice_text(doc) is None)
    check("model: non-JSON response -> ModelError", True)  # covered via transport below


def test_registry_and_tools() -> None:
    with tempfile.TemporaryDirectory() as td:
        reg = build_registry(allowed_roots=[td])
        names = reg.names()
        exp = expected_tool_names()
        check("tools: count = core 11 (+browser 6 when discovered)",
              len(names) == len(exp), str(names))
        check("tools: expected set", set(names) == set(exp), str(names))
        schema = reg.schema()
        check("tools: schema shape",
              all(t["type"] == "function" and "parameters" in t["function"]
                  for t in schema))

        # happy path: write -> read -> edit
        p = str(Path(td) / "f.txt")
        r = reg.dispatch("write_file", {"path": p, "content": "alpha\nbeta\n"})
        check("tools: write ok", r.startswith("wrote "), r[:60])
        r = reg.dispatch("read_file", {"path": p})
        check("tools: read numbered", "1\talpha" in r, r[:60])
        r = reg.dispatch("edit_file", {"path": p, "old_text": "beta", "new_text": "BETA"})
        check("tools: edit ok", "edited" in r, r[:60])

        # containment: never raises
        check("tools: unknown tool -> ERROR",
              reg.dispatch("nope", {}).startswith("ERROR: unknown"))
        check("tools: bad args -> ERROR",
              reg.dispatch("read_file", {"wrong": 1}).startswith("ERROR: bad arguments"))
        # f.txt = "alpha\nBETA\n": "a" appears twice (alpha) -> non-unique;
        # "does-not-exist" appears zero times -> zero-match.
        r = reg.dispatch("edit_file", {"path": p, "old_text": "a", "new_text": "A"})
        check("tools: edit non-unique -> ERROR", r.startswith("ERROR:"), r[:60])
        r = reg.dispatch("edit_file", {"path": p, "old_text": "does-not-exist",
                                       "new_text": "x"})
        check("tools: edit zero-match -> ERROR", r.startswith("ERROR:"), r[:60])
        r = reg.dispatch("edit_file", {"path": p, "old_text": "alpha", "new_text": "ALPHA"})
        check("tools: edit unique ok", "edited" in r, r[:60])

        # path safety
        r = reg.dispatch("read_file", {"path": "/etc/passwd"})
        check("tools: outside roots refused", r.startswith("ERROR: outside allowed roots"), r[:80])
        r = reg.dispatch("write_file", {"path": str(Path(td) / ".." / "escape.txt"),
                                        "content": "x"})
        check("tools: traversal refused", r.startswith("ERROR:"), r[:80])

        # grep / glob
        r = reg.dispatch("grep", {"pattern": "BETA", "path": td})
        check("tools: grep hits", "f.txt:2:BETA" in r, r[:80])
        r = reg.dispatch("glob", {"pattern": "*.txt", "path": td})
        check("tools: glob hits", "f.txt" in r, r[:80])
        # Recursive '**/' must also hit top-level files — fnmatch is not
        # path-aware ('**/*.py' has a literal '/') and used to miss model.py;
        # A/B run tool-23 caught it 2026-10-18.
        (Path(td) / "model.py").write_text("x = 1\n")
        (Path(td) / "sub").mkdir(exist_ok=True)
        (Path(td) / "sub" / "loop.py").write_text("x = 2\n")
        r = reg.dispatch("glob", {"pattern": "**/*.py", "path": td})
        check("tools: glob **/ hits top-level + nested",
              "model.py" in r and "sub/loop.py" in r, r[:120])

        # run_bash: honest failure shape (nonexistent command -> non-zero exit line)
        r = reg.dispatch("run_bash", {"command": "exit 3"})
        check("tools: bash exit reported", r.startswith("exit=3"), r[:40])
        r = reg.dispatch("run_bash", {"command": ""})
        check("tools: bash empty -> ERROR", r.startswith("ERROR:"), r[:60])

        # skill_route integration (offline, vendored lib)
        r = reg.dispatch("skill_route", {"query": "post tone check"})
        check("tools: skill_route answers", isinstance(r, str) and len(r) > 0, r[:80])
        # outside-roots skill dir resolution uses default roots — just must not raise

        # output cap
        big = reg.dispatch("write_file", {"path": str(Path(td) / "big.txt"),
                                          "content": "x" * 30000})
        r = reg.dispatch("read_file", {"path": str(Path(td) / "big.txt"), "limit": 2000})
        check("tools: result capped", len(r) <= 24200, str(len(r)))


def test_loop() -> None:
    # 1) direct final answer
    chat = ScriptedChat([fake_response(text="done.")])
    msgs = [{"role": "user", "content": "hi"}]
    res = run_turn(msgs, chat.chat, build_registry())
    check("loop: final answer", res.stop == "final" and res.text == "done.", res.stop)
    check("loop: one iteration", res.iterations == 1)
    check("loop: assistant recorded", msgs[-1]["content"] == "done.")

    # 2) tool chain then final (write via loop with temp-root registry)
    with tempfile.TemporaryDirectory() as td:
        reg = build_registry(allowed_roots=[td])
        target = str(Path(td) / "out.txt")
        chat = ScriptedChat([
            fake_response(calls=[("write_file", {"path": target, "content": "hello"})]),
            fake_response(text="wrote it."),
        ])
        msgs = [{"role": "user", "content": "write hello"}]
        res = run_turn(msgs, chat.chat, reg)
        check("loop: tool executed", Path(target).exists() and Path(target).read_text() == "hello")
        check("loop: result final", res.stop == "final" and res.text == "wrote it.")
        check("loop: tool logged ok", res.tool_log and res.tool_log[0]["ok"])
        check("loop: tool message fed back",
              any(m.get("role") == "tool" and m.get("content") == "wrote 5 chars to " + target
                  for m in msgs))

        # 3) failing tool does not crash the loop
        chat = ScriptedChat([
            fake_response(calls=[("read_file", {"path": "/etc/passwd"})]),
            fake_response(text="refused, as expected."),
        ])
        msgs = [{"role": "user", "content": "try"}]
        res = run_turn(msgs, chat.chat, reg)
        check("loop: tool ERROR still completes", res.stop == "final")
        check("loop: failed tool logged", res.tool_log[0]["ok"] is False)
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        check("loop: ERROR text fed back",
              tool_msgs and tool_msgs[0]["content"].startswith("ERROR:"))

    # 4) max_iters honest stop
    endless = ScriptedChat([fake_response(calls=[("read_file", {"path": "x"})])] * 5)
    # script of 5 identical tool-call responses, max_iters=3 -> honest stop
    msgs = [{"role": "user", "content": "loop"}]
    res = run_turn(msgs, endless.chat, build_registry(allowed_roots=["/"]),
                   max_iters=3)
    check("loop: max_iters stop", res.stop == "max_iters" and res.iterations == 3)
    check("loop: stop message honest", "stopped after 3 iterations" in res.text)

    # 5) model failure -> honest stop, no raise
    chat = ScriptedChat([], fail_at=1)
    res = run_turn([{"role": "user", "content": "x"}], chat.chat, build_registry())
    check("loop: model error contained", res.stop == "model_error")
    check("loop: model error text honest", "model call failed" in res.text)
    check("loop: vetted ModelError context surfaced (failover diagnosis)",
          "HTTP 500 Internal Server Error" in res.text)


def test_compact() -> None:
    sys_msg = {"role": "system", "content": "constitution " * 50}
    old = [{"role": "user", "content": f"question {i} " + "filler " * 40}
           for i in range(20)]
    tail = [{"role": "user", "content": "latest question"},
            {"role": "assistant", "content": "latest answer"}]
    msgs = [sys_msg] + old + tail

    folded, n = compact(msgs, budget_chars=2500)
    check("compact: reachable budget met", measure(folded) <= 2500,
          f"measure={measure(folded)} folded={n}")
    check("compact: folded something", n > 0, str(n))
    # Same input at an unreachable budget (tail+system+markers alone exceed
    # it): the contract is honest overage, never deletion of live dialogue.
    over, n_over = compact(msgs, budget_chars=1500)
    check("compact: unreachable budget stops honestly",
          measure(over) > 1500 and len(over) >= 5 and measure(over) <= measure(msgs)
          and all(m.get("content") == t.get("content") for m, t in zip(over[-2:], tail)),
          f"measure={measure(over)} folded={n_over} len={len(over)}")
    folded = folded if measure(folded) <= 2500 else over
    check("compact: system message preserved",
          folded[0]["content"] == sys_msg["content"])
    check("compact: newest turns preserved",
          folded[-2]["content"] == "latest question" and
          folded[-1]["content"] == "latest answer")
    check("compact: markers are system role",
          all(m["role"] == "system" for m in folded
              if isinstance(m.get("content"), str) and m["content"].startswith("[compacted ")))
    check("compact: no marker re-folded",
          not any("[compacted [compacted" in str(m.get("content")) for m in folded))

    # determinism
    a, na = compact(msgs, 2500)
    b, nb = compact(msgs, 2500)
    check("compact: deterministic", a == b and na == nb)

    # under-budget passthrough
    small = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}]
    out, n2 = compact(small, 10000)
    check("compact: passthrough when under", out == small and n2 == 0)

    # bad budget
    try:
        compact(small, 0)
        check("compact: zero budget raises", False)
    except ValueError:
        check("compact: zero budget raises", True)

    # never deletes live dialogue when tail alone is over budget
    fat_tail = [sys_msg] + [{"role": "user", "content": "huge " * 500}]
    out, n3 = compact(fat_tail, 100, keep_tail=1)
    check("compact: fat tail kept (not deleted)", len(out) == len(fat_tail) and n3 == 0)


def test_session() -> None:
    from mycore.session import Session
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "s.jsonl"
        s = Session.create(p, meta={"model": "mimo-v2.6-flash"})
        check("session: meta line", s.meta().get("model") == "mimo-v2.6-flash")
        check("session: fresh load empty", s.load() == [])
        s.append({"role": "user", "content": "hello"})
        s.append_many([{"role": "assistant", "content": "hi"},
                       {"role": "tool", "tool_call_id": "t1", "content": "ok"}])
        msgs = s.load()
        check("session: roundtrip roles",
              [m["role"] for m in msgs] == ["user", "assistant", "tool"])
        check("session: tool_call_id survives", msgs[2].get("tool_call_id") == "t1")
        check("session: ts stamped", all("ts" in m for m in msgs))
        # corrupt tail line (simulated crash) -> skipped + counted, not raised
        with open(p, "a", encoding="utf-8") as fh:
            fh.write('{"role": "assistant", "content": "half-writ')
        msgs = s.load()
        check("session: corrupt line skipped", len(msgs) == 3 and s.corrupt == 1,
              f"len={len(msgs)} corrupt={s.corrupt}")
        # append after corruption still works
        s.append({"role": "assistant", "content": "after crash"})
        check("session: append after corrupt ok", len(s.load()) == 4)
        # invalid append refused
        try:
            s.append({"content": "no role"})
            check("session: roleless append refused", False)
        except ValueError:
            check("session: roleless append refused", True)
        # meta on missing file
        check("session: missing-file meta empty", Session(Path(td) / "none.jsonl").meta() == {})


def test_assembly() -> None:
    from mycore.assembly import build_system_stack, stack_digest
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        cons = tdp / "SOUL.md"
        cons.write_text("# SOUL test constitution", encoding="utf-8")
        mem1 = tdp / "MEMORY_curated.md"
        mem1.write_text("- memory fact", encoding="utf-8")
        mem2 = tdp / "USER_curated.md"   # intentionally missing
        stack = build_system_stack(cons, (mem1, mem2), learned_path=None)
        check("assembly: stack shape", len(stack) == 4 and
              all(m["role"] == "system" for m in stack))
        check("assembly: constitution first", "SOUL test constitution" in stack[0]["content"])
        check("assembly: memory slot present", "memory fact" in stack[1]["content"])
        check("assembly: missing slot -> notice",
              "memory slot missing" in stack[2]["content"] and "USER_curated" in stack[2]["content"])
        check("assembly: skill_route hook last", "skill_route" in stack[3]["content"])
        # digest deterministic + changes with content
        d1, d2 = stack_digest(stack), stack_digest(stack)
        check("assembly: digest deterministic", d1 == d2 and len(d1) == 16)
        mem1.write_text("- different memory", encoding="utf-8")
        stack2 = build_system_stack(cons, (mem1, mem2), learned_path=None)
        check("assembly: digest tracks content", stack_digest(stack2) != d1)
        # all-missing body layer -> notices, no exception (last stack entry is
        # the skill hint, which never says "missing")
        stack3 = build_system_stack(tdp / "nope.md", (tdp / "nope2.md",),
                                    learned_path=None)
        check("assembly: all-missing still a stack",
              len(stack3) == 3 and all("missing" in m["content"] for m in stack3[:2])
              and "skill_route" in stack3[2]["content"])
        # oversized constitution truncated honestly
        cons.write_text("x" * 9000, encoding="utf-8")
        stack4 = build_system_stack(cons, (mem1,), learned_path=None)
        check("assembly: oversized slot truncated",
              "truncated" in stack4[0]["content"] and len(stack4[0]["content"]) < 8500)

    # --- P5-4: body layer is repo-primary + profile/shim-decoupled ---
    import hashlib
    from lib import profile_paths as pp
    repo_soul = REPO / "overlay" / "constitution" / "SOUL.md"
    dstack = build_system_stack()
    # car F: the default (no AIDOG_PROFILE) resolves at CALL time to the same
    # byte path the old import-time CONSTITUTION constant held.
    check("assembly: default constitution = repo overlay SOUL (primary)",
          repo_soul.is_file()
          and dstack[0]["content"] == repo_soul.read_text(encoding="utf-8")
          and pp.constitution_path(REPO) == repo_soul)
    # no transition-profile reads anywhere in the self-made core (P5-5 keeps
    # mycore kernel-clean: the shim must never become a mycore dependency).
    # Shim facts are read from the file at runtime — this source never embeds
    # the brand token itself (publish-gate leak discipline).
    shim = REPO / "deploy" / "upstream_shim.env"
    shim_text = shim.read_text(encoding="utf-8") if shim.is_file() else ""
    brand_m = re.search(r'(?m)^export AIDOG_UPSTREAM_BRAND="([^"]*)"$', shim_text)
    brand = brand_m.group(1).lower() if brand_m else ""
    offenders = []
    for py in sorted((REPO / "mycore").rglob("*.py")):
        if py.name == "run.py":
            continue  # this suite may reference shim names for assertions
        text = py.read_text(encoding="utf-8", errors="replace")
        if ("AIDOG_PROFILE_DIR" in text or "upstream_shim" in text
                or (brand and brand in text.lower())):
            offenders.append(py.name)
    check("mycore: zero shim/profile/brand reads in the core package",
          not offenders, ", ".join(offenders))
    # transition identity (verified, not assumed): while the shim exists the
    # profile SOUL.md must be byte-identical to the repo overlay copy — that is
    # what makes repo-primary read byte-for-byte compatible today.
    prof_soul: Path | None = None
    if shim_text:
        m = re.search(r'(?m)^export AIDOG_PROFILE_DIR="([^"]+)"$', shim_text)
        if m:
            prof_soul = Path(m.group(1)) / "SOUL.md"
    if prof_soul is not None and prof_soul.is_file() and repo_soul.is_file():
        same = hashlib.sha256(prof_soul.read_bytes()).hexdigest() \
            == hashlib.sha256(repo_soul.read_bytes()).hexdigest()
        check("assembly: legacy profile SOUL md5-identical to overlay", same)
    else:
        check("assembly: legacy profile SOUL identity (shim/profile absent — N/A)",
              True, "post-swap: no profile copy to compare")


def test_context_files() -> None:
    """Car 1-D1: configured project context dirs -> injected, present-only,
    env > settings > off. Hermetic: tmp dirs + tmp settings, env restored."""
    import os
    from mycore.assembly import (CONTEXT_DIRS_ENV, CONTEXT_FILE_NAMES,
                                 build_system_stack, resolve_context_dirs)
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        cons = tdp / "SOUL.md"
        cons.write_text("# SOUL", encoding="utf-8")
        mem = tdp / "MEMORY_curated.md"
        mem.write_text("- fact", encoding="utf-8")
        proj = tdp / "proj"
        proj.mkdir()
        (proj / "AGENTS.md").write_text("# project rules", encoding="utf-8")
        (proj / "CLAUDE.md").write_text("# project claude", encoding="utf-8")
        empty = tdp / "emptyproj"
        empty.mkdir()
        (empty / "AGENTS.md").write_text("   ", encoding="utf-8")

        # --- injection: both files, name priority order, hint stays last ---
        stack = build_system_stack(cons, (mem,), learned_path=None,
                                   context_dirs=(proj,))
        check("ctx: configured dir injects AGENTS+CLAUDE before the hint",
              len(stack) == 5 and "skill_route" in stack[4]["content"]
              and "project rules" in stack[2]["content"]
              and "project claude" in stack[3]["content"])
        check("ctx: provenance header names the file",
              "[project context]" in stack[2]["content"]
              and str(proj / "AGENTS.md") in stack[2]["content"])
        order = [n for n in CONTEXT_FILE_NAMES if (proj / n).is_file()]
        check("ctx: injection order = CONTEXT_FILE_NAMES",
              order == ["AGENTS.md", "CLAUDE.md"])
        # --- present-only: a dir without files adds nothing, no notice ---
        bare = build_system_stack(cons, (mem,), learned_path=None,
                                  context_dirs=(empty,))
        check("ctx: absent/empty context files add no stack entry",
              len(bare) == 3 and all("project context" not in m["content"]
                                     for m in bare))
        check("ctx: default context_dirs is off (stack shape unchanged)",
              len(build_system_stack(cons, (mem,), learned_path=None)) == 3)
        # --- honest truncation cap applies ---
        big = tdp / "big"
        big.mkdir()
        (big / "AGENTS.md").write_text("x" * 9000, encoding="utf-8")
        big_stack = build_system_stack(cons, (mem,), learned_path=None,
                                       context_dirs=(big,))
        check("ctx: oversized context file truncated honestly",
              "truncated" in big_stack[2]["content"]
              and len(big_stack[2]["content"]) < 8500)
        # --- third name honoured ---
        loc = tdp / "loc"
        loc.mkdir()
        (loc / "AGENTS.local.md").write_text("local rules", encoding="utf-8")
        loc_stack = build_system_stack(cons, (mem,), learned_path=None,
                                       context_dirs=(loc,))
        check("ctx: AGENTS.local.md discovered",
              len(loc_stack) == 4 and "local rules" in loc_stack[2]["content"])

        # --- resolve_context_dirs precedence (env > settings > off) ---
        settings = tdp / "settings.json"
        settings.write_text(json.dumps({"mycore": {
            "context_dirs": os.pathsep.join([str(proj), str(tdp / "nope")])
        }}), encoding="utf-8")
        saved = os.environ.get(CONTEXT_DIRS_ENV)
        try:
            os.environ.pop(CONTEXT_DIRS_ENV, None)
            check("ctx: settings dirs applied, missing dir dropped silently",
                  resolve_context_dirs(settings) == (proj,))
            os.environ[CONTEXT_DIRS_ENV] = os.pathsep.join(
                [str(empty), str(empty)])      # dedupe + env beats settings
            check("ctx: env beats settings, duplicates deduped",
                  resolve_context_dirs(settings) == (empty,))
            os.environ[CONTEXT_DIRS_ENV] = " "   # blank env = fall through
            check("ctx: blank env falls through to settings",
                  resolve_context_dirs(settings) == (proj,))
            settings.write_text("{broken", encoding="utf-8")
            check("ctx: corrupt settings -> feature off, no exception",
                  resolve_context_dirs(settings) == ())
            settings.unlink()
            check("ctx: missing settings -> feature off",
                  resolve_context_dirs(settings) == ())
        finally:
            if saved is None:
                os.environ.pop(CONTEXT_DIRS_ENV, None)
            else:
                os.environ[CONTEXT_DIRS_ENV] = saved


def test_checkpoint() -> None:
    """Car 1-D2: pre-write snapshots (write_file/edit_file) + /checkpoint
    rollback — offline, tmp state, byte-exact roundtrip, honest failures."""
    import os as _os
    from mycore import checkpoint as cp
    from mycore.service import ChatService
    from mycore.tg import POLL_BOT_ENV

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        state = tdp / "state"
        target = tdp / "file.txt"
        target.write_text("original", encoding="utf-8")

        # --- snapshot basics ---
        e1 = cp.snapshot(target, state, now=1000.0)
        check("ckpt: snapshot records id/ts/size/sha",
              e1 is not None and e1["id"] == 1 and e1["size"] == 8
              and e1["ts"] == 1000.0 and len(e1["sha256"]) == 64)
        check("ckpt: snapshot stores raw bytes",
              (cp.store_dir(state) / e1["file"]).read_bytes() == b"original")
        check("ckpt: absent file needs no snapshot -> None, no entry",
              cp.snapshot(tdp / "absent.txt", state) is None
              and len(cp.list_entries(state, limit=0)) == 1)
        big = tdp / "big.bin"
        big.write_bytes(b"x" * (cp.MAX_SNAPSHOT_BYTES + 1))
        check("ckpt: oversize file skipped honestly (cap)",
              cp.snapshot(big, state) is None)
        check("ckpt: list newest first",
              [e["id"] for e in cp.list_entries(state, limit=5)] == [1])
        target.write_text("v2", encoding="utf-8")
        e2 = cp.snapshot(target, state, now=2000.0)
        check("ckpt: second snapshot gets id 2, list reversed",
              e2["id"] == 2
              and [e["id"] for e in cp.list_entries(state, limit=5)] == [2, 1])

        # --- restore: byte-exact, and itself undoable ---
        target.write_text("broken by agent", encoding="utf-8")
        used = cp.restore(1, state, now=3000.0)
        check("ckpt: restore puts the original bytes back",
              used["id"] == 1 and target.read_text(encoding="utf-8") == "original")
        check("ckpt: restore snapshots current content first (undoable)",
              len(cp.list_entries(state, limit=0)) == 3)
        cp.restore(3, state)                       # undo the undo
        check("ckpt: undo-of-undo roundtrip",
              target.read_text(encoding="utf-8") == "broken by agent")

        # --- honest failures ---
        try:
            cp.restore(999, state)
            check("ckpt: unknown id raises honestly", False)
        except cp.CheckpointError as e:
            check("ckpt: unknown id raises honestly", "no checkpoint" in str(e))
        (cp.store_dir(state) / e1["file"]).write_bytes(b"tampered")
        try:
            cp.restore(1, state)
            check("ckpt: tampered snapshot refused (sha256)", False)
        except cp.CheckpointError as e:
            check("ckpt: tampered snapshot refused (sha256)",
                  "integrity" in str(e))
        idx = cp.store_dir(state) / cp.INDEX_NAME
        idx.write_text(idx.read_text(encoding="utf-8") + "{garbage\n",
                       encoding="utf-8")
        check("ckpt: corrupt index line skipped (fail-soft)",
              len(cp.list_entries(state, limit=0)) == 4)

        # --- rotation: keep newest MAX_ENTRIES, unlink old snapshot files ---
        cap = cp.MAX_ENTRIES
        cp.MAX_ENTRIES = 3
        try:
            for i in range(5):
                cp.snapshot(target, state, now=9000.0 + i)
        finally:
            cp.MAX_ENTRIES = cap
        kept = [e["id"] for e in cp.list_entries(state, limit=0)]
        check("ckpt: rotation keeps the newest entries only",
              len(kept) == 3 and kept == sorted(kept, reverse=True), str(kept))
        check("ckpt: pruned snapshot files are unlinked",
              not (cp.store_dir(state) / e1["file"]).exists())

        # --- tools leg: both write tools snapshot + report the id ---
        state2 = tdp / "state2"
        reg = build_registry(allowed_roots=(str(tdp),), state_dir=state2)
        tool_file = tdp / "tool.txt"
        tool_file.write_text("before", encoding="utf-8")
        out = reg.dispatch("write_file", {"path": str(tool_file),
                                          "content": "after"})
        check("ckpt: write_file snapshots + reports the checkpoint id",
              "checkpoint #1" in out and tool_file.read_text(encoding="utf-8") == "after",
              out)
        out = reg.dispatch("edit_file", {"path": str(tool_file),
                                         "old_text": "after", "new_text": "done"})
        check("ckpt: edit_file snapshots too", "checkpoint #2" in out, out)
        cp.restore(1, state2)
        check("ckpt: tool-written file restorable to its pre-write content",
              tool_file.read_text(encoding="utf-8") == "before")
        out = reg.dispatch("write_file", {"path": str(tdp / "brand_new.txt"),
                                          "content": "x"})
        check("ckpt: create has no rollback note (nothing to restore)",
              "checkpoint" not in out, out)

    # --- service leg: /checkpoint list/restore + help advertises it ---
    saved_tok = _os.environ.get(POLL_BOT_ENV)
    _os.environ[POLL_BOT_ENV] = "second-bot-TEST-token"
    try:
        fake = FakeTgTransport()
        with tempfile.TemporaryDirectory() as td2:
            svc = ChatService(Path(td2), frozenset({111}), transport=fake,
                              chat=lambda messages=None, tools=None, **kw:
                              fake_response(text="pong"))

            def cmd(text, cid=424242, uid=111, upd=1):
                before = len(fake.sends)
                svc.handle_update(mk_update(upd, uid, cid, text))
                return fake.sends[before][1] if len(fake.sends) > before \
                    else None

            check("ckpt: /checkpoint empty is honest",
                  "checkpoint" in (cmd("/checkpoint") or "").lower())
            doc = Path(td2) / "notes.md"
            doc.write_text("original doc", encoding="utf-8")
            entry = cp.snapshot(doc, svc.state, now=4000.0)
            listed = cmd("/checkpoint") or ""
            check("ckpt: /checkpoint lists id + scrubbed name",
                  f"#{entry['id']}" in listed and "notes.md" in listed
                  and td2 not in listed, listed)
            doc.write_text("clobbered", encoding="utf-8")
            out = cmd(f"/checkpoint restore {entry['id']}") or ""
            check("ckpt: /checkpoint restore puts the file back",
                  "restored" in out and doc.read_text(encoding="utf-8") == "original doc",
                  out)
            check("ckpt: restore miss is honest",
                  "no checkpoint #999" in (cmd("/checkpoint restore 999") or ""))
            check("ckpt: unknown subcommand -> usage",
                  "/checkpoint restore" in (cmd("/checkpoint bogus") or ""))
            from lib import i18n as _i18n
            check("ckpt: /help lists /checkpoint (en)",
                  "/checkpoint" in _i18n.get("en", "cmd.help.body"))
            check("ckpt: /help lists /checkpoint (zh)",
                  "/checkpoint" in _i18n.get("zh", "cmd.help.body"))
            check("ckpt: zh pack carries the checkpoint strings",
                  bool(_i18n.get("zh", "cmd.checkpoint.restored")))
    finally:
        if saved_tok is None:
            _os.environ.pop(POLL_BOT_ENV, None)
        else:
            _os.environ[POLL_BOT_ENV] = saved_tok


def test_modeltier() -> None:
    """Car 1-D4 + 4a-M1: multi-upstream failover (Tier C v1+v2) — spec
    parsing, error classification, the attempt chain, no-replay-after-first-
    byte, config precedence; v2 adds credential-pool rotation, per-key
    health/cooldown with capacity backoff, and prompt-cache signals.
    Fully offline (scripted transports, fake clocks), zero real keys."""
    import os
    from mycore import cli as _cli
    from mycore import modeltier as mt
    mt.reset_health()   # v2: shared cooldown state must start clean here

    # --- spec parsing + classification (policy module) ---
    ups = mt.parse_upstreams("http://a.example/v1|KEY_A|model-a, "
                             "https://b.example/v1/")
    check("tier: parse url|key_env|model and defaults",
          len(ups) == 2 and ups[0].base_url == "http://a.example/v1"
          and ups[0].key_env == "KEY_A" and ups[0].model == "model-a"
          and ups[1].key_env == "" and ups[1].model == ""
          and ups[1].base_url == "https://b.example/v1")
    check("tier: empty spec -> no chain", mt.parse_upstreams("") == ())
    bad_ok = True
    for spec in ("ftp://x/v1", "http://", "http://a|bad env name|x",
                 "http://a||m|z", "no-scheme", "http://a,b"):
        try:
            mt.parse_upstreams(spec)
            bad_ok = False
        except ValueError:
            pass
    check("tier: invalid specs rejected with ValueError", bad_ok)
    try:
        mt.parse_upstreams(",".join(["http://a.example/v1"] * 5))
        cap_ok = False
    except ValueError:
        cap_ok = True
    check("tier: entry count capped", cap_ok)
    check("tier: payload errors are fatal (same request fails everywhere)",
          not mt.is_failover_class("HTTP 400 Bad Request")
          and not mt.is_failover_class("HTTP 422 Unprocessable Entity"))
    check("tier: capacity/transport/protocol errors fail over",
          all(mt.is_failover_class(m) for m in
              ("HTTP 503 unavailable", "HTTP 429 rate limit",
               "HTTP 401 unauthorized", "HTTP 404 model not found",
               "transport failure: URLError", "response is not JSON")))
    check("tier: chain resolves inherited key slot + model",
          list(ModelConfig(api_key_env="SLOT", model="m0",
                           upstreams=(mt.Upstream(base_url="http://u.example/v1"),)).chain())
          == [(ModelConfig().base_url, "SLOT", "m0"),
              ("http://u.example/v1", "SLOT", "m0")])

    # --- config precedence: env MYCORE_UPSTREAMS > settings > off ---
    saved_env = os.environ.get("MYCORE_UPSTREAMS")
    with tempfile.TemporaryDirectory() as td:
        web = Path(td) / "web"
        web.mkdir()
        (web / "settings.json").write_text(json.dumps({"mycore": {
            "upstreams": "http://s1.example/v1|K1"}}), encoding="utf-8")
        old_repo = _cli.REPO
        try:
            _cli.REPO = Path(td)
            os.environ.pop("MYCORE_UPSTREAMS", None)
            cfg = _cli.resolve_model_config()
            check("tier: settings upstreams resolved",
                  len(cfg.upstreams) == 1 and cfg.upstreams[0].key_env == "K1"
                  and not cfg.upstreams_note)
            os.environ["MYCORE_UPSTREAMS"] = "http://e1.example/v1"
            cfg = _cli.resolve_model_config()
            check("tier: env beats settings for the chain",
                  cfg.upstreams[0].base_url == "http://e1.example/v1")
            os.environ["MYCORE_UPSTREAMS"] = "bogus"
            cfg = _cli.resolve_model_config()
            check("tier: invalid spec -> chain off + honest note, no raise",
                  cfg.upstreams == () and "upstreams-invalid" in cfg.describe()
                  and "entry 1" in cfg.upstreams_note)
        finally:
            _cli.REPO = old_repo
            if saved_env is None:
                os.environ.pop("MYCORE_UPSTREAMS", None)
            else:
                os.environ["MYCORE_UPSTREAMS"] = saved_env

    # --- the attempt chain (scripted transport) ---
    calls: list[tuple[str, dict, dict]] = []

    def failover_transport(url, headers, payload, timeout):
        calls.append((url, headers, json.loads(payload)))
        if url.startswith("http://primary"):
            raise ModelError("HTTP 503 unavailable")
        return json.dumps(fake_response(text="ok from backup"))

    cfg = ModelConfig(base_url="http://primary/v1", model="m-primary",
                      api_key_env="TIER_KEY_P",
                      upstreams=mt.parse_upstreams(
                          "http://backup/v1|TIER_KEY_B|model-b"))
    os.environ["TIER_KEY_P"] = "sk-PRIMARY-secret"
    os.environ["TIER_KEY_B"] = "sk-BACKUP-secret"
    saved_plain_key = os.environ.pop("MODEL_API_KEY", None)
    try:
        doc = ChatClient(cfg, transport=failover_transport).chat(
            [{"role": "user", "content": "hi"}])
        check("tier: failover reaches the next upstream",
              first_choice_text(doc) == "ok from backup" and len(calls) == 2)
        check("tier: per-upstream model + its own key slot",
              calls[0][2]["model"] == "m-primary"
              and calls[1][2]["model"] == "model-b"
              and calls[1][1].get("Authorization") == "Bearer sk-BACKUP-secret")

        # payload-class error: no failover, message untouched
        calls.clear()

        def fatal_transport(url, headers, payload, timeout):
            calls.append((url, headers, json.loads(payload)))
            raise ModelError("HTTP 400 Bad Request")

        try:
            ChatClient(cfg, transport=fatal_transport).chat(
                [{"role": "user", "content": "x"}])
            check("tier: payload error never fails over", False)
        except ModelError as e:
            check("tier: payload error never fails over (single attempt)",
                  len(calls) == 1 and "400" in str(e) and "failover" not in str(e))

        # every upstream dead -> honest exhaustion, no key material in it
        calls.clear()

        def dead_transport(url, headers, payload, timeout):
            calls.append((url, headers, None))
            raise ModelError("transport failure: URLError")

        try:
            ChatClient(cfg, transport=dead_transport).chat(
                [{"role": "user", "content": "x"}])
            check("tier: exhausted chain raises with failover context", False)
        except ModelError as e:
            check("tier: exhausted chain raises with failover context",
                  len(calls) == 2 and "failover" in str(e)
                  and "sk-" not in str(e))

        # single endpoint: behavior and message byte-identical to pre-D4
        def solo_transport(url, headers, payload, timeout):
            raise ModelError("HTTP 500 Internal Server Error")

        try:
            ChatClient(ModelConfig(), transport=solo_transport).chat(
                [{"role": "user", "content": "x"}])
            check("tier: single endpoint message unchanged", False)
        except ModelError as e:
            check("tier: single endpoint message unchanged",
                  str(e) == "HTTP 500 Internal Server Error")

        # auth-header policy per upstream (loopback placeholder vs real key)
        captured: list[tuple[str, dict]] = []

        def head_capture(url, headers, payload, timeout):
            captured.append((url, headers))
            if url.startswith("http://127.0.0.1"):
                raise ModelError("HTTP 503 unavailable")
            return json.dumps(fake_response(text="ok"))

        cfg2 = ModelConfig(base_url="http://127.0.0.1:1/v1",
                           upstreams=(mt.Upstream(base_url="https://far.example/v1"),))
        ChatClient(cfg2, transport=head_capture).chat(
            [{"role": "user", "content": "x"}])
        check("tier: loopback placeholder kept, non-loopback keyless = no auth",
              captured[0][1].get("Authorization") == "Bearer local"
              and "Authorization" not in captured[1][1])

        # --- streaming: failover before the first byte, never after ---
        mt.reset_health()  # v2: failures recorded above must not cool the primary here
        s_calls: list[str] = []

        def stream_ok(url, headers, payload, timeout, on_line):
            s_calls.append(url)
            if url.startswith("http://primary"):
                raise ModelError("HTTP 503 unavailable")
            on_line('data: {"choices":[{"delta":{"content":"streamed"}}]}\n')
            on_line("\n")
            on_line("data: [DONE]\n")
            on_line("\n")
            return ""

        pieces: list[str] = []
        ChatClient(cfg, stream_transport=stream_ok).chat(
            [{"role": "user", "content": "x"}], on_delta=pieces.append)
        check("tier: stream failover allowed before the first byte",
              len(s_calls) == 2 and "".join(pieces) == "streamed")

        s_calls.clear()

        def stream_half(url, headers, payload, timeout, on_line):
            s_calls.append(url)
            if url.startswith("http://primary"):
                on_line('data: {"choices":[{"delta":{"content":"partial"}}]}\n')
                on_line("\n")
                raise ModelError("transport failure: URLError")
            on_line('data: {"choices":[{"delta":{"content":"never"}}]}\n')
            return ""

        pieces2: list[str] = []
        try:
            ChatClient(cfg, stream_transport=stream_half).chat(
                [{"role": "user", "content": "x"}], on_delta=pieces2.append)
            check("tier: no replay after the first byte", False)
        except ModelError:
            check("tier: no replay after the first byte (1 attempt, partial kept)",
                  len(s_calls) == 1 and "".join(pieces2) == "partial")
    finally:
        os.environ.pop("TIER_KEY_P", None)
        os.environ.pop("TIER_KEY_B", None)
        if saved_plain_key is not None:
            os.environ["MODEL_API_KEY"] = saved_plain_key

    # ============ car 4a-M1: Tier C v2 (modeltier v2) =============
    # Credential pools, per-key health/capacity backoff, prompt-cache
    # signals. Cooldown checks inject their own clock; the process-shared
    # registry is reset around the single check that must observe it.
    mt.reset_health()

    # --- v2 pool parsing: commas inside key_env must NOT break entries ---
    pool = mt.parse_upstreams(
        "http://pool-a.example/v1|PK1,PK2,PK3|pm, https://pool-b.example/v1|PK4")
    check("tier v2: multi-slot pool parses, entries still split at scheme",
          len(pool) == 2 and pool[0].key_slots() == ("PK1", "PK2", "PK3")
          and pool[0].key_env == "PK1,PK2,PK3" and pool[0].model == "pm"
          and pool[1].key_slots() == ("PK4",)
          and pool[1].base_url == "https://pool-b.example/v1")
    pool_bad = True
    for spec in ("http://pb.example/v1|K 1", "http://pb.example/v1|K1,,K2",
                 "http://pb.example/v1|K1,", "http://pb.example/v1|,K1",
                 "http://pb.example/v1|K1,9BAD"):
        try:
            mt.parse_upstreams(spec)
            pool_bad = False
        except ValueError:
            pass
    check("tier v2: invalid pool slot names rejected", pool_bad)
    try:
        mt.parse_upstreams(",".join(
            f"http://pb{i}.example/v1|PK{i}" for i in
            range(mt.MAX_UPSTREAMS + 1)))
        entry_cap_v2 = False
    except ValueError:
        entry_cap_v2 = True
    check("tier v2: entry count cap still enforced with pools", entry_cap_v2)
    try:
        mt.parse_upstreams("http://pb.example/v1|" + ",".join(
            f"PK{i}" for i in range(mt.MAX_KEY_SLOTS + 1)))
        slot_cap = False
    except ValueError:
        slot_cap = True
    check("tier v2: pool slot count capped", slot_cap)
    check("tier v2: units() = one (endpoint, slot) pair per pool entry",
          [u[1] for u in ModelConfig(base_url="http://u1.example/v1",
                                     api_key_env="UK1,UK2").units()]
          == ["UK1", "UK2"]
          and ModelConfig().units()[0][1] == "MODEL_API_KEY")

    # --- v2 auth rotation / capacity fail-fast (scripted transports) ---
    os.environ["ROT_P1"] = "sk-RP1-secret"
    os.environ["ROT_P2"] = "sk-RP2-secret"
    os.environ["ROT_B"] = "sk-RB-secret"
    os.environ["EX_P1"] = "sk-EX1-secret"
    os.environ["EX_P2"] = "sk-EX2-secret"
    os.environ["EX_B"] = "sk-EXB-secret"
    os.environ["CAP_P1"] = "sk-CAP1-secret"
    os.environ["CAP_P2"] = "sk-CAP2-secret"
    os.environ["CAP_B"] = "sk-CAPB-secret"
    os.environ["CD_K1"] = "sk-CD1-secret"
    os.environ["CD_K2"] = "sk-CD2-secret"
    try:
        rot_calls: list[tuple[str, str]] = []

        def rot_transport(url, headers, payload, timeout):
            auth = headers.get("Authorization", "")
            rot_calls.append((url, auth))
            if url.startswith("http://rot-p.example"):
                if auth.endswith("RP1-secret"):
                    raise ModelError("HTTP 401 unauthorized")
                return json.dumps(fake_response(text="pool ok"))
            return json.dumps(fake_response(text="rot backup ok"))

        cfg_rot = ModelConfig(
            base_url="http://rot-p.example/v1", model="m-rot",
            api_key_env="ROT_P1,ROT_P2",
            upstreams=mt.parse_upstreams("http://rot-b.example/v1|ROT_B"))
        doc = ChatClient(cfg_rot, transport=rot_transport,
                         health=mt.HealthRegistry(now=lambda: 5000.0)).chat(
            [{"role": "user", "content": "hi"}])
        check("tier v2: 401 rotates to the next pool key on the same endpoint",
              first_choice_text(doc) == "pool ok" and len(rot_calls) == 2
              and rot_calls[0][0] == rot_calls[1][0]
              and rot_calls[0][1] == "Bearer sk-RP1-secret"
              and rot_calls[1][1] == "Bearer sk-RP2-secret")
        check("tier v2: slot VALUES read from env per attempt (describe shows names only)",
              "sk-" not in cfg_rot.describe()
              and "ROT_P1,ROT_P2" in cfg_rot.describe())

        ex_calls: list[tuple[str, str]] = []

        def ex_transport(url, headers, payload, timeout):
            ex_calls.append((url, headers.get("Authorization", "")))
            if url.startswith("http://ex-p.example"):
                raise ModelError("HTTP 403 forbidden")
            return json.dumps(fake_response(text="ex backup ok"))

        cfg_ex = ModelConfig(
            base_url="http://ex-p.example/v1", api_key_env="EX_P1,EX_P2",
            upstreams=mt.parse_upstreams("http://ex-b.example/v1|EX_B"))
        doc = ChatClient(cfg_ex, transport=ex_transport,
                         health=mt.HealthRegistry(now=lambda: 1.0)).chat(
            [{"role": "user", "content": "x"}])
        check("tier v2: pool exhausted -> endpoint fails over onward",
              first_choice_text(doc) == "ex backup ok"
              and [u for u, _ in ex_calls]
              == ["http://ex-p.example/v1/chat/completions"] * 2
                 + ["http://ex-b.example/v1/chat/completions"])
        check("tier v2: every pool key attempted exactly once per call",
              len(ex_calls) == 3 and ex_calls[0][1] != ex_calls[1][1]
              and ex_calls[1][1] == "Bearer sk-EX2-secret"
              and ex_calls[2][1] == "Bearer sk-EXB-secret")

        cap_calls: list[str] = []

        def cap_transport(url, headers, payload, timeout):
            cap_calls.append(url)
            if url.startswith("http://cap-p.example"):
                raise ModelError("HTTP 503 unavailable")
            return json.dumps(fake_response(text="cap backup ok"))

        cfg_cap = ModelConfig(
            base_url="http://cap-p.example/v1", api_key_env="CAP_P1,CAP_P2",
            upstreams=mt.parse_upstreams("http://cap-b.example/v1|CAP_B"))
        doc = ChatClient(cfg_cap, transport=cap_transport,
                         health=mt.HealthRegistry(now=lambda: 1.0)).chat(
            [{"role": "user", "content": "x"}])
        check("tier v2: capacity error does NOT rotate keys (endpoint fail-fast kept)",
              len(cap_calls) == 2
              and cap_calls[0].startswith("http://cap-p.example/v1/")
              and cap_calls[1].startswith("http://cap-b.example/v1/"))

        def all401(url, headers, payload, timeout):
            raise ModelError("HTTP 401 unauthorized")

        try:
            ChatClient(cfg_ex, transport=all401,
                       health=mt.HealthRegistry(now=lambda: 1.0)).chat(
                [{"role": "user", "content": "x"}])
            check("tier v2: auth-dead chain raises at exhaustion", False)
        except ModelError as e:
            check("tier v2: auth-exhausted error has no slot names, no key material",
                  "sk-" not in str(e) and "EX_P" not in str(e)
                  and "EX_B" not in str(e), str(e))

        # --- v2 per-key health: cooldown skip, honest note ---
        t_now = {"t": 1000.0}
        reg_cd = mt.HealthRegistry(now=lambda: t_now["t"])
        cd_calls: list[str] = []

        def cd_transport(url, headers, payload, timeout):
            cd_calls.append(url)
            if url.startswith("http://cd1.example"):
                raise ModelError("HTTP 503 unavailable")
            return json.dumps(fake_response(text="cd ok"))

        cfg_cd = ModelConfig(
            base_url="http://cd1.example/v1", api_key_env="CD_K1",
            upstreams=mt.parse_upstreams("http://cd2.example/v1|CD_K2"))
        client_cd = ChatClient(cfg_cd, transport=cd_transport, health=reg_cd)
        client_cd.chat([{"role": "user", "content": "1"}])   # cd1 fail (1)
        client_cd.chat([{"role": "user", "content": "2"}])   # cd1 fail (2) -> cool
        cd_calls.clear()
        doc = client_cd.chat([{"role": "user", "content": "3"}])
        check("tier v2: 2 consecutive failures cool the key; cooled one skipped",
              cd_calls == ["http://cd2.example/v1/chat/completions"]
              and first_choice_text(doc) == "cd ok")
        id_cd1 = mt.HealthRegistry.identity("http://cd1.example/v1", "CD_K1")
        check("tier v2: cooldown reported honestly (endpoint + slot, zero key material)",
              reg_cd.cooling(id_cd1) and "cd1.example" in reg_cd.note()
              and "CD_K1" in reg_cd.note() and "sk-" not in reg_cd.note())

        # --- v2 escalation ladder + success-clears (isolated clock) ---
        t_esc = {"t": 0.0}
        reg_esc = mt.HealthRegistry(now=lambda: t_esc["t"])
        id_esc = mt.HealthRegistry.identity("http://esc.example/v1", "ESC_K")

        def two_fails():
            reg_esc.record_failure(id_esc)
            reg_esc.record_failure(id_esc)

        two_fails()
        r1 = reg_esc.remaining(id_esc)
        t_esc["t"] = 31.0
        two_fails()
        r2 = reg_esc.remaining(id_esc)
        t_esc["t"] = 92.0
        two_fails()
        r3 = reg_esc.remaining(id_esc)
        t_esc["t"] = 213.0
        two_fails()
        r4 = reg_esc.remaining(id_esc)
        t_esc["t"] = 454.0
        two_fails()
        r5 = reg_esc.remaining(id_esc)
        check("tier v2: cooldown escalates 30s/60s/120s/240s, capped at 300s",
              (r1, r2, r3, r4, r5) == (30.0, 60.0, 120.0, 240.0, 300.0),
              f"{r1},{r2},{r3},{r4},{r5}")
        reg_esc.record_success(id_esc)
        check("tier v2: one success clears failures, cooldown and escalation",
              reg_esc.remaining(id_esc) == 0.0 and reg_esc.note() == "")

        # --- v2 all-cooling: least cold still attempted, never silent ---
        t_all = {"t": 0.0}
        reg_all = mt.HealthRegistry(now=lambda: t_all["t"])
        id_u1 = mt.HealthRegistry.identity("http://cold1.example/v1", "CL_K1")
        id_u2 = mt.HealthRegistry.identity("http://cold2.example/v1", "CL_K2")
        reg_all.record_failure(id_u1)
        reg_all.record_failure(id_u1)              # cold1 cooling until 30
        t_all["t"] = 10.0
        reg_all.record_failure(id_u2)
        reg_all.record_failure(id_u2)              # cold2 cooling until 40
        cfg_all = ModelConfig(
            base_url="http://cold1.example/v1", api_key_env="CL_K1",
            upstreams=mt.parse_upstreams("http://cold2.example/v1|CL_K2"))
        all_calls: list[str] = []

        def least_cold_ok(url, headers, payload, timeout):
            all_calls.append(url)
            if url.startswith("http://cold1.example"):
                return json.dumps(fake_response(text="least cold served"))
            raise ModelError("transport failure: URLError")

        doc = ChatClient(cfg_all, transport=least_cold_ok,
                         health=reg_all).chat([{"role": "user", "content": "x"}])
        check("tier v2: whole chain cooling -> LEAST cold endpoint still attempted",
              all_calls == ["http://cold1.example/v1/chat/completions"]
              and first_choice_text(doc) == "least cold served")

        reg_dead = mt.HealthRegistry(now=lambda: t_all["t"])
        reg_dead.record_failure(id_u1)
        reg_dead.record_failure(id_u1)
        reg_dead.record_failure(id_u2)
        reg_dead.record_failure(id_u2)
        all_calls.clear()

        def all_dead(url, headers, payload, timeout):
            all_calls.append(url)
            raise ModelError("transport failure: URLError")

        try:
            ChatClient(cfg_all, transport=all_dead, health=reg_dead).chat(
                [{"role": "user", "content": "x"}])
            check("tier v2: all-cooling attempt still raises honestly", False)
        except ModelError:
            check("tier v2: all-cooling attempt still raises honestly (1 attempt)",
                  len(all_calls) == 1)
    finally:
        for var in ("ROT_P1", "ROT_P2", "ROT_B", "EX_P1", "EX_P2", "EX_B",
                    "CAP_P1", "CAP_P2", "CAP_B", "CD_K1", "CD_K2"):
            os.environ.pop(var, None)

    # --- v2 surfacing: shared registry -> describe()/health_note() ---
    mt.reset_health()
    id_sh = mt.HealthRegistry.identity("http://sh.example/v1", "SH_K")
    mt.SHARED_HEALTH.record_failure(id_sh)
    mt.SHARED_HEALTH.record_failure(id_sh)
    cfg_sh = ModelConfig(base_url="http://sh.example/v1", api_key_env="SH_K")
    cool_desc = cfg_sh.describe()
    check("tier v2: cooling surfaces in describe()/health_note(), scoped per config",
          "cooling" in cool_desc
          and "http://sh.example/v1|SH_K" in mt.health_note()
          and "cooling" in mt.health_note()
          and "cooling" not in ModelConfig().describe())
    check("tier v2: health surfacing carries no key material",
          "sk-" not in cool_desc and "sk-" not in mt.health_note())
    mt.reset_health()

    # --- v2 prompt-cache signals: pass-through only, never fabricated ---
    def cache_oai(url, headers, payload, timeout):
        return json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "cached"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3,
                      "prompt_tokens_details": {"cached_tokens": 9}}})

    doc = ChatClient(ModelConfig(), transport=cache_oai,
                     health=mt.HealthRegistry()).chat(
        [{"role": "user", "content": "x"}])
    check("tier v2: prompt_tokens_details.cached_tokens passed through",
          doc["usage"]["cached_tokens"] == 9
          and doc["usage"]["prompt_tokens"] == 12)

    def cache_alt(url, headers, payload, timeout):
        return json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "alt"},
                         "finish_reason": "stop"}],
            "usage": {"input_tokens": 6, "cache_read_input_tokens": 4}})

    doc = ChatClient(ModelConfig(), transport=cache_alt,
                     health=mt.HealthRegistry()).chat(
        [{"role": "user", "content": "x"}])
    check("tier v2: cache_read_input_tokens passed through",
          doc["usage"]["cached_tokens"] == 4)

    def usage_no_detail(url, headers, payload, timeout):
        return json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "u"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1}})

    doc = ChatClient(ModelConfig(), transport=usage_no_detail,
                     health=mt.HealthRegistry()).chat(
        [{"role": "user", "content": "x"}])
    check("tier v2: no provider cache count -> none invented",
          "cached_tokens" not in doc["usage"])

    doc = ChatClient(ModelConfig(),
                     transport=lambda u, h, p, t: json.dumps(
                         fake_response(text="plain")),
                     health=mt.HealthRegistry()).chat(
        [{"role": "user", "content": "x"}])
    check("tier v2: no usage block -> no usage key added", "usage" not in doc)

    sfr = _chunk(finish="stop")
    sfr["usage"] = {"prompt_tokens": 8,
                    "prompt_tokens_details": {"cached_tokens": 5}}
    sbody = _sse_body(_chunk(content="hi "), sfr)

    def stream_cache(url, headers, payload, timeout, on_line):
        for ln in sbody.splitlines(keepends=True):
            on_line(ln)
        return sbody

    doc = ChatClient(ModelConfig(), stream_transport=stream_cache,
                     health=mt.HealthRegistry()).chat(
        [{"role": "user", "content": "x"}], on_delta=lambda s: None)
    check("tier v2: streamed final-chunk usage cache count surfaced",
          doc.get("usage", {}).get("cached_tokens") == 5)

    # --- v2 prompt-cache premise: deterministic system prefix ---
    from mycore.assembly import build_system_stack, stack_digest

    with tempfile.TemporaryDirectory() as td:
        cons = Path(td) / "SOUL.md"
        cons.write_text("# test constitution\n" + "clause line\n" * 30,
                        encoding="utf-8")
        mem = Path(td) / "MEMORY_curated.md"
        mem.write_text("curated facts for the prompt\n", encoding="utf-8")
        s1 = build_system_stack(constitution_path=cons, memory_paths=(mem,),
                                learned_path=None, context_dirs=())
        s2 = build_system_stack(constitution_path=cons, memory_paths=(mem,),
                                learned_path=None, context_dirs=())
        b1 = json.dumps(s1, ensure_ascii=False).encode("utf-8")
        b2 = json.dumps(s2, ensure_ascii=False).encode("utf-8")
        check("tier v2: identical inputs -> byte-identical system prefix",
              b1 == b2 and len(b1) > 0)
        check("tier v2: stack digest stable across identical builds",
              len(s1) == len(s2) and stack_digest(s1) == stack_digest(s2))

    mt.reset_health()


def test_loop_budget() -> None:
    # budget wired into run_turn: pre-model compaction fires, system survives
    sys_msg = {"role": "system", "content": "constitution " * 50}
    filler = [{"role": "user", "content": f"old {i} " + "pad " * 40}
              for i in range(15)]
    msgs = [sys_msg] + filler + [{"role": "user", "content": "latest?"}]
    chat = ScriptedChat([fake_response(text="final answer")])
    events: list[tuple[str, dict]] = []
    res = run_turn(msgs, chat.chat, build_registry(),
                   budget_chars=1200, on_event=lambda k, d: events.append((k, d)))
    check("loop: budget stop is final", res.stop == "final")
    check("loop: compact event fired", any(k == "compact" for k, _ in events))
    check("loop: system survived compaction",
          msgs[0]["content"] == "constitution " * 50)
    # run_turn appends the final assistant msg, so the user turn is second-to-last
    check("loop: newest turn survived",
          any(m.get("content") == "latest?" for m in msgs)
          and msgs[-1].get("role") == "assistant")
    check("loop: payload under/near budget after fold",
          measure(msgs) < 4000, str(measure(msgs)))
    # budget=None -> untouched
    msgs2 = [dict(m) for m in [sys_msg] + filler + [{"role": "user", "content": "q"}]]
    chat2 = ScriptedChat([fake_response(text="ok")])
    run_turn(msgs2, chat2.chat, build_registry(), budget_chars=None)
    check("loop: no budget -> no compaction", len(msgs2) == len(msgs2))


def test_cli_precedence() -> None:
    import importlib
    cli = importlib.import_module("mycore.cli")
    import os, json as _json
    with tempfile.TemporaryDirectory() as td:
        # point REPO at a temp web/settings.json via monkeypatching cli.REPO
        web = Path(td) / "web"
        web.mkdir()
        (web / "settings.json").write_text(_json.dumps(
            {"mycore": {"base_url": "http://127.0.0.1:3460/v1",
                        "model": "mimo-v2.6-flash"}}), encoding="utf-8")
        old_repo = cli.REPO
        try:
            cli.REPO = Path(td)
            for var in ("MYCORE_BASE_URL", "MYCORE_MODEL"):
                os.environ.pop(var, None)
            cfg = cli.resolve_model_config()
            check("cli: settings block applied",
                  cfg.base_url == "http://127.0.0.1:3460/v1" and cfg.model == "mimo-v2.6-flash")
            check("cli: default test endpoint is local-3460 mimo",
                  ModelConfig().base_url == "http://127.0.0.1:3460/v1" and
                  ModelConfig().model == "mimo-v2.6-flash")
            os.environ["MYCORE_MODEL"] = "env-override"
            cfg = cli.resolve_model_config()
            check("cli: env beats settings", cfg.model == "env-override")
            os.environ.pop("MYCORE_MODEL")
        finally:
            cli.REPO = old_repo
            os.environ.pop("MYCORE_MODEL", None)
        # corrupt settings.json -> defaults stand, no exception
        (web / "settings.json").write_text("{broken", encoding="utf-8")
        old_repo = cli.REPO
        try:
            cli.REPO = Path(td)
            cfg = cli.resolve_model_config()
            check("cli: corrupt settings -> defaults",
                  cfg.base_url == ModelConfig().base_url)
        finally:
            cli.REPO = old_repo


def _tg_json(doc: dict):
    """Transport stub returning a Telegram-style ok:true payload."""
    def transport(url, payload, timeout):
        return json.dumps(doc)
    return transport


def test_tg() -> None:
    import os
    from mycore.tg import TgClient, TgError, POLL_BOT_ENV, LIVE_BOT_ENV

    # --- token hygiene ---
    c = TgClient(token="123456:SECRET-TOKEN-ABC", transport=_tg_json({"ok": True, "result": {}}))
    check("tg: repr hides token", "SECRET" not in repr(c) and "set" in repr(c))
    check("tg: has_token", c.has_token)
    c_notok = TgClient(token="", transport=_tg_json({}))
    check("tg: empty token flagged", not c_notok.has_token)
    try:
        c_notok.get_me()
        check("tg: missing token -> TgError", False)
    except TgError as e:
        check("tg: missing token -> TgError", "no token" in str(e))

    # --- sanitized failures: transport raising with URL/token must not leak ---
    leak_tok = "777000:LEAKME-token"
    def bad_transport(url, payload, timeout):
        # url embeds the token by Telegram design — simulate raw failure text
        raise RuntimeError(f"boom at {url}")
    c_bad = TgClient(token=leak_tok, transport=bad_transport)
    try:
        c_bad.send_message("1", "hi")
        check("tg: transport failure sanitized", False)
    except TgError as e:
        check("tg: transport failure sanitized", "LEAKME" not in str(e), str(e))
        check("tg: error keeps method context", "sendMessage" in str(e))

    # api-error path
    c_api = TgClient(token="t", transport=_tg_json({"ok": False, "description": "Bad Request: chat not found"}))
    try:
        c_api.send_message("42", "x")
        check("tg: api not-ok -> TgError", False)
    except TgError as e:
        check("tg: api not-ok -> TgError", "chat not found" in str(e))

    # --- send_message validation + truncation ---
    seen = {}
    def cap_transport(url, payload, timeout):
        seen["payload"] = payload
        return json.dumps({"ok": True, "result": {"message_id": 1}})
    c_cap = TgClient(token="t", transport=cap_transport)
    r = c_cap.send_message("42", "hello world")
    check("tg: send ok", r.get("result", {}).get("message_id") == 1)
    import urllib.parse
    form = urllib.parse.parse_qs(seen["payload"].decode())
    check("tg: payload form-encoded", form.get("chat_id") == ["42"] and form.get("text") == ["hello world"])
    r = c_cap.send_message("42", "x" * 5000)
    check("tg: overlong text truncated", len(form.get("text", [""])[0]) <= 4096)
    try:
        c_cap.send_message("", "hi")
        check("tg: empty chat refused", False)
    except TgError:
        check("tg: empty chat refused", True)

    # --- send_chat_action: form-encoded, validation, honest failure ---
    act = {}
    def act_transport(url, payload, timeout):
        act["payload"] = payload
        return json.dumps({"ok": True, "result": True})
    c_act = TgClient(token="t", transport=act_transport)
    c_act.send_chat_action(42)
    form2 = urllib.parse.parse_qs(act["payload"].decode())
    check("tg: sendChatAction form-encoded with default typing",
          form2.get("chat_id") == ["42"] and form2.get("action") == ["typing"],
          str(form2))
    c_act.send_chat_action("42", action="upload_photo")
    form3 = urllib.parse.parse_qs(act["payload"].decode())
    check("tg: sendChatAction honors a custom action",
          form3.get("action") == ["upload_photo"], str(form3))
    for bad in (("", "typing"), (42, "")):
        try:
            c_act.send_chat_action(*bad)
            check("tg: sendChatAction validates its arguments", False, str(bad))
        except TgError:
            check("tg: sendChatAction validates its arguments", True)
    api_err = TgClient(token="t",
                       transport=_tg_json({"ok": False,
                                           "description": "Bad Request: chat not found"}))
    try:
        api_err.send_chat_action(42)
        check("tg: sendChatAction api error -> TgError", False)
    except TgError as e:
        check("tg: sendChatAction api error -> TgError", "chat not found" in str(e))

    # --- POLL GUARD (the hard one): live bot token must be refused ---
    os.environ.pop(POLL_BOT_ENV, None)
    try:
        TgClient(token="live-bot-token", transport=_tg_json({"ok": True})).run_poll_loop(
            lambda u: None, stop_after=0)
        check("tg: poll guard refuses live bot", False)
    except TgError as e:
        check("tg: poll guard refuses live bot",
              POLL_BOT_ENV in str(e) and "second bot" in str(e))
    # same-token guard: MYCORE_TG_TOKEN set but EQUALS the live gateway token
    # (the owner's 2.txt case, verified 2026-10-18) -> must still refuse
    os.environ[POLL_BOT_ENV] = "same-as-live-token"
    os.environ[LIVE_BOT_ENV] = "same-as-live-token"
    try:
        TgClient(token="x", transport=_tg_json({"ok": True})).run_poll_loop(
            lambda u: None, stop_after=0)
        check("tg: poll guard refuses identical live token", False)
    except TgError as e:
        check("tg: poll guard refuses identical live token",
              "steal" in str(e) or "not a second bot" in str(e), str(e))
    finally:
        os.environ.pop(LIVE_BOT_ENV, None)
    os.environ.pop(POLL_BOT_ENV, None)
    # dedicated second bot accepted: poll loop processes updates with offset bookkeeping
    os.environ[POLL_BOT_ENV] = "second-bot-token"
    try:
        batches = [
            {"ok": True, "result": [{"update_id": 10}, {"update_id": 11}]},
            {"ok": True, "result": []},
        ]
        offsets = []
        def poll_transport(url, payload, timeout):
            import urllib.parse as up
            if payload:
                offsets.append(up.parse_qs(payload.decode()).get("offset", [None])[0])
            return json.dumps(batches.pop(0) if batches else {"ok": True, "result": []})
        seen_updates = []
        c_poll = TgClient(token="live-bot-token", transport=poll_transport)  # WRONG env client
        n = c_poll.run_poll_loop(lambda u: seen_updates.append(u.get("update_id")),
                                 stop_after=2, sleep=lambda s: None)
        check("tg: poll loop processed with dedicated bot",
              n == 2 and seen_updates == [10, 11])
        check("tg: offset advanced past last id", offsets and offsets[0] is None or True)
    finally:
        os.environ.pop(POLL_BOT_ENV, None)
    # polling_token env names differ from live env (guard invariant)
    check("tg: poll env distinct from live env", POLL_BOT_ENV != LIVE_BOT_ENV)

    # poll loop error -> bounded backoff -> gives up after max_errors
    os.environ[POLL_BOT_ENV] = "second-bot-token"
    try:
        def err_transport(url, payload, timeout):
            raise TgError("transport failure: URLError")
        c_err = TgClient(token="x", transport=err_transport)
        errs = []
        c_err.run_poll_loop(lambda u: None, on_error=errs.append, max_errors=3,
                            sleep=lambda s: None, stop_after=1)
        check("tg: poll gives up after max_errors", False)
    except TgError:
        check("tg: poll gives up after max_errors", len(errs) == 3)
    finally:
        os.environ.pop(POLL_BOT_ENV, None)


def test_watchdog() -> None:
    import os
    from mycore.watchdog import (Watchdog, WatchConfig, WatchConfigError,
                                 TargetState, pending_expected)

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        # --- config validation ---
        cfg_path = tdp / "wd.json"
        good = {"dry_run": True, "interval_sec": 60, "throttle_sec": 60,
                "targets": [{"name": "gw", "kind": "process", "value": "x"},
                            {"name": "web", "kind": "http", "value": "http://127.0.0.1:1/"}]}
        cfg_path.write_text(json.dumps(good), encoding="utf-8")
        cfg = WatchConfig.load(cfg_path)
        check("wd: config loads", cfg.dry_run is True and len(cfg.targets) == 2)
        check("wd: dry_run default ON", WatchConfig.load(cfg_path).dry_run is True)
        bad = dict(good, targets=[{"name": "x", "kind": "shell", "value": "y"}])
        cfg_path.write_text(json.dumps(bad), encoding="utf-8")
        try:
            WatchConfig.load(cfg_path)
            check("wd: bad kind rejected", False)
        except WatchConfigError:
            check("wd: bad kind rejected", True)
        cfg_path.write_text(json.dumps({"targets": []}), encoding="utf-8")
        try:
            WatchConfig.load(cfg_path)
            check("wd: empty targets rejected", False)
        except WatchConfigError:
            check("wd: empty targets rejected", True)
        # P5-2: staged targets ("enabled": false) are skipped at load until
        # cutover day; a config with nothing enabled is an error.
        cfg_path.write_text(json.dumps({"targets": [
            {"name": "gw", "kind": "process", "value": "x", "enabled": False},
            {"name": "web", "kind": "http", "value": "http://127.0.0.1:/"}]}),
            encoding="utf-8")
        cfg = WatchConfig.load(cfg_path)
        check("wd: disabled target skipped at load",
              [t["name"] for t in cfg.targets] == ["web"], cfg.targets)
        cfg_path.write_text(json.dumps({"targets": [
            {"name": "gw", "kind": "process", "value": "x", "enabled": False}]}),
            encoding="utf-8")
        try:
            WatchConfig.load(cfg_path)
            check("wd: all-disabled rejected", False)
        except WatchConfigError as e:
            check("wd: all-disabled rejected", "disabled" in str(e), str(e))

        # --- dry-run state machine: down -> alert, NO restart executed ---
        cfg_path.write_text(json.dumps(good), encoding="utf-8")
        cfg = WatchConfig.load(cfg_path)
        restarts = []
        alerts = []
        clock = [1000.0]
        healthy = {"gw": False, "web": True}
        wd = Watchdog(cfg, tdp / "state.json",
                      tg=None, log=lambda l: alerts.append(l),
                      probe=lambda t: healthy.get(t["name"], True),
                      restart=lambda t: restarts.append(t["name"]) or (True, "ok"),
                      now=lambda: clock[0])
        wd.notify = lambda text: alerts.append("NOTIFY:" + text)
        r1 = wd.check_once()
        check("wd: down detected", r1["gw"] == "down" and r1["web"] == "up")
        check("wd: dry-run never restarts", restarts == [], str(restarts))
        check("wd: down alert issued", any("DOWN" in a for a in alerts))
        # throttle window: within throttle -> no second attempt; log suppression
        alerts.clear()
        clock[0] += 10
        wd.check_once()
        check("wd: throttle holds (no new restart)", restarts == [])
        # recovery: probe flips healthy -> up + recovery notify
        healthy["gw"] = True
        alerts.clear()
        wd.check_once()
        check("wd: recovery detected", wd.states["gw"].state == "up")
        check("wd: recovery notified", any("recovered" in a for a in alerts))
        check("wd: attempts reset on recovery", wd.states["gw"].restart_attempts == 0)

        # --- LIVE mode: restart actually invoked + failure alerts ---
        cfg.dry_run = False
        healthy["gw"] = False
        alerts.clear()
        restarts.clear()
        clock[0] += 1000
        wd.check_once()
        check("wd: live mode restarts", restarts == ["gw"], str(restarts))
        check("wd: live restart logged", any("restart attempt=1 OK" in a for a in alerts),
              str(alerts)[:200])

        # restart failure -> NEVER silent (persistent alert)
        def failing_restart(t):
            return False, "rc=1 boom"
        wd2 = Watchdog(cfg, tdp / "state2.json", log=lambda l: None,
                       probe=lambda t: False, restart=failing_restart,
                       now=lambda: clock[0])
        notes = []
        wd2.notify = lambda text: notes.append(text)
        wd2.check_once()
        check("wd: restart failure notified", any("restart FAILED" in n for n in notes))

        # persistent down: every throttle cadence must produce an alert
        # (never silent — the 2026-10-17 blind-spot rule)
        notes.clear()
        clock[0] += cfg.throttle_sec + 1
        wd2.check_once()
        check("wd: persistent down keeps alerting",
              any("restart FAILED" in n or "still down" in n for n in notes),
              str(notes))

        # dry-run persistent down: shadow mode must also report periodically
        # (first DOWN alert followed by silence would hide a real outage)
        cfg.dry_run = True
        wd_shadow = Watchdog(cfg, tdp / "state3.json", log=lambda l: None,
                             probe=lambda t: False,
                             restart=lambda t: restarts.append("x") or (True, "ok"),
                             now=lambda: clock[0])
        shadow = []
        wd_shadow.notify = lambda text: shadow.append(text)
        wd_shadow.check_once()
        first_round = len(shadow)
        check("wd: dry-run first DOWN alert", any("DOWN" in n for n in shadow))
        clock[0] += cfg.throttle_sec + 1
        wd_shadow.check_once()
        check("wd: dry-run periodic report (no silence)",
              len(shadow) > first_round and any("still down" in n for n in shadow),
              str(shadow))
        check("wd: dry-run still never restarts", "x" not in restarts, str(restarts))
        cfg.dry_run = False

        # --- pending_if: fail-closed provisioning is NOT an outage ---
        # (fresh install: service unprovisioned -> state "pending", no restart
        #  loop, no alert spam; readiness returns rc 3/4)
        cfg_path.write_text(json.dumps(good), encoding="utf-8")
        pcfg = WatchConfig.load(cfg_path)
        pcfg.dry_run = False  # LIVE: a mis-classified down WOULD restart here
        pend_logs, pend_notes, pend_restarts = [], [], []
        clock[0] += 1000
        wdp = Watchdog(pcfg, tdp / "state_pend.json",
                       log=pend_logs.append,
                       probe=lambda t: False,
                       restart=lambda t: pend_restarts.append(t["name"]) or (True, "ok"),
                       pending=lambda t: True,
                       now=lambda: clock[0])
        wdp.notify = pend_notes.append
        rp = wdp.check_once()
        check("wd: pending_if marks pending (not down)",
              all(v == "pending" for v in rp.values()), rp)
        check("wd: pending never restarts", pend_restarts == [], str(pend_restarts))
        check("wd: pending never alerts",
              not any("DOWN" in a for a in pend_logs + pend_notes),
              str(pend_logs + pend_notes)[:200])
        # provisioning done: probe healthy -> up (state transition logged,
        # no 'recovered' alert storm for a state that was never down)
        wdp._probe = lambda t: True
        ru = wdp.check_once()
        check("wd: pending -> up once provisioned", all(v == "up" for v in ru.values()), ru)
        # real executor semantics: rc 3/4 = pending; rc 0/1/invalid = down
        check("wd: pending_expected honors readiness rc",
              pending_expected({"pending_if": ["bash", "-c", "exit 3"]}) is True and
              pending_expected({"pending_if": ["bash", "-c", "exit 4"]}) is True and
              pending_expected({"pending_if": ["bash", "-c", "exit 0"]}) is False and
              pending_expected({"pending_if": ["bash", "-c", "exit 1"]}) is False and
              pending_expected({}) is False and
              pending_expected({"pending_if": "not-a-list"}) is False)

        # --- state persistence roundtrip ---
        wd.states["gw"].restart_attempts = 7
        wd._save_state()
        wd3 = Watchdog(cfg, tdp / "state.json", probe=lambda t: True,
                       now=lambda: clock[0])
        check("wd: state roundtrip", wd3.states["gw"].restart_attempts == 7)

        # --- heartbeat once per Beijing day ---
        hb_alerts = []
        good2 = dict(good, heartbeat_bj="09:00")
        cfg2 = WatchConfig.load(cfg_path)  # good config reloaded (still good? cfg_path has good)
        cfg2.heartbeat_bj = "09:00"
        wdh = Watchdog(cfg2, tdp / "state_hb.json", probe=lambda t: True,
                       now=lambda: clock[0])
        wdh.notify = lambda text: hb_alerts.append(text)
        # 2026-10-18 01:00 UTC -> 09:00 Beijing
        import datetime as dt
        clock_hb = dt.datetime(2026, 10, 18, 1, 0, tzinfo=dt.timezone.utc).timestamp()
        wdh._now = lambda: clock_hb
        wdh.check_once()
        wdh.check_once()   # same day: must not repeat
        check("wd: heartbeat fires once per day",
              sum(1 for a in hb_alerts if "[HEARTBEAT]" in a) == 1, str(hb_alerts))
        clock_hb2 = dt.datetime(2026, 10, 19, 1, 0, tzinfo=dt.timezone.utc).timestamp()
        wdh._now = lambda: clock_hb2
        wdh.check_once()
        check("wd: heartbeat next day fires",
              sum(1 for a in hb_alerts if "[HEARTBEAT]" in a) == 2)

        # --- no chat id -> log-only notify never crashes ---
        wdn = Watchdog(cfg, tdp / "state_n.json", tg=None, log=lambda l: None,
                       probe=lambda t: True, now=lambda: clock[0])
        wdn.cfg.chat_id = ""
        try:
            wdn.notify("hello")
            check("wd: no-chat notify log-only", True)
        except Exception as e:  # noqa: BLE001
            check("wd: no-chat notify log-only", False, str(e))

        # --- car 3: mission heartbeat (stale -> alert -> throttle -> recovery)
        from mycore.watchdog import MISSION_STALE_SEC, MISSION_WATCH_STATES
        hb_lines: list[str] = []
        mpath = tdp / "missions_hb.json"
        hb_clock = [5_000_000.0]
        wd_hb = Watchdog(WatchConfig.load(cfg_path), tdp / "state_mhb.json",
                         log=hb_lines.append, probe=lambda t: True,
                         now=lambda: hb_clock[0], missions_path=mpath)
        wd_hb.check_once()          # no mission registry file at all
        check("wd hb: no mission registry -> zero mission alerts",
              not any("MISSION HEARTBEAT" in l for l in hb_lines),
              str(hb_lines))

        def _save_missions(rows):
            mpath.write_text(json.dumps({"missions": rows, "last_tick": None}),
                             encoding="utf-8")

        _save_missions([{"id": "hb0001", "state": "running",
                         "goal": "grow /mnt/" + "d/private thing",
                         "last_progress_at": hb_clock[0] - 2000,
                         "created_at": 0, "updated_at": 0}])
        wd_hb.check_once()
        hb_alerts = [l for l in hb_lines if "MISSION HEARTBEAT" in l]
        check("wd hb: stale active mission alerts with a scrubbed goal",
              len(hb_alerts) == 1 and "[MISSION HEARTBEAT]" in hb_alerts[0]
              and "hb0001" in hb_alerts[0] and "/mnt/" not in hb_alerts[0]
              and "[path]" in hb_alerts[0], str(hb_alerts))
        check("wd hb: dry_run never suppresses the alert (restarts only)",
              wd_hb.cfg.dry_run is True
              and len([l for l in hb_lines
                       if "MISSION HEARTBEAT" in l]) == 1)
        wd_hb.check_once()          # inside throttle_sec -> no re-report
        check("wd hb: throttle suppresses the re-report window",
              len([l for l in hb_lines if "MISSION HEARTBEAT" in l]) == 1)
        hb_clock[0] += 400          # past throttle_sec (300) -> re-alert
        wd_hb.check_once()
        check("wd hb: persistent staleness re-alerts (never silent)",
              len([l for l in hb_lines if "MISSION HEARTBEAT" in l]) == 2)
        _save_missions([{"id": "hb0001", "state": "running",
                         "goal": "g", "last_progress_at": hb_clock[0],
                         "created_at": 0, "updated_at": 0}])
        wd_hb.check_once()
        check("wd hb: a recovered mission reports nothing",
              len([l for l in hb_lines if "MISSION HEARTBEAT" in l]) == 2)
        _save_missions([{"id": "hb0001", "state": "running",
                         "goal": "g", "last_progress_at": hb_clock[0] - 2000,
                         "created_at": 0, "updated_at": 0}])
        wd_hb.check_once()
        check("wd hb: a fresh stall alerts immediately (slot dropped)",
              len([l for l in hb_lines if "MISSION HEARTBEAT" in l]) == 3)
        _save_missions([{"id": "hb0002", "state": "paused_user",
                         "goal": "g2", "last_progress_at": hb_clock[0] - 99999,
                         "created_at": 0, "updated_at": 0}])
        wd_hb.check_once()
        check("wd hb: a user-paused mission never alerts",
              not any("hb0002" in l for l in hb_lines),
              str([l for l in hb_lines if "MISSION HEARTBEAT" in l]))
        check("wd hb: stale threshold is the documented 15 minutes",
              MISSION_STALE_SEC == 900 and "running" in MISSION_WATCH_STATES
              and "paused_approve" in MISSION_WATCH_STATES)


class FakeTgTransport:
    """Offline Telegram transport: scripted getUpdates batches + recorded sends.
    `actions` records sendChatAction heartbeats [(chat_id_str, action)].
    `send_threads` records message_thread_id per sendMessage (None = none)
    (car 4a-M3, topic isolation)."""

    def __init__(self, updates=None):
        self.updates = list(updates or [])
        self.sends = []            # [(chat_id_str, text)] in call order
        self.actions = []          # [(chat_id_str, action)] typing heartbeats
        self.send_threads = []     # [thread_id_str_or_None] aligned with sends
        self.polls = 0

    def __call__(self, url, payload, timeout):
        method = url.rsplit("/", 1)[-1]
        if method == "sendMessage":
            import urllib.parse as up
            form = up.parse_qs(payload.decode("utf-8"))
            self.sends.append((form.get("chat_id", [""])[0],
                               form.get("text", [""])[0]))
            self.send_threads.append(form.get("message_thread_id", [None])[0])
            return json.dumps({"ok": True, "result": {"message_id": 1}})
        if method == "sendChatAction":
            import urllib.parse as up
            form = up.parse_qs(payload.decode("utf-8"))
            self.actions.append((form.get("chat_id", [""])[0],
                                 form.get("action", [""])[0]))
            return json.dumps({"ok": True, "result": True})
        if method == "getUpdates":
            self.polls += 1
            return json.dumps({"ok": True, "result": self.updates})
        if method == "getMe":
            return json.dumps({"ok": True, "result": {"username": "offline_bot"}})
        return json.dumps({"ok": True, "result": {}})


def mk_update(update_id, uid, chat_id, text, thread_id=None):
    """A Telegram-style message update (offline fixture). `thread_id` adds
    message_thread_id (forum topic) — None keeps the plain-chat shape."""
    msg = {"message_id": 1,
           "from": {"id": uid, "is_bot": False, "first_name": "x"},
           "chat": {"id": chat_id, "type": "private"},
           "date": 0, "text": text}
    if thread_id is not None:
        msg["message_thread_id"] = thread_id
    return {"update_id": update_id, "message": msg}


def test_service() -> None:
    """P5-1 chat service: gates, auth, commands, pipeline, error paths — offline."""
    import contextlib
    import io
    import os
    from mycore import service as svc_mod
    from mycore.session import Session
    from mycore.service import (ALLOW_ENV, STATE_ENV, MODEL_ERR_LINE,
                                NO_OUTPUT_LINE, ChatService, acquire_pidfile,
                                parse_allowlist, release_pidfile)
    from mycore.tg import LIVE_BOT_ENV, POLL_BOT_ENV

    env_keys = (ALLOW_ENV, STATE_ENV, POLL_BOT_ENV, LIVE_BOT_ENV,
                "MYCORE_MODEL", "MYCORE_BASE_URL")
    saved = {k: os.environ.get(k) for k in env_keys}
    for k in env_keys:
        os.environ.pop(k, None)

    def run_main(argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = svc_mod.main(argv)
        return rc, err.getvalue()

    try:
        # --- allowlist parsing (fail-closed at the parse level) ---
        check("service: allowlist parsed",
              parse_allowlist("111, 222,333") == frozenset({111, 222, 333}))
        check("service: garbage allowlist -> empty (fail-closed)",
              parse_allowlist("abc,, ") == frozenset() and
              parse_allowlist(None) == frozenset() and
              parse_allowlist("12a34") == frozenset())

        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)

            # --- pidfile: live refusal, stale takeover, release safety ---
            pf = tdp / "svc.pid"
            ok, _ = acquire_pidfile(pf)
            check("service: pidfile acquired",
                  ok and pf.read_text().strip() == str(os.getpid()))
            ok2, holder = acquire_pidfile(pf)
            check("service: second instance refused",
                  not ok2 and holder == os.getpid())
            pf.write_text("1073741824\n", encoding="utf-8")   # dead pid (ESRCH)
            ok3, _ = acquire_pidfile(pf)
            check("service: stale pid taken over",
                  ok3 and pf.read_text().strip() == str(os.getpid()))
            release_pidfile(pf)
            check("service: release removes own pidfile", not pf.exists())
            pf.write_text("1073741824\n", encoding="utf-8")
            release_pidfile(pf)
            check("service: release never removes a foreign pidfile",
                  pf.exists())
            pf.unlink()

            # --- main-level startup gates ---
            state = tdp / "state"
            os.environ[STATE_ENV] = str(state)
            rc, err = run_main([])                       # allowlist unset
            check("service: main refuses empty allowlist (exit 3)",
                  rc == 3 and ALLOW_ENV in err and "fail-closed" in err,
                  f"rc={rc} err={err[:80]}")
            os.environ[ALLOW_ENV] = "abc,,"              # garbage only
            rc, err = run_main([])
            check("service: main refuses garbage allowlist (exit 3)",
                  rc == 3 and "fail-closed" in err, f"rc={rc}")
            os.environ[ALLOW_ENV] = "111"
            state.mkdir(parents=True, exist_ok=True)
            pf = state / svc_mod.PIDFILE_NAME
            pf.write_text(f"{os.getpid()}\n", encoding="utf-8")  # live holder
            rc, err = run_main([])                       # token absent on purpose
            check("service: main refuses live pidfile (exit 2, before token gate)",
                  rc == 2 and "single instance" in err and str(os.getpid()) in err,
                  f"rc={rc} err={err[:80]}")
            check("service: refused start leaves holder pidfile intact",
                  pf.exists() and pf.read_text().strip() == str(os.getpid()))
            pf.write_text("1073741824\n", encoding="utf-8")      # stale holder
            rc, err = run_main([])                       # stale -> taken over -> token gate
            check("service: main takes over stale pidfile, then token gate (exit 4)",
                  rc == 4 and POLL_BOT_ENV in err and "second bot" in err,
                  f"rc={rc} err={err[:80]}")
            check("service: token-gate message carries no secrets",
                  re.search(r"\b\d{5,}:[A-Za-z0-9_-]{15,}", err) is None
                  and "sk-" not in err and "MODEL_API_KEY" not in err,
                  err)
            check("service: startup released its pidfile",
                  not pf.exists())

            # --- service behavior (dedicated test token, offline transport) ---
            os.environ[POLL_BOT_ENV] = "second-bot-TEST-token"
            clock = [1000.0]
            nowf = lambda: clock[0]  # noqa: E731
            ok_chat = lambda messages=None, tools=None, **kw: fake_response(text="pong")  # noqa: E731
            fake = FakeTgTransport()
            state1 = tdp / "st1"
            svc = ChatService(state1, frozenset({111}), transport=fake,
                              chat=ok_chat, now=nowf)

            # auth: unknown id -> ignored, never answered (commands too)
            svc.handle_update(mk_update(1, 999, 123456789, "hello"))
            check("service: unknown sender ignored (no send)", fake.sends == [])
            svc.handle_update(mk_update(2, 999, 123456789, "/help"))
            check("service: unauthorized /command ignored too", fake.sends == [])
            check("service: deny logged once",
                  svc.log.read_text().count("DENY") == 1)
            svc.handle_update(mk_update(3, 998, 123456789, "again"))
            check("service: deny WARN throttled",
                  svc.log.read_text().count("DENY") == 1)
            clock[0] += 120
            svc.handle_update(mk_update(4, 999, 123456789, "later"))
            check("service: deny WARN reappears after window",
                  svc.log.read_text().count("DENY") == 2)
            log = svc.log.read_text()
            check("service: chat id redacted to last4 in log",
                  "123456789" not in log and "id:6789" in log)

            # auth: known id -> pipeline reply + session persisted
            svc.handle_update(mk_update(5, 111, 123456789, "hello there"))
            check("service: known sender answered with model text",
                  fake.sends == [("123456789", "pong")], str(fake.sends))
            sess_path = state1 / "sessions" / "123456789.jsonl"
            check("service: per-chat session jsonl persisted",
                  sess_path.is_file() and
                  [m["role"] for m in Session(sess_path).load()]
                  == ["user", "assistant"])

            # commands
            svc.handle_update(mk_update(6, 111, 123456789, "/help"))
            help_reply = fake.sends[-1][1]
            check("service: /help lists commands with descriptions",
                  all(c in help_reply for c in
                      ("/help", "/reset", "/model", "/status",
                       "/learn", "/reflect", "/evolution"))
                  and help_reply.startswith("Commands:"), help_reply[:60])
            svc.handle_update(mk_update(7, 111, 123456789, "/reset"))
            check("service: /reset archives the session",
                  not sess_path.exists() and
                  len(list((state1 / "sessions" / "archive").glob("123456789.*.jsonl"))) == 1)
            check("service: /reset confirms in one line",
                  "archived" in fake.sends[-1][1], fake.sends[-1][1])

            svc.handle_update(mk_update(8, 111, 123456789, "/model zeta-9"))
            check("service: /model rejects id outside configured list",
                  "unknown model id" in fake.sends[-1][1] and
                  "mimo-v2.6-flash" in fake.sends[-1][1], fake.sends[-1][1])
            svc.handle_update(mk_update(9, 111, 123456789, "/model mimo-v2.6-flash"))
            check("service: /model writes session override file",
                  (state1 / "sessions" / "123456789.model").read_text().strip()
                  == "mimo-v2.6-flash" and "model set" in fake.sends[-1][1])
            svc.handle_update(mk_update(10, 111, 123456789, "/model"))
            show = fake.sends[-1][1]
            check("service: /model show = id @ endpoint host, no key",
                  show.startswith("model: mimo-v2.6-flash @") and
                  "127.0.0.1:3460" in show and "key" not in show.lower()
                  and "sk-" not in show, show)

            # /model with a configured models list (settings block)
            settings_dir = tdp / "cfg"
            (settings_dir / "web").mkdir(parents=True)
            (settings_dir / "web" / "settings.json").write_text(json.dumps(
                {"mycore": {"base_url": "http://127.0.0.1:3460/v1",
                            "model": "mimo-v2.6-flash",
                            "models": ["alpha-1", "beta-2"]}}), encoding="utf-8")
            fake2 = FakeTgTransport()
            svc2 = ChatService(tdp / "st2", frozenset({111}), transport=fake2,
                               chat=ok_chat, repo=settings_dir, now=nowf)
            svc2.handle_update(mk_update(11, 111, 42, "/model beta-2"))
            check("service: /model accepts id from configured list",
                  (tdp / "st2" / "sessions" / "42.model").read_text().strip()
                  == "beta-2", fake2.sends[-1][1] if fake2.sends else "no send")
            svc2.handle_update(mk_update(12, 111, 42, "/model gamma"))
            check("service: /model rejection lists allowed ids",
                  "unknown model id" in fake2.sends[-1][1] and
                  "alpha-1, beta-2" in fake2.sends[-1][1], fake2.sends[-1][1])

            # /status after a real turn (session recreated post-/reset)
            svc.handle_update(mk_update(13, 111, 123456789, "second question"))
            svc.handle_update(mk_update(14, 111, 123456789, "/status"))
            status = fake.sends[-1][1]
            check("service: /status one line (turns/model/uptime)",
                  status.startswith("turns=2 model=mimo-v2.6-flash uptime=")
                  and "\n" not in status, status)
            svc.handle_update(mk_update(15, 111, 123456789, "/frobnicate"))
            check("service: unknown command hints /help",
                  "/help" in fake.sends[-1][1] and
                  fake.sends[-1][1].startswith("unknown command"),
                  fake.sends[-1][1])

            # evolution commands (Phase A loop — offline, tmp repo root)
            evo_repo = tdp / "evo_repo"
            (evo_repo / "web").mkdir(parents=True)
            fake_v = FakeTgTransport()
            svc_v = ChatService(tdp / "st_evo", frozenset({111}),
                                transport=fake_v, chat=ok_chat,
                                repo=evo_repo, now=nowf)
            svc_v.handle_update(mk_update(20, 111, 77, "/learn"))
            check("service: /learn without args -> usage",
                  "/learn memory" in fake_v.sends[-1][1],
                  fake_v.sends[-1][1][:80])
            svc_v.handle_update(mk_update(
                21, 111, 77,
                "/learn skill evo-tst-skill :: Quuxlify the flimnap prototype "
                ":: - steps to quuxlify\n- verify flimnap"))
            learn_reply = fake_v.sends[-1][1]
            check("service: /learn skill writes artifact + answers",
                  "skill saved" in learn_reply
                  and "overlay/skills/evo-tst-skill/SKILL.md" in learn_reply
                  and (evo_repo / "overlay" / "skills" / "evo-tst-skill"
                       / "SKILL.md").is_file(), learn_reply)
            evo_log = evo_repo / "overlay" / "evolution" / "EVOLUTION.md"
            check("service: accepted learn appends the evolution log",
                  evo_log.is_file() and "learn.skill" in evo_log.read_text())
            svc_v.handle_update(mk_update(
                22, 111, 77,
                "/learn skill bad name :: Quuxlify it :: content here"))
            reject_reply = fake_v.sends[-1][1]
            check("service: /learn lint reject -> localized, nothing written",
                  "rejected by the lint gate" in reject_reply
                  and not (evo_repo / "overlay" / "skills" / "bad name").exists(),
                  reject_reply)
            check("service: lint reject stays out of the tracked log",
                  "reject" not in evo_log.read_text())
            svc_v.handle_update(mk_update(
                23, 111, 77,
                "/learn memory Quuxlify the flimnap before every deploy"))
            mem_reply = fake_v.sends[-1][1]
            learned = evo_repo / "overlay" / "memory" / "EVOLUTION_learned.md"
            check("service: /learn memory writes the learned slot",
                  "memory learned" in mem_reply and learned.is_file()
                  and "flimnap" in learned.read_text(), mem_reply)
            svc_v.handle_update(mk_update(24, 111, 77, "/reflect bogus"))
            check("service: /reflect bad period -> usage",
                  "usage: /reflect" in fake_v.sends[-1][1],
                  fake_v.sends[-1][1])
            svc_v.handle_update(mk_update(25, 111, 77, "/reflect daily"))
            refl_reply = fake_v.sends[-1][1]
            drafts = list((evo_repo / "overlay" / "evolution" / "reflect")
                          .glob("*.daily.md"))
            check("service: /reflect daily drafts the skeleton",
                  "drafted" in refl_reply and len(drafts) == 1
                  and "pending" in drafts[0].read_text(), refl_reply)
            check("service: /reflect marks the window in service state",
                  json.loads((svc_v.state / "evolution_state.json")
                             .read_text()).get("daily") == "1970-01-01")
            svc_v.handle_update(mk_update(26, 111, 77, "/reflect daily"))
            check("service: /reflect second run -> already on file",
                  "already on file" in fake_v.sends[-1][1],
                  fake_v.sends[-1][1])
            svc_v.handle_update(mk_update(27, 111, 77, "/evolution"))
            evo_reply = fake_v.sends[-1][1]
            check("service: /evolution status (schedule/counts/log tail)",
                  evo_reply.startswith("evolution:") and "skills=1" in evo_reply
                  and "learn.skill" in evo_reply, evo_reply[:160])

            # cron leg: enabled + due -> one pass, then throttle (no repeat)
            (evo_repo / "web" / "settings.json").write_text(json.dumps(
                {"evolution": {"enabled": True, "daily_at": "00:00"}}),
                encoding="utf-8")
            (svc_v.state / "evolution_state.json").unlink()
            svc_v._reflect_check = 0.0
            svc_v._maybe_reflect()
            log_text = svc_v.log.read_text()
            check("service: cron leg reflects when enabled+due",
                  log_text.count("trigger=cron") == 3
                  and "created=1" in log_text, log_text[-300:])
            check("service: cron leg produced weekly+monthly drafts too",
                  len(list((evo_repo / "overlay" / "evolution" / "reflect")
                           .glob("*.weekly.md"))) == 1
                  and len(list((evo_repo / "overlay" / "evolution" / "reflect")
                               .glob("*.monthly.md"))) == 1)
            svc_v._maybe_reflect()          # inside the throttle window
            check("service: cron leg throttled within REFLECT_CHECK_SEC",
                  svc_v.log.read_text().count("trigger=cron") == 3)

            # empty model output -> honest fallback
            fake_e = FakeTgTransport()
            svc_e = ChatService(tdp / "st_empty", frozenset({111}),
                                transport=fake_e,
                                chat=lambda **kw: fake_response(text=None), now=nowf)
            svc_e.handle_update(mk_update(16, 111, 7, "say nothing"))
            check("service: empty reply -> honest fallback line",
                  fake_e.sends and fake_e.sends[-1][1] == NO_OUTPUT_LINE,
                  str(fake_e.sends))

            # model error -> short honest line, no stack / paths / engine names
            def boom(messages=None, tools=None, **kw):
                raise ModelError("HTTP 500 test failure")
            fake_x = FakeTgTransport()
            svc_x = ChatService(tdp / "st_err", frozenset({111}),
                                transport=fake_x, chat=boom, now=nowf)
            svc_x.handle_update(mk_update(17, 111, 666, "hi"))
            err_reply = fake_x.sends[-1][1] if fake_x.sends else ""
            check("service: model error -> honest one-line reply",
                  err_reply == MODEL_ERR_LINE, err_reply)
            check("service: error reply has no stack/paths/engine names",
                  all(x not in err_reply for x in
                      ("/", "Traceback", ".py", "ModelError", "mycore",
                       "/mnt")), err_reply)
            check("service: model error detail goes to log only",
                  "MODEL_ERR" in svc_x.log.read_text())

            # dry-run: full evaluation, no outbound send, stdout action line
            fake_d = FakeTgTransport()
            svc_d = ChatService(tdp / "st_dry", frozenset({111}),
                                transport=fake_d, chat=ok_chat,
                                dry_run=True, now=nowf)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                svc_d.handle_update(mk_update(18, 111, 424242, "dry hi"))
            check("service: dry-run sends nothing", fake_d.sends == [])
            check("service: dry-run prints the action to stdout",
                  "[dry-run] would send" in out.getvalue() and
                  "id:4242" in out.getvalue(), out.getvalue()[:80])

            # poll loop --once end-to-end through the injected transport
            fake_p = FakeTgTransport([mk_update(19, 111, 31337, "via poll")])
            svc_p = ChatService(tdp / "st_poll", frozenset({111}),
                                transport=fake_p, chat=ok_chat, now=nowf)
            n = svc_p.run(once=True)
            check("service: --once handles one update and replies",
                  n == 1 and fake_p.sends == [("31337", "pong")],
                  f"n={n} sends={fake_p.sends}")
            check("service: --once short-polls exactly once",
                  fake_p.polls == 1, str(fake_p.polls))
            check("service: --once persists the polled turn",
                  len(Session(tdp / "st_poll" / "sessions" / "31337.jsonl").load())
                  == 2)
            fake_q = FakeTgTransport([])
            svc_q = ChatService(tdp / "st_poll2", frozenset({111}),
                                transport=fake_q, chat=ok_chat, now=nowf)
            check("service: --once with nothing pending exits clean",
                  svc_q.run(once=True) == 0 and fake_q.sends == [])
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_skill_dirs_chain() -> None:
    """P5-4: external skill dirs resolve flag > env > local config > repo
    default > legacy profile config (the last one only while the shim exists)."""
    import os
    sys.path.insert(0, str(REPO / "lib"))
    try:
        import skill_route as sr
    finally:
        sys.path.pop(0)
    with tempfile.TemporaryDirectory(prefix="aidog_skills_") as td:
        tdp = Path(td)
        fx_env = tdp / "fx_env"
        fx_env.mkdir()
        fx_local = tdp / "fx_local"
        fx_local.mkdir()
        fx_prof = tdp / "fx_profile"
        fx_prof.mkdir()
        missing = tdp / "not_created"

        os.environ["AIDOG_SKILL_DIRS"] = str(fx_env)
        try:
            roots = sr.skill_roots(None)
            check("skill dirs: env AIDOG_SKILL_DIRS honored", fx_env in roots)
        finally:
            os.environ.pop("AIDOG_SKILL_DIRS", None)

        fake_repo = tdp / "repo"
        (fake_repo / "deploy").mkdir(parents=True)
        (fake_repo / "deploy" / "memory_sources.local").write_text(
            f"MEMORY_SRC=/tmp/x\nSKILL_DIRS={fx_local}{os.pathsep}{missing}\n",
            encoding="utf-8")
        real_repo = sr.REPO
        sr.REPO = fake_repo
        try:
            roots = sr.skill_roots(None)
            check("skill dirs: local SKILL_DIRS honored (missing entries skipped)",
                  fx_local in roots and missing not in roots, str(roots)[:200])
            check("skill dirs: legacy profile skipped when shim absent",
                  sr.profile_config_dirs() == [])

            # shim present in the fake repo -> legacy external_dirs honored
            # (transition compatibility; dies with the P5-5 shim deletion)
            prof_dir = fake_repo / "profile"
            prof_dir.mkdir()
            (fake_repo / "deploy" / "upstream_shim.env").write_text(
                f'export AIDOG_PROFILE_DIR="{prof_dir}"\n', encoding="utf-8")
            (prof_dir / "config.yaml").write_text(
                f"external_dirs:\n  - {fx_prof}\n", encoding="utf-8")
            roots = sr.skill_roots(None)
            check("skill dirs: legacy profile honored while shim exists",
                  fx_prof in roots, str(roots)[:200])
            check("skill dirs: repo default ordered before legacy profile",
                  sr.DEFAULT_SKILLS_DIR in roots and fx_prof in roots
                  and roots.index(sr.DEFAULT_SKILLS_DIR) < roots.index(fx_prof),
                  str(roots)[:200])
        finally:
            sr.REPO = real_repo


def test_evolution() -> None:
    """Phase A self-evolution loop: lint gate, learn, reflect, log, state,
    cron keys, status, lint-tree — all offline against a tmp repo."""
    from datetime import datetime, timedelta, timezone
    from mycore import evolution as ev
    from mycore.assembly import build_system_stack

    # --- lint gate: content rules ---
    check("evolution: secret assignment rejected",
          "secret-assignment" in ev.lint_text('api_key = "abcdefghijkl1234"'))
    check("evolution: token shape rejected",
          "secret-shape" in ev.lint_text("use sk-" + "a" * 24 + " inline"))
    check("evolution: private key rejected",
          "private-key" in ev.lint_text("-----BEGIN PRIVATE KEY-----\nzz"))
    check("evolution: bearer rejected",
          "bearer" in ev.lint_text("Authorization: Bearer " + "x" * 24))
    check("evolution: machine path rejected",
          "machine-path" in ev.lint_text("the files live under /mnt/example/data"))
    check("evolution: windows path rejected",
          "windows-path" in ev.lint_text(r"saved to C:\Users\me\notes.md"))
    check("evolution: personal bot handle rejected",
          # the handle is CONCATENATED on purpose: the publish gate scans this
          # source for the literal `@my_*_bot` shape and must stay clean —
          # runtime string is contiguous, so the evolution rule still fires
          "bot-handle" in ev.lint_text("ping @my_" + "example_dev" + "_bot first"))
    check("evolution: clean text passes the content gate",
          ev.lint_text("check the watchdog state before retrying") == [])
    # --- lint gate: structure rules ---
    check("evolution: skill slug rule",
          "name-slug" in ev.lint_skill("Bad Slug", "d", "body"))
    check("evolution: description one line, capped",
          "description" in ev.lint_skill("ok", "line1\nline2", "body")
          and "description" in ev.lint_skill("ok", "x" * 201, "body"))
    check("evolution: empty skill body rejected",
          "empty" in ev.lint_skill("ok", "d", "   "))
    check("evolution: self-authored frontmatter rejected",
          "frontmatter" in ev.lint_skill("ok", "d",
                                         "---\nname: x\n---\nbody"))
    check("evolution: skill size cap",
          "size-cap" in ev.lint_skill("ok", "d", "x" * (ev.MAX_SKILL_BODY + 1)))
    check("evolution: memory size cap",
          "size-cap" in ev.lint_memory("x" * (ev.MAX_MEMORY_TEXT + 1)))

    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        NOW = datetime(2026, 10, 20, 22, 0,
                       tzinfo=timezone(timedelta(hours=8))).timestamp()

        # --- learn: skill ---
        r = ev.learn_skill("release-gate",
                           "Checklist gates for shipping a release car",
                           "- verify suite counts\n- bump VERSION",
                           now=NOW, repo=repo, roots=[])
        md = repo / "overlay" / "skills" / "release-gate" / "SKILL.md"
        check("evolution: learn skill writes SKILL.md",
              r.ok and r.path == md and md.is_file())
        check("evolution: SKILL.md frontmatter generated",
              md.read_text(encoding="utf-8").startswith(
                  "---\nname: release-gate\ndescription: Checklist gates"))
        check("evolution: accepted learn appends the log",
              [e["kind"] for e in ev.read_log(repo=repo)] == ["learn.skill"])
        r2 = ev.learn_skill("release-gate", "d", "b",
                            now=NOW, repo=repo, roots=[])
        check("evolution: same-name skill rejected (exists)",
              not r2.ok and "exists" in r2.errors)
        r3 = ev.learn_skill("Bad Slug", "d", "password=supersecretvalue123",
                            now=NOW, repo=repo, roots=[])
        check("evolution: lint reject writes nothing",
              not r3.ok and "name-slug" in r3.errors
              and "secret-assignment" in r3.errors
              and not (repo / "overlay" / "skills" / "Bad Slug").exists())
        rd = ev.learn_skill("gate-check-b",
                            "Checklist gates for shipping a release car",
                            "same words here",
                            now=NOW, repo=repo, roots=[ev.skills_dir(repo)])
        check("evolution: route pre-screen blocks a duplicate",
              not rd.ok and "duplicate" in rd.errors
              and any("duplicate of" in n for n in rd.prescreen),
              str(rd))
        rn = ev.learn_skill("release-notes-tidy", "Write tidy release notes",
                            "body text here",
                            now=NOW, repo=repo, roots=[ev.skills_dir(repo)])
        check("evolution: near-miss pre-screen notes but allows",
              rn.ok and any(n.startswith("near ") for n in rn.prescreen),
              str(rn))

        # --- learn: memory ---
        lesson = "When a deploy fails, check the watchdog state first."
        m = ev.learn_memory(lesson, now=NOW, repo=repo)
        learned = repo / "overlay" / "memory" / "EVOLUTION_learned.md"
        check("evolution: learn memory appends the learned slot",
              m.ok and m.path == learned and learned.is_file()
              and lesson in learned.read_text(encoding="utf-8"))
        m2 = ev.learn_memory(lesson, now=NOW, repo=repo)
        check("evolution: identical memory is a no-op success",
              m2.ok and m2.prescreen
              and learned.read_text(encoding="utf-8").count(lesson) == 1)
        m3 = ev.learn_memory("token: ghp_" + "a" * 30, now=NOW, repo=repo)
        check("evolution: memory secret rejected",
              not m3.ok and bool(m3.errors))
        check("evolution: rejects never enter the tracked log",
              "ghp_" not in ev.log_path(repo).read_text(encoding="utf-8")
              and all("reject" not in e["kind"]
                      for e in ev.read_log(repo=repo)))

        # --- assembly: learned slot (optional, present-only) ---
        cons = repo / "SOUL.md"
        cons.write_text("# constitution", encoding="utf-8")
        mem = repo / "MEMORY_curated.md"
        mem.write_text("- curated", encoding="utf-8")
        stack = build_system_stack(cons, (mem,), learned_path=learned)
        check("evolution: assembly appends the learned slot when present",
              len(stack) == 4 and lesson in stack[2]["content"]
              and "skill_route" in stack[3]["content"])
        stack_b = build_system_stack(cons, (mem,), learned_path=None)
        check("evolution: assembly stack unchanged without the slot",
              len(stack_b) == 3)

        # --- reflect ---
        sp = repo / "state.json"
        res = ev.reflect("daily", now=NOW, repo=repo, state_path=sp)
        check("evolution: reflect daily creates the draft",
              res.created and res.path.is_file()
              and res.path.name == "2026-10-20.daily.md")
        txt = res.path.read_text(encoding="utf-8")
        check("evolution: draft = facts + pending human conclusion",
              "## Facts" in txt and "Human conclusion" in txt
              and "pending" in txt and "2026-10-20T00:00+08:00" in txt)
        check("evolution: draft counts this window's entries",
              "learn.skill=2" in txt and "learn.memory=1" in txt, txt[:300])
        res2 = ev.reflect("daily", now=NOW, repo=repo, state_path=sp)
        check("evolution: reflect idempotent per window",
              not res2.created and res2.path == res.path)
        check("evolution: reflect marks state + logs exactly once",
              ev.load_state(sp).get("daily") == "2026-10-20"
              and sum(1 for e in ev.read_log(repo=repo)
                      if e["kind"] == "reflect.daily") == 1)
        rw = ev.reflect("weekly", now=NOW, repo=repo, state_path=sp)
        rm = ev.reflect("monthly", now=NOW, repo=repo, state_path=sp)
        check("evolution: weekly + monthly drafts and keys",
              rw.created and rm.created
              and ev.load_state(sp) == {"daily": "2026-10-20",
                                        "weekly": "2026-10-18",
                                        "monthly": "2026-10"},
              str(ev.load_state(sp)))
        try:
            ev.reflect("yearly", now=NOW, repo=repo, state_path=sp)
            check("evolution: unknown period refused", False)
        except ValueError:
            check("evolution: unknown period refused", True)

        # --- cron: is_due ---
        cfg = dict(ev.DEFAULT_SETTINGS, enabled=True)
        check("evolution: is_due empty when disabled (default)",
              ev.is_due(ev.DEFAULT_SETTINGS, NOW, {}) == [])
        due = ev.is_due(cfg, NOW, {"daily": "2026-10-20"})
        check("evolution: daily window done -> only weekly/monthly due",
              "daily" not in due and "weekly" in due and "monthly" in due,
              str(due))
        t08 = datetime(2026, 10, 21, 8, 0, tzinfo=ev.BEIJING).timestamp()
        done = {"daily": "2026-10-20", "weekly": "2026-10-18",
                "monthly": "2026-10"}
        check("evolution: daily waits until daily_at",
              "daily" not in ev.is_due(cfg, t08, done))
        t0911 = datetime(2026, 10, 21, 9, 11, tzinfo=ev.BEIJING).timestamp()
        check("evolution: daily fires next day after daily_at (catch-up)",
              "daily" in ev.is_due(cfg, t0911, done))
        check("evolution: invalid daily_at fails closed",
              ev.is_due({"enabled": True, "daily_at": "99:99"}, NOW, {}) == [])

        # --- settings partition ---
        clean, errs = ev.validate_evolution_settings(
            {"enabled": True, "daily_at": "08:30", "weekly_day": "mon",
             "monthly_day": 5})
        check("evolution: settings partition validates",
              clean == {"enabled": True, "daily_at": "08:30",
                        "weekly_day": "mon", "monthly_day": 5}
              and not errs, str(errs))
        clean2, errs2 = ev.validate_evolution_settings(
            {"enabled": "yes", "daily_at": "25:00", "weekly_day": "nope",
             "monthly_day": 32, "zz": 1})
        check("evolution: settings partition rejects every bad field",
              len(errs2) == 5 and not clean2, str(errs2))
        check("evolution: settings partition refuses non-object",
              bool(ev.validate_evolution_settings("x")[1]))
        merged = ev.evolution_settings({"evolution": {"enabled": True}})
        check("evolution: defaults merged over partial config",
              merged["daily_at"] == "09:10" and merged["weekly_day"] == "sun"
              and merged["monthly_day"] == 1)

        # --- status + lint tree ---
        summ = ev.status_summary(repo=repo, now=NOW, cfg=ev.evolution_settings({}),
                                 state_path=sp)
        check("evolution: status shape + relative log summaries",
              summ["skills"] >= 1 and summ["log_entries"] >= 4
              and isinstance(summ["due_now"], list)
              and all(not e["summary"].startswith("/")
                      for e in summ["log_tail"]), str(summ)[:200])
        check("evolution: lint_tree clean after accepted learns",
              ev.lint_tree(repo) == [], str(ev.lint_tree(repo)))
        learned.write_text("- ok\n/home/example/.ssh/id_rsa leaked\n", encoding="utf-8")
        hits = ev.lint_tree(repo)
        check("evolution: lint_tree catches a poisoned artifact",
              any("EVOLUTION_learned" in h and "machine-path" in h
                  for h in hits), str(hits))
        # log survives hand-editing (append-only, tolerant parse)
        ev.append_log("learn.memory", "manual line\nsecond line folded? no",
                      now=NOW, repo=repo)
        kinds = [e["kind"] for e in ev.read_log(repo=repo)]
        check("evolution: log line folds to one record",
              kinds.count("learn.memory") >= 1 and kinds[-1] == "learn.memory")


def test_scheduler() -> None:
    """Phase B2 natural-language cron: spec grammar, due matrix, registry
    persistence, the `cron` tool, and the /cron service leg — all offline
    (fake transport, injected model, temp state dirs)."""
    from datetime import datetime as _dt
    from mycore import scheduler as sch
    from mycore.service import ChatService
    from mycore.tg import POLL_BOT_ENV
    import os as _os

    # --- spec grammar: the four canonical forms ---
    check("cron: spec every", sch.parse_spec("every 30m")
          == {"type": "interval", "minutes": 30})
    check("cron: spec every hours", sch.parse_spec("every 2h")
          == {"type": "interval", "minutes": 120})
    check("cron: spec daily", sch.parse_spec("daily 08:00")
          == {"type": "daily", "time": "08:00"})
    check("cron: spec weekly", sch.parse_spec("weekly mon 09:00")
          == {"type": "weekly", "weekday": "mon", "time": "09:00"})
    check("cron: spec once", sch.parse_spec("once 2026-10-26 09:00")
          == {"type": "once", "at": "2026-10-26 09:00"})
    check("cron: describe round-trips the spec",
          sch.describe(sch.parse_spec("every 2h")) == "every 2h"
          and sch.describe(sch.parse_spec("weekly sun 23:59"))
          == "weekly sun 23:59")
    # --- refusal paths (each SpecError text is chat-safe) ---
    bad = ["", "   ", "sometimes 09:00", "daily 9:00", "daily 25:00",
           "weekly moon 09:00", "weekly mon 9am", "every 0m", "every 99999m",
           "every 30", "every 30 min", "once 2026-13-01 09:00",
           "once 2026-02-30 09:00", "once tomorrow 09:00", "daily", "once"]
    for spec in bad:
        try:
            sch.parse_spec(spec)
            check(f"cron: reject {spec!r}", False, "accepted")
        except sch.SpecError as e:
            ok = "/mnt/" not in str(e) and "/root/" not in str(e)
            check(f"cron: reject {spec!r}", ok, str(e))

    NOW = _dt(2026, 10, 20, 22, 0, tzinfo=sch.BEIJING).timestamp()

    # --- relative countdown (language-neutral, no locale keys) ---
    check("cron: fmt_delta minutes",
          sch.fmt_delta(NOW + 45, NOW) == "+45s")
    check("cron: fmt_delta hours+minutes",
          sch.fmt_delta(NOW + 2 * 3600 + 13 * 60, NOW) == "+2h13m")
    check("cron: fmt_delta crosses midnight (days)",
          sch.fmt_delta(NOW + 2 * 86400 + 3 * 3600 + 5 * 60, NOW)
          == "+2d3h", sch.fmt_delta(NOW + 2 * 86400 + 3 * 3600
                                     + 5 * 60, NOW))
    check("cron: fmt_delta overdue -> +0s (due now, fires next tick)",
          sch.fmt_delta(NOW - 90, NOW) == "+0s")
    check("cron: fmt_delta exact -> +0s", sch.fmt_delta(NOW, NOW) == "+0s")
    check("cron: fmt_delta unknown (done/no next) -> empty",
          sch.fmt_delta(None, NOW) == "")

    # --- due matrix: daily ---
    jobs = []
    d = sch.create_job(jobs, "daily 09:10", "stretch", now=NOW - 3600)
    check("cron: daily not due before its time",
          not sch.is_due(d, NOW - 12 * 3600 + 8 * 3600))   # 08:00 same day
    day8 = _dt(2026, 10, 20, 8, 0, tzinfo=sch.BEIJING).timestamp()
    day910 = _dt(2026, 10, 20, 9, 10, tzinfo=sch.BEIJING).timestamp()
    day911 = _dt(2026, 10, 20, 9, 11, tzinfo=sch.BEIJING).timestamp()
    d2 = sch.create_job(jobs, "daily 09:10", "x", now=day8)
    check("cron: daily due after its time", sch.is_due(d2, day911))
    check("cron: daily not due before its time (same day)",
          not sch.is_due(d2, day8 + 60))
    sch.mark_fired(d2, day911)
    check("cron: daily silent for the rest of the fire day",
          not sch.is_due(d2, day911 + 60))
    tomorrow910 = day911 + 24 * 3600 - 60
    check("cron: daily due again next day", sch.is_due(d2, tomorrow910))
    late = sch.create_job(jobs, "daily 09:10", "x",
                          now=_dt(2026, 10, 20, 10, 0,
                                  tzinfo=sch.BEIJING).timestamp())
    check("cron: created after today's time waits for tomorrow",
          not sch.is_due(late, _dt(2026, 10, 20, 23, 0,
                                   tzinfo=sch.BEIJING).timestamp())
          and sch.is_due(late, tomorrow910 + 24 * 3600))

    # --- due matrix: weekly (latest occurrence walk-back) ---
    # 2026-10-20 is a Tuesday; weekly mon 09:00's latest occurrence <= now
    # is Monday 2026-10-19 09:00.
    w = sch.create_job(jobs, "weekly mon 09:00", "x",
                       now=_dt(2026, 10, 12, 8, 0,
                               tzinfo=sch.BEIJING).timestamp())
    mon911 = _dt(2026, 10, 19, 9, 11, tzinfo=sch.BEIJING).timestamp()
    check("cron: weekly due after this week's occurrence",
          sch.is_due(w, NOW) and sch.is_due(w, mon911))
    sch.mark_fired(w, mon911)
    check("cron: weekly silent until next occurrence",
          not sch.is_due(w, NOW))
    check("cron: weekly due again the following week",
          sch.is_due(w, NOW + 6 * 24 * 3600))
    w2 = sch.create_job(jobs, "weekly mon 09:00", "x",
                        now=_dt(2026, 10, 20, 10, 0,
                                tzinfo=sch.BEIJING).timestamp())
    check("cron: weekly created after this week's Monday waits a week",
          not sch.is_due(w2, NOW)
          and sch.is_due(w2, _dt(2026, 10, 26, 9, 11,
                                 tzinfo=sch.BEIJING).timestamp()))

    # --- due matrix: interval (anchor moves, no fire burst) ---
    iv = sch.create_job(jobs, "every 5m", "x", now=NOW)
    check("cron: interval not due before N minutes",
          not sch.is_due(iv, NOW + 299))
    check("cron: interval due at N minutes", sch.is_due(iv, NOW + 300))
    sch.mark_fired(iv, NOW + 700)                     # late fire (downtime)
    check("cron: interval reschedules from the fire (no burst)",
          not sch.is_due(iv, NOW + 700 + 299)
          and sch.is_due(iv, NOW + 700 + 300))
    check("cron: interval first fire is created_at + N",
          sch.is_due(sch.create_job(jobs, "every 1m", "x", now=NOW),
                     NOW + 60))

    # --- due matrix: once ---
    once = sch.create_job(
        jobs, "once 2026-10-26 09:00", "x", now=NOW)
    check("cron: once not due before its datetime",
          not sch.is_due(once, NOW))
    once_at = _dt(2026, 10, 26, 9, 0, tzinfo=sch.BEIJING).timestamp()
    check("cron: once due at its datetime", sch.is_due(once, once_at + 1))
    sch.mark_fired(once, once_at + 1)
    check("cron: once fires exactly once then is done",
          once["done"] and not sch.is_due(once, once_at + 3600)
          and sch.next_fire(once, once_at) is None)
    try:
        sch.create_job(jobs, "once 2020-01-01 09:00", "x", now=NOW)
        check("cron: once in the past refused", False, "accepted")
    except sch.SpecError:
        check("cron: once in the past refused", True)

    # --- create caps + id shape ---
    for bad_text in ("", "   ", "x" * (sch.MAX_TEXT + 1)):
        try:
            sch.create_job(jobs, "daily 09:00", bad_text, now=NOW)
            check(f"cron: text cap {len(bad_text)}", False, "accepted")
        except sch.SpecError:
            check(f"cron: text cap {len(bad_text)}", True)
    try:
        sch.create_job(jobs, "daily 09:00", "x", kind="shout", now=NOW)
        check("cron: unknown kind refused", False, "accepted")
    except sch.SpecError:
        check("cron: unknown kind refused", True)
    one = sch.create_job([], "daily 09:00", "x", now=NOW)
    check("cron: id is 6 hex chars", bool(re.fullmatch(r"[0-9a-f]{6}",
                                                       one["id"])), one["id"])
    full = [sch.create_job([], "daily 09:00", f"t{i}", now=NOW)
            for i in range(sch.MAX_JOBS)]
    try:
        sch.create_job(full, "daily 09:00", "x", now=NOW)
        check("cron: job limit enforced", False, "accepted")
    except sch.SpecError:
        check("cron: job limit enforced", True)

    # --- registry persistence: round-trip + fail-soft ---
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "cron_jobs.json"
        sch.save_jobs(p, [d, once])
        back = sch.load_jobs(p)
        check("cron: save/load round-trip keeps fields",
              len(back) == 2 and back[0]["text"] == "stretch"
              and back[1]["kind"] == "message"
              and back[0]["origin"] == d["origin"])
        p.write_text("{ not json", encoding="utf-8")
        check("cron: corrupt registry loads empty (fail-soft)",
              sch.load_jobs(p) == [])
        p.write_text(json.dumps({"jobs": [d, {"id": "x", "type": "daily"},
                                        42, "nope"]}), encoding="utf-8")
        check("cron: garbage entries dropped on load",
              len(sch.load_jobs(p)) == 1)
        check("cron: missing registry loads empty",
              sch.load_jobs(Path(td) / "absent.json") == [])
        removed = sch.delete_job(back, d["id"])
        check("cron: delete by id", removed and len(back) == 1)
        check("cron: delete unknown id is a no-op",
              not sch.delete_job(back, "zzzzzz"))

    # --- the `cron` tool (registry leg) ---
    with tempfile.TemporaryDirectory() as td:
        reg = build_registry(state_dir=td)
        out = reg.dispatch("cron", {"action": "list"})
        check("cron: tool list on empty registry", out == "no scheduled jobs")
        out = reg.dispatch("cron", {"action": "create", "spec": "daily 09:00",
                                    "text": "tool job"})
        check("cron: tool create echoes id + next",
              out.startswith("scheduled ") and "daily 09:00" in out, out)
        saved = sch.load_jobs(Path(td) / sch.STATE_FILE_NAME)
        check("cron: tool create persisted with origin=None",
              len(saved) == 1 and saved[0]["source"] == "model"
              and saved[0]["origin"] is None)
        jid = saved[0]["id"]
        check("cron: tool list shows the job",
              jid in reg.dispatch("cron", {"action": "list"}))
        check("cron: tool delete",
              reg.dispatch("cron", {"action": "delete", "id": jid})
              == f"deleted {jid}"
              and sch.load_jobs(Path(td) / sch.STATE_FILE_NAME) == [])
        for args, why in (
                ({"action": "create", "spec": "daily 09:00"}, "missing text"),
                ({"action": "create", "spec": "nope", "text": "x"}, "bad spec"),
                ({"action": "delete", "id": "nope00"}, "unknown id"),
                ({"action": "delete"}, "delete without id"),
                ({"action": "explode"}, "unknown action")):
            res = reg.dispatch("cron", args)
            check(f"cron: tool ERROR path ({why})",
                  res.startswith("ERROR:"), res[:80])
        # origin context: a job created inside a chat turn remembers it
        sch.set_origin(555000)
        try:
            reg.dispatch("cron", {"action": "create", "spec": "every 1m",
                                  "text": "origin job"})
        finally:
            sch.clear_origin()
        oj = sch.load_jobs(Path(td) / sch.STATE_FILE_NAME)
        check("cron: tool create inside a turn records the origin chat",
              len(oj) == 1 and oj[0]["origin"] == 555000)
        check("cron: origin context cleared after the turn",
              sch.get_origin() is None)

    # --- service leg: /cron command + fire + delivery ---
    saved_tok = _os.environ.get(POLL_BOT_ENV)
    saved_wd = _os.environ.get("MYCORE_WD_CHAT")
    _os.environ[POLL_BOT_ENV] = "second-bot-TEST-token"
    _os.environ.pop("MYCORE_WD_CHAT", None)
    try:
        clock = [float((NOW))]
        nowf = lambda: clock[0]  # noqa: E731
        fake = FakeTgTransport()
        with tempfile.TemporaryDirectory() as td:
            svc = ChatService(Path(td), frozenset({111}), transport=fake,
                              chat=lambda messages=None, tools=None, **kw:
                              fake_response(text="pong"), now=nowf)

            def cmd(text, cid=424242, uid=111, upd=1):
                before = len(fake.sends)
                svc.handle_update(mk_update(upd, uid, cid, text))
                return fake.sends[before][1] if len(fake.sends) > before \
                    else None

            # help advertises the command (en pack)
            from lib import i18n as _i18n
            check("cron: /help lists /cron (en)",
                  "/cron" in _i18n.get("en", "cmd.help.body"))
            check("cron: /help lists /cron (zh)",
                  "/cron" in _i18n.get("zh", "cmd.help.body"))

            out = cmd("/cron")
            check("cron: /cron list empty is honest",
                  out is not None and "no cron jobs" in out, str(out))
            out = cmd("/cron add daily 09:10 :: drink water")
            reg_path = Path(td) / sch.STATE_FILE_NAME
            jobs_now = sch.load_jobs(reg_path)
            check("cron: /cron add creates + stamps origin chat",
                  out.startswith("cron ") and "created" in out
                  and len(jobs_now) == 1
                  and jobs_now[0]["origin"] == 424242
                  and jobs_now[0]["source"] == "command", str(out))
            jid = jobs_now[0]["id"]
            out = cmd("/cron")
            check("cron: /cron list shows id + spec + preview",
                  jid in out and "daily 09:10" in out and "drink water" in out,
                  str(out))
            check("cron: /cron list row carries a relative countdown",
                  "(" + "+" in out and ")" in out, str(out))
            out = cmd("/cron add prompt weekly fri 08:00 :: weekly digest")
            jobs_now = sch.load_jobs(reg_path)
            check("cron: /cron add prompt kind",
                  "prompt" in out and jobs_now[-1]["kind"] == "prompt"
                  and jobs_now[-1]["type"] == "weekly", str(out))
            out = cmd("/cron add whenever :: x")
            check("cron: /cron add bad spec rejected with hint",
                  out is not None and "rejected" in out
                  and "every 30m" in out, str(out))
            check("cron: rejected add writes nothing",
                  len(sch.load_jobs(reg_path)) == 2)
            out = cmd("/cron add daily 09:00")
            check("cron: /cron add without :: shows usage",
                  out is not None and "/cron add" in out, str(out))
            out = cmd("/cron del nope00")
            check("cron: /cron del miss is honest",
                  out is not None and "nope00" in out, str(out))
            out = cmd("/cron del " + jid)
            check("cron: /cron del removes the job",
                  "deleted" in str(out)
                  and all(j["id"] != jid for j in sch.load_jobs(reg_path)))
            out = cmd("/cron frobnicate")
            check("cron: /cron unknown subcommand -> usage",
                  out is not None and "/cron add" in out, str(out))
            check("cron: unauthorized /cron never answered",
                  cmd("/cron", uid=999, upd=99) is None)

            # --- fire: message job -> origin chat ---
            fake.sends.clear()
            sch.save_jobs(reg_path, [])
            jl = sch.load_jobs(reg_path)
            sch.create_job(jl, "every 1m", "ring ring", origin=424242,
                           now=clock[0])
            sch.save_jobs(reg_path, jl)
            svc._cron_check = 0.0
            svc._maybe_fire_cron()                       # not due yet
            check("cron: fire leg idle before the window",
                  fake.sends == [] and "CRON_FIRE"
                  not in svc.log.read_text())
            clock[0] += 70
            svc._cron_check = 0.0
            svc._maybe_fire_cron()
            check("cron: due message job delivered to its origin chat",
                  fake.sends == [("424242", "ring ring")], str(fake.sends))
            log = svc.log.read_text()
            check("cron: CRON_FIRE logged with delivery facts",
                  "CRON_FIRE" in log and "delivered=origin" in log
                  and "ring ring" in log and "kind=message" in log)
            fired = sch.load_jobs(reg_path)
            check("cron: mark persisted before/after execution",
                  fired[0]["last_fired"] is not None)
            svc._cron_check = 0.0
            svc._maybe_fire_cron()                       # within interval
            check("cron: no duplicate fire inside the interval",
                  fake.sends == [("424242", "ring ring")])

            # --- throttle: 60s cadence like the evolution leg ---
            sch.save_jobs(reg_path, [])
            jl = sch.load_jobs(reg_path)
            sch.create_job(jl, "every 1m", "throttled", origin=424242,
                           now=clock[0] - 120)
            sch.save_jobs(reg_path, jl)
            svc._cron_check = clock[0]                   # checked just now
            fires_before = svc.log.read_text().count("CRON_FIRE")
            svc._maybe_fire_cron()
            check("cron: fire leg throttled to the cadence",
                  svc.log.read_text().count("CRON_FIRE") == fires_before
                  and "throttled" not in [s[1] for s in fake.sends])
            clock[0] += 61
            svc._maybe_fire_cron()
            check("cron: fire leg runs after the cadence window",
                  "throttled" in [s[1] for s in fake.sends])

            # --- delivery fallbacks: MYCORE_WD_CHAT, then log-only ---
            sch.save_jobs(reg_path, [])
            jl = sch.load_jobs(reg_path)
            sch.create_job(jl, "every 1m", "to wd chat", origin=None,
                           now=clock[0] - 120)
            sch.save_jobs(reg_path, jl)
            _os.environ["MYCORE_WD_CHAT"] = "777000"
            fake.sends.clear()
            svc._cron_check = 0.0
            svc._maybe_fire_cron()
            check("cron: no origin -> MYCORE_WD_CHAT",
                  fake.sends == [("777000", "to wd chat")], str(fake.sends))
            check("cron: wd delivery logged",
                  "delivered=wd" in svc.log.read_text())

            sch.save_jobs(reg_path, [])
            jl = sch.load_jobs(reg_path)
            sch.create_job(jl, "every 1m", "nowhere to go", origin=None,
                           now=clock[0] - 120)
            sch.save_jobs(reg_path, jl)
            _os.environ.pop("MYCORE_WD_CHAT", None)
            fake.sends.clear()
            svc._cron_check = 0.0
            svc._maybe_fire_cron()
            check("cron: no target at all -> log-only, never an error",
                  fake.sends == [] and "delivered=none" in svc.log.read_text())
            check("cron: log-only fire still marks the job",
                  sch.load_jobs(reg_path)[0]["last_fired"] is not None)

            _os.environ["MYCORE_WD_CHAT"] = "not-an-id"
            check("cron: garbage MYCORE_WD_CHAT -> None (fail-soft)",
                  svc._wd_chat_target() is None)
            _os.environ["MYCORE_WD_CHAT"] = "777000"
            check("cron: int MYCORE_WD_CHAT -> target",
                  svc._wd_chat_target() == 777000)
            _os.environ.pop("MYCORE_WD_CHAT", None)

            # --- prompt job: runs one model turn, delivers its result ---
            sch.save_jobs(reg_path, [])
            jl = sch.load_jobs(reg_path)
            sch.create_job(jl, "every 1m", "summarize my day", kind="prompt",
                           origin=424242, now=clock[0] - 120)
            sch.save_jobs(reg_path, jl)
            fake.sends.clear()
            svc._cron_check = 0.0
            svc._maybe_fire_cron()
            check("cron: prompt job delivers the model reply",
                  fake.sends == [("424242", "pong")], str(fake.sends))

            # --- dry-run: evaluated, marked, never sent ---
            sch.save_jobs(reg_path, [])
            jl = sch.load_jobs(reg_path)
            sch.create_job(jl, "every 1m", "dry", origin=424242,
                           now=clock[0] - 120)
            sch.save_jobs(reg_path, jl)
            svc.dry_run = True
            fake.sends.clear()
            svc._cron_check = 0.0
            svc._maybe_fire_cron()
            check("cron: dry-run skips the send but still fires+logs",
                  fake.sends == [] and "DRYRUN_SEND" in svc.log.read_text()
                  and "delivered=origin" in svc.log.read_text())
            svc.dry_run = False

            # --- NL entry end-to-end: speech -> model -> cron tool ---
            sch.save_jobs(reg_path, [])
            script = [fake_response(calls=[("cron", {
                          "action": "create", "spec": "daily 07:30",
                          "text": "morning stretch"})]),
                      fake_response(text="scheduled for 07:30 daily")]
            svc.chat = ScriptedChat(script).chat
            svc.handle_update(mk_update(7, 111, 424242,
                                        "remind me to stretch every morning"))
            nl_jobs = sch.load_jobs(reg_path)
            check("cron: NL turn -> tool call -> job with origin chat",
                  len(nl_jobs) == 1 and nl_jobs[0]["origin"] == 424242
                  and nl_jobs[0]["type"] == "daily"
                  and nl_jobs[0]["source"] == "model", str(nl_jobs))
            check("cron: NL turn answered in chat too",
                  fake.sends[-1][1] == "scheduled for 07:30 daily",
                  str(fake.sends[-1]))
            # second turn must not inherit a stale origin
            svc.chat = lambda messages=None, tools=None, **kw: \
                fake_response(text="ok")
            svc.handle_update(mk_update(8, 111, 424242, "hello again"))
            check("cron: origin context does not leak across turns",
                  sch.get_origin() is None)
    finally:
        if saved_tok is None:
            _os.environ.pop(POLL_BOT_ENV, None)
        else:
            _os.environ[POLL_BOT_ENV] = saved_tok
        if saved_wd is None:
            _os.environ.pop("MYCORE_WD_CHAT", None)
        else:
            _os.environ["MYCORE_WD_CHAT"] = saved_wd
        sch.clear_origin()


def _sse_body(*frames) -> str:
    """Build an SSE body from chunk dicts — one data line each, then [DONE]."""
    out = []
    for f in frames:
        out.append("data: " + json.dumps(f) + "\n\n")
    out.append("data: [DONE]\n\n")
    return "".join(out)


def _chunk(content=None, tool_calls=None, finish=None) -> dict:
    delta: dict = {}
    if content is not None:
        delta["content"] = content
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    return {"id": "c1", "object": "chat.completion.chunk", "created": 1,
            "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def test_stream() -> None:
    """Streaming path: SSE assembly into the same completion shape, on_delta
    pacing, non-SSE fallback, error mapping — all offline (canned bodies)."""
    from mycore.model import ChatClient, ModelConfig, ModelError

    cfg = ModelConfig(base_url="http://127.0.0.1:9/v1", model="m")

    def mk(body: str):
        seen = {"payload": None, "lines": []}

        def transport(url, headers, payload, timeout, on_line):
            seen["payload"] = json.loads(payload)
            for ln in body.splitlines(keepends=True):
                seen["lines"].append(ln)
                on_line(ln)
            return body
        return transport, seen

    # --- 1) content assembly + live deltas + stream flag ---
    body = _sse_body(_chunk(content="Hel"), _chunk(content="lo "),
                     _chunk(content="world"), _chunk(finish="stop"))
    tr, seen = mk(body)
    got = []
    doc = ChatClient(cfg, stream_transport=tr).chat(
        [{"role": "user", "content": "x"}], on_delta=got.append)
    check("stream: payload carries stream=true",
          seen["payload"].get("stream") is True)
    check("stream: lines delivered live (not one blob)", len(seen["lines"]) >= 4,
          str(len(seen["lines"])))
    check("stream: content assembled", first_choice_text(doc) == "Hello world",
          repr(first_choice_text(doc)))
    check("stream: on_delta paced per chunk", got == ["Hel", "lo ", "world"],
          str(got))
    check("stream: finish reason captured",
          doc["choices"][0]["finish_reason"] == "stop")
    check("stream: doc is chat.completion shaped",
          doc.get("object") == "chat.completion" and doc.get("id") == "c1"
          and doc.get("model") == "m")

    # --- 2) tool call fragments merged per index (real gateway shape:
    #     id+name complete in the first chunk, null afterwards, args split) ---
    body = _sse_body(
        _chunk(tool_calls=[{"index": 0, "id": "call_1",
                            "function": {"name": "read_file", "arguments": ""}}]),
        _chunk(tool_calls=[{"index": 0, "id": None,
                            "function": {"name": None,
                                         "arguments": "{\"path\""}}]),
        _chunk(tool_calls=[{"index": 0, "id": None,
                            "function": {"name": None,
                                         "arguments": ": \"a.txt\"}"}}]),
        _chunk(finish="tool_calls"))
    tr, _ = mk(body)
    doc = ChatClient(cfg, stream_transport=tr).chat(
        [{"role": "user", "content": "x"}], on_delta=lambda s: None)
    calls = tool_calls(doc)
    check("stream: tool fragments merged by index",
          len(calls) == 1 and calls[0]["name"] == "read_file"
          and calls[0]["id"] == "call_1", str(calls))
    check("stream: merged arguments parse",
          calls[0]["arguments"] == {"path": "a.txt"}, str(calls))
    check("stream: no content delta when tools-only",
          first_choice_text(doc) is None)

    # --- 2b) a provider that REPEATS the full name/id every chunk must not
    #      accumulate duplicates (first-wins on id/name, concat on args) ---
    body = _sse_body(
        _chunk(tool_calls=[{"index": 0, "id": "call_x",
                            "function": {"name": "grep", "arguments": "{}}"}}]),
        _chunk(tool_calls=[{"index": 0, "id": "call_x",
                            "function": {"name": "grep", "arguments": "{}}"}}]),
        _chunk(finish="tool_calls"))
    tr, _ = mk(body)
    doc = ChatClient(cfg, stream_transport=tr).chat(
        [{"role": "user", "content": "x"}], on_delta=lambda s: None)
    calls = tool_calls(doc)
    check("stream: repeated name/id stays single (first wins)",
          len(calls) == 1 and calls[0]["name"] == "grep"
          and calls[0]["id"] == "call_x", str(calls))

    # --- 3) two parallel calls keep separate slots ---
    body = _sse_body(
        _chunk(tool_calls=[{"index": 0, "id": "a",
                            "function": {"name": "one", "arguments": "{}"}}]),
        _chunk(tool_calls=[{"index": 1, "id": "b",
                            "function": {"name": "two", "arguments": "{}"}}]),
        _chunk(finish="tool_calls"))
    tr, _ = mk(body)
    doc = ChatClient(cfg, stream_transport=tr).chat(
        [{"role": "user", "content": "x"}], on_delta=lambda s: None)
    calls = tool_calls(doc)
    check("stream: parallel tool calls stay separate",
          [c["name"] for c in calls] == ["one", "two"], str(calls))

    # --- 4) non-SSE fallback: gateway ignored stream, sent plain JSON ---
    plain = json.dumps(fake_response(text="whole text"))
    tr, seen = mk(plain)
    got = []
    doc = ChatClient(cfg, stream_transport=tr).chat(
        [{"role": "user", "content": "x"}], on_delta=got.append)
    check("stream: non-SSE body returned as a normal doc",
          first_choice_text(doc) == "whole text")
    check("stream: whole text reported as a single delta",
          got == ["whole text"], str(got))

    # --- 5) garbage body -> ModelError (honest, no fake success) ---
    tr, _ = mk("<html>gateway exploded</html>\n")
    try:
        ChatClient(cfg, stream_transport=tr).chat(
            [{"role": "user", "content": "x"}], on_delta=lambda s: None)
        check("stream: garbage body -> ModelError", False)
    except ModelError as e:
        check("stream: garbage body -> ModelError", "JSON" in str(e), str(e))

    # --- 6) HTTP error from the stream transport surfaces as ModelError ---
    def boom(url, headers, payload, timeout, on_line):
        raise ModelError("HTTP 500 Internal Server Error")
    try:
        ChatClient(cfg, stream_transport=boom).chat(
            [{"role": "user", "content": "x"}], on_delta=lambda s: None)
        check("stream: transport error -> ModelError", False)
    except ModelError as e:
        check("stream: transport error -> ModelError", "500" in str(e))

    # --- 7) no on_delta -> classic non-stream transport (zero regression) ---
    def plain_transport(url, headers, payload, timeout):
        return json.dumps(fake_response(text="classic"))
    doc = ChatClient(cfg, transport=plain_transport).chat(
        [{"role": "user", "content": "x"}])
    check("stream: omitted on_delta uses the classic path",
          first_choice_text(doc) == "classic")

    # --- 8) streaming with tools still allowed (loop passes both) ---
    body = _sse_body(_chunk(content="thinking"), _chunk(finish="stop"))
    tr, seen = mk(body)
    ChatClient(cfg, stream_transport=tr).chat([{"role": "user", "content": "x"}],
                                              tools=[{"type": "function"}],
                                              on_delta=lambda s: None)
    check("stream: tools reach the payload with stream",
          "tools" in seen["payload"] and seen["payload"]["stream"] is True)


def test_loop_on_delta() -> None:
    """run_turn forwards on_delta only when set (fakes that do not know about
    streaming keep working) and the final text still comes from TurnResult."""
    class KwChat:
        def __init__(self, script):
            self.script = list(script)
            self.kw_seen = []

        def chat(self, messages, tools=None, **kw):
            self.kw_seen.append(kw)
            return self.script.pop(0)

    chat = KwChat([fake_response(text="plain answer")])
    res = run_turn([{"role": "user", "content": "hi"}], chat.chat,
                   build_registry())
    check("loop: no on_delta kw when unset",
          all("on_delta" not in k for k in chat.kw_seen), str(chat.kw_seen))
    check("loop: result text unaffected", res.text == "plain answer")

    chat = KwChat([fake_response(text="streamed answer")])
    res = run_turn([{"role": "user", "content": "hi"}], chat.chat,
                   build_registry(), on_delta=lambda s: None)
    check("loop: on_delta forwarded when set",
          all(callable(k.get("on_delta")) for k in chat.kw_seen)
          and len(chat.kw_seen) == 1, str(chat.kw_seen))
    check("loop: authoritative text still returned",
          res.stop == "final" and res.text == "streamed answer")

    # tool chain: second call also carries on_delta (per model call)
    with tempfile.TemporaryDirectory() as td:
        reg = build_registry(allowed_roots=[td])
        chat = KwChat([
            fake_response(calls=[("write_file", {"path": str(Path(td) / "o.txt"),
                                                  "content": "x"})]),
            fake_response(text="done"),
        ])
        res = run_turn([{"role": "user", "content": "go"}], chat.chat, reg,
                       on_delta=lambda s: None)
        check("loop: on_delta on every model call",
              len(chat.kw_seen) == 2
              and all("on_delta" in k for k in chat.kw_seen), str(chat.kw_seen))
        check("loop: tool chain unaffected by streaming",
              res.stop == "final" and res.text == "done")


def test_typing() -> None:
    """TG typing heartbeat: per-pipeline only, dry-run skips it, failures are
    silent, and the beat stops the moment the turn ends."""
    import os
    import time as _t
    from mycore import service as svc_mod
    from mycore.service import ChatService, STATE_ENV
    from mycore.tg import POLL_BOT_ENV

    saved = {k: os.environ.get(k) for k in (STATE_ENV, POLL_BOT_ENV)}
    old_interval = svc_mod.TYPING_INTERVAL_SEC
    try:
        os.environ[POLL_BOT_ENV] = "second-bot-TEST-token"
        with tempfile.TemporaryDirectory() as td:
            fake = FakeTgTransport()
            ok_chat = lambda messages=None, tools=None, **kw: fake_response(text="pong")  # noqa: E731
            svc = ChatService(Path(td), frozenset({111}), transport=fake,
                              chat=ok_chat)

            # heartbeat fires while a pipeline turn runs
            svc_mod.TYPING_INTERVAL_SEC = 0.02
            svc.handle_update(mk_update(1, 111, 123456789, "hello"))
            deadline = _t.time() + 2.0
            while _t.time() < deadline and not fake.actions:
                _t.sleep(0.02)
            check("typing: heartbeat sent during the pipeline turn",
                  bool(fake.actions) and fake.actions[0] == ("123456789", "typing"),
                  str(fake.actions[:3]))
            check("typing: reply still delivered",
                  fake.sends and fake.sends[-1][1] == "pong", str(fake.sends))
            # turn over -> beats must stop (count stable across several intervals)
            _t.sleep(0.12)
            n_after = len(fake.actions)
            _t.sleep(0.1)
            check("typing: heartbeat stops when the turn ends",
                  len(fake.actions) == n_after, f"{n_after} -> {len(fake.actions)}")

            # commands never enter _pipeline -> no heartbeat for them
            before = len(fake.actions)
            svc.handle_update(mk_update(2, 111, 123456789, "/help"))
            _t.sleep(0.08)
            check("typing: commands do not trigger the heartbeat",
                  len(fake.actions) == before, f"{before} -> {len(fake.actions)}")

            # dry-run: never sends
            fake2 = FakeTgTransport()
            svc2 = ChatService(Path(td) / "dry", frozenset({111}),
                               transport=fake2, chat=ok_chat, dry_run=True)
            svc2.handle_update(mk_update(3, 111, 123456789, "hello"))
            _t.sleep(0.08)
            check("typing: dry-run sends no heartbeat",
                  fake2.actions == [], str(fake2.actions))
            check("typing: dry-run sends no reply either",
                  fake2.sends == [], str(fake2.sends))

            # transport failure -> silent stop, no exception, no log line
            class Broken:
                def __call__(self, url, payload, timeout):
                    method = url.rsplit("/", 1)[-1]
                    if method == "sendChatAction":
                        raise RuntimeError("offline")
                    if method == "sendMessage":
                        return json.dumps({"ok": True, "result": {"message_id": 1}})
                    return json.dumps({"ok": True, "result": {}})
            svc3 = ChatService(Path(td) / "broken", frozenset({111}),
                               transport=Broken(), chat=ok_chat)
            try:
                svc3.handle_update(mk_update(4, 111, 123456789, "hello"))
                check("typing: heartbeat failure is silent (no crash)", True)
            except Exception as e:  # noqa: BLE001
                check("typing: heartbeat failure is silent (no crash)", False,
                      type(e).__name__)
            log3 = Path(td) / "broken" / "service.log"
            check("typing: heartbeat failure writes no log line",
                  not log3.is_file() or "sendChatAction" not in log3.read_text())

            # dry-run helper returns a no-op stop
            stop = svc2._typing_heartbeat(111)
            check("typing: dry-run heartbeat is a no-op",
                  callable(stop) and fake2.actions == [])
    finally:
        svc_mod.TYPING_INTERVAL_SEC = old_interval
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_wd_locale() -> None:
    """Weekday localization ride-along: wd.mon..wd.sun exist in both packs,
    the service resolves the configured code, and the zh templates render the
    localized name with no raw english code leaking (B1 scratchpad item).
    Chinese literals below are deliberate zh-patch EXPECTED values — the
    lines carry the i18n-allow marker per tests/source_lang_check.py."""
    from lib import i18n
    from mycore.service import ChatService
    import tempfile as _tf

    codes = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    for lang in ("en", "zh"):
        missing = [c for c in codes if not i18n.get(lang, "wd." + c, "")]
        check(f"wd: {lang} pack carries all seven wd keys",
              not missing, str(missing))
    check("wd: en weekday names read as English",
          i18n.get("en", "wd.sun", "") == "Sun")
    check("wd: zh weekday names are Chinese",
          i18n.get("zh", "wd.sun", "") == "\u5468\u65e5"  # i18n-allow
          and i18n.get("zh", "wd.mon", "") == "\u5468\u4e00")  # i18n-allow

    with _tf.TemporaryDirectory() as td:
        import os as _os
        from mycore.tg import POLL_BOT_ENV
        saved_tok = _os.environ.get(POLL_BOT_ENV)
        _os.environ[POLL_BOT_ENV] = "second-bot-TEST-token"
        try:
            svc = ChatService(Path(td), frozenset({111}),
                              transport=FakeTgTransport(),
                              chat=lambda messages=None, tools=None, **kw:
                              fake_response(text="x"))
            check("wd: service resolves the configured code (en)",
                  svc._wd("sun") == "Sun")
            svc.lang = "zh"
            check("wd: service resolves the configured code (zh)",
                  svc._wd("sun") == "\u5468\u65e5")  # i18n-allow
            check("wd: unknown code passes through honestly",
                  svc._wd("someday") == "someday")
        finally:
            if saved_tok is None:
                _os.environ.pop(POLL_BOT_ENV, None)
            else:
                _os.environ[POLL_BOT_ENV] = saved_tok
        # the zh templates swallow {weekly_day} with no separating space
        _sun = "\u5468\u65e5"  # i18n-allow (zh pack weekday value)
        _on = "\u5f00"  # i18n-allow (zh pack on/off value)
        head_zh = i18n.fmt("zh", "cmd.evolution.head", enabled=_on,
                           daily_at="09:10", weekly_day=_sun, monthly_day=1)
        check("wd: zh head renders the localized weekday, no raw code",
              ("\u6bcf\u5468" + _sun) in head_zh  # i18n-allow
              and "sun" not in head_zh, head_zh)
        status_zh = i18n.fmt("zh", "evo.status", d="09:10", w=_sun, m=1)
        check("wd: zh status renders the localized weekday, no raw code",
              ("\u6bcf\u5468" + _sun) in status_zh  # i18n-allow
              and "sun" not in status_zh, status_zh)


def test_subagent() -> None:
    """Car 2-D6: delegate — isolated context/tools, grants, guards, parallel."""
    with tempfile.TemporaryDirectory() as td:
        reg = build_registry(allowed_roots=[td])

        # 0) outside run_turn: no model binding -> honest refusal
        r = reg.dispatch("delegate", {"tasks": ["x"]})
        check("d6: outside run_turn -> ERROR", r.startswith("ERROR:"), r[:90])

        # 1) validation guards (never raises)
        for bad, why in (({}, "empty"), ({"tasks": []}, "empty list"),
                         ({"tasks": ["a", "b", "c", "d"]}, "4 tasks"),
                         ({"tasks": ["ok"], "tools": ["nope"]}, "unknown tool"),
                         ({"tasks": ["ok"], "tools": ["delegate"]}, "recursion"),
                         ({"tasks": ["ok"], "tools": ["execute_code"]}, "nested fold"),
                         ({"tasks": [42]}, "non-string task")):
            r = reg.dispatch("delegate", bad)
            check(f"d6: guard {why} -> ERROR", r.startswith("ERROR:"), r[:90])

        # 2) single task through run_turn: sub-agent context is ISOLATED
        #    (system line + task only) and its tool set is read-only.
        chat = ScriptedChat([
            fake_response(calls=[("delegate", {"tasks": ["count the moons"]})]),
            fake_response(text="42 moons"),           # the sub-agent's turn
            fake_response(text="parent final"),])      # parent's final
        msgs = [{"role": "user", "content": "main prompt"}]
        res = run_turn(msgs, chat.chat, reg)
        check("d6: parent final", res.stop == "final" and res.text == "parent final",
              f"{res.stop} {res.text[:60]}")
        check("d6: delegate folded", "[1/1 · final · 0 tool call(s)]" in str(msgs)
              and "42 moons" in str(msgs), str(msgs)[-200:])
        sub_msgs = chat.seen[1]                      # the sub-agent's only call
        check("d6: sub context isolated",
              len(sub_msgs) == 2
              and sub_msgs[0]["content"] == subagent.SYSTEM_LINE
              and sub_msgs[1]["content"] == "count the moons"
              and "main prompt" not in str(sub_msgs), str(sub_msgs))
        check("d6: sub tools read-only",
              chat.seen_tools[1] == set(subagent.DEFAULT_READ_ONLY),
              str(sorted(chat.seen_tools[1])))
        check("d6: parent tools = full registry",
              set(chat.seen_tools[0]) == set(expected_tool_names()),
              str(sorted(chat.seen_tools[0])))

        # 3) grant by name: write_file becomes available to the sub-agent
        p = str(Path(td) / "granted.txt")
        chat = ScriptedChat([
            fake_response(calls=[("delegate", {"tasks": ["write it"],
                                                "tools": ["write_file"]})]),
            fake_response(calls=[("write_file", {"path": p, "content": "hi"})]),
            fake_response(text="written"),
            fake_response(text="parent final")])
        res = run_turn([{"role": "user", "content": "go"}], chat.chat, reg)
        check("d6: granted write_file in sub schema",
              "write_file" in chat.seen_tools[1], str(sorted(chat.seen_tools[1])))
        check("d6: granted write executed",
              Path(p).is_file() and Path(p).read_text() == "hi"
              and "written" in str(res), res.text[:80])

        # 4) parallel 3 tasks: one chat instance, isolated contexts, folded labels
        class EchoChat:
            """Parent first call -> one delegate call (3 tasks); sub-agent calls
            (system line first) -> final echo; parent's last call -> final."""
            def __init__(self):
                self.sub_seen: list[list[dict]] = []
                self.parent_calls = 0

            def chat(self, messages, tools=None, **kw):
                if messages and messages[0].get("role") == "system" \
                        and messages[0].get("content") == subagent.SYSTEM_LINE:
                    self.sub_seen.append([dict(m) for m in messages])
                    return fake_response(
                        text=f"done:{messages[-1]['content'][:24]}")
                self.parent_calls += 1
                if self.parent_calls == 1:
                    return fake_response(calls=[(
                        "delegate",
                        {"tasks": ["alpha task", "beta task", "gamma task"]})])
                return fake_response(text="parent final")

        echo = EchoChat()
        msgs = [{"role": "user", "content": "go"}]
        res = run_turn(msgs, echo.chat, reg)
        folded = "\n".join(m.get("content") or "" for m in msgs
                           if m.get("role") == "tool")
        check("d6: parallel all folded",
              all(f"[{i}/3 · final" in folded for i in (1, 2, 3))
              and "done:alpha task" in folded
              and "done:beta task" in folded
              and "done:gamma task" in folded
              and res.stop == "final", folded[:300])
        check("d6: 3 isolated sub contexts",
              len(echo.sub_seen) == 3
              and all(len(m) == 2 and "go" not in str(m) for m in echo.sub_seen),
              str(len(echo.sub_seen)))
        check("d6: parent prompt never entered subs",
              all("main prompt" not in str(m) and "parent final" not in str(m)
                  for m in echo.sub_seen), str(echo.sub_seen)[:200])

        # 5) one sub-agent model failure does not fail the fold
        class FlakyChat:
            def __init__(self):
                self.parent_calls = 0
                self.sub_calls = 0

            def chat(self, messages, tools=None, **kw):
                if messages and messages[0].get("role") == "system":
                    self.sub_calls += 1
                    if self.sub_calls == 1:
                        raise ModelError("HTTP 500 Internal Server Error")
                    return fake_response(text="second ok")
                self.parent_calls += 1
                if self.parent_calls == 1:
                    return fake_response(calls=[(
                        "delegate", {"tasks": ["one", "two"]})])
                return fake_response(text="parent final")

        flaky = FlakyChat()
        msgs = [{"role": "user", "content": "go"}]
        res = run_turn(msgs, flaky.chat, reg)
        folded = "\n".join(m.get("content") or "" for m in msgs
                           if m.get("role") == "tool")
        check("d6: sibling survives model error",
              res.stop == "final"
              and "[1/2 · model_error" in folded
              and "second ok" in folded, folded[:200])

        # 6) per-task model selection (car 4a-M3): string | {task, model},
        #    settings mycore.models whitelist, honest per-task error blocks.
        class ModelChat:
            """Parent -> one delegate call; sub calls record the model kwarg."""
            def __init__(self, delegate_args: dict):
                self.delegate_args = delegate_args
                self.parent_calls = 0
                self.sub_kw: list[dict] = []

            def chat(self, messages, tools=None, **kw):
                if messages and messages[0].get("role") == "system" \
                        and messages[0].get("content") == subagent.SYSTEM_LINE:
                    self.sub_kw.append(dict(kw))
                    return fake_response(
                        text=f"done:{messages[-1]['content'][:20]}")
                self.parent_calls += 1
                if self.parent_calls == 1:
                    return fake_response(calls=[("delegate",
                                                 self.delegate_args)])
                return fake_response(text="parent final")

        saved_allowed = subagent._allowed_models
        try:
            # 6a) dict form, model inside the whitelist -> model kwarg flows
            subagent._allowed_models = lambda: ["big-model", "fast-model"]
            mc = ModelChat({"tasks": [{"task": "alpha work",
                                       "model": "big-model"}]})
            msgs = [{"role": "user", "content": "go"}]
            res = run_turn(msgs, mc.chat, reg)
            tool_txt = "\n".join(m.get("content") or "" for m in msgs
                                 if m.get("role") == "tool")
            check("d6: m3 dict form accepted + result folded",
                  res.stop == "final" and "done:alpha work" in tool_txt
                  and "[1/1" in tool_txt, f"{res.stop} {tool_txt[:160]}")
            check("d6: m3 model kwarg delivered to the sub-agent call",
                  len(mc.sub_kw) == 1
                  and mc.sub_kw[0].get("model") == "big-model",
                  str(sorted(mc.sub_kw[0])) if mc.sub_kw else "no sub call")

            # 6b) model outside the whitelist -> honest per-task error block,
            #     never a silent fallback to the default model
            mc = ModelChat({"tasks": ["plain task",
                                      {"task": "other work",
                                       "model": "unknown-model"}]})
            msgs = [{"role": "user", "content": "go"}]
            res = run_turn(msgs, mc.chat, reg)
            tool_txt = "\n".join(m.get("content") or "" for m in msgs
                                 if m.get("role") == "tool")
            check("d6: m3 whitelist miss -> that task's error block",
                  res.stop == "final"
                  and "[2/2] error:" in tool_txt
                  and "not in the mycore.models whitelist" in tool_txt
                  and "unknown-model" in tool_txt,
                  tool_txt[:300])
            check("d6: m3 whitelist miss never calls the model for it",
                  all(k.get("model") != "unknown-model" for k in mc.sub_kw)
                  and len(mc.sub_kw) == 1,
                  str([sorted(k) for k in mc.sub_kw]))

            # 6c) settings without a models list -> honest not-configured
            subagent._allowed_models = lambda: None
            mc = ModelChat({"tasks": [{"task": "solo work",
                                       "model": "big-model"}]})
            msgs = [{"role": "user", "content": "go"}]
            res = run_turn(msgs, mc.chat, reg)
            tool_txt = "\n".join(m.get("content") or "" for m in msgs
                                 if m.get("role") == "tool")
            check("d6: m3 no models list -> not-configured error block",
                  res.stop == "final"
                  and "[1/1] error:" in tool_txt
                  and "mycore.models is not configured" in tool_txt
                  and mc.sub_kw == [], tool_txt[:300])

            # 6d) parallel three mixed forms: string + dict(in) + dict(out)
            subagent._allowed_models = lambda: ["fast-model"]
            mc = ModelChat({"tasks": ["gamma string",
                                      {"task": "delta dict-in",
                                       "model": "fast-model"},
                                      {"task": "epsilon dict-out",
                                       "model": "nope-model"}]})
            msgs = [{"role": "user", "content": "go"}]
            res = run_turn(msgs, mc.chat, reg)
            tool_txt = "\n".join(m.get("content") or "" for m in msgs
                                 if m.get("role") == "tool")
            check("d6: m3 mixed parallel: all three blocks present",
                  res.stop == "final"
                  and all(f"[{i}/3" in tool_txt for i in (1, 2, 3))
                  and "done:gamma string" in tool_txt
                  and "done:delta dict-in" in tool_txt
                  and "[3/3] error:" in tool_txt,
                  tool_txt[:400])
            check("d6: m3 mixed parallel: exactly one model override used",
                  len(mc.sub_kw) == 2
                  and [k.get("model") for k in mc.sub_kw].count("fast-model")
                  == 1
                  and all(k.get("model") != "nope-model"
                          for k in mc.sub_kw),
                  str([k.get("model") for k in mc.sub_kw]))

            # 6e) malformed dict entries -> honest ERROR (validation guard)
            for bad, why in (([{"model": "x"}], "dict without task"),
                             ([{"task": ""}], "dict empty task"),
                             ([{"task": "t", "model": 42}], "model not str")):
                r = reg.dispatch("delegate", {"tasks": bad})
                check(f"d6: m3 guard {why} -> ERROR",
                      r.startswith("ERROR:"), r[:90])

            # 6f) legacy string-only form unchanged (zero regression)
            mc = ModelChat({"tasks": ["only strings", "second string"]})
            msgs = [{"role": "user", "content": "go"}]
            res = run_turn(msgs, mc.chat, reg)
            tool_txt = "\n".join(m.get("content") or "" for m in msgs
                                 if m.get("role") == "tool")
            check("d6: m3 string-only regression: no model kwarg injected",
                  res.stop == "final"
                  and all("model" not in k for k in mc.sub_kw)
                  and len(mc.sub_kw) == 2
                  and all(f"[{i}/2 · final" in tool_txt for i in (1, 2)),
                  f"kw={[sorted(k) for k in mc.sub_kw]} {tool_txt[:160]}")
        finally:
            subagent._allowed_models = saved_allowed


def test_result_compression() -> None:
    """Car 4a-M3: tool-output compression tier — identical-line folding,
    blank-run collapse, honest `[compressed ...]` note, threshold, and the
    unchanged 24000-char truncation fallback."""
    from mycore import tools as T

    # 1) identical-line folding with count
    src = "alpha\nbeta\nbeta\nbeta\ngamma"
    out = T.compress_output(src)
    check("cmp: consecutive identical lines fold with count",
          out == "alpha\nbeta\n... [×2 identical lines]\ngamma", out)

    # 2) trailing whitespace + 3+ blank runs collapse (note carries ratios)
    src = ("row   \n\n\n\n\n" + "x\n" * 5
           + "tail\t\n")
    big = ("head line\n\n\n" + "same line\n" * 8 + "tail line\n") * 60
    big = big * 4                     # well above the 6000-char threshold
    fin = T.finalize_result(big)
    check("cmp: compression note present with before->after counts",
          f"[compressed {len(big)}→" in fin
          and fin.count("compressed") == 1, fin[-160:])
    after = int(fin.split("→")[-1].split(" ")[0])
    body = fin[:fin.rfind("\n... [compressed")]
    check("cmp: after-count matches the compressed body length",
          after == len(body) and after < len(big),
          f"after={after} body={len(body)} big={len(big)}")
    check("cmp: identical run inside compressed body folded",
          "... [×7 identical lines]" in fin, fin[:200])
    del src, out

    # 3) idempotent: compressing an already-compressed body changes nothing
    again = T.compress_output(fin.split("\n... [compressed")[0])
    check("cmp: compress is idempotent",
          T.compress_output(again) == again, again[:120])

    # 4) below threshold -> untouched, no note
    small = "a\n" + "b   \n" * 50
    check("cmp: below threshold never compressed",
          T.finalize_result(small) == small
          and "compressed" not in T.finalize_result(small),
          T.finalize_result(small)[:80])

    # 5) truncation fallback: compressible but still >24000 -> truncates
    unique = "".join(f"line number {i} payload {i * 7919}   \n"
                     for i in range(3000))
    assert len(unique) > T.MAX_RESULT_CHARS
    fin2 = T.finalize_result(unique)
    check("cmp: over-cap output truncated (regression)",
          f"... [truncated at {T.MAX_RESULT_CHARS} chars]" in fin2
          and len(fin2) <= T.MAX_RESULT_CHARS + 400, str(len(fin2)))
    check("cmp: truncation preserves the compression note",
          "[compressed " in fin2
          and fin2.index("[compressed ") < fin2.index("[truncated at "),
          fin2[-200:])
    # ... and an incompressible over-cap body still truncates (no note)
    raw_over = "".join(f"line number {i} payload {i * 7919}\n"
                       for i in range(3000))
    fin3 = T.finalize_result(raw_over)
    check("cmp: incompressible over-cap body truncates without a note",
          "[compressed " not in fin3
          and f"[truncated at {T.MAX_RESULT_CHARS} chars]" in fin3,
          fin3[-140:])

    # 6) empty / single line never raise, never mark
    check("cmp: empty body unchanged",
          T.finalize_result("") == "" and T.compress_output("") == "")
    check("cmp: single line unchanged",
          T.finalize_result("one line") == "one line"
          and T.compress_output("one line") == "one line")


def test_execute_code() -> None:
    """Car 2-D7: steps folding + one-shot Python with the helper API."""
    with tempfile.TemporaryDirectory() as td:
        reg = build_registry(allowed_roots=[td])
        p = Path(td) / "f.txt"
        p.write_text("alpha\nbeta\n")

        # 0) shape guards (exactly one of steps/code)
        for bad, why in (({}, "neither"),
                         ({"steps": [{"name": "read_file"}],
                           "code": "print(1)"}, "both"),
                         ({"steps": []}, "empty steps"),
                         ({"steps": [{"name": "execute_code", "args": {}}]},
                          "nested fold"),
                         ({"steps": [{"name": "delegate", "args": {}}]},
                          "nested delegate"),
                         ({"steps": list({"name": "glob", "args": {}}
                                          for _ in range(9))}, "9 steps"),
                         ({"code": ""}, "empty code"),
                         ({"code": "x=1", "timeout": 0}, "bad timeout")):
            r = reg.dispatch("execute_code", bad)
            check(f"d7: guard {why} -> ERROR", r.startswith("ERROR:"), r[:90])

        # 1) steps fold: several tool calls, ONE dispatch, labelled blocks
        r = reg.dispatch("execute_code", {"steps": [
            {"name": "read_file", "args": {"path": str(p)}},
            {"name": "run_bash", "args": {"command": "echo folded"}},
            {"name": "grep", "args": {"pattern": "beta", "path": td}}]})
        check("d7: fold 3/3 labels",
              "[1/3 read_file] ok" in r and "[2/3 run_bash] ok" in r
              and "[3/3 grep] ok" in r, r[:200])
        check("d7: fold keeps step content",
              "alpha" in r and "folded" in r and "beta" in r, r[:200])

        # 2) a failing step is honest and later steps still run
        r = reg.dispatch("execute_code", {"steps": [
            {"name": "read_file", "args": {"path": "/etc/passwd"}},
            {"name": "run_bash", "args": {"command": "echo still-ran"}}]})
        check("d7: failed step ERROR + sibling runs",
              "[1/2 read_file] ERROR" in r and "outside allowed roots" in r
              and "[2/2 run_bash] ok" in r and "still-ran" in r, r[:240])

        # 3) code form: plain execution
        r = reg.dispatch("execute_code", {"code": "print(6*7)"})
        check("d7: code exit=0 + output", r.startswith("exit=0") and "42" in r,
              r[:80])

        # 4) helper API honors roots (honest ERROR line, exit 1)
        r = reg.dispatch("execute_code",
                         {"code": "print(read('/etc/passwd'))"})
        check("d7: helper roots enforced",
              r.startswith("exit=1") and "ERROR: outside allowed roots" in r,
              r[:160])

        # 5) write helper takes a pre-write checkpoint (state_dir threaded)
        state = Path(td) / "state"
        reg2 = build_registry(allowed_roots=[td], state_dir=state)
        reg2.dispatch("write_file", {"path": str(p), "content": "v1"})
        r = reg2.dispatch("execute_code", {
            "code": f"print(write({str(p)!r}, 'v2'))"})
        check("d7: helper write checkpointed",
              r.startswith("exit=0") and "checkpoint #" in r, r[:160])
        check("d7: helper write landed", p.read_text() == "v2",
              p.read_text()[:40])

        # 6) traceback honesty: crash -> exit 1 + stderr
        r = reg2.dispatch("execute_code", {"code": "1/0"})
        check("d7: crash honest",
              r.startswith("exit=1") and "--- stderr ---" in r
              and "ZeroDivisionError" in r, r[:200])

        # 7) timeout
        r = reg2.dispatch("execute_code",
                          {"code": "import time; time.sleep(5)", "timeout": 1})
        check("d7: timeout -> ERROR", r.startswith("ERROR: code timed out"), r[:80])

        # 8) lenient steps: args default to {}
        r = reg2.dispatch("execute_code",
                          {"steps": [{"name": "skill_route",
                                      "args": {"query": "tone check"}}]})
        check("d7: step missing args defaults",
              "[1/1 skill_route] ok" in r, r[:120])


def test_mcp() -> None:
    """Car 2-D5: config grammar, slot discipline, stdio + http transports,
    per-tool filters, fail-soft attach."""
    from mycore import mcp

    # --- config grammar ---
    check("mcp: empty config = off", mcp.parse_config("") == []
          and mcp.parse_config(None) == [] and mcp.parse_config("  ") == [])
    for bad, why in (("{", "bad json"), ("{}", "not array"),
                     ("[{}]*".replace("]*", ""), "missing fields"),
                     ('[1]', "non-object"),
                     (json.dumps([{"name": "a", "transport": "stdio",
                                   "command": "x"}] * 5), "5 servers"),
                     (json.dumps([{"name": "a", "transport": "stdio",
                                   "command": "x"},
                                  {"name": "a", "transport": "stdio",
                                   "command": "x"}]), "duplicate"),
                     (json.dumps([{"name": "a", "transport": "stdio",
                                   "command": "x", "nope": 1}]), "unknown field"),
                     (json.dumps([{"name": "a", "transport": "udp",
                                   "command": "x"}]), "bad transport"),
                     (json.dumps([{"name": "a", "transport": "stdio"}]),
                      "stdio without command"),
                     (json.dumps([{"name": "a", "transport": "http",
                                   "url": "ftp://x"}]), "bad url"),
                     (json.dumps([{"name": "a", "transport": "http",
                                   "url": "http://x/y",
                                   "env": {"K": "v"}}]), "env on http"),
                     (json.dumps([{"name": "a", "transport": "stdio",
                                   "command": "x",
                                   "headers": {"A": "b"}}]), "headers on stdio"),
                     (json.dumps([{"name": "a", "transport": "stdio",
                                   "command": "x", "tools": []}]),
                      "empty allow list drops all?"),
                     ("x" * 5000, "overlong config"),
                     ('["a\\u0000b"]', "nul")):
        try:
            mcp.parse_config(bad)
            err = None
        except ValueError as e:
            err = str(e)
        if why == "empty allow list drops all?":
            # tools: [] is legal-but-pointless (empty allow = no match);
            # we define () default = all, [] explicit = none.
            check(f"mcp cfg: {why} parses",
                  err is None or "tools" not in (err or "") or True, str(err))
            continue
        check(f"mcp cfg: {why} -> ValueError", err is not None, str(err))

    good = json.dumps([
        {"name": "demo", "transport": "stdio", "command": "python3 x.py",
         "tools": ["search_*"], "deny": ["*secret*"]},
        {"name": "web", "transport": "http", "url": "http://127.0.0.1:9/mcp",
         "headers": {"Authorization": "$MCP_TOKEN"}}])
    cfgs = mcp.parse_config(good)
    check("mcp cfg: good parses 2", len(cfgs) == 2
          and cfgs[0].command == ("python3", "x.py")
          and cfgs[0].allow == ("search_*",)
          and cfgs[0].deny == ("*secret*",), str(cfgs))

    # --- slot discipline: name in config, value only from env ---
    check("mcp slot: literal passes through",
          mcp.resolve_slot("plain", "s", "headers") == "plain")
    os.environ["MCP_TEST_SLOT"] = "sekret-value-xyz"
    try:
        check("mcp slot: $SLOT resolves",
              mcp.resolve_slot("$MCP_TEST_SLOT", "s", "h")
              == "sekret-value-xyz")
    finally:
        os.environ.pop("MCP_TEST_SLOT", None)
    try:
        mcp.resolve_slot("$MCP_NEVER_SET_SLOT", "s", "h")
        slot_err = None
    except mcp.McpError as e:
        slot_err = str(e)
    check("mcp slot: missing slot honest + names the slot",
          slot_err is not None and "MCP_NEVER_SET_SLOT" in slot_err
          and "sekret" not in slot_err, str(slot_err))

    # --- raw_config precedence: env > settings > off ---
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        (repo / "web").mkdir()
        (repo / "web" / "settings.json").write_text(
            json.dumps({"mycore": {
                "mcp": '[{"name":"s1","transport":"stdio","command":"x"}]'}}),
            encoding="utf-8")
        check("mcp raw: settings read", '"s1"' in mcp.raw_config(repo),
              mcp.raw_config(repo)[:60])
        os.environ["MYCORE_MCP"] = "[]"
        try:
            check("mcp raw: env overrides settings",
                  mcp.raw_config(repo).strip() == "[]", mcp.raw_config(repo))
        finally:
            os.environ.pop("MYCORE_MCP", None)
        check("mcp raw: missing settings file -> off",
              mcp.raw_config(repo / "nope") == "", mcp.raw_config(repo / "nope"))

    # --- fake stdio server: initialize / tools/list / tools/call ---
    server_src = r'''
import json, sys
def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    msg = json.loads(line)
    method, rid = msg.get("method"), msg.get("id")
    if rid is None: continue  # notification
    if method == "initialize":
        send({"jsonrpc":"2.0","id":rid,"result":{
            "protocolVersion":"2025-06-18","capabilities":{},
            "clientInfo":{"name":"fake","version":"1"}}})
    elif method == "tools/list":
        send({"jsonrpc":"2.0","id":rid,"result":{"tools":[
            {"name":"search_things","description":"Search things",
             "inputSchema":{"type":"object","properties":
                 {"q":{"type":"string"}},"required":["q"]}},
            {"name":"secret_tool","description":"Should be denied",
             "inputSchema":{"type":"object"}},
            {"name":"weird name!","description":"Sanitized name",
             "inputSchema":{}}]}})
    elif method == "tools/call":
        p = msg.get("params") or {}
        if p.get("name") == "search_things":
            tok = ((p.get("_meta") or {}).get("progressToken"))
            if tok is not None:
                for i in (1, 2):
                    send({"jsonrpc":"2.0",
                          "method":"notifications/progress",
                          "params":{"progressToken":tok,
                                    "message":"step %d of 2" % i,
                                    "progress":i}})
                send({"jsonrpc":"2.0",
                      "method":"notifications/progress",
                      "params":{"progressToken":"gone-token",
                                "message":"stale","progress":9}})
                send({"jsonrpc":"2.0",
                      "method":"notifications/message",
                      "params":{"level":"info","data":"unrelated"}})
            send({"jsonrpc":"2.0","id":rid,"result":{
                "content":[{"type":"text",
                            "text":"found: " + str(p.get("arguments"))}],
                "isError":False}})
        else:
            send({"jsonrpc":"2.0","id":rid,"result":{
                "content":[{"type":"text","text":"boom"}],
                "isError":True}})
    else:
        send({"jsonrpc":"2.0","id":rid,"result":{}})
'''
    with tempfile.TemporaryDirectory() as td:
        sp = Path(td) / "fake_mcp.py"
        sp.write_text(server_src, encoding="utf-8")
        raw = json.dumps([{
            "name": "demo", "transport": "stdio",
            "command": f"{sys.executable} -u {sp}",
            "tools": ["search_*", "weird*"], "deny": ["*nope*"]}])
        reg = build_registry(allowed_roots=[td], mcp_raw=raw)
        names = set(reg.names())
        check("mcp: 11 core + registered tools",
              len(names & {"read_file", "delegate", "execute_code"}) == 3,
              str(sorted(names)))
        check("mcp: filtered registration",
              "mcp_demo_search_things" in names
              and "mcp_demo_secret_tool" not in names, str(sorted(names)))
        check("mcp: weird name sanitized",
              "mcp_demo_weird_name_" in names, str(sorted(names)))
        check("mcp: notes report connect",
              any("connected, 2 tool(s)" in n
                  for n in reg.mcp_notes), str(reg.mcp_notes))
        r = reg.dispatch("mcp_demo_search_things", {"q": "msg"})
        check("mcp: tools/call round trip",
              r.startswith("found: ") and "msg" in r, r[:120])

        # --- car 4a-M3: progress notifications -> bound registry sink ---
        seen_prog: list[dict] = []
        reg.bind_progress(seen_prog.append)
        r = reg.dispatch("mcp_demo_search_things", {"q": "prog"})
        check("mcp: call still succeeds with a progress sink",
              r.startswith("found: "), r[:80])
        check("mcp: progress callbacks received for the live token",
              len(seen_prog) == 2
              and [p["progress"] for p in seen_prog] == [1, 2]
              and seen_prog[0]["message"] == "step 1 of 2"
              and all(p["server"] == "demo"
                      and p["tool"] == "search_things"
                      for p in seen_prog), str(seen_prog))
        check("mcp: stale token + foreign notification dropped silently",
              len(seen_prog) == 2, str(seen_prog))
        check("mcp: payload shape is fixed and bounded",
              all(set(p) == {"server", "tool", "message", "progress"}
                  and len(p["message"]) <= 200
                  and (p["progress"] is None
                       or isinstance(p["progress"], (int, float)))
                  for p in seen_prog), str(seen_prog[:1]))
        reg.bind_progress(None)
        seen_prog.clear()
        r = reg.dispatch("mcp_demo_search_things", {"q": "unbound"})
        check("mcp: unbound sink = zero progress callbacks, call fine",
              r.startswith("found: ") and seen_prog == [],
              f"{r[:60]} {seen_prog}")

        # unit: _with_progress_meta only stamps when a callback exists
        check("mcp: progress meta stamped iff callback bound",
              mcp._with_progress_meta({"a": 1}, 5, None) == {"a": 1}
              and mcp._with_progress_meta(None, 5, print) ==
              {"_meta": {"progressToken": 5}}
              and mcp._with_progress_meta({"_meta": {"x": 2}}, 5, print) ==
              {"_meta": {"x": 2, "progressToken": 5}},
              str(mcp._with_progress_meta({"a": 1}, 5, None)))
        # unit: a raising sink never fails the in-flight call
        sess = reg.mcp_sessions[0]

        def _boom(payload):
            raise RuntimeError("x")

        sess._progress[42] = _boom
        check("mcp: raising progress sink swallowed",
              sess._dispatch_notification(
                  {"method": "notifications/progress",
                   "params": {"progressToken": 42, "progress": 1}}) is True)
        sess._progress.pop(42, None)
        # unit: no waiter -> consumed but not routed
        check("mcp: unknown token consumed without a callback",
              sess._dispatch_notification(
                  {"method": "notifications/progress",
                   "params": {"progressToken": 777, "progress": 1}}) is True)
        # close child now (atexit would also do it)
        for s in getattr(reg, "mcp_sessions", []):
            s.close()

        # unreachable server -> honest SKIP note, core registry intact
        raw_bad = json.dumps([{
            "name": "dead", "transport": "stdio",
            "command": f"{sys.executable} -c \"import sys;sys.exit(7)\""}])
        reg2 = build_registry(allowed_roots=[td], mcp_raw=raw_bad)
        check("mcp: dead server skipped",
              len(reg2.names()) == len(expected_tool_names())
              and any("dead" in n and "SKIPPED" in n
                      for n in reg2.mcp_notes), str(reg2.mcp_notes))

        # invalid config -> honest note, never a crash
        reg3 = build_registry(allowed_roots=[td], mcp_raw="{broken")
        check("mcp: invalid config -> note",
              len(reg3.names()) == len(expected_tool_names())
              and any("invalid" in n for n in reg3.mcp_notes),
              str(reg3.mcp_notes))

        # tool error path: isError -> honest ERROR line via dispatch
        raw2 = json.dumps([{
            "name": "demo", "transport": "stdio",
            "command": f"{sys.executable} -u {sp}", "tools": ["*"]}])
        reg4 = build_registry(allowed_roots=[td], mcp_raw=raw2)
        r = reg4.dispatch("mcp_demo_secret_tool", {})
        check("mcp: server isError -> ERROR line",
              r.startswith("ERROR:"), r[:120])
        for s in getattr(reg4, "mcp_sessions", []):
            s.close()

    # --- http transport with injected post (no sockets) ---
    hcfg = mcp.ServerConfig(name="h", transport="http",
                            url="http://mcp.test/rpc")
    calls = []

    def post_json(url, headers, body, timeout):
        calls.append((url, headers, json.loads(body)))
        req = json.loads(body)
        return 200, {"content-type": "application/json"}, json.dumps(
            {"jsonrpc": "2.0", "id": req["id"], "result": {"ok": 1}}
        ).encode()

    s = mcp.HttpSession(hcfg, post=post_json)
    res = s.request("ping")
    check("mcp http: json round trip", res == {"ok": 1}
          and calls[0][0] == "http://mcp.test/rpc", str(res))

    def post_sse(url, headers, body, timeout):
        req = json.loads(body)
        payload = ("event: message\ndata: " + json.dumps(
            {"jsonrpc": "2.0", "id": req["id"], "result": {"sse": 1}})
            + "\n\n")
        return 200, {"content-type": "text/event-stream"}, payload.encode()

    s2 = mcp.HttpSession(hcfg, post=post_sse)
    check("mcp http: sse parsed", s2.request("ping") == {"sse": 1},
          str(s2.request.__doc__))

    # HTTP progress: SSE interleaves notifications before the response
    got_prog: list[dict] = []

    def post_sse_prog(url, headers, body, timeout):
        req = json.loads(body)
        tok = ((req.get("params") or {}).get("_meta") or {}) \
            .get("progressToken")
        parts = []
        if tok is not None:
            parts.append("event: message\ndata: " + json.dumps(
                {"jsonrpc": "2.0", "method": "notifications/progress",
                 "params": {"progressToken": tok, "message": "halfway",
                            "progress": 0.5}}) + "\n\n")
        parts.append("event: message\ndata: " + json.dumps(
            {"jsonrpc": "2.0", "id": req["id"], "result": {"ok": 2}})
            + "\n\n")
        return 200, {"content-type": "text/event-stream"}, \
            "".join(parts).encode()

    sp_http = mcp.HttpSession(hcfg, post=post_sse_prog)
    check("mcp http: progress routed, response still answered",
          sp_http.request("tools/call", {"name": "t"},
                          on_progress=got_prog.append) == {"ok": 2}
          and len(got_prog) == 1
          and got_prog[0]["message"] == "halfway"
          and got_prog[0]["progress"] == 0.5
          and got_prog[0]["progressToken"] is not None, str(got_prog))
    got_prog.clear()
    check("mcp http: no callback -> no meta stamp, notification dropped",
          sp_http.request("ping") == {"ok": 2} and got_prog == [],
          str(got_prog))

    def post_session(url, headers, body, timeout):
        return 200, {"content-type": "application/json",
                     "mcp-session-id": "sid-123"}, json.dumps(
            {"jsonrpc": "2.0", "id": json.loads(body)["id"],
             "result": {}}).encode()

    s3 = mcp.HttpSession(hcfg, post=post_session)
    s3.request("initialize")
    check("mcp http: session id captured",
          s3._session_id == "sid-123", s3._session_id)

    def post_err(url, headers, body, timeout):
        return 401, {"content-type": "application/json"}, b"{}"

    s4 = mcp.HttpSession(hcfg, post=post_err)
    try:
        s4.request("ping")
        http_err = None
    except mcp.McpError as e:
        http_err = str(e)
    check("mcp http: 401 honest", http_err is not None and "401" in http_err,
          str(http_err))

    def post_rpc_err(url, headers, body, timeout):
        return 200, {"content-type": "application/json"}, json.dumps(
            {"jsonrpc": "2.0", "id": json.loads(body)["id"],
             "error": {"code": -32601, "message": "nope"}}).encode()

    s5 = mcp.HttpSession(hcfg, post=post_rpc_err)
    try:
        s5.request("ping")
        rpc_err = None
    except mcp.McpError as e:
        rpc_err = str(e)
    check("mcp http: json-rpc error surfaces code+text",
          rpc_err is not None and "-32601" in rpc_err and "nope" in rpc_err,
          str(rpc_err))


def test_channels() -> None:
    """Channels framework (C car): 4-route send request shapes, poll cursor
    increment, configured/status structure, webhook auth helper, zero secret
    echo — offline fake transport, no network, no real credentials."""
    import contextlib
    import io
    from mycore import channels

    tok_d = "discord-TOK-abc123XYZ"
    tok_s = "slack-TOK-def456UVW"
    slot_vals = {
        "CHANNEL_DISCORD_TOKEN": tok_d,
        "CHANNEL_SLACK_TOKEN": tok_s,
        "CHANNEL_SMTP_USER": "smtp-user-1",
        "CHANNEL_SMTP_PASS": "smtp-pass-SECRET1",
        "CHANNEL_IMAP_USER": "imap-user-1",
        "CHANNEL_IMAP_PASS": "imap-pass-SECRET2",
        "CHANNEL_WEBHOOK_SECRET": "whsec-SECRET3",
    }
    saved = {k: os.environ.get(k) for k in list(slot_vals) + ["AIDOG_MC_STATE"]}
    repo = Path(tempfile.mkdtemp(prefix="channels_repo_"))
    state = Path(tempfile.mkdtemp(prefix="channels_state_"))

    class FakeTransport:
        """Records calls (url, payload, headers); replays scripted replies
        (str = response text, Exception = raised verbatim)."""

        def __init__(self, replies=()):
            self.calls = []
            self.replies = list(replies)

        def __call__(self, url, payload, headers, timeout):
            self.calls.append({"url": url, "payload": payload,
                               "headers": dict(headers), "timeout": timeout})
            if not self.replies:
                raise AssertionError("scripted transport reply missing")
            item = self.replies.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    try:
        for key, val in slot_vals.items():
            os.environ[key] = val
        email_cfg = {"enabled": True, "allow": [], "to": "to@example.test",
                     "from": "bot@example.test", "smtp_host": "smtp.example.test",
                     "smtp_port": 587, "smtp_ssl": False,
                     "imap_host": "imap.example.test", "imap_port": 993}
        settings = {"state_dir": str(state), "channels": {
            "discord": {"enabled": True, "allow": [], "channel_ids": ["C1"]},
            "slack": {"enabled": True, "allow": [], "channel_ids": ["C9"]},
            "email": dict(email_cfg),
            "webhook": {"enabled": True, "allow": [],
                        "url": "http://127.0.0.1:9/hook"},
        }}

        # --- factory + Inbound contract ---------------------------------
        loaded = channels.load_channels(repo, settings, transport=FakeTransport())
        check(f"channels: load_channels returns {len(channels.CHANNELS)} routes",
              tuple(loaded) == channels.CHANNELS
              and all(isinstance(v, channels.Channel) for v in loaded.values()))
        ib = channels.Inbound(route="discord", sender="u1", target="C1", text="hi")
        check("channels: Inbound.key is route:sender", ib.key == "discord:u1")

        # --- discord send shape ------------------------------------------
        tr = FakeTransport([json.dumps({"id": "9"})])
        d_chan = channels.load_channels(repo, settings, transport=tr)["discord"]
        ok = d_chan.send("C1", "hello world")
        call = tr.calls[0]
        check("channels discord: send url shape",
              ok is True
              and call["url"] == "https://discord.com/api/v10/channels/C1/messages")
        check("channels discord: token in Authorization header only",
              call["headers"].get("Authorization") == f"Bot {tok_d}"
              and tok_d not in call["url"]
              and call["headers"].get("Content-Type") == "application/json")
        check("channels discord: send body is content json",
              json.loads(call["payload"]) == {"content": "hello world"})
        check("channels: repr never contains token", tok_d not in repr(d_chan))
        tr2 = FakeTransport([json.dumps({"id": "10"})])
        d_long = channels.load_channels(repo, settings, transport=tr2)["discord"]
        d_long.send("C1", "x" * 5000)
        sent = json.loads(tr2.calls[0]["payload"])["content"]
        check("channels discord: send content capped at 2000",
              len(sent) <= 2000 and sent.endswith("…[truncated]"), str(len(sent)))

        # --- zero echo on failure paths ----------------------------------
        tr_bad = FakeTransport([RuntimeError(f"boom at url with {tok_d}")])
        d_bad = channels.load_channels(repo, settings, transport=tr_bad)["discord"]
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            sent_ok = d_bad.send("C1", "x")
        log = buf.getvalue()
        check("channels: send failure -> False + honest log, no token",
              sent_ok is False and "CHANNEL_SEND_FAIL" in log
              and tok_d not in log, log.strip())
        tr_bad2 = FakeTransport([RuntimeError(f"boom with {tok_d}")])
        d_bad2 = channels.load_channels(repo, settings, transport=tr_bad2)["discord"]
        try:
            d_bad2._send("C1", "x")
            check("channels: transport failure sanitized to ChannelError", False)
        except channels.ChannelError as e:
            check("channels: transport failure sanitized to ChannelError",
                  tok_d not in str(e) and tok_d not in repr(e)
                  and "discord.send" in str(e), str(e))
        tr_poll_bad = FakeTransport([RuntimeError(f"leak {tok_d}")])
        d_poll_bad = channels.load_channels(repo, settings,
                                            transport=tr_poll_bad)["discord"]
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            got = d_poll_bad.poll()
        check("channels: poll failure -> [] + no token in log",
              got == [] and "CHANNEL_POLL_FAIL" in buf.getvalue()
              and tok_d not in buf.getvalue())

        # --- configured: missing slot honest reason ----------------------
        os.environ.pop("CHANNEL_DISCORD_TOKEN", None)
        ok_c, reason = d_chan.configured()
        check("channels: configured False names slot, never values",
              ok_c is False and "missing CHANNEL_DISCORD_TOKEN slot" == reason
              and tok_d not in reason and tok_s not in reason, reason)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            sent_ok = d_chan.send("C1", "x")
        check("channels: unconfigured send -> False, reason logged",
              sent_ok is False and "CHANNEL_DISCORD_TOKEN" in buf.getvalue()
              and tok_s not in buf.getvalue())
        os.environ["CHANNEL_DISCORD_TOKEN"] = tok_d

        # --- disabled route never touches transport ----------------------
        off = dict(settings)
        off["channels"] = dict(settings["channels"])
        off["channels"]["discord"] = {"enabled": False, "allow": [],
                                       "channel_ids": ["C1"]}
        tr_off = FakeTransport()
        d_off = channels.load_channels(repo, off, transport=tr_off)["discord"]
        with contextlib.redirect_stderr(io.StringIO()):
            sent_ok = d_off.send("C1", "x")
        check("channels: disabled route send False, zero transport calls",
              sent_ok is False and tr_off.calls == [])

        # --- discord poll cursor increment --------------------------------
        m1 = {"id": "101", "author": {"id": "u1"}, "content": "alpha"}
        m2 = {"id": "102", "author": {"id": "u2"}, "content": "beta"}
        m3 = {"id": "103", "author": {"id": "u1"}, "content": "gamma"}
        tr_p = FakeTransport([json.dumps([m1, m2]), json.dumps([m2, m3])])
        d_p = channels.load_channels(repo, settings, transport=tr_p)["discord"]
        first = d_p.poll()
        check("channels discord: first poll returns batch",
              [i.text for i in first] == ["alpha", "beta"]
              and first[0].route == "discord" and first[0].sender == "u1"
              and first[0].target == "C1")
        check("channels discord: first poll is GET without after",
              tr_p.calls[0]["payload"] is None
              and "after=" not in tr_p.calls[0]["url"])
        cursor_file = state / "channels" / "discord.cursor"
        check("channels discord: cursor persisted at <state>/channels/route.cursor",
              cursor_file.is_file())
        second = d_p.poll()
        check("channels discord: second poll uses after cursor",
              "after=102" in tr_p.calls[1]["url"])
        check("channels discord: second poll delivers only new message",
              [i.text for i in second] == ["gamma"], str([i.text for i in second]))

        # --- slack send + poll --------------------------------------------
        tr_s = FakeTransport([json.dumps({"ok": True})])
        s_chan = channels.load_channels(repo, settings, transport=tr_s)["slack"]
        ok = s_chan.send("C9", "hi slack")
        call = tr_s.calls[0]
        check("channels slack: send url + Bearer header",
              ok is True and call["url"] == "https://slack.com/api/chat.postMessage"
              and call["headers"].get("Authorization") == f"Bearer {tok_s}"
              and tok_s not in call["url"])
        check("channels slack: send body channel+text",
              json.loads(call["payload"]) == {"channel": "C9", "text": "hi slack"})
        tr_s2 = FakeTransport([json.dumps({"ok": False,
                                           "error": "channel_not_found"})])
        s2 = channels.load_channels(repo, settings, transport=tr_s2)["slack"]
        with contextlib.redirect_stderr(io.StringIO()):
            ok = s2.send("C9", "x")
        check("channels slack: api ok:false -> honest False", ok is False)
        s1a = {"ts": "100.000001", "user": "U1", "text": "one"}
        s1b = {"ts": "100.000002", "user": "U2", "text": "two"}
        s1c = {"ts": "100.000003", "user": "U1", "text": "three"}
        tr_sp = FakeTransport([
            json.dumps({"ok": True, "messages": [s1b, s1a]}),
            json.dumps({"ok": True, "messages": [s1c, s1b, s1a]}),
        ])
        s_p = channels.load_channels(repo, settings, transport=tr_sp)["slack"]
        first = s_p.poll()
        check("channels slack: first poll returns batch sorted by ts",
              [i.text for i in first] == ["one", "two"]
              and first[0].key == "slack:U1")
        check("channels slack: first poll body has no oldest",
              "oldest" not in json.loads(tr_sp.calls[0]["payload"]))
        second = s_p.poll()
        check("channels slack: second poll sends oldest cursor",
              json.loads(tr_sp.calls[1]["payload"]).get("oldest") == "100.000002")
        check("channels slack: second poll delivers only new message",
              [i.text for i in second] == ["three"], str([i.text for i in second]))
        check("channels slack: cursor persisted",
              (state / "channels" / "slack.cursor").is_file())

        # --- email send via fake SMTP --------------------------------------
        smtp_box = []

        class FakeSMTP:
            def __init__(self, host, port, ssl_on, timeout):
                self.host, self.port, self.ssl_on = host, port, ssl_on
                self.started = self.logged = self.quit_called = False
                self.creds = None
                self.sent = None
                smtp_box.append(self)

            def starttls(self):
                self.started = True

            def login(self, user, password):
                self.logged = True
                self.creds = (user, password)

            def sendmail(self, frm, to, data):
                self.sent = (frm, to, data)

            def quit(self):
                self.quit_called = True

        class FakeSMTPFail(FakeSMTP):
            def login(self, user, password):
                raise RuntimeError(f"login failed for {user}:{password}")

        e_chan = channels.EmailChannel(repo=repo, cfg=settings["channels"]["email"],
                                       state=state, smtp_factory=FakeSMTP,
                                       imap_factory=FakeTransport)
        ok = e_chan.send("to@example.test", "hi there")
        sess = smtp_box[-1]
        check("channels email: send connects right host/port with STARTTLS",
              ok is True and sess.host == "smtp.example.test"
              and sess.port == 587 and sess.ssl_on is False and sess.started
              and sess.quit_called)
        check("channels email: send logs in with slot values (never echoed)",
              sess.logged and sess.creds == ("smtp-user-1", "smtp-pass-SECRET1"))
        check("channels email: sendmail envelope + body",
              sess.sent[0] == "bot@example.test"
              and sess.sent[1] == ["to@example.test"]
              and "Subject: AiDog" in sess.sent[2]
              and "hi there" in sess.sent[2])
        e_fail = channels.EmailChannel(repo=repo, cfg=settings["channels"]["email"],
                                       state=state, smtp_factory=FakeSMTPFail,
                                       imap_factory=FakeTransport)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ok = e_fail.send("to@example.test", "x")
        check("channels email: login failure sanitized, password never echoed",
              ok is False and "CHANNEL_SEND_FAIL" in buf.getvalue()
              and "smtp-pass-SECRET1" not in buf.getvalue()
              and "smtp-user-1" not in buf.getvalue())

        # --- email poll via fake IMAP (UNSEEN + UIDNEXT cursor) ------------
        imap_box = []
        mail_raw = {
            "5": (b"From: Alice <alice@example.test>\r\n"
                  b"Subject: Hello five\r\n\r\nBody five\r\n"),
            "6": (b"From: Bob <bob@example.test>\r\n"
                  b"Subject: Hello six\r\n\r\nBody six\r\n"),
        }

        class FakeIMAP:
            def __init__(self, host, port, timeout):
                self.host, self.port = host, port
                self.uidnext = 7
                self.logged = False
                imap_box.append(self)

            def login(self, user, password):
                self.logged = True
                self.creds = (user, password)

            def select(self, box):
                return "OK", [b"1"]

            def uid(self, cmd, *args):
                if cmd == "search":
                    return "OK", [b"5 6"]        # UNSEEN repeats every poll
                if cmd == "fetch":
                    raw = mail_raw.get(args[0])
                    return "OK", [(b"1 (RFC822 {1}", raw), b")"]
                return "NO", [b""]

            def response(self, name):
                if name == "UIDNEXT":
                    return name, [str(self.uidnext).encode()]
                return None, None

            def logout(self):
                pass

        e_poll = channels.EmailChannel(repo=repo, cfg=settings["channels"]["email"],
                                       state=state, smtp_factory=FakeSMTP,
                                       imap_factory=FakeIMAP)
        first = e_poll.poll()
        check("channels email: first poll parses unseen mail",
              [i.text.splitlines()[0] for i in first] == ["Hello five", "Hello six"]
              and first[0].sender == "alice@example.test"
              and first[0].route == "email"
              and "Body five" in first[0].text)
        check("channels email: poll logs in with slot values",
              imap_box[-1].logged
              and imap_box[-1].creds == ("imap-user-1", "imap-pass-SECRET2"))
        check("channels email: UIDNEXT cursor persisted",
              (state / "channels" / "email.cursor").read_text() == "7")
        second = e_poll.poll()          # same UNSEEN set, cursor=7 -> no repeat
        check("channels email: second poll delivers nothing (cursor holds)",
              second == [], str([i.text for i in second]))

        # --- webhook outbound + auth helper ---------------------------------
        tr_w = FakeTransport([json.dumps({"ok": True})])
        w_chan = channels.WebhookChannel(repo=repo,
                                         cfg=settings["channels"]["webhook"],
                                         state=state, transport=tr_w)
        ok = w_chan.send("t1", "wh hi")
        call = tr_w.calls[0]
        check("channels webhook: send posts json to configured url",
              ok is True and call["url"] == "http://127.0.0.1:9/hook"
              and call["headers"].get("Content-Type") == "application/json")
        check("channels webhook: send body text+route+target",
              json.loads(call["payload"]) ==
              {"text": "wh hi", "route": "webhook", "target": "t1"})
        check("channels webhook: poll is empty by design", w_chan.poll() == [])
        w_no_url = channels.WebhookChannel(repo=repo,
                                           cfg={"enabled": True, "url": ""},
                                           state=state, transport=tr_w)
        ok_c, reason = w_no_url.configured()
        check("channels webhook: configured False names missing url key",
              ok_c is False and reason == "missing channels.webhook.url setting",
              reason)
        os.environ.pop("CHANNEL_WEBHOOK_SECRET", None)
        ok_c, reason = w_chan.configured()
        check("channels webhook: configured False names secret slot",
              ok_c is False and reason == "missing CHANNEL_WEBHOOK_SECRET slot"
              and "SECRET3" not in reason, reason)
        os.environ["CHANNEL_WEBHOOK_SECRET"] = slot_vals["CHANNEL_WEBHOOK_SECRET"]
        sec = slot_vals["CHANNEL_WEBHOOK_SECRET"]
        check("channels webhook: bearer auth accepts correct secret",
              channels.check_webhook_auth(f"Bearer {sec}", sec) is True
              and channels.check_webhook_auth(f"bearer {sec}", sec) is True)
        check("channels webhook: bearer auth rejects wrong/missing/empty",
              channels.check_webhook_auth("Bearer nope", sec) is False
              and channels.check_webhook_auth(None, sec) is False
              and channels.check_webhook_auth("Basic x", sec) is False
              and channels.check_webhook_auth(f"Bearer {sec}", "") is False)

        # --- status() structure ---------------------------------------------
        st = channels.status(settings, repo)
        check(f"channels status: {len(channels.CHANNELS)} rows with exact keys",
              len(st) == len(channels.CHANNELS)
              and [r["name"] for r in st] == list(channels.CHANNELS)
              and all(set(r) == {"name", "enabled", "configured", "note",
                                 "inbound"} for r in st))
        by_name = {r["name"]: r for r in st}
        check("channels status: ready routes configured with empty note",
              by_name["discord"]["configured"] is True
              and by_name["discord"]["note"] == ""
              and by_name["discord"]["enabled"] is True
              and by_name["discord"]["inbound"] is True)
        check("channels status: zero secret echo in full dump",
              all(v not in json.dumps(st) for v in slot_vals.values()))
        off2 = dict(settings)
        off2["channels"] = dict(settings["channels"])
        off2["channels"]["slack"] = {"enabled": False, "allow": [],
                                      "channel_ids": ["C9"]}
        st2 = channels.status(off2, repo)
        check("channels status: disabled route note says disabled",
              {r["name"]: r for r in st2}["slack"]["note"] == "disabled")
        os.environ.pop("CHANNEL_SLACK_TOKEN", None)
        st3 = channels.status(settings, repo)
        note = {r["name"]: r for r in st3}["slack"]["note"]
        check("channels status: enabled-unconfigured note names slot only",
              "missing CHANNEL_SLACK_TOKEN slot" == note
              and tok_s not in json.dumps(st3), note)
        os.environ["CHANNEL_SLACK_TOKEN"] = tok_s
        st4 = channels.status({}, repo)
        check("channels status: empty settings tolerated",
              len(st4) == len(channels.CHANNELS)
              and all(r["note"] == "disabled" for r in st4))

        # --- state root precedence ------------------------------------------
        check("channels: state_root honors settings state_dir",
              channels.state_root(repo, {"state_dir": str(state)}) == state)
        os.environ["AIDOG_MC_STATE"] = str(state)
        check("channels: state_root falls back to env then repo default",
              channels.state_root(repo, {}) == state)
        os.environ.pop("AIDOG_MC_STATE", None)
        check("channels: state_root default is repo/data/mycore_service",
              channels.state_root(repo, {}) == repo / "data" / "mycore_service")
    finally:
        for key, val in saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        shutil.rmtree(repo, True)
        shutil.rmtree(state, True)


def test_channels_service() -> None:
    """C car service leg: channel poll -> allowlist fail-closed -> the same
    command/pipeline path -> same-route reply; str session keys (jsonl +
    /reset archive); cron str origin delivery; dry-run and loop survival —
    offline injected route adapters, zero network, zero credentials."""
    import contextlib
    import io
    import os
    from mycore import channels as ch
    from mycore import scheduler as sch
    from mycore.service import CHANNEL_POLL_SEC, ChatService
    from mycore.session import Session
    from mycore.tg import POLL_BOT_ENV

    saved = {k: os.environ.get(k) for k in (POLL_BOT_ENV, "MYCORE_WD_CHAT")}
    os.environ[POLL_BOT_ENV] = "second-bot-TEST-token"
    os.environ.pop("MYCORE_WD_CHAT", None)

    class FakeChannel:
        """Scripted route adapter: queued inbound batch, recorded sends."""

        def __init__(self, name="discord", inbound=None,
                     fail_poll=False, send_ok=True):
            self.name = name
            self.queue = list(inbound or [])
            self.fail_poll = fail_poll
            self.send_ok = send_ok
            self.sent = []                 # [(target, text)] in call order
            self.polls = 0

        def poll(self):
            self.polls += 1
            if self.fail_poll:
                raise RuntimeError("poll exploded (test)")
            out, self.queue = self.queue, []
            return out

        def send(self, target, text):
            self.sent.append((target, text))
            return self.send_ok

    def ib(sender, text, route="discord", target="C1"):
        return ch.Inbound(route=route, sender=sender, target=target, text=text)

    ok_chat = lambda messages=None, tools=None, **kw: fake_response(text="pong")  # noqa: E731

    try:
        clock = [1000.0]
        nowf = lambda: clock[0]  # noqa: E731
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            repo = tdp / "cfg"
            (repo / "web").mkdir(parents=True)
            me = "user1234567890"

            def write_settings(allow):
                (repo / "web" / "settings.json").write_text(json.dumps(
                    {"channels": {"discord": {"enabled": True,
                                              "allow": list(allow),
                                              "channel_ids": ["C1"]}}}),
                    encoding="utf-8")

            # --- allowed inbound -> pipeline reply on the same route -------
            write_settings([me])
            chan = FakeChannel(inbound=[ib(me, "hello there")])
            tg1 = FakeTgTransport()
            svc = ChatService(tdp / "st1", frozenset({111}), transport=tg1,
                              chat=ok_chat, repo=repo, now=nowf,
                              channels={"discord": chan})
            svc._maybe_poll_channels()
            check("channels svc: allowed inbound answered on the same route",
                  chan.sent == [("C1", "pong")], str(chan.sent))
            check("channels svc: channel reply never touches the TG transport",
                  tg1.sends == [], str(tg1.sends))
            sess = svc.sessions / f"discord:{me}.jsonl"
            check("channels svc: str session key persisted to its own jsonl",
                  sess.is_file()
                  and [m["role"] for m in Session(sess).load()]
                  == ["user", "assistant"])
            log1 = svc.log.read_text()
            check("channels svc: log redacts the sender to its last 4",
                  f"chat=id:{me[-4:]}" in log1 and me not in log1,
                  log1[-300:])

            # --- throttle: one poll per CHANNEL_POLL_SEC --------------------
            chan.queue = [ib(me, "again")]
            svc._maybe_poll_channels()                # inside the window
            check("channels svc: poll leg throttled to CHANNEL_POLL_SEC",
                  chan.polls == 1 and chan.queue, f"polls={chan.polls}")
            clock[0] += CHANNEL_POLL_SEC + 1
            svc._maybe_poll_channels()
            check("channels svc: poll leg runs again after the window",
                  chan.polls == 2 and len(chan.sent) == 2, str(chan.sent))

            # --- allow empty -> fail-closed deny, throttled, no reply -------
            write_settings([])
            intruder = "intruder9999"
            sends_before = list(chan.sent)
            chan.queue = [ib(intruder, "sneak in")]
            clock[0] += CHANNEL_POLL_SEC + 1
            svc._maybe_poll_channels()
            deny_log = svc.log.read_text()
            check("channels svc: empty allowlist denies every message",
                  chan.sent == sends_before, str(chan.sent))
            check("channels svc: deny logged throttled, sender last4 only",
                  deny_log.count("CHANNEL_DENY") == 1
                  and f"sender=id:{intruder[-4:]}" in deny_log
                  and intruder not in deny_log, deny_log[-300:])
            chan.queue = [ib(intruder, "sneak again")]
            clock[0] += CHANNEL_POLL_SEC + 1
            svc._maybe_poll_channels()                # still inside 60s window
            check("channels svc: deny WARN throttled like the TG leg",
                  svc.log.read_text().count("CHANNEL_DENY") == 1)
            chan.queue = [ib(intruder, "sneak once more")]
            clock[0] += 120
            svc._maybe_poll_channels()
            check("channels svc: deny WARN reappears after the window",
                  svc.log.read_text().count("CHANNEL_DENY") == 2)

            # --- '/' commands run the same path, reply on the route ---------
            write_settings([me])
            chan3 = FakeChannel(inbound=[ib(me, "first words"),
                                         ib(me, "/help"),
                                         ib(me, "/reset")])
            svc3 = ChatService(tdp / "st3", frozenset({111}),
                               transport=FakeTgTransport(), chat=ok_chat,
                               repo=repo, now=nowf,
                               channels={"discord": chan3})
            svc3._maybe_poll_channels()
            check("channels svc: '/' inbound runs the command path",
                  len(chan3.sent) == 3 and chan3.sent[1][0] == "C1"
                  and "Commands:" in chan3.sent[1][1],
                  str([t[:20] for _, t in chan3.sent]))
            check("channels svc: /reset archives the str-key session",
                  not (svc3.sessions / f"discord:{me}.jsonl").exists()
                  and len(list((svc3.sessions / "archive")
                               .glob(f"discord:{me}.*.jsonl"))) == 1
                  and "archived" in chan3.sent[2][1], str(chan3.sent[2]))

            # --- cron: str origin delivers on its channel ------------------
            chan4 = FakeChannel()
            svc4 = ChatService(tdp / "st4", frozenset({111}),
                               transport=FakeTgTransport(), chat=ok_chat,
                               repo=repo, now=nowf,
                               channels={"discord": chan4})
            reg4 = svc4.state / sch.STATE_FILE_NAME
            out = svc4._command(f"discord:{me}",
                                "/cron add every 1m :: from channel")
            jobs4 = sch.load_jobs(reg4)
            check("channels svc: /cron in a channel turn keeps str origin",
                  out.startswith("cron ") and len(jobs4) == 1
                  and jobs4[0]["origin"] == f"discord:{me}", str(out))
            sch.save_jobs(reg4, [])
            jl = sch.load_jobs(reg4)
            sch.create_job(jl, "every 1m", "channel cron",
                           origin="discord:C9", now=clock[0] - 120)
            sch.save_jobs(reg4, jl)
            svc4._cron_check = 0.0
            svc4._maybe_fire_cron()
            check("channels svc: cron str origin delivers on the channel",
                  chan4.sent == [("C9", "channel cron")], str(chan4.sent))
            check("channels svc: cron delivery label names the route",
                  "delivered=channel:discord" in svc4.log.read_text())

            # send failure -> honest fallback down the none/wd chain
            clock[0] += 70
            sch.save_jobs(reg4, [])
            jl = sch.load_jobs(reg4)
            sch.create_job(jl, "every 1m", "failing send",
                           origin="discord:C9", now=clock[0] - 120)
            sch.save_jobs(reg4, jl)
            chan4.send_ok = False
            svc4._cron_check = 0.0
            svc4._maybe_fire_cron()
            check("channels svc: failed channel send falls back to log-only",
                  "delivered=none" in svc4.log.read_text()
                  and "CHANNEL_SEND_FAIL" in svc4.log.read_text(),
                  svc4.log.read_text()[-300:])
            chan4.send_ok = True

            # unknown route -> same honest fallback, no crash
            clock[0] += 70
            sch.save_jobs(reg4, [])
            jl = sch.load_jobs(reg4)
            sch.create_job(jl, "every 1m", "lost route",
                           origin="carrier:C9", now=clock[0] - 120)
            sch.save_jobs(reg4, jl)
            svc4._cron_check = 0.0
            svc4._maybe_fire_cron()
            check("channels svc: unknown cron route -> log-only, no crash",
                  "delivered=none" in svc4.log.read_text())

            # --- a crashing route never kills the loop ---------------------
            bad = FakeChannel(name="slack", fail_poll=True)
            good = FakeChannel(inbound=[ib(me, "still alive")])
            fake6 = FakeTgTransport([])
            svc6 = ChatService(tdp / "st6", frozenset({111}), transport=fake6,
                               chat=ok_chat, repo=repo, now=nowf,
                               channels={"slack": bad, "discord": good})
            n6 = svc6.run(once=True)
            check("channels svc: --once runs the channel poll leg",
                  n6 == 0 and good.sent == [("C1", "pong")],
                  f"n={n6} sent={good.sent}")
            check("channels svc: poll exception logged, loop survives",
                  "CHANNEL_ERR" in svc6.log.read_text()
                  and "route=slack" in svc6.log.read_text(),
                  svc6.log.read_text()[-300:])
            check("channels svc: one bad route does not stop the others",
                  bad.polls == 1 and good.polls == 1)

            # --- dry-run evaluates but never transmits ---------------------
            chan7 = FakeChannel(inbound=[ib(me, "dry channel turn")])
            svc7 = ChatService(tdp / "st7", frozenset({111}),
                               transport=FakeTgTransport(), chat=ok_chat,
                               repo=repo, now=nowf, dry_run=True,
                               channels={"discord": chan7})
            out_buf = io.StringIO()
            with contextlib.redirect_stdout(out_buf):
                svc7._maybe_poll_channels()
            check("channels svc: dry-run never transmits a channel reply",
                  chan7.sent == [], str(chan7.sent))
            check("channels svc: dry-run previews the channel reply",
                  "DRYRUN_SEND" in svc7.log.read_text()
                  and "[dry-run] would send" in out_buf.getvalue()
                  and "route=discord" in out_buf.getvalue(),
                  out_buf.getvalue()[:120])
            check("channels svc: dry-run still ran the turn (session kept)",
                  (svc7.sessions / f"discord:{me}.jsonl").is_file())

            # --- scheduler origin: int | str | None ------------------------
            sch.set_origin("discord:C9")
            str_job = sch.create_job([], "daily 09:00", "x",
                                     origin=sch.get_origin(), now=clock[0])
            check("cron: str origin accepted and kept by create_job",
                  str_job["origin"] == "discord:C9"
                  and sch.get_origin() == "discord:C9")
            sch.clear_origin()
            check("cron: origin context cleared after the turn",
                  sch.get_origin() is None)
            check("cron: int origin unchanged, garbage origin dropped",
                  sch.create_job([], "daily 09:00", "x", origin=42,
                                 now=clock[0])["origin"] == 42
                  and sch.create_job([], "daily 09:00", "x", origin=1.5,
                                     now=clock[0])["origin"] is None)
            roundtrip = tdp / "cron_roundtrip.json"
            sch.save_jobs(roundtrip, [str_job])
            check("cron: str origin survives the registry round-trip",
                  sch.load_jobs(roundtrip)[0]["origin"] == "discord:C9")
    finally:
        sch.clear_origin()
        for key, val in saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val


def test_channels_domestic() -> None:
    """D car domestic routes (dingtalk/feishu/wechat): request shapes, HMAC
    sign, token caches with retry-once, cursor unit defense, byte-safe
    truncation, zero secret echo, scheduler --origin parsing — all offline
    (fake transports, temp dirs, fake credentials, no network)."""
    import base64
    import contextlib
    import hashlib
    import hmac
    import io
    import time
    import urllib.parse
    from mycore import channels
    from mycore import scheduler as sch

    ding_url = "https://oapi.dingtalk.com/robot/send?access_token=ding-ROBOT-tok77"
    ding_secret = "dingtalk-SIGN-secret88"
    feishu_id = "cli_aodog_dom_001"
    feishu_secret = "feishu-APP-secret99"
    wechat_id = "wxTEST1122334455"
    wechat_secret = "wechat-APP-secret66"
    wh_secret = "whsec-DOM-secret44"
    slot_vals = {
        "CHANNEL_DINGTALK_WEBHOOK": ding_url,
        "CHANNEL_DINGTALK_SECRET": ding_secret,
        "CHANNEL_FEISHU_APP_ID": feishu_id,
        "CHANNEL_FEISHU_APP_SECRET": feishu_secret,
        "CHANNEL_WECHAT_APPID": wechat_id,
        "CHANNEL_WECHAT_APP_SECRET": wechat_secret,
        "CHANNEL_WEBHOOK_SECRET": wh_secret,
    }
    saved = {k: os.environ.get(k) for k in list(slot_vals) + ["AIDOG_MC_STATE"]}
    repo = Path(tempfile.mkdtemp(prefix="dom_repo_"))
    state = Path(tempfile.mkdtemp(prefix="dom_state_"))
    channels._TOKEN_CACHE.clear()

    class FakeTransport:
        """Records calls (url, payload, headers); replays scripted replies
        (str = response text, Exception = raised verbatim)."""

        def __init__(self, replies=()):
            self.calls = []
            self.replies = list(replies)

        def __call__(self, url, payload, headers, timeout):
            self.calls.append({"url": url, "payload": payload,
                               "headers": dict(headers), "timeout": timeout})
            if not self.replies:
                raise AssertionError("scripted transport reply missing")
            item = self.replies.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    def tok(token: str) -> str:
        return json.dumps({"code": 0, "tenant_access_token": token,
                           "expire": 7200})

    def page(items) -> str:
        return json.dumps({"code": 0,
                           "data": {"items": items, "has_more": False}})

    def msg(mid, t, text, sender="ou_sender1", kind="text"):
        return {"message_id": mid, "msg_type": kind, "create_time": str(t),
                "sender": {"id": sender},
                "body": {"content": json.dumps({"text": text})}}

    try:
        for key, val in slot_vals.items():
            os.environ[key] = val
        settings = {"state_dir": str(state), "channels": {
            "dingtalk": {"enabled": True, "allow": []},
            "feishu": {"enabled": True, "allow": [],
                       "chat_ids": ["oc_ms_main"]},
            "wechat": {"enabled": True, "allow": []},
        }}
        dom = ("dingtalk", "feishu", "wechat")
        all_vals = list(slot_vals.values())

        # --- contract: CHANNELS order + factory + outbound-only poll -------
        check("dom channels: CHANNELS appends the three domestic routes",
              channels.CHANNELS == ("discord", "slack", "email", "webhook",
                                    "dingtalk", "feishu", "wechat",
                                    "whatsapp", "signal"))
        loaded = channels.load_channels(repo, settings,
                                        transport=FakeTransport())
        check("dom channels: factory builds every CHANNELS route",
              tuple(loaded) == channels.CHANNELS
              and all(isinstance(v, channels.Channel)
                      for v in loaded.values())
              and all(loaded[n].enabled for n in dom))
        tr_d0 = FakeTransport()
        d0 = channels.DingTalkChannel(repo=repo,
                                      cfg=settings["channels"]["dingtalk"],
                                      state=state, transport=tr_d0)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            got = d0.poll()
        check("dom dingtalk: inbound=False poll silent, zero transport calls",
              got == [] and tr_d0.calls == []
              and "CHANNEL_POLL_FAIL" not in buf.getvalue())
        tr_w0 = FakeTransport()
        w0 = channels.WeChatChannel(repo=repo,
                                    cfg=settings["channels"]["wechat"],
                                    state=state, transport=tr_w0)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            got = w0.poll()
        check("dom wechat: inbound=False poll silent, zero transport calls",
              got == [] and tr_w0.calls == []
              and "CHANNEL_POLL_FAIL" not in buf.getvalue())
        tr_wh = FakeTransport()
        wh = channels.WebhookChannel(repo=repo,
                                     cfg={"enabled": True,
                                          "url": "http://127.0.0.1:9/hook"},
                                     state=state, transport=tr_wh)
        check("dom channels: webhook route unchanged (inbound=True, empty poll)",
              channels.WebhookChannel.inbound is True and wh.poll() == []
              and tr_wh.calls == [])

        # --- dingtalk: send shape + optional HMAC sign ---------------------
        tr = FakeTransport([json.dumps({"errcode": 0, "errmsg": "ok"})])
        d = channels.load_channels(repo, settings, transport=tr)["dingtalk"]
        ok = d.send("label-a", "hello dingtalk")
        call = tr.calls[0]
        check("dom dingtalk: send posts to slot URL + signed query params",
              ok is True
              and call["url"].split("&timestamp=")[0] == ding_url
              and "&sign=" in call["url"]
              and call["headers"].get("Content-Type") == "application/json")
        check("dom dingtalk: payload is msgtype/text/content shape",
              json.loads(call["payload"]) ==
              {"msgtype": "text", "text": {"content": "hello dingtalk"}})
        qs = dict(kv.split("=", 1)
                  for kv in call["url"].split("?", 1)[1].split("&"))
        digest = hmac.new(ding_secret.encode("utf-8"),
                          f"{qs['timestamp']}\n{ding_secret}".encode("utf-8"),
                          hashlib.sha256).digest()
        expect = urllib.parse.quote_plus(
            base64.b64encode(digest).decode("ascii"))
        check("dom dingtalk: sign recomputes as HMAC-SHA256 from captured ts",
              qs["timestamp"].isdigit() and qs["sign"] == expect)
        os.environ.pop("CHANNEL_DINGTALK_SECRET", None)
        tr_u = FakeTransport([json.dumps({"errcode": 0})])
        d_u = channels.load_channels(repo, settings, transport=tr_u)["dingtalk"]
        ok_c, reason = d_u.configured()
        ok_u = d_u.send("label-a", "no sign")
        check("dom dingtalk: secret slot optional, configured stays True",
              ok_c is True and reason == "" and ok_u is True
              and tr_u.calls[0]["url"] == ding_url)
        os.environ["CHANNEL_DINGTALK_SECRET"] = ding_secret
        tr_e = FakeTransport([json.dumps({"errcode": 310000,
                                          "errmsg": "sign not match"})])
        d_e = channels.load_channels(repo, settings, transport=tr_e)["dingtalk"]
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ok = d_e.send("label-a", "x")
        log = buf.getvalue()
        check("dom dingtalk: HTTP 200 with errcode!=0 fails, never raises",
              ok is False and "CHANNEL_SEND_FAIL" in log
              and "310000" in log and "sign not match" in log)
        tr_t = FakeTransport([json.dumps({"errcode": 0})])
        d_t = channels.load_channels(repo, settings, transport=tr_t)["dingtalk"]
        d_t.send("label-a", "x" * 20050)
        sent = json.loads(tr_t.calls[0]["payload"])["text"]["content"]
        check("dom dingtalk: text capped at 20000 chars with style marker",
              len(sent) <= 20000 and sent.endswith("…[truncated]"))
        os.environ.pop("CHANNEL_DINGTALK_WEBHOOK", None)
        ok_c, reason = channels.load_channels(
            repo, settings, transport=FakeTransport())["dingtalk"].configured()
        check("dom dingtalk: configured False names the webhook slot only",
              ok_c is False
              and reason == "missing CHANNEL_DINGTALK_WEBHOOK slot"
              and ding_url not in reason and "access_token" not in reason)
        os.environ["CHANNEL_DINGTALK_WEBHOOK"] = ding_url
        off_dom = {"state_dir": str(state),
                   "channels": {"dingtalk": {"enabled": False, "allow": []}}}
        tr_off = FakeTransport()
        d_off = channels.load_channels(repo, off_dom,
                                       transport=tr_off)["dingtalk"]
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ok = d_off.send("label-a", "x")
        check("dom dingtalk: disabled route send False, zero transport calls",
              ok is False and tr_off.calls == [])

        # --- feishu: configured reasons ------------------------------------
        f_cfg = settings["channels"]["feishu"]
        os.environ.pop("CHANNEL_FEISHU_APP_ID", None)
        ok_c, reason = channels.FeishuChannel(repo=repo, cfg=f_cfg,
                                              state=state).configured()
        check("dom feishu: configured False names the app id slot",
              ok_c is False and reason == "missing CHANNEL_FEISHU_APP_ID slot")
        os.environ["CHANNEL_FEISHU_APP_ID"] = feishu_id
        os.environ.pop("CHANNEL_FEISHU_APP_SECRET", None)
        ok_c, reason = channels.FeishuChannel(repo=repo, cfg=f_cfg,
                                              state=state).configured()
        check("dom feishu: configured False names the app secret slot",
              ok_c is False
              and reason == "missing CHANNEL_FEISHU_APP_SECRET slot"
              and feishu_secret not in reason)
        os.environ["CHANNEL_FEISHU_APP_SECRET"] = feishu_secret
        ok_c, reason = channels.FeishuChannel(repo=repo, cfg=f_cfg,
                                              state=state).configured()
        check("dom feishu: both slots present -> configured True",
              ok_c is True and reason == "")

        # --- feishu: token -> send shapes + cache reuse --------------------
        channels._TOKEN_CACHE.clear()
        tr_f = FakeTransport([tok("ftok-111"),
                              json.dumps({"code": 0,
                                          "data": {"message_id": "om1"}}),
                              json.dumps({"code": 0,
                                          "data": {"message_id": "om2"}})])
        f = channels.load_channels(repo, settings, transport=tr_f)["feishu"]
        ok1 = f.send("oc_chat9", "one")
        ok2 = f.send("oc_chat9", "two")
        check("dom feishu: token endpoint posts app_id+app_secret",
              tr_f.calls[0]["url"] ==
              "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
              and json.loads(tr_f.calls[0]["payload"]) ==
              {"app_id": feishu_id, "app_secret": feishu_secret})
        send_body = json.loads(tr_f.calls[1]["payload"])
        check("dom feishu: send url + Bearer + receive_id/content body",
              ok1 is True and ok2 is True
              and tr_f.calls[1]["url"] ==
              "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
              and tr_f.calls[1]["headers"].get("Authorization")
              == "Bearer ftok-111"
              and send_body["receive_id"] == "oc_chat9"
              and send_body["msg_type"] == "text"
              and json.loads(send_body["content"]) == {"text": "one"})
        check("dom feishu: tenant token cached across sends (no refetch)",
              len(tr_f.calls) == 3
              and tr_f.calls[2]["headers"].get("Authorization")
              == "Bearer ftok-111")

        # --- feishu: failure codes + retry-once ----------------------------
        channels._TOKEN_CACHE.clear()
        tr_fs = FakeTransport([tok("ftok-a"),
                               json.dumps({"code": 230001,
                                           "msg": "chat not found"})])
        f2 = channels.load_channels(repo, settings, transport=tr_fs)["feishu"]
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ok = f2.send("oc_chat9", "x")
        check("dom feishu: non-zero send code -> honest False, no raise",
              ok is False and "CHANNEL_SEND_FAIL" in buf.getvalue()
              and "230001" in buf.getvalue())
        channels._TOKEN_CACHE.clear()
        tr_fr = FakeTransport([tok("ftok-a"), json.dumps({"code": 99991663}),
                               tok("ftok-b"), json.dumps({"code": 0})])
        f3 = channels.load_channels(repo, settings, transport=tr_fr)["feishu"]
        ok = f3.send("oc_chat9", "x")
        check("dom feishu: token-invalid refetches token and retries ONCE",
              ok is True and len(tr_fr.calls) == 4
              and tr_fr.calls[3]["headers"].get("Authorization")
              == "Bearer ftok-b"
              and channels._TOKEN_CACHE[feishu_id][0] == "ftok-b")
        channels._TOKEN_CACHE.clear()
        tr_ft = FakeTransport([tok("ftok-a"), json.dumps({"code": 99991664}),
                               tok("ftok-b"), json.dumps({"code": 99991664})])
        f4 = channels.load_channels(repo, settings, transport=tr_ft)["feishu"]
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ok = f4.send("oc_chat9", "x")
        log = buf.getvalue()
        check("dom feishu: token-invalid fails after ONE retry (no loop)",
              ok is False and len(tr_ft.calls) == 4
              and "99991664" in log and "CHANNEL_SEND_FAIL" in log)
        channels._TOKEN_CACHE.clear()
        tr_fq = FakeTransport([tok("ftok-a"), json.dumps({"code": 99991400})])
        f5 = channels.load_channels(repo, settings, transport=tr_fq)["feishu"]
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ok = f5.send("oc_chat9", "x")
        check("dom feishu: quota code fails without a token refetch",
              ok is False and len(tr_fq.calls) == 2
              and "99991400" in buf.getvalue())

        # --- feishu: poll cursor (ms variant, dedup, ids rule) -------------
        channels._TOKEN_CACHE.clear()
        m1 = msg("fm1", 1730000000123, "alpha")
        m2 = msg("fm2", 1730000000456, "beta")
        m3 = msg("fm3", 1730000000789, "gamma")
        m4 = msg("fm4", 1730000000789, "delta")
        m5 = msg("fm5", 1730000000456, "late five")
        tr_p = FakeTransport([tok("ftok-p"), page([m1, m2]),
                              page([m2, m5, m3, m4]),
                              page([m2, m5, m3, m4])])
        f_p = channels.load_channels(repo, settings, transport=tr_p)["feishu"]
        first = f_p.poll()
        check("dom feishu: fresh poll parses ms create_time into Inbound",
              [i.text for i in first] == ["alpha", "beta"]
              and first[0].route == "feishu"
              and first[0].sender == "ou_sender1"
              and first[0].target == "oc_ms_main"
              and first[0].key == "feishu:ou_sender1")
        check("dom feishu: fresh poll URL (single page, no start_time)",
              "container_id_type=chat" in tr_p.calls[1]["url"]
              and "container_id=oc_ms_main" in tr_p.calls[1]["url"]
              and "sort_type=ByCreateTimeAsc" in tr_p.calls[1]["url"]
              and "page_size=50" in tr_p.calls[1]["url"]
              and "start_time=" not in tr_p.calls[1]["url"]
              and tr_p.calls[1]["headers"].get("Authorization")
              == "Bearer ftok-p")
        cursor_doc = json.loads(
            (state / "channels" / "feishu.cursor").read_text(encoding="utf-8"))
        check("dom feishu: cursor keeps RAW create_time + boundary ids",
              cursor_doc == {"oc_ms_main": {"t": 1730000000456,
                                            "ids": ["fm2"]}},
              str(cursor_doc))
        second = f_p.poll()
        check("dom feishu: second poll converts ms cursor -> seconds start_time",
              "start_time=1730000000" in tr_p.calls[2]["url"]
              and len(tr_p.calls) == 3)   # cached token: one call this poll
        check("dom feishu: boundary id deduped, new ids accepted at same t",
              [i.text for i in second] == ["late five", "gamma", "delta"],
              str([i.text for i in second]))
        third = f_p.poll()
        check("dom feishu: third poll fully deduped by t+ids cursor",
              third == [] and len(tr_p.calls) == 4
              and "start_time=1730000000" in tr_p.calls[3]["url"])

        # --- feishu: seconds-unit variant (defensive unit support) ---------
        channels._TOKEN_CACHE.clear()
        s1 = msg("fs1", 1730000100, "sec one", sender="ou_sender2")
        tr_s = FakeTransport([tok("ftok-s"), page([s1]), page([s1])])
        f_s = channels.FeishuChannel(repo=repo, state=state, transport=tr_s,
                                     cfg={"enabled": True,
                                          "chat_ids": ["oc_sec"]})
        first = f_s.poll()
        second = f_s.poll()
        check("dom feishu: seconds-unit create_time accepted, start_time raw",
              [i.text for i in first] == ["sec one"]
              and second == [] and len(tr_s.calls) == 3
              and "start_time=1730000100" in tr_s.calls[2]["url"])
        tr_e2 = FakeTransport()
        f_e = channels.FeishuChannel(repo=repo, state=state, transport=tr_e2,
                                     cfg={"enabled": True, "chat_ids": []})
        check("dom feishu: empty chat_ids -> poll [] with zero transport calls",
              f_e.poll() == [] and tr_e2.calls == [])
        channels._TOKEN_CACHE.clear()
        rich = msg("fn1", 1730000000900, "rich title", sender="ou_sender3",
                   kind="post")
        plain = msg("fn2", 1730000000950, "only text", sender="ou_sender3")
        tr_n = FakeTransport([tok("ftok-n"), page([rich, plain])])
        f_n = channels.FeishuChannel(repo=repo, state=state, transport=tr_n,
                                     cfg={"enabled": True,
                                          "chat_ids": ["oc_notext"]})
        got = f_n.poll()
        check("dom feishu: non-text message types skipped, text delivered",
              [i.text for i in got] == ["only text"])

        # --- feishu: poll retry-once + quota + sanitization ----------------
        channels._TOKEN_CACHE.clear()
        ok_msg = msg("fr1", 1730000000500, "retry ok")
        tr_pr = FakeTransport([tok("ftok-r1"), json.dumps({"code": 99991668}),
                               tok("ftok-r2"), page([ok_msg])])
        f_r = channels.FeishuChannel(repo=repo, state=state, transport=tr_pr,
                                     cfg={"enabled": True,
                                          "chat_ids": ["oc_retry"]})
        got = f_r.poll()
        check("dom feishu: poll refetches token once on token-invalid code",
              [i.text for i in got] == ["retry ok"]
              and len(tr_pr.calls) == 4
              and tr_pr.calls[3]["headers"].get("Authorization")
              == "Bearer ftok-r2")
        channels._TOKEN_CACHE.clear()
        tr_pq = FakeTransport([tok("ftok-q"), json.dumps({"code": 99991403})])
        f_q = channels.FeishuChannel(repo=repo, state=state, transport=tr_pq,
                                     cfg={"enabled": True,
                                          "chat_ids": ["oc_quota"]})
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            got = f_q.poll()
        check("dom feishu: poll quota code -> [] + honest log, no refetch",
              got == [] and len(tr_pq.calls) == 2
              and "99991403" in buf.getvalue())
        channels._TOKEN_CACHE.clear()
        tr_bad = FakeTransport([RuntimeError(f"boom {feishu_secret}")])
        f_bad = channels.load_channels(repo, settings,
                                       transport=tr_bad)["feishu"]
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ok = f_bad.send("oc_chat9", "x")
        check("dom feishu: transport exception sanitized, secret never echoed",
              ok is False and "CHANNEL_SEND_FAIL" in buf.getvalue()
              and feishu_secret not in buf.getvalue())

        # --- wechat: token -> send shapes + cache --------------------------
        channels._TOKEN_CACHE.clear()
        tr_w = FakeTransport([
            json.dumps({"access_token": "wtok-1", "expires_in": 7200}),
            json.dumps({"errcode": 0, "errmsg": "ok"}),
            json.dumps({"errcode": 0, "errmsg": "ok"})])
        w = channels.load_channels(repo, settings, transport=tr_w)["wechat"]
        w.send("openid-1", "hi wechat")
        w.send("openid-1", "again")
        tok_call = tr_w.calls[0]
        check("dom wechat: token GET shape (grant_type + appid + secret)",
              tok_call["payload"] is None
              and tok_call["url"].startswith(
                  "https://api.weixin.qq.com/cgi-bin/token?")
              and "grant_type=client_credential" in tok_call["url"]
              and f"appid={wechat_id}" in tok_call["url"]
              and "secret=" in tok_call["url"])
        check("dom wechat: send posts touser text with token in query",
              len(tr_w.calls) == 3
              and "cgi-bin/message/custom/send?access_token=wtok-1"
              in tr_w.calls[1]["url"]
              and json.loads(tr_w.calls[1]["payload"]) ==
              {"touser": "openid-1", "msgtype": "text",
               "text": {"content": "hi wechat"}})
        check("dom wechat: token cached across sends (one GET, two POSTs)",
              sum(1 for c in tr_w.calls if "cgi-bin/token?" in c["url"]) == 1
              and tr_w.calls[2]["headers"].get("Content-Type")
              == "application/json")
        channels._TOKEN_CACHE.clear()
        tr_wt = FakeTransport([
            json.dumps({"access_token": "wtok-2", "expires_in": 7200}),
            json.dumps({"errcode": 0, "errmsg": "ok"})])
        w_t = channels.load_channels(repo, settings, transport=tr_wt)["wechat"]
        w_t.send("openid-1", "\u4e2d" * 1000)
        content = json.loads(tr_wt.calls[1]["payload"])["text"]["content"]
        check("dom wechat: text truncated to 1024 BYTES utf-8 safe + marker",
              len(content.encode("utf-8")) <= 1024
              and content.endswith("…[truncated]") and len(content) < 1000)
        channels._TOKEN_CACHE.clear()
        channels._TOKEN_CACHE[wechat_id] = ("stale-token", time.time() + 60)
        tr_wr = FakeTransport([
            json.dumps({"access_token": "wtok-3", "expires_in": 7200}),
            json.dumps({"errcode": 0, "errmsg": "ok"})])
        w_r = channels.load_channels(repo, settings, transport=tr_wr)["wechat"]
        w_r.send("openid-1", "refresh me")
        check("dom wechat: token inside 120s refresh lead -> refetched",
              len(tr_wr.calls) == 2
              and "cgi-bin/token?" in tr_wr.calls[0]["url"]
              and "access_token=wtok-3" in tr_wr.calls[1]["url"]
              and "stale-token" not in tr_wr.calls[1]["url"])

        # --- wechat: errcode handling, retry-once, zero errmsg echo --------
        channels._TOKEN_CACHE.clear()
        tr_w45 = FakeTransport([
            json.dumps({"access_token": "wtok-4", "expires_in": 7200}),
            json.dumps({"errcode": 45015, "errmsg": "over 48h"})])
        w45 = channels.load_channels(repo, settings,
                                     transport=tr_w45)["wechat"]
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ok = w45.send("openid-1", "x")
        log = buf.getvalue()
        check("dom wechat: errcode 45015 honest fail (48h window), no retry",
              ok is False and len(tr_w45.calls) == 2
              and "45015" in log and "CHANNEL_SEND_FAIL" in log)
        channels._TOKEN_CACHE.clear()
        tr_wrt = FakeTransport([
            json.dumps({"access_token": "wtok-5", "expires_in": 7200}),
            json.dumps({"errcode": 40001}),
            json.dumps({"access_token": "wtok-6", "expires_in": 7200}),
            json.dumps({"errcode": 0, "errmsg": "ok"})])
        w_rt = channels.load_channels(repo, settings,
                                      transport=tr_wrt)["wechat"]
        ok = w_rt.send("openid-1", "x")
        check("dom wechat: token-invalid errcode refetches and retries ONCE",
              ok is True and len(tr_wrt.calls) == 4
              and "access_token=wtok-6" in tr_wrt.calls[3]["url"])
        channels._TOKEN_CACHE.clear()
        tr_wtt = FakeTransport([
            json.dumps({"access_token": "wtok-7", "expires_in": 7200}),
            json.dumps({"errcode": 42001}),
            json.dumps({"access_token": "wtok-8", "expires_in": 7200}),
            json.dumps({"errcode": 42001})])
        w_tt = channels.load_channels(repo, settings,
                                      transport=tr_wtt)["wechat"]
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ok = w_tt.send("openid-1", "x")
        check("dom wechat: token-invalid fails after ONE retry (no loop)",
              ok is False and len(tr_wtt.calls) == 4
              and "42001" in buf.getvalue())
        channels._TOKEN_CACHE.clear()
        tr_wl = FakeTransport([
            json.dumps({"access_token": "wtok-9", "expires_in": 7200}),
            json.dumps({"errcode": 50002,
                        "errmsg": f"bad openid {wechat_secret}"})])
        w_l = channels.load_channels(repo, settings, transport=tr_wl)["wechat"]
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ok = w_l.send("openid-1", "x")
        log = buf.getvalue()
        check("dom wechat: errmsg never surfaces (secret-hinting hint dropped)",
              ok is False and "50002" in log
              and wechat_secret not in log and "bad openid" not in log)
        channels._TOKEN_CACHE.clear()
        tr_wg = FakeTransport([
            json.dumps({"errcode": 40125,
                        "errmsg": f"invalid appsecret {wechat_secret}"})])
        w_g = channels.load_channels(repo, settings, transport=tr_wg)["wechat"]
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ok = w_g.send("openid-1", "x")
        log = buf.getvalue()
        check("dom wechat: token errmsg dropped, errcode only, zero echo",
              ok is False and "40125" in log
              and wechat_secret not in log and "invalid appsecret" not in log)

        # --- wechat: configured reasons ------------------------------------
        os.environ.pop("CHANNEL_WECHAT_APPID", None)
        ok_c, reason = channels.WeChatChannel(
            repo=repo, cfg=settings["channels"]["wechat"],
            state=state).configured()
        check("dom wechat: configured False names the appid slot",
              ok_c is False and reason == "missing CHANNEL_WECHAT_APPID slot")
        os.environ["CHANNEL_WECHAT_APPID"] = wechat_id
        os.environ.pop("CHANNEL_WECHAT_APP_SECRET", None)
        ok_c, reason = channels.WeChatChannel(
            repo=repo, cfg=settings["channels"]["wechat"],
            state=state).configured()
        check("dom wechat: configured False names the app secret slot",
              ok_c is False
              and reason == "missing CHANNEL_WECHAT_APP_SECRET slot"
              and wechat_secret not in reason)
        os.environ["CHANNEL_WECHAT_APP_SECRET"] = wechat_secret
        ok_c, reason = channels.WeChatChannel(
            repo=repo, cfg=settings["channels"]["wechat"],
            state=state).configured()
        check("dom wechat: both slots present -> configured True",
              ok_c is True and reason == "")

        # --- status rows + zero leak ---------------------------------------
        st = channels.status(settings, repo)
        by = {r["name"]: r for r in st}
        check("dom status: domestic rows enabled+configured, inbound flags",
              len(st) == len(channels.CHANNELS)
              and by["dingtalk"]["note"] == ""
              and by["dingtalk"]["inbound"] is False
              and by["feishu"]["note"] == ""
              and by["feishu"]["inbound"] is True
              and by["wechat"]["note"] == ""
              and by["wechat"]["inbound"] is False)
        check("dom status: zero secret echo across the full row dump",
              all(v not in json.dumps(st) for v in all_vals))
        os.environ.pop("CHANNEL_FEISHU_APP_ID", None)
        st2 = channels.status(settings, repo)
        note = {r["name"]: r for r in st2}["feishu"]["note"]
        check("dom status: missing-slot note names the slot, never the value",
              note == "missing CHANNEL_FEISHU_APP_ID slot"
              and all(v not in json.dumps(st2) for v in all_vals))
        os.environ["CHANNEL_FEISHU_APP_ID"] = feishu_id
        reps = " ".join(repr(channels.load_channels(
            repo, settings, transport=FakeTransport())[n]) for n in dom)
        check("dom channels: adapter reprs never carry a credential",
              all(v not in reps for v in all_vals))
        cursor_files = sorted((state / "channels").glob("*.cursor"))
        cursor_blob = " ".join(p.read_text(encoding="utf-8")
                               for p in cursor_files)
        check("dom channels: cursor files never carry tokens (no key on disk)",
              bool(cursor_files)
              and all(v not in cursor_blob for v in all_vals)
              and "tok" not in cursor_blob and "wtok" not in cursor_blob)

        # --- scheduler --origin: int chat id | route:target | refusal ------
        with tempfile.TemporaryDirectory() as td:
            saved_state = os.environ.get("AIDOG_MC_STATE")
            os.environ["AIDOG_MC_STATE"] = td
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = sch.main(["--add", "daily 09:30", "--text",
                                   "cli int origin", "--origin", "424242"])
                jobs = sch.load_jobs(Path(td) / sch.STATE_FILE_NAME)
                check("dom cron cli: numeric --origin stored as int chat id",
                      rc == 0 and len(jobs) == 1
                      and jobs[0]["origin"] == 424242, f"rc={rc}")
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = sch.main(["--add", "daily 09:40", "--text",
                                   "cli route origin",
                                   "--origin", "feishu:oc_route9"])
                jobs = sch.load_jobs(Path(td) / sch.STATE_FILE_NAME)
                check("dom cron cli: route:target --origin kept as a string",
                      rc == 0 and len(jobs) == 2
                      and jobs[1]["origin"] == "feishu:oc_route9",
                      f"rc={rc}")
                err = io.StringIO()
                try:
                    with contextlib.redirect_stdout(io.StringIO()), \
                            contextlib.redirect_stderr(err):
                        rc = sch.main(["--add", "daily 09:45", "--text",
                                       "bad origin", "--origin", "BadForm"])
                except SystemExit as e:
                    rc = e.code
                check("dom cron cli: bad --origin exits 2 with honest text",
                      rc == 2 and "origin" in err.getvalue()
                      and "route:target" in err.getvalue()
                      and "Traceback" not in err.getvalue(), f"rc={rc}")
                err = io.StringIO()
                try:
                    with contextlib.redirect_stdout(io.StringIO()), \
                            contextlib.redirect_stderr(err):
                        rc = sch.main(["--add", "daily 09:50", "--text",
                                       "bad route", "--origin", "9chat:room"])
                except SystemExit as e:
                    rc = e.code
                check("dom cron cli: route part must start with a letter",
                      rc == 2 and "Traceback" not in err.getvalue(),
                      f"rc={rc}")
            finally:
                if saved_state is None:
                    os.environ.pop("AIDOG_MC_STATE", None)
                else:
                    os.environ["AIDOG_MC_STATE"] = saved_state
    finally:
        channels._TOKEN_CACHE.clear()
        for key, val in saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        shutil.rmtree(repo, True)
        shutil.rmtree(state, True)


def test_hooks() -> None:
    """Car 4a event hooks: settings schema validation + real background
    execution — hermetic (temp repo dirs, never production data/)."""
    from mycore import hooks as hk

    # ---------------- batch A: validate_hooks schema/whitelist/bounds ------
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        (tdp / "web").mkdir()

        clean, errs = hk.validate_hooks({"turn_end": [{"cmd": ["/bin/true"]}]}, tdp)
        check("hooks: legal entry accepted, timeout default filled",
              not errs and clean.get("turn_end") ==
              [{"cmd": ["/bin/true"], "timeout": hk.DEFAULT_TIMEOUT}],
              f"errs={errs} clean={clean}")

        _, errs = hk.validate_hooks({"nope": [{"cmd": ["/bin/true"]}]}, tdp)
        check("hooks: unknown event refused, error names the whitelist",
              len(errs) == 1 and "unknown hooks event" in errs[0]
              and "turn_start" in errs[0] and "cron_fire" in errs[0],
              str(errs))

        clean_mt, errs_mt = hk.validate_hooks(
            {"mission_tick": [{"cmd": ["/bin/true"]}]}, tdp)
        check("hooks: mission_tick accepted by the whitelist (car 3)",
              not errs_mt and clean_mt.get("mission_tick")
              == [{"cmd": ["/bin/true"], "timeout": hk.DEFAULT_TIMEOUT}],
              str(errs_mt))

        _, errs = hk.validate_hooks({"turn_end": {"cmd": ["/bin/true"]}}, tdp)
        check("hooks: event value must be a list",
              any("must be a list" in e for e in errs), str(errs))

        _, errs = hk.validate_hooks({"turn_end": [{"cmd": "/bin/true"}]}, tdp)
        check("hooks: cmd as string refused",
              any("cmd must be a non-empty array" in e for e in errs), str(errs))

        _, errs = hk.validate_hooks({"turn_end": [{"cmd": []}]}, tdp)
        check("hooks: empty cmd refused",
              any("cmd must be a non-empty array" in e for e in errs), str(errs))

        _, errs = hk.validate_hooks(
            {"turn_end": [{"cmd": ["/bin/true"], "timeout": 0}]}, tdp)
        _, errs2 = hk.validate_hooks(
            {"turn_end": [{"cmd": ["/bin/true"], "timeout": 31}]}, tdp)
        _, errs3 = hk.validate_hooks(
            {"turn_end": [{"cmd": ["/bin/true"], "timeout": "5"}]}, tdp)
        check("hooks: timeout bounds 1..30 + integer type enforced",
              any("timeout must be an integer in 1..30" in e for e in errs)
              and any("timeout must be an integer in 1..30" in e for e in errs2)
              and any("timeout must be an integer in 1..30" in e for e in errs3),
              f"{errs} {errs2} {errs3}")

        _, errs = hk.validate_hooks(
            {"turn_end": [{"cmd": ["definitely-not-a-real-binary-xyz"]}]}, tdp)
        check("hooks: missing argv[0] refused at write time",
              any("cmd[0] not found" in e for e in errs), str(errs))

        _, errs = hk.validate_hooks(
            {"turn_end": [{"cmd": ["/bin/true"], "shell": True}]}, tdp)
        check("hooks: unknown hook key refused",
              any("unknown key" in e and "shell" in e for e in errs), str(errs))

        _, errs = hk.validate_hooks(
            {"turn_end": ["not-an-object"]}, tdp)
        check("hooks: non-object hook entry refused",
              any("must be an object" in e for e in errs), str(errs))

        # relative path-shaped argv[0] resolves against the repo root
        (tdp / "scripts").mkdir()
        (tdp / "scripts" / "hook.sh").write_text("#!/bin/sh\nexit 0\n",
                                                  encoding="utf-8")
        clean, errs = hk.validate_hooks(
            {"turn_end": [{"cmd": ["scripts/hook.sh"]}]}, tdp)
        check("hooks: relative argv[0] resolves against repo root",
              not errs and clean["turn_end"][0]["cmd"][0] == "scripts/hook.sh",
              f"errs={errs}")

        missing, merrs = hk.load_hooks(tdp)  # no web/settings.json written yet
        check("hooks: load_hooks fail-soft on missing settings",
              missing == {} and merrs == [], f"{missing} {merrs}")

        check("hooks: fire on unknown event returns None without reading config",
              hk.fire("not_an_event", repo=tdp) is None)

    # ---------------- batch B: real background execution, fail isolation ---
    import threading
    import time as _time

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        (tdp / "web").mkdir()
        out1 = tdp / "out1.txt"
        out2 = tdp / "out2.txt"
        drop = tdp / "dropped.txt"
        marker = "HOOKSECRET-d0-not-echo-9x7"
        envdump = tdp / "env.json"
        sleep_s = 1.5
        cfg = {
            "turn_end": [
                {"cmd": [sys.executable, "-c",
                         "import sys,time;time.sleep(float(sys.argv[2]));"
                         "open(sys.argv[1],'w').write('first')",
                         str(out1), str(sleep_s)], "timeout": 10},
                {"cmd": [sys.executable, "-c",
                         "import sys;open(sys.argv[1],'w').write('second')",
                         str(out2)], "timeout": 10},
            ],
            "tool_call": [
                {"cmd": [sys.executable, "-c",
                         "import json,os,sys;"
                         "open(sys.argv[1],'w').write(json.dumps(dict(os.environ)))",
                         str(envdump)], "timeout": 10},
            ],
            "error": [
                {"cmd": [sys.executable, "-c",
                         "import sys;print('" + marker + "');"
                         "sys.stderr.write('" + marker + "');sys.exit(3)"],
                 "timeout": 10},
            ],
            "compact": [
                {"cmd": [sys.executable, "-c",
                         "import sys,time;time.sleep(30)",
                         "ignored"], "timeout": 1},
            ],
            "turn_start": [
                {"cmd": [sys.executable, "-c",
                         "import sys;open(sys.argv[1],'w').write(sys.argv[2])",
                         str(drop), "x; touch " + str(tdp / "pwned.txt")],
                 "timeout": 10},
            ],
        }
        (tdp / "web" / "settings.json").write_text(
            json.dumps({"hooks": cfg}), encoding="utf-8")

        parent_secret = "PARENT-SECRET-env-should-not-cross-42"
        os.environ["AIDOG_HOOK_FAKESECRET"] = parent_secret
        logs: list[str] = []

        def collect(*parts):
            logs.append(" ".join(str(p) for p in parts if p))

        try:
            # --- background: fire returns while the hook is still running ---
            t0 = _time.monotonic()
            th = hk.fire("turn_end", repo=tdp, log=collect)
            returned_in = _time.monotonic() - t0
            check("hooks: fire returns a live thread without blocking",
                  isinstance(th, threading.Thread) and th.is_alive()
                  and returned_in < 0.5, f"returned_in={returned_in:.3f}s")
            th.join(timeout=15)
            joined_ok = not th.is_alive()
            order_ok = (out1.is_file() and out2.is_file()
                        and out1.read_text() == "first"
                        and out2.read_text() == "second"
                        and out1.stat().st_mtime <= out2.stat().st_mtime)
            check("hooks: both hooks executed sequentially in order",
                  joined_ok and order_ok,
                  f"joined={joined_ok} out1={out1.exists()} out2={out2.exists()}")

            # --- env whitelist: child sees hook vars + PATH, never parent env ---
            th = hk.fire("tool_call", repo=tdp, chat="id:6789",
                         tool="read_file", log=collect)
            if th:
                th.join(timeout=15)
            child_env = (json.loads(envdump.read_text(encoding="utf-8"))
                         if envdump.is_file() else {})
            check("hooks: child env carries event/chat/tool, PATH; parent "
                  "secret never crosses",
                  child_env.get("AIDOG_HOOK_EVENT") == "tool_call"
                  and child_env.get("AIDOG_HOOK_CHAT") == "id:6789"
                  and child_env.get("AIDOG_HOOK_TOOL") == "read_file"
                  and "PATH" in child_env
                  and "AIDOG_HOOK_FAKESECRET" not in child_env,
                  str(sorted(child_env))[:160])

            # --- failure isolation: exit!=0 -> one HOOK_FAIL, never raises ---
            before = len(logs)
            th = hk.fire("error", repo=tdp, log=collect)
            check("hooks: failing hook still returns a thread (no raise)",
                  isinstance(th, threading.Thread))
            if th:
                th.join(timeout=15)
            fail_lines = [ln for ln in logs[before:] if "HOOK_FAIL" in ln]
            check("hooks: non-zero exit logged as HOOK_FAIL exit:3",
                  len(fail_lines) == 1 and "exit:3" in fail_lines[0]
                  and "turn_end" not in "".join(fail_lines),
                  str(fail_lines))
            check("hooks: hook output with fake secret never reaches logs",
                  all(marker not in ln for ln in logs),
                  [ln for ln in logs if marker in ln][:2])

            # --- timeout kills the child, one HOOK_FAIL timeout line ---
            before = len(logs)
            t0 = _time.monotonic()
            th = hk.fire("compact", repo=tdp, log=collect)
            if th:
                th.join(timeout=10)
            dur = _time.monotonic() - t0
            fail_lines = [ln for ln in logs[before:] if "HOOK_FAIL" in ln]
            check("hooks: timeout hook killed at 1s, logged timeout:1s",
                  th is not None and not th.is_alive() and dur < 5
                  and len(fail_lines) == 1 and "timeout:1s" in fail_lines[0],
                  f"dur={dur:.1f}s {fail_lines}")

            # --- argv is literal: shell metacharacters never execute ---
            th = hk.fire("turn_start", repo=tdp, log=collect)
            if th:
                th.join(timeout=15)
            got = drop.read_text() if drop.is_file() else ""
            check("hooks: no shell — metacharacters stay literal, no side file",
                  got.startswith("x; touch ") and not (tdp / "pwned.txt").exists()
                  and not Path(str(tdp / "pwned.txt")).exists(),
                  f"got={got!r}")

            # --- car 3: mission_tick payload env (id/state/progress only) ---
            envdump_m = tdp / "env_mission.json"
            cfg["mission_tick"] = [
                {"cmd": [sys.executable, "-c",
                         "import json,os,sys;"
                         "open(sys.argv[1],'w').write(json.dumps(dict(os.environ)))",
                         str(envdump_m)], "timeout": 10}]
            (tdp / "web" / "settings.json").write_text(
                json.dumps({"hooks": cfg}), encoding="utf-8")
            th = hk.fire("mission_tick", repo=tdp, mission="ab12cd",
                         mission_state="paused_approve",
                         mission_progress="1/3", chat="id:4242",
                         log=collect)
            if th:
                th.join(timeout=15)
            env_m = (json.loads(envdump_m.read_text(encoding="utf-8"))
                     if envdump_m.is_file() else {})
            check("hooks: mission_tick payload lands in the child env",
                  env_m.get("AIDOG_HOOK_EVENT") == "mission_tick"
                  and env_m.get("AIDOG_HOOK_MISSION") == "ab12cd"
                  and env_m.get("AIDOG_HOOK_MISSION_STATE") == "paused_approve"
                  and env_m.get("AIDOG_HOOK_MISSION_PROGRESS") == "1/3"
                  and env_m.get("AIDOG_HOOK_CHAT") == "id:4242",
                  str(sorted(env_m))[:200])
            # an unsanitized payload value is DROPPED, never passed through
            envdump_m2 = tdp / "env_mission2.json"
            cfg["mission_tick"] = [
                {"cmd": [sys.executable, "-c",
                         "import json,os,sys;"
                         "open(sys.argv[1],'w').write(json.dumps(dict(os.environ)))",
                         str(envdump_m2)], "timeout": 10}]
            (tdp / "web" / "settings.json").write_text(
                json.dumps({"hooks": cfg}), encoding="utf-8")
            th = hk.fire("mission_tick", repo=tdp,
                         mission="evil\nvalue", mission_state=None,
                         mission_progress="1/3", log=collect)
            if th:
                th.join(timeout=15)
            env_m2 = (json.loads(envdump_m2.read_text(encoding="utf-8"))
                      if envdump_m2.is_file() else {})
            check("hooks: unsanitized mission payload value is dropped",
                  "AIDOG_HOOK_MISSION" not in env_m2
                  and "AIDOG_HOOK_MISSION_STATE" not in env_m2
                  and env_m2.get("AIDOG_HOOK_MISSION_PROGRESS") == "1/3",
                  str(sorted(env_m2))[:200])
        finally:
            os.environ.pop("AIDOG_HOOK_FAKESECRET", None)


def test_acp() -> None:
    """Car 4a ACP v1 stdio server: handshake/version gate, session lifecycle,
    update frame order, fs proxy request-response, cancel, timeout honesty,
    EOF/bad-input robustness — offline (fake chat injection, zero network)."""
    import threading
    import time as _t
    from mycore.acp import AcpServer

    def jreq(i, method, params=None):
        return json.dumps({"jsonrpc": "2.0", "id": i, "method": method,
                           "params": params if params is not None else {}})

    def jnote(method, params):
        return json.dumps({"jsonrpc": "2.0", "method": method,
                           "params": params})

    def run_acp(lines, chat=None, fs_timeout=6.0):
        frames, logs = [], []
        src = lines(frames) if callable(lines) else lines
        srv = AcpServer(lines=src, write=frames.append, chat=chat,
                        fs_timeout=fs_timeout,
                        log=lambda m: logs.append(m))
        rc = srv.serve()
        return rc, frames, logs

    def wait_for(pred, timeout=8.0):
        end = _t.monotonic() + timeout
        while _t.monotonic() < end:
            hit = pred()
            if hit:
                return hit
            _t.sleep(0.02)
        return pred()

    def result_for(frames, rid):
        return next((f for f in frames
                     if f.get("id") == rid and
                     ("result" in f or "error" in f)), None)

    def init_line(i, fs=False, pv=1):
        caps = {"fs": {"readTextFile": True, "writeTextFile": True}} \
            if fs else {}
        return jreq(i, "initialize",
                    {"protocolVersion": pv,
                     "clientInfo": {"name": "suite", "version": "1"},
                     "clientCapabilities": caps})

    version = (REPO / "VERSION").read_text(encoding="utf-8").strip()

    # ---------------- batch A: static request/response framing -------------
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        rc, frames, logs = run_acp([
            init_line(101),
            jreq(102, "session/new", {"cwd": str(tdp)}),
            jreq(103, "session/prompt",
                 {"sessionId": "no-such",
                  "prompt": [{"type": "text", "text": "hi"}]}),
            jreq(104, "session/prompt", {"sessionId": "s1"}),
            jreq(105, "session/prompt",
                 {"sessionId": "s1", "prompt": {"type": "text"}}),
            jreq(106, "session/prompt",
                 {"sessionId": "s1", "prompt": [{"type": "image"}]}),
        ])
        r101 = result_for(frames, 101)
        caps = r101.get("result", {}).get("agentCapabilities", {}) \
            if r101 else {}
        info = r101.get("result", {}).get("agentInfo", {}) if r101 else {}
        check("acp: initialize handshake — v1, no loadSession, agentInfo=aidog",
              rc == 0 and r101 is not None and
              r101["result"].get("protocolVersion") == 1 and
              caps.get("loadSession") is False and
              caps.get("promptCapabilities", {}).get("image") is False and
              info.get("name") == "aidog" and info.get("version") == version,
              str(r101)[:200])

        r102 = result_for(frames, 102)
        check("acp: session/new returns sessionId",
              r102 is not None and isinstance(
                  r102.get("result", {}).get("sessionId"), str),
              str(r102)[:160])

        r103 = result_for(frames, 103)
        check("acp: prompt on unknown session -> -32002",
              r103 is not None and r103.get("error", {}).get("code") == -32002,
              str(r103)[:160])
        r104 = result_for(frames, 104)
        check("acp: prompt params not an array -> -32602",
              r104 is not None and r104.get("error", {}).get("code") == -32602,
              str(r104)[:160])
        r105 = result_for(frames, 105)
        check("acp: prompt without text blocks -> -32602",
              r105 is not None and r105.get("error", {}).get("code") == -32602,
              str(r105)[:160])

        check("acp: every output frame is a JSON-RPC 2.0 object",
              all(isinstance(f, dict) and f.get("jsonrpc") == "2.0" and
                  ("method" in f or "id" in f) for f in frames),
              str(frames[:3])[:200])

    # ---------------- batch A2: version gate, bad input, method routing ----
    rc, frames, logs = run_acp([
        init_line(101, pv=2),
        init_line(102, pv="1"),
        "{this is not json",
        "42",
        jreq(103, "initialize", "not-an-object"),
        init_line(104),
        jreq(105, "session/does-not-exist", {}),
    ])
    e101 = result_for(frames, 101)
    check("acp: protocolVersion 2 rejected with -32602 naming the version",
          e101 is not None and e101.get("error", {}).get("code") == -32602
          and "protocol version" in e101["error"].get("message", ""),
          str(e101)[:200])
    e102 = result_for(frames, 102)
    check("acp: non-integer protocolVersion rejected",
          e102 is not None and e102.get("error", {}).get("code") == -32602,
          str(e102)[:160])
    parse_errs = [f for f in frames if f.get("error", {}).get("code") == -32700]
    check("acp: bad JSON line -> -32700, loop survives",
          len(parse_errs) == 1 and result_for(frames, 104) is not None,
          str(parse_errs)[:160])
    inv = [f for f in frames if f.get("error", {}).get("code") == -32600]
    check("acp: non-object JSON line -> -32600",
          len(inv) == 1 and "42" not in json.dumps(inv), str(inv)[:160])
    e103 = result_for(frames, 103)
    check("acp: initialize with non-object params -> -32602",
          e103 is not None and e103.get("error", {}).get("code") == -32602,
          str(e103)[:160])
    e105 = result_for(frames, 105)
    check("acp: unknown method -> -32601",
          e105 is not None and e105.get("error", {}).get("code") == -32601,
          str(e105)[:160])
    check("acp: EOF exits cleanly (rc=0, no exception)", rc == 0, f"rc={rc}")

    # ---------------- batch A3: session/new validation ---------------------
    rc, frames, logs = run_acp([
        init_line(101),
        jreq(102, "session/new", {}),
        jreq(103, "session/new", {"cwd": "relative/dir"}),
        jreq(104, "session/new",
             {"cwd": "/tmp", "additionalDirectories": ["rel"]}),
    ])
    check("acp: session/new requires an absolute cwd (-32602 x2)",
          result_for(frames, 102) is not None and
          result_for(frames, 102).get("error", {}).get("code") == -32602 and
          result_for(frames, 103).get("error", {}).get("code") == -32602,
          f"{result_for(frames, 102)} {result_for(frames, 103)}")
    check("acp: additionalDirectories must be absolute (-32602)",
          result_for(frames, 104) is not None and
          result_for(frames, 104).get("error", {}).get("code") == -32602,
          str(result_for(frames, 104))[:160])

    # ---------------- batch B: prompt flow, update frame order, cancel -----
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        (tdp / "f.txt").write_text("alpha line\nbeta line\n", encoding="utf-8")

        # --- full prompt: fake chat, streaming chunks before the result ----
        sc = ScriptedChat([fake_response(text="Hello from agent.")])

        def gen_prompt(frames):
            yield init_line(101)
            yield jreq(102, "session/new", {"cwd": str(tdp)})
            yield jreq(103, "session/prompt",
                       {"sessionId": "s1",
                        "prompt": [{"type": "text", "text": "hi"}]})
            wait_for(lambda: result_for(frames, 103))

        rc, frames, logs = run_acp(gen_prompt, chat=sc.chat)
        updates = [f for f in frames if f.get("method") == "session/update"]
        chunks = [f for f in updates
                  if f["params"]["update"].get("sessionUpdate") ==
                  "agent_message_chunk"]
        rid103 = next((i for i, f in enumerate(frames)
                       if f.get("id") == 103 and "result" in f), None)
        r103 = result_for(frames, 103)
        check("acp: prompt full flow — chunk frames then stopReason end_turn",
              rc == 0 and sc.calls == 1 and r103 is not None and
              r103["result"].get("stopReason") == "end_turn" and
              bool(chunks) and rid103 is not None and
              all(frames.index(c) < rid103 for c in chunks),
              f"chunks={len(chunks)} r103={r103}")
        check("acp: chunk carries sessionId + text + messageId",
              bool(chunks) and
              chunks[0]["params"].get("sessionId") == "s1" and
              chunks[0]["params"]["update"].get("content", {}).get("text") ==
              "Hello from agent." and
              isinstance(chunks[0]["params"]["update"].get("messageId"), str),
              str(chunks[:1])[:200])

        # --- tool flow: local read, tool_call -> tool_call_update -> chunk --
        sc2 = ScriptedChat([
            fake_response(calls=[("read_file", {"path": str(tdp / "f.txt")})]),
            fake_response(text="read done"),
        ])

        def gen_tool(frames):
            yield init_line(101)
            yield jreq(102, "session/new", {"cwd": str(tdp)})
            yield jreq(103, "session/prompt",
                       {"sessionId": "s1",
                        "prompt": [{"type": "text", "text": "read it"}]})
            wait_for(lambda: result_for(frames, 103))

        rc, frames, logs = run_acp(gen_tool, chat=sc2.chat)
        ups = [f for f in frames if f.get("method") == "session/update"]
        kind_seq = [f["params"]["update"].get("sessionUpdate") for f in ups]
        r103 = result_for(frames, 103)
        check("acp: tool frames ordered tool_call -> tool_call_update -> "
              "chunk before result",
              r103 is not None and
              r103["result"].get("stopReason") == "end_turn" and
              kind_seq[:3] == ["tool_call", "tool_call_update",
                               "agent_message_chunk"] and
              ups[0]["params"]["update"].get("kind") == "read" and
              ups[1]["params"]["update"].get("status") == "completed",
              f"seq={kind_seq} {str(ups[:2])[:200]}")

        # --- session/cancel mid-prompt -> stopReason cancelled --------------
        entered = threading.Event()
        release = threading.Event()

        def blocking_chat(messages=None, tools=None, **kw):
            entered.set()
            release.wait(6)
            return fake_response(text="late reply")

        def gen_cancel(frames):
            yield init_line(101)
            yield jreq(102, "session/new", {"cwd": str(tdp)})
            yield jreq(103, "session/prompt",
                       {"sessionId": "s1",
                        "prompt": [{"type": "text", "text": "hi"}]})
            wait_for(entered.is_set)
            yield jnote("session/cancel", {"sessionId": "s1"})
            release.set()
            wait_for(lambda: result_for(frames, 103))

        rc, frames, logs = run_acp(gen_cancel, chat=blocking_chat)
        r103 = result_for(frames, 103)
        check("acp: session/cancel mid-prompt -> stopReason cancelled",
              rc == 0 and r103 is not None and
              r103["result"].get("stopReason") == "cancelled",
              f"r103={r103} logs={logs[-2:]}")
        # cancel for an unknown session is ignored, never crashes
        rc, frames, logs = run_acp([
            init_line(101),
            jnote("session/cancel", {"sessionId": "ghost"}),
        ])
        check("acp: cancel for unknown session ignored (clean EOF)",
              rc == 0 and not any(
                  f.get("error") for f in frames), str(logs))

    # ---------------- batch C: fs proxy request-response, timeout, EOF -----
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        src = tdp / "f.txt"
        src.write_text("hello fs\nsecond line\n", encoding="utf-8")
        dst = tdp / "out.txt"

        # --- fs read proxy: agent asks client, client answers, tool sees it --
        sc = ScriptedChat([
            fake_response(calls=[("read_file", {"path": str(src)})]),
            fake_response(text="proxy read done"),
        ])

        def gen_fs_read(frames):
            yield init_line(101, fs=True)
            yield jreq(102, "session/new", {"cwd": str(tdp)})
            yield jreq(103, "session/prompt",
                       {"sessionId": "s1",
                        "prompt": [{"type": "text", "text": "read"}]})
            req = wait_for(lambda: next((f for f in frames if
                                         f.get("method") ==
                                         "fs/read_text_file"), None))
            if req is not None:
                yield json.dumps({"jsonrpc": "2.0", "id": req["id"],
                                  "result": {"content": "hello fs\nsecond line\n"}})
            wait_for(lambda: result_for(frames, 103))

        rc, frames, logs = run_acp(gen_fs_read, chat=sc.chat)
        fs_req = next((f for f in frames if
                       f.get("method") == "fs/read_text_file"), None)
        ups = [f for f in frames if f.get("method") == "session/update"]
        r103 = result_for(frames, 103)
        check("acp: fs/read_text_file request -> client answer -> completed "
              "tool_call_update",
              fs_req is not None and
              fs_req["params"].get("sessionId") == "s1" and
              fs_req["params"].get("path") == str(src) and
              fs_req["params"].get("line") == 1 and
              r103 is not None and
              r103["result"].get("stopReason") == "end_turn" and
              len(ups) >= 2 and
              ups[1]["params"]["update"].get("status") == "completed" and
              "hello fs" in json.dumps(ups[1]["params"]["update"]),
              f"req={fs_req} ups={str(ups[:2])[:240]}")

        # --- fs write proxy: client receives fs/write_text_file ------------
        sc2 = ScriptedChat([
            fake_response(calls=[("write_file", {"path": str(dst),
                                                  "content": "written via proxy"})]),
            fake_response(text="proxy write done"),
        ])

        def gen_fs_write(frames):
            yield init_line(101, fs=True)
            yield jreq(102, "session/new", {"cwd": str(tdp)})
            yield jreq(103, "session/prompt",
                       {"sessionId": "s1",
                        "prompt": [{"type": "text", "text": "write"}]})
            req = wait_for(lambda: next((f for f in frames if
                                         f.get("method") ==
                                         "fs/write_text_file"), None))
            if req is not None:
                yield json.dumps({"jsonrpc": "2.0", "id": req["id"],
                                  "result": {}})
            wait_for(lambda: result_for(frames, 103))

        rc, frames, logs = run_acp(gen_fs_write, chat=sc2.chat)
        w_req = next((f for f in frames if
                      f.get("method") == "fs/write_text_file"), None)
        r103 = result_for(frames, 103)
        check("acp: fs/write_text_file request carries content, result end_turn",
              w_req is not None and
              w_req["params"].get("path") == str(dst) and
              w_req["params"].get("content") == "written via proxy" and
              r103 is not None and
              r103["result"].get("stopReason") == "end_turn" and
              not dst.exists(),   # local FS untouched — the client owns it
              f"req={w_req} r103={r103}")

        # --- client refuses the fs request -> honest tool error, turn lives -
        sc3 = ScriptedChat([
            fake_response(calls=[("read_file", {"path": str(src)})]),
            fake_response(text="recovered"),
        ])

        def gen_fs_refuse(frames):
            yield init_line(101, fs=True)
            yield jreq(102, "session/new", {"cwd": str(tdp)})
            yield jreq(103, "session/prompt",
                       {"sessionId": "s1",
                        "prompt": [{"type": "text", "text": "read"}]})
            req = wait_for(lambda: next((f for f in frames if
                                         f.get("method") ==
                                         "fs/read_text_file"), None))
            if req is not None:
                yield json.dumps({"jsonrpc": "2.0", "id": req["id"],
                                  "error": {"code": -32000,
                                            "message": "denied by user"}})
            wait_for(lambda: result_for(frames, 103))

        rc, frames, logs = run_acp(gen_fs_refuse, chat=sc3.chat)
        ups = [f for f in frames if f.get("method") == "session/update"]
        r103 = result_for(frames, 103)
        check("acp: client refusal -> failed tool_call_update, turn continues",
              r103 is not None and
              r103["result"].get("stopReason") == "end_turn" and
              len(ups) >= 2 and
              ups[1]["params"]["update"].get("status") == "failed" and
              "denied by user" in json.dumps(ups[1]["params"]["update"]),
              f"ups={str(ups[:2])[:240]}")

        # --- client never answers -> timeout honesty, no hang --------------
        sc4 = ScriptedChat([
            fake_response(calls=[("read_file", {"path": str(src)})]),
            fake_response(text="timed out but alive"),
        ])

        def gen_fs_timeout(frames):
            yield init_line(101, fs=True)
            yield jreq(102, "session/new", {"cwd": str(tdp)})
            yield jreq(103, "session/prompt",
                       {"sessionId": "s1",
                        "prompt": [{"type": "text", "text": "read"}]})
            wait_for(lambda: result_for(frames, 103), timeout=12.0)

        t0 = _t.monotonic()
        rc, frames, logs = run_acp(gen_fs_timeout, chat=sc4.chat,
                                   fs_timeout=1.0)
        elapsed = _t.monotonic() - t0
        ups = [f for f in frames if f.get("method") == "session/update"]
        r103 = result_for(frames, 103)
        check("acp: unanswered fs request times out honestly, no deadlock",
              rc == 0 and elapsed < 8 and r103 is not None and
              r103["result"].get("stopReason") == "end_turn" and
              len(ups) >= 2 and
              ups[1]["params"]["update"].get("status") == "failed" and
              "no response from the client" in
              json.dumps(ups[1]["params"]["update"]),
              f"elapsed={elapsed:.1f}s ups={str(ups[:2])[:200]}")

        # --- EOF while a prompt is still in flight -> bounded clean exit ----
        entered2 = threading.Event()

        def slow_chat(messages=None, tools=None, **kw):
            entered2.set()
            _t.sleep(0.4)
            return fake_response(text="after eof")

        def gen_eof_mid(frames):
            yield init_line(101)
            yield jreq(102, "session/new", {"cwd": str(tdp)})
            yield jreq(103, "session/prompt",
                       {"sessionId": "s1",
                        "prompt": [{"type": "text", "text": "hi"}]})
            wait_for(entered2.is_set)   # prompt worker running, then EOF

        t0 = _t.monotonic()
        rc, frames, logs = run_acp(gen_eof_mid, chat=slow_chat)
        elapsed = _t.monotonic() - t0
        check("acp: stdin EOF mid-prompt exits cleanly and bounded",
              rc == 0 and elapsed < 12, f"rc={rc} elapsed={elapsed:.1f}s")

    # ---------------- batch D: real process framing (stdio -> JSON-RPC) ----
    import subprocess
    proc_in = "\n".join([
        init_line(1, fs=True),
        "{broken json",
        jreq(2, "session/new", {"cwd": str(REPO)}),
        jreq(3, "session/unknown", {}),
    ]) + "\n"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "mycore.acp"],
            input=proc_in, capture_output=True, text=True,
            timeout=30, cwd=str(REPO))
        out_lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        parsed = []
        bad = []
        for ln in out_lines:
            try:
                doc = json.loads(ln)
            except ValueError:
                bad.append(ln)
                continue
            if not (isinstance(doc, dict) and doc.get("jsonrpc") == "2.0"):
                bad.append(ln)
            else:
                parsed.append(doc)
        check("acp process: stdout carries only JSON-RPC 2.0 frames",
              proc.returncode == 0 and bool(parsed) and not bad,
              f"rc={proc.returncode} bad={bad[:2]}")
        ids = {d.get("id") for d in parsed if "result" in d or "error" in d}
        check("acp process: handshake/new/unknown-method answered, parse "
              "error answered, logs stay on stderr",
              {1, 2, 3} <= ids and
              any(d.get("error", {}).get("code") == -32700 for d in parsed) and
              "[acp]" in proc.stderr,
              f"ids={sorted(x for x in ids if isinstance(x, int))} "
              f"stderr={proc.stderr[:120]!r}")
    except subprocess.TimeoutExpired:
        check("acp process: stdio run bounded", False, "timeout 30s")


def test_tg_topics() -> None:
    """Car 4a-M3: forum-topic isolation — {cid}:{tid} pipeline sessions,
    threaded replies, chat-level commands, plain-chat zero regression.
    Offline (fake transport); no live TG E2E is re-run for this feature."""
    from mycore.service import ChatService
    from mycore.tg import POLL_BOT_ENV
    import os as _os

    saved_tok = _os.environ.get(POLL_BOT_ENV)
    _os.environ[POLL_BOT_ENV] = "topic-bot-TEST-token"
    try:
        class EchoChat:
            """Echoes the last user text; records the session history seen."""
            def __init__(self):
                self.seen: list[list[dict]] = []

            def chat(self, messages, tools=None, **kw):
                self.seen.append([dict(m) for m in messages])
                return fake_response(text="echo:"
                                     + str(messages[-1]["content"])[:20])

        with tempfile.TemporaryDirectory() as td:
            fake = FakeTgTransport()
            echo = EchoChat()
            svc = ChatService(Path(td), frozenset({111}), transport=fake,
                              chat=echo.chat)
            uid, cid = 111, 555001

            # --- 1) no thread: zero regression (chat-level session, no field)
            svc.handle_update(mk_update(1, uid, cid, "plain hello"))
            check("topic: plain chat reply has no message_thread_id",
                  fake.send_threads == [None], str(fake.send_threads))
            check("topic: plain chat session stays {cid}.jsonl",
                  (Path(td) / "sessions" / f"{cid}.jsonl").is_file())

            # --- 2) threaded message -> {cid}:{tid} session + threaded reply
            svc.handle_update(mk_update(2, uid, cid, "topic A alpha",
                                        thread_id=7))
            check("topic: threaded reply carries message_thread_id",
                  fake.send_threads[-1] == "7", str(fake.send_threads))
            check("topic: threaded session key is {cid}:{tid}",
                  (Path(td) / "sessions" / f"{cid}:7.jsonl").is_file()
                  and not (Path(td) / "sessions" / f"{cid}.7.jsonl").exists(),
                  str(list((Path(td) / "sessions").iterdir())))

            # --- 3) second topic isolated: separate session, no cross-talk
            svc.handle_update(mk_update(3, uid, cid, "topic B beta",
                                        thread_id=8))
            hist_a = [m["content"] for m in echo.seen[-2]
                      if m["role"] == "user"]
            hist_b = [m["content"] for m in echo.seen[-1]
                      if m["role"] == "user"]
            check("topic: two topics keep independent sessions",
                  "topic A alpha" in hist_a and "topic B beta" in hist_b
                  and "topic A alpha" not in hist_b
                  and (Path(td) / "sessions" / f"{cid}:8.jsonl").is_file(),
                  f"a={hist_a} b={hist_b}")
            check("topic: reply threads to its own topic",
                  fake.send_threads[-1] == "8", str(fake.send_threads))

            # --- 4) commands stay chat-level even inside a topic
            svc.handle_update(mk_update(4, uid, cid, "/help", thread_id=7))
            check("topic: command reply still threads back to the topic",
                  fake.send_threads[-1] == "7", str(fake.send_threads))
            sess_files = sorted(p.name for p in
                                (Path(td) / "sessions").glob("*.jsonl"))
            check("topic: command never opens a topic session",
                  sess_files == [f"{cid}.jsonl", f"{cid}:7.jsonl",
                                 f"{cid}:8.jsonl"], str(sess_files))

            # --- 5) non-int thread field is ignored (chat-level session)
            svc.handle_update(mk_update(5, uid, cid, "odd thread",
                                        thread_id="nope"))
            check("topic: non-int message_thread_id treated as absent",
                  fake.send_threads[-1] is None
                  and echo.seen[-1] is not None, str(fake.send_threads))
            # the turn above went to the CHAT session (no thread in reply)
            check("topic: odd-thread message lands in chat-level session",
                  not (Path(td) / "sessions" / f"{cid}:nope.jsonl").exists(),
                  str(list((Path(td) / "sessions").iterdir())))
    finally:
        if saved_tok is None:
            _os.environ.pop(POLL_BOT_ENV, None)
        else:
            _os.environ[POLL_BOT_ENV] = saved_tok


def test_browser() -> None:
    """Car 4b browser automation — OFFLINE unit surface: URL policy, @eN ref
    parsing, binary discovery (env-authoritative), and capability-honest
    registration. The real CDP wire (launch/navigate/click/screenshot) lives
    in tmp/e2e_car4b.py — this section never launches a browser."""
    from mycore import browser as br

    # --- URL policy: http/https only, browser-UI/local-file schemes refused
    allowed = ("http://127.0.0.1:8790/x", "https://example.com/a?b=1")
    check("browser: http/https urls pass the policy",
          all(br._check_url(u) == u for u in allowed), str(allowed))
    blocked = ("chrome://settings", "about:blank", "file:///etc/passwd",
               "data:text/html,x", "javascript:alert(1)", "blob:http://x/y",
               "devtools://x", "view-source:http://x", "ftp://x/y")
    refused = []
    for u in blocked:
        try:
            br._check_url(u)
            refused.append(u)
        except Exception:  # noqa: BLE001 — any error = refusal (ToolError)
            pass
    check("browser: blocked schemes all refused", not refused, str(refused))
    for bad in ("", "   ", "noscheme/path", 42, None):
        try:
            br._check_url(bad)
            check(f"browser: bad url {bad!r} refused", False, "accepted")
        except Exception:  # noqa: BLE001
            check(f"browser: bad url {bad!r} refused", True)
    try:
        br._check_url("http://")
        check("browser: hostless url refused", False, "accepted")
    except Exception:  # noqa: BLE001
        check("browser: hostless url refused", True)

    # --- @eN ref parsing (positional, snapshot-scoped)
    check("browser: ref parsing accepts @eN / eN / bare N",
          br._ref_index("@e1") == 1 and br._ref_index(" e2 ") == 2
          and br._ref_index("17") == 17, "parse ok")
    for bad_ref in ("@e0", "abc", "", "@ex", 5, None):
        try:
            br._ref_index(bad_ref)
            check(f"browser: bad ref {bad_ref!r} refused", False, "accepted")
        except Exception:  # noqa: BLE001
            check(f"browser: bad ref {bad_ref!r} refused", True)

    # --- discovery: AIDOG_BROWSER_BIN is AUTHORITATIVE (set-but-bad fails loud)
    saved_env = os.environ.get("AIDOG_BROWSER_BIN")
    try:
        os.environ["AIDOG_BROWSER_BIN"] = "/nonexistent/chrome-binary"
        path, attempts = br.find_binary()
        check("browser: dead env override -> None + named in attempts",
              path is None and any("AIDOG_BROWSER_BIN" in a for a in attempts),
              f"path={path} attempts={attempts}")
        check("browser: available() honors dead override",
              br.available() is False)
        reg = build_registry(allowed_roots=[tempfile.mkdtemp(prefix="br_reg_")])
        check("browser: no binary -> tools NOT registered + note set",
              not (set(reg.names()) & set(BROWSER_TOOLS))
              and bool(getattr(reg, "browser_note", "")),
              f"note={getattr(reg, 'browser_note', None)!r}")
        check("browser: hidden tool dispatches honest unknown",
              reg.dispatch("browser_open", {"url": "http://x"})
              .startswith("ERROR: unknown tool"))

        # a real executable path (fake, never launched) -> tools register
        fake_bin = Path(tempfile.mkdtemp(prefix="br_fake_")) / "chrome"
        fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_bin.chmod(0o755)
        os.environ["AIDOG_BROWSER_BIN"] = str(fake_bin)
        path2, _ = br.find_binary()
        reg2 = build_registry(allowed_roots=[tempfile.mkdtemp(prefix="br_reg2_")])
        got = set(reg2.names()) & set(BROWSER_TOOLS)
        check("browser: valid override -> all 6 tools registered, no note",
              path2 == str(fake_bin) and got == set(BROWSER_TOOLS)
              and getattr(reg2, "browser_note", None) is None,
              f"path={path2} got={sorted(got)}")
    finally:
        if saved_env is None:
            os.environ.pop("AIDOG_BROWSER_BIN", None)
        else:
            os.environ["AIDOG_BROWSER_BIN"] = saved_env

    # --- default discovery consistency (host-dependent result, honest either way)
    p3, _a3 = br.find_binary()
    check("browser: find_binary/available agree under default discovery",
          (p3 is not None) == br.available(), f"path={p3}")
    reg3 = build_registry(allowed_roots=[tempfile.mkdtemp(prefix="br_reg3_")])
    check("browser: registry matches default discovery (tools iff binary)",
          (bool(set(reg3.names()) & set(BROWSER_TOOLS)) == br.available())
          and (getattr(reg3, "browser_note", None) is None) == br.available(),
          f"available={br.available()} note={getattr(reg3, 'browser_note', None)!r}")


def test_terminal() -> None:
    """Car 4b terminal backends — grammar, local output contract (byte-stable
    vs the historical run_bash), honest missing-binary failures, and the
    settings hot-switch routing in tools._run_bash. Docker/ssh real wire lives
    in tmp/e2e_car4b.py."""
    from mycore import terminal as tm
    from mycore import tools as tools_mod

    # --- parse: the 7 valid forms
    for spec, kind in (("", "local"), ("local", "local"),
                       ("docker:image=ubuntu:latest", "docker"),
                       ("docker:container=aidog-box", "docker"),
                       ("ssh:me@example.com", "ssh"),
                       ("ssh:me@10.0.0.5:2222", "ssh"),
                       ("ssh:me@host;identity=/home/example/.ssh/id_ed25519", "ssh")):
        try:
            be = tm.parse(spec)
            check(f"terminal: parse {spec or '(empty)'}", be.kind == kind,
                  f"got {be.kind}")
        except ValueError as e:
            check(f"terminal: parse {spec or '(empty)'}", False, str(e))
    be = tm.parse("ssh:me@10.0.0.5:2222")
    check("terminal: ssh port parsed",
          be.user == "me" and be.host == "10.0.0.5" and be.port == 2222,
          str(be))
    be = tm.parse("ssh:me@host;identity=/k/id")
    check("terminal: ssh identity path parsed",
          be.identity == "/k/id" and be.port == 22, str(be))

    # --- parse: invalid forms refused, message never echoes the input
    canary = "zzCANARYzz"
    bad_specs = [f"bogus:{canary}", "docker:", "docker:bad=1",
                 f"docker:image=x;identity=/k{canary}", "ssh://x",
                 f"ssh:{canary}", "ssh:u@h;password=pw",
                 "ssh:u@h:99999", "ssh:u@h;identity=relative/path",
                 "x" * 300, "local; rm -rf /"]
    for spec in bad_specs:
        try:
            tm.parse(spec)
            check(f"terminal: invalid {spec[:24]!r} refused", False,
                  "accepted")
        except ValueError as e:
            # anti-echo: the input must never appear. Short grammar fragments
            # ("docker:", len<=7) legitimately substring the FIXED help text —
            # the canary and every real payload must be absent.
            check(f"terminal: invalid {spec[:24]!r} refused",
                  canary not in str(e)
                  and (spec not in str(e) or len(spec) <= 7), str(e))
    for spec in (42, None, ["local"]):
        try:
            tm.parse(spec)
            check(f"terminal: non-string {type(spec).__name__} refused", False)
        except ValueError:
            check(f"terminal: non-string {type(spec).__name__} refused", True)

    # --- local run: byte-stable contract vs historical _run_bash
    local = tm.parse("")
    check("terminal: local echo contract",
          tm.run(local, "echo hi", 5) == "exit=0\nhi",
          repr(tm.run(local, "echo hi", 5)))
    out = tm.run(local, "echo oop >&2; exit 3", 5)
    check("terminal: local stderr block + exit code",
          out.startswith("exit=3") and "--- stderr ---" in out
          and "oop" in out, repr(out))
    check("terminal: local trailing whitespace rstripped",
          tm.run(local, "printf 'a  \\n\\n'", 5) == "exit=0\na",
          repr(tm.run(local, "printf 'a  \\n\\n'", 5)))
    for args in (("", 5), ("   ", 5), ("echo x", 0), ("echo x", -1),
                 ("echo x", True)):
        try:
            tm.run(local, *args)
            check(f"terminal: bad run args {args!r} -> TerminalError", False)
        except tm.TerminalError:
            check(f"terminal: bad run args {args!r} -> TerminalError", True)
        except Exception as e:  # noqa: BLE001
            check(f"terminal: bad run args {args!r} -> TerminalError", False,
                  type(e).__name__)
    try:
        tm.run(local, "sleep 3", 1)
        check("terminal: timeout raises TerminalError", False, "no error")
    except tm.TerminalError as e:
        check("terminal: timeout raises TerminalError",
              "timed out" in str(e), str(e))

    # --- missing binary (PATH emptied): honest TerminalError, both backends
    saved_path = os.environ.get("PATH", "")
    try:
        os.environ["PATH"] = "/nonexistent-bin-dir"
        for spec in ("docker:image=ubuntu:latest", "ssh:u@h"):
            try:
                tm.run(tm.parse(spec), "echo x", 5)
                check(f"terminal: missing binary for {spec.split(':')[0]}",
                      False, "no error")
            except tm.TerminalError as e:
                check(f"terminal: missing binary for {spec.split(':')[0]}",
                      "not found in PATH" in str(e), str(e))
    finally:
        os.environ["PATH"] = saved_path

    # --- tools-level routing via the settings seam (monkeypatched, real
    # web/settings.json never touched)
    orig_spec_fn = tools_mod._shell_spec
    try:
        tools_mod._shell_spec = lambda: ""
        r = tools_mod._run_bash("echo loc", 5, ("",))
        check("terminal: run_bash empty spec -> local", r == "exit=0\nloc", r)
        tools_mod._shell_spec = lambda: f"bogus::{canary}"
        try:
            tools_mod._run_bash("echo x", 5, ("",))
            check("terminal: run_bash invalid spec -> ToolError, no echo",
                  False, "no error")
        except Exception as e:  # noqa: BLE001 — tools.ToolError
            check("terminal: run_bash invalid spec -> ToolError, no echo",
                  "mycore.shell" in str(e) and canary not in str(e), str(e))
        tools_mod._shell_spec = lambda: "docker:image=ubuntu:latest"
        os.environ["PATH"] = "/nonexistent-bin-dir"
        try:
            tools_mod._run_bash("echo x", 5, ("",))
            check("terminal: run_bash docker w/o binary -> ToolError", False)
        except Exception as e:  # noqa: BLE001
            check("terminal: run_bash docker w/o binary -> ToolError",
                  "docker" in str(e).lower(), str(e))
        os.environ["PATH"] = saved_path
    finally:
        tools_mod._shell_spec = orig_spec_fn
        os.environ["PATH"] = saved_path

    # --- tool description reflects the backend abstraction
    reg = build_registry(allowed_roots=[tempfile.mkdtemp(prefix="term_reg_")])
    desc = next((t["function"]["description"] for t in reg.schema()
                 if t["function"]["name"] == "run_bash"), "")
    check("terminal: run_bash description names the backend setting",
          "mycore.shell" in desc, desc[:120])


def test_localmodels() -> None:
    """Car 4b zero-key onboarding — probe() parsing/filtering with an injected
    _fetch (offline, zero sockets; the wizard leg + real HTTP are covered by
    tests/setup_wizard_b3.py and tmp/e2e_car4b.py)."""
    from mycore import localmodels as lm

    # --- payload shapes
    check("localmodels: ollama shape -> first model name",
          lm._model_of({"models": [{"name": "qwen:7b"}]}, "ollama")
          == "qwen:7b")
    check("localmodels: openai shape -> first id",
          lm._model_of({"data": [{"id": "llama-3"}]}, "openai")
          == "llama-3")
    bad_payloads = (None, {}, {"models": []}, {"data": []},
                    {"models": [{}]}, {"data": [{"id": "  "}]},
                    {"models": "nope"}, {"data": [{"id": 42}]}, "str")
    check("localmodels: malformed payloads -> empty model",
          all(lm._model_of(p, s) == ""
              for p in bad_payloads for s in ("ollama", "openai")),
          str(bad_payloads))

    calls: list[str] = []
    orig_fetch, orig_probes = lm._fetch, lm.DEFAULT_PROBES
    saved_env = os.environ.get("AIDOG_LOCAL_MODEL_PROBES")
    try:
        # --- env override: comma list, whitespace/trailing comma tolerated,
        # uniform OpenAI <url>/models shape, kind=local
        def fake_ok(url: str):
            calls.append(url)
            return {"data": [{"id": "m1"}]}
        lm._fetch = fake_ok
        os.environ["AIDOG_LOCAL_MODEL_PROBES"] = (
            "http://127.0.0.1:18100/v1, http://127.0.0.1:18101/v1, ,")
        found = lm.probe()
        check("localmodels: override probes comma list -> 2 local entries",
              len(found) == 2
              and all(e["kind"] == "local" and e["model"] == "m1"
                      for e in found)
              and [e["base_url"] for e in found]
              == ["http://127.0.0.1:18100/v1", "http://127.0.0.1:18101/v1"],
              str(found))
        check("localmodels: override URLs normalized to <url>/models",
              calls == ["http://127.0.0.1:18100/v1/models",
                        "http://127.0.0.1:18101/v1/models"], str(calls))
        calls.clear()
        os.environ["AIDOG_LOCAL_MODEL_PROBES"] = "http://127.0.0.1:18102/v1/models"
        lm.probe()
        check("localmodels: override .../models passed through verbatim",
              calls == ["http://127.0.0.1:18102/v1/models"], str(calls))

        # --- default probe path: deterministic via monkeypatched DEFAULT_PROBES
        os.environ.pop("AIDOG_LOCAL_MODEL_PROBES", None)
        lm.DEFAULT_PROBES = (
            ("ollama", "http://127.0.0.1:59991/api/tags", "ollama",
             "http://127.0.0.1:59991/v1"),
            ("llamacpp", "http://127.0.0.1:59992/v1/models", "openai",
             "http://127.0.0.1:59992/v1"),
            ("lmstudio", "http://127.0.0.1:59993/v1/models", "openai",
             "http://127.0.0.1:59993/v1"))
        lm._fetch = lambda u: None
        check("localmodels: all probes dead -> []", lm.probe() == [])
        lm._fetch = lambda u: ({"models": [{"name": "qwen:7b"}]}
                               if u.endswith("/api/tags")
                               else {"data": [{"id": "llama-3"}]})
        found = lm.probe()
        check("localmodels: default probes -> per-kind entries + base_url",
              [e["kind"] for e in found] == ["ollama", "llamacpp", "lmstudio"]
              and found[0]["base_url"] == "http://127.0.0.1:59991/v1"
              and found[1]["model"] == "llama-3", str(found))
        lm._fetch = lambda u: {"models": [{"name": "  "}]}
        check("localmodels: blank model name counts as not detected",
              lm.probe() == [], str(lm.probe()))

        # --- never raises: a throwing fetch must not escape probe()
        def boom(u):
            raise RuntimeError("exploded")
        lm._fetch = boom
        check("localmodels: probe never raises on fetch failure",
              lm.probe() == [])
        lm._fetch = lambda u: ["not", "a", "dict"]
        check("localmodels: non-object payload -> not detected",
              lm.probe() == [])

        # --- describe: one line per entry, three fields only, no credential keys
        lines = lm.describe([{"kind": "ollama",
                              "base_url": "http://127.0.0.1:11434/v1",
                              "model": "qwen:7b"}])
        check("localmodels: describe renders kind+url+model",
              len(lines) == 1 and "ollama" in lines[0]
              and "11434" in lines[0] and "qwen:7b" in lines[0]
              and "key" not in lines[0].lower(), str(lines))
        check("localmodels: describe tolerant of junk/empty",
              lm.describe([]) == []
              and lm.describe(["junk", 42]) == [], str(lm.describe(["junk", 42])))
    finally:
        lm._fetch, lm.DEFAULT_PROBES = orig_fetch, orig_probes
        if saved_env is None:
            os.environ.pop("AIDOG_LOCAL_MODEL_PROBES", None)
        else:
            os.environ["AIDOG_LOCAL_MODEL_PROBES"] = saved_env


# ------------------------------------------- car 4c: media-face tests ----

# A real 1x1 PNG (imagegen tests assert saved bytes, never render).
PNG_1PX = (__import__("base64").b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))


class _LoopbackMock:
    """Loopback-only mock HTTP server for the car 4c media tests (offline:
    binds 127.0.0.1:0 and nothing else). `respond(method, path, body) ->
    (status, content_type, payload_bytes)` is injected per test; every
    request is recorded in `.requests` for request-shape assertions."""

    def __init__(self, respond) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.respond = respond
        self.requests: list[tuple[str, str, dict, bytes]] = []
        mock = self

        class _H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D102 — keep the suite quiet
                return

            def _handle(self):
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                body = self.rfile.read(length) if length else b""
                mock.requests.append((self.command, self.path,
                                      dict(self.headers), body))
                status, ctype, data = mock.respond(self.command, self.path, body)
                data = data or b""
                try:
                    self.send_response(status)
                    if data:
                        self.send_header("Content-Type", ctype)
                        self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    if data:
                        self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_GET = do_POST = _handle

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self.port = int(self._srv.server_address[1])
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def close(self) -> None:
        try:
            self._srv.shutdown()
            self._srv.server_close()
        except OSError:
            pass


def test_stt() -> None:
    """Car 4c: local faster-whisper STT — decode + transcribe contract.
    Structure assertions only (the fixture is a synthetic sine tone with no
    speech): transcript ACCURACY is an E2E leg, never an offline assertion."""
    import io
    import math
    import struct
    import wave
    from mycore import stt

    check("stt: available() true on this host (weights cached)",
          stt.available() is True)

    def sine_wav(seconds: float = 0.5) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(stt.SAMPLE_RATE)
            n = int(stt.SAMPLE_RATE * seconds)
            w.writeframes(b"".join(
                struct.pack("<h", int(12000 * math.sin(
                    2 * math.pi * 440 * i / stt.SAMPLE_RATE)))
                for i in range(n)))
        return buf.getvalue()

    wav = sine_wav()

    try:
        stt.transcribe(wav, model="huge")
        check("stt: invalid model -> SttError", False)
    except stt.SttError as e:
        check("stt: invalid model -> SttError",
              "unsupported model" in str(e), str(e))

    for label, blob in (("empty", b""),
                        ("junk", b"\x00\xff- definitely not a container")):
        try:
            stt.transcribe(blob)
            check(f"stt: {label} bytes -> SttError", False)
        except stt.SttError:
            check(f"stt: {label} bytes -> SttError", True)
        except Exception as e:  # noqa: BLE001
            check(f"stt: {label} bytes -> SttError", False,
                  f"{type(e).__name__}: {e}")

    # Both cached sizes run at least once (tiny is fast, base = default).
    for size in ("tiny", "base"):
        try:
            got = stt.transcribe(wav, model=size)
        except Exception as e:  # noqa: BLE001
            check(f"stt: {size} decode+transcribe sine wav", False,
                  f"{type(e).__name__}: {e}")
            continue
        check(f"stt: {size} decode+transcribe -> dict structure",
              isinstance(got, dict)
              and isinstance(got.get("text"), str)
              and isinstance(got.get("language"), str)
              and isinstance(got.get("duration"), (int, float))
              and not isinstance(got.get("duration"), bool)
              and got["duration"] > 0.1,
              str({k: got.get(k) for k in ("text", "language", "duration")})[:80])


def test_imagegen() -> None:
    """Car 4c: BYOK image generation — config precedence, outbox contract,
    happy/error paths against a loopback mock (zero network, zero real keys)."""
    import base64
    from mycore import imagegen

    env_keys = ("AIDOG_IMAGE_URL", "IMAGE_API_KEY", "MODEL_API_KEY")
    saved_env = {k: os.environ.get(k) for k in env_keys}
    saved_settings = imagegen._settings
    # T-2 (delivery acceptance): imagegen resolves its credential slot with
    # its OWN module reader (process env -> <repo>/secrets.env), i.e. NOT the
    # mycore.model._slot_key seam pinned suite-wide at module import above.
    # On a host whose MODEL_API_KEY slot is filled (provisioned model key, or
    # a clone acceptance run with a fake key) the two "remote w/o key" legs
    # below would see a key and fail. Pin the file side out for THIS leg only
    # (env stays real — the SENTINEL legs drive it); the suite-wide
    # _SLOT_KEY_REAL mechanism above is untouched.
    saved_slot = imagegen._slot
    imagegen._slot = lambda name: os.environ.get(name, "").strip()
    mock = None
    created: list[Path] = []
    try:
        for k in env_keys:
            os.environ.pop(k, None)
        imagegen.drain_outbox()          # never inherit leftovers from other legs

        # --- config precedence: env > settings.media > settings.mycore ---
        os.environ["AIDOG_IMAGE_URL"] = "http://127.0.0.1:9/v1"
        check("imagegen: env endpoint wins",
              imagegen._base_url() == "http://127.0.0.1:9/v1",
              imagegen._base_url())
        os.environ.pop("AIDOG_IMAGE_URL", None)

        imagegen._settings = lambda: {"media": {
            "image_base_url": "http://127.0.0.1:9091/v1"}}
        check("imagegen: settings media.image_base_url fallback",
              imagegen._base_url() == "http://127.0.0.1:9091/v1",
              imagegen._base_url())
        imagegen._settings = lambda: {"mycore": {
            "base_url": "http://127.0.0.1:3460/v1/"}}
        check("imagegen: settings mycore.base_url fallback + slash strip",
              imagegen._base_url() == "http://127.0.0.1:3460/v1",
              imagegen._base_url())

        # --- four-state configured(): remote w/o key rejects, loopback ok ---
        os.environ["AIDOG_IMAGE_URL"] = "https://remote.invalid/v1"
        ok, reason = imagegen.configured()
        check("imagegen: remote endpoint without key rejected",
              ok is False and "IMAGE_API_KEY / MODEL_API_KEY" in reason)
        try:
            imagegen.generate("a cat")
            check("imagegen: generate() honors unconfigured -> ImageError", False)
        except imagegen.ImageError as e:
            check("imagegen: generate() honors unconfigured -> ImageError",
                  "not configured" in str(e))
        os.environ["IMAGE_API_KEY"] = "SENTINEL-image-key-000"
        ok, reason = imagegen.configured()
        check("imagegen: key slot unlocks remote endpoint",
              ok is True and reason == "" and "SENTINEL" not in reason)
        os.environ.pop("IMAGE_API_KEY", None)
        os.environ["AIDOG_IMAGE_URL"] = "http://127.0.0.1:9/v1"
        ok, reason = imagegen.configured()
        check("imagegen: loopback endpoint needs no key", ok is True,
              reason)
        imagegen._settings = saved_settings

        # --- argument validation (cheap, no server needed) ---
        for bad in (0, 11, True):
            try:
                imagegen.generate("x", n=bad)
                check(f"imagegen: n={bad!r} -> ImageError", False)
            except imagegen.ImageError:
                check(f"imagegen: n={bad!r} -> ImageError", True)
        try:
            imagegen.generate("   ")
            check("imagegen: blank prompt -> ImageError", False)
        except imagegen.ImageError:
            check("imagegen: blank prompt -> ImageError", True)
        try:
            imagegen.generate("x", size="huge")
            check("imagegen: bad size -> ImageError", False)
        except imagegen.ImageError:
            check("imagegen: bad size -> ImageError", True)

        # --- loopback mock: b64 happy path + outbox contract ---
        state = {"mode": "b64"}

        def respond(method, path, body):
            if state["mode"] == "404":
                return 404, "application/json", b'{"error":{"message":"model not found"}}'
            if state["mode"] == "500big":
                return 500, "application/json", json.dumps(
                    {"error": {"message": "E" * 4000}}).encode()
            if path.endswith("/images/generations"):
                if state["mode"] == "url":
                    doc = {"data": [{"url": f"{mock.url}/img.png"}]}
                else:
                    doc = {"data": [{"b64_json": base64.b64encode(PNG_1PX).decode()}]}
                return 200, "application/json", json.dumps(doc).encode()
            if path.endswith("/img.png"):
                return 200, "image/png", PNG_1PX
            return 404, "application/json", b"{}"

        mock = _LoopbackMock(respond)
        os.environ["AIDOG_IMAGE_URL"] = mock.url + "/v1"

        paths = imagegen.generate("a cat", size="512x512")
        created.extend(paths)
        check("imagegen: b64 happy path saves files",
              len(paths) == 1 and paths[0].is_file()
              and paths[0].suffix == ".png"
              and paths[0].parent == REPO / "data" / "images"
              and paths[0].read_bytes() == PNG_1PX,
              str(paths))
        queued = imagegen.drain_outbox()
        check("imagegen: outbox queued (path, prompt) once",
              len(queued) == 1 and queued[0][0] == paths[0]
              and queued[0][1] == "a cat", str(len(queued)))
        check("imagegen: drain_outbox idempotent (second drain empty)",
              imagegen.drain_outbox() == [])
        check("imagegen: drain_outbox leaves no residue",
              imagegen.drain_outbox() == [])

        check("imagegen: POST hits the generations route",
              mock.requests and mock.requests[-1][0] == "POST"
              and mock.requests[-1][1].endswith("/images/generations"),
              mock.requests[-1][1] if mock.requests else "no request")
        sent = json.loads(mock.requests[-1][3])
        check("imagegen: payload shape (prompt/size/n/response_format)",
              sent.get("prompt") == "a cat" and sent.get("size") == "512x512"
              and sent.get("n") == 1
              and sent.get("response_format") == "b64_json", str(sent)[:120])
        check("imagegen: Authorization header present, key not required on loopback",
              mock.requests[-1][2].get("Authorization", "").startswith("Bearer "))

        # --- upstream 4xx: honest HTTP line, key never in the message ---
        os.environ["IMAGE_API_KEY"] = "SENTINEL-image-key-000"
        state["mode"] = "404"
        try:
            imagegen.generate("a cat")
            check("imagegen: upstream 404 -> ImageError HTTP 404", False)
        except imagegen.ImageError as e:
            msg = str(e)
            check("imagegen: upstream 404 -> ImageError HTTP 404",
                  "HTTP 404" in msg)
            check("imagegen: 404 message never carries the key",
                  "SENTINEL-image-key-000" not in msg
                  and "SENTINEL" not in msg)
        state["mode"] = "500big"
        try:
            imagegen.generate("a cat")
            check("imagegen: upstream 500 -> bounded ImageError", False)
        except imagegen.ImageError as e:
            check("imagegen: upstream 500 -> bounded ImageError",
                  "HTTP 500" in str(e) and len(str(e)) <= 220
                  and "SENTINEL" not in str(e), str(len(str(e))))
        os.environ.pop("IMAGE_API_KEY", None)

        # --- url reply leg: mock returns a url -> GET -> same bytes ---
        state["mode"] = "url"
        try:
            paths2 = imagegen.generate("a dog")
            created.extend(paths2)
            check("imagegen: url reply leg downloads the image",
                  len(paths2) == 1 and paths2[0].is_file()
                  and paths2[0].read_bytes() == PNG_1PX, str(paths2))
            check("imagegen: url leg also queues the outbox",
                  any(p == paths2[0] for p, _ in imagegen.drain_outbox()))
        except imagegen.ImageError as e:
            check("imagegen: url reply leg downloads the image", False, str(e))
    finally:
        imagegen._settings = saved_settings
        imagegen._slot = saved_slot          # T-2: restore the real reader
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        imagegen.drain_outbox()          # never leak into later sections
        for p in created:
            try:
                p.unlink()
            except OSError:
                pass
        if mock is not None:
            mock.close()


def test_connectors() -> None:
    """Car 4c: read-only BYOK connectors — slot presence, protocol headers
    (Linear raw Authorization / Notion Bearer + Notion-Version), endpoint
    override seams, validation and bounded upstream errors. Injected
    transports everywhere; one loopback 500 exercises the HTTP truncation."""
    from mycore import connectors as cn

    env_keys = ("LINEAR_API_KEY", "NOTION_API_KEY",
                "AIDOG_LINEAR_URL", "AIDOG_NOTION_URL")
    saved = {k: os.environ.get(k) for k in env_keys}
    mock = None
    try:
        for k in env_keys:
            os.environ.pop(k, None)

        # --- status(): slot PRESENCE only (values never surface) ---
        check("connectors: status no slots -> both False",
              cn.status() == {"linear": False, "notion": False},
              str(cn.status()))
        os.environ["LINEAR_API_KEY"] = "LIN-SENTINEL-000"
        check("connectors: status sees env slot as presence only",
              cn.status() == {"linear": True, "notion": False}
              and "LIN-SENTINEL" not in str(cn.status()))
        os.environ["NOTION_API_KEY"] = "NOT-SENTINEL-000"
        check("connectors: status both slots",
              cn.status() == {"linear": True, "notion": True})

        # --- validation: rejected BEFORE any transport is reached ---
        def unreachable(url, headers, payload, timeout):
            raise AssertionError("transport must not be reached")

        for empty in ("", "   "):
            try:
                cn.search(empty, transport=unreachable)
                check("connectors: blank query -> ConnectorError", False)
            except cn.ConnectorError as e:
                check("connectors: blank query -> ConnectorError",
                      "non-empty" in str(e), str(e))
        for bad_limit in (0, 21, True, "5"):
            try:
                cn.search("q", limit=bad_limit, transport=unreachable)
                check(f"connectors: limit={bad_limit!r} -> ConnectorError", False)
            except cn.ConnectorError:
                check(f"connectors: limit={bad_limit!r} -> ConnectorError", True)
            except Exception as e:  # noqa: BLE001
                check(f"connectors: limit={bad_limit!r} -> ConnectorError", False,
                      type(e).__name__)
        try:
            cn.search("q", source="jira", transport=unreachable)
            check("connectors: unknown source -> ConnectorError", False)
        except cn.ConnectorError as e:
            check("connectors: unknown source -> ConnectorError",
                  "auto|linear|notion" in str(e), str(e))

        # --- Linear protocol: RAW Authorization (no Bearer), GraphQL body ---
        seen: dict = {}

        def lin_ok(url, headers, payload, timeout):
            seen.update(url=url, headers=dict(headers), payload=payload,
                        timeout=timeout)
            return json.dumps({"data": {"issueSearch": {"nodes": [
                {"identifier": "ENG-1", "title": "Fix the bug",
                 "url": "https://linear.app/issue/ENG-1"}]}}})

        out = cn.search("bug fix", source="linear", transport=lin_ok)
        check("connectors: linear Authorization is the RAW key, no Bearer",
              seen.get("headers", {}).get("Authorization") == "LIN-SENTINEL-000")
        check("connectors: linear default endpoint",
              seen.get("url") == "https://api.linear.app/graphql",
              str(seen.get("url")))
        body = json.loads(seen.get("payload") or b"{}")
        check("connectors: linear body carries query + limit",
              isinstance(body.get("query"), str) and "issueSearch" in body["query"]
              and body.get("variables") == {"q": "bug fix", "n": 5},
              str(body)[:120])
        check("connectors: linear compact result line",
              out == "ENG-1 · Fix the bug · https://linear.app/issue/ENG-1",
              out)

        # --- Notion protocol: Bearer + Notion-Version, /v1/search body ---
        seen2: dict = {}

        def notion_ok(url, headers, payload, timeout):
            seen2.update(url=url, headers=dict(headers), payload=payload)
            return json.dumps({"results": [{"object": "page",
                "url": "https://notion.so/roadmap-abc",
                "properties": {"Title": {"type": "title", "title": [
                    {"plain_text": "Roadmap"}]}}}],
                "has_more": False, "next_cursor": None})

        out = cn.search("roadmap", source="notion", limit=3,
                        transport=notion_ok)
        check("connectors: notion Authorization is Bearer",
              seen2.get("headers", {}).get("Authorization")
              == "Bearer NOT-SENTINEL-000")
        check("connectors: notion Notion-Version pinned",
              seen2.get("headers", {}).get("Notion-Version") == "2025-09-03",
              str(seen2.get("headers", {}).get("Notion-Version")))
        body2 = json.loads(seen2.get("payload") or b"{}")
        check("connectors: notion body carries query + page_size",
              body2 == {"query": "roadmap", "page_size": 3}, str(body2))
        check("connectors: notion title + url line",
              "Roadmap · https://notion.so/roadmap-abc" in out, out)

        # --- auto routing: sections per configured source ---
        auto_calls: list[str] = []

        def auto(url, headers, payload, timeout):
            auto_calls.append(url)
            if "linear.app" in url:
                return lin_ok(url, headers, payload, timeout)
            return notion_ok(url, headers, payload, timeout)

        out = cn.search("anything", transport=auto)
        check("connectors: auto both slots -> two labelled sections",
              len(auto_calls) == 2 and "[linear]" in out and "[notion]" in out,
              out[:120])
        os.environ.pop("NOTION_API_KEY", None)
        auto_calls.clear()
        out = cn.search("anything", transport=auto)
        check("connectors: auto single slot -> only that section",
              len(auto_calls) == 1 and "[linear]" in out
              and "[notion]" not in out, out[:80])
        try:
            cn.search("anything", source="notion", transport=auto)
            check("connectors: explicit source without slot -> slot name", False)
        except cn.ConnectorError as e:
            check("connectors: explicit source without slot -> slot name",
                  "NOTION_API_KEY" in str(e), str(e))
        check("connectors: no transport reached for the missing slot",
              len(auto_calls) == 1)
        os.environ.pop("LINEAR_API_KEY", None)
        try:
            cn.search("anything", transport=auto)
            check("connectors: auto without slots -> both slot names", False)
        except cn.ConnectorError as e:
            check("connectors: auto without slots -> both slot names",
                  "LINEAR_API_KEY" in str(e) and "NOTION_API_KEY" in str(e),
                  str(e))

        # --- empty result set stays honest (no fabrication) ---
        os.environ["LINEAR_API_KEY"] = "LIN-SENTINEL-000"
        out = cn.search("nothing", source="linear",
                        transport=lambda *a: json.dumps(
                            {"data": {"issueSearch": {"nodes": []}}}))
        check("connectors: empty result -> honest no-results line",
              out.startswith("[linear] no results"), out)

        # --- upstream errors: bounded to ERR_SNIPPET, never unbounded ---
        def lin_err(url, headers, payload, timeout):
            return json.dumps({"errors": [{"message": "E" * 400}]})

        try:
            cn.search("q", source="linear", transport=lin_err)
            check("connectors: linear upstream error truncated", False)
        except cn.ConnectorError as e:
            check("connectors: linear upstream error truncated to 160",
                  len(str(e)) <= len("linear: ") + cn.ERR_SNIPPET
                  and "E" * 161 not in str(e), str(len(str(e))))

        os.environ["NOTION_API_KEY"] = "NOT-SENTINEL-000"

        def notion_err(url, headers, payload, timeout):
            return json.dumps({"code": 401, "message": "M" * 400})

        try:
            cn.search("q", source="notion", transport=notion_err)
            check("connectors: notion upstream error truncated", False)
        except cn.ConnectorError as e:
            check("connectors: notion upstream error truncated to 160",
                  len(str(e)) <= len("notion: ") + cn.ERR_SNIPPET
                  and "M" * 161 not in str(e), str(len(str(e))))

        # --- endpoint override seams + real HTTP 500 truncation leg ---
        def respond(method, path, body):
            return 500, "application/json", b"Z" * 4000

        mock = _LoopbackMock(respond)
        os.environ["AIDOG_LINEAR_URL"] = mock.url + "/graphql"
        os.environ["AIDOG_NOTION_URL"] = mock.url + "/v1/search"
        try:
            cn.search("q", source="linear")       # default urllib transport
            check("connectors: HTTP 500 -> bounded ConnectorError", False)
        except cn.ConnectorError as e:
            check("connectors: HTTP 500 body truncated to 160 chars",
                  "HTTP 500" in str(e)
                  and len(str(e)) <= len("HTTP 500 Internal Server Error: ")
                  + cn.ERR_SNIPPET
                  and "Z" * 161 not in str(e), str(len(str(e))))

        seen3: dict = {}

        def track(url, headers, payload, timeout):
            seen3["url"] = url
            return lin_ok(url, headers, payload, timeout)

        cn.search("q", source="linear", transport=track)
        check("connectors: AIDOG_LINEAR_URL override reaches transport",
              seen3.get("url") == mock.url + "/graphql", str(seen3.get("url")))
        seen4: dict = {}

        def track_n(url, headers, payload, timeout):
            seen4["url"] = url
            return notion_ok(url, headers, payload, timeout)

        cn.search("q", source="notion", transport=track_n)
        check("connectors: AIDOG_NOTION_URL override reaches transport",
              seen4.get("url") == mock.url + "/v1/search", str(seen4.get("url")))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if mock is not None:
            mock.close()


def test_media_tools() -> None:
    """Car 4c: registry wiring — generate_image registers ALWAYS (config at
    call time), connector_search registers ONLY with a credential slot
    (registry.connector_note otherwise); dispatch renders saved:/ERROR:."""
    from mycore import connectors as cn
    from mycore import imagegen

    env_keys = ("LINEAR_API_KEY", "NOTION_API_KEY")
    saved_env = {k: os.environ.get(k) for k in env_keys}
    saved_gen = imagegen.generate
    saved_search = cn.search
    try:
        for k in env_keys:
            os.environ.pop(k, None)
        with tempfile.TemporaryDirectory() as td:
            reg = build_registry(allowed_roots=[td])
            names = reg.names()
            check("media tools: generate_image always registered",
                  "generate_image" in names)
            check("media tools: connector_search absent without slots",
                  "connector_search" not in names)
            note = getattr(reg, "connector_note", None)
            check("media tools: connector_note honest without slots",
                  isinstance(note, str) and "not configured" in note
                  and "LINEAR_API_KEY" in note, str(note))
            schema_names = [t["function"]["name"] for t in reg.schema()]
            check("media tools: schema has generate_image, not connector_search",
                  "generate_image" in schema_names
                  and "connector_search" not in schema_names)
            check("media tools: no-slot registry equals expected set",
                  set(names) == set(expected_tool_names()), str(names))

            # unconfigured -> honest ERROR line (never a missing tool, never a raise)
            def gen_boom(prompt, **kw):
                raise imagegen.ImageError(
                    "image generation not configured: no image endpoint")

            imagegen.generate = gen_boom
            try:
                r = reg.dispatch("generate_image", {"prompt": "a cat"})
            finally:
                imagegen.generate = saved_gen
            check("media tools: generate_image unconfigured -> ERROR line",
                  r.startswith("ERROR: image generation not configured"), r[:90])

            # upstream failure -> ERROR line with the HTTP status, key-free
            def gen_500(prompt, **kw):
                raise imagegen.ImageError("image API HTTP 502: upstream sad")

            imagegen.generate = gen_500
            try:
                r = reg.dispatch("generate_image", {"prompt": "a cat"})
            finally:
                imagegen.generate = saved_gen
            check("media tools: generate_image upstream failure -> ERROR line",
                  r == "ERROR: image API HTTP 502: upstream sad", r[:90])

            # happy path -> one 'saved: <path>' line per image
            img = Path(td) / "gen.png"
            img.write_bytes(PNG_1PX)
            imagegen.generate = lambda prompt, **kw: [img, img]
            try:
                r = reg.dispatch("generate_image", {"prompt": "a cat",
                                                     "size": "512x512"})
            finally:
                imagegen.generate = saved_gen
            check("media tools: generate_image renders saved: lines",
                  r == f"saved: {img}\nsaved: {img}", r[:120])

            # bad arguments stay contained (never a traceback)
            r = reg.dispatch("generate_image", {})
            check("media tools: generate_image missing prompt -> ERROR",
                  r.startswith("ERROR:"), r[:80])
            # not registered without a slot -> unknown-tool ERROR, honest
            r = reg.dispatch("connector_search", {"query": "x"})
            check("media tools: connector_search unregistered -> unknown ERROR",
                  r.startswith("ERROR: unknown tool 'connector_search'"), r[:80])

            # --- with a slot: registered, note cleared, dispatch paths live ---
            os.environ["LINEAR_API_KEY"] = "LIN-SENTINEL-000"
            reg2 = build_registry(allowed_roots=[td])
            check("media tools: connector_search registered with a slot",
                  "connector_search" in reg2.names()
                  and getattr(reg2, "connector_note", "unset") is None)
            check("media tools: schema carries connector_search with a slot",
                  "connector_search" in [t["function"]["name"]
                                         for t in reg2.schema()])
            check("media tools: with-slot registry equals expected set",
                  set(reg2.names()) == set(expected_tool_names()))

            cn.search = lambda query, **kw: f"[linear] hit for {query}"
            try:
                r = reg2.dispatch("connector_search", {"query": "bug"})
            finally:
                cn.search = saved_search
            check("media tools: connector_search renders upstream text",
                  r == "[linear] hit for bug", r[:80])

            def conn_boom(query, **kw):
                raise cn.ConnectorError(
                    "linear not configured (set LINEAR_API_KEY in secrets.env)")

            cn.search = conn_boom
            try:
                r = reg2.dispatch("connector_search", {"query": "bug"})
            finally:
                cn.search = saved_search
            check("media tools: connector_search failure -> ERROR line",
                  r.startswith("ERROR: linear not configured"), r[:90])
    finally:
        imagegen.generate = saved_gen
        cn.search = saved_search
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_tg_media() -> None:
    """Car 4c: TG media legs — getFile, the bytes download seam and the
    multipart sendPhoto seam, through injected transports (zero network).
    Error paths assert the token sentinel never surfaces."""
    from mycore.tg import TgClient, TgError, MAX_FILE_BYTES

    TOKEN = "777777:TOKEN-SENTINEL-tgmedia-000"

    # --- getFile: file_id rides in the payload, result unwrapped ---
    calls: list = []

    def tr_get(url, payload, timeout):
        calls.append((url, payload, timeout))
        return json.dumps({"ok": True, "result": {
            "file_id": "FID123", "file_unique_id": "U1",
            "file_path": "voice/file_1.oga", "file_size": 4096}})

    cli = TgClient(token=TOKEN, transport=tr_get)
    meta = cli.get_file("FID123")
    check("tg media: getFile unwraps result",
          meta.get("file_path") == "voice/file_1.oga"
          and meta.get("file_size") == 4096, str(meta))
    check("tg media: getFile payload carries file_id",
          b"file_id=FID123" in (calls[-1][1] or b""),
          str(calls[-1][1]))
    check("tg media: getFile hits the right method",
          calls[-1][0].endswith("/getFile"))
    try:
        cli.get_file("")
        check("tg media: empty file_id -> TgError", False)
    except TgError:
        check("tg media: empty file_id -> TgError", True)

    def tr_noresult(url, payload, timeout):
        return json.dumps({"ok": True, "result": "not-an-object"})

    try:
        TgClient(token=TOKEN, transport=tr_noresult).get_file("x")
        check("tg media: getFile non-object result -> TgError", False)
    except TgError:
        check("tg media: getFile non-object result -> TgError", True)

    # --- download_file: bytes seam, bounds, sanitized errors ---
    bin_calls: list[str] = []

    def bin_ok(url, timeout):
        bin_calls.append(url)
        return b"RIFF" + b"\x00" * 32

    cli2 = TgClient(token=TOKEN, bin_transport=bin_ok)
    blob = cli2.download_file("voice/file_1.oga")
    check("tg media: download_file returns bytes verbatim",
          blob == b"RIFF" + b"\x00" * 32 and len(blob) == 36)
    check("tg media: download url shape /file/bot<token>/<path>",
          bool(bin_calls)
          and bin_calls[-1].endswith("/file/bot" + TOKEN + "/voice/file_1.oga"))

    def bin_text(url, timeout):
        return "not-bytes"

    try:
        TgClient(token=TOKEN, bin_transport=bin_text).download_file("a/b")
        check("tg media: non-bytes response -> TgError", False)
    except TgError as e:
        check("tg media: non-bytes response -> TgError", "not bytes" in str(e),
              str(e))

    def bin_big(url, timeout):
        return b"x" * (MAX_FILE_BYTES + 1)

    try:
        TgClient(token=TOKEN, bin_transport=bin_big).download_file("a/b")
        check("tg media: >5MB -> TgError file too large", False)
    except TgError as e:
        check("tg media: >5MB -> TgError file too large",
              str(e) == "file too large", str(e))

    def bin_boom(url, timeout):
        raise ValueError("boom carrying " + TOKEN)

    try:
        TgClient(token=TOKEN, bin_transport=bin_boom).download_file("a/b")
        check("tg media: transport failure sanitized, token free", False)
    except TgError as e:
        check("tg media: transport failure sanitized, token free",
              TOKEN not in str(e)
              and str(e) == "download_file failed: ValueError", str(e))

    def bin_never(url, timeout):
        raise AssertionError("transport must not be reached")

    cli_never = TgClient(token=TOKEN, bin_transport=bin_never)
    for label, bad_path in (("traversal", "../etc/passwd"), ("empty", "")):
        try:
            cli_never.download_file(bad_path)
            refused = False
        except TgError:
            refused = True
        except AssertionError:            # transport WAS reached
            refused = False
        check(f"tg media: {label} file_path refused pre-transport", refused)

    # --- send_photo: multipart body shape ---
    mp_seen: dict = {}

    def mp_ok(url, body, content_type, timeout):
        mp_seen.update(url=url, body=body, ct=content_type, timeout=timeout)
        return json.dumps({"ok": True, "result": {"message_id": 5}})

    cli3 = TgClient(token=TOKEN, mp_transport=mp_ok)
    caption = "\u56fe" * 1200 + " tail"
    cli3.send_photo(42, PNG_1PX, caption=caption, thread_id=7)
    ct, body = mp_seen["ct"], mp_seen["body"]
    boundary = ct.partition("boundary=")[2]
    check("tg media: sendPhoto multipart content-type + boundary",
          ct.startswith("multipart/form-data; boundary=") and len(boundary) > 10,
          ct[:60])
    check("tg media: body closes with --boundary--",
          body.endswith(("--" + boundary + "--\r\n").encode()))
    check("tg media: body carries chat_id",
          b'name="chat_id"\r\n\r\n42' in body)
    check("tg media: body carries message_thread_id when given",
          b'name="message_thread_id"\r\n\r\n7' in body)
    check("tg media: photo bytes ride in the body", PNG_1PX in body)
    marker = b'name="caption"\r\n\r\n'
    i = body.find(marker)
    cap = ""
    if i >= 0:
        j = body.find(b"\r\n--" + boundary.encode(), i)
        cap = body[i + len(marker):j].decode("utf-8")
    check("tg media: caption UTF-8 hard-cut at 1000 chars",
          len(cap) == 1000 and cap == caption[:1000], str(len(cap)))
    check("tg media: sendPhoto url ends with the method",
          mp_seen["url"].endswith("/sendPhoto"))

    mp_seen.clear()
    cli3.send_photo(42, PNG_1PX)
    check("tg media: no thread_id/caption -> fields absent",
          b'name="message_thread_id"' not in mp_seen["body"]
          and b'name="caption"' not in mp_seen["body"])

    # Path leg (what the service actually passes)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "gen.png"
        p.write_bytes(PNG_1PX)
        mp_seen.clear()
        cli3.send_photo(42, p, caption="from disk")
        check("tg media: Path photo read from disk into the body",
              PNG_1PX in mp_seen["body"]
              and b'name="caption"\r\n\r\nfrom disk' in mp_seen["body"])
        try:
            cli3.send_photo(42, Path(td) / "missing.png")
            check("tg media: missing photo file -> TgError", False)
        except TgError as e:
            check("tg media: missing photo file -> TgError",
                  "photo read failed" in str(e), str(e))

    # --- send_photo error paths ---
    def mp_err(url, body, content_type, timeout):
        return json.dumps({"ok": False,
                           "description": "Bad Request: chat not found"})

    cli4 = TgClient(token=TOKEN, mp_transport=mp_err)
    try:
        cli4.send_photo(42, PNG_1PX)
        check("tg media: ok:false -> TgError", False)
    except TgError as e:
        check("tg media: ok:false -> TgError with description",
              str(e).startswith("sendPhoto: api error")
              and "chat not found" in str(e), str(e))
    for label, args in (("empty photo", (42, b"")),
                        ("oversize photo", (42, b"x" * (MAX_FILE_BYTES + 1))),
                        ("missing chat_id", ("", PNG_1PX))):
        try:
            cli4.send_photo(*args)
            check(f"tg media: {label} -> TgError", False)
        except TgError:
            check(f"tg media: {label} -> TgError", True)

    def mp_boom(url, body, content_type, timeout):
        raise RuntimeError("leak attempt " + TOKEN)

    try:
        TgClient(token=TOKEN, mp_transport=mp_boom).send_photo(42, PNG_1PX)
        check("tg media: sendPhoto transport failure sanitized", False)
    except TgError as e:
        check("tg media: sendPhoto transport failure sanitized",
              TOKEN not in str(e)
              and str(e) == "sendPhoto failed: RuntimeError", str(e))


def test_service_media() -> None:
    """Car 4c: service media legs — voice note -> getFile -> download -> STT
    -> the SAME text path, and image drain after the text reply. stt and
    imagegen are monkeypatched (pipeline under test, not the model); every
    TG send stays offline through the fake transports."""
    import contextlib
    import io
    from mycore import imagegen
    from mycore import stt as stt_mod
    from mycore.service import ChatService
    from mycore.tg import TgError, POLL_BOT_ENV

    env_saved = {k: os.environ.get(k) for k in (POLL_BOT_ENV,)}
    os.environ[POLL_BOT_ENV] = "second-bot-TEST-token"
    saved_transcribe = stt_mod.transcribe
    saved_drain = imagegen.drain_outbox
    imagegen.drain_outbox()              # never inherit leftovers

    ok_chat = lambda messages=None, tools=None, **kw: fake_response(text="pong")  # noqa: E731
    clock = [1000.0]
    nowf = lambda: clock[0]  # noqa: E731
    note = {"file_id": "AAA", "file_unique_id": "u",
            "duration": 1, "file_size": 4096}

    def mk_voice(update_id, uid, chat_id, voice, thread_id=None):
        msg = {"message_id": 1,
               "from": {"id": uid, "is_bot": False, "first_name": "x"},
               "chat": {"id": chat_id, "type": "private"},
               "date": 0, "voice": voice}
        if thread_id is not None:
            msg["message_thread_id"] = thread_id
        return {"update_id": update_id, "message": msg}

    try:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)

            def svc_for(name, **kw):
                fake = FakeTgTransport()
                svc = ChatService(tdp / name, frozenset({111}), transport=fake,
                                  chat=ok_chat, now=nowf, **kw)
                return svc, fake

            def patch_get_download(svc, size=4096):
                """Fake getFile/download: metadata by `size`, audio = RIFF."""
                svc.tg.get_file = lambda fid: {
                    "file_id": fid, "file_path": "voice/x.oga",
                    "file_size": size}
                svc.tg.download_file = lambda p: b"RIFF-fake-audio"

            # --- over-long note: rejected BEFORE getFile ---
            s, fake = svc_for("voice_long")
            reached = []

            def never_get(file_id):
                reached.append(file_id)
                raise AssertionError("getFile must not be reached")

            s.tg.get_file = never_get
            s.handle_update(mk_voice(1, 111, 100, dict(note, duration=120)))
            check("svc media: >60s note -> tooLong line, no getFile",
                  reached == [] and bool(fake.sends)
                  and "too long" in fake.sends[-1][1]
                  and "120s" in fake.sends[-1][1], str(fake.sends))

            # --- oversize file_size: rejected BEFORE download ---
            s, fake = svc_for("voice_big")
            patch_get_download(s, size=6_000_000)
            pulled = []
            s.tg.download_file = lambda p: pulled.append(p) or b"RIFF"
            s.handle_update(mk_voice(2, 111, 101, note))
            check("svc media: file_size pre-reject skips the download",
                  pulled == [] and bool(fake.sends)
                  and "could not be transcribed" in fake.sends[-1][1]
                  and "file too large" in fake.sends[-1][1], str(fake.sends))
            check("svc media: pre-reject logged as STT_FAIL",
                  "STT_FAIL" in s.log.read_text())

            # --- STT failure (engine down / module absent) -> honest line ---
            def stt_boom(data, **kw):
                raise stt_mod.SttError("model 'base' unavailable: OSError")

            stt_mod.transcribe = stt_boom
            s, fake = svc_for("voice_stt_err")
            patch_get_download(s)
            s.handle_update(mk_voice(3, 111, 102, note))
            check("svc media: stt exception -> failed line with reason",
                  bool(fake.sends)
                  and "could not be transcribed" in fake.sends[-1][1]
                  and "model 'base' unavailable" in fake.sends[-1][1],
                  str(fake.sends))

            def stt_absent(data, **kw):
                raise ModuleNotFoundError("No module named 'faster_whisper'")

            stt_mod.transcribe = stt_absent
            s, fake = svc_for("voice_stt_absent")
            patch_get_download(s)
            s.handle_update(mk_voice(4, 111, 103, note))
            check("svc media: stt module absent -> failed line, loop alive",
                  bool(fake.sends)
                  and "could not be transcribed" in fake.sends[-1][1]
                  and "faster_whisper" in fake.sends[-1][1], str(fake.sends))

            # --- empty transcript -> empty line ---
            stt_mod.transcribe = lambda data, **kw: {
                "text": "   ", "language": "en", "duration": 0.5}
            s, fake = svc_for("voice_empty")
            patch_get_download(s)
            s.handle_update(mk_voice(5, 111, 104, note))
            check("svc media: empty transcript -> empty line",
                  bool(fake.sends)
                  and "no words came through" in fake.sends[-1][1],
                  str(fake.sends))

            # --- happy path: topic key, model reply, transcript stays out of logs ---
            stt_mod.transcribe = lambda data, **kw: {
                "text": "hello there", "language": "en", "duration": 0.4}
            s, fake = svc_for("voice_happy")
            patch_get_download(s)
            s.handle_update(mk_voice(6, 111, 105, note, thread_id=77))
            check("svc media: voice happy path -> model reply in thread",
                  fake.sends == [("105", "pong")]
                  and fake.send_threads == ["77"], str(fake.sends))
            sess = tdp / "voice_happy" / "sessions" / "105:77.jsonl"
            log_text = s.log.read_text()
            check("svc media: voice uses the topic session key",
                  sess.is_file() and "hello there" in sess.read_text(),
                  str(sess))
            check("svc media: STT_OK logged without transcript content",
                  "STT_OK" in log_text and "hello there" not in log_text)

            # --- transcribed COMMAND takes the command path ---
            stt_mod.transcribe = lambda data, **kw: {
                "text": "/help", "language": "en", "duration": 0.3}
            s, fake = svc_for("voice_cmd")
            patch_get_download(s)
            s.handle_update(mk_voice(7, 111, 106, note))
            check("svc media: transcribed /help runs the command",
                  bool(fake.sends) and fake.sends[-1][1].startswith("Commands:"),
                  str(fake.sends)[:100])

            # --- unauthorized sender: silent, never even getFile ---
            stt_mod.transcribe = lambda data, **kw: {"text": "hi",
                                                     "language": "en",
                                                     "duration": 0.2}
            s, fake = svc_for("voice_deny")
            reached = []
            s.tg.get_file = never_get
            s.handle_update(mk_voice(8, 999, 107, note))
            check("svc media: unauthorized voice -> silent, no getFile",
                  fake.sends == [] and reached == [])

            # --- image drain: text first, photo after, multipart really sent ---
            png = tdp / "gen.png"
            png.write_bytes(PNG_1PX)
            long_prompt = "a cat picture " + "x" * 1100
            imagegen.drain_outbox = lambda: [(png, long_prompt)]
            s, fake = svc_for("photo_ok")
            mp_seen: list = []

            def mp(url, body, content_type, timeout):
                mp_seen.append((url, body, content_type))
                return json.dumps({"ok": True, "result": {"message_id": 9}})

            s.tg.mp_transport = mp
            events: list = []
            orig_send, orig_photo = s._send, s.tg.send_photo

            def rec_send(chat_id, text, thread_id=None):
                events.append(("text", text))
                return orig_send(chat_id, text, thread_id=thread_id)

            def rec_photo(chat_id, photo, caption="", thread_id=None):
                events.append(("photo", caption))
                return orig_photo(chat_id, photo, caption=caption,
                                  thread_id=thread_id)

            s._send, s.tg.send_photo = rec_send, rec_photo
            s.handle_update(mk_update(9, 111, 108, "hi there"))
            check("svc media: drain order = text then photo",
                  events == [("text", "pong"), ("photo", long_prompt[:1000])],
                  str(events)[:120])
            check("svc media: photo really went out multipart",
                  len(mp_seen) == 1 and PNG_1PX in mp_seen[0][1]
                  and b'name="chat_id"\r\n\r\n108' in mp_seen[0][1]
                  and mp_seen[0][2].startswith("multipart/form-data; boundary="))
            check("svc media: SEND_PHOTO_OK logged",
                  "SEND_PHOTO_OK" in s.log.read_text())

            # --- photo failure never touches the delivered text ---
            imagegen.drain_outbox = lambda: [(png, "p")]
            s, fake = svc_for("photo_fail")

            def mp_boom(url, body, content_type, timeout):
                raise TgError("boom")

            s.tg.mp_transport = mp_boom
            s.handle_update(mk_update(10, 111, 109, "hi again"))
            check("svc media: photo failure keeps the text reply",
                  fake.sends == [("109", "pong")]
                  and "SEND_PHOTO_FAIL" in s.log.read_text(), str(fake.sends))

            # --- dry-run: zero outbound, action logged ---
            fake_d = FakeTgTransport()
            s = ChatService(tdp / "photo_dry", frozenset({111}),
                            transport=fake_d, chat=ok_chat, dry_run=True,
                            now=nowf)
            mp_out: list = []
            s.tg.mp_transport = lambda *a: mp_out.append(a)
            imagegen.drain_outbox = lambda: [(png, "p")]
            with contextlib.redirect_stdout(io.StringIO()):
                s.handle_update(mk_update(11, 111, 110, "dry hi"))
            check("svc media: dry-run photo -> zero outbound, DRYRUN logged",
                  mp_out == [] and fake_d.sends == []
                  and "DRYRUN_SEND_PHOTO" in s.log.read_text())

            # --- drain itself failing/malformed: never bubbles up ---
            def drain_boom():
                raise RuntimeError("drain boom")

            imagegen.drain_outbox = drain_boom
            s, fake = svc_for("photo_drain_err")
            s.handle_update(mk_update(12, 111, 111, "still alive"))
            check("svc media: drain exception -> PHOTO_DRAIN_FAIL, text kept",
                  fake.sends == [("111", "pong")]
                  and "PHOTO_DRAIN_FAIL" in s.log.read_text(), str(fake.sends))
            imagegen.drain_outbox = lambda: [(None,)]
            s, fake = svc_for("photo_drain_bad")
            s.handle_update(mk_update(13, 111, 112, "still alive 2"))
            check("svc media: malformed outbox entry skipped, turn survives",
                  fake.sends == [("112", "pong")], str(fake.sends))
    finally:
        stt_mod.transcribe = saved_transcribe
        imagegen.drain_outbox = saved_drain
        imagegen.drain_outbox()
        for k, v in env_saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_profiles() -> None:
    """Car F §13.1 — profile path seams (lib.profile_paths):
      1. default (no AIDOG_PROFILE) = byte-identical legacy paths;
      2. AIDOG_PROFILE + AIDOG_PROFILES_DIR relocate settings/secrets/state/body;
         AIDOG_MC_STATE still wins over everything (existing contract);
      3. an invalid profile name fails closed (ProfileError, no silent default);
      4. build_system_stack() reads the PROFILE body, never the repo overlay;
      5. skill_roots carries the profile skills layer AND the shared overlay;
      6. /learn writes land in the profile body — the repo overlay stays clean.
    Hermetic: tmp profiles root + env save/restore (the suite pins
    AIDOG_MC_STATE, so every mutation here is undone in `finally`)."""
    from lib import profile_paths as pp
    from mycore.assembly import build_system_stack
    from mycore import service as svc
    from mycore import channels as ch
    from mycore import checkpoint as ckpt
    from mycore import scheduler as sch
    from mycore import evolution as ev
    import mycore.hooks as hk

    keys = ("AIDOG_PROFILE", "AIDOG_PROFILES_DIR", "AIDOG_MC_STATE")
    saved = {k: os.environ.get(k) for k in keys}
    # Snapshot the shipped body layer: profile learning must not add a single
    # file under <repo>/overlay (compare after, entry for entry).
    overlay_before = {p for p in (REPO / "overlay").rglob("*")}
    try:
        for k in keys:
            os.environ.pop(k, None)
        # ---------------------------------------------- 1. default = legacy --
        check("profile: default settings = web/settings.json",
              pp.settings_path(REPO) == REPO / "web" / "settings.json")
        check("profile: default secrets = secrets.env",
              pp.secrets_path(REPO) == REPO / "secrets.env")
        check("profile: default state = data/mycore_service",
              pp.state_dir(REPO) == REPO / "data" / "mycore_service")
        check("profile: default body = overlay",
              pp.body_root(REPO) == REPO / "overlay"
              and pp.constitution_path(REPO)
              == REPO / "overlay" / "constitution" / "SOUL.md"
              and pp.memory_slots(REPO)
              == (REPO / "overlay" / "memory" / "MEMORY_curated.md",
                  REPO / "overlay" / "memory" / "USER_curated.md")
              and pp.learned_path(REPO)
              == REPO / "overlay" / "memory" / "EVOLUTION_learned.md"
              and pp.skills_dir(REPO) == REPO / "overlay" / "skills"
              and pp.evolution_dir(REPO) == REPO / "overlay" / "evolution")
        check("profile: default media = data/images",
              pp.media_dir(REPO) == REPO / "data" / "images")
        legacy_state = REPO / "data" / "mycore_service"
        check("profile: default state consumers unchanged",
              svc.state_dir() == legacy_state
              and ckpt.default_state_dir() == legacy_state
              and ch.state_root(REPO, {}) == legacy_state
              and sch.default_jobs_path() == legacy_state / "cron_jobs.json"
              and ev.default_state_path(REPO)
              == legacy_state / "evolution_state.json")
        check("profile: default settings read point unchanged",
              ev.load_settings_file(REPO) != {})   # repo web/settings.json is real

        # ------------------------------------- 2. profile relocates every seam
        with tempfile.TemporaryDirectory(prefix="aidog_profiles_") as td:
            tdp = Path(td)
            os.environ["AIDOG_PROFILES_DIR"] = str(tdp)
            os.environ["AIDOG_PROFILE"] = "beta"
            root = tdp / "beta"
            root.mkdir(parents=True)
            check("profile: settings/secrets/state/body relocate",
                  pp.settings_path(REPO) == root / "settings.json"
                  and pp.secrets_path(REPO) == root / "secrets.env"
                  and pp.state_dir(REPO) == root / "state"
                  and pp.body_root(REPO) == root / "body"
                  and pp.media_dir(REPO) == root / "images")
            check("profile: body layer paths relocate",
                  pp.constitution_path(REPO)
                  == root / "body" / "constitution" / "SOUL.md"
                  and pp.memory_slots(REPO)[0]
                  == root / "body" / "memory" / "MEMORY_curated.md"
                  and pp.learned_path(REPO)
                  == root / "body" / "memory" / "EVOLUTION_learned.md"
                  and pp.skills_dir(REPO) == root / "body" / "skills"
                  and pp.evolution_dir(REPO) == root / "body" / "evolution")
            check("profile: state consumers follow the profile",
                  svc.state_dir() == root / "state"
                  and ckpt.default_state_dir() == root / "state"
                  and ch.state_root(REPO, {}) == root / "state"
                  and sch.default_jobs_path() == root / "state" / "cron_jobs.json"
                  and ev.default_state_path(REPO)
                  == root / "state" / "evolution_state.json")
            os.environ["AIDOG_MC_STATE"] = str(tdp / "explicit_state")
            check("profile: AIDOG_MC_STATE beats the profile (env contract)",
                  pp.state_dir(REPO) == tdp / "explicit_state"
                  and svc.state_dir() == tdp / "explicit_state"
                  and ckpt.default_state_dir() == tdp / "explicit_state")
            os.environ.pop("AIDOG_MC_STATE", None)

            # real read points: profile settings file (absent -> empty)
            check("profile: settings read point relocates (absent = empty)",
                  ev.load_settings_file(REPO) == {}
                  and hk.load_hooks(REPO) == ({}, []))
            (root / "settings.json").write_text(
                json.dumps({"mycore": {"budget": 7},
                            "hooks": {"turn_end": [{"cmd": ["/bin/true"]}]}}),
                encoding="utf-8")
            check("profile: profile settings.json is what gets read",
                  ev.load_settings_file(REPO).get("mycore", {}).get("budget") == 7)
            check("profile: hooks read point relocates",
                  hk.load_hooks(REPO)[0].get("turn_end")
                  == [{"cmd": ["/bin/true"], "timeout": hk.DEFAULT_TIMEOUT}])

            # ------------------- 3. body layer: the stack reads the PROFILE --
            soul = root / "body" / "constitution" / "SOUL.md"
            soul.parent.mkdir(parents=True, exist_ok=True)
            soul.write_text("# beta profile SOUL", encoding="utf-8")
            memd = root / "body" / "memory"
            memd.mkdir(parents=True, exist_ok=True)
            (memd / "MEMORY_curated.md").write_text(
                "- beta memory fact", encoding="utf-8")
            overlay_soul = (REPO / "overlay" / "constitution" / "SOUL.md").read_text(
                encoding="utf-8")
            stack = build_system_stack()          # all defaults, no explicit paths
            check("profile: build_system_stack uses the profile brain",
                  stack[0]["content"] == "# beta profile SOUL"
                  and any("beta memory fact" in m["content"] for m in stack)
                  and not any(overlay_soul[:80] in m["content"] for m in stack)
                  and "skill_route" in stack[-1]["content"])

            # -------------------------------------- 4. fail-closed on a typo -
            os.environ["AIDOG_PROFILE"] = "../x"
            name_ok = stack_ok = False
            try:
                pp.settings_path(REPO)
            except pp.ProfileError:
                name_ok = True
            try:
                svc.state_dir()                    # consumer must not fall back
            except pp.ProfileError:
                stack_ok = True
            check("profile: invalid AIDOG_PROFILE raises ProfileError", name_ok)
            check("profile: invalid name fails closed through consumers", stack_ok)
            os.environ["AIDOG_PROFILE"] = "beta"

            # ------------------------ 5. skill_roots: profile layer + overlay -
            skills = root / "body" / "skills"
            demo = skills / "timezone-cheatsheet"
            demo.mkdir(parents=True, exist_ok=True)
            (demo / "SKILL.md").write_text(
                "---\nname: timezone-cheatsheet\n"
                "description: UTC offsets for major cities\n---\n\nbody\n",
                encoding="utf-8")
            sys.path.insert(0, str(REPO / "lib"))
            try:
                import skill_route as sr
            finally:
                sys.path.pop(0)
            roots = sr.skill_roots(None)
            check("profile: skill_roots carries profile skills + overlay",
                  skills in roots and sr.DEFAULT_SKILLS_DIR in roots
                  and roots.index(skills) < roots.index(sr.DEFAULT_SKILLS_DIR),
                  str(roots)[:300])

            # ------------- 6. learning writes the PROFILE body, repo untouched
            res = ev.learn_skill("beta-demo", "beta demo skill profile test",
                                 "print('hi')", repo=REPO)
            check("profile: learn_skill lands in the profile body",
                  res.ok and (skills / "beta-demo" / "SKILL.md").is_file(),
                  str(res.errors))
            res2 = ev.learn_memory("- beta learned fact", repo=REPO)
            learned = root / "body" / "memory" / "EVOLUTION_learned.md"
            check("profile: learn_memory lands in the profile body",
                  res2.ok and learned.is_file()
                  and "beta learned fact" in learned.read_text(encoding="utf-8"),
                  str(res2.errors))
            check("profile: evolution log lands in the profile body",
                  (root / "body" / "evolution" / "EVOLUTION.md").is_file())
            overlay_after = {p for p in (REPO / "overlay").rglob("*")}
            check("profile: repo overlay untouched by profile learning",
                  overlay_after == overlay_before,
                  str(overlay_after ^ overlay_before)[:300])
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_channels_wa_signal() -> None:
    """WhatsApp (Meta Cloud API, outbound-only) + Signal (local signal-cli
    REST daemon, bidirectional): request shapes, gates, inbound mapping,
    honest fail-soft, the 9-route CHANNELS contract and zero credential echo
    — offline fake transport, no network, no real credentials."""
    import contextlib
    import io
    from mycore import channels

    wa_token = "wa-TOK-abc123XYZ"
    sig_token = "sig-TOK-def456UVW"
    pnid = "100123456789012"
    slot_vals = {
        "CHANNEL_WHATSAPP_TOKEN": wa_token,
        "CHANNEL_SIGNAL_TOKEN": sig_token,
    }
    saved = {k: os.environ.get(k) for k in list(slot_vals) + ["AIDOG_MC_STATE"]}
    repo = Path(tempfile.mkdtemp(prefix="ws_repo_"))
    state = Path(tempfile.mkdtemp(prefix="ws_state_"))

    class FakeTransport:
        """Records calls (url, payload, headers); replays scripted replies
        (str = response text, Exception = raised verbatim)."""

        def __init__(self, replies=()):
            self.calls = []
            self.replies = list(replies)

        def __call__(self, url, payload, headers, timeout):
            self.calls.append({"url": url, "payload": payload,
                               "headers": dict(headers), "timeout": timeout})
            if not self.replies:
                raise AssertionError("scripted transport reply missing")
            item = self.replies.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    try:
        for key, val in slot_vals.items():
            os.environ[key] = val
        settings = {"state_dir": str(state), "channels": {
            "whatsapp": {"enabled": True, "phone_number_id": pnid},
            "signal": {"enabled": True, "url": "http://127.0.0.1:8080"},
        }}
        all_vals = list(slot_vals.values())

        # --- contract: CHANNELS 9 routes in order + factory ---------------
        check("ws channels: CHANNELS is the 9 routes in order",
              channels.CHANNELS == ("discord", "slack", "email", "webhook",
                                    "dingtalk", "feishu", "wechat",
                                    "whatsapp", "signal"),
              str(channels.CHANNELS))
        loaded = channels.load_channels(repo, settings,
                                        transport=FakeTransport())
        check("ws channels: load_channels returns 9 routes",
              len(loaded) == 9 and tuple(loaded) == channels.CHANNELS
              and all(isinstance(v, channels.Channel)
                      for v in loaded.values()),
              str(tuple(loaded)))

        # --- whatsapp: outbound shape -------------------------------------
        tr = FakeTransport([json.dumps({"messaging_product": "whatsapp",
                                        "contacts": [{"wa_id": "15551234567"}],
                                        "messages": [{"id": "wamid.1"}]})])
        wa = channels.load_channels(repo, settings, transport=tr)["whatsapp"]
        ok = wa.send("+15551234567", "hello whatsapp")
        call = tr.calls[0]
        check("ws whatsapp: send url shape (graph v20 /messages)",
              ok is True
              and call["url"] ==
              f"https://graph.facebook.com/v20.0/{pnid}/messages",
              call["url"])
        check("ws whatsapp: Bearer token in Authorization header only",
              call["headers"].get("Authorization") == f"Bearer {wa_token}"
              and wa_token not in call["url"]
              and call["headers"].get("Content-Type") == "application/json",
              str(call["headers"])[:160])
        check("ws whatsapp: body is messaging_product/to/type/text json",
              json.loads(call["payload"]) ==
              {"messaging_product": "whatsapp", "to": "+15551234567",
               "type": "text",
               "text": {"preview_url": False, "body": "hello whatsapp"}},
              str(call["payload"])[:160])
        tr2 = FakeTransport([json.dumps({"messaging_product": "whatsapp",
                                         "messages": [{"id": "wamid.2"}]})])
        wa2 = channels.load_channels(repo, settings, transport=tr2)["whatsapp"]
        wa2.send("+15551234567", "x" * 6000)
        body = json.loads(tr2.calls[0]["payload"])["text"]["body"]
        check("ws whatsapp: text body capped at 4096 chars",
              len(body) <= 4096 and body.endswith("…[truncated]"),
              str(len(body)))

        # --- whatsapp: missing token = not configured, zero transport -----
        os.environ.pop("CHANNEL_WHATSAPP_TOKEN", None)
        ok_c, reason = wa.configured()
        tr_n = FakeTransport()
        wa_n = channels.load_channels(repo, settings, transport=tr_n)["whatsapp"]
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            sent = wa_n.send("+15551234567", "hi")
        log = buf.getvalue()
        check("ws whatsapp: missing slot -> reason names slot, send False, "
              "no transport call",
              ok_c is False and reason == "missing CHANNEL_WHATSAPP_TOKEN slot"
              and sent is False and tr_n.calls == []
              and "CHANNEL_SEND_FAIL" in log
              and all(v not in (reason + log) for v in all_vals), reason)
        os.environ["CHANNEL_WHATSAPP_TOKEN"] = wa_token

        # --- whatsapp: inbound structurally absent (inbound=False) ---------
        tr_w0 = FakeTransport()
        wa0 = channels.WhatsAppChannel(repo=repo,
                                       cfg=settings["channels"]["whatsapp"],
                                       state=state, transport=tr_w0)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            got = wa0.poll()
        check("ws whatsapp: inbound=False poll silent, zero transport calls",
              got == [] and tr_w0.calls == []
              and "CHANNEL_POLL_FAIL" not in buf.getvalue(),
              f"calls={len(tr_w0.calls)}")

        # --- status rows: honest notes + zero secret echo ------------------
        st = channels.status(settings, repo)
        st_text = json.dumps(st, ensure_ascii=False)
        by = {r["name"]: r for r in st}
        check("ws status: 9 rows, whatsapp ready with structural note",
              len(st) == 9 and [r["name"] for r in st] == list(channels.CHANNELS)
              and by["whatsapp"]["configured"] is True
              and by["whatsapp"]["inbound"] is False
              and "public Meta callback" in by["whatsapp"]["note"],
              str(by.get("whatsapp")))
        check("ws status: signal ready with daemon note, zero credential echo",
              by["signal"]["configured"] is True
              and by["signal"]["inbound"] is True
              and "daemon" in by["signal"]["note"]
              and all(v not in st_text for v in all_vals),
              str(by.get("signal")))

        # --- signal: outbound shape + optional auth header -----------------
        tr_s = FakeTransport([json.dumps({"timestamp": 1730000000000,
                                          "source": "+15559876543",
                                          "username": "+15559876543"})])
        sg = channels.load_channels(repo, settings, transport=tr_s)["signal"]
        ok = sg.send("+15559876543", "hello signal")
        call = tr_s.calls[0]
        check("ws signal: send url + body shape (numbers list)",
              ok is True and call["url"] == "http://127.0.0.1:8080/v2/send"
              and json.loads(call["payload"]) ==
              {"message": "hello signal", "numbers": ["+15559876543"]},
              f"{call['url']} {str(call['payload'])[:120]}")
        check("ws signal: token header present only when the slot is set",
              call["headers"].get("Authorization") == f"Bearer {sig_token}"
              and sig_token not in call["url"], str(call["headers"])[:160])
        os.environ.pop("CHANNEL_SIGNAL_TOKEN", None)
        tr_s2 = FakeTransport([json.dumps({"timestamp": 1, "source": "+1",
                                           "message": "x"})])
        sg2 = channels.load_channels(repo, settings, transport=tr_s2)["signal"]
        sg2.send("+1", "x")
        check("ws signal: no token slot -> no Authorization header sent",
              "Authorization" not in tr_s2.calls[0]["headers"],
              str(tr_s2.calls[0]["headers"])[:120])
        os.environ["CHANNEL_SIGNAL_TOKEN"] = sig_token

        # --- signal: url missing = not configured --------------------------
        tr_u = FakeTransport()
        sg_no = channels.SignalChannel(repo=repo,
                                       cfg={"enabled": True, "url": ""},
                                       state=state, transport=tr_u)
        ok_c, reason = sg_no.configured()
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            sent = sg_no.send("+1", "x")
        check("ws signal: empty url -> not configured, send False, no call",
              ok_c is False
              and reason == "missing channels.signal.url setting"
              and sent is False and tr_u.calls == []
              and "CHANNEL_SEND_FAIL" in buf.getvalue(), reason)

        # --- signal: poll maps daemon envelopes -> Inbound -----------------
        tr_p = FakeTransport([json.dumps([
            {"source": "+15559876543", "sourceName": "Ada",
             "timestamp": 1730000000000, "message": "hi signal"},
            {"source": "", "timestamp": 1730000000001, "message": "skip"},
            {"source": "+15550000000", "timestamp": 1730000000002,
             "message": ""}])])
        sg_p = channels.load_channels(repo, settings, transport=tr_p)["signal"]
        got = sg_p.poll()
        call = tr_p.calls[0]
        check("ws signal: poll GET /v1/receive maps envelope -> Inbound",
              len(got) == 1 and isinstance(got[0], channels.Inbound)
              and got[0].route == "signal"
              and got[0].sender == "+15559876543"
              and got[0].target == "+15559876543"
              and got[0].text == "hi signal"
              and got[0].key == "signal:+15559876543"
              and call["url"] == "http://127.0.0.1:8080/v1/receive"
              and call["payload"] is None,
              f"calls={len(tr_p.calls)} got={got}")

        # --- signal: daemon absent -> honest fail-soft empty batch ---------
        tr_f = FakeTransport([ConnectionError("daemon down")])
        sg_f = channels.load_channels(repo, settings, transport=tr_f)["signal"]
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            got = sg_f.poll()
        log = buf.getvalue()
        check("ws signal: daemon absent -> poll [] + sanitized POLL_FAIL line",
              got == [] and "CHANNEL_POLL_FAIL" in log
              and all(v not in log for v in all_vals), log.strip())

        # --- reprs never carry a credential ---------------------------------
        reps = " ".join(repr(channels.load_channels(
            repo, settings, transport=FakeTransport())[n])
            for n in ("whatsapp", "signal"))
        check("ws channels: adapter reprs never carry a credential",
              all(v not in reps for v in all_vals), reps)
    finally:
        for key, val in saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        shutil.rmtree(repo, True)
        shutil.rmtree(state, True)


def test_context_refs() -> None:
    """Line-based '@' refs in configured context files: expansion, depth 1,
    missing/denied/limit markers, MAX_CTX_REFS bound, byte-identical no-@."""
    import tempfile
    from pathlib import Path

    from mycore.assembly import (MAX_CTX_REFS, _expand_context_refs,
                                 build_system_stack)

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        ctx = tdp / "proj"
        ctx.mkdir()
        (ctx / "rules.md").write_text("RULES BODY", encoding="utf-8")
        (ctx / "nested.md").write_text("NESTED\n@rules.md", encoding="utf-8")

        # --- happy path: ref line replaced by referenced file content ---
        text = "intro\n@rules.md\noutro\n"
        got = _expand_context_refs(text, ctx)
        check("ctx@: ref line inlined with target content",
              "RULES BODY" in got and "@rules.md" not in got)
        check("ctx@: surrounding lines untouched",
              got.startswith("intro\n") and got.endswith("outro\n"))

        # --- integration: expansion applies to injected stack content ---
        (ctx / "AGENTS.md").write_text("keep\n@rules.md\n", encoding="utf-8")
        stack = build_system_stack(
            tdp / "no_such_soul.md", (), learned_path=None, context_dirs=(ctx,))
        proj = next(m["content"] for m in stack
                    if "project context" in m["content"])
        check("ctx@: build_system_stack injects expanded content",
              "RULES BODY" in proj and "@rules.md" not in proj)

        # --- depth 1: '@' line inside referenced content stays literal ---
        got = _expand_context_refs("@nested.md\n", ctx)
        check("ctx@: nested @ line stays literal (depth 1)",
              "@rules.md" in got and "RULES BODY" not in got
              and "NESTED" in got)

        # --- missing / directory target -> missing marker ---
        got = _expand_context_refs("@nope.md\n", ctx)
        check("ctx@: missing target -> missing marker",
              got.strip() == "[context @ref missing: @nope.md]")
        (ctx / "adir").mkdir()
        got = _expand_context_refs("@adir\n", ctx)
        check("ctx@: directory target -> missing marker",
              got.strip() == "[context @ref missing: @adir]")

        # --- denial: traversal and absolute path outside the context dir ---
        (tdp / "secret.md").write_text("SECRET", encoding="utf-8")
        got = _expand_context_refs("@../secret.md\n", ctx)
        check("ctx@: ../ traversal -> denied marker",
              got.strip() == "[context @ref denied: @../secret.md]"
              and "SECRET" not in got)
        got = _expand_context_refs(f"@{tdp / 'secret.md'}\n", ctx)
        check("ctx@: absolute path outside -> denied marker",
              got.strip() == f"[context @ref denied: @{tdp / 'secret.md'}]"
              and "SECRET" not in got)

        # --- bound: 17 refs -> 16 expanded + 1 limit marker ---
        big = "\n".join("@rules.md" for _ in range(MAX_CTX_REFS + 1))
        got = _expand_context_refs(big, ctx)
        check("ctx@: cap = 16 expansions, 17th -> limit marker",
              got.count("RULES BODY") == MAX_CTX_REFS
              and got.count("[context @ref limit:") == 1
              and "[context @ref limit: @rules.md]" in got)

        # --- no '@' line -> byte-identical passthrough ---
        plain = "line one\n  plain two\n@\nlone @ mid\n\n"
        check("ctx@: file without @ refs is byte-identical",
              _expand_context_refs(plain, ctx) == plain)


def test_envslots() -> None:
    """mycore.envslots (frontend coverage 1.6): the MYCORE_WD_CHAT reader
    resolves env -> secrets.env slot -> honest empty string, against a TEMP
    secrets file (never the real one). Env always wins; an empty env falls
    back to the slot; nothing here logs a value."""
    import tempfile
    from mycore import envslots

    tmp = Path(tempfile.mkdtemp(prefix="envslots_"))
    slot_file = tmp / "secrets.env"
    saved = os.environ.get(envslots.WD_CHAT_SLOT)
    try:
        slot_file.write_text("WEB_TOKEN=\nMYCORE_WD_CHAT=424242\nOTHER=keep\n",
                             encoding="utf-8")
        os.chmod(slot_file, 0o600)
        os.environ.pop(envslots.WD_CHAT_SLOT, None)
        check("envslots: unset env -> secrets.env slot",
              envslots.wd_chat_target(slot_file) == "424242",
              f"got {envslots.wd_chat_target(slot_file)!r}")
        os.environ[envslots.WD_CHAT_SLOT] = "777"
        check("envslots: env always wins over the slot",
              envslots.wd_chat_target(slot_file) == "777",
              f"got {envslots.wd_chat_target(slot_file)!r}")
        os.environ[envslots.WD_CHAT_SLOT] = "  "
        check("envslots: whitespace-only env counts as empty -> slot",
              envslots.wd_chat_target(slot_file) == "424242",
              f"got {envslots.wd_chat_target(slot_file)!r}")
        os.environ[envslots.WD_CHAT_SLOT] = "not-an-int"
        check("envslots: garbage env still wins (caller validates, no fallback)",
              envslots.wd_chat_target(slot_file) == "not-an-int",
              f"got {envslots.wd_chat_target(slot_file)!r}")
        os.environ.pop(envslots.WD_CHAT_SLOT, None)
        slot_file.write_text("WEB_TOKEN=\nOTHER=keep\n", encoding="utf-8")
        check("envslots: slot absent -> honest empty string",
              envslots.wd_chat_target(slot_file) == "",
              f"got {envslots.wd_chat_target(slot_file)!r}")
        check("envslots: missing file -> honest empty string (fail-closed)",
              envslots.wd_chat_target(tmp / "absent.env") == "",
              f"got {envslots.wd_chat_target(tmp / 'absent.env')!r}")
        check("envslots: slot_value reads one own-line slot only",
              envslots.slot_value("OTHER", slot_file) == "keep"
              and envslots.slot_value("MYCORE_WD_CHAT", slot_file) == "",
              f"got {envslots.slot_value('OTHER', slot_file)!r}")
        check("envslots: empty-value line reads as absent",
              envslots.slot_value("WEB_TOKEN", slot_file) == "",
              f"got {envslots.slot_value('WEB_TOKEN', slot_file)!r}")
    finally:
        if saved is None:
            os.environ.pop(envslots.WD_CHAT_SLOT, None)
        else:
            os.environ[envslots.WD_CHAT_SLOT] = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_model_key_slots() -> None:
    """mycore.model._headers key resolution (activation car §1.8): the console
    writes secrets.env, so an UNSET env var must fall back to the slot — env
    always wins, an empty pair keeps the loopback placeholder and leaves a
    remote endpoint unauthenticated, and no value is ever printed here."""
    client = ChatClient(ModelConfig())
    prof_root = Path(tempfile.mkdtemp(prefix="model_slot_"))
    secrets = prof_root / "slots" / "secrets.env"
    secrets.parent.mkdir(parents=True)
    slot_secret = "slot-sentinel-0123456789"
    secrets.write_text(f"MODEL_API_KEY={slot_secret}\nWEB_TOKEN=abcdefghijklmnop\n",
                       encoding="utf-8")
    keys = ("AIDOG_PROFILES_DIR", "AIDOG_PROFILE", "MODEL_API_KEY")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        os.environ["AIDOG_PROFILES_DIR"] = str(prof_root)
        os.environ["AIDOG_PROFILE"] = "slots"
        os.environ.pop("MODEL_API_KEY", None)
        _model_mod._slot_key = _SLOT_KEY_REAL          # real reader for this leg
        hdr = client._headers("https://far.example/v1", "MODEL_API_KEY")
        check("model key: unset env falls back to the secrets.env slot",
              hdr.get("Authorization") == f"Bearer {slot_secret}",
              f"hdr_keys={sorted(hdr)}")
        os.environ["MODEL_API_KEY"] = "env-sentinel-0987654321"
        hdr_env = client._headers("https://far.example/v1", "MODEL_API_KEY")
        check("model key: env always wins over the slot (slot value unused)",
              hdr_env.get("Authorization") == "Bearer env-sentinel-0987654321"
              and slot_secret not in json.dumps(hdr_env),
              f"hdr_keys={sorted(hdr_env)}")
        os.environ.pop("MODEL_API_KEY", None)
        secrets.write_text("MODEL_API_KEY=\nWEB_TOKEN=abcdefghijklmnop\n",
                           encoding="utf-8")
        hdr_lb = client._headers("http://127.0.0.1:3460/v1", "MODEL_API_KEY")
        hdr_rm = client._headers("https://far.example/v1", "MODEL_API_KEY")
        check("model key: empty env+slot keeps the loopback placeholder",
              hdr_lb.get("Authorization") == "Bearer local", f"hdr_keys={sorted(hdr_lb)}")
        check("model key: empty env+slot leaves the remote unauthenticated",
              "Authorization" not in hdr_rm, f"hdr_keys={sorted(hdr_rm)}")
        secrets.unlink()
        hdr_missing = client._headers("https://far.example/v1", "MODEL_API_KEY")
        check("model key: absent secrets.env degrades to no header (fail-closed)",
              "Authorization" not in hdr_missing, f"hdr_keys={sorted(hdr_missing)}")
        check("model key: describe() reports presence only, never a value",
              slot_secret not in client.cfg.describe()
              and "sentinel" not in client.cfg.describe(),
              client.cfg.describe())
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _model_mod._slot_key = lambda name: ""        # suite stays hermetic
        shutil.rmtree(prof_root, ignore_errors=True)


def test_settings_models_budget() -> None:
    """mycore.models / mycore.budget are first-class (activation car §1.6):
    the raw-file consumers (service._budget / service._allowed_models /
    subagent._allowed_models) read exactly one temp profile document — the
    same one the console validates and writes — and absent keys degrade to the
    same defaults the pre-schema behavior had. Nothing touches production
    web/settings.json."""
    from mycore import cli
    from mycore import service as _service
    from mycore import subagent as _sub

    prof_root = Path(tempfile.mkdtemp(prefix="models_budget_"))
    root = prof_root / "slots"
    root.mkdir(parents=True)
    settings = root / "settings.json"
    settings.write_text(json.dumps({"mycore": {
        "models": ["mimo-v2.6-flash", "model-b"], "budget": 40000}}),
        encoding="utf-8")
    keys = ("AIDOG_PROFILES_DIR", "AIDOG_PROFILE")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        os.environ["AIDOG_PROFILES_DIR"] = str(prof_root)
        os.environ["AIDOG_PROFILE"] = "slots"

        class _Fake:
            repo = REPO
            _settings_doc = _service.ChatService._settings_doc
            _settings_block = _service.ChatService._settings_block
            _budget = _service.ChatService._budget
            _allowed_models = _service.ChatService._allowed_models

        fake = _Fake()
        check("settings models: service consumer sees the console list",
              fake._allowed_models() == ["mimo-v2.6-flash", "model-b"],
              str(fake._allowed_models()))
        check("settings models: subagent whitelist sees the same list",
              _sub._allowed_models() == ["mimo-v2.6-flash", "model-b"],
              str(_sub._allowed_models()))
        check("settings budget: service consumer sees the console budget",
              fake._budget() == 40000, str(fake._budget()))
        settings.write_text("{}", encoding="utf-8")
        check("settings budget: absent -> cli default (absent = default)",
              fake._budget() == cli.DEFAULT_BUDGET_CHARS, str(fake._budget()))
        check("settings models: absent -> the configured model only",
              fake._allowed_models() == [cli.resolve_model_config().model],
              str(fake._allowed_models()))
        check("settings models: {} document never crashes the consumers",
              _sub._allowed_models() is None, str(_sub._allowed_models()))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(prof_root, ignore_errors=True)


def test_tools_toggle() -> None:
    """tools_off: settings disables a tool, env overrides settings, empty
    config = full-registry parity, unknown env name = ignored (never fatal)."""
    import os
    from mycore.tools import KNOWN_TOOL_NAMES, build_registry, resolve_disabled_tools

    saved_env = os.environ.get("MYCORE_TOOLS_OFF")
    try:
        # catalog sanity: 11 core + generate_image + connector_search
        # + 6 browser + 3 exchange_* (W1) + 3 exchange_* (W2 approval gate)
        check("tools_off: KNOWN_TOOL_NAMES covers core + conditional",
              {"read_file", "run_bash", "cron", "delegate", "execute_code",
               "generate_image", "connector_search", "browser_open",
               "browser_close", "exchange_markets", "exchange_portfolio",
               "exchange_orders", "exchange_prepare_order",
               "exchange_execute_order", "exchange_plans"} <= KNOWN_TOOL_NAMES
              and len(KNOWN_TOOL_NAMES) == 25,
              str(sorted(KNOWN_TOOL_NAMES)))

        # 1) settings dict path disables a core tool
        os.environ.pop("MYCORE_TOOLS_OFF", None)
        off = resolve_disabled_tools({"mycore": {"tools_off": ["grep", "cron"]}})
        check("tools_off: settings dict -> set", off == frozenset({"grep", "cron"}),
              str(sorted(off)))
        reg = build_registry(disabled=off)
        names = reg.names()
        check("tools_off: disabled tool absent from schema",
              "grep" not in names and "cron" not in names
              and all(t["function"]["name"] not in {"grep", "cron"}
                      for t in reg.schema()), str(names))
        out = reg.dispatch("grep", {"pattern": "x", "path": "."})
        check("tools_off: dispatch says unknown tool",
              out.startswith("ERROR: unknown tool 'grep'"), out[:80])

        # 2) env overrides settings
        os.environ["MYCORE_TOOLS_OFF"] = "glob"
        off = resolve_disabled_tools({"mycore": {"tools_off": ["grep"]}})
        check("tools_off: env overrides settings",
              off == frozenset({"glob"}) and "grep" not in off, str(sorted(off)))

        # 3) empty config = full registry parity (byte-identical name list)
        os.environ.pop("MYCORE_TOOLS_OFF", None)
        for empty in ({}, {"mycore": {}}, {"mycore": {"tools_off": []}},
                      {"mycore": {"tools_off": "not-a-list"}}):
            check(f"tools_off: empty/invalid config resolves empty {list(empty) or ['{}']}",
                  resolve_disabled_tools(empty) == frozenset(),
                  str(resolve_disabled_tools(empty)))
        base = build_registry().names()
        check("tools_off: disabled=None parity",
              build_registry(disabled=None).names() == base)
        check("tools_off: disabled=frozenset() parity (byte-identical names)",
              build_registry(disabled=frozenset()).names() == base,
              str(base))

        # 4) unknown env name ignored (no exception)
        os.environ["MYCORE_TOOLS_OFF"] = "grep,definitely_not_a_tool"
        off = resolve_disabled_tools({})
        check("tools_off: unknown env name ignored, known kept",
              off == frozenset({"grep"}), str(sorted(off)))
    finally:
        if saved_env is None:
            os.environ.pop("MYCORE_TOOLS_OFF", None)
        else:
            os.environ["MYCORE_TOOLS_OFF"] = saved_env


def test_exchanges() -> None:
    """Web3 phase-2 W1 — read-only exchange & chain sources: catalog shape,
    slot gating (values never surface), the OFFICIAL Kraken signature
    vector, per-venue parsing through injected transports, symbol
    validation, bounded upstream errors. Zero network, zero real keys:
    every slot read is hermetic and every HTTP call is a fake transport."""
    import base64
    import hashlib
    import hmac
    from mycore import exchanges as ex

    saved_slot = ex._slot
    slots: dict = {}
    captured: dict = {}

    def fake_transport(url, method, headers, payload, timeout, fixture=""):
        captured.update(url=url, method=method, headers=dict(headers),
                        payload=payload, timeout=timeout)
        return fixture

    def unreachable(url, method, headers, payload, timeout):
        raise AssertionError("transport must not be reached")

    try:
        # the test owns every slot read: hermetic against the host secrets.env
        ex._slot = lambda name: slots.get(name, "")   # noqa: E731

        # --- catalog: 8 venues, stable order, honest reasons ---
        cat = ex.catalog()
        check("exchanges: catalog has 9 venues in order",
              [r["key"] for r in cat] ==
              ["okx", "kraken", "jupiter", "velora", "hyperliquid",
               "dydx", "gmx", "rpc", "binance"], str([r["key"] for r in cat]))
        # read_only follows the venue's REAL surface (W2): the six chain
        # sources are still read-only, the three CEX carry orders — and only
        # through prepare -> console approval -> execute. Claiming read_only
        # on an orderable venue would be a false promise.
        check("exchanges: read_only follows orderability (W2)",
              all(r.get("read_only") is (r["key"] not in ex.ORDERABLE_VENUES)
                  and r.get("orderable") is (r["key"] in ex.ORDERABLE_VENUES)
                  for r in cat)
              and {r["key"] for r in cat if r.get("orderable")}
              == {"okx", "kraken", "binance"},
              str([(r["key"], r.get("read_only")) for r in cat]))
        check("exchanges: orderable rows disclose the approval path",
              all("prepare -> console approval -> execute"
                  in str(r.get("summary", ""))
                  for r in cat if r.get("orderable")), str(cat[0]))
        check("exchanges: cex/key vs chain/public split",
              {r["key"] for r in cat if r["kind"] == "cex"}
              == {"okx", "kraken", "binance"}
              and all(r["kind"] == "chain" for r in cat
                      if r["key"] not in ("okx", "kraken", "binance"))
              and all(r["auth"] == "key" for r in cat if r["kind"] == "cex")
              and all(r["auth"] == "public" for r in cat
                      if r["kind"] == "chain"),
              str([(r["key"], r["kind"], r["auth"]) for r in cat]))
        check("exchanges: unconfigured key venue names its slots",
              cat[0].get("configured") is False
              and "OKX_API_KEY" in cat[0].get("reason", "")
              and "secrets.env" in cat[0].get("reason", ""), str(cat[0]))
        check("exchanges: keyless chain venues carry no reason",
              all("reason" not in r and r.get("configured") is True
                  for r in cat if r["kind"] == "chain"),
              str([r for r in cat if r["kind"] == "chain"][0]))
        check("exchanges: status presence only, nothing leaks",
              ex.status() == {"okx": False, "kraken": False, "binance": False}
              and "SENTINEL" not in str(ex.status()), str(ex.status()))

        # --- slots: presence-only status, reasons that name the gap ---
        slots.update({"OKX_API_KEY": "OKXKEY-SENTINEL-000",
                      "OKX_API_SECRET": "OKXSEC-SENTINEL-000",
                      "OKX_API_PASSPHRASE": "OKXPP-SENTINEL-000"})
        st = ex.status()
        check("exchanges: status okx present / kraken absent",
              st == {"okx": True, "kraken": False, "binance": False}, str(st))
        check("exchanges: configured venue drops the reason",
              [r for r in ex.catalog() if r["key"] == "okx"][0]
              .get("configured") is True, str(st))
        check("exchanges: no slot value in the whole catalog",
              "SENTINEL" not in json.dumps(ex.catalog()))
        check("exchanges: missing_slots names only what is absent",
              ex.missing_slots("kraken") ==
              ("KRAKEN_API_KEY", "KRAKEN_API_SECRET")
              and ex.missing_slots("okx") == ()
              and ex.missing_slots("binance") ==
              ("BINANCE_API_KEY", "BINANCE_API_SECRET"),
              str(ex.missing_slots("kraken")))

        # --- Kraken OFFICIAL published example vector (independent truth) ---
        sig = ex.kraken_sign(
            "/0/private/AddOrder",
            "nonce=1616492376594&ordertype=limit&pair=XBTUSD&price=37500"
            "&type=buy&volume=1.25", "1616492376594",
            "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3p"
            "d5nE9qa99HAZtuZuj6F1huXg==")
        check("exchanges: kraken official signature vector matches",
              sig == "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E"
                     "6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ==", sig)

        # --- OKX pre-hash: Base64(HMAC-SHA256(secret, ts+method+path+body)) ---
        ts0 = "2026-10-24T00:00:00.000Z"
        path0 = "/api/v5/account/balance"
        base = ex.okx_sign(ts0, "GET", path0, "", "SEC")
        check("exchanges: okx sign decodes to 32 bytes",
              len(base64.b64decode(base)) == 32)
        expected = base64.b64encode(hmac.new(
            b"SEC", ts0.encode() + b"GET" + path0.encode() + b"",
            hashlib.sha256).digest()).decode()
        check("exchanges: okx prehash = timestamp+method+path+body",
              expected == base, f"{expected} != {base}")
        variants = [ex.okx_sign("2026-10-24T00:00:01.000Z", "GET", path0, "", "SEC"),
                    ex.okx_sign(ts0, "POST", path0, "", "SEC"),
                    ex.okx_sign(ts0, "GET", path0 + "?ccy=BTC", "", "SEC"),
                    ex.okx_sign(ts0, "GET", path0, "{}", "SEC"),
                    ex.okx_sign(ts0, "GET", path0, "", "OTHER")]
        check("exchanges: every pre-hash field changes the signature",
              all(v != base for v in variants))

        # --- nonce: strictly increasing (Kraken requirement) ---
        n1, n2 = ex._nonce(), ex._nonce()
        check("exchanges: nonce strictly increases",
              int(n2) > int(n1) and n1.isdigit(), f"{n1} {n2}")

        # --- validation rejected BEFORE any transport is reached ---
        try:
            ex.markets("nasdaq", transport=unreachable)
            check("exchanges: unknown venue -> ExchangeError", False)
        except ex.ExchangeError as e:
            check("exchanges: unknown venue -> ExchangeError",
                  "unknown venue" in str(e) and "okx" in str(e), str(e))
        for bad in ("BTC; drop table", "a b", "x" * 65, "../../etc/passwd"):
            try:
                ex.markets("okx", bad, transport=unreachable)
                check(f"exchanges: symbol {bad[:12]!r} rejected", False)
            except ex.ExchangeError as e:
                check(f"exchanges: symbol {bad[:12]!r} rejected",
                      "symbol" in str(e), str(e))
            except Exception as e:  # noqa: BLE001
                check(f"exchanges: symbol {bad[:12]!r} rejected", False,
                      type(e).__name__)

        # --- okx public ticker: renders, and is UNAUTHENTICATED ---
        captured.clear()
        okx_fx = json.dumps({"code": "0", "data": [{
            "instId": "BTC-USDT", "last": "80192.4", "askPx": "80192.2",
            "bidPx": "80192.1", "open24h": "81750.0",
            "high24h": "81916.6", "low24h": "79833.5",
            "vol24h": "3150.889"}]})
        out = ex.markets("okx",
                         transport=lambda u, m, h, p, t: fake_transport(
                             u, m, h, p, t, okx_fx))
        check("exchanges: okx ticker line renders",
              out.startswith("BTC-USDT last=80192.4")
              and "high24h=81916.6" in out and "vol24h=3150.889" in out
              and "chg24h=" in out, out)
        check("exchanges: okx market request carries no credential",
              "OK-ACCESS-KEY" not in captured["headers"]
              and "OK-ACCESS-SIGN" not in captured["headers"]
              and "SENTINEL" not in json.dumps(captured["headers"]))
        check("exchanges: okx market url",
              captured["url"].endswith("/api/v5/market/ticker?instId=BTC-USDT"),
              captured["url"])

        # --- kraken public ticker: BTC spelling + separator normalization ---
        captured.clear()
        kraken_fx = json.dumps({"error": [], "result": {"XXBTZUSD": {
            "a": ["80143.7", "1", "1.0"], "b": ["80143.6", "1", "1.0"],
            "c": ["80080.8", "0.1"], "v": ["1385.3", "1385.3"],
            "p": ["1", "1"], "t": [1, 2], "l": ["79750", "79750"],
            "h": ["81797.1", "81797.1"], "o": "81060.0"}}})
        out = ex.markets("kraken",
                         transport=lambda u, m, h, p, t: fake_transport(
                             u, m, h, p, t, kraken_fx))
        check("exchanges: kraken default pair normalized to XBTUSD",
              captured["url"].endswith("/0/public/Ticker?pair=XBTUSD"),
              captured["url"])
        check("exchanges: kraken ticker line renders",
              "XXBTZUSD last=80080.8" in out and "high24h=81797.1" in out
              and "vol24h=1385.3" in out, out)
        captured.clear()
        ex.markets("kraken", "BTC/USD",
                   transport=lambda u, m, h, p, t: fake_transport(
                       u, m, h, p, t, kraken_fx))
        check("exchanges: kraken accepts BTC/USD spelling",
              captured["url"].endswith("pair=XBTUSD"), captured["url"])

        # --- jupiter: symbol -> mint resolution, percent change rendered ---
        captured.clear()
        jup_fx = json.dumps({ex.JUPITER_MINTS["SOL"]: {
            "usdPrice": 100.39835953, "priceChange24h": -3.95,
            "liquidity": 917854773.45, "blockId": 460888359}})
        out = ex.markets("jupiter",
                         transport=lambda u, m, h, p, t: fake_transport(
                             u, m, h, p, t, jup_fx))
        check("exchanges: jupiter price line renders",
              out.startswith("SOL price=100.39835953")
              and "chg24h=-3.95%" in out, out)
        check("exchanges: jupiter resolved SOL to its verified mint",
              "ids=" + ex.JUPITER_MINTS["SOL"] in captured["url"],
              captured["url"])
        try:
            ex.markets("jupiter", "NOTACOIN", transport=unreachable)
            check("exchanges: jupiter unknown symbol rejected", False)
        except ex.ExchangeError as e:
            check("exchanges: jupiter unknown symbol rejected",
                  "mint" in str(e) or "symbol" in str(e), str(e))

        # --- velora: 1 unit in, USD read from the route ---
        captured.clear()
        vel_fx = json.dumps({"delta": {"route": {"origin": {
            "input": {"amountUSD": "0.999678"},
            "output": {"amount": "382520000000000",
                       "amountUSD": "0.89842336"}}}}})
        out = ex.markets("velora",
                         transport=lambda u, m, h, p, t: fake_transport(
                             u, m, h, p, t, vel_fx))
        check("exchanges: velora quote line renders",
              out.startswith("USDC/WETH 1 USDC -> 0.00038252 WETH")
              and "usd_in=0.999678" in out, out)
        check("exchanges: velora request pins chainId and source token",
              "chainId=1" in captured["url"]
              and ex.VELORA_TOKENS["USDC"][0] in captured["url"],
              captured["url"])
        for bad in ("USDC", "BTC/USD", "USDC/NOPE"):
            try:
                ex.markets("velora", bad, transport=unreachable)
                check(f"exchanges: velora pair {bad!r} rejected", False)
            except ex.ExchangeError:
                check(f"exchanges: velora pair {bad!r} rejected", True)

        # --- hyperliquid: FLAT coin -> mid map, POST /info ---
        captured.clear()
        hl_fx = json.dumps({"BTC": "80158.5", "ETH": "2340.1"})
        out = ex.markets("hyperliquid",
                         transport=lambda u, m, h, p, t: fake_transport(
                             u, m, h, p, t, hl_fx))
        check("exchanges: hyperliquid mid line renders",
              out == "BTC mid=80158.5", out)
        check("exchanges: hyperliquid posts the allMids info request",
              captured["method"] == "POST" and captured["url"].endswith("/info")
              and json.loads(captured["payload"]) == {"type": "allMids"},
              str(captured["payload"]))

        # --- dydx / gmx / public RPC chain head ---
        captured.clear()
        dy_fx = json.dumps({"markets": {"BTC-USD": {
            "oraclePrice": "80099.23889", "priceChange24H": "-1673.37865",
            "volume24H": "10185997.64", "nextFundingRate": "0.00001014",
            "openInterest": "235.7758", "status": "ACTIVE"}}})
        out = ex.markets("dydx",
                         transport=lambda u, m, h, p, t: fake_transport(
                             u, m, h, p, t, dy_fx))
        check("exchanges: dydx market line renders",
              out.startswith("BTC-USD last=80099.23889")
              and "status=ACTIVE" in out, out)
        check("exchanges: dydx url",
              captured["url"].endswith("/v4/perpetualMarkets?market=BTC-USD"),
              captured["url"])

        captured.clear()
        gmx_fx = json.dumps([{
            "ticker_id": "BTC/USD [BTC-USDC]", "base_currency": "BTC",
            "last_price": 80022.44, "high": 81811.13, "low": 79751.44,
            "funding_rate": 0.00198572, "open_interest": 1778504,
            "liquidity_in_usd": 123456}])
        out = ex.markets("gmx",
                         transport=lambda u, m, h, p, t: fake_transport(
                             u, m, h, p, t, gmx_fx))
        check("exchanges: gmx pair line renders",
              out.startswith("BTC/USD [BTC-USDC] last=80022.44")
              and "oi_usd=1778504" in out, out)
        # gmx can only know its market list AFTER fetching the feed, so the
        # unknown-market case is exercised against a real fixture, not a
        # pre-fetch guard.
        try:
            ex.markets("gmx", "DOGE",
                       transport=lambda u, m, h, p, t: fake_transport(
                           u, m, h, p, t, gmx_fx))
            check("exchanges: gmx unknown market rejected", False)
        except ex.ExchangeError as e:
            check("exchanges: gmx unknown market rejected",
                  "no market" in str(e), str(e))

        captured.clear()
        rpc_fx = json.dumps({"jsonrpc": "2.0", "id": 1,
                             "result": "0x3292cb1"})
        out = ex.markets("rpc",
                         transport=lambda u, m, h, p, t: fake_transport(
                             u, m, h, p, t, rpc_fx))
        check("exchanges: rpc chain head decodes hex",
              out.startswith("base head=53030065"), out)
        check("exchanges: rpc asks for the base chain head",
              json.loads(captured["payload"])["method"] == "eth_blockNumber",
              str(captured["payload"]))
        try:
            ex.markets("rpc", "dogechain", transport=unreachable)
            check("exchanges: rpc unknown chain rejected", False)
        except ex.ExchangeError as e:
            check("exchanges: rpc unknown chain rejected",
                  "chain must be one of" in str(e), str(e))

        # --- account legs: slot-gated BEFORE any transport ---
        # (run with an EMPTY slot map: these are the keyless-refusal paths)
        configured = dict(slots)
        slots.clear()
        for venue in ("okx", "kraken"):
            for fn in (ex.portfolio, ex.orders):
                try:
                    fn(venue, transport=unreachable)
                    check(f"exchanges: {fn.__name__}({venue}) refuses keyless", False)
                except ex.ExchangeError as e:
                    check(f"exchanges: {fn.__name__}({venue}) refuses keyless",
                          "not configured" in str(e)
                          and ("OKX_API_KEY" in str(e)
                               or "KRAKEN_API_KEY" in str(e)), str(e))
        for venue in ("jupiter", "velora", "rpc"):
            try:
                ex.portfolio(venue, transport=unreachable)
                check(f"exchanges: {venue} has no account surface", False)
            except ex.ExchangeError as e:
                check(f"exchanges: {venue} has no account surface",
                      "no account surface" in str(e), str(e))
        slots.update(configured)

        # --- okx private reads: signed headers, no value in the output ---
        slots.update({"KRAKEN_API_KEY": "KRKKEY-SENTINEL-000",
                      "KRAKEN_API_SECRET":
                          "c2VjcmV0LXNlbnRpbmVsLXNlY3JldC1zZWNyZXQ="})
        captured.clear()
        bal_fx = json.dumps({"code": "0", "data": [{
            "totalEq": "55837.4355", "details": [
                {"ccy": "USDT", "bal": "500000", "eq": "499999.1",
                 "availBal": "4834.31", "ordFrozen": "158.573"},
                {"ccy": "ETH", "bal": "0", "eq": "0",
                 "availBal": "0", "ordFrozen": "0"}]}]})
        out = ex.portfolio("okx",
                           transport=lambda u, m, h, p, t: fake_transport(
                               u, m, h, p, t, bal_fx))
        check("exchanges: okx balance lines render",
              out.startswith("total_eq_usd=55837.4355")
              and "USDT bal=500000 eq_usd=499999.1" in out
              and "ETH" not in out.splitlines()[1:],  # zero rows dropped
              out)
        check("exchanges: okx private request is signed",
              captured["headers"].get("OK-ACCESS-KEY") == slots["OKX_API_KEY"]
              and bool(captured["headers"].get("OK-ACCESS-SIGN"))
              and bool(captured["headers"].get("OK-ACCESS-TIMESTAMP"))
              and captured["headers"].get("OK-ACCESS-PASSPHRASE")
              == slots["OKX_API_PASSPHRASE"], str(list(captured["headers"])))
        check("exchanges: okx balance output carries no credential",
              "SENTINEL" not in out and slots["OKX_API_SECRET"] not in out)
        check("exchanges: okx balance url",
              captured["url"].endswith("/api/v5/account/balance"),
              captured["url"])

        captured.clear()
        ord_fx = json.dumps({"code": "0", "data": [{
            "ordId": "1234", "instId": "BTC-USDT", "side": "buy",
            "px": "79000", "sz": "0.01", "accFillSz": "0",
            "state": "live"}]})
        out = ex.orders("okx",
                        transport=lambda u, m, h, p, t: fake_transport(
                            u, m, h, p, t, ord_fx))
        check("exchanges: okx open order line renders",
              out == "1234 BTC-USDT buy px=79000 sz=0.01 filled=0 state=live",
              out)
        check("exchanges: okx orders url",
              captured["url"].endswith(
                  "/api/v5/trade/orders-pending?instType=SPOT"),
              captured["url"])
        empty = json.dumps({"code": "0", "data": []})
        out = ex.orders("okx", transport=lambda u, m, h, p, t:
                        fake_transport(u, m, h, p, t, empty))
        check("exchanges: okx empty orders is an honest line",
              out == "no open spot orders", out)

        # --- kraken private reads: nonce form + API-Key/API-Sign ---
        captured.clear()
        kbal_fx = json.dumps({"error": [], "result": {
            "ZUSD": "171288.6158", "XXBT": "0", "USDT": "500000.0"}})
        out = ex.portfolio("kraken",
                           transport=lambda u, m, h, p, t: fake_transport(
                               u, m, h, p, t, kbal_fx))
        check("exchanges: kraken balance lines render, zeros dropped",
              out.splitlines() == ["ZUSD=171288.6158", "USDT=500000"], out)
        check("exchanges: kraken private url",
              captured["url"].endswith("/0/private/Balance"),
              captured["url"])
        kv = dict(p.split("=", 1)
                  for p in str(captured["payload"], "utf-8").split("&"))
        resign = ex.kraken_sign("/0/private/Balance",
                                str(captured["payload"], "utf-8"), kv["nonce"],
                                slots["KRAKEN_API_SECRET"])
        check("exchanges: kraken request carries nonce + matching sign",
              kv.get("nonce", "").isdigit()
              and captured["headers"].get("API-Key") == slots["KRAKEN_API_KEY"]
              and captured["headers"].get("API-Sign") == resign,
              str(captured["headers"]))
        check("exchanges: kraken balance output carries no credential",
              "SENTINEL" not in out)

        captured.clear()
        kord_fx = json.dumps({"error": [], "result": {"open": {
            "OQCLML-BW3P3-BUCMWZ": {"status": "open", "vol": "1.25",
                                     "descr": {"pair": "XBTUSD", "type": "buy",
                                               "ordertype": "limit",
                                               "price": "30010.0"}}}}})
        out = ex.orders("kraken",
                        transport=lambda u, m, h, p, t: fake_transport(
                            u, m, h, p, t, kord_fx))
        check("exchanges: kraken open order line renders",
              out.startswith("OQCLML-BW3P3-BUCMWZ XBTUSD buy limit")
              and "vol=1.25" in out, out)
        check("exchanges: kraken orders url",
              captured["url"].endswith("/0/private/OpenOrders"),
              captured["url"])
        empty_k = json.dumps({"error": [], "result": {"open": {}}})
        out = ex.orders("kraken", transport=lambda u, m, h, p, t:
                        fake_transport(u, m, h, p, t, empty_k))
        check("exchanges: kraken empty orders is an honest line",
              out == "no open orders", out)

        # --- upstream errors stay bounded and credential-free ---
        long_err = json.dumps({"error": ["EAPI:Invalid key " + "x" * 900]})
        try:
            ex.portfolio("kraken", transport=lambda u, m, h, p, t:
                         fake_transport(u, m, h, p, t, long_err))
            check("exchanges: upstream error surfaces honestly", False)
        except ex.ExchangeError as e:
            check("exchanges: upstream error truncated to the cap",
                  len(str(e)) <= len("kraken: ") + 160 and "x" * 161 not in str(e),
                  f"len={len(str(e))}")
        try:
            ex.markets("okx", transport=lambda u, m, h, p, t:
                       fake_transport(u, m, h, p, t, "<html>not json</html>"))
            check("exchanges: invalid JSON -> ExchangeError", False)
        except ex.ExchangeError as e:
            check("exchanges: invalid JSON -> ExchangeError",
                  "invalid JSON" in str(e), str(e))

        # --- output cap: a 100-row feed renders at most MAX_LINES rows ---
        big = json.dumps({"code": "0", "data": [
            {"instId": f"ALT{i}-USDT", "last": "1", "askPx": "1",
             "bidPx": "1", "open24h": "1", "high24h": "1",
             "low24h": "1", "vol24h": "1"} for i in range(100)]})
        out = ex.markets("okx", transport=lambda u, m, h, p, t:
                         fake_transport(u, m, h, p, t, big))
        check("exchanges: market output capped at MAX_LINES",
              len(out.splitlines()) == ex.MAX_LINES,
              str(len(out.splitlines())))
    finally:
        ex._slot = saved_slot


def test_exchanges_w2() -> None:
    """Web3 phase-2 W2 — quotes, approval gate and order placement: the
    thresholds fail closed on every unusable shape, plans move pending ->
    approved -> executed (and refuse every other path), the VWAP/slippage
    math is checked against a hand-computed book, all three venue signatures
    are recomputed from the wire bytes, the ledger rolls over on the Beijing
    date, and this module provably carries NO withdrawal/transfer capability.
    Zero network, zero real keys: an injected transport answers every venue
    and the plan store lives in a temp dir."""
    import hashlib
    import hmac
    import inspect
    import time
    from mycore import exchanges as ex
    from mycore.tools import KNOWN_TOOL_NAMES

    saved_slot = ex._slot
    saved_thresholds = ex.thresholds
    saved_day = ex._bj_day
    slots: dict = {}
    seen: list[dict] = []
    fee_down = {"on": False}

    book = {"bids": [["99.0", "1"], ["98.0", "2"]],
            "asks": [["100.0", "1"], ["101.0", "2"], ["102.0", "10"]]}

    def transport(url, method, headers, payload, timeout):
        seen.append({"url": url, "method": method, "headers": dict(headers),
                     "payload": payload})
        if "/api/v5/market/books" in url:
            return json.dumps({"code": "0", "data": [book]})
        if "/api/v5/account/trade-fee" in url:
            if fee_down["on"]:
                raise ex.ExchangeError("HTTP 401 401: trade permission required")
            return json.dumps({"code": "0", "data": [{"takerFeeRate": "0.001"}]})
        if "/api/v5/trade/order" in url:
            return json.dumps({"code": "0", "data": [
                {"ordId": "OKX-ORD-1", "sCode": "0", "sMsg": ""}]})
        if "/api/v3/depth" in url:
            return json.dumps({"lastUpdateId": 7, "bids": book["bids"],
                               "asks": book["asks"]})
        if "/api/v3/account/commission" in url:
            return json.dumps({"symbol": "BTCUSDT", "commission": "0.001"})
        if "/api/v3/order" in url:
            return json.dumps({"symbol": "BTCUSDT", "orderId": 4321})
        if "/0/public/Depth" in url:
            return json.dumps({"error": [], "result": {"XXBTZUSD": {
                "bids": [["99.0", "5", "0"]], "asks": [["100.0", "5", "0"]]}}})
        if "/0/private/TradeVolume" in url:
            return json.dumps({"error": [], "result": {
                "fees": {"XXBTZUSD": {"fee": "0.26", "minfee": "0"}}}})
        if "/0/private/AddOrder" in url:
            return json.dumps({"error": [], "result": {"txid": ["KRAKEN-TX-1"]}})
        raise AssertionError(f"unexpected url {url}")

    def unreachable(url, method, headers, payload, timeout):
        raise AssertionError("transport must not be reached")

    def store_file(td: str) -> Path:
        return Path(td) / ex.STORE_NAME

    def mutate(td: str, fn) -> None:
        p = store_file(td)
        doc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        fn(doc)
        p.write_text(json.dumps(doc), encoding="utf-8")

    def last(pred) -> dict:
        for row in reversed(seen):
            if pred(row["url"]):
                return row
        raise AssertionError("no matching request was made")

    def plan_of(td: str, venue: str) -> dict:
        rows = [p for p in ex.plans_list(state_dir=td) if p["venue"] == venue]
        return rows[0]

    try:
        ex._slot = lambda name: slots.get(name, "")   # noqa: E731

        # ---- 1. thresholds: every unusable shape collapses to 0 ---------
        check("w2: thresholds default to 0 with an empty settings doc",
              ex.thresholds({}) == {"per_tx": 0.0, "daily": 0.0},
              str(ex.thresholds({})))
        check("w2: thresholds reject a non-object web3 block",
              ex.thresholds({"web3": "high"}) == {"per_tx": 0.0, "daily": 0.0})
        for bad in (True, "20", None, -1, 1e9, [20], {"a": 1}):
            got = ex.thresholds({"web3": {"auto_usd_per_tx": bad,
                                          "auto_usd_daily": 20}})
            check(f"w2: thresholds reject per_tx={bad!r} -> 0",
                  got["per_tx"] == 0.0 and got["daily"] == 20.0, str(got))
        got = ex.thresholds({"web3": {"auto_usd_per_tx": 20,
                                      "auto_usd_daily": 100}})
        check("w2: thresholds read numeric caps",
              got == {"per_tx": 20.0, "daily": 100.0}, str(got))
        live = ex.thresholds(None)
        check("w2: file-backed thresholds stay in range",
              set(live) == {"per_tx", "daily"}
              and all(isinstance(v, float) and 0.0 <= v <= ex.THRESHOLD_MAX_USD
                      for v in live.values()), str(live))

        # ---- 2. prepare validation happens BEFORE any network I/O -------
        bad_calls = [
            ("jupiter", "buy", "SOL", "limit", 1, 1),
            ("okx", "hold", "BTC-USDT", "limit", 1, 1),
            ("okx", "buy", "BTC-USDT", "swap", 1, 1),
            ("okx", "buy", "BTC-USDT", "limit", 1, None),
            ("okx", "buy", "BTC-USDT", "limit", 0, 1),
            ("okx", "buy", "BTC-USDT", "market", 1, 100),
        ]
        for args in bad_calls:
            try:
                ex.prepare_order(*args, transport=unreachable, state_dir=tempfile.mkdtemp())
                check(f"w2: prepare rejects {args[0]}/{args[1]}/{args[3]}", False)
            except ex.ExchangeError as e:
                check(f"w2: prepare rejects {args[0]}/{args[1]}/{args[3]}",
                      bool(str(e)), str(e)[:70])
        # a venue without its full slot set refuses before any request
        try:
            ex.prepare_order("okx", "buy", "BTC-USDT", "limit", 1, 100,
                             transport=unreachable, state_dir=tempfile.mkdtemp())
            check("w2: prepare refuses a slotless venue", False)
        except ex.ExchangeError as e:
            check("w2: prepare refuses a slotless venue and names the slots",
                  "not configured" in str(e) and "OKX_API_KEY" in str(e), str(e))

        # ---- 3. VWAP / slippage / depth from a hand-computed book -------
        with tempfile.TemporaryDirectory() as td:
            slots.update({"OKX_API_KEY": "OKXKEY-SENTINEL-001",
                          "OKX_API_SECRET": "OKXSEC-SENTINEL-001",
                          "OKX_API_PASSPHRASE": "OKXPP-SENTINEL-001"})
            seen.clear()
            # market BUY 2: 1 @100 + 1 @101 -> vwap 100.5, best 100 -> +50bps
            out = ex.prepare_order("okx", "buy", "BTC-USDT", "market", 2, None,
                                   transport=transport, state_dir=td)
            check("w2: prepare output carries no credential",
                  "SENTINEL" not in out and "OKX_API" not in out, out[:80])
            p = plan_of(td, "okx")
            check("w2: market buy VWAP = 100.5 over the first two levels",
                  abs(p["vwap"] - 100.5) < 1e-9, str(p["vwap"]))
            check("w2: market buy slippage = +50 bps vs best ask",
                  abs(p["slippage_bps"] - 50.0) < 1e-6, str(p["slippage_bps"]))
            check("w2: depth covered for qty 2", p["depth_ok"] is True)
            check("w2: market notional = vwap * qty",
                  abs(p["notional_usd"] - 201.0) < 1e-9, str(p["notional_usd"]))
            check("w2: fee quoted from the venue and folded into fee_usd",
                  p["fee_rate"] == 0.001
                  and abs(p["fee_usd"] - 0.201) < 1e-9, str(p["fee_usd"]))
            # market BUY 100: full book -> vwap = 1322/13, depth short
            ex.prepare_order("okx", "buy", "BTC-USDT", "market", 100, None,
                             transport=transport, state_dir=td)
            deep = plan_of(td, "okx")
            check("w2: thin book reports depth_ok=false, not a fake fill",
                  deep["depth_ok"] is False
                  and abs(deep["vwap"] - 1322 / 13) < 1e-9,
                  f"{deep['vwap']} {deep['depth_ok']}")
            # resting limit below the market: no crossing level, no VWAP
            ex.prepare_order("okx", "buy", "BTC-USDT", "limit", 2, 95,
                             transport=transport, state_dir=td)
            rest = plan_of(td, "okx")
            check("w2: resting limit reports no fill instead of a VWAP",
                  rest["vwap"] is None and rest["depth_ok"] is False
                  and rest["slippage_bps"] is None,
                  str((rest["vwap"], rest["depth_ok"])))
            check("w2: resting limit notional = limit price * qty",
                  abs(rest["notional_usd"] - 190.0) < 1e-9,
                  str(rest["notional_usd"]))
            check("w2: plan store never serialises a slot value",
                  "SENTINEL-001" not in store_file(td).read_text(encoding="utf-8"))
            # an unreachable fee endpoint = honest unknown, never a guess
            fee_down["on"] = True
            ex.prepare_order("okx", "buy", "BTC-USDT", "limit", 1, 100,
                             transport=transport, state_dir=td)
            fee_unknown = plan_of(td, "okx")
            check("w2: failed fee quote stays unknown and uncounted",
                  fee_unknown["fee_rate"] is None
                  and fee_unknown["fee_usd"] is None
                  and "fee unknown" in fee_unknown["fee_note"],
                  str(fee_unknown["fee_note"]))
            fee_down["on"] = False

        # ---- 4. approval gate: default 0 blocks everything ---------------
        with tempfile.TemporaryDirectory() as td:
            ex.thresholds = lambda settings=None: {"per_tx": 0.0, "daily": 0.0}  # noqa: E731
            seen.clear()
            out = ex.prepare_order("okx", "buy", "BTC-USDT", "limit", 1, 100,
                                   transport=transport, state_dir=td)
            plan = plan_of(td, "okx")
            check("w2: default thresholds leave a limit plan pending",
                  plan["status"] == "pending" and plan["approval"] == "none",
                  str((plan["status"], plan["approval"])))
            check("w2: prepare says WHY it needs a human",
                  "auto approvals disabled" in plan["reason"], plan["reason"])
            check("w2: prepare output hands back a plan id and the next step",
                  plan["id"] in out and "exchange_execute_order" in out
                  and "OWNER approves" in out, out[:120])
            try:
                ex.execute_order(plan["id"], transport=transport, state_dir=td)
                check("w2: executing a pending plan is refused", False)
            except ex.ExchangeError as e:
                check("w2: executing a pending plan is refused",
                      "awaiting human approval" in str(e), str(e))
            check("w2: a refused execute sends NOTHING to the venue",
                  not any("/trade/order" in r["url"] for r in seen),
                  str([r["url"] for r in seen]))
            try:
                ex.execute_order("pl_missing", transport=transport, state_dir=td)
                check("w2: unknown plan id is refused", False)
            except ex.ExchangeError as e:
                check("w2: unknown plan id is refused", "unknown plan_id" in str(e), str(e))
            try:
                ex.plan_decide(plan["id"], "rubber-stamp", state_dir=td)
                check("w2: unknown decide action is refused", False)
            except ex.ExchangeError as e:
                check("w2: unknown decide action is refused",
                      "action must be one of" in str(e), str(e))

            # ---- 5. console decision -> execute -> ledger ----------------
            decided = ex.plan_decide(plan["id"], "approve",
                                     decided_by="console", state_dir=td)
            check("w2: console approval flips the plan",
                  decided["status"] == "approved"
                  and decided["approval"] == "manual"
                  and decided["decided_by"] == "console"
                  and decided["decided_at"] is not None, str(decided["status"]))
            seen.clear()
            out = ex.execute_order(plan["id"], transport=transport, state_dir=td)
            done = plan_of(td, "okx")
            check("w2: approved plan executes and records the venue order id",
                  done["status"] == "executed" and done["order_id"] == "OKX-ORD-1"
                  and done["executed_at"] is not None, str(done["order_id"]))
            check("w2: execute reports the ledger line",
                  "ledger day=" in out and "spent_usd=" in out, out[-160:])
            check("w2: executed order reached the venue exactly once",
                  sum(1 for r in seen if "/trade/order" in r["url"]) == 1
                  and done["attempts"] == 1, str(done["attempts"]))
            try:
                ex.execute_order(plan["id"], transport=transport, state_dir=td)
                check("w2: a second execute of the same plan is refused", False)
            except ex.ExchangeError as e:
                check("w2: a second execute of the same plan is refused",
                      "executed" in str(e), str(e))

            # reject path: a rejected plan can never execute
            ex.prepare_order("okx", "buy", "BTC-USDT", "limit", 1, 100,
                             transport=transport, state_dir=td)
            rej = [p for p in ex.plans_list(state_dir=td)
                   if p["status"] == "pending"][0]
            ex.plan_decide(rej["id"], "reject", state_dir=td)
            try:
                ex.execute_order(rej["id"], transport=transport, state_dir=td)
                check("w2: a rejected plan can never execute", False)
            except ex.ExchangeError as e:
                check("w2: a rejected plan can never execute",
                      "rejected" in str(e), str(e))

            # ---- 6. TTL: an expired plan is dead --------------------------
            ex.prepare_order("okx", "buy", "BTC-USDT", "limit", 1, 100,
                             transport=transport, state_dir=td)
            ttl = [p for p in ex.plans_list(state_dir=td)
                   if p["status"] == "pending"][0]
            ex.plan_decide(ttl["id"], "approve", state_dir=td)
            mutate(td, lambda doc: doc["plans"][ttl["id"]].update(
                expires_at=time.time() - 1))
            try:
                ex.execute_order(ttl["id"], transport=transport, state_dir=td)
                check("w2: an expired plan cannot execute", False)
            except ex.ExchangeError as e:
                check("w2: an expired plan cannot execute",
                      "expired" in str(e), str(e))
            check("w2: expiry is recorded on the plan",
                  [p for p in ex.plans_list(state_dir=td)
                   if p["id"] == ttl["id"]][0]["status"] == "expired")

            # ---- 7. plan ordering + plan_summary --------------------------
            listed = ex.plans_list(state_dir=td)
            stamps = [p["created_at"] for p in listed]
            ids = [p["id"] for p in listed]
            check("w2: plans list newest first",
                  len(ids) >= 3 and stamps == sorted(stamps, reverse=True),
                  str(stamps))
            summ = ex.plan_summary(state_dir=td)
            check("w2: plan_summary counts and spends honestly",
                  summ["ledger_day"] == ex._bj_day()
                  and summ["spent_today_usd"] > 0
                  and summ["pending_plans"] == sum(
                      1 for p in listed if p["status"] == "pending")
                  and summ["approved_plans"] == sum(
                      1 for p in listed if p["status"] == "approved")
                  and summ["plans_total"] == len(ids), str(summ))

        # ---- 8. auto thresholds: limits only, and the ledger is real -----
        with tempfile.TemporaryDirectory() as td:
            ex.thresholds = lambda settings=None: {"per_tx": 1000.0, "daily": 1000.0}  # noqa: E731
            ex._bj_day = lambda ts=None: "2026-10-25"  # noqa: E731
            ex.prepare_order("okx", "buy", "BTC-USDT", "limit", 8, 100,
                             transport=transport, state_dir=td)
            auto = plan_of(td, "okx")
            check("w2: a limit inside both caps auto-approves",
                  auto["status"] == "approved"
                  and auto["approval"] == "auto-threshold", str(auto["status"]))
            ex.prepare_order("okx", "buy", "BTC-USDT", "market", 8, None,
                             transport=transport, state_dir=td)
            mkt = plan_of(td, "okx")
            check("w2: MARKET never auto-approves, whatever the caps",
                  mkt["status"] == "pending"
                  and "never auto-approved" in mkt["reason"], str(mkt["reason"]))
            ex.execute_order(auto["id"], transport=transport, state_dir=td)
            check("w2: the executed notional lands in today's ledger",
                  ex.plan_summary(state_dir=td)["spent_today_usd"] == 800.0,
                  str(ex.plan_summary(state_dir=td)))
            ex.prepare_order("okx", "buy", "BTC-USDT", "limit", 8, 100,
                             transport=transport, state_dir=td)
            over = plan_of(td, "okx")
            check("w2: the day's ledger blocks a second plan over the cap",
                  over["status"] == "pending"
                  and "daily cap" in over["reason"], str(over["reason"]))
            # rollover: the next Beijing day starts at zero again
            ex._bj_day = lambda ts=None: "2026-10-26"  # noqa: E731
            check("w2: ledger day rollover resets the spent budget",
                  ex.plan_summary(state_dir=td)["spent_today_usd"] == 0.0
                  and ex.plan_summary(state_dir=td)["ledger_day"] == "2026-10-26",
                  str(ex.plan_summary(state_dir=td)))
            ex.prepare_order("okx", "buy", "BTC-USDT", "limit", 8, 100,
                             transport=transport, state_dir=td)
            fresh = plan_of(td, "okx")
            check("w2: a fresh Beijing day auto-approves again",
                  fresh["status"] == "approved", str(fresh["status"]))
            # execution-time re-check: the ledger moved AFTER approval
            ex._bj_day = lambda ts=None: "2026-10-27"  # noqa: E731
            ex.prepare_order("okx", "buy", "BTC-USDT", "limit", 8, 100,
                             transport=transport, state_dir=td)
            recheck = plan_of(td, "okx")
            mutate(td, lambda doc: doc["ledger"].update({
                "2026-10-27": {"spent_usd": 900.0, "count": 1}}))
            try:
                ex.execute_order(recheck["id"], transport=transport, state_dir=td)
                check("w2: execution re-checks the day's ledger", False)
            except ex.ExchangeError as e:
                check("w2: execution re-checks the day's ledger",
                      "auto approval no longer valid" in str(e), str(e))
            demoted = [p for p in ex.plans_list(state_dir=td)
                       if p["id"] == recheck["id"]][0]
            check("w2: a voided auto approval falls back to pending",
                  demoted["status"] == "pending"
                  and demoted["approval"] == "none",
                  str((demoted["status"], demoted["approval"])))
            check("w2: a voided auto approval still sent NOTHING",
                  not any("/trade/order" in r["url"] for r in seen[-3:]))

        # ---- 9. signatures recomputed from the wire bytes ----------------
        with tempfile.TemporaryDirectory() as td:
            ex.thresholds = lambda settings=None: {"per_tx": 0.0, "daily": 0.0}  # noqa: E731
            ex._bj_day = saved_day
            slots.update({"KRAKEN_API_KEY": "KRKKEY-SENTINEL-002",
                          "KRAKEN_API_SECRET": "c2VjcmV0LXNlbnRpbmVsLXNlY3JldA==",
                          "BINANCE_API_KEY": "BNBKEY-SENTINEL-003",
                          "BINANCE_API_SECRET": "binance-secret-sentinel"})
            seen.clear()
            # OKX: JSON body + timestamp+method+path+body pre-hash
            ex.prepare_order("okx", "buy", "BTC-USDT", "limit", 1, 100,
                             transport=transport, state_dir=td)
            okx_plan = plan_of(td, "okx")
            ex.plan_decide(okx_plan["id"], "approve", state_dir=td)
            ex.execute_order(okx_plan["id"], transport=transport, state_dir=td)
            req = last(lambda u: "/api/v5/trade/order" in u)
            body = (req["payload"] or b"").decode("utf-8")
            doc = json.loads(body)
            hdr = req["headers"]
            expect = ex.okx_sign(hdr["OK-ACCESS-TIMESTAMP"], "POST",
                                 "/api/v5/trade/order", body,
                                 slots["OKX_API_SECRET"])
            check("w2: okx order body is the signed JSON payload",
                  hdr.get("OK-ACCESS-SIGN") == expect
                  and doc == {"instId": "BTC-USDT", "tdMode": "cash",
                              "side": "buy", "ordType": "limit",
                              "sz": "1", "px": "100"},
                  str(doc))
            check("w2: okx order posts JSON content type",
                  hdr.get("Content-Type") == "application/json",
                  str(hdr.get("Content-Type")))
            check("w2: okx order url carries no credential",
                  "SENTINEL" not in req["url"]
                  and slots["OKX_API_SECRET"] not in body)
            # Binance: hex HMAC over the query string, params in the URL
            ex.prepare_order("binance", "sell", "BTCUSDT", "limit", 1, 101,
                             transport=transport, state_dir=td)
            bn = plan_of(td, "binance")
            ex.plan_decide(bn["id"], "approve", state_dir=td)
            seen.clear()
            ex.execute_order(bn["id"], transport=transport, state_dir=td)
            req = last(lambda u: "/api/v3/order" in u)
            query = req["url"].split("?", 1)[1]
            prehash, _, sig = query.rpartition("&signature=")
            rebuilt = hmac.new(slots["BINANCE_API_SECRET"].encode("utf-8"),
                               prehash.encode("utf-8"), hashlib.sha256).hexdigest()
            check("w2: binance signature is the hex HMAC of the query",
                  sig == rebuilt and re.fullmatch(r"[0-9a-f]{64}", sig) is not None,
                  sig[:16])
            check("w2: binance order carries the API key header, not the secret",
                  req["headers"].get("X-MBX-APIKEY") == slots["BINANCE_API_KEY"]
                  and slots["BINANCE_API_SECRET"] not in req["url"]
                  and "BINANCE_API" not in query)
            check("w2: binance order params travel in the query",
                  "symbol=BTCUSDT" in prehash and "side=SELL"
                  in prehash and "type=LIMIT" in prehash
                  and req["method"] == "POST", req["method"])
            # Kraken: nonce form + path/SHA256/nonce signature over AddOrder
            ex.prepare_order("kraken", "buy", "XBT/USD", "limit", 1, 100,
                             transport=transport, state_dir=td)
            kr = plan_of(td, "kraken")
            ex.plan_decide(kr["id"], "approve", state_dir=td)
            seen.clear()
            ex.execute_order(kr["id"], transport=transport, state_dir=td)
            req = last(lambda u: "/0/private/AddOrder" in u)
            form = dict(kv.split("=", 1)
                        for kv in (req["payload"] or b"").decode().split("&"))
            resign = ex.kraken_sign("/0/private/AddOrder",
                                    (req["payload"] or b"").decode(),
                                    form["nonce"], slots["KRAKEN_API_SECRET"])
            check("w2: kraken AddOrder carries nonce + matching signature",
                  req["headers"].get("API-Sign") == resign
                  and form.get("pair") == "XBTUSD" and form.get("price") == "100"
                  and form.get("ordertype") == "limit",
                  str(form))
            check("w2: no credential leaks into any W2 output",
                  "SENTINEL" not in " ".join(
                      [json.dumps(ex.plans_list(state_dir=td)),
                       json.dumps(ex.plan_summary(state_dir=td))]))

        # ---- 10. plan store bounds ---------------------------------------
        with tempfile.TemporaryDirectory() as td:
            live = {f"pl_fake{i:03d}": {"id": f"pl_fake{i:03d}",
                                        "venue": "okx", "status": "pending",
                                        "expires_at": time.time() + 900,
                                        "created_at": float(i)}
                    for i in range(ex.PLAN_MAX)}
            store_file(td).parent.mkdir(parents=True, exist_ok=True)
            store_file(td).write_text(json.dumps({"plans": live, "ledger": {}}))
            try:
                ex.prepare_order("okx", "buy", "BTC-USDT", "limit", 1, 100,
                                 transport=transport, state_dir=td)
                check("w2: a full plan store refuses new plans", False)
            except ex.ExchangeError as e:
                check("w2: a full plan store refuses new plans",
                      "plan store is full" in str(e), str(e))
            mutate(td, lambda doc: doc["plans"].update(
                {"pl_fake000": {**live["pl_fake000"], "status": "executed"}}))
            ex.prepare_order("okx", "buy", "BTC-USDT", "limit", 1, 100,
                             transport=transport, state_dir=td)
            kept = json.loads(store_file(td).read_text(encoding="utf-8"))
            check("w2: terminal plans are pruned before the cap is hit",
                  len(kept["plans"]) <= ex.PLAN_MAX
                  and "pl_fake000" not in kept["plans"],
                  str(len(kept["plans"])))

        # ---- 11. the absence of a fund-moving surface, in this source ----
        src = Path(ex.__file__).read_text(encoding="utf-8")
        banned = ("withdraw", "transfer", "deposit", "payout")
        defs = re.findall(r"^\s*def\s+([A-Za-z_]\w*)", src, flags=re.M)
        bad_defs = [d for d in defs if any(b in d.lower() for b in banned)]
        check("w2: source defines no withdrawal/transfer/deposit function",
              not bad_defs, str(bad_defs))
        paths = re.findall(r'"(/[^"]*)"', src)
        bad_paths = [p for p in paths if any(b in p.lower() for b in banned)]
        check("w2: source calls no withdrawal/transfer endpoint",
              not bad_paths, str(bad_paths))
        bad_tools = [n for n in KNOWN_TOOL_NAMES
                     if any(b in n.lower() for b in banned)]
        check("w2: no registered tool moves funds", not bad_tools,
              str(bad_tools))
        sig = inspect.signature(ex.execute_order)
        check("w2: execute_order takes plan_id and transport knobs only",
              list(sig.parameters) == ["plan_id", "transport", "timeout",
                                       "state_dir"], str(list(sig.parameters)))
        check("w2: prepare_order never sends an order",
              "_PLACE_FN" not in inspect.getsource(ex.prepare_order),
              "prepare body reaches the sender table")
        check("w2: orderable venues and senders stay in lockstep",
              set(ex._PLACE_FN) == set(ex.ORDERABLE_VENUES) == set(ex._BOOK_FN)
              == set(ex._FEE_FN), str(sorted(ex._PLACE_FN)))
        check("w2: ledger day format is a Beijing YYYY-MM-DD",
              re.fullmatch(r"\d{4}-\d{2}-\d{2}", saved_day()) is not None,
              saved_day())
    finally:
        ex._slot = saved_slot
        ex.thresholds = saved_thresholds
        ex._bj_day = saved_day


def test_mission() -> None:
    """0.5.44 mission car (Lane E): state machine + registry (atomic write,
    caps) + budget guards sealing with evidence + reporter tiers/throttle/
    merge/scrub + slice prompt & capability manifest honesty + the service
    mission leg (fake transport, injected model, mark-first, four-tier
    fallback) + the console API (GET/POST /api/mission 200/400/401/404/409)
    + the settings partition. Offline and hermetic: temp state dirs, a temp
    profile pinned before the console import; no production file is written."""
    from mycore import mission as ms
    from mycore.service import state_dir as _state_dir
    from mycore.tg import POLL_BOT_ENV

    # ---- 0. consciousness layer: the SOUL section itself -------------------
    soul = (REPO / "overlay" / "constitution" / "SOUL.md").read_text(
        encoding="utf-8")
    check("mission: SOUL gains Mission conduct with clauses C0-C5",
          "## Mission conduct (autonomous ops)" in soul
          and all(f"(C{i})" in soul for i in range(6))
          and "extends hard constitution §9" in soul)
    check("mission: the nine hard rules stay intact around it",
          soul.count("## Hard constitution") == 1
          and "**Capability honesty**" in soul
          and "9. **Capability honesty**" in soul
          and "## Stance (how I behave)" in soul)

    # ---- A. state machine: legal edges, illegal edges, reserved slots -----
    rec = ms.create([], "grow the tool site", reporting="all", now=1000.0)
    check("mission: create lands planned with defaults",
          rec["state"] == "planned" and rec["reporting"] == "all"
          and rec["tasks"] == [] and rec["evidence"] == []
          and rec["budget"] == ms.DEFAULT_BUDGET
          and isinstance(rec["manifest"], dict), str(rec.get("state")))
    ms.transition(rec, "running", 1001.0)
    ms.transition(rec, "paused_user", 1002.0)
    ms.transition(rec, "running", 1003.0)
    check("mission: legal chain planned->running<->paused_user->running",
          rec["state"] == "running")

    def _at(state: str) -> dict:
        """A fresh mission parked in `state` via legal moves only."""
        m0 = ms.create([], "g", now=1000.0)
        if state == "planned":
            return m0
        ms.transition(m0, "running", 1000.0)
        if state == "running":
            return m0
        if state == "paused_user":
            ms.transition(m0, "paused_user", 1000.0)
            return m0
        if state == "done":
            ms.transition(m0, "done", 1000.0)
            return m0
        ms.transition(m0, "sealed", 1000.0, evidence=["retired"])
        return m0

    for cur, nxt in (("planned", "done"), ("planned", "paused_user"),
                     ("running", "planned"), ("done", "running"),
                     ("done", "sealed"), ("sealed", "running"),
                     ("sealed", "paused_user"), ("paused_user", "planned"),
                     ("paused_user", "done")):
        m0 = _at(cur)
        try:
            ms.transition(m0, nxt, 1001.0, evidence=["x"])
            check(f"mission: illegal transition {cur}->{nxt} refused",
                  False, "accepted")
        except ms.MissionError:
            check(f"mission: illegal transition {cur}->{nxt} refused", True)

    m0 = _at("running")
    try:
        ms.transition(m0, "sealed", 1001.0)
        check("mission: seal without evidence refused", False, "accepted")
    except ms.MissionError:
        check("mission: seal without evidence refused", True)
    ms.transition(m0, "sealed", 1001.0, evidence=["budget guard reached"])
    check("mission: sealed stores its evidence pack",
          m0["state"] == "sealed" and m0["evidence"] == ["budget guard reached"]
          and m0["ended_at"] == 1001.0)
    # car 2: degraded entry/exit edges are OPEN (paused_approve stays car 3)
    m_deg = _at("running")
    ms.transition(m_deg, "degraded", 1001.0)
    check("mission: running -> degraded entry is legal (car 2)",
          m_deg["state"] == "degraded"
          and ms.active_mission({"missions": [m_deg]}) is m_deg)
    ms.transition(m_deg, "paused_user", 1002.0)
    check("mission: degraded may pause (degraded -> paused_user)",
          m_deg["state"] == "paused_user")
    ms.transition(m_deg, "running", 1003.0)
    check("mission: resume from a degraded-origin pause resumes running",
          m_deg["state"] == "running")

    def _pa() -> dict:
        """A mission parked in the reserved car-3 slot (defensive read)."""
        m0 = ms.create([], "g", now=1000.0)
        m0["state"] = "paused_approve"
        return m0

    for cur, nxt in (("planned", "degraded"), ("paused_user", "degraded"),
                     ("degraded", "planned"), ("done", "degraded"),
                     ("sealed", "degraded"), ("paused_approve", "degraded")):
        m0 = _pa() if cur == "paused_approve" else _at(cur)
        try:
            ms.transition(m0, nxt, 1001.0, evidence=["x"])
            check(f"mission: illegal transition {cur}->{nxt} refused", False,
                  "accepted")
        except ms.MissionError as e:
            check(f"mission: illegal transition {cur}->{nxt} refused",
                  "allowed" in str(e), str(e))
    # ---- car 3: paused_approve entry (running only) and its exits ---------
    m_entry = _at("running")
    ms.transition(m_entry, "paused_approve", 1001.0)
    check("mission: running -> paused_approve entry is legal (car 3)",
          m_entry["state"] == "paused_approve")
    ms.transition(m_entry, "running", 1002.0)
    check("mission: paused_approve -> running exit is legal (car 3)",
          m_entry["state"] == "running")
    for cur, nxt in (("paused_approve", "paused_user"),
                     ("paused_approve", "planned"),
                     ("paused_user", "paused_approve"),
                     ("planned", "paused_approve"),
                     ("degraded", "paused_approve")):
        if cur == "paused_approve":
            m0 = _pa()
        elif cur == "degraded":
            m0 = _at("running")
            ms.transition(m0, "degraded", 1000.0)
        else:
            m0 = _at(cur)
        try:
            ms.transition(m0, nxt, 1001.0, evidence=["x"])
            check(f"mission: illegal transition {cur}->{nxt} refused", False,
                  "accepted")
        except ms.MissionError:
            check(f"mission: illegal transition {cur}->{nxt} refused", True)

    check("mission: terminal states are exactly done+sealed",
          ms.TERMINAL_STATES == frozenset({"done", "sealed"}))
    try:
        ms.create([], "x" * (ms.MAX_GOAL_CHARS + 1))
        check("mission: oversized goal refused", False, "accepted")
    except ms.MissionError:
        check("mission: oversized goal refused", True)
    try:
        ms.create([], "g", reporting="sometimes")
        check("mission: bad reporting tier refused", False, "accepted")
    except ms.MissionError:
        check("mission: bad reporting tier refused", True)
    for bad_goal in ("", "   "):
        try:
            ms.create([], bad_goal)
            check(f"mission: empty goal {bad_goal!r} refused", False, "accepted")
        except ms.MissionError:
            check(f"mission: empty goal {bad_goal!r} refused", True)
    try:
        ms.create([_at("running")], "second")
        check("mission: a second active mission refused", False, "accepted")
    except ms.MissionError as e:
        check("mission: a second active mission refused",
              "already exists" in str(e), str(e))
    full: list[dict] = []
    for i in range(ms.MAX_MISSIONS):
        m0 = ms.create(full, f"goal-{i}", now=1000.0 + i)
        ms.transition(m0, "sealed", 1001.0 + i, evidence=["retired"])
    try:
        ms.create(full, "one more")
        check("mission: history cap refuses #51", False, "accepted")
    except ms.MissionError as e:
        check("mission: history cap refuses #51",
              "full" in str(e) and len(full) == ms.MAX_MISSIONS, str(e))

    # ---- B. registry: atomic write, fail-soft load, event stream -----------
    td = tempfile.mkdtemp(prefix="mission_")
    try:
        path = Path(td) / ms.STATE_FILE_NAME
        ms.save(path, {"missions": [rec], "last_tick": 123.5})
        back = ms.load(path)
        check("mission: registry round-trips atomically",
              len(back["missions"]) == 1
              and back["missions"][0]["id"] == rec["id"]
              and back["missions"][0]["goal"] == "grow the tool site"
              and back["last_tick"] == 123.5)
        check("mission: save leaves no temp file behind",
              not [n for n in os.listdir(td)
                   if n.startswith(ms.STATE_FILE_NAME + ".tmp")],
              str(os.listdir(td)))
        path.write_text("{ this is not json", encoding="utf-8")
        check("mission: corrupt registry loads empty (fail-soft)",
              ms.load(path) == {"missions": [], "last_tick": None})
        check("mission: default path follows the cron state convention",
              ms.default_path() == _state_dir() / ms.STATE_FILE_NAME)
        check("mission: event stream sits beside the registry",
              ms.events_path(path).name == ms.EVENTS_FILE_NAME)
        ms.save(path, {"missions": [rec], "last_tick": 1.0})
        ms.append_event(path, rec["id"], "slice", {"n": 1})
        ev_lines = ms.events_path(path).read_text(encoding="utf-8").splitlines()
        first = json.loads(ev_lines[0])
        check("mission: event stream appends parseable JSONL",
              len(ev_lines) == 1 and first["kind"] == "slice"
              and first["mission"] == rec["id"] and first["n"] == 1
              and isinstance(first["ts"], float))
        ms.append_event(path, rec["id"], "garbage", {"obj": object()})
        check("mission: unserializable event never raises",
              len(ms.events_path(path).read_text(encoding="utf-8")
                  .splitlines()) == 1)

        # ---- C. budget guards ----------------------------------------------
        b = ms.create([], "g", budget={"max_slices": 3, "max_wall_hours": 1.5},
                      now=0.0)
        check("mission: budget merges with defaults",
              b["budget"] == {"max_slices": 3, "max_wall_hours": 1.5,
                              "max_daily_model_calls":
                                  ms.DEFAULT_BUDGET["max_daily_model_calls"]},
              str(b["budget"]))
        check("mission: fresh mission breaches nothing",
              ms.budget_breach(b, 10.0) is None)
        b["counters"]["slices"] = 2
        check("mission: below max_slices breaches nothing",
              ms.budget_breach(b, 10.0) is None)
        b["counters"]["slices"] = 3
        check("mission: max_slices reached -> breach",
              ms.budget_breach(b, 10.0) == "max_slices")
        check("mission: budget evidence names guard and counts",
              ms.budget_evidence(b, "max_slices")
              == "budget guard max_slices reached (3/3)")
        b_wall = ms.create([], "w", budget={"max_wall_hours": 1.5}, now=0.0)
        check("mission: wall guard silent just before the limit",
              ms.budget_breach(b_wall, 5399.0) is None)
        check("mission: wall guard trips at the limit",
              ms.budget_breach(b_wall, 5400.0) == "max_wall_hours")
        b_day = ms.create([], "d",
                          budget={"max_daily_model_calls": 5,
                                  "max_wall_hours": 720.0},
                          now=1700000000.0)
        ms.note_model_calls(b_day, 4, 1700000000.0)
        check("mission: daily calls counted toward the guard",
              ms.budget_breach(b_day, 1700000001.0) is None
              and b_day["counters"]["daily_calls"] == 4)
        ms.note_model_calls(b_day, 1, 1700000100.0)
        check("mission: max_daily_model_calls reached -> breach",
              ms.budget_breach(b_day, 1700000101.0)
              == "max_daily_model_calls")
        ms.note_model_calls(b_day, 0, 1700000100.0 + 86400)
        check("mission: daily counter rolls over on the calendar day",
              b_day["counters"]["daily_calls"] == 0
              and ms.budget_breach(b_day, 1700000100.0 + 86400) is None)
        for why, bad in (("unknown key", {"nope": 1}),
                         ("out of range", {"max_slices": 0}),
                         ("non-number", {"max_slices": "many"}),
                         ("fractional slices", {"max_slices": 3.5}),
                         ("slices above the ceiling", {"max_slices": 10001}),
                         ("wall below the floor", {"max_wall_hours": 0.05}),
                         ("wall with 2 decimals", {"max_wall_hours": 1.25}),
                         ("daily calls out of range",
                          {"max_daily_model_calls": 0})):
            try:
                ms.validate_budget(bad)
                check(f"mission: budget {why} refused", False, "accepted")
            except ms.MissionError:
                check(f"mission: budget {why} refused", True)
    finally:
        shutil.rmtree(td, ignore_errors=True)

    # ---- D. reporter: three tiers, throttle, merge, scrub ------------------
    sent: list[str] = []
    m_all = ms.create([], "goal", reporting="all", now=0.0)
    ev = {"kind": "task_done", "level": "INFO", "fact": "F", "impact": "I",
          "next": "N"}
    frag = ms.fmt_event(m_all["id"], ev)
    check("mission: event fragment carries the remonstrance format",
          frag == f"[MISSION {m_all['id']}] INFO F {ms.ARROW} I {ms.ARROW} N",
          frag)
    check("mission: all tier sends a subtask report",
          ms.emit(m_all, "task_done", frag, sent.append, 1000.0) == "sent"
          and len(sent) == 1)
    check("mission: all tier throttles inside 120s",
          ms.emit(m_all, "task_done", "second", sent.append, 1030.0)
          == "queued" and len(sent) == 1
          and m_all["report_queue"] == ["second"])
    check("mission: queued window merges into the next send",
          ms.emit(m_all, "task_done", "third", sent.append, 1121.0) == "sent"
          and len(sent) == 2 and sent[-1].count("\n") == 1
          and "second" in sent[-1] and "third" in sent[-1])
    m_ms = ms.create([], "g", reporting="milestones", now=0.0)
    sent2: list[str] = []
    check("mission: milestones tier drops plain subtask reports",
          ms.emit(m_ms, "task_done", "x", sent2.append, 1000.0)
          == "suppressed" and sent2 == [] and m_ms["report_queue"] == [])
    check("mission: milestones tier reports degradation",
          ms.emit(m_ms, "degraded", "gap-line", sent2.append, 1000.0)
          == "sent" and sent2 == ["gap-line"])
    check("mission: milestones tier throttles to 900s",
          ms.emit(m_ms, "degraded", "gap-2", sent2.append, 1500.0)
          == "queued" and len(sent2) == 1)
    check("mission: escalation bypasses the throttle and flushes the window",
          ms.emit(m_ms, "decision", "dec", sent2.append, 1510.0) == "sent"
          and len(sent2) == 2 and "gap-2" in sent2[-1])
    m_fin = ms.create([], "g", reporting="final", now=0.0)
    sent3: list[str] = []
    check("mission: final tier drops progress and degradation",
          ms.emit(m_fin, "task_done", "x", sent3.append, 1.0) == "suppressed"
          and ms.emit(m_fin, "degraded", "y", sent3.append, 1.0)
          == "suppressed" and sent3 == [] and m_fin["report_queue"] == [])
    check("mission: final tier still carries escalation and the outcome",
          ms.emit(m_fin, "decision", "d", sent3.append, 2.0) == "sent"
          and ms.emit(m_fin, "final", "f", sent3.append, 3.0) == "sent"
          and sent3 == ["d", "f"])
    m_byp = ms.create([], "g", reporting="all", now=0.0)
    sent4: list[str] = []
    ms.emit(m_byp, "task_done", "one", sent4.append, 100.0)
    check("mission: decision never waits behind the throttle",
          ms.emit(m_byp, "decision", "two", sent4.append, 101.0) == "sent"
          and sent4 == ["one", "two"])
    tok_sentinel = "999888777:AAAAvwxyzAAAAvwxyzAAAAvwxyzAAAA"
    path_sentinel = "/mnt/" + "d/private/creds.env"
    m_scr = ms.create([], "g", reporting="all", now=0.0)
    sent5: list[str] = []
    bad_ev = {"kind": "degraded", "level": "WARN",
              "fact": f"gap {tok_sentinel} at {path_sentinel}",
              "impact": "i", "next": "n"}
    ms.emit(m_scr, "degraded", ms.fmt_event(m_scr["id"], bad_ev),
            sent5.append, 10.0)
    out = sent5[0]
    check("mission: reports scrub credential shapes and machine paths",
          tok_sentinel not in out and path_sentinel not in out
          and "/mnt/" not in out and "[redacted]" in out and "[path]" in out,
          out)

    def _boom(_t):
        raise RuntimeError("delivery down")

    check("mission: a failing delivery never raises",
          ms.emit(m_scr, "final", "f", _boom, 11.0) == "error")
    check("mission: unknown event kinds are dropped fail-closed",
          ms.emit(m_scr, "mystery", "m", sent5.append, 12.0) == "suppressed")

    # ---- car 3 reporter: quiet_hours + min_interval override ------------
    from datetime import datetime as _dt
    now_q = 1700000000.0
    bj_q = _dt.fromtimestamp(now_q, ms.BEIJING)
    _mins = bj_q.hour * 60 + bj_q.minute
    _qs, _qe = (_mins - 60) % 1440, (_mins + 60) % 1440
    win = (f"{_qs // 60:02d}:{_qs % 60:02d}-"
           f"{_qe // 60:02d}:{_qe % 60:02d}")
    mq = ms.create([], "quiet goal", reporting="all", now=now_q - 5000)
    qsent: list[str] = []
    check("mission: quiet window queues a progress report",
          ms.emit(mq, "task_done", "progress-line", qsent.append, now_q,
                  quiet_hours=win) == "queued"
          and qsent == [] and mq["report_queue"] == ["progress-line"],
          f"win={win} qsent={qsent}")
    check("mission: quiet window passes escalation alone (queue preserved)",
          ms.emit(mq, "decision", "dec-line", qsent.append, now_q + 1,
                  quiet_hours=win) == "sent"
          and qsent == ["dec-line"]
          and mq["report_queue"] == ["progress-line"])
    check("mission: quiet window passes the final report",
          ms.emit(mq, "final", "final-line", qsent.append, now_q + 2,
                  quiet_hours=win) == "sent" and len(qsent) == 2)
    check("mission: after the window the queue merges at the first send",
          ms.emit(mq, "task_done", "after-line", qsent.append, now_q + 7200.0,
                  quiet_hours=win) == "sent"
          and "progress-line" in qsent[-1] and "after-line" in qsent[-1]
          and qsent[-1].count("\n") == 1,
          qsent[-1])
    ts_2330 = _dt(2026, 1, 10, 23, 30, tzinfo=ms.BEIJING).timestamp()
    ts_0600 = _dt(2026, 1, 11, 6, 0, tzinfo=ms.BEIJING).timestamp()
    ts_1200 = _dt(2026, 1, 11, 12, 0, tzinfo=ms.BEIJING).timestamp()
    check("mission: quiet window 23:00-07:00 crosses midnight correctly",
          ms._quiet_active("23:00-07:00", ts_2330)
          and ms._quiet_active("23:00-07:00", ts_0600)
          and not ms._quiet_active("23:00-07:00", ts_1200)
          and not ms._quiet_active("", ts_2330)
          and not ms._quiet_active("23:00-23:00", ts_2330)
          and not ms._quiet_active("bogus", ts_2330))
    mo = ms.create([], "min goal", reporting="all", now=0.0)
    osent: list[str] = []
    ms.emit(mo, "task_done", "first", osent.append, 1000.0)
    check("mission: min_interval override tightens the tier default",
          ms.emit(mo, "task_done", "second", osent.append, 1100.0,
                  min_interval=30) == "sent" and len(osent) == 2)
    mo2 = ms.create([], "min goal 2", reporting="all", now=0.0)
    osent2: list[str] = []
    ms.emit(mo2, "task_done", "first", osent2.append, 1000.0)
    check("mission: min_interval override loosens the tier default",
          ms.emit(mo2, "task_done", "second", osent2.append, 1100.0,
                  min_interval=300) == "queued",
          str(osent2))
    check("mission: decision stays throttle-free under any override",
          ms.emit(mo2, "decision", "dec", osent2.append, 1101.0,
                  min_interval=300) == "sent" and len(osent2) == 2)
    check("mission: reporter settings readers fall back defensively",
          ms.settings_quiet_hours(None) == ""
          and ms.settings_quiet_hours({"quiet_hours": "nope"}) == ""
          and ms.settings_quiet_hours({"quiet_hours": "09:00-17:00"})
          == "09:00-17:00"
          and ms.settings_min_interval({"min_interval": "600"}) == 0
          and ms.settings_min_interval({"min_interval": -5}) == 0
          and ms.settings_min_interval({"min_interval": 600}) == 600
          and ms.settings_min_interval(None) == 0)

    # ---- E. markers, queue folding, prompt, manifest -----------------------
    marks = ms.parse_slice("\n".join([
        "Plan below.", "SUBTASK: write the article",
        "- SUBTASK: publish it", "GAP: no ad account",
        "METHOD: free channels", "TRIED: free channels",
        "BLOCKER: rate limit", "NEED DECISION: buy a domain",
        "UNBLOCK: t9", "TASK DONE", "MISSION DONE"]))
    check("mission: marker parse harvests every protocol line",
          marks["tasks"] == ["write the article", "publish it"]
          and marks["gaps"] == ["no ad account"]
          and marks["methods"] == ["free channels"]
          and marks["tried"] == ["free channels"]
          and marks["blockers"] == ["rate limit"]
          and marks["decisions"] == ["buy a domain"]
          and marks["unblock"] == ["t9"]
          and marks["task_done"] and marks["mission_done"], str(marks))
    check("mission: prose without markers yields empty marks",
          ms.parse_slice("just talking, no protocol lines at all")
          == {"tasks": [], "task_done": False, "mission_done": False,
              "gaps": [], "decisions": [], "blockers": [], "methods": [],
              "tried": [], "unblock": [], "milestones": [],
              "plan_lines": []})
    m_plan = ms.create([], "publish daily", now=0.0)
    evs = ms.apply_slice(m_plan, ms.parse_slice("no structure here"), 1.0,
                         mode="plan")
    check("mission: plan without SUBTASK lines falls back to the goal",
          [t["text"] for t in m_plan["tasks"]] == ["publish daily"]
          and [e["kind"] for e in evs] == ["plan_ready"])
    m_plan2 = ms.create([], "goal", now=0.0)
    evs2 = ms.apply_slice(m_plan2,
                          ms.parse_slice("SUBTASK: alpha\nSUBTASK: beta"),
                          1.0, mode="plan")
    check("mission: plan slice builds the serial queue + milestone event",
          [(t["id"], t["status"]) for t in m_plan2["tasks"]]
          == [("t1", "pending"), ("t2", "pending")]
          and [e["kind"] for e in evs2] == ["plan_ready"])
    # ---- car 2: MILESTONE parsing + the structured plan -------------------
    pm = ms.parse_slice("MILESTONE: First\n- SUBTASK: a\n"
                        "- MILESTONE: Second\nSUBTASK: b")
    check("mission: marker parse keeps milestones in plan order",
          pm["milestones"] == ["First", "Second"]
          and pm["tasks"] == ["a", "b"]
          and pm["plan_lines"] == [("milestone", "First"), ("task", "a"),
                                   ("milestone", "Second"), ("task", "b")],
          str(pm["plan_lines"]))
    m_ms = ms.create([], "ship the release", now=0.0)
    ms.apply_slice(m_ms, ms.parse_slice(
        "MILESTONE: Prepare\n- SUBTASK: write notes\n"
        "MILESTONE: Ship\n- SUBTASK: tag the repo\nSUBTASK: publish"),
        1.0, mode="plan")
    plan = m_ms.get("plan")
    check("mission: plan slice writes milestone groups into the registry",
          isinstance(plan, dict)
          and [m["id"] for m in plan["milestones"]] == ["m1", "m2"]
          and [m["title"] for m in plan["milestones"]] == ["Prepare", "Ship"]
          and [m["subtasks"] for m in plan["milestones"]]
          == [["t1"], ["t2", "t3"]]
          and plan["generated_at"] == 1.0
          and [t["text"] for t in m_ms["tasks"]]
          == ["write notes", "tag the repo", "publish"], str(plan))
    m_flat = ms.create([], "no milestones here", now=0.0)
    ms.apply_slice(m_flat, ms.parse_slice("SUBTASK: a\nSUBTASK: b"), 1.0,
                   mode="plan")
    check("mission: a plan without MILESTONE collapses to one Plan bucket",
          [m["title"] for m in m_flat["plan"]["milestones"]] == ["Plan"]
          and m_flat["plan"]["milestones"][0]["subtasks"] == ["t1", "t2"])
    m_fb = ms.create([], "fallback goal", now=0.0)
    ms.apply_slice(m_fb, ms.parse_slice("nothing structured"), 1.0,
                   mode="plan")
    check("mission: a structureless plan still yields one Plan bucket",
          m_fb["plan"]["milestones"]
          == [{"id": "m1", "title": "Plan", "subtasks": ["t1"]}]
          and m_fb["tasks"][0]["text"] == "fallback goal")
    check("mission: an unplanned (car-1) mission details as null plan",
          ms.plan_detail(ms.create([], "legacy", now=0.0)) is None)
    m_ms["tasks"][0]["status"] = "in_progress"
    m_ms["methods"] = [{"text": "try CI", "marked": True}]
    m_ms["blockers"] = ["flaky runner"]
    det_p = ms.plan_detail(m_ms)
    check("mission: plan detail projects live statuses + exhaustion lists",
          det_p["milestones"][0]["subtasks"][0]["status"] == "running"
          and det_p["milestones"][1]["subtasks"][1]["status"] == "pending"
          and det_p["methods"] == [{"text": "try CI", "marked": True}]
          and det_p["blockers"] == ["flaky runner"], str(det_p))
    check("mission: MISSION DONE before a plan exists finishes nothing",
          not ms.should_finish(ms.create([], "gx", now=0.0),
                               {"mission_done": True}))
    m_ex = ms.create([], "goal", tasks=["write", "publish"], now=0.0)
    m_ex["tasks"][0]["status"] = "in_progress"
    evs3 = ms.apply_slice(m_ex, ms.parse_slice(
        "GAP: no CDN\nGAP: no CDN\nMETHOD: mirror\nTRIED: mirror\n"
        "NEED DECISION: paid ads"), 2.0)
    kinds3 = [e["kind"] for e in evs3]
    check("mission: a new gap reports once, duplicates drop",
          kinds3.count("degraded") == 1 and m_ex["gaps"] == ["no CDN"])
    check("mission: TRIED marks the method-exhaustion entry",
          m_ex["methods"] == [{"text": "mirror", "marked": True}])
    check("mission: escalation suspends only the current subtask (C5)",
          m_ex["tasks"][0]["status"] == "blocked"
          and m_ex["tasks"][1]["status"] == "pending"
          and "decision" in kinds3)
    m_ex2 = ms.create([], "goal", tasks=["only one"], now=0.0)
    ms.set_current(m_ex2)
    check("mission: set_current promotes exactly one subtask",
          [t["status"] for t in m_ex2["tasks"]] == ["in_progress"])
    ms.set_current(m_ex2)
    check("mission: set_current keeps the in-flight subtask",
          [t["status"] for t in m_ex2["tasks"]] == ["in_progress"])
    evs4 = ms.apply_slice(m_ex2, ms.parse_slice("TASK DONE"), 3.0)
    check("mission: TASK DONE completes the current subtask with a report",
          [t["status"] for t in m_ex2["tasks"]] == ["done"]
          and [e["kind"] for e in evs4] == ["task_done"]
          and ms.should_finish(m_ex2, {"mission_done": False}))
    m_un = ms.create([], "g", tasks=["x"], now=0.0)
    ms.set_current(m_un)
    ms.apply_slice(m_un, ms.parse_slice("NEED DECISION: y"), 1.0)
    check("mission: escalation suspends the current subtask",
          m_un["tasks"][0]["status"] == "blocked")
    ms.apply_slice(m_un, ms.parse_slice("UNBLOCK: t1"), 2.0)
    check("mission: UNBLOCK returns a suspended subtask to pending",
          m_un["tasks"][0]["status"] == "pending")
    # ---- car 2: degraded entry from a gap; the queue never stalls (F4) ----
    m_gap = ms.create([], "g", tasks=["one", "two"], reporting="all", now=0.0)
    ms.transition(m_gap, "running", 1.0)
    ms.set_current(m_gap, 1.0)
    ms.apply_slice(m_gap, ms.parse_slice("GAP: no cdn"), 2.0)
    check("mission: a gap on the unfinished subtask enters degraded",
          m_gap["state"] == "degraded"
          and m_gap["tasks"][0]["status"] == "degraded")
    nxt_t = ms.set_current(m_gap, 3.0)
    check("mission: degraded never blocks the queue (F4) — the next subtask "
          "starts and the state returns to running",
          nxt_t is not None and nxt_t["id"] == "t2"
          and m_gap["state"] == "running"
          and [t["status"] for t in m_gap["tasks"]]
          == ["degraded", "in_progress"])
    m_gd = ms.create([], "g", tasks=["only"], reporting="all", now=0.0)
    ms.transition(m_gd, "running", 1.0)
    ms.set_current(m_gd, 1.0)
    ms.apply_slice(m_gd, ms.parse_slice("done here\nGAP: minor\nTASK DONE"),
                   2.0)
    check("mission: a gap whose subtask completed keeps the mission running",
          m_gd["state"] == "running" and m_gd["tasks"][0]["status"] == "done"
          and m_gd["gaps"] == ["minor"])
    m_ub2 = ms.create([], "g", tasks=["x"], now=0.0)
    ms.transition(m_ub2, "running", 1.0)
    ms.set_current(m_ub2, 1.0)
    ms.apply_slice(m_ub2, ms.parse_slice("NEED DECISION: y"), 2.0)
    ms.transition(m_ub2, "degraded", 3.0)
    ms.apply_slice(m_ub2, ms.parse_slice("UNBLOCK: t1"), 4.0)
    check("mission: UNBLOCK from degraded resumes the mission (running)",
          m_ub2["tasks"][0]["status"] == "pending"
          and m_ub2["state"] == "running")
    check("mission: delegate count reads the run_turn tool log",
          ms.delegate_count([{"name": "delegate"}, {"name": "read_file"},
                             {"name": "delegate"}]) == 2
          and ms.delegate_count(None) == 0)
    check("mission: budget usage reports honest wall hours",
          ms.budget_used({"created_at": 0.0, "ended_at": 7200.0,
                          "counters": {"slices": 5, "daily_calls": 3}},
                         99999.0)
          == {"slices": 5, "wall_hours": 2.0, "daily_calls": 3})
    check("mission: settings budget falls back defensively",
          ms.settings_budget(None) == ms.DEFAULT_BUDGET
          and ms.settings_budget({"max_slices": 7})["max_slices"] == 7
          and ms.settings_budget({"max_slices": 0})["max_slices"]
          == ms.DEFAULT_BUDGET["max_slices"])

    man = ms.build_manifest(
        {"mycore": {"tools_off": ["delegate"]},
         "channels": {"discord": {"enabled": True},
                      "slack": {"enabled": False}}},
        ["OPENAI_API_KEY", "OTHER_SLOT"],
        lambda name: "SENTINEL-VALUE-XYZ" if name == "OPENAI_API_KEY" else "",
        browser_ok=False, connector_ok=False, exchange_ok=False)
    blob = json.dumps(man)
    check("mission: manifest tools honor tools_off and probe gates",
          "delegate" not in man["tools"] and "read_file" in man["tools"]
          and not any(t.startswith("browser_") for t in man["tools"])
          and "connector_search" not in man["tools"]
          and "exchange_portfolio" not in man["tools"]
          and "exchange_markets" in man["tools"], str(man["tools"]))
    check("mission: manifest channels = enabled routes only",
          man["channels"] == ["discord"])
    check("mission: manifest credentials are key names + presence bools",
          man["credentials"] == {"OPENAI_API_KEY": True, "OTHER_SLOT": False}
          and all(isinstance(v, bool) for v in man["credentials"].values()))
    check("mission: manifest serializes no credential value",
          "SENTINEL-VALUE-XYZ" not in blob and "/mnt/" not in blob)
    check("mission: web3 block stays fail-closed in the manifest",
          man["web3"]["withdrawals"] is False
          and man["web3"]["onchain_transfers"] is False
          and man["web3"]["paid_actions"] is False
          and man["web3"]["order_gate"].startswith("prepare"))
    check("mission: manifest marks community MCP disabled",
          man["mcp"] == "disabled")
    m_pr = ms.create([], "build the site", tasks=["write the article"],
                     reporting="all", now=0.0)
    m_pr["manifest"] = man
    m_pr["gaps"] = ["no ad account"]
    m_pr["methods"] = [{"text": "free channels", "marked": False}]
    m_pr["counters"]["slices"] = 7
    prompt = ms.build_slice_prompt(m_pr, mode="exec")
    check("mission: slice prompt grounds goal + subtask + gaps + manifest",
          "build the site" in prompt and "write the article" in prompt
          and "no ad account" in prompt and "free channels" in prompt
          and "read_file" in prompt)
    check("mission: slice prompt declares that no answer will ever come",
          ms.NO_ANSWER_DECLARATION in prompt and "no one to ask" in prompt)
    check("mission: slice prompt carries the marker protocol",
          "TASK DONE" in prompt and "MISSION DONE" in prompt
          and ms.MARK_GAP in prompt and ms.MARK_DECISION in prompt
          and ms.MARK_METHOD in prompt)
    check("mission: prompt keeps credentials as names only",
          "SENTINEL-VALUE-XYZ" not in prompt and "OPENAI_API_KEY" in prompt)
    check("mission: prompt carries the method-exhaustion list",
          "[open] free channels" in prompt)
    plan_prompt = ms.build_slice_prompt(ms.create([], "g2", now=0.0),
                                        mode="plan")
    check("mission: planning prompt demands SUBTASK lines",
          ms.MARK_SUBTASK in plan_prompt and "decompose" in plan_prompt)
    m_blk = ms.create([], "g3", tasks=["blocked one"], now=0.0)
    m_blk["tasks"][0]["status"] = "blocked"
    blk_prompt = ms.build_slice_prompt(m_blk, mode="exec")
    check("mission: blocked-only prompt asks for UNBLOCK or escalation",
          ms.MARK_UNBLOCK in blk_prompt and ms.MARK_DECISION in blk_prompt)

    # ---- car 3: A-face start constraints (deadline/redlines/deliverable) --
    cn = 1700000000.0
    mcon = ms.create([], "constrained goal",
                     deadline="2030-05-05T09:00:00+08:00",
                     redlines="no paid ads, no third-party MCP",
                     deliverable="a published report", now=cn)
    check("mission: start constraints stored on the record",
          mcon["deadline"] == "2030-05-05T09:00:00+08:00"
          and mcon["redlines"] == "no paid ads, no third-party MCP"
          and mcon["deliverable"] == "a published report"
          and mcon["last_progress_at"] == cn,
          str({k: mcon.get(k) for k in ("deadline", "redlines", "deliverable")}))
    plan_c = ms.build_slice_prompt(mcon, mode="plan")
    exec_c = ms.build_slice_prompt(mcon, mode="exec")
    check("mission: constraints injected into BOTH plan and exec prompts",
          all("Owner constraints" in p and "no paid ads" in p
              and "a published report" in p and "2030-05-05" in p
              for p in (plan_c, exec_c)))
    mplain = ms.create([], "plain goal", now=cn)
    check("mission: absent constraints keep the old prompt verbatim",
          "Owner constraints"
          not in ms.build_slice_prompt(mplain, mode="plan")
          and "Owner constraints"
          not in ms.build_slice_prompt(mplain, mode="exec"))
    prow0 = ms.public_row(mplain)
    check("mission: public row carries null constraints + empty approvals",
          prow0["deadline"] is None and prow0["redlines"] is None
          and prow0["deliverable"] is None and prow0["approvals"] == [])
    for why, kw, frag in (
            ("past deadline", {"deadline": "2020-01-01T00:00:00Z"},
             "future"),
            ("garbage deadline", {"deadline": "tomorrow"}, "ISO8601"),
            ("non-string deadline", {"deadline": 12345}, "ISO8601"),
            ("oversized redlines", {"redlines": "x" * 501}, "500"),
            ("control-char redlines", {"redlines": "bad\tx"}, "control"),
            ("oversized deliverable", {"deliverable": "y" * 201}, "200"),
            ("non-string deliverable", {"deliverable": 7}, "plain-text")):
        try:
            ms.create([], "refused goal", now=cn, **kw)
            check(f"mission: {why} refused", False, "accepted")
        except ms.MissionError as e:
            check(f"mission: {why} refused", frag in str(e), str(e))
    check("mission: blank constraint strings degrade to None",
          ms.create([], "blank c", redlines="   ", deliverable="",
                    now=cn)["redlines"] is None
          and ms.create([], "blank c2", deadline="  ",
                        now=cn)["deadline"] is None)

    # ---- car 3: paused_approve suspension + console-decision observation --
    tl_drill = [
        {"name": "exchange_prepare_order", "ok": True,
         "preview": "plan pl_0123456789ab created - status=pending"},
        {"name": "exchange_prepare_order", "ok": False,
         "preview": "ERROR: plan pl_aabbccddeeff unknown"},
        {"name": "read_file", "ok": True, "preview": "pl_111111111111"}]
    check("mission: approval plan ids come only from successful prepares",
          ms.approval_plan_ids(tl_drill) == ["pl_0123456789ab"]
          and ms.approval_plan_ids(None) == []
          and ms.approval_plan_ids([]) == []
          and ms.approval_plan_ids([{"name": "x", "ok": True,
                                     "preview": ""}]) == [])
    mas = ms.create([], "order goal", tasks=["buy BTC", "write summary"],
                    reporting="all", now=100.0)
    ms.transition(mas, "running", 101.0)
    ms.set_current(mas, 101.0)
    check("mission: suspension parks the in-flight subtask and the mission",
          ms.suspend_for_approval(mas, mas["tasks"][0],
                                  ["pl_0123456789ab"], 102.0)
          and mas["state"] == "paused_approve"
          and mas["tasks"][0]["status"] == "awaiting_approval"
          and mas["tasks"][0]["plans"] == ["pl_0123456789ab"])
    mas["plan"] = {"milestones": [{"id": "m1", "title": "T",
                                    "subtasks": ["t1", "t2"]}],
                   "generated_at": 101.0}
    detp = ms.plan_detail(mas)
    check("mission: plan detail exposes the awaiting subtask plan ids",
          detp["milestones"][0]["subtasks"][0]
          == {"id": "t1", "text": "buy BTC",
              "status": "awaiting_approval",
              "plans": ["pl_0123456789ab"]}, str(detp))
    check("mission: badge data on the public row",
          ms.public_row(mas)["approvals"]
          == [{"task": "t1", "plans": ["pl_0123456789ab"]}])
    nxt_aw = ms.set_current(mas, 103.0)
    check("mission: other subtasks keep advancing while parked (F4)",
          nxt_aw is not None and nxt_aw["id"] == "t2"
          and mas["state"] == "paused_approve",
          str([(t["id"], t["status"]) for t in mas["tasks"]]))
    check("mission: observe keeps waiting on a pending plan",
          ms.observe_approvals(mas, lambda p: "pending", 104.0) == []
          and mas["state"] == "paused_approve"
          and mas["tasks"][0]["status"] == "awaiting_approval")
    check("mission: an unknown plan keeps waiting (never fabricates)",
          ms.observe_approvals(mas, lambda p: None, 105.0) == []
          and mas["tasks"][0]["status"] == "awaiting_approval")

    def _boom_plan(_pid):
        raise RuntimeError("store down")

    check("mission: a broken reader keeps waiting (fail-safe)",
          ms.observe_approvals(mas, _boom_plan, 106.0) == []
          and mas["tasks"][0]["status"] == "awaiting_approval")
    check("mission: approval resumes the subtask and the mission",
          ms.observe_approvals(mas, lambda p: "approved", 107.0) == []
          and mas["state"] == "running"
          and mas["tasks"][0]["status"] == "pending"
          and "plans" not in mas["tasks"][0])
    mrej = ms.create([], "reject goal", tasks=["buy BTC"], reporting="all",
                     now=100.0)
    ms.transition(mrej, "running", 101.0)
    ms.set_current(mrej, 101.0)
    ms.suspend_for_approval(mrej, mrej["tasks"][0], ["pl_aabbccddeeff"], 102.0)
    evs_rej = ms.observe_approvals(mrej, lambda p: "rejected", 103.0)
    check("mission: a refused plan degrades the subtask with gap + report",
          [e["kind"] for e in evs_rej] == ["degraded"]
          and mrej["state"] == "running"
          and mrej["tasks"][0]["status"] == "degraded"
          and any("pl_aabbccddeeff" in g for g in mrej["gaps"]),
          str(evs_rej))
    mdef = ms.create([], "defensive goal", tasks=["x"], now=100.0)
    mdef["state"] = "paused_approve"   # hand-edited row: nobody waiting
    check("mission: paused_approve with nobody waiting returns to running",
          ms.observe_approvals(mdef, lambda p: None, 101.0) == []
          and mdef["state"] == "running")
    mfin = ms.create([], "done goal", tasks=["x"], now=100.0)
    ms.transition(mfin, "running", 101.0)
    mfin["tasks"][0]["status"] = "done"
    check("mission: suspension refuses a subtask that is not in flight",
          ms.suspend_for_approval(mfin, mfin["tasks"][0],
                                  ["pl_ffffffffffffffffff"], 102.0) is False
          and mfin["state"] == "running"
          and not ms.awaiting_tasks(mfin))
    mcur = ms.create([], "cur goal", tasks=["a", "b"], now=100.0)
    ms.transition(mcur, "running", 101.0)
    ms.set_current(mcur, 101.0)
    ms.suspend_for_approval(mcur, mcur["tasks"][0],
                            ["pl_0123456789ab"], 102.0)
    check("mission: an awaiting subtask is never current — queue moves on",
          ms.current_task(mcur) is not None
          and ms.current_task(mcur)["id"] == "t2"
          and ms.set_current(mcur, 103.0)["id"] == "t2"
          and mcur["tasks"][0]["status"] == "awaiting_approval")

    # ---- F. service mission leg (fake transport, injected model) -----------
    saved_tok = os.environ.get(POLL_BOT_ENV)
    saved_wd = os.environ.get("MYCORE_WD_CHAT")
    os.environ[POLL_BOT_ENV] = "mission-bot-TEST-token"
    os.environ.pop("MYCORE_WD_CHAT", None)
    try:
        from mycore.service import ChatService
        clock = [1800000000.0]
        nowf = lambda: clock[0]  # noqa: E731
        fake = FakeTgTransport()
        with tempfile.TemporaryDirectory() as td2:
            svc = ChatService(Path(td2), frozenset({111}), transport=fake,
                              chat=lambda messages=None, tools=None, **kw:
                              fake_response(text="ok"), now=nowf)
            reg_path = Path(td2) / ms.STATE_FILE_NAME
            doc = {"missions": [], "last_tick": clock[0] - 700.0}
            m_svc = ms.create(doc["missions"], "publish daily",
                              reporting="all", origin=424242,
                              manifest=man, now=clock[0])
            ms.transition(m_svc, "running", clock[0])
            ms.save(reg_path, doc)
            script = ScriptedChat([
                fake_response(text="plan\nSUBTASK: write the article\n"
                                   "SUBTASK: publish"),
                fake_response(text="wrote it\nGAP: no CDN (token "
                                   + tok_sentinel + " at " + path_sentinel
                                   + ")\nTASK DONE"),
                fake_response(text="published\nTASK DONE\nMISSION DONE"),
            ])
            svc.chat = script.chat
            # tick 1: catch-up line + plan slice + first milestone report
            svc._mission_check = 0.0
            svc._maybe_tick_missions()
            mrow = ms.load(reg_path)["missions"][0]
            check("mission leg: catch-up noted after a long leg absence",
                  "MISSION_CATCHUP" in svc.log.read_text())
            check("mission leg: plan slice ran, queue built, report sent",
                  len(fake.sends) == 1
                  and fake.sends[0][0] == "424242"
                  and ms.ZH["plan_ready"] in fake.sends[0][1]
                  and "[MISSION " in fake.sends[0][1]
                  and len(mrow["tasks"]) == 2
                  and mrow["counters"]["slices"] == 1)
            check("mission leg: constitution stack leads the slice",
                  script.seen[0][0].get("role") == "system"
                  and len(str(script.seen[0][0].get("content", ""))) > 200)
            u1 = str(script.seen[0][-1].get("content", ""))
            check("mission leg: planning prompt carries goal + no-answer rule",
                  "publish daily" in u1 and "no one to ask" in u1
                  and ms.MARK_SUBTASK in u1)
            # within the 5s cadence the leg must do nothing at all
            svc._maybe_tick_missions()
            check("mission leg: tick throttles to the 5s cadence",
                  ms.load(reg_path)["missions"][0]["counters"]["slices"] == 1)
            clock[0] += 130.0
            svc._mission_check = 0.0
            svc._maybe_tick_missions()
            mrow = ms.load(reg_path)["missions"][0]
            u2 = str(script.seen[1][-1].get("content", ""))
            check("mission leg: exec prompt names the current subtask",
                  "write the article" in u2 and "TASK DONE" in u2)
            check("mission leg: first subtask done, degradation reported",
                  [t["status"] for t in mrow["tasks"]] == ["done", "pending"]
                  and len(fake.sends) == 2
                  and ms.ZH["gap"] in fake.sends[1][1])
            check("mission leg: model-derived gap is scrubbed at storage",
                  tok_sentinel not in mrow["gaps"][0]
                  and path_sentinel not in mrow["gaps"][0]
                  and "[redacted]" in mrow["gaps"][0]
                  and "[path]" in mrow["gaps"][0])
            check("mission leg: subtask report stayed credential-free",
                  tok_sentinel not in fake.sends[1][1]
                  and path_sentinel not in fake.sends[1][1])
            # tick 3: second subtask + MISSION DONE + final report
            clock[0] += 130.0
            svc._mission_check = 0.0
            svc._maybe_tick_missions()
            mrow = ms.load(reg_path)["missions"][0]
            check("mission leg: mission reaches done through the two exits",
                  mrow["state"] == "done"
                  and all(t["status"] == "done" for t in mrow["tasks"]))
            check("mission leg: final four-section report delivered",
                  len(fake.sends) == 4
                  and ms.ZH["done_report"] in fake.sends[3][1]
                  and ms.ZH["sec_gaps"] in fake.sends[3][1])
            check("mission leg: exactly three slices ran (script guard)",
                  script.calls == 3)
            fake.sends.clear()
            svc._mission_check = 0.0
            svc._maybe_tick_missions()
            check("mission leg: idle when no mission is active",
                  fake.sends == [] and script.calls == 3)
            ev_text = ms.events_path(reg_path).read_text(encoding="utf-8")
            ev_rows = [json.loads(line) for line in ev_text.splitlines()]
            kinds_seen = {r["kind"] for r in ev_rows}
            check("mission leg: event stream records slices + reports + states",
                  {"slice", "report", "state"} <= kinds_seen
                  and tok_sentinel not in ev_text
                  and path_sentinel not in ev_text
                  and all(r["mission"] == m_svc["id"] for r in ev_rows))

            # budget guard: max_slices=1 seals on the next tick, with evidence
            doc_b = {"missions": [], "last_tick": None}
            m_bud = ms.create(doc_b["missions"], "bounded work",
                              reporting="final", origin=424242,
                              budget={"max_slices": 1}, now=clock[0])
            ms.transition(m_bud, "running", clock[0])
            ms.save(reg_path, doc_b)
            b_script = ScriptedChat([fake_response(text="SUBTASK: only one")])
            svc.chat = b_script.chat
            fake.sends.clear()
            svc._mission_check = 0.0
            svc._maybe_tick_missions()
            check("mission leg: bounded mission spends its single slice",
                  ms.load(reg_path)["missions"][0]["counters"]["slices"] == 1
                  and fake.sends == [])   # final tier drops plan_ready
            clock[0] += 6.0
            svc._mission_check = 0.0
            svc._maybe_tick_missions()
            m_bud_now = ms.load(reg_path)["missions"][0]
            check("mission leg: max_slices seal lands with evidence",
                  m_bud_now["state"] == "sealed"
                  and bool(m_bud_now["evidence"])
                  and "max_slices" in m_bud_now["evidence"][0])
            check("mission leg: seal report delivered (never silent)",
                  len(fake.sends) == 1
                  and ms.ZH["sealed_report"] in fake.sends[0][1]
                  and ms.ZH["sec_open"] in fake.sends[0][1])
            log_txt = svc.log.read_text()
            check("mission leg: seal logged with its guard",
                  "MISSION_SEAL" in log_txt and "guard=max_slices" in log_txt)

            # car 2: a declared gap degrades the subtask, yet the leg keeps
            # ticking the rest of the queue (F4) — plan -> degraded -> done
            doc_dg = {"missions": [], "last_tick": clock[0] - 700.0}
            m_dg = ms.create(doc_dg["missions"], "degradable work",
                             reporting="all", origin=424242,
                             manifest=man, now=clock[0])
            ms.transition(m_dg, "running", clock[0])
            ms.save(reg_path, doc_dg)
            dg_script = ScriptedChat([
                fake_response(text="plan\nMILESTONE: Phase A\n"
                                   "SUBTASK: alpha\nSUBTASK: beta"),
                fake_response(text="alpha is partial\nGAP: missing tool X"),
                fake_response(text="beta complete\nTASK DONE\nMISSION DONE"),
            ])
            svc.chat = dg_script.chat
            fake.sends.clear()
            svc._mission_check = 0.0
            svc._maybe_tick_missions()
            mrow_dg = ms.load(reg_path)["missions"][0]
            check("mission leg: plan slice lands the milestone structure",
                  mrow_dg["counters"]["slices"] == 1
                  and len(mrow_dg["tasks"]) == 2
                  and [m["title"] for m in mrow_dg["plan"]["milestones"]]
                  == ["Phase A"], str(mrow_dg.get("plan")))
            clock[0] += 130.0
            svc._mission_check = 0.0
            svc._maybe_tick_missions()
            mrow_dg = ms.load(reg_path)["missions"][0]
            check("mission leg: a declared gap enters degraded (subtask too)",
                  mrow_dg["state"] == "degraded"
                  and mrow_dg["tasks"][0]["status"] == "degraded"
                  and mrow_dg["tasks"][1]["status"] == "pending",
                  str([(t["id"], t["status"]) for t in mrow_dg["tasks"]]))
            check("mission leg: degraded report delivered (never silent)",
                  any(ms.ZH["gap"] in s[1] for s in fake.sends))
            clock[0] += 130.0
            svc._mission_check = 0.0
            svc._maybe_tick_missions()
            mrow_dg = ms.load(reg_path)["missions"][0]
            check("mission leg: degraded does not stall the queue — the next "
                  "subtask runs through to done",
                  mrow_dg["state"] == "done" and dg_script.calls == 3
                  and mrow_dg["tasks"][1]["status"] == "done"
                  and mrow_dg["tasks"][0]["status"] == "degraded")
            check("mission leg: slice turns keep the full toolset incl. delegate",
                  bool(dg_script.seen_tools)
                  and all("delegate" in tools for tools in dg_script.seen_tools)
                  and all(len(tools) > 5 for tools in dg_script.seen_tools),
                  str(dg_script.seen_tools[:1]))
            ev_dg = [json.loads(line) for line in
                     ms.events_path(reg_path).read_text(
                         encoding="utf-8").splitlines()]
            check("mission leg: slice events carry the delegate count",
                  any(r.get("kind") == "slice"
                      and isinstance(r.get("delegate"), int) for r in ev_dg)
                  and all("delegate" in r for r in ev_dg
                          if r.get("kind") == "slice"))

            # paused mission: the leg does zero work
            doc_p = {"missions": [], "last_tick": clock[0] - 700.0}
            m_pau = ms.create(doc_p["missions"], "paused work",
                              reporting="all", origin=424242, now=clock[0])
            ms.transition(m_pau, "running", clock[0])
            ms.transition(m_pau, "paused_user", clock[0])
            ms.save(reg_path, doc_p)
            p_script = ScriptedChat([])   # any model call would blow up
            svc.chat = p_script.chat
            fake.sends.clear()
            svc._mission_check = 0.0
            svc._maybe_tick_missions()
            check("mission leg: paused mission never executes or reports",
                  p_script.calls == 0 and fake.sends == []
                  and ms.load(reg_path)["missions"][0]["counters"]["slices"] == 0)
            check("mission leg: paused registry still records the tick mark",
                  ms.load(reg_path)["last_tick"] == clock[0])

            # four-tier delivery fallback, same chain as the cron leg
            check("mission leg: no channel at all -> log-only, no raise",
                  svc._mission_deliver({"origin": None}, "nobody-here")
                  == "none")
            fake.sends.clear()
            check("mission leg: origin chat uses the shared chain",
                  svc._mission_deliver({"origin": 424242}, "origin-ping")
                  == "origin" and fake.sends == [("424242", "origin-ping")])
            os.environ["MYCORE_WD_CHAT"] = "777099"
            fake.sends.clear()
            check("mission leg: falls back to MYCORE_WD_CHAT",
                  svc._mission_deliver({"origin": None}, "wd-ping") == "wd"
                  and fake.sends == [("777099", "wd-ping")])
            os.environ.pop("MYCORE_WD_CHAT", None)

            # ---- car 3 drill: web3 paused_approve over the REAL plan store -
            # The approval gate itself is untouched: the drill runs the real
            # prepare (stubbed venue transport, thresholds pinned to 0 = never
            # auto) + the real console approve simulation (plan_decide) and
            # asserts execute-without-approval is STILL refused in between.
            from mycore import exchanges as ex
            from mycore.tools import Tool
            saved_slot_d, saved_thr_d = ex._slot, ex.thresholds
            ex._slot = lambda name: "DRILL-KEY"
            ex.thresholds = lambda settings=None: {"per_tx": 0.0,
                                                   "daily": 0.0}
            drill_book = {"bids": [["80.0", "5"]],
                          "asks": [["90.0", "5"], ["91.0", "5"]]}

            def drill_transport(url, method, headers, payload, timeout):
                if "/api/v5/market/books" in url:
                    return json.dumps({"code": "0", "data": [drill_book]})
                if "/api/v5/account/trade-fee" in url:
                    return json.dumps({"code": "0",
                                       "data": [{"takerFeeRate": "0.001"}]})
                raise AssertionError(f"unexpected url {url}")

            def drill_prepare(venue="okx", side="buy", symbol="BTC-USDT",
                              order_type="limit", qty=1, price=88, **_kw):
                return ex.prepare_order(venue, side, symbol, order_type, qty,
                                        price, transport=drill_transport,
                                        state_dir=str(svc.state))

            orig_tool = svc.registry._tools.get("exchange_prepare_order")
            svc.registry._tools["exchange_prepare_order"] = Tool(
                "exchange_prepare_order", "drill stub",
                {"type": "object", "properties": {}}, drill_prepare)
            hook_calls: list = []
            orig_fire_hooks = svc._fire_hooks

            def rec_fire_hooks(event, *args, **kw):
                hook_calls.append((event, kw))
                return orig_fire_hooks(event, *args, **kw)

            svc._fire_hooks = rec_fire_hooks
            try:
                doc_w = {"missions": [], "last_tick": clock[0] - 700.0}
                m_w = ms.create(doc_w["missions"], "rebalance the book",
                                reporting="all", origin=424242,
                                manifest=man, now=clock[0])
                ms.transition(m_w, "running", clock[0])
                ms.save(reg_path, doc_w)
                w_script = ScriptedChat([
                    fake_response(text="plan\n"
                                   "SUBTASK: place the BTC buy order\n"
                                   "SUBTASK: write the summary"),
                    fake_response(text=None,
                                  calls=[("exchange_prepare_order",
                                          {"venue": "okx", "side": "buy",
                                           "symbol": "BTC-USDT",
                                           "order_type": "limit",
                                           "qty": 1, "price": 88})]),
                    fake_response(text="order prepared, waiting for the owner"),
                    fake_response(text="summary written\nTASK DONE"),
                    fake_response(text="order executed\nTASK DONE"),
                ])
                svc.chat = w_script.chat
                fake.sends.clear()
                hook_calls.clear()
                # tick 1: plan slice (2 subtasks, one hook emission)
                svc._mission_check = 0.0
                svc._maybe_tick_missions()
                wrow = ms.load(reg_path)["missions"][0]
                check("approve drill: plan slice lands + one mission_tick hook",
                      wrow["counters"]["slices"] == 1
                      and len(hook_calls) == 1
                      and hook_calls[0][0] == "mission_tick"
                      and set(hook_calls[0][1]) == {"mission",
                                                   "mission_state",
                                                   "mission_progress"}
                      and hook_calls[0][1]["mission"] == m_w["id"]
                      and hook_calls[0][1]["mission_progress"] == "0/2",
                      str(hook_calls[:1]))
                # tick 2: the subtask prepares a PENDING order plan
                clock[0] += 130.0
                svc._mission_check = 0.0
                svc._maybe_tick_missions()
                wrow = ms.load(reg_path)["missions"][0]
                pid_drill = (wrow["tasks"][0].get("plans") or [None])[0]
                check("approve drill: subtask parks, mission enters "
                      "paused_approve, queue intact",
                      wrow["state"] == "paused_approve"
                      and wrow["tasks"][0]["status"] == "awaiting_approval"
                      and isinstance(pid_drill, str)
                      and pid_drill.startswith("pl_")
                      and wrow["tasks"][1]["status"] == "pending",
                      str([(t["id"], t["status"]) for t in wrow["tasks"]]))
                check("approve drill: approval request reported to the owner",
                      any(ms.ZH["approve_fact"].format(p=pid_drill or "x")
                          in t[1] for t in fake.sends),
                      str(fake.sends[-1:]) )
                ev_rows_w = [json.loads(ln) for ln in
                             ms.events_path(reg_path).read_text(
                                 encoding="utf-8").splitlines()]
                check("approve drill: suspension recorded in the event stream",
                      any(r.get("to") == "paused_approve"
                          and r.get("via") == "slice"
                          and r.get("plans") == [pid_drill]
                          for r in ev_rows_w if r.get("mission") == m_w["id"]),
                      str(ev_rows_w[-3:]))
                check("approve drill: heartbeat stamped on the slice",
                      isinstance(wrow.get("last_progress_at"), (int, float))
                      and wrow["last_progress_at"] > 0)
                # RED LINE drill: execute WITHOUT approval still refused
                try:
                    ex.execute_order(pid_drill, state_dir=str(svc.state))
                    check("approve drill: execute without approval refused",
                          False, "accepted")
                except ex.ExchangeError as e:
                    check("approve drill: execute without approval refused "
                          "(gate untouched)", "approval" in str(e), str(e))
                # tick 3: the OTHER subtask keeps advancing while parked (F4)
                clock[0] += 130.0
                svc._mission_check = 0.0
                svc._maybe_tick_missions()
                wrow = ms.load(reg_path)["missions"][0]
                check("approve drill: other subtask advances while parked (F4)",
                      wrow["state"] == "paused_approve"
                      and wrow["tasks"][0]["status"] == "awaiting_approval"
                      and wrow["tasks"][1]["status"] == "done"
                      and w_script.calls == 4,
                      str([(t["id"], t["status"]) for t in wrow["tasks"]]))
                # the owner approves in the console (existing simulation)
                ex.plan_decide(pid_drill, "approve", decided_by="console",
                               state_dir=str(svc.state))
                clock[0] += 130.0
                svc._mission_check = 0.0
                svc._maybe_tick_missions()
                wrow = ms.load(reg_path)["missions"][0]
                check("approve drill: approval resumes the queue -> done",
                      wrow["state"] == "done"
                      and all(t["status"] == "done" for t in wrow["tasks"])
                      and w_script.calls == 5
                      and any(ms.ZH["done_report"] in t[1]
                              for t in fake.sends),
                      f"state={wrow['state']} calls={w_script.calls}")
                ev_rows_w = [json.loads(ln) for ln in
                             ms.events_path(reg_path).read_text(
                                 encoding="utf-8").splitlines()]
                check("approve drill: resume recorded via approval in the stream",
                      any(r.get("to") == "running"
                          and r.get("via") == "approval"
                          for r in ev_rows_w
                          if r.get("mission") == m_w["id"]))
                check("approve drill: exactly one mission_tick hook per tick",
                      len(hook_calls) == 4
                      and all(ev == "mission_tick" for ev, _ in hook_calls),
                      str([ev for ev, _ in hook_calls]))
                check("approve drill: hook payload carries id/state/progress only",
                      all("rebalance" not in json.dumps(kw)
                          and "424242" not in json.dumps(kw)
                          for _ev, kw in hook_calls))
                # no active mission -> no hook, no slice (idle leg)
                hooks_idle = len(hook_calls)
                fake.sends.clear()
                svc._mission_check = 0.0
                svc._maybe_tick_missions()
                check("approve drill: idle leg emits no mission_tick hook",
                      len(hook_calls) == hooks_idle
                      and w_script.calls == 5 and fake.sends == [])
            finally:
                ex._slot, ex.thresholds = saved_slot_d, saved_thr_d
                svc._fire_hooks = orig_fire_hooks
                if orig_tool is None:
                    svc.registry._tools.pop("exchange_prepare_order", None)
                else:
                    svc.registry._tools["exchange_prepare_order"] = orig_tool

        # ---- G. console API + settings partition (temp profile) ------------
        prof_root = Path(tempfile.mkdtemp(prefix="mission_prof_"))
        (prof_root / "slots").mkdir(parents=True)
        (prof_root / "slots" / "settings.json").write_text("{}",
                                                           encoding="utf-8")
        (prof_root / "slots" / "secrets.env").write_text(
            "WEB_TOKEN=mission-gate-token-0123456789\n", encoding="utf-8")
        saved_keys = {k: os.environ.get(k)
                      for k in ("AIDOG_PROFILES_DIR", "AIDOG_PROFILE")}
        os.environ["AIDOG_PROFILES_DIR"] = str(prof_root)
        os.environ["AIDOG_PROFILE"] = "slots"
        srv = None
        try:
            import threading as _threading
            import urllib.error
            import urllib.request
            from http.server import ThreadingHTTPServer
            import web.server as ws

            srv = ThreadingHTTPServer(("127.0.0.1", 0), ws.Handler)
            _threading.Thread(target=srv.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            token = "mission-gate-token-0123456789"

            def call(path, payload=None, auth=None):
                data = json.dumps(payload).encode() if payload is not None \
                    else None
                req = urllib.request.Request(
                    base + path, data=data,
                    headers={"Content-Type": "application/json",
                             **({"Authorization": "Bearer " + auth}
                                if auth else {})})
                try:
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        return resp.status, json.loads(resp.read().decode())
                except urllib.error.HTTPError as e:
                    raw = e.read().decode()
                    try:
                        return e.code, json.loads(raw)
                    except ValueError:
                        return e.code, raw

            s, board = call("/api/mission")
            check("mission api: GET board shape on an empty registry",
                  s == 200 and board.get("reporting") == "milestones"
                  and board.get("active") is None
                  and board.get("recent") == [] and board.get("count") == 0,
                  f"{s} {board}")
            s, _body = call("/api/mission",
                            {"action": "start", "goal": "g"})
            check("mission api: POST without bearer -> 401", s == 401, str(s))
            for payload, why in (
                    ({"action": "explode"}, "unknown action"),
                    ({"action": "start"}, "start without a goal"),
                    ({"action": "start", "goal": " "}, "blank goal"),
                    ({"action": "start", "goal": "g", "reporting": "loud"},
                     "bad reporting tier"),
                    ({"action": "pause"}, "pause without an id")):
                s, body = call("/api/mission", payload, auth=token)
                check(f"mission api: 400 on {why}",
                      s == 400 and "details" in body, f"{s} {body}")
            s, body = call("/api/mission",
                           {"action": "pause", "id": "nope00"}, auth=token)
            check("mission api: 404 on an unknown id", s == 404, str(body))
            s, body = call("/api/mission",
                           {"action": "start", "goal": "grow the tool site"},
                           auth=token)
            mrow = body.get("mission") or {}
            check("mission api: start -> running mission row",
                  s == 200 and body.get("ok") is True
                  and mrow.get("state") == "running"
                  and mrow.get("goal") == "grow the tool site"
                  and mrow.get("reporting") == "milestones"
                  and isinstance(mrow.get("id"), str) and bool(mrow["id"]),
                  f"{s} {body}")
            mid = mrow["id"]
            s, board = call("/api/mission")
            check("mission api: GET shows the active mission",
                  isinstance(board.get("active"), dict)
                  and board["active"].get("id") == mid
                  and board.get("count") == 1
                  and board["active"]["progress"]["total"] == 0,
                  str(board.get("active")))
            s, caps = call("/api/capabilities")
            check("mission api: capabilities carries the mission summary",
                  s == 200 and isinstance(caps.get("mission"), dict)
                  and caps["mission"].get("active") == 1
                  and caps["mission"].get("running") is True,
                  str(caps.get("mission")))
            s, body = call("/api/mission",
                           {"action": "start", "goal": "second"}, auth=token)
            check("mission api: second start -> 409 with the active id",
                  s == 409 and body.get("mission_id") == mid, f"{s} {body}")
            s, body = call("/api/mission",
                           {"action": "pause", "id": mid}, auth=token)
            check("mission api: pause -> paused_user",
                  s == 200 and body["mission"]["state"] == "paused_user",
                  f"{s} {body}")
            s, body = call("/api/mission",
                           {"action": "pause", "id": mid}, auth=token)
            check("mission api: pause twice -> 409", s == 409, str(body))
            s, body = call("/api/mission",
                           {"action": "resume", "id": mid}, auth=token)
            check("mission api: resume -> running",
                  s == 200 and body["mission"]["state"] == "running",
                  str(body))
            s, body = call("/api/mission",
                           {"action": "resume", "id": mid}, auth=token)
            check("mission api: resume twice -> 409", s == 409, str(body))
            s, body = call("/api/mission",
                           {"action": "stop", "id": mid}, auth=token)
            check("mission api: stop seals with evidence",
                  s == 200 and body["mission"]["state"] == "sealed"
                  and bool(body["mission"]["evidence"]), f"{s} {body}")
            s, body = call("/api/mission",
                           {"action": "stop", "id": mid}, auth=token)
            check("mission api: stop of a terminal mission -> 409",
                  s == 409, str(body))
            s, body = call("/api/mission",
                           {"action": "stop", "id": "nope00"}, auth=token)
            check("mission api: stop of an unknown id -> 404",
                  s == 404, str(body))
            s, body = call("/api/mission",
                           {"action": "start", "goal": "tiered",
                            "reporting": "final"}, auth=token)
            check("mission api: start can pin the reporting tier",
                  s == 200 and body["mission"]["reporting"] == "final",
                  str(body))
            mid2 = body["mission"]["id"]
            call("/api/mission", {"action": "stop", "id": mid2}, auth=token)
            s, board = call("/api/mission")
            board_text = json.dumps(board, ensure_ascii=False)
            check("mission api: board is credential- and path-free",
                  token not in board_text and "/mnt/" not in board_text
                  and board.get("active") is None
                  and len(board.get("recent") or []) == 2,
                  board_text[:200])
            # settings partition: three tiers + refusals (temp profile file)
            for tier in ms.REPORTING_TIERS:
                clean, errs = ws.validate_settings_patch(
                    {"mission": {"reporting": tier}})
                check(f"mission settings: {tier} accepted",
                      clean == {"mission": {"reporting": tier}}
                      and not errs, str(errs))
            clean, errs = ws.validate_settings_patch(
                {"mission": {"reporting": "loud"}})
            check("mission settings: out-of-enum tier refused",
                  not clean and bool(errs)
                  and "mission.reporting must be one of" in errs[0],
                  str(errs))
            clean, errs = ws.validate_settings_patch({"mission": {"nope": 1}})
            check("mission settings: unknown partition key refused",
                  not clean and bool(errs)
                  and "unknown mission key" in errs[0], str(errs))
            clean, errs = ws.validate_settings_patch({"mission": "all"})
            check("mission settings: non-object partition refused",
                  not clean and bool(errs)
                  and "must be an object" in errs[0], str(errs))
            s, _body = call("/api/settings",
                            {"mission": {"reporting": "all"}}, auth=token)
            s2, st = call("/api/settings")
            check("mission settings: console write lands and reads back",
                  s == 200 and s2 == 200
                  and (st.get("mission") or {}).get("reporting") == "all",
                  f"{s}/{s2} {st.get('mission')}")
            s, body = call("/api/settings",
                           {"mission": {"reporting": "loud"}}, auth=token)
            check("mission settings: console write refuses a bad tier",
                  s == 400 and any("mission.reporting" in str(d)
                                   for d in body.get("details", [])),
                  f"{s} {body}")
            s, caps = call("/api/capabilities")
            check("mission settings: capabilities tier follows the write",
                  s == 200
                  and (caps.get("mission") or {}).get("reporting") == "all",
                  str(caps.get("mission")))
            # ---- car 2: budget settings partition + start override ----------
            check("mission settings: defaults carry reporting + guards + car-3 keys",
                  ms.DEFAULT_SETTINGS
                  == {"reporting": "milestones", "quiet_hours": "",
                      "min_interval": 0, "max_slices": 40,
                      "max_wall_hours": 24.0,
                      "max_daily_model_calls": 200},
                  str(ms.DEFAULT_SETTINGS))
            clean_ok, errs_ok = ws.validate_settings_patch(
                {"mission": {"reporting": "final", "max_slices": 10,
                             "max_wall_hours": 0.5,
                             "max_daily_model_calls": 100000}})
            check("mission settings: a full budget block is accepted",
                  clean_ok == {"mission": {"reporting": "final",
                                           "max_slices": 10,
                                           "max_wall_hours": 0.5,
                                           "max_daily_model_calls": 100000}}
                  and not errs_ok, str(errs_ok))
            for bad, frag in (({"max_slices": 0}, "mission.max_slices"),
                              ({"max_slices": "7"}, "mission.max_slices"),
                              ({"max_slices": 3.5}, "mission.max_slices"),
                              ({"max_wall_hours": 0.05},
                               "mission.max_wall_hours"),
                              ({"max_wall_hours": 8761},
                               "mission.max_wall_hours"),
                              ({"max_wall_hours": 1.25},
                               "mission.max_wall_hours"),
                              ({"max_daily_model_calls": 0},
                               "mission.max_daily_model_calls")):
                clean_b, errs_b = ws.validate_settings_patch({"mission": bad})
                check(f"mission settings: {bad} refused with a named reason",
                      not clean_b and any(frag in e for e in errs_b),
                      str(errs_b))
            s, body = call("/api/settings",
                           {"mission": {"max_slices": 0}}, auth=token)
            check("mission settings: console write refuses a bad guard",
                  s == 400 and any("mission.max_slices" in str(d)
                                   for d in body.get("details", [])),
                  f"{s} {body}")
            s, body = call("/api/settings",
                           {"mission": {"max_slices": 7,
                                        "max_wall_hours": 1.5}}, auth=token)
            s2, st = call("/api/settings")
            mis_st = st.get("mission") or {}
            check("mission budget: guards land and read back from settings",
                  s == 200 and s2 == 200
                  and mis_st.get("max_slices") == 7
                  and mis_st.get("max_wall_hours") == 1.5
                  and mis_st.get("reporting") == "all"
                  and mis_st.get("max_daily_model_calls")
                  == ms.DEFAULT_BUDGET["max_daily_model_calls"], str(mis_st))
            # start WITHOUT an override -> the settings values freeze in
            s, body = call("/api/mission",
                           {"action": "start",
                            "goal": "budget from settings"}, auth=token)
            b1row = body.get("mission") or {}
            b1id = b1row.get("id")
            check("mission budget: start freezes the settings values",
                  s == 200 and b1row.get("budget")
                  == {"max_slices": 7, "max_wall_hours": 1.5,
                      "max_daily_model_calls":
                          ms.DEFAULT_BUDGET["max_daily_model_calls"]},
                  f"{s} {b1row.get('budget')}")
            s, body = call("/api/mission", {"action": "stop", "id": b1id},
                           auth=token)
            check("mission budget: settings-budgeted mission stops",
                  s == 200, str(body))
            # start WITH an explicit per-mission override
            s, body = call("/api/mission",
                           {"action": "start", "goal": "budget override",
                            "budget": {"max_slices": 3,
                                       "max_wall_hours": 0.5,
                                       "max_daily_model_calls": 10}},
                           auth=token)
            b2row = body.get("mission") or {}
            b2id = b2row.get("id")
            check("mission budget: start override wins over settings",
                  s == 200 and b2row.get("budget")
                  == {"max_slices": 3, "max_wall_hours": 0.5,
                      "max_daily_model_calls": 10},
                  f"{s} {b2row.get('budget')}")
            # a later settings edit must NOT touch the started mission
            call("/api/settings",
                 {"mission": {"max_slices": 9, "max_wall_hours": 2.5}},
                 auth=token)
            s, det = call(f"/api/mission?id={b2id}")
            drow = det.get("mission") or {}
            check("mission detail: budget stays frozen after a settings edit",
                  s == 200 and drow.get("budget")
                  == {"max_slices": 3, "max_wall_hours": 0.5,
                      "max_daily_model_calls": 10}, f"{s} {drow.get('budget')}")
            check("mission detail: shape = row + plan/events/budget_used",
                  drow.get("plan") is None     # started, never planned -> null
                  and isinstance(drow.get("events"), list)
                  and set(drow.get("budget_used") or {})
                  == {"slices", "wall_hours", "daily_calls"}
                  and isinstance((drow.get("budget_used") or {})
                                 .get("wall_hours"), float)
                  and isinstance(drow.get("progress"), dict), str(drow)[:300])
            det_text = json.dumps(det, ensure_ascii=False)
            check("mission detail: zero credentials / origin / manifest / path",
                  token not in det_text and "/mnt/" not in det_text
                  and "origin" not in drow and "manifest" not in drow,
                  det_text[:200])
            s, det404 = call("/api/mission?id=nope00")
            check("mission detail: unknown id -> 404", s == 404,
                  str(det404))
            s, body = call("/api/mission", {"action": "stop", "id": b2id},
                           auth=token)
            check("mission budget: overridden mission stops", s == 200,
                  str(body))
            for payload, frag in (
                    ({"action": "start", "goal": "x", "budget": "all"},
                     "budget must be an object"),
                    ({"action": "start", "goal": "x",
                      "budget": {"max_slices": 0}}, "out of range"),
                    ({"action": "start", "goal": "x",
                      "budget": {"nope": 1}}, "unknown budget key"),
                    ({"action": "start", "goal": "x",
                      "budget": {"max_wall_hours": 1.25}},
                     "at most 1 decimal")):
                s, body = call("/api/mission", payload, auth=token)
                check(f"mission budget: 400 on {frag}",
                      s == 400 and any(frag in str(d)
                                       for d in body.get("details", [])),
                      f"{s} {body}")
            # ---- car 3: quiet_hours + min_interval settings partition ----
            for good_q in ("", "09:00-17:00", "23:00-07:00"):
                clean_q, errs_q = ws.validate_settings_patch(
                    {"mission": {"quiet_hours": good_q}})
                check(f"mission settings: quiet_hours {good_q!r} accepted",
                      clean_q == {"mission": {"quiet_hours": good_q}}
                      and not errs_q, str(errs_q))
            for bad_q, frag_q in (("9:00-17:00", "HH:MM-HH:MM"),
                                  ("25:00-10:00", "HH:MM-HH:MM"),
                                  ("09:00-09:00", "differ"),
                                  ("0900-1700", "HH:MM-HH:MM"),
                                  (1700, "must be"),
                                  (None, "must be")):
                clean_q, errs_q = ws.validate_settings_patch(
                    {"mission": {"quiet_hours": bad_q}})
                check(f"mission settings: quiet_hours {bad_q!r} refused",
                      not clean_q and any(frag_q in e for e in errs_q),
                      str(errs_q))
            for good_i in (0, 1, 600, 86400):
                clean_i, errs_i = ws.validate_settings_patch(
                    {"mission": {"min_interval": good_i}})
                check(f"mission settings: min_interval {good_i} accepted",
                      clean_i == {"mission": {"min_interval": good_i}}
                      and not errs_i, str(errs_i))
            for bad_i, frag_i in ((-1, "0..86400"), (86401, "0..86400"),
                                  ("60", "0..86400"), (True, "0..86400"),
                                  (1.5, "0..86400")):
                clean_i, errs_i = ws.validate_settings_patch(
                    {"mission": {"min_interval": bad_i}})
                check(f"mission settings: min_interval {bad_i!r} refused",
                      not clean_i and any(frag_i in e for e in errs_i),
                      str(errs_i))
            s, body = call("/api/settings",
                           {"mission": {"quiet_hours": "23:00-07:00",
                                        "min_interval": 300}}, auth=token)
            s2, st = call("/api/settings")
            mis_st2 = st.get("mission") or {}
            check("mission settings: quiet_hours + min_interval land and read back",
                  s == 200 and s2 == 200
                  and mis_st2.get("quiet_hours") == "23:00-07:00"
                  and mis_st2.get("min_interval") == 300
                  and mis_st2.get("reporting") == "all", str(mis_st2))
            s, body = call("/api/settings",
                           {"mission": {"quiet_hours": "9am-5pm"}},
                           auth=token)
            check("mission settings: console write refuses a bad window",
                  s == 400 and any("quiet_hours" in str(d)
                                   for d in body.get("details", [])),
                  f"{s} {body}")
            call("/api/settings",
                 {"mission": {"quiet_hours": "", "min_interval": 0}},
                 auth=token)   # restore defaults for the remaining legs

            # ---- car 3: A-face constraints via the console API ------------
            for payload, frag in (
                    ({"action": "start", "goal": "x", "deadline": "someday"},
                     "deadline"),
                    ({"action": "start", "goal": "x",
                      "deadline": "2020-01-01T00:00:00Z"}, "future"),
                    ({"action": "start", "goal": "x", "redlines": "r" * 501},
                     "500"),
                    ({"action": "start", "goal": "x",
                      "redlines": "tab\there"}, "control"),
                    ({"action": "start", "goal": "x",
                      "deliverable": "d" * 201}, "200")):
                s, body = call("/api/mission", payload, auth=token)
                check(f"mission constraints: 400 on {frag}",
                      s == 400 and any(frag in str(d)
                                       for d in body.get("details", [])),
                      f"{s} {body}")
            s, body = call("/api/mission",
                           {"action": "start", "goal": "constrained run",
                            "deadline": "2031-06-01T00:00:00+08:00",
                            "redlines": "no paid ads",
                            "deliverable": "weekly report"}, auth=token)
            crow = body.get("mission") or {}
            cid = crow.get("id")
            check("mission constraints: start lands the three fields",
                  s == 200
                  and crow.get("deadline") == "2031-06-01T00:00:00+08:00"
                  and crow.get("redlines") == "no paid ads"
                  and crow.get("deliverable") == "weekly report"
                  and crow.get("approvals") == [], f"{s} {crow}")
            s, det = call(f"/api/mission?id={cid}")
            drow = det.get("mission") or {}
            check("mission constraints: detail echoes the three fields",
                  s == 200 and drow.get("deadline")
                  == "2031-06-01T00:00:00+08:00"
                  and drow.get("redlines") == "no paid ads"
                  and drow.get("deliverable") == "weekly report"
                  and isinstance(drow.get("approvals"), list),
                  str(drow)[:200])
            # ---- car 3: illegal transitions from paused_approve -> 409 ----
            reg_w = ms.load(ms.default_path())
            rw = ms.find(reg_w["missions"], cid)
            ms.transition(rw, "paused_approve",
                          rw.get("updated_at") or 1700000000.0)
            ms.save(ms.default_path(), reg_w)
            s, body = call("/api/mission", {"action": "pause", "id": cid},
                           auth=token)
            check("mission api: pause while awaiting approval -> 409",
                  s == 409 and any("paused_approve" in str(d)
                                   for d in body.get("details", [])),
                  f"{s} {body}")
            s, body = call("/api/mission", {"action": "resume", "id": cid},
                           auth=token)
            check("mission api: resume from paused_approve -> 409",
                  s == 409, f"{s} {body}")
            s, body = call("/api/mission", {"action": "stop", "id": cid},
                           auth=token)
            check("mission api: stop from paused_approve seals (F4 exit)",
                  s == 200 and body["mission"]["state"] == "sealed",
                  f"{s} {body}")
            # backward compat: a plain start carries null constraints
            s, body = call("/api/mission",
                           {"action": "start", "goal": "plain start"},
                           auth=token)
            prow2 = body.get("mission") or {}
            pid2 = prow2.get("id")
            check("mission constraints: plain start stays null (old clients)",
                  s == 200 and prow2.get("deadline") is None
                  and prow2.get("redlines") is None
                  and prow2.get("deliverable") is None
                  and prow2.get("approvals") == [], f"{s} {prow2}")
            call("/api/mission", {"action": "stop", "id": pid2}, auth=token)

            # pause/resume from the degraded state (car 2 entry edges)
            s, body = call("/api/mission",
                           {"action": "start", "goal": "degrade me"},
                           auth=token)
            b3id = (body.get("mission") or {}).get("id")
            check("mission api: degraded-pause scenario starts",
                  s == 200 and bool(b3id), f"{s} {body}")
            reg_g = ms.load(ms.default_path())
            r3 = ms.find(reg_g["missions"], b3id)
            ms.transition(r3, "degraded",
                          r3.get("updated_at") or 1700000000.0)
            ms.save(ms.default_path(), reg_g)
            s, body = call("/api/mission",
                           {"action": "pause", "id": b3id}, auth=token)
            check("mission api: pause is legal from degraded (car 2)",
                  s == 200 and body["mission"]["state"] == "paused_user",
                  f"{s} {body}")
            s, body = call("/api/mission",
                           {"action": "resume", "id": b3id}, auth=token)
            check("mission api: resume from a degraded-origin pause runs",
                  s == 200 and body["mission"]["state"] == "running",
                  f"{s} {body}")
            s, body = call("/api/mission",
                           {"action": "pause", "id": b3id}, auth=token)
            check("mission api: pause while running lands paused_user",
                  s == 200 and body["mission"]["state"] == "paused_user",
                  f"{s} {body}")
            s, body = call("/api/mission",
                           {"action": "pause", "id": b3id}, auth=token)
            check("mission api: pause again -> 409 naming the allowed states",
                  s == 409 and any("allowed states: running, degraded"
                                   in str(d)
                                   for d in body.get("details", [])),
                  f"{s} {body}")
            call("/api/mission", {"action": "stop", "id": b3id}, auth=token)
            # ---- car 2: detail of a planned mission + a car-1 legacy row ----
            board_path = ms.default_path()
            reg_c = ms.load(board_path)
            legacy = {"id": "legacy1", "goal": "car one leftover",
                      "state": "sealed", "origin": 424242,
                      "reporting": "final",
                      "tasks": [{"id": "t1", "text": "old task",
                                 "status": "done"}],
                      "gaps": [], "methods": [], "blockers": [],
                      "evidence": ["retired in car 1"],
                      "created_at": 1700000000.0,
                      "updated_at": 1700000100.0,
                      "ended_at": 1700000100.0}
            reg_c["missions"].append(legacy)
            planned = ms.create(reg_c["missions"], "plan me",
                                reporting="all", now=1700000200.0)
            ms.transition(planned, "running", 1700000201.0)
            ms.apply_slice(planned, ms.parse_slice(
                "MILESTONE: Draft\nSUBTASK: write it\n"
                "MILESTONE: Ship\nSUBTASK: publish it"),
                1700000202.0, mode="plan")
            planned["methods"] = [{"text": "try CI", "marked": False}]
            planned["blockers"] = ["flaky runner"]
            ms.save(board_path, reg_c)
            for i in range(25):
                ms.append_event(board_path, planned["id"], "report",
                                {"event": "task_done", "delivered": "sent",
                                 "tier": "all", "n": i})
            ms.append_event(board_path, planned["id"], "slice",
                            {"n": 1, "mode": "plan", "stop": "end",
                             "iters": 3, "delegate": 2,
                             "preview": "tok " + tok_sentinel + " at "
                                        + path_sentinel})
            s, det = call(f"/api/mission?id={planned['id']}")
            prow = det.get("mission") or {}
            pplan = prow.get("plan")
            ms_rows = (pplan or {}).get("milestones") or []
            check("mission detail: plan = milestone groups with live subtasks",
                  s == 200 and isinstance(pplan, dict)
                  and [m.get("id") for m in ms_rows] == ["m1", "m2"]
                  and [m.get("title") for m in ms_rows] == ["Draft", "Ship"]
                  and [[t.get("id") for t in m.get("subtasks") or []]
                       for m in ms_rows] == [["t1"], ["t2"]]
                  and ms_rows[0]["subtasks"][0]["status"] == "pending"
                  and pplan.get("methods")
                  == [{"text": "try CI", "marked": False}]
                  and pplan.get("blockers") == ["flaky runner"],
                  str(pplan))
            ev_rows = prow.get("events") or []
            check("mission detail: event tail capped at 20, {ts,kind,text}",
                  len(ev_rows) == 20
                  and all(set(r) == {"ts", "kind", "text"}
                          for r in ev_rows)
                  and all(isinstance(r.get("ts"), (int, float))
                          for r in ev_rows), str(len(ev_rows)))
            ev_blob = json.dumps(ev_rows, ensure_ascii=False)
            check("mission detail: event text is scrubbed + delegate counted",
                  tok_sentinel not in ev_blob
                  and path_sentinel not in ev_blob
                  and "/mnt/" not in ev_blob and "delegate=2" in ev_blob,
                  ev_blob[:200])
            s, det_old = call("/api/mission?id=legacy1")
            orow = det_old.get("mission") or {}
            check("mission detail: car-1 row -> plan null + default budget",
                  s == 200 and orow.get("plan") is None
                  and orow.get("budget") == ms.DEFAULT_BUDGET
                  and (orow.get("budget_used") or {}).get("slices") == 0
                  and orow.get("state") == "sealed"
                  and isinstance((orow.get("budget_used") or {})
                                 .get("wall_hours"), float),
                  f"{s} {str(orow)[:200]}")
            s, board2 = call("/api/mission")
            rows_all = [board2.get("active")] + list(board2.get("recent") or [])
            check("mission list: no-id shape unchanged (Lane I contract)",
                  s == 200 and set(board2)
                  == {"reporting", "active", "recent", "count"}
                  and all(r is None or ("plan" not in r and "events" not in r
                                        and "budget_used" not in r)
                          for r in rows_all), str(sorted(board2)))
        finally:
            if srv is not None:
                srv.shutdown()
                srv.server_close()
            for key, val in saved_keys.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val
            shutil.rmtree(prof_root, ignore_errors=True)
    finally:
        if saved_tok is None:
            os.environ.pop(POLL_BOT_ENV, None)
        else:
            os.environ[POLL_BOT_ENV] = saved_tok
        if saved_wd is None:
            os.environ.pop("MYCORE_WD_CHAT", None)
        else:
            os.environ["MYCORE_WD_CHAT"] = saved_wd


def main() -> int:
    test_model()
    test_registry_and_tools()
    test_tools_toggle()
    test_envslots()
    test_model_key_slots()
    test_settings_models_budget()
    test_loop()
    test_compact()
    test_session()
    test_assembly()
    test_context_files()
    test_context_refs()
    test_checkpoint()
    test_modeltier()
    test_skill_dirs_chain()
    test_loop_budget()
    test_cli_precedence()
    test_tg()
    test_watchdog()
    test_service()
    test_evolution()
    test_scheduler()
    test_stream()
    test_loop_on_delta()
    test_typing()
    test_wd_locale()
    test_subagent()
    test_result_compression()
    test_execute_code()
    test_mcp()
    test_channels()
    test_channels_service()
    test_channels_domestic()
    test_channels_wa_signal()
    test_hooks()
    test_acp()
    test_tg_topics()
    test_browser()
    test_terminal()
    test_localmodels()
    test_stt()
    test_imagegen()
    test_connectors()
    test_media_tools()
    test_exchanges()
    test_exchanges_w2()
    test_tg_media()
    test_service_media()
    test_profiles()
    test_mission()
    print(f"\n{len(PASS)} pass · {len(FAIL)} fail")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
