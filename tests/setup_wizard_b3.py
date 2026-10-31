#!/usr/bin/env python3
"""B3 setup-wizard suite — deploy/aidog_setup.py + aidog setup dispatch contract.

Offline (no network, no live stack), stdlib only; every write is confined to a
throwaway fake repo under /tmp — the REAL secrets.env / web/settings.json are
never touched (REPO self-derives from the copied script's location).
Exit 0 = all assertions passed.

Contracts locked here (regression wall):
  - --check is READ-ONLY: reports slot status by name + length, never creates
    or mutates secrets.env; exit 0 ready / 2 pending / 1 broken-format or
    invalid settings JSON
  - wizard provisioning: fresh fake repo gets secrets.env (+ settings.json)
    from templates only when missing; existing files are never clobbered
  - wizard fill: each answer written own-line, read back and verified; output
    reports name + length only — the piped values never appear in stdout+stderr
  - no-overwrite: a pre-filled slot is reported "kept" and stays byte-identical
  - validation: invalid answers are retried with the reason (value not echoed),
    max 3 attempts, then the slot stays empty (fail-closed, exit pending)
  - EOF: prompt input ending early is graceful — summary still prints, no
    traceback, no crash, exit follows slot state
  - boot leg: wizard's final question and --yes both delegate to
    deploy/install.sh with its exit code propagated; --no-boot never runs it
  - dispatch: `aidog setup --check/--yes/--no-boot` passes flags through;
    a non-TTY `aidog setup` (no flags) falls back to --yes (never blocks on
    prompts in scripts); --help lists the flags; unknown flags exit 2
  - zero-key onboarding (car 4b): the INTERACTIVE wizard probes local model
    servers between settings.json creation and the manual base_url prompt —
    a live AIDOG_LOCAL_MODEL_PROBES endpoint is adopted (base_url/model in
    settings, MODEL_API_KEY stays empty, NO manual base_url/model prompt);
    default probes (ollama :11434 / llama-server :8080 / lm-studio :1234,
    expected closed here) print one honest miss line and the prompt sequence
    stays byte-compatible with the manual flow above; probes must stay fast
    (<3s wall for the whole wizard run)
English output per repo i18n policy.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SETUP = REPO / "deploy" / "aidog_setup.py"
PERSIST = REPO / "deploy" / "aidog_persist.py"
TMP = Path("/tmp")  # hard boundary: no writes outside /tmp

PASS: list[str] = []
FAIL: list[str] = []

# Piped fixture answers — values are FAKE and constructed so that no source
# line ever starts with KEY=<value> (publish-gate leak pattern).
TOK_WEB = "fake_" + "webtok_0123456789abcdef"       # >=16, no spaces
TOK_TG = "123456789:" + "FAKESECRETabcdefABCDEF123456"
TOK_ALLOW = "123456789," + "987654321"
TOK_GH = "ghp_" + "FAKE0123456789abcdef0123456"
TOK_WD = "-100" + "1234567890"
TOK_ENDPOINT = "http://127.0.0.1:" + "9999/v1"
TOK_MODEL = "fake-model-1"


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


def run(script: Path, args: list[str], stdin: str = "",
        extra_env: dict | None = None) -> tuple[int, str]:
    env = dict(os.environ)
    # determinism: an inherited probe override from the operator shell must not
    # shift prompt sequences — default probing applies unless a test opts in
    env.pop("AIDOG_LOCAL_MODEL_PROBES", None)
    if extra_env:
        env.update(extra_env)
    p = subprocess.run([sys.executable, str(script), *args],
                       input=stdin, capture_output=True, text=True,
                       timeout=60, env=env)
    return p.returncode, p.stdout + p.stderr


def make_repo(t: Path) -> Path:
    """Throwaway fake repo: wizard + dispatcher + templates + stub installer."""
    (t / "deploy").mkdir(parents=True, exist_ok=True)
    (t / "web").mkdir(parents=True, exist_ok=True)
    for name in ("aidog_setup.py", "aidog_persist.py"):
        shutil.copy2(REPO / "deploy" / name, t / "deploy" / name)
    # zero-key onboarding probe module (namespace package — no mycore/
    # __init__.py copied, so the import stays light in the fake repo)
    (t / "mycore").mkdir(exist_ok=True)
    shutil.copy2(REPO / "mycore" / "localmodels.py",
                 t / "mycore" / "localmodels.py")
    shutil.copy2(REPO / "secrets.env.example", t / "secrets.env.example")
    shutil.copy2(REPO / "web" / "settings.example.json",
                 t / "web" / "settings.example.json")
    # Stub installer: records invocation, exit code controlled by FAKE_RC.
    (t / "deploy" / "install.sh").write_text(
        '#!/usr/bin/env bash\n'
        'echo ran >> "$(dirname "$0")/install.marker"\n'
        'exit "${FAKE_RC:-0}"\n', encoding="utf-8")
    return t


def sub(t: Path, name: str) -> Path:
    """Per-test subdir under the caller base (removed with it — no /tmp leaks)."""
    d = t / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_py(t: Path) -> Path:
    return t / "deploy" / "aidog_setup.py"


def persist_py(t: Path) -> Path:
    return t / "deploy" / "aidog_persist.py"


def own_line(path: Path, key: str) -> str | None:
    try:
        m = re.search(rf"(?m)^{re.escape(key)}=(\S+)$", path.read_text(encoding="utf-8"))
        return m.group(1) if m else None
    except OSError:
        return None


def format_violations(path: Path) -> list[int]:
    bad: list[int] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [-1]
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$", line):
            bad.append(i)
    return bad


def no_leak(out: str, *values: str) -> bool:
    return not any(v and v in out for v in values)


def full_answers(*, web: str = TOK_WEB, tg: str = TOK_TG, allow: str = TOK_ALLOW,
                 api: str = "", gh: str = TOK_GH, wd: str = TOK_WD,
                 base: str = "", model: str = "", boot: str = "n") -> str:
    """Exact stdin sequence for a fresh fake repo (all 6 slots empty)."""
    return "\n".join([web, tg, allow, api, gh, wd, base, model, boot]) + "\n"


# --------------------------------------------------------------------- tests

def test_help_and_dispatch(t: Path) -> None:
    rc, out = run(persist_py(t), ["setup", "--help"])
    check("aidog setup --help exits 0", rc == 0, f"rc={rc}")
    check("--help lists --check/--yes/--no-boot",
          all(f in out for f in ("--check", "--yes", "--no-boot")), out[:400])
    rc, _ = run(persist_py(t), ["setup", "--bogus"])
    check("unknown setup flag exits 2 (argparse)", rc == 2, f"rc={rc}")


def test_check_readonly(t: Path) -> None:
    rc, out = run(setup_py(t), ["--check"])
    check("--check fresh repo: exit 2 (absent = pending)", rc == 2, f"rc={rc} {out[:200]}")
    check("--check reports ABSENT by name", "ABSENT" in out, out[:200])
    check("--check created nothing (read-only)",
          not (t / "secrets.env").exists() and not (t / "web" / "settings.json").exists())

    # broken own-line format -> exit 1 (error, not pending)
    (t / "secrets.env").write_text(
        "# header\nWEB_TOKEN=" + TOK_WEB + "\nGLUED LINE with spaces\n",
        encoding="utf-8")
    rc, out = run(setup_py(t), ["--check"])
    check("--check broken format: exit 1", rc == 1, f"rc={rc} {out[:300]}")
    check("--check names the violating line, not its content",
          "violate own-line" in out and "line(s) 3" in out and "GLUED" not in out,
          out[:300])
    check("--check does not echo the filled value",
          no_leak(out, TOK_WEB), "secret leaked")

    # invalid settings JSON -> exit 1
    shutil.copy2(REPO / "secrets.env.example", t / "secrets.env")
    (t / "web" / "settings.json").write_text("{not json", encoding="utf-8")
    rc, out = run(setup_py(t), ["--check"])
    check("--check invalid settings JSON: exit 1", rc == 1, f"rc={rc} {out[:300]}")
    check("--check flags INVALID JSON by name", "INVALID JSON" in out, out[:300])

    # required slot FILLED but wrong format -> pending (never a false ready)
    (t / "web" / "settings.json").write_text(
        (REPO / "web" / "settings.example.json").read_text(encoding="utf-8"),
        encoding="utf-8")
    sec = t / "secrets.env"
    sec.write_text(sec.read_text(encoding="utf-8")
                   .replace("MYCORE_TG_TOKEN=", "MYCORE_TG_TOKEN=" + "short"),
                   encoding="utf-8")
    rc, out = run(setup_py(t), ["--check"])
    check("--check required slot invalid format: exit 2 (pending)", rc == 2,
          f"rc={rc} {out[-300:]}")
    check("--check flags INVALID format on the slot",
          "INVALID format" in out and "MYCORE_TG_TOKEN" in out, out[-400:])
    check("--check pending list includes the invalid slot",
          "pending:" in out and "MYCORE_TG_TOKEN" in out.split("pending:")[-1],
          out[-300:])


def test_yes_no_boot(t: Path) -> None:
    t2 = make_repo(sub(t, "yes"))
    rc, out = run(setup_py(t2), ["--yes", "--no-boot"])
    check("--yes --no-boot fresh: exit 2 (required slots pending)",
          rc == 2, f"rc={rc} {out[:300]}")
    check("--yes --no-boot provisioned secrets.env from template",
          (t2 / "secrets.env").is_file() and own_line(t2 / "secrets.env", "WEB_TOKEN") is None)
    check("--yes --no-boot provisioned settings.json from template",
          (t2 / "web" / "settings.json").is_file())
    check("--yes --no-boot never ran install.sh",
          not (t2 / "deploy" / "install.marker").exists())
    check("--yes --no-boot reports pending names", "pending:" in out, out[-400:])

    # all required filled -> exit 0, still no boot
    shutil.copy2(REPO / "secrets.env.example", t2 / "secrets.env")
    for key, val in (("WEB_TOKEN", TOK_WEB), ("MYCORE_TG_TOKEN", TOK_TG),
                     ("AIDOG_TG_ALLOWED_USERS", TOK_ALLOW)):
        (t2 / "secrets.env").write_text(
            (t2 / "secrets.env").read_text(encoding="utf-8")
            .replace(f"{key}=", f"{key}={val}"), encoding="utf-8")
    shutil.copy2(REPO / "web" / "settings.example.json", t2 / "web" / "settings.json")
    rc, out = run(setup_py(t2), ["--yes", "--no-boot"])
    check("--yes --no-boot all required filled: exit 0", rc == 0, f"rc={rc} {out[-300:]}")
    check("--yes --no-boot still never booted",
          not (t2 / "deploy" / "install.marker").exists())
    check("--yes zero-echo of filled values", no_leak(out, TOK_WEB, TOK_TG, TOK_ALLOW))


def test_yes_boot_delegation(t: Path) -> None:
    t2 = make_repo(sub(t, "boot"))
    rc, out = run(setup_py(t2), ["--yes"], extra_env={"FAKE_RC": "0"})
    check("--yes runs install.sh (marker)", (t2 / "deploy" / "install.marker").is_file(),
          out[:200])
    check("--yes propagates install.sh exit 0", rc == 0, f"rc={rc}")
    rc, _ = run(setup_py(t2), ["--yes"], extra_env={"FAKE_RC": "2"})
    check("--yes propagates install.sh exit 2", rc == 2, f"rc={rc}")

    # dispatcher: non-TTY `aidog setup` (no flags) must fall back to --yes,
    # never block on prompts in scripts
    (t2 / "deploy" / "install.marker").unlink(missing_ok=True)
    rc, _ = run(persist_py(t2), ["setup"], stdin="", extra_env={"FAKE_RC": "0"})
    check("non-TTY `aidog setup` falls back to installer (marker)",
          (t2 / "deploy" / "install.marker").is_file(), "installer not run")
    check("non-TTY `aidog setup` exit 0", rc == 0, f"rc={rc}")


def test_wizard_full_fill(t: Path) -> None:
    t2 = make_repo(sub(t, "fill"))
    rc, out = run(setup_py(t2), [], stdin=full_answers())
    check("wizard full fill: exit 0", rc == 0, f"rc={rc} {out[-400:]}")
    sec = t2 / "secrets.env"
    check("WEB_TOKEN written own-line", own_line(sec, "WEB_TOKEN") == TOK_WEB)
    check("MYCORE_TG_TOKEN written own-line", own_line(sec, "MYCORE_TG_TOKEN") == TOK_TG)
    check("allowlist written own-line",
          own_line(sec, "AIDOG_TG_ALLOWED_USERS") == TOK_ALLOW)
    check("MODEL_API_KEY empty input left it empty",
          own_line(sec, "MODEL_API_KEY") is None)
    check("AIDOG_GH_PAT written own-line", own_line(sec, "AIDOG_GH_PAT") == TOK_GH)
    check("MYCORE_WD_CHAT appended (absent from template)",
          own_line(sec, "MYCORE_WD_CHAT") == TOK_WD)
    check("secrets.env format clean after writes", format_violations(sec) == [],
          str(format_violations(sec)))
    comments = [l for l in sec.read_text(encoding="utf-8").splitlines()
                if l.strip().startswith("#")]
    check("template comments preserved", len(comments) >= 5, f"{len(comments)} comment lines")
    check("wizard reports written + length",
          "written WEB_TOKEN" in out and f"len {len(TOK_WEB)}" in out, out[:400])
    check("wizard ZERO-ECHO (values never printed)",
          no_leak(out, TOK_WEB, TOK_TG, TOK_ALLOW, TOK_GH, TOK_WD),
          "a piped secret appeared in output")
    check("wizard summary says all required filled",
          "all required slots filled" in out, out[-400:])
    check("boot declined -> install.sh not run",
          not (t2 / "deploy" / "install.marker").exists())
    st = json.loads((t2 / "web" / "settings.json").read_text(encoding="utf-8"))
    check("settings kept template endpoint (Enter = keep)",
          st["mycore"]["base_url"] == "http://127.0.0.1:3460/v1", str(st.get("mycore")))
    check("settings untouched keys preserved (language)",
          st.get("language") == "en", str(st.get("language")))


def test_wizard_no_overwrite(t: Path) -> None:
    t2 = make_repo(sub(t, "keep"))
    pre = ("# pre-existing header — do not touch\n"
           "WEB_TOKEN=" + TOK_WEB + "\n"
           "MYCORE_TG_TOKEN=\n")
    (t2 / "secrets.env").write_text(pre, encoding="utf-8")
    before = (t2 / "secrets.env").read_bytes()
    # WEB_TOKEN is filled -> NOT prompted; remaining prompts: tg, allow, api, gh,
    # wd, base, model, boot
    stdin = "\n".join([TOK_TG, TOK_ALLOW, "", TOK_GH, TOK_WD, "", "", "n"]) + "\n"
    rc, out = run(setup_py(t2), [], stdin=stdin)
    after_line = own_line(t2 / "secrets.env", "WEB_TOKEN")
    check("prefilled WEB_TOKEN kept byte-identical", after_line == TOK_WEB,
          str(after_line))
    check("prefilled header comment preserved",
          (t2 / "secrets.env").read_text(encoding="utf-8")
          .startswith("# pre-existing header"))
    check("wizard reports kept, not rewritten", "kept" in out and
          "WEB_TOKEN: set" in out, out[:300])
    check("kept value never echoed", no_leak(out, TOK_WEB), "value leaked")
    check("wizard still filled the other slots",
          own_line(t2 / "secrets.env", "MYCORE_TG_TOKEN") == TOK_TG)
    check("prefilled run: exit 0", rc == 0, f"rc={rc} {out[-300:]}")
    check("prefilled run never booted",
          not (t2 / "deploy" / "install.marker").exists())
    check("bytes outside the written line unchanged",
          len((t2 / "secrets.env").read_bytes()) >= len(before) - 1)


def test_wizard_validation_retries(t: Path) -> None:
    t2 = make_repo(sub(t, "retry"))
    # 3 invalid WEB_TOKEN answers -> skipped, stays empty (fail-closed)
    bad1, bad2, bad3 = "short", "also bad", "tiny_9chars"  # all <16 or spaced
    stdin = "\n".join([bad1, bad2, bad3,          # WEB_TOKEN: 3 strikes
                       TOK_TG, TOK_ALLOW, "", TOK_GH, TOK_WD, "", "", "n"]) + "\n"
    rc, out = run(setup_py(t2), [], stdin=stdin)
    check("3 invalid answers -> exit pending 2", rc == 2, f"rc={rc} {out[-300:]}")
    check("invalid reason shown with attempt counter",
          "invalid format" in out and "attempt 3/3" in out, out[:400])
    check("skipped-after-3 reported fail-closed",
          "skipped after 3 attempts" in out and "fail-closed" in out, out[:500])
    check("WEB_TOKEN stays empty", own_line(t2 / "secrets.env", "WEB_TOKEN") is None)
    check("invalid answers never echoed",
          no_leak(out, bad1, bad2, bad3), "invalid input leaked")
    check("later slots still filled after a skip",
          own_line(t2 / "secrets.env", "MYCORE_TG_TOKEN") == TOK_TG)
    check("pending summary printed", "pending:" in out, out[-300:])


def test_wizard_eof_graceful(t: Path) -> None:
    t2 = make_repo(sub(t, "eof"))
    rc, out = run(setup_py(t2), [], stdin="")
    check("empty stdin: exit 2 (nothing filled)", rc == 2, f"rc={rc} {out[-200:]}")
    check("empty stdin: no traceback", "Traceback" not in out, out[-500:])
    check("empty stdin: unasked slots reported",
          "not asked (input ended)" in out, out[:400])
    check("empty stdin: summary still printed",
          "secrets.env format:" in out and "pending:" in out, out[-400:])
    check("empty stdin: secrets provisioned for next run",
          (t2 / "secrets.env").is_file())
    check("empty stdin: no boot", not (t2 / "deploy" / "install.marker").exists())

    # EOF mid-wizard (after 2 answers) also stays graceful
    t3 = make_repo(sub(t, "eof2"))
    rc, out = run(setup_py(t3), [], stdin=f"{TOK_WEB}\n{TOK_TG}\n")
    check("mid-wizard EOF: no traceback", "Traceback" not in out, out[-500:])
    check("mid-wizard EOF: partial writes kept",
          own_line(t3 / "secrets.env", "WEB_TOKEN") == TOK_WEB)
    check("mid-wizard EOF: exit pending 2", rc == 2, f"rc={rc}")
    check("mid-wizard EOF: zero echo", no_leak(out, TOK_WEB, TOK_TG))


def test_wizard_endpoint_and_boot_yes(t: Path) -> None:
    t2 = make_repo(sub(t, "ep"))
    # endpoint: invalid then change; model: change; boot: y (stub install.sh)
    stdin = "\n".join([TOK_WEB, TOK_TG, TOK_ALLOW, "", TOK_GH, TOK_WD,
                       "ftp://bad-endpoint", TOK_ENDPOINT,
                       TOK_MODEL, "y"]) + "\n"
    rc, out = run(setup_py(t2), [], stdin=stdin, extra_env={"FAKE_RC": "0"})
    st = json.loads((t2 / "web" / "settings.json").read_text(encoding="utf-8"))
    check("invalid endpoint rejected with reason", "base_url: invalid" in out, out[:600])
    check("endpoint updated + JSON valid", st["mycore"]["base_url"] == TOK_ENDPOINT,
          str(st.get("mycore")))
    check("model updated", st["mycore"]["model"] == TOK_MODEL, str(st.get("mycore")))
    check("settings write reported read-back",
          "JSON re-parsed ok" in out, out[-400:])
    check("boot question 'y' ran install.sh",
          (t2 / "deploy" / "install.marker").is_file(), "installer not run")
    check("boot leg propagates installer exit", rc == 0, f"rc={rc}")
    check("wizard output still zero-echo",
          no_leak(out, TOK_WEB, TOK_TG, TOK_ALLOW, TOK_GH, TOK_WD))

    # --no-boot skips the final question entirely (8 lines, no boot line)
    t4 = make_repo(sub(t, "nb"))
    stdin = "\n".join([TOK_WEB, TOK_TG, TOK_ALLOW, "", TOK_GH, TOK_WD,
                       "", "", ]) + "\n"
    rc, _ = run(setup_py(t4), ["--no-boot"], stdin=stdin)
    check("--no-boot wizard: exit 0 without boot question",
          rc == 0 and not (t4 / "deploy" / "install.marker").exists(),
          f"rc={rc}")


def test_dispatch_flags(t: Path) -> None:
    t2 = make_repo(sub(t, "dsp"))
    rc, out = run(persist_py(t2), ["setup", "--check"])
    check("`aidog setup --check` passes through (exit 2 fresh)", rc == 2,
          f"rc={rc} {out[:200]}")
    check("`aidog setup --check` output is wizard's", "[setup]" in out, out[:200])
    check("`aidog setup --check` created nothing",
          not (t2 / "secrets.env").exists())
    rc, _ = run(persist_py(t2), ["setup", "--yes", "--no-boot"])
    check("`aidog setup --yes --no-boot` provisions + exits 2",
          rc == 2 and (t2 / "secrets.env").is_file(), f"rc={rc}")
    check("dispatcher --yes --no-boot never booted",
          not (t2 / "deploy" / "install.marker").exists())


def test_local_model_detected_adopt(t: Path) -> None:
    """Case A: live OpenAI-shaped endpoint via AIDOG_LOCAL_MODEL_PROBES ->
    wizard adopts it (zero-key), manual base_url prompt never appears."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — stdlib handler API
            if self.path == "/v1/models":
                body = json.dumps({"data": [{"id": "fake-local-1"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: object) -> None:  # keep suite output clean
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    try:
        t2 = make_repo(sub(t, "local_hit"))
        # slots (api key empty) + "use which?" answer (Enter = 1) + boot n
        stdin = "\n".join([TOK_WEB, TOK_TG, TOK_ALLOW, "", TOK_GH, TOK_WD,
                           "", "n"]) + "\n"
        t0 = time.monotonic()
        rc, out = run(setup_py(t2), [], stdin=stdin,
                      extra_env={"AIDOG_LOCAL_MODEL_PROBES": url})
        elapsed = time.monotonic() - t0
        st = json.loads((t2 / "web" / "settings.json").read_text(encoding="utf-8"))
        check("A: 'local model server detected' printed",
              "local model server detected" in out, out[:600])
        check("A: numbered entry shows kind + url + model",
              f"local {url} (model: fake-local-1)" in out, out[:600])
        check("A: adopted base_url written to settings",
              st["mycore"]["base_url"] == url, str(st.get("mycore")))
        check("A: model taken from endpoint",
              st["mycore"]["model"] == "fake-local-1", str(st.get("mycore")))
        check("A: other mycore keys preserved (template)",
              st["mycore"].get("context_dirs") == ""
              and st["mycore"].get("upstreams") == ""
              and st["mycore"].get("mcp") == "", str(st.get("mycore")))
        check("A: MODEL_API_KEY still empty (zero-key)",
              own_line(t2 / "secrets.env", "MODEL_API_KEY") is None)
        check("A: confirmation line (no API key needed)",
              "using local model server" in out and "no API key needed" in out,
              out[-600:])
        check("A: manual base_url prompt ABSENT",
              "model endpoint base_url" not in out, out[:600])
        check("A: manual model prompt ABSENT", "model name [" not in out,
              out[:600])
        check("A: wizard exit 0", rc == 0, f"rc={rc} {out[-300:]}")
        check("A: wizard run fast (<3s)", elapsed < 3.0, f"{elapsed:.2f}s")
        check("A: zero-echo of piped values",
              no_leak(out, TOK_WEB, TOK_TG, TOK_ALLOW, TOK_GH, TOK_WD))
    finally:
        srv.shutdown()
        srv.server_close()


def test_local_model_miss_default(t: Path) -> None:
    """Case B: default probes all closed -> one honest miss line, and the
    prompt sequence stays exactly the legacy manual flow."""
    t2 = make_repo(sub(t, "local_miss"))
    stdin = full_answers()
    t0 = time.monotonic()
    rc, out = run(setup_py(t2), [], stdin=stdin)  # no probe override (default)
    elapsed = time.monotonic() - t0
    st = json.loads((t2 / "web" / "settings.json").read_text(encoding="utf-8"))
    check("B: 'no local model server detected' printed",
          "no local model server detected" in out, out[:600])
    check("B: miss line names the three default probes",
          all(s in out for s in ("ollama :11434", "llama-server :8080",
                                 "lm-studio :1234")), out[:600])
    check("B: no selection prompt shown", "use which?" not in out, out[:600])
    check("B: prompt sequence unchanged (template endpoint kept on Enter)",
          st["mycore"]["base_url"] == "http://127.0.0.1:3460/v1"
          and st["mycore"]["model"] == "mimo-v2.6-flash",
          str(st.get("mycore")))
    check("B: wizard exit 0 (sequence intact)", rc == 0,
          f"rc={rc} {out[-300:]}")
    check("B: summary still reports settings line",
          "settings mycore.base_url:" in out, out[-400:])
    check("B: wizard run fast (<3s)", elapsed < 3.0, f"{elapsed:.2f}s")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="aidog_b3_", dir=str(TMP)) as td:
        base = Path(td)
        t = make_repo(base / "help")
        test_help_and_dispatch(t)

        t = make_repo(base / "check")
        test_check_readonly(t)

        test_yes_no_boot(base)
        test_yes_boot_delegation(base)
        test_wizard_full_fill(base)
        test_wizard_no_overwrite(base)
        test_wizard_validation_retries(base)
        test_wizard_eof_graceful(base)
        test_wizard_endpoint_and_boot_yes(base)
        test_dispatch_flags(base)

        test_local_model_detected_adopt(base)
        test_local_model_miss_default(base)

    print(f"\n{len(PASS)} pass · {len(FAIL)} fail")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
