"""mycore.watchdog — process/endpoint supervision for the mycore runtime (P4-1c).

Design (owner authorized process switches, 2026-10-18):
  - one check cycle over configured targets (process pattern / http endpoint)
  - state machine per target: up -> down (restart attempt) -> up (recovered)
  - persistent-down retry on a throttle window, NEVER silent (lesson from the
    2026-10-17 boot blind spot: a failed bring-up must keep alerting)
  - notifications via TgClient.send_message ONLY (stateless — no getUpdates
    conflict with the live gateway); chat id comes from env, token never echoed
  - **dry_run default ON**: shadow mode evaluates and logs every decision but
    executes no restart — flip explicitly (`--execute` / config dry_run=false)
    only when the operator intends real process control.

Config file (JSON): {"dry_run": bool, "interval_sec": int, "throttle_sec": int,
  "targets": [{"name", "kind": "process"|"http", "value",
               "enabled": bool (optional, default true — false = staged until cutover),
               "restart": ["shell", "argv"...] (optional)}]}
State: JSON file next to config (or --state), gitignored runtime only.
English comments per repo i18n policy.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .tg import TgClient, TgError

DEFAULT_INTERVAL = 60
DEFAULT_THROTTLE = 300
RESTART_TIMEOUT = 60


class WatchConfigError(ValueError):
    pass


@dataclass
class TargetState:
    state: str = "unknown"            # up | down | unknown
    since: float = 0.0
    last_restart: float = 0.0
    restart_attempts: int = 0
    last_alert: float = 0.0


@dataclass
class WatchConfig:
    dry_run: bool = True
    interval_sec: int = DEFAULT_INTERVAL
    throttle_sec: int = DEFAULT_THROTTLE
    targets: list[dict] = field(default_factory=list)
    chat_id: str = ""                 # notify target ("" = log-only)
    heartbeat_bj: str = "09:00"        # daily Beijing-time heartbeat HH:MM

    @classmethod
    def load(cls, path: str | Path, chat_id: str = "") -> "WatchConfig":
        try:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise WatchConfigError(f"cannot read config: {type(e).__name__}") from None
        if not isinstance(doc, dict):
            raise WatchConfigError("config must be a JSON object")
        targets = doc.get("targets")
        if not isinstance(targets, list) or not targets:
            raise WatchConfigError("config needs a non-empty targets list")
        for t in targets:
            if not isinstance(t, dict) or not t.get("name") or t.get("kind") not in ("process", "http"):
                raise WatchConfigError(f"bad target: {t!r}")
            if t["kind"] == "process" and not t.get("value"):
                raise WatchConfigError(f"process target needs value: {t['name']}")
            if t["kind"] == "http" and not str(t.get("value", "")).startswith(("http://", "https://")):
                raise WatchConfigError(f"http target needs http(s) url: {t['name']}")
        # P5-2: targets may be staged with "enabled": false until cutover day
        # — they are skipped entirely (no probe, no state, no alerts).
        targets = [t for t in targets if t.get("enabled", True)]
        if not targets:
            raise WatchConfigError("all targets are disabled — nothing to supervise")
        cfg = cls(
            dry_run=bool(doc.get("dry_run", True)),
            interval_sec=int(doc.get("interval_sec", DEFAULT_INTERVAL)),
            throttle_sec=int(doc.get("throttle_sec", DEFAULT_THROTTLE)),
            targets=targets, chat_id=chat_id,
            heartbeat_bj=str(doc.get("heartbeat_bj", "09:00")),
        )
        if not 5 <= cfg.interval_sec <= 3600:
            raise WatchConfigError("interval_sec must be 5..3600")
        if not 30 <= cfg.throttle_sec <= 3600:
            raise WatchConfigError("throttle_sec must be 30..3600")
        return cfg


# --------------------------------------------------------------- probes -----

def probe_target(t: dict) -> bool:
    """True = healthy, False = down; unexpected probe errors -> False
    (honest: an unreadable target is treated as down and will alert)."""
    if t["kind"] == "process":
        pattern = str(t["value"])
        try:
            out = subprocess.run(["pgrep", "-f", pattern], capture_output=True,
                                 text=True, timeout=10)
            return out.returncode == 0 and bool(out.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            return False
    # http
    try:
        with urllib.request.urlopen(str(t["value"]), timeout=8) as r:
            want = int(t.get("expect", 200))
            return r.status == want
    except Exception:  # noqa: BLE001 — any endpoint failure counts as down
        return False


def run_restart(t: dict, timeout: int = RESTART_TIMEOUT) -> tuple[bool, str]:
    """Execute a target's restart command (argv list, shell=False).
    Returns (ok, detail). Only called when dry_run is off."""
    cmd = t.get("restart")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) for c in cmd):
        return False, "no restart command configured"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"restart timed out after {timeout}s"
    except OSError as e:
        return False, f"restart spawn failed: {type(e).__name__}"
    ok = proc.returncode == 0
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [""]
    return ok, f"rc={proc.returncode} {detail[0][:120]}".strip()


# ---------------------------------------------------------------- engine ----

class Watchdog:
    def __init__(self, cfg: WatchConfig, state_path: str | Path,
                 tg: TgClient | None = None,
                 log: Callable[[str], None] | None = None,
                 probe: Callable[[dict], bool] = probe_target,
                 restart: Callable[[dict], tuple[bool, str]] = run_restart,
                 now: Callable[[], float] = time.time) -> None:
        self.cfg = cfg
        self.state_path = Path(state_path)
        self.tg = tg
        self._log = log or (lambda line: print(line, flush=True))
        self._probe = probe
        self._restart = restart
        self._now = now
        self.states: dict[str, TargetState] = {t["name"]: TargetState() for t in cfg.targets}
        self._load_state()
        self._last_heartbeat_day = ""

    # ---------------------------------------------------------- state I/O --
    def _load_state(self) -> None:
        try:
            doc = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for name, st in (doc.get("targets") or {}).items():
            if name in self.states and isinstance(st, dict):
                self.states[name] = TargetState(**{
                    k: st.get(k, getattr(TargetState(), k))
                    for k in ("state", "since", "last_restart", "restart_attempts", "last_alert")})
        self._last_heartbeat_day = doc.get("heartbeat_day", "")

    def _save_state(self) -> None:
        doc = {"targets": {n: st.__dict__ for n, st in self.states.items()},
               "heartbeat_day": self._last_heartbeat_day}
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError as e:
            self._log(f"[wd] state save failed: {type(e).__name__}")

    # ---------------------------------------------------------- notify -----
    def notify(self, text: str) -> None:
        """Send-only TG notification (no getUpdates -> no live-stack conflict).
        No chat id = log-only; token absence = honest log line, never a crash."""
        line = f"[wd] {text}"
        if not self.cfg.chat_id:
            self._log(line + " (log-only: no chat id)")
            return
        if self.tg is None or not self.tg.has_token:
            self._log(line + " (notify skipped: no token)")
            return
        try:
            self.tg.send_message(self.cfg.chat_id, text)
            self._log(line + " -> sent")
        except TgError as e:
            self._log(f"{line} -> notify FAILED: {e}")

    # ------------------------------------------------------------ cycle ----
    def check_once(self) -> dict[str, str]:
        """One supervision cycle. Returns target -> up|down (post-restart)."""
        now = self._now()
        results: dict[str, str] = {}
        for t in self.cfg.targets:
            name = t["name"]
            st = self.states[name]
            healthy = self._probe(t)
            cur = "up" if healthy else "down"
            if st.state != cur:
                self._log(f"[wd] {name}: {st.state or 'unknown'} -> {cur}")
            if healthy:
                if st.state == "down":
                    self.notify(f"🟢 mycore wd · {name} recovered "
                                f"(attempts={st.restart_attempts})")
                st.state = "up"
                st.restart_attempts = 0
                st.since = now
                results[name] = "up"
                continue

            # ---- down path ----
            if st.state != "down":
                st.state = "down"
                st.since = now
                st.restart_attempts = 0
                st.last_alert = now
                self.notify(f"🔴 mycore wd · {name} is DOWN — "
                            + ("dry-run: no action" if self.cfg.dry_run else "restarting"))
            throttle_ok = (now - st.last_restart) >= self.cfg.throttle_sec
            if throttle_ok:
                st.last_restart = now
                st.restart_attempts += 1
                if self.cfg.dry_run:
                    self._log(f"[wd] {name}: restart suppressed (dry-run) "
                              f"attempt={st.restart_attempts}")
                    # Shadow mode must still report on the throttle cadence —
                    # one DOWN alert and then permanent TG silence would hide a
                    # real outage (never-silent rule, 2026-10-17 blind spot).
                    if (now - st.last_alert) >= self.cfg.throttle_sec:
                        st.last_alert = now
                        self.notify(f"⚠️ mycore wd · {name} still down "
                                    f"(dry-run, attempt {st.restart_attempts}; "
                                    f"run with --execute to act)")
                else:
                    ok, detail = run_restart_result(t, self._restart)
                    st.last_alert = now
                    self._log(f"[wd] {name}: restart attempt={st.restart_attempts} "
                              f"{'OK' if ok else 'FAIL'} {detail}")
                    if not ok:
                        # Persistent failure must keep alerting (never silent).
                        self.notify(f"⚠️ mycore wd · {name} restart FAILED "
                                    f"(attempt {st.restart_attempts}): {detail}")
                    else:
                        self.notify(f"🔄 mycore wd · {name} restart issued, verifying…")
            elif (now - st.last_alert) >= self.cfg.throttle_sec:
                # Inside the throttle window but too long since any alert —
                # the independent reminder path (kept reachable by design:
                # last_alert moves only on alert-worthy events).
                st.last_alert = now
                self.notify(f"⚠️ mycore wd · {name} still down "
                            f"({st.restart_attempts} attempt(s), restart in "
                            f"{int(self.cfg.throttle_sec - (now - st.last_restart))}s)")
            results[name] = "down"
        self._save_state()
        self._maybe_heartbeat(now, results)
        return results

    def _maybe_heartbeat(self, now: float, results: dict[str, str]) -> None:
        """Daily heartbeat in Beijing time (owner timezone), once per day."""
        import datetime as dt
        bj = dt.datetime.fromtimestamp(now, dt.timezone.utc) + dt.timedelta(hours=8)
        day = bj.strftime("%Y-%m-%d")
        hhmm = bj.strftime("%H:%M")
        target = self.cfg.heartbeat_bj
        if hhmm >= target and self._last_heartbeat_day != day:
            self._last_heartbeat_day = day
            summary = " ".join(f"{k}={v}" for k, v in results.items())
            self.notify(f"🟢 [心跳] mycore watchdog · {day} {hhmm} · {summary}")
            self._save_state()

    def loop(self, max_cycles: int | None = None) -> None:
        mode = "DRY-RUN (shadow)" if self.cfg.dry_run else "LIVE"
        self._log(f"[wd] starting: {len(self.cfg.targets)} target(s), "
                  f"interval={self.cfg.interval_sec}s, mode={mode}")
        cycles = 0
        while True:
            self.check_once()
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                return
            time.sleep(self.cfg.interval_sec)


def run_restart_result(t: dict, restart: Callable[[dict], tuple[bool, str]]) -> tuple[bool, str]:
    """Thin indirection so tests can swap the restart executor."""
    return restart(t)


# ------------------------------------------------------------------ CLI ----

def main(argv: list[str] | None = None) -> int:
    """CLI: python3 -m mycore.watchdog --config path [--once|--loop N] [--execute]

    Defaults to DRY-RUN shadow mode: every decision is logged, no process is
    touched. --execute flips live restarts (owner authorized process switches,
    2026-10-18) — operator intent must be explicit every single run.
    Notification chat id: env MYCORE_WD_CHAT ("" = log-only). Token: env
    TELEGRAM_BOT_TOKEN read lazily by TgClient (send-only, no polling conflict).
    Exit: 0 healthy / 1 any target down / 2 bad invocation.
    """
    import argparse
    ap = argparse.ArgumentParser(prog="mycore.watchdog",
                                 description="mycore supervision loop (dry-run by default)")
    ap.add_argument("--config", required=True, help="JSON config path")
    ap.add_argument("--state", default=None, help="state file (default: <config>.state.json)")
    ap.add_argument("--once", action="store_true", help="single check cycle then exit")
    ap.add_argument("--loop", type=int, default=None, metavar="N",
                    help="run N cycles then exit (omit = forever)")
    ap.add_argument("--execute", action="store_true",
                    help="allow real restarts (overrides config dry_run=true)")
    args = ap.parse_args(argv)

    try:
        cfg = WatchConfig.load(args.config, chat_id=os.environ.get("MYCORE_WD_CHAT", ""))
    except WatchConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    if args.execute:
        cfg.dry_run = False

    state_path = args.state or (args.config + ".state.json")
    tg = TgClient() if os.environ.get("TELEGRAM_BOT_TOKEN") else None
    wd = Watchdog(cfg, state_path, tg=tg)

    if args.once:
        results = wd.check_once()
    else:
        wd.loop(max_cycles=args.loop)
        results = wd.states and {n: s.state for n, s in wd.states.items()}
    results = results or {}
    return 1 if any(v != "up" for v in results.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
