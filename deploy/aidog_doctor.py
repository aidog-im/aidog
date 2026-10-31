#!/usr/bin/env python3
"""aidog doctor — deterministic health check for the embodied system (I'm AiDog) transition stack.

Product ancestor of the future `aidog doctor` CLI. Every check encodes a P1 lesson
(env glue, overlay-only discipline, memory budgets, gateway/watchdog liveness).
English output per repo i18n policy; credentials are never printed (length/presence only).

Phase-aware counting (P5-3) — `deploy/upstream_shim.env` is the phase resolver:
  * shim present (today): the transition-stack gate runs in full — **23 active
    checks, 0 SKIP** expected (dirty repo may WARN; nothing else).
  * shim absent (post-swap, P5-5): the 20 shim/profile-bound checks are emitted
    as `SKIP (post-swap)` entries — counted separately in the summary line,
    NEVER FAIL — and 4 mycore-runtime checks activate instead:
    mycore.service.pid, mycore.watchdog.state, mycore.secrets.slots,
    mycore.web.console. Post-swap expected = **9 active + 20 SKIP** (29 entries),
    target 0 FAIL / 0 WARN (repo dirt is the only tolerated WARN).

Exit code: 0 = all pass (warnings + skips allowed), 1 = at least one FAIL.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent  # self-deriving: no machine path
SHIM_PATH = REPO / "deploy/upstream_shim.env"
SHIM_PRESENT = SHIM_PATH.exists()             # single P5-3 phase resolver
SHIM: dict = {}
try:
    for _line in SHIM_PATH.read_text().splitlines():
        _m = re.match(r'^\s*export\s+([A-Z0-9_]+)="([^"]*)"\s*$', _line)
        if _m:
            SHIM[_m.group(1)] = _m.group(2)
except OSError:
    pass
UPSTREAM_HOME = Path(SHIM.get("AIDOG_KERNEL_HOME") or "/nonexistent")
PROFILE = Path(SHIM.get("AIDOG_PROFILE_DIR") or "/nonexistent")
UPSTREAM = Path(SHIM.get("AIDOG_UPSTREAM_TREE") or "/nonexistent")
BRAND = SHIM.get("AIDOG_UPSTREAM_BRAND") or ""
SECRETS = REPO / "secrets.env"
RESULTS: list[tuple[str, str, str]] = []  # (level, name, detail)

# mycore runtime paths (repo-relative; read-only, never restarted from here)
MYCORE_STATE_DIR = REPO / "data" / "mycore_service"
MYCORE_PIDFILE = MYCORE_STATE_DIR / "mycore_service.pid"
MYCORE_SERVICE_LOG = MYCORE_STATE_DIR / "service.log"
MYCORE_WD_STATE = REPO / "deploy" / "watchdog.mycore.local.state.json"
MYCORE_WD_FRESH_SEC = 300                   # state file must be younger than 5 min

# Checks whose subject dies with the transition wiring (P5-5). While the shim
# exists they run normally; afterwards each pinned name is emitted as a
# `SKIP (post-swap)` entry (never FAIL). Names are static so the post-swap
# doctor count is stable and documentable: 20 SKIP.
SHIM_SKIP: dict[str, list[tuple[str, str]]] = {
    "check_upstream_clean": [
        ("upstream.tree", "transition install tree"),
        ("upstream.overlay_only", "transition install tree"),
    ],
    "check_profile_env": [
        ("profile.env", "profile .env"),
        ("profile.env.tg_token", "profile .env"),
        ("profile.env.allowlist", "profile .env"),
    ],
    "check_config": [
        ("config.model.provider", "profile config"),
        ("config.provider.models_static", "profile config"),
        ("config.model.base_url", "profile config"),
        ("config.fallback", "profile config"),
        ("config.telemetry", "profile config"),
        ("config.standalone", "profile config"),
        ("config.skills.external_dirs", "profile config"),
    ],
    "check_soul_sync": [
        ("soul.sync", "profile SOUL copy (assembly reads the overlay directly)"),
    ],
    "check_memory": [
        ("memory.curated", "profile memories (overlay is the post-swap source)"),
        ("memory.user", "profile memories (overlay is the post-swap source)"),
        ("memory.archive", "profile memories (overlay is the post-swap source)"),
    ],
    "check_processes": [
        ("gateway.process", "transition gateway process"),
        ("watchdog.process", "transition watchdog session"),
        ("watchdog.state", "transition watchdog state file"),
    ],
    "check_version": [
        ("kernel.version", "transition kernel binary"),
    ],
}


def add(level: str, name: str, detail: str) -> None:
    RESULTS.append((level, name, detail))


def sh(cmd: list[str], timeout: int = 10) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (out.stdout or out.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return f"__ERROR__ {type(e).__name__}"


def env_line_value(path: Path, key: str) -> str | None:
    """Return value if KEY sits on its OWN line (the P1 glue-pit detector)."""
    try:
        m = re.search(rf"(?m)^{re.escape(key)}=(\S+)$", path.read_text())
        return m.group(1) if m else None
    except Exception:  # noqa: BLE001
        return None


def check_repo() -> None:
    if (REPO / ".git").is_dir():
        dirty = sh(["git", "-C", str(REPO), "status", "--porcelain"])
        add("PASS" if not dirty else "WARN", "repo.git", "clean" if not dirty else f"dirty: {len(dirty.splitlines())} entries")
    else:
        add("FAIL", "repo.git", "not a git work tree")


def check_upstream_clean() -> None:
    """Iron rule: never patch upstream source (upstream update would reset it)."""
    if not UPSTREAM.is_dir():
        add("WARN", "upstream.tree", "not installed (expected at P5 removal)")
        return
    out = sh(["git", "-C", str(UPSTREAM), "status", "--porcelain", "-uno"], timeout=15)
    if out == "__ERROR__" or not (UPSTREAM / ".git").exists():
        add("WARN", "upstream.tree", "not a git checkout; cannot verify pristine state")
    else:
        add("PASS" if not out else "FAIL", "upstream.overlay_only",
            "pristine" if not out else f"MODIFIED: {len(out.splitlines())} files (violates overlay-only!)")


def fs_type(path: Path) -> str:
    """Longest-mountpoint filesystem type covering path (from /proc/mounts)."""
    best, best_type = "", ""
    try:
        for line in Path("/proc/mounts").read_text().splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            mp = parts[1].replace("\\040", " ")
            if str(path).startswith(mp.rstrip("/") + "/") or str(path) == mp:
                if len(mp) >= len(best):
                    best, best_type = mp, parts[2]
    except OSError:
        pass
    return best_type


def check_secrets() -> None:
    if not SECRETS.exists():
        add("WARN", "secrets.env", "absent (template only) — fill before P2 web console")
        return
    mode = stat.S_IMODE(SECRETS.stat().st_mode)
    filled = sum(1 for line in SECRETS.read_text().splitlines()
                 if line.strip() and not line.strip().startswith("#")
                 and "=" in line and line.split("=", 1)[1].strip())
    fs = fs_type(SECRETS)
    if fs in {"9p", "drvfs", "virtiofs"} and (mode & 0o077):
        # WSL drive mounts expose no POSIX mode enforcement (chmod is a no-op);
        # on this single-user host access is governed by the Windows ACL side.
        add("PASS", "secrets.env.mode",
            f"{oct(mode)} shown — {fs} mount: POSIX mode not enforceable (Windows ACL governs)")
    else:
        ok_mode = (mode & 0o077) == 0
        add("PASS" if ok_mode else "FAIL", "secrets.env.mode", oct(mode) + (" (0600-ish)" if ok_mode else " TOO OPEN"))
    add("PASS" if filled else "WARN", "secrets.env.values", f"{filled} credential slots filled (values not shown)")


def check_profile_env() -> None:
    envf = PROFILE / ".env"
    if not envf.exists():
        add("FAIL", "profile.env", "missing")
        return
    tok = env_line_value(envf, "TELEGRAM_BOT_TOKEN")
    add("PASS" if tok and len(tok) >= 40 else "FAIL", "profile.env.tg_token",
        f"own-line, length {len(tok)}" if tok else "MISSING or glued mid-line (P1 pit)")
    uid = env_line_value(envf, "TELEGRAM_ALLOWED_USERS")
    add("PASS" if uid else "WARN", "profile.env.allowlist", f"own-line, {len(uid)} chars" if uid else "not set")


def check_config() -> None:
    cfg = PROFILE / "config.yaml"
    if not cfg.exists():
        add("FAIL", "profile.config", "missing"); return
    text = cfg.read_text()

    def has(pattern: str) -> bool:
        return re.search(pattern, text, re.M) is not None

    # Provider may be the builtin "custom" or a named providers.<name> entry
    # (task 3: named entry gives /model its static configured list).
    mprov = re.search(r'(?m)^  provider: "?([A-Za-z0-9._-]+)"?$', text)
    add("PASS" if mprov else "FAIL", "config.model.provider",
        mprov.group(1) if mprov else "missing/unexpected")
    # Named provider entry must declare its static list (discovery off).
    if mprov and mprov.group(1) != "custom":
        np = has(r'(?m)^    discover_models: false$') and has(r'(?m)^    models:$')
        add("PASS" if np else "FAIL", "config.provider.models_static",
            "discover_models=false + models list" if np else "static list missing")
    # Owner may switch model groups via config (2026-10-18: -> 3460/mimo) —
    # doctor follows whatever localhost group the profile config points at.
    mu = re.search(r"(?m)^  base_url: (http://localhost:\d+/v1)$", text)
    add("PASS" if mu else "FAIL", "config.model.base_url", mu.group(1) if mu else "unexpected")
    mf = has(r"(?m)^    model: gpt-6\.1-sol$")
    add("PASS" if mf else "WARN", "config.fallback", "entry uses model: field" if mf else "fallback entry not found")
    mt = has(r'(?ms)^telemetry:\n  shared_metrics:\n    enabled: false')
    add("PASS" if mt else "WARN", "config.telemetry", "disabled" if mt else "CHECK telemetry (must be off)")
    add("PASS" if has(r"(?m)^  standalone: true$") else "WARN", "config.standalone", "true (expected transition shim)" if has(r"(?m)^  standalone: true$") else "false/absent (multiplex mode?)")

    dirs: list[str] = []
    in_ext = False
    for ln in text.splitlines():
        s = ln.strip()
        if not in_ext:
            if s == "external_dirs:":
                in_ext = True
            continue
        if s.startswith("- "):
            dirs.append(s[2:].strip())
        elif s and not s.startswith("#"):
            break
    missing = [d for d in dirs if not Path(d).is_dir()]
    add("PASS" if dirs and not missing else "FAIL", "config.skills.external_dirs",
        f"{len(dirs)} dirs, all exist" if dirs and not missing else f"missing: {missing or 'none configured'}")


def check_soul_sync() -> None:
    src, dst = REPO / "overlay/constitution/SOUL.md", PROFILE / "SOUL.md"
    if not dst.exists():
        add("FAIL", "soul.loaded", "profile SOUL.md missing"); return
    add("PASS" if src.read_text() == dst.read_text() else "WARN", "soul.sync",
        "identical to repo overlay" if src.read_text() == dst.read_text() else "DIFFERS from repo (re-run cp)")


def check_memory() -> None:
    mem, usr = PROFILE / "memories/MEMORY.md", PROFILE / "memories/USER.md"
    for path, limit, name in ((mem, 2200, "memory.curated"), (usr, 1375, "memory.user")):
        if not path.exists():
            add("FAIL", name, "missing"); continue
        n = len(path.read_text())
        add("PASS" if n <= limit else "FAIL", name, f"{n}/{limit} chars")
    arch = PROFILE / "memories/ARCHIVE/MEMORY_full.md"
    add("PASS" if arch.exists() and arch.stat().st_size > 1000 else "FAIL", "memory.archive",
        f"{arch.stat().st_size // 1024}KB" if arch.exists() else "missing (run deploy/sync_memory.sh)")


def check_processes() -> None:
    # Exclude the tmux server pid: its argv quotes our start command and would
    # false-match the pattern (same self-match guard as boot_services/watchdog).
    srv = sh(["tmux", "display-message", "-p", "#{pid}"], timeout=5)

    def alive(pattern: str) -> bool:
        if not pattern:
            return False
        return any(p != srv and p.isdigit()
                   for p in sh(["pgrep", "-f", pattern]).split())

    gw = alive(SHIM.get("AIDOG_KERNEL_CMD") or "")
    wd = alive("deploy/watchdog.sh")
    add("PASS" if gw else "FAIL", "gateway.process", "running" if gw else "DOWN")
    add("PASS" if wd else "FAIL", "watchdog.process", "running" if wd else "DOWN")
    state = PROFILE / "logs/watchdog_state"
    if state.exists():
        s = state.read_text().strip()
        add("PASS" if s == "up" else "WARN", "watchdog.state", s)


def check_endpoints() -> None:
    # Model gateway (loopback): status only. Probe the endpoint the runtime
    # config actually uses (groups are user-switchable; do not hardcode):
    # profile config while the shim exists, product settings afterwards.
    base = "http://localhost:3456/v1"
    if SHIM_PRESENT:
        try:
            mc = re.search(r"(?m)^  base_url: (http://localhost:\d+/v1)$",
                           (PROFILE / "config.yaml").read_text())
            if mc:
                base = mc.group(1)
        except OSError:
            pass
    else:
        base = settings_mycbase()
    hostport = base.rsplit("//", 1)[-1].removesuffix("/v1")
    try:
        with urllib.request.urlopen(f"{base}/models", timeout=6) as r:
            add("PASS" if r.status == 200 else "WARN", "model.endpoint", f"{hostport} /v1/models -> {r.status}")
    except Exception as e:  # noqa: BLE001
        add("FAIL", "model.endpoint", f"{hostport} unreachable: {type(e).__name__}")
    # Telegram identity (outbound; token never printed). Post-swap the poller
    # is the mycore service, so the dedicated own-line slot applies.
    tok = env_line_value(PROFILE / ".env", "TELEGRAM_BOT_TOKEN") or env_line_value(UPSTREAM_HOME / ".env", "TELEGRAM_BOT_TOKEN")
    if not tok and not SHIM_PRESENT:
        tok = env_line_value(SECRETS, "MYCORE_TG_TOKEN")
    if tok:
        last: Exception | None = None
        for attempt in (0, 1):  # outbound probes flake (observed P3): retry once
            try:
                with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/getMe", timeout=15) as r:
                    name = __import__("json").load(r)["result"]["username"]
                add("PASS", "telegram.api", f"getMe OK -> @{name}")
                last = None
                break
            except Exception as e:  # noqa: BLE001
                last = e
                if attempt == 0:
                    time.sleep(0.6)
        if last is not None:
            add("FAIL", "telegram.api", f"getMe failed: {type(last).__name__}")
    elif SHIM_PRESENT:
        add("FAIL", "telegram.api", "no token on own line")
    else:
        # Unprovisioned post-swap = deliberately fail-closed, not a red alert.
        add("WARN", "telegram.api", "pending (fail-closed): dedicated token slot not provisioned")


def settings_mycbase() -> str:
    """Post-swap model endpoint: web/settings.json mycore.base_url, else the
    console's own default (same value web/server.py merges in)."""
    try:
        doc = json.loads((REPO / "web/settings.json").read_text())
        mc = doc.get("mycore") if isinstance(doc, dict) else None
        url = mc.get("base_url") if isinstance(mc, dict) else None
    except (OSError, ValueError):
        url = None
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return url.rstrip("/")
    return "http://127.0.0.1:3460/v1"


def check_version() -> None:
    cmd = shlex.split(SHIM.get("AIDOG_KERNEL_VERSION_CMD") or "true")
    v = sh(cmd, timeout=30)
    first = v.splitlines()[0] if v else "unknown"
    if BRAND:  # display de-branding: binary output carries the upstream name
        first = re.sub(rf"(?i)\b{re.escape(BRAND)}\b", "kernel", first)
    add("PASS" if "stable" in v else "WARN", "kernel.version", first)


# ------------------------------------------- mycore-runtime checks (post-swap) --
# Activated ONLY when the shim is absent (see SHIM_SKIP / module docstring).
# Read-only: pid liveness via /proc, freshness via mtime, counts — nothing is
# ever restarted from here and no secret value ever reaches the output.


def _pid_live(pid: int | None, marker: str) -> bool:
    if not pid or pid <= 1:
        return False
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
    except OSError:
        return False
    return marker in cmd


def check_mycore_service() -> None:
    """Chat-service pidfile/health: running / provisioned-but-down / pending."""
    try:
        pid = int(MYCORE_PIDFILE.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        pid = None
    live = _pid_live(pid, "mycore.service")
    provisioned = bool(env_line_value(SECRETS, "MYCORE_TG_TOKEN")) and bool(
        env_line_value(PROFILE / ".env", "TELEGRAM_ALLOWED_USERS")
        or os.environ.get("AIDOG_TG_ALLOWED_USERS", "").strip())
    if live:
        add("PASS", "mycore.service.pid", f"running (pid {pid})")
    elif provisioned:
        add("FAIL", "mycore.service.pid", "provisioned but not running")
    else:
        add("WARN", "mycore.service.pid", "pending (fail-closed: allowlist/token not provisioned)")


def check_mycore_watchdog() -> None:
    """Supervision: process up AND state file fresh (< 5 min)."""
    proc = bool(sh(["pgrep", "-f", r"python3 -m mycore\.watchdog"]).split())
    if not MYCORE_WD_STATE.exists():
        add("FAIL", "mycore.watchdog.state",
            "process down; state file missing" if not proc else "state file missing")
        return
    try:
        age = int(time.time() - MYCORE_WD_STATE.stat().st_mtime)
        doc = json.loads(MYCORE_WD_STATE.read_text())
        tg = doc.get("targets") if isinstance(doc, dict) else None
        states = [f"{k}={(v or {}).get('state', '?')}" for k, v in tg.items()] \
            if isinstance(tg, dict) else []
    except (OSError, ValueError, TypeError, AttributeError):
        age, states = -1, []
    detail = f"state age {age}s" + ("; " + " ".join(states) if states else "")
    if not proc:
        add("FAIL", "mycore.watchdog.state", f"process down; {detail}")
    elif age < 0:
        add("FAIL", "mycore.watchdog.state", "state file unreadable")
    elif age > MYCORE_WD_FRESH_SEC:
        add("WARN", "mycore.watchdog.state", f"STALE ({age}s > {MYCORE_WD_FRESH_SEC}s); {detail}")
    else:
        add("PASS", "mycore.watchdog.state", detail)


def check_mycore_secrets() -> None:
    """Required secrets.env slot count for the post-swap product (presence and
    length only — key names are printed, never values)."""
    required = ("WEB_TOKEN", "MYCORE_TG_TOKEN")
    present = {k: bool(env_line_value(SECRETS, k)) for k in required}
    have = sum(1 for ok in present.values() if ok)
    if have == len(required):
        add("PASS", "mycore.secrets.slots",
            f"{have}/{len(required)} required slots filled (values not shown)")
    else:
        absent = ",".join(k for k, ok in present.items() if not ok)
        add("WARN", "mycore.secrets.slots",
            f"{have}/{len(required)} filled; absent: {absent} (fail-closed pending)")


def check_mycore_web() -> None:
    """Console liveness (loopback GET only)."""
    port = int(os.environ.get("AIDOG_WEB_PORT", "8790"))
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
            doc = json.load(r)
        ok = r.status == 200 and isinstance(doc, dict) and doc.get("status") == "ok"
        add("PASS" if ok else "FAIL", "mycore.web.console",
            f":{port}/api/health -> {'ok' if ok else 'unexpected body'}")
    except Exception as e:  # noqa: BLE001
        add("FAIL", "mycore.web.console", f":{port} unreachable: {type(e).__name__}")


SHIM_BOUND_FNS = {check_upstream_clean, check_profile_env, check_config,
                  check_soul_sync, check_memory, check_processes, check_version}
MYCORE_FNS = (check_mycore_service, check_mycore_watchdog,
              check_mycore_secrets, check_mycore_web)


def _run(fn) -> None:
    try:
        print(f".. {fn.__name__}", file=sys.stderr, flush=True)
        fn()
    except Exception as e:  # noqa: BLE001
        add("FAIL", fn.__name__, f"checker crashed: {type(e).__name__}: {e}")


def main() -> int:
    plan = (check_repo, check_upstream_clean, check_secrets, check_profile_env,
            check_config, check_soul_sync, check_memory, check_processes,
            check_endpoints, check_version)
    for fn in plan:
        if fn in SHIM_BOUND_FNS:
            if SHIM_PRESENT:
                _run(fn)
            else:
                # Phase-aware: the subject died with the transition wiring —
                # honest SKIP, counted separately, never a FAIL.
                for name, why in SHIM_SKIP[fn.__name__]:
                    add("SKIP", name, f"SKIP (post-swap): {why} retired with the wiring")
            continue
        _run(fn)
    if not SHIM_PRESENT:
        for fn in MYCORE_FNS:
            _run(fn)

    icons = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌", "SKIP": "⏭️ "}
    width = max(len(n) for _, n, _ in RESULTS)
    for level, name, detail in RESULTS:
        print(f"{icons[level]} {name:<{width}}  {detail}")
    fails = sum(1 for l, _, _ in RESULTS if l == "FAIL")
    warns = sum(1 for l, _, _ in RESULTS if l == "WARN")
    skips = sum(1 for l, _, _ in RESULTS if l == "SKIP")
    line = f"{len(RESULTS) - skips} checks · {fails} FAIL · {warns} WARN"
    if skips:
        line += f" · {skips} SKIP (post-swap)"
    print(f"\n{line}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
