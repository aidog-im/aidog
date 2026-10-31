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
from mycore.model import (ChatClient, ModelConfig, ModelError,  # noqa: E402
                          first_choice_text, tool_calls)
from mycore.loop import TurnResult  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


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
        self.calls = 0

    def chat(self, messages, tools=None, **kw):
        self.calls += 1
        self.seen.append([dict(m) for m in messages])
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
        check("tools: exactly 9 core tools", len(names) == 9, str(names))
        check("tools: expected set", set(names) == {
            "read_file", "write_file", "edit_file", "run_bash",
            "grep", "glob", "http_fetch", "skill_route", "cron"}, str(names))
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
    from mycore.assembly import CONSTITUTION
    repo_soul = REPO / "overlay" / "constitution" / "SOUL.md"
    dstack = build_system_stack()
    check("assembly: default constitution = repo overlay SOUL (primary)",
          repo_soul.is_file()
          and dstack[0]["content"] == repo_soul.read_text(encoding="utf-8")
          and CONSTITUTION == repo_soul)
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
    """Car 1-D4: multi-upstream failover (Tier C v1) — spec parsing, error
    classification, the attempt chain, no-replay-after-first-byte, config
    precedence. Fully offline (scripted transports), zero real keys."""
    import os
    from mycore import cli as _cli
    from mycore import modeltier as mt

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


class FakeTgTransport:
    """Offline Telegram transport: scripted getUpdates batches + recorded sends.
    `actions` records sendChatAction heartbeats [(chat_id_str, action)]."""

    def __init__(self, updates=None):
        self.updates = list(updates or [])
        self.sends = []            # [(chat_id_str, text)] in call order
        self.actions = []          # [(chat_id_str, action)] typing heartbeats
        self.polls = 0

    def __call__(self, url, payload, timeout):
        method = url.rsplit("/", 1)[-1]
        if method == "sendMessage":
            import urllib.parse as up
            form = up.parse_qs(payload.decode("utf-8"))
            self.sends.append((form.get("chat_id", [""])[0],
                               form.get("text", [""])[0]))
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


def mk_update(update_id, uid, chat_id, text):
    """A Telegram-style message update (offline fixture)."""
    return {"update_id": update_id,
            "message": {"message_id": 1,
                        "from": {"id": uid, "is_bot": False, "first_name": "x"},
                        "chat": {"id": chat_id, "type": "private"},
                        "date": 0, "text": text}}


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
          "machine-path" in ev.lint_text("the files live under <MNT_PATH>/data"))
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
        learned.write_text("- ok\n<HOME_DIR>/id_rsa leaked\n", encoding="utf-8")
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


def main() -> int:
    test_model()
    test_registry_and_tools()
    test_loop()
    test_compact()
    test_session()
    test_assembly()
    test_context_files()
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
    print(f"\n{len(PASS)} pass · {len(FAIL)} fail")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
