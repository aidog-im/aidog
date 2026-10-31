#!/usr/bin/env python3
"""Car F §13.1 offline isolation gate — multi-profile seams (tests/profile_f.py).

Offline (zero network), stdlib only, zero real credentials; every write is
confined to a throwaway profiles root under /tmp. Exit 0 = all assertions pass.

Contracts locked here:
  A. lib.profile_paths seams (in-process; the three env keys are saved and
     restored around every mutation):
     - no AIDOG_PROFILE -> byte-identical legacy paths (web/settings.json,
       secrets.env, data/mycore_service, overlay/*, data/images);
     - AIDOG_PROFILE + AIDOG_PROFILES_DIR relocate every seam under
       <root>/<name>/, and AIDOG_MC_STATE still wins over everything;
     - an invalid name fails closed (ProfileError) — a typo must never fall
       through to another profile's brain (empty/unset = default, no raise);
     - profile_name() re-reads the env on EVERY call (no caching).
  B. real dual-profile subprocess cross-check (the core leg): two live
     ChatService processes (alice/bob) built in a self-contained child script
     (offline FakeTg transport + echo chat injected; no run.py import), each
     handling /learn memory, /cron add, /learn skill, /reflect and one plain
     turn, with a unique marker token written into its own profile settings:
     - every artifact (session jsonl, cron_jobs.json, evolution_state.json,
       learned memory, learned skill, service log) lands under <root>/<name>/;
     - alice's tree carries zero bob bytes and vice versa;
     - the default layer (data/mycore_service, overlay, web/settings.json,
       secrets.env) is byte+mtime UNCHANGED across the subprocess leg AND
       the whole default layer carries zero profile-marker bytes at the end
       of the suite — the anti-crosstalk wall.
     Known concurrency note: the default mycore service may be LIVE on this
     machine, so its own churn files (service.log / boot.log / pidfile /
     cron_jobs.json / sessions/) are excluded from the strict mtime compare —
     they are still scanned for marker bytes, and no probe-chat session may
     ever appear there. The strict window is the subprocess leg itself (the
     only writers under test); the marker scan runs again after all legs.
  C. isolation semantics:
     - build_system_stack reads the A profile's body and never B's;
     - skill_roots carries the profile skills layer before the shared
       overlay/skills, and PROPAGATES ProfileError (known behavior: it does
       not swallow a bad name — asserted, not patched);
     - `aidog profile create` seeds settings/secrets/body/state/profile.json,
       the seeded secrets template carries NO value of the repo's real secret
       keys, SOUL seed == overlay copy, rm removes the dir, duplicate and
       invalid create are refused (rc != 0);
     - run_service.sh --check: invalid AIDOG_PROFILE = 5, an existing profile
       with empty slots = 3/4 (fail-closed, never starts), unset = legacy
       slot semantics (ready rc=0 on this provisioned machine).
English output per repo i18n policy.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))  # `python3 tests/profile_f.py` from anywhere
TMP = Path("/tmp")  # hard boundary: no writes outside /tmp (except the
                    # gitignored profiles/<fixture> probe dir, removed again)

PROFILE_KEYS = ("AIDOG_PROFILE", "AIDOG_PROFILES_DIR", "AIDOG_MC_STATE")

# Live-service churn files under data/mycore_service (the default profile's
# service may run concurrently on this machine) — excluded from the strict
# mtime/hash compare, still scanned for marker bytes.
VOLATILE_STATE = {"service.log", "boot.log", "mycore_service.pid",
                  "cron_jobs.json"}

# Unique per-profile tokens: written into profile settings + every artifact
# the child produces, scanned for in the default layer (cross-contamination).
MK = {"alice": "FTMARK-ALICE-7f3a91", "bob": "FTMARK-BOB-51ce04"}
PROBE_CHAT = 990001  # child session chat id — must never hit default state

# Purely fabricated bot token for the child's HARD token gate (tg.py
# polling_token). Concatenated so no source line ever reads KEY=<value>;
# never printed, never used for network (transport is the injected fake).
FAKE_TG = "10000000" + ":" + "TESTFAKEtoken1234567890"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + (f"  ({detail})" if detail and not ok else ""))


@contextmanager
def profile_env(**values: str | None):
    """Set only the three profile env keys (None = unset); restore after."""
    saved = {k: os.environ.get(k) for k in PROFILE_KEYS}
    try:
        for k in PROFILE_KEYS:
            os.environ.pop(k, None)
        for k, v in values.items():
            if v is not None:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ------------------------------------------------------------ child script --
# Self-contained: fake transport + echo chat written inline (never imports
# mycore/tests/run.py). Piped via `python3 -` with cwd=repo root so the child
# can `import mycore`, while the script's own directory never enters sys.path.
CHILD = r'''
import json, os, sys
from pathlib import Path

REPO = Path(os.environ["FTEST_REPO"])
sys.path.insert(0, str(REPO))

from lib import profile_paths as pp


class FakeTg:
    def __init__(self):
        self.sends = []
        self.polls = 0

    def __call__(self, url, payload, timeout):
        method = url.rsplit("/", 1)[-1]
        if method == "sendMessage":
            import urllib.parse as up
            form = up.parse_qs(payload.decode("utf-8"))
            self.sends.append(form.get("chat_id", [""])[0])
            return json.dumps({"ok": True, "result": {"message_id": 1}})
        if method == "sendChatAction":
            return json.dumps({"ok": True, "result": True})
        if method == "getUpdates":
            self.polls += 1
            return json.dumps({"ok": True, "result": []})
        if method == "getMe":
            return json.dumps({"ok": True, "result": {"username": "ftest_bot"}})
        return json.dumps({"ok": True, "result": {}})


def mk_update(i, uid, cid, text):
    return {"update_id": i, "message": {
        "message_id": i,
        "from": {"id": uid, "is_bot": False, "first_name": "x"},
        "chat": {"id": cid, "type": "private"},
        "date": 0, "text": text}}


def fake_response(text):
    return {"choices": [{"message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}]}


class EchoChat:
    def chat(self, messages, tools=None, **kw):
        return fake_response("echo:" + str(messages[-1]["content"])[:20])


from mycore.service import ChatService

name = pp.profile_name()
state = pp.state_dir(REPO)
mk = os.environ["FTEST_MARKER"]
fake = FakeTg()
echo = EchoChat()
svc = ChatService(state, frozenset({1}), transport=fake,
                  chat=echo.chat, repo=REPO)
uid, cid = 1, 990001
svc.handle_update(mk_update(1, uid, cid,
                            "/learn memory - ftest learned fact " + mk))
svc.handle_update(mk_update(2, uid, cid,
                            "/cron add daily 10:00 :: ftest ping " + mk))
svc.handle_update(mk_update(3, uid, cid,
                            "/learn skill ftest-" + name + " :: ftest skill "
                            + mk + " :: print('hi')"))
svc.handle_update(mk_update(4, uid, cid, "/reflect daily"))
svc.handle_update(mk_update(5, uid, cid, "hello ftest " + mk))
print("FT_OK name=" + name + " state=" + str(state)
      + " sends=" + str(len(fake.sends)))
'''


# --------------------------------------------------------------- utilities --
def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def snap_static() -> dict[Path, tuple]:
    """overlay/** + web/settings.json + secrets.env -> (sha256, mtime_ns)."""
    out: dict[Path, tuple] = {}
    for p in sorted((REPO / "overlay").rglob("*")):
        if p.is_file():
            out[p] = (_sha(p), p.stat().st_mtime_ns)
    for rel in ("web/settings.json", "secrets.env"):
        p = REPO / rel
        out[p] = (_sha(p), p.stat().st_mtime_ns) if p.is_file() else (None, None)
    return out


def snap_state() -> dict[str, tuple]:
    """data/mycore_service/** -> rel path -> (sha256, mtime_ns)."""
    base = REPO / "data" / "mycore_service"
    out: dict[str, tuple] = {}
    if base.is_dir():
        for p in sorted(base.rglob("*")):
            if p.is_file():
                out[p.relative_to(base).as_posix()] = (
                    _sha(p), p.stat().st_mtime_ns)
    return out


def _volatile(rel: str) -> bool:
    return (rel.split("/", 1)[0] in VOLATILE_STATE
            or rel.startswith("sessions/"))


def run_child(name: str, root: Path) -> tuple[int, str]:
    env = dict(os.environ)
    env.pop("TELEGRAM_BOT_TOKEN", None)   # never compare against live gateway
    for k in PROFILE_KEYS:
        env.pop(k, None)
    env.update({
        "AIDOG_PROFILE": name,
        "AIDOG_PROFILES_DIR": str(root),
        "AIDOG_MC_STATE": str(root / name / "state"),
        "MYCORE_TG_TOKEN": FAKE_TG,
        "FTEST_REPO": str(REPO),
        "FTEST_MARKER": MK[name],
    })
    p = subprocess.run([sys.executable, "-"], input=CHILD, text=True,
                       capture_output=True, cwd=str(REPO), env=env,
                       timeout=300)
    return p.returncode, p.stdout + p.stderr


def persist(args: list[str], profiles_dir: Path) -> tuple[int, str]:
    env = dict(os.environ)
    for k in PROFILE_KEYS:
        env.pop(k, None)
    env["AIDOG_PROFILES_DIR"] = str(profiles_dir)
    p = subprocess.run(
        [sys.executable, str(REPO / "deploy" / "aidog_persist.py"), *args],
        capture_output=True, text=True, cwd=str(REPO), env=env, timeout=60)
    return p.returncode, p.stdout + p.stderr


def run_service_check(**setvals: str) -> tuple[int, str]:
    env = dict(os.environ)
    for k in PROFILE_KEYS:
        env.pop(k, None)
    # credentials derive from secrets.env only for this leg (operator env
    # must not mask a profile's empty slots)
    env.pop("AIDOG_TG_ALLOWED_USERS", None)
    env.pop("MYCORE_TG_TOKEN", None)
    for k, v in setvals.items():
        env[k] = v
    p = subprocess.run(["bash", str(REPO / "deploy" / "run_service.sh"),
                        "--check"],
                       capture_output=True, text=True, cwd=str(REPO),
                       env=env, timeout=60)
    return p.returncode, p.stdout + p.stderr


def own_line_values(path: Path) -> dict[str, str]:
    """KEY=value map of an own-line .env file (values stay in memory only —
    never printed, per credential discipline). Missing file -> {}."""
    try:
        txt = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for m in re.finditer(r"(?m)^([A-Za-z_][A-Za-z0-9_]*)=(\S*)$", txt):
        out[m.group(1)] = m.group(2)
    return out


def tree_has(root: Path, token: str) -> bool:
    needle = token.encode("utf-8")
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        try:
            if needle in p.read_bytes():
                return True
        except OSError:
            continue
    return False


# ------------------------------------------------------------- A: seams ----
def test_path_seams() -> None:
    from lib import profile_paths as pp

    def name_result(value: str, present: bool = True) -> tuple[str, str]:
        if present:
            os.environ["AIDOG_PROFILE"] = value
        else:
            os.environ.pop("AIDOG_PROFILE", None)
        try:
            return ("ok", pp.profile_name())
        except Exception as e:  # noqa: BLE001 — classify whatever came back
            return (type(e).__name__, "")

    with profile_env():
        check("A: default settings = web/settings.json",
              pp.settings_path(REPO) == REPO / "web" / "settings.json")
        check("A: default secrets = secrets.env",
              pp.secrets_path(REPO) == REPO / "secrets.env")
        check("A: default state = data/mycore_service",
              pp.state_dir(REPO) == REPO / "data" / "mycore_service")
        check("A: default media = data/images",
              pp.media_dir(REPO) == REPO / "data" / "images")
        check("A: default body = overlay (all derived paths)",
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

    base = Path(tempfile.mkdtemp(prefix="aidog_pf_a_", dir=str(TMP)))
    try:
        root = base / "profiles"
        with profile_env(AIDOG_PROFILE="alice",
                         AIDOG_PROFILES_DIR=str(root)):
            r = root / "alice"
            check("A: profile relocates settings/secrets/state/media",
                  pp.settings_path(REPO) == r / "settings.json"
                  and pp.secrets_path(REPO) == r / "secrets.env"
                  and pp.state_dir(REPO) == r / "state"
                  and pp.media_dir(REPO) == r / "images")
            check("A: profile relocates the body layer",
                  pp.body_root(REPO) == r / "body"
                  and pp.skills_dir(REPO) == r / "body" / "skills"
                  and pp.constitution_path(REPO)
                  == r / "body" / "constitution" / "SOUL.md"
                  and pp.evolution_dir(REPO) == r / "body" / "evolution")
            os.environ["AIDOG_MC_STATE"] = str(root / "explicit_state")
            check("A: AIDOG_MC_STATE wins over the profile",
                  pp.state_dir(REPO) == root / "explicit_state")

        # fail-closed: every malformed name raises — never a silent default
        bad_names = ["../x", "a/b", "UPPER", "-lead", "a" * 33,
                     "a b", "a.b"]
        with profile_env():
            raised = [n for n in bad_names
                      if name_result(n)[0] != "ProfileError"]
            check("A: invalid names all raise ProfileError (fail-closed)",
                  not raised, f"did not raise: {raised}")
            check("A: unset/empty name -> default profile, no raise",
                  name_result("", present=False) == ("ok", "")
                  and name_result("") == ("ok", ""))
            # known behavior: profile_name() strips surrounding whitespace —
            # blank stays the default, "alice " normalizes (never a fallback)
            check("A: whitespace forms normalize (blank = default, known)",
                  name_result(" ") == ("ok", "")
                  and name_result("alice ") == ("ok", "alice"))
            # no caching: every call re-reads the env
            seq = [name_result("alpha"), name_result("", present=False),
                   name_result("beta"), name_result("alpha")]
            check("A: profile_name() re-reads env on every call (no cache)",
                  seq == [("ok", "alpha"), ("ok", ""), ("ok", "beta"),
                          ("ok", "alpha")], str(seq))
    finally:
        import shutil
        shutil.rmtree(base, ignore_errors=True)


# ------------------------------------------------- B: dual-profile children --
def check_default_layer(snap0: dict, state0: dict) -> None:
    """Strict anti-crosstalk compare over the dual-profile subprocess window:
    overlay + web/settings.json + secrets.env byte+mtime identical, default
    state non-volatile files identical, and zero marker bytes anywhere."""
    snap1 = snap_static()
    state1 = snap_state()

    bad = []
    for p, v in snap0.items():
        if snap1.get(p) != v:
            bad.append(p.relative_to(REPO).as_posix())
    for p in snap1:
        if p not in snap0:
            bad.append("new:" + p.relative_to(REPO).as_posix())
    check("B: default layer overlay + settings + secrets byte+mtime "
          "unchanged across the subprocess leg", not bad, ", ".join(bad[:8]))

    sbad = []
    for rel, v in state0.items():
        if _volatile(rel):
            continue
        if state1.get(rel) != v:
            sbad.append("changed:" + rel)
    for rel in state1:
        if _volatile(rel):
            continue
        if rel not in state0:
            sbad.append("new:" + rel)
    check("B: default data/mycore_service non-volatile files unchanged "
          "across the subprocess leg", not sbad, ", ".join(sbad[:8]))

    check("B: zero profile-marker bytes in the default layer",
          not marker_hits(snap1, state1),
          ", ".join(marker_hits(snap1, state1)[:6]))

    landed = [r for r in (f"sessions/{PROBE_CHAT}.jsonl",) if r in state1]
    check("B: probe chat session never lands in default state",
          not landed, ", ".join(landed))


def marker_hits(snap: dict, state: dict) -> list[str]:
    marks = [MK["alice"].encode(), MK["bob"].encode()]
    hits: list[str] = []
    for p in snap:
        if not p.is_file():
            continue
        try:
            blob = p.read_bytes()
        except OSError:
            continue
        if any(m in blob for m in marks):
            hits.append(p.relative_to(REPO).as_posix())
    base = REPO / "data" / "mycore_service"
    for rel in state:
        try:
            blob = (base / rel).read_bytes()
        except OSError:
            continue
        if any(m in blob for m in marks):
            hits.append("data/mycore_service/" + rel)
    return hits


def test_dual_profiles(root: Path) -> None:
    snap0 = snap_static()
    state0 = snap_state()
    root.mkdir(parents=True, exist_ok=True)
    for name in ("alice", "bob"):
        rc, out = persist(["profile", "create", name], root)
        check(f"B: seed {name} via `aidog profile create`",
              rc == 0, f"rc={rc} {out[-200:]}")
        settings = root / name / "settings.json"
        try:
            doc = json.loads(settings.read_text(encoding="utf-8"))
            doc["_ftest_marker"] = MK[name]
            settings.write_text(json.dumps(doc), encoding="utf-8")
        except (OSError, ValueError) as e:
            check(f"B: {name} settings marker injected", False, str(e))

    for name in ("alice", "bob"):
        other = "bob" if name == "alice" else "alice"
        rc, out = run_child(name, root)
        check(f"B: {name} child process exit 0",
              rc == 0 and "FT_OK" in out, f"rc={rc} {out[-400:]}")
        check(f"B: {name} child never echoes the fake token",
              FAKE_TG not in out, "token leaked to output")
        st = root / name / "state"
        sess = st / "sessions" / f"{PROBE_CHAT}.jsonl"
        check(f"B: {name} session jsonl under profile state",
              sess.is_file() and MK[name].encode() in sess.read_bytes(),
              str(sess))
        cron_p = st / "cron_jobs.json"
        cron_ok = False
        try:
            jobs = json.loads(cron_p.read_text(encoding="utf-8")).get("jobs")
            cron_ok = (len(jobs) == 1
                       and MK[name] in str(jobs[0].get("text", "")))
        except (OSError, ValueError, AttributeError):
            pass
        check(f"B: {name} cron_jobs.json under profile state", cron_ok,
              str(cron_p))
        evo_ok = False
        try:
            json.loads((st / "evolution_state.json").read_text(
                encoding="utf-8"))
            evo_ok = True
        except (OSError, ValueError):
            pass
        check(f"B: {name} evolution_state.json under profile state", evo_ok,
              str(st / "evolution_state.json"))
        learned = root / name / "body" / "memory" / "EVOLUTION_learned.md"
        check(f"B: {name} learned memory in profile body",
              learned.is_file()
              and MK[name].encode() in learned.read_bytes(), str(learned))
        skill = root / name / "body" / "skills" / f"ftest-{name}" / "SKILL.md"
        check(f"B: {name} learned skill in profile body",
              skill.is_file() and MK[name].encode() in skill.read_bytes(),
              str(skill))
        settings = root / name / "settings.json"
        check(f"B: {name} profile settings still carries its marker",
              settings.is_file()
              and MK[name].encode() in settings.read_bytes(), str(settings))
        check(f"B: {name} tree has zero {other} bytes",
              not tree_has(root / name, MK[other]),
              f"{MK[other]} found under {name}/")

    check_default_layer(snap0, state0)


# --------------------------------------- C5/C6: stack + skill_roots seams ---
def test_stack_and_roots(root: Path) -> None:
    from mycore.assembly import build_system_stack

    marks = {"alpha": ("SOUL-ALPHA-3e81c2", "MEM-ALPHA-6b4d07",
                       "LEARNED-ALPHA-9fa350"),
             "beta": ("SOUL-BETA-77c1de", "MEM-BETA-02e8af",
                      "LEARNED-BETA-45b906")}
    for name, (soul, mem, learned) in marks.items():
        cons = root / name / "body" / "constitution" / "SOUL.md"
        cons.parent.mkdir(parents=True, exist_ok=True)
        cons.write_text(soul, encoding="utf-8")
        memd = root / name / "body" / "memory"
        memd.mkdir(parents=True, exist_ok=True)
        (memd / "MEMORY_curated.md").write_text(mem, encoding="utf-8")
        (memd / "USER_curated.md").write_text("USER slot " + name,
                                              encoding="utf-8")
        (memd / "EVOLUTION_learned.md").write_text(learned,
                                                   encoding="utf-8")
        (root / name / "body" / "skills").mkdir(parents=True, exist_ok=True)

    for name in ("alpha", "beta"):
        other = "beta" if name == "alpha" else "alpha"
        with profile_env(AIDOG_PROFILE=name,
                         AIDOG_PROFILES_DIR=str(root)):
            stack = build_system_stack()
            joined = "\n".join(m.get("content") or "" for m in stack)
            check(f"C5: {name} stack carries its SOUL, never {other}'s",
                  marks[name][0] in joined and marks[other][0] not in joined)
            check(f"C5: {name} stack carries its memory, never {other}'s",
                  marks[name][1] in joined and marks[other][1] not in joined)
            check(f"C5: {name} stack carries its learned slot, never "
                  f"{other}'s",
                  marks[name][2] in joined and marks[other][2] not in joined)

    sys.path.insert(0, str(REPO / "lib"))
    try:
        import skill_route as sr
    finally:
        sys.path.remove(str(REPO / "lib"))
    try:
        with profile_env(AIDOG_PROFILE="alpha",
                         AIDOG_PROFILES_DIR=str(root)):
            pskills = root / "alpha" / "body" / "skills"
            roots = sr.skill_roots(None)
            check("C6: skill_roots = profile skills + shared overlay "
                  "(profile first)",
                  pskills in roots and sr.DEFAULT_SKILLS_DIR in roots
                  and roots.index(pskills) < roots.index(sr.DEFAULT_SKILLS_DIR)
                  and sr.DEFAULT_SKILLS_DIR == REPO / "overlay" / "skills",
                  str(roots)[:300])
            # known behavior (asserted, not patched): skill_roots does NOT
            # swallow ProfileError — a bad name fails the call, loudly.
            os.environ["AIDOG_PROFILE"] = "../x"
            got = ""
            try:
                sr.skill_roots(None)
            except Exception as e:  # noqa: BLE001 — classify the escape
                got = type(e).__name__
            check("C6: skill_roots propagates ProfileError (fail-closed)",
                  got == "ProfileError", f"raised={got or 'nothing'}")
    finally:
        pass


# ------------------------------------------------ C7: `aidog profile` CLI ---
def test_profile_cli(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    rc, out = persist(["profile", "create", "ftest"], root)
    check("C7: profile create exits 0", rc == 0, f"rc={rc} {out[-200:]}")
    p = root / "ftest"
    seeded = [p / "settings.json", p / "secrets.env", p / "profile.json",
              p / "body" / "constitution" / "SOUL.md",
              p / "body" / "memory" / "MEMORY_curated.md",
              p / "body" / "memory" / "USER_curated.md",
              p / "state", p / "body" / "skills", p / "body" / "evolution",
              p / "images"]
    missing = [str(q.relative_to(p)) for q in seeded if not q.exists()]
    check("C7: create seeds settings/secrets/body/state/profile.json",
          not missing, f"missing: {missing}")

    meta_ok = False
    try:
        meta = json.loads((p / "profile.json").read_text(encoding="utf-8"))
        meta_ok = (meta.get("name") == "ftest"
                   and isinstance(meta.get("web_port"), int))
    except (OSError, ValueError):
        pass
    check("C7: profile.json records name + web_port", meta_ok)

    repo_settings = REPO / "web" / "settings.json"
    if repo_settings.is_file():
        check("C7: settings seeded byte-identical from web/settings.json",
              (p / "settings.json").read_bytes() == repo_settings.read_bytes())
    else:
        check("C7: settings seed comparison (no repo settings — N/A)", True)
    repo_soul = REPO / "overlay" / "constitution" / "SOUL.md"
    if repo_soul.is_file():
        check("C7: SOUL seed byte-identical to the overlay copy",
              (p / "body" / "constitution" / "SOUL.md").read_bytes()
              == repo_soul.read_bytes())
    else:
        check("C7: SOUL seed comparison (no overlay SOUL — N/A)", True)

    slots = own_line_values(p / "secrets.env")
    expected = {"WEB_TOKEN", "MYCORE_TG_TOKEN", "AIDOG_TG_ALLOWED_USERS"}
    check("C7: profile secrets = empty own-line template only",
          set(slots) == expected and not any(slots.values()),
          str(sorted(slots)))
    repo_slots = own_line_values(REPO / "secrets.env")
    filled = {k: v for k, v in repo_slots.items() if v}
    txt = (p / "secrets.env").read_text(encoding="utf-8")
    leaked = [k for k, v in filled.items()
              if len(v) >= 4 and v in txt]
    check("C7: profile secrets carry NONE of the repo's real slot values",
          not leaked, f"leaked keys: {leaked}")
    check("C7: create output never echoes repo secret values",
          not any(v in out for v in filled.values() if len(v) >= 4),
          "a repo secret value appeared in CLI output")

    rc2, out2 = persist(["profile", "create", "ftest"], root)
    check("C7: duplicate create refused (rc != 0)", rc2 != 0,
          f"rc={rc2} {out2[-160:]}")
    rc3, out3 = persist(["profile", "create", "UPPER"], root)
    check("C7: invalid name create refused (rc != 0)", rc3 != 0,
          f"rc={rc3} {out3[-160:]}")
    rc4, out4 = persist(["profile", "list"], root)
    check("C7: profile list shows ftest",
          rc4 == 0 and "ftest" in out4, f"rc={rc4} {out4[-200:]}")
    rc5, out5 = persist(["profile", "rm", "ftest"], root)
    check("C7: profile rm removes the dir",
          rc5 == 0 and not p.exists(), f"rc={rc5} {out5[-160:]}")


# ---------------------------------------------- C8: run_service.sh semantics --
def test_run_service() -> None:
    rc, out = run_service_check(AIDOG_PROFILE="../x")
    check("C8: invalid AIDOG_PROFILE -> rc=5 (fail-closed)", rc == 5,
          f"rc={rc} {out[-200:]}")

    fixture = REPO / "profiles" / f"ftrun{os.getpid()}"
    pre_existing = fixture.exists()
    check("C8: repo profile fixture is fresh", not pre_existing,
          str(fixture))
    if not pre_existing:
        fixture.mkdir(parents=True, exist_ok=True)
        (fixture / "secrets.env").write_text(
            "WEB_TOKEN=\nMYCORE_TG_TOKEN=\nAIDOG_TG_ALLOWED_USERS=\n",
            encoding="utf-8")
    try:
        rc2, out2 = run_service_check(AIDOG_PROFILE=fixture.name)
        check("C8: existing profile + empty slots -> rc 3/4 (never starts)",
              rc2 in (3, 4), f"rc={rc2} {out2[-200:]}")
        check("C8: --check never materializes the profile state dir",
              not (fixture / "state").exists(), str(fixture))
    finally:
        import shutil
        shutil.rmtree(fixture, ignore_errors=True)

    slots = own_line_values(REPO / "secrets.env")
    ready = bool(slots.get("AIDOG_TG_ALLOWED_USERS")) \
        and bool(slots.get("MYCORE_TG_TOKEN"))
    rc3, out3 = run_service_check()
    if ready:
        check("C8: default profile --check ready (rc=0)", rc3 == 0,
              f"rc={rc3} {out3[-200:]}")
    else:
        check("C8: default profile --check pending (rc 3/4, env not "
              "provisioned)", rc3 in (3, 4), f"rc={rc3}")


# --------------------------------------------- default-layer end-of-suite scan
def check_default_layer_scan() -> None:
    """Whole-suite honesty scan: no profile marker may exist anywhere in the
    default layer after ALL legs ran, and the probe chat session never
    landed in the default state (cheap, non-flaky — no mtime compare)."""
    snap = snap_static()
    state = snap_state()
    hits = marker_hits(snap, state)
    check("layer: zero profile-marker bytes anywhere in the default layer "
          "after all legs", not hits, ", ".join(hits[:6]))
    landed = [r for r in (f"sessions/{PROBE_CHAT}.jsonl",) if r in state]
    check("layer: probe chat session absent from default state (suite end)",
          not landed, ", ".join(landed))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="aidog_profile_f_",
                                     dir=str(TMP)) as td:
        base = Path(td)
        test_path_seams()                          # A
        test_dual_profiles(base / "bprofiles")     # B (subprocess cross-check)
        test_stack_and_roots(base / "cprofiles")   # C5 + C6
        test_profile_cli(base / "cliprofiles")     # C7
        test_run_service()                         # C8
    check_default_layer_scan()

    print(f"\n{len(PASS)} pass · {len(FAIL)} fail")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
