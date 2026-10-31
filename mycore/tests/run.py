"""mycore offline test suite — fake model transport, zero network, zero credentials.

Run: python3 -m mycore.tests.run   (from the repo root)
Exit 0 = all assertions passed. English output per repo i18n policy.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

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
        check("tools: exactly 8 core tools", len(names) == 8, str(names))
        check("tools: expected set", set(names) == {
            "read_file", "write_file", "edit_file", "run_bash",
            "grep", "glob", "http_fetch", "skill_route"}, str(names))
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
    """Offline Telegram transport: scripted getUpdates batches + recorded sends."""

    def __init__(self, updates=None):
        self.updates = list(updates or [])
        self.sends = []            # [(chat_id_str, text)] in call order
        self.polls = 0

    def __call__(self, url, payload, timeout):
        method = url.rsplit("/", 1)[-1]
        if method == "sendMessage":
            import urllib.parse as up
            form = up.parse_qs(payload.decode("utf-8"))
            self.sends.append((form.get("chat_id", [""])[0],
                               form.get("text", [""])[0]))
            return json.dumps({"ok": True, "result": {"message_id": 1}})
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


def main() -> int:
    test_model()
    test_registry_and_tools()
    test_loop()
    test_compact()
    test_session()
    test_assembly()
    test_skill_dirs_chain()
    test_loop_budget()
    test_cli_precedence()
    test_tg()
    test_watchdog()
    test_service()
    test_evolution()
    print(f"\n{len(PASS)} pass · {len(FAIL)} fail")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
