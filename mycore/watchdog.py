"""mycore.watchdog — process/endpoint supervision for the mycore runtime (P4-1c).

Design (owner authorized process switches, 2026-10-18):
  - one check cycle over configured targets (process pattern / http endpoint)
  - state machine per target: up -> down (restart attempt) -> up (recovered)
  - persistent-down retry on a throttle window, NEVER silent (lesson from the
    2026-10-17 boot blind spot: a failed bring-up must keep alerting)
  - mission heartbeat (car 3): every cycle reads the mission registry —
    an ACTIVE mission (running/degraded/paused_approve) whose
    last_progress_at is older than MISSION_STALE_SEC alerts
    `[MISSION HEARTBEAT] ...` on the same notify path, re-reported on the
    throttle cadence while stale and silent again once progress resumes.
    No active mission = one fail-soft read, zero alerts. dry_run never
    suppresses alerts (it only gates restarts — same as the down alerts).
  - notifications via TgClient.send_message ONLY (stateless — no getUpdates
    conflict with the live gateway); chat id comes from env, token never echoed
  - **dry_run default ON**: shadow mode evaluates and logs every decision but
    executes no restart — flip explicitly (`--execute` / config dry_run=false)
    only when the operator intends real process control.

Config file (JSON): {"dry_run": bool, "interval_sec": int, "throttle_sec": int,
  "targets": [{"name", "kind": "process"|"http", "value",
               "enabled": bool (optional, default true — false = staged until cutover),
               "restart": ["shell", "argv"...] (optional),
               "pending_if": ["argv"...] (optional — readiness command run when the
                   probe fails; rc 3/4 = fail-closed provisioning pending (fresh
                   install): state "pending", NO restart, NO alert; any other rc =
                   regular down)}]}
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
from .envslots import wd_chat_target  # noqa: E402  (slot fallback, car 1.6)

DEFAULT_INTERVAL = 60
DEFAULT_THROTTLE = 300
RESTART_TIMEOUT = 60

# Car 3 mission heartbeat: an active mission that shows no progress for this
# long triggers a TG alert (tunable — one constant, seconds).
MISSION_STALE_SEC = 900

# Car 7 (D11 watch escalation): escalation policy constants.
#   L1 = a target went down (recorded on the transition).
#   L2 = ESCALATE_AFTER consecutive FAILED restarts -> circuit breaker
#        OPENS: automatic restarts HALT until the target recovers or a human
#        intervenes (never silent: reminders keep firing on the throttle).
#   L3 = the L2 circuit stayed open for L3_AFTER_SEC -> "needs a human"
#        escalation, re-notified on the throttle cadence.
#   recovery -> circuit closed, counters reset, a level-0 record closes the
#        chain so the console sees the full story (L1 -> L2 -> L3 -> clear).
# Every transition is dual-written: TG via notify() (log-only when no chat
# id — honest, never faked) AND the state journal the console event stream
# reads (`escalations` in the state JSON).
DEFAULT_ESCALATE_AFTER = 3         # failed restarts before the circuit opens
L3_AFTER_SEC = 1800                # seconds a closed L2 circuit -> L3
ESC_JOURNAL_MAX = 20               # journal records kept in the state file

# States the heartbeat watches (design 3.2 + car 3 spec): a user-paused or
# not-yet-started mission is never an alert.
MISSION_WATCH_STATES = ("running", "degraded", "paused_approve")


class WatchConfigError(ValueError):
    pass


@dataclass
class TargetState:
    state: str = "unknown"            # up | down | pending | unknown
    since: float = 0.0
    last_restart: float = 0.0
    restart_attempts: int = 0
    last_alert: float = 0.0
    # Car 7 (D11): escalation level (0 = clear, 1 = down, 2 = circuit open,
    # 3 = needs a human) + when level 2 opened (drives the L3 timer).
    esc_level: int = 0
    esc_since: float = 0.0


@dataclass
class WatchConfig:
    dry_run: bool = True
    interval_sec: int = DEFAULT_INTERVAL
    throttle_sec: int = DEFAULT_THROTTLE
    targets: list[dict] = field(default_factory=list)
    chat_id: str = ""                 # notify target ("" = log-only)
    heartbeat_bj: str = "09:00"        # daily Beijing-time heartbeat HH:MM
    escalate_after: int = DEFAULT_ESCALATE_AFTER   # car 7: L2 circuit after N

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
            escalate_after=int(doc.get("escalate_after", DEFAULT_ESCALATE_AFTER)),
        )
        if not 5 <= cfg.interval_sec <= 3600:
            raise WatchConfigError("interval_sec must be 5..3600")
        if not 30 <= cfg.throttle_sec <= 3600:
            raise WatchConfigError("throttle_sec must be 30..3600")
        if not 1 <= cfg.escalate_after <= 20:
            raise WatchConfigError("escalate_after must be 1..20")
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


def pending_expected(t: dict) -> bool:
    """Readiness gate for a failing probe: run the target's `pending_if`
    command (argv list, shell=False). rc 3/4 = fail-closed provisioning is
    pending (expected on a fresh install — not an outage). Missing/invalid
    command, timeout or spawn error = not pending (regular down path)."""
    cmd = t.get("pending_if")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) for c in cmd):
        return False
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode in (3, 4)


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
                 pending: Callable[[dict], bool] = pending_expected,
                 now: Callable[[], float] = time.time,
                 missions_path: str | Path | None = None) -> None:
        self.cfg = cfg
        self.state_path = Path(state_path)
        self.tg = tg
        self._log = log or (lambda line: print(line, flush=True))
        self._probe = probe
        self._restart = restart
        self._pending = pending
        self._now = now
        # Car 3 heartbeat: registry path (None = mycore.mission.default_path,
        # resolved at check time so env/profile seams stay authoritative).
        self.missions_path = Path(missions_path) if missions_path else None
        self.states: dict[str, TargetState] = {t["name"]: TargetState() for t in cfg.targets}
        self._esc: list[dict] = []        # car 7: escalation journal (state file)
        self._load_state()
        self._last_heartbeat_day = ""
        # Last [MISSION HEARTBEAT] alert per mission id (in-memory on purpose:
        # a watchdog restart re-alerts immediately while the mission is still
        # stale — never-silent survives restarts).
        self._mission_alerts: dict[str, float] = {}

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
                    for k in ("state", "since", "last_restart", "restart_attempts",
                              "last_alert", "esc_level", "esc_since")})
        self._last_heartbeat_day = doc.get("heartbeat_day", "")
        # Car 7 (D11): the escalation journal rides the same state file —
        # the console event stream reads it (dual write with TG notify).
        recs = doc.get("escalations")
        if isinstance(recs, list):
            self._esc = [r for r in recs[-ESC_JOURNAL_MAX:]
                         if isinstance(r, dict)][-ESC_JOURNAL_MAX:]

    def _save_state(self) -> None:
        doc = {"targets": {n: st.__dict__ for n, st in self.states.items()},
               "heartbeat_day": self._last_heartbeat_day,
               "escalations": self._esc[-ESC_JOURNAL_MAX:]}
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError as e:
            self._log(f"[wd] state save failed: {type(e).__name__}")

    # ------------------------------------------------- escalation (car 7) --
    def _escalate(self, level: int, target: str, attempt: int,
                  reason: str, text: str) -> None:
        """One escalation transition -> DUAL WRITE: (1) TG via notify() —
        log-only when no chat id, never faked; (2) a journal record in the
        state file that GET /api/events renders as the console's chain
        (L?/attempt/why, real timestamp). Level 0 = recovery/clear."""
        self._esc.append({"ts": round(self._now(), 3), "level": int(level),
                          "target": str(target)[:64],
                          "attempt": int(attempt),
                          "reason": str(reason)[:200]})
        del self._esc[:-ESC_JOURNAL_MAX]
        self.notify(text)

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
            if healthy:
                cur = "up"
            else:
                # A target that is merely not provisioned yet (fail-closed
                # fresh install) is PENDING — never an outage: no restart
                # loop, no alert spam (install.sh verifies this path).
                cur = "pending" if self._pending(t) else "down"
            if st.state != cur:
                self._log(f"[wd] {name}: {st.state or 'unknown'} -> {cur}")
            if cur == "up":
                if st.state == "down":
                    # Car 7 (D11): recovery closes the escalation chain in
                    # the journal too (L? -> L0), same text as before.
                    self._escalate(0, name, st.restart_attempts, "recovered",
                                   f"🟢 mycore wd · {name} recovered "
                                   f"(attempts={st.restart_attempts})")
                    st.esc_level = 0
                    st.esc_since = 0.0
                st.state = "up"
                st.restart_attempts = 0
                st.since = now
                results[name] = "up"
                continue
            if cur == "pending":
                st.state = "pending"
                st.restart_attempts = 0
                st.since = now
                results[name] = "pending"
                continue

            # ---- down path ----
            if st.state != "down":
                st.state = "down"
                st.since = now
                st.restart_attempts = 0
                st.last_alert = now
                st.esc_level = 1              # L1: the target is down
                st.esc_since = 0.0
                self._escalate(1, name, 0, "target down",
                               f"🔴 mycore wd · {name} is DOWN — "
                               + ("dry-run: no action" if self.cfg.dry_run
                                  else "restarting"))
            throttle_ok = (now - st.last_restart) >= self.cfg.throttle_sec
            if st.esc_level >= 2:
                # Car 7 (D11): circuit breaker is OPEN — automatic restarts
                # are HALTED (never silent: reminders keep firing, L3 lands
                # once the open circuit outlives L3_AFTER_SEC).
                if st.esc_level == 2 and (now - st.esc_since) >= L3_AFTER_SEC:
                    st.esc_level = 3
                    st.last_alert = now
                    self._escalate(
                        3, name, st.restart_attempts,
                        "circuit open too long — manual restart required",
                        f"🆘 mycore wd · L3 {name} still down after "
                        f"{L3_AFTER_SEC}s with the circuit OPEN — manual "
                        f"restart required ({st.restart_attempts} failed "
                        f"attempt(s))")
                elif (now - st.last_alert) >= self.cfg.throttle_sec:
                    st.last_alert = now
                    self.notify(f"⚠️ mycore wd · {name} still down "
                                f"(L{st.esc_level}, circuit OPEN, "
                                f"{st.restart_attempts} failed attempt(s); "
                                f"automatic restart halted)")
            elif throttle_ok:
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
                        # Car 7 (D11): L2 — N consecutive REAL failures open
                        # the circuit breaker (automatic restarts halt).
                        if st.esc_level < 2 and st.restart_attempts >= self.cfg.escalate_after:
                            st.esc_level = 2
                            st.esc_since = now
                            self._escalate(
                                2, name, st.restart_attempts,
                                f"{st.restart_attempts} consecutive restart "
                                f"failures — circuit open",
                                f"🛑 mycore wd · L2 {name}: "
                                f"{st.restart_attempts} consecutive restart "
                                f"failures — automatic restart HALTED "
                                f"(circuit open); manual attention required: "
                                f"{detail}")
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
        self._check_missions(now)          # car 3 heartbeat (never silent)
        self._maybe_heartbeat(now, results)
        return results

    # ------------------------------------------------- mission heartbeat --
    def _check_missions(self, now: float) -> None:
        """Car 3: one fail-soft read of the mission registry per cycle.

        An ACTIVE mission (running/degraded/paused_approve) whose
        last_progress_at (fallback updated_at/created_at for pre-car-3 rows)
        is older than MISSION_STALE_SEC alerts `[MISSION HEARTBEAT]` on the
        existing notify path (Chinese, scrubbed goal, never a credential or
        a raw path). Stale state re-alerts on the cfg.throttle_sec cadence
        (never-silent parity with the down path); a mission that made
        progress — or ended — drops its alert slot, so a FRESH stall alerts
        immediately and a recovered mission reports nothing. No registry / no
        active mission = zero alerts. dry_run is irrelevant here: notify()
        gates nothing (dry_run only gates restarts)."""
        try:
            from . import mission as ms
            path = (self.missions_path if self.missions_path is not None
                    else ms.default_path())
            doc = ms.load(path)
        except Exception:  # noqa: BLE001 — a heartbeat bug never breaks the cycle
            return
        stale: set[str] = set()
        for rec in doc.get("missions") or []:
            if not isinstance(rec, dict):
                continue
            mid = str(rec.get("id") or "")
            state = str(rec.get("state") or "")
            if not mid or state not in MISSION_WATCH_STATES:
                continue
            base = (rec.get("last_progress_at") or rec.get("updated_at")
                    or rec.get("created_at") or 0)
            try:
                age = now - float(base)
            except (TypeError, ValueError):
                continue
            if age < MISSION_STALE_SEC:
                continue                      # healthy: never an alert
            stale.add(mid)
            last = self._mission_alerts.get(mid)
            if last is not None and now - last < self.cfg.throttle_sec:
                continue                      # inside the re-report window
            self._mission_alerts[mid] = now
            mins = max(1, int(age // 60))
            goal = ms.scrub(str(rec.get("goal") or ""))[:80]
            msg = (f"\u26a0\ufe0f [MISSION HEARTBEAT] \u4efb\u52a1 {mid} "
                   f"\u5df2 {mins} \u5206\u949f\u65e0\u8fdb\u5c55"
                   f"\uff08{state}\uff09 \u00b7 {goal}")
            self.notify(ms.scrub(msg))
        for mid in list(self._mission_alerts):
            if mid not in stale:
                self._mission_alerts.pop(mid, None)   # recovered / ended

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
            self.notify(f"🟢 [HEARTBEAT] mycore watchdog · {day} {hhmm} · {summary}")
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
    Notification chat id: env MYCORE_WD_CHAT, else the secrets.env slot of
    the same name (mycore.envslots; "" = log-only). Token: env
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
        cfg = WatchConfig.load(args.config, chat_id=wd_chat_target())
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
