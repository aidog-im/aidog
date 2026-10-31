"""mycore.mission — the autonomous mission entity (0.5.44 minimal loop).

Design anchor: docs/design/autonomous_ops_design.md (car 1, sec. 3.2-3.4).
A mission is a one-shot goal a user starts from the console; this module
owns everything that does NOT need the live service:

  * registry       <state>/missions.json — atomic writes (tmp + os.replace),
                    cap MAX_MISSIONS history rows, at most ONE active mission.
  * state machine  planned -> running <-> paused_user -> done | sealed,
                    plus running -> degraded -> running (car 2 entry: a
                    declared gap degrades the current subtask while the
                    queue keeps advancing — F4). Degraded may also pause
                    (degraded -> paused_user) and exits to done/sealed.
                    `paused_approve` remains a reserved car-3 slot with
                    out-edges only (see TRANSITIONS).
                    `done` and `sealed` are the ONLY terminal states, and
                    sealing REQUIRES an evidence list (never a silent stop).
  * plan           the plan slice yields a structured plan written into the
                    registry: {milestones: [{id, title, subtasks: [task
                    ids]}], generated_at}. METHOD/TRIED/BLOCKER marks feed
                    the method-exhaustion lists (mission.methods /
                    mission.blockers), which the detail API reports
                    alongside the milestones. A plan without any MILESTONE
                    line collapses to one bucket ("Plan"); missions that
                    predate car 2 carry no plan key and detail as null.
  * serial queue   mission.tasks[] (id/text/status: pending | in_progress |
                    done | degraded | blocked). The plan groups these flat
                    task ids under milestones (single source of truth for
                    status). One subtask in flight, one slice per tick —
                    a degraded point never stalls the queue.
  * slice protocol one slice = one run_turn executed by the service leg
                    (mycore.service._maybe_tick_missions). The slice prompt
                    carries goal + current subtask + gaps + capability
                    manifest + method-exhaustion list, and says out loud that
                    NOBODY will answer inside the mission (F2). The reply is
                    parsed for ASCII markers (see MARKERS below).
  * budgets        max_slices / max_wall_hours / max_daily_model_calls — any
                    guard reached seals the mission with evidence and a final
                    report; never a silent stop.
  * reporter       three tiers (settings `mission.reporting`:
                    all | milestones | final), per-tier min_interval throttle
                    (120s / 900s / none), same-window merging, escalation and
                    terminal reports bypass the throttle, output scrubbed.
                    Delivery itself is injected (the service passes its own
                    four-tier cron fallback chain: origin -> route:target ->
                    MYCORE_WD_CHAT -> log-only — no parallel implementation).

Marker protocol (model-facing, ASCII, fail-soft — a missing marker simply
keeps the subtask in flight and the budget guards bound the loop):

    MILESTONE: <title>     group the queue into a milestone (plan slice;
                           a leading "- " bullet is tolerated)
    SUBTASK: <text>       decompose (planning slice; falls back to the goal)
    TASK DONE             current subtask finished
    MISSION DONE          whole goal achieved
    GAP: <text>           declare a degraded path / missing capability
    METHOD: <text>        add one attempt to the method-exhaustion list
    TRIED: <text>         mark an attempt as worked through
    BLOCKER: <text>       raise a blocker (reportable, throttle-protected)
    NEED DECISION: <text> escalate a PAID / AUTHORIZED / DECISION-bound item
                          (recorded, reported, the subtask is suspended —
                           other subtasks keep running; SOUL C5)
    UNBLOCK: <tid>        release a suspended subtask back to pending

Language: code/comments English (repo i18n policy); REPORTS to the owner
are Chinese by the remonstrance protocol — those literals live in ZH as
unicode escapes so this file stays ASCII-clean for the source-language lint.
No machine paths, no credential values: every outbound report passes
scrub() first (token / bearer / key shapes + absolute paths).
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
from lib import profile_paths as pp  # noqa: E402  (profile state seam, cron parity)

from .loop import DEFAULT_MAX_ITERS  # noqa: E402  (one source for slice iters)

BEIJING = timezone(timedelta(hours=8), "+08:00")   # fixed offset, no DST (cron parity)

STATE_FILE_NAME = "missions.json"
EVENTS_FILE_NAME = "mission_events.jsonl"          # append-only event stream

MAX_MISSIONS = 50              # history cap (active row counts toward it)
MAX_GOAL_CHARS = 4000
MAX_TASKS = 100
MAX_TASK_TEXT = 2000
MAX_MARK_TEXT = 600            # one parsed marker line, after the prefix
MAX_EVIDENCE = 20
MAX_EVIDENCE_CHARS = 500
MAX_REPORT_CHARS = 3500        # one report fragment, before merging

STATES = ("planned", "running", "paused_user", "degraded",
          "paused_approve", "done", "sealed")
TERMINAL_STATES = frozenset({"done", "sealed"})
ACTIVE_STATES = frozenset({"planned", "running", "paused_user",
                           "degraded", "paused_approve"})

# Legal moves. `degraded` entry opened in car 2 (a declared gap on the
# unfinished current subtask; the queue and the leg keep going — F4).
# `paused_approve` entry opens in car 3; its out-edges are reserved meanwhile.
TRANSITIONS: dict[str, frozenset[str]] = {
    "planned": frozenset({"running", "sealed"}),
    "running": frozenset({"paused_user", "degraded", "done", "sealed"}),
    "paused_user": frozenset({"running", "sealed"}),
    "degraded": frozenset({"running", "paused_user", "done", "sealed"}),
    "paused_approve": frozenset({"running", "done", "sealed"}),  # car 3 opens entry
    "done": frozenset(),
    "sealed": frozenset(),
}

TASK_STATUSES = ("pending", "in_progress", "done", "degraded", "blocked")

REPORTING_TIERS = ("all", "milestones", "final")
DEFAULT_REPORTING = "milestones"                 # owner decision (car contract #3)

# Budget guards (car 2: configurable via the settings `mission` partition and
# overridable per start — the parsed values freeze into the mission record).
# BUDGET_BOUNDS is the single range source for BOTH write paths.
DEFAULT_BUDGET: dict = {"max_slices": 40, "max_wall_hours": 24.0,
                        "max_daily_model_calls": 200}
BUDGET_BOUNDS: dict = {"max_slices": (1, 10000),
                       "max_wall_hours": (0.1, 8760.0),
                       "max_daily_model_calls": (1, 100000)}

# settings `mission` partition — tier + the three budget guards; the console
# always renders this block (no absence checks upstream).
DEFAULT_SETTINGS: dict = {"reporting": DEFAULT_REPORTING, **DEFAULT_BUDGET}
# Per-tier throttle: seconds between REPORTS (0 = no throttle). Terminal and
# escalation reports bypass it entirely (THROTTLE_BYPASS) — never swallowed.
MIN_INTERVAL = {"all": 120.0, "milestones": 900.0, "final": 0.0}

# event kind -> tiers that report it (design doc 3.4 table, car-1 mapping:
# `milestone` in car 1 = plan_ready; subtask granularity is the `all` tier).
REPORTABLE: dict[str, frozenset[str]] = {
    "plan_ready": frozenset({"all", "milestones"}),
    "task_done": frozenset({"all"}),
    "degraded": frozenset({"all", "milestones"}),
    "blocker": frozenset({"all", "milestones"}),
    "error": frozenset({"all", "milestones"}),   # slice model failure = anomaly
    "decision": frozenset({"all", "milestones", "final"}),
    "final": frozenset({"all", "milestones", "final"}),
}
THROTTLE_BYPASS = frozenset({"decision", "final"})

# model-facing ASCII markers (see module doc)
MARK_MILESTONE = "MILESTONE:"
MARK_SUBTASK = "SUBTASK:"
MARK_TASK_DONE = "TASK DONE"
MARK_MISSION_DONE = "MISSION DONE"
MARK_GAP = "GAP:"
MARK_METHOD = "METHOD:"
MARK_TRIED = "TRIED:"
MARK_BLOCKER = "BLOCKER:"
MARK_DECISION = "NEED DECISION:"
MARK_UNBLOCK = "UNBLOCK:"
NO_ANSWER_DECLARATION = ("You will NOT receive any answer, approval or "
                         "feedback from anyone during this mission. There is "
                         "no one to ask — do not pose questions and do not "
                         "wait for input.")

ARROW = "\u2192"

# Reporter copy — Chinese to the owner (remonstrance protocol). Unicode-
# escaped so this source file stays ASCII for tests/source_lang_check.py; keys are English.
ZH = {
    "fact": "\u4e8b\u5b9e",
    "impact": "\u5f71\u54cd",
    "next": "\u4e0b\u4e00\u6b65",
    "task_done": "\u5b50\u4efb\u52a1\u5b8c\u6210",
    "progress": "\u8fdb\u5ea6",
    "continue": "\u7ee7\u7eed",
    "report_progress": "\u6574\u7406\u5b8c\u6210\u62a5\u544a",
    "plan_ready": "\u8ba1\u5212\u5c31\u7eea",
    "total_tasks": "\u5171 {n} \u4e2a\u5b50\u4efb\u52a1",
    "enter_watch": "\u8fdb\u5165\u5b88\u591c\u6267\u884c",
    "advance": "\u63a8\u8fdb {tid}",
    "gap": "\u80fd\u529b\u7f3a\u53e3",
    "degraded_fact": "\u80fd\u529b\u7f3a\u53e3: {g}",
    "degraded_impact": "\u8be5\u9879\u6309\u58f0\u660e\u964d\u7ea7\u6267\u884c\uff0c\u4e0d\u505c\u6446",
    "degraded_next": "\u6309\u964d\u7ea7\u8def\u5f84\u7ee7\u7eed",
    "blocker": "\u5361\u70b9",
    "blocker_fact": "\u5361\u70b9: {b}",
    "blocker_impact": "\u5907\u9009\u65b9\u6cd5 {n} \u6761\u9010\u9879\u6253\u6807",
    "blocker_next": "\u7a77\u4e3e\u672a\u5c3d\u4e0d\u7b97\u6b62",
    "decision": "\u9700\u88c1\u51b3",
    "decision_fact": "\u9700\u88c1\u51b3: {d}",
    "decision_impact": "\u8be5\u5b50\u9879\u6302\u8d77\uff0c\u5176\u4f59\u5b50\u9879\u7ee7\u7eed",
    "decision_next": "\u8bf7\u5728\u63a7\u5236\u53f0\u88c1\u51b3",
    "error_fact": "\u5206\u7247\u6267\u884c\u5f02\u5e38",
    "error_impact": "\u672c\u7247\u672a\u63a8\u8fdb\uff0c\u4e0b\u4e2a\u5206\u7247\u91cd\u8bd5",
    "error_next": "\u6301\u7eed\u5931\u8d25\u5c06\u6309\u9884\u7b97\u62a4\u680f\u5c01\u5b58",
    "done_report": "\u5b8c\u6210\u62a5\u544a",
    "sealed_report": "\u5c01\u5b58\u62a5\u544a",
    "sec_done": "\u5b8c\u6210",
    "sec_gaps": "\u964d\u7ea7 gaps",
    "sec_open": "\u672a\u7adf",
    "sec_next": "\u5efa\u8bae",
    "tasks_done": "{d}/{t} \u5b50\u4efb\u52a1",
    "pending_note": "{n} \u4e2a\u5b50\u4efb\u52a1\u672a\u5b8c\u6210",
    "none": "\u65e0",
    "suggest_gaps": "\u8865\u9f50\u5df2\u767b\u8bb0\u7f3a\u53e3\u5bf9\u5e94\u7684\u80fd\u529b\u540e\u53ef\u7ee7\u7eed",
    "suggest_seal": "\u8865\u9f50\u80fd\u529b\u6216\u8c03\u6574\u9884\u7b97\u62a4\u680f\u540e\u53ef\u91cd\u65b0\u7acb\u6807",
}


class MissionError(ValueError):
    """Expected refusal (bad goal, illegal move, full registry). The message
    is console/chat-safe by contract: no machine paths, no values."""


# ------------------------------------------------------------------ times ---
def _bj(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, BEIJING)


def day_key(ts: float) -> str:
    """Beijing calendar day of `ts` — the daily model-call budget window."""
    return _bj(ts).strftime("%Y-%m-%d")


# ------------------------------------------------------------------ paths ---
def default_path() -> Path:
    """Registry path: same state-dir convention as mycore.scheduler
    (env AIDOG_MC_STATE > <profile>/state > <repo>/data/mycore_service)."""
    return pp.state_dir(REPO) / STATE_FILE_NAME


def events_path(registry_path: str | Path) -> Path:
    return Path(registry_path).with_name(EVENTS_FILE_NAME)


def append_event(registry_path: str | Path, mission_id: str, kind: str,
                 detail: dict | None = None) -> None:
    """Append one line to the mission event stream (JSONL, append-only).
    Fail-soft by design: an unwritable stream must never break execution —
    the registry and service.log keep the durable record either way."""
    rec: dict = {"ts": round(time.time(), 3), "mission": str(mission_id),
                 "kind": str(kind)}
    if isinstance(detail, dict):
        rec.update(detail)
    try:
        path = events_path(registry_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — an event stream write never breaks execution
        pass


# --------------------------------------------------------------- registry ---
def _valid_mission(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    if not isinstance(raw.get("id"), str) or not raw["id"]:
        return False
    if raw.get("state") not in STATES:
        return False
    return isinstance(raw.get("goal"), str) and bool(raw.get("goal").strip())


def load(path: str | Path) -> dict:
    """Registry -> {"missions": [...], "last_tick": ts|None}. Absent or
    corrupt files yield the empty structure (fail-soft: a bad line must not
    take the executor down)."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"missions": [], "last_tick": None}
    if not isinstance(doc, dict):
        return {"missions": [], "last_tick": None}
    raw = doc.get("missions")
    missions = [m for m in raw if _valid_mission(m)] if isinstance(raw, list) else []
    tick = doc.get("last_tick")
    return {"missions": missions,
            "last_tick": tick if isinstance(tick, (int, float)) else None}


def save(path: str | Path, doc: dict) -> None:
    """Atomic persist (tmp + os.replace); OSError propagates — an unwritable
    registry is an honest error, never a silent success (cron parity)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(
        {"missions": doc.get("missions") or [],
         "last_tick": doc.get("last_tick")},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def new_id(missions: list[dict]) -> str:
    """Short unique id (6 hex, collision-checked) — scheduler.new_id parity."""
    taken = {m.get("id") for m in missions}
    while True:
        cand = uuid.uuid4().hex[:6]
        if cand not in taken:
            return cand


def find(missions: list[dict], mission_id: str) -> dict | None:
    for m in missions:
        if m.get("id") == mission_id:
            return m
    return None


def active_mission(doc: dict) -> dict | None:
    """First non-terminal mission (at most one exists by construction)."""
    for m in doc.get("missions") or []:
        if m.get("state") in ACTIVE_STATES:
            return m
    return None


def validate_budget(budget) -> dict:
    """Merge + bound a mission budget (defaults for absent keys); raises
    MissionError with a chat-safe reason on anything out of range."""
    if budget is None:
        return dict(DEFAULT_BUDGET)
    if not isinstance(budget, dict):
        raise MissionError("budget must be an object")
    out = dict(DEFAULT_BUDGET)
    for key, val in budget.items():
        if key not in DEFAULT_BUDGET:
            raise MissionError(f"unknown budget key: {key}")
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise MissionError(f"budget.{key} must be a number")
        lo, hi = BUDGET_BOUNDS[key]
        if not lo <= float(val) <= hi:
            raise MissionError(f"budget.{key} out of range {lo}..{hi}")
        if key == "max_wall_hours":
            if round(float(val), 1) != float(val):
                raise MissionError(
                    f"budget.{key} allows at most 1 decimal")
            out[key] = float(val)
        else:
            if not float(val).is_integer():
                raise MissionError(f"budget.{key} must be a whole number")
            out[key] = int(val)
    return out


def settings_budget(block) -> dict:
    """Effective default budget from the settings `mission` block (car 2).
    Defensive by design: an absent, non-object or out-of-range stored value
    falls back to DEFAULT_BUDGET — a mission never starts without guards."""
    out = dict(DEFAULT_BUDGET)
    if not isinstance(block, dict):
        return out
    for key in DEFAULT_BUDGET:
        if key not in block:
            continue
        try:
            out[key] = validate_budget({key: block[key]})[key]
        except MissionError:
            continue
    return out


def create(missions: list[dict], goal: str, *,
           reporting: str | None = None,
           budget: dict | None = None,
           origin: int | str | None = None,
           manifest: dict | None = None,
           tasks: list[str] | None = None,
           slice_iters: int | None = None,
           now: float | None = None) -> dict:
    """Validate + append one mission in `planned` (mutates, returns it).

    Refuses (MissionError): empty/oversized goal, unknown reporting tier, a
    full history (MAX_MISSIONS) or an EXISTING ACTIVE mission — the executor
    is single-mission by contract (one running row, ever)."""
    tier = reporting if reporting is not None else DEFAULT_REPORTING
    if tier not in REPORTING_TIERS:
        raise MissionError("reporting must be one of: " + "|".join(REPORTING_TIERS))
    body = (goal or "").strip()
    if not body:
        raise MissionError("empty goal")
    if len(body) > MAX_GOAL_CHARS:
        raise MissionError(f"goal too long — {MAX_GOAL_CHARS} chars max")
    if any(isinstance(m, dict) and m.get("state") in ACTIVE_STATES
           for m in missions):
        raise MissionError("an active mission already exists — stop it first")
    if len(missions) >= MAX_MISSIONS:
        raise MissionError(f"mission history is full ({MAX_MISSIONS}) — "
                           "retire old missions first")
    if slice_iters is not None and (isinstance(slice_iters, bool)
                                    or not isinstance(slice_iters, int)
                                    or not 1 <= slice_iters <= 100):
        raise MissionError("slice_iters must be an integer in 1..100")
    now = time.time() if now is None else now
    rows: list[dict] = []
    for i, text in enumerate(tasks or []):
        text = str(text).strip()[:MAX_TASK_TEXT]
        if text:
            rows.append({"id": f"t{i + 1}", "text": text, "status": "pending"})
    if len(rows) > MAX_TASKS:
        raise MissionError(f"too many tasks — {MAX_TASKS} max")
    rec: dict = {
        "id": new_id(missions),
        "goal": body,
        "state": "planned",
        "origin": origin if isinstance(origin, (int, str)) else None,
        "reporting": tier,
        "budget": validate_budget(budget),
        "slice_iters": int(slice_iters) if slice_iters else int(DEFAULT_MAX_ITERS),
        "counters": {"slices": 0, "daily_calls": 0, "calls_date": day_key(now)},
        "tasks": rows,
        "gaps": [],           # declared degraded paths (strings)
        "methods": [],        # method-exhaustion list [{text, marked}]
        "blockers": [],       # observed blockers (strings)
        "manifest": dict(manifest) if isinstance(manifest, dict) else {},
        "last_report_at": None,
        "report_queue": [],   # same-window merge buffer (formatted fragments)
        "last_tick": None,
        "ended_at": None,
        "evidence": [],
        "created_at": now,
        "updated_at": now,
    }
    missions.append(rec)
    return rec


def transition(mission_: dict, new_state: str, now: float, *,
               evidence: list[str] | None = None,
               reason: str = "") -> None:
    """Move one mission along the state machine (mutates). Illegal edges and
    an evidence-less seal raise MissionError (F4: sealed always carries an
    evidence pack — a terminal state is never empty)."""
    cur = mission_.get("state")
    if new_state not in STATES:
        raise MissionError(f"unknown state {new_state!r}")
    if new_state not in TRANSITIONS.get(cur, frozenset()):
        allowed = ", ".join(sorted(TRANSITIONS.get(cur, frozenset()))) \
            or "none (terminal)"
        raise MissionError(
            f"illegal transition {cur} -> {new_state} — allowed from "
            f"{cur}: {allowed}")
    if new_state == "sealed":
        ev = [str(e).strip()[:MAX_EVIDENCE_CHARS]
              for e in (evidence or []) if str(e).strip()]
        if not ev:
            raise MissionError("sealing requires at least one evidence entry")
        mission_["evidence"] = ev[:MAX_EVIDENCE]
    mission_["state"] = new_state
    mission_["updated_at"] = now
    if new_state in TERMINAL_STATES:
        mission_["ended_at"] = now


# ---------------------------------------------------------------- budgets ---
def note_model_calls(mission_: dict, n: int, now: float) -> None:
    """Charge `n` model calls to today's daily budget (rolls over on the
    Beijing calendar day — one window, one counter)."""
    counters = mission_.setdefault("counters", {})
    today = day_key(now)
    if counters.get("calls_date") != today:
        counters["calls_date"] = today
        counters["daily_calls"] = 0
    counters["daily_calls"] = int(counters.get("daily_calls") or 0) + max(0, int(n))


def budget_breach(mission_: dict, now: float) -> str | None:
    """-> the name of the first guard reached, or None. Checked BEFORE a
    slice is marked (a sealed mission never runs another slice)."""
    budget = {**DEFAULT_BUDGET, **(mission_.get("budget") or {})}
    counters = mission_.get("counters") or {}
    if int(counters.get("slices") or 0) >= int(budget["max_slices"]):
        return "max_slices"
    created_raw = mission_.get("created_at")
    created = float(created_raw) if isinstance(created_raw, (int, float)) else now
    if now - created >= float(budget["max_wall_hours"]) * 3600:
        return "max_wall_hours"
    if int(counters.get("daily_calls") or 0) >= int(budget["max_daily_model_calls"]):
        return "max_daily_model_calls"
    return None


def budget_evidence(mission_: dict, guard: str) -> str:
    """English evidence line for the sealed pack (data, console-rendered)."""
    budget = {**DEFAULT_BUDGET, **(mission_.get("budget") or {})}
    counters = mission_.get("counters") or {}
    if guard == "max_slices":
        actual = int(counters.get("slices") or 0)
    elif guard == "max_daily_model_calls":
        actual = int(counters.get("daily_calls") or 0)
    else:
        created_raw = mission_.get("created_at")
        created = float(created_raw) if isinstance(created_raw, (int, float)) else time.time()
        actual = int(max(0.0, time.time() - created) // 3600)
    return f"budget guard {guard} reached ({actual}/{budget[guard]})"


# ------------------------------------------------------------ capability ----
def build_manifest(settings: dict, slot_names=(), present=None, *,
                   browser_ok: bool | None = None,
                   connector_ok: bool | None = None,
                   exchange_ok: bool | None = None) -> dict:
    """F1 capability manifest, generated at mission start and frozen into the
    record. Names and booleans ONLY: tools after the tools_off kill set (plus
    the live probe gates the registry itself uses), enabled channel routes,
    credential slot key names with a presence bool (never a value), and the
    honest web3 block (approval gate, no withdrawals, no paid tools). The
    probe kwargs exist for deterministic tests; None = live probe."""
    settings = settings if isinstance(settings, dict) else {}
    mc = settings.get("mycore") if isinstance(settings.get("mycore"), dict) else {}
    try:
        from .tools import KNOWN_TOOL_NAMES, resolve_disabled_tools
        disabled = set(resolve_disabled_tools(settings))
        tools = set(KNOWN_TOOL_NAMES) - disabled
    except Exception:  # noqa: BLE001 — an unavailable catalog degrades honestly
        tools = set()
    if browser_ok is None:
        try:
            from . import browser as _browser
            browser_ok = bool(_browser.available())
        except Exception:  # noqa: BLE001
            browser_ok = False
    if not browser_ok:
        tools -= {"browser_open", "browser_snapshot", "browser_click",
                  "browser_type", "browser_screenshot", "browser_close"}
    if connector_ok is None:
        try:
            from . import connectors as _connectors
            connector_ok = any(_connectors.status().values())
        except Exception:  # noqa: BLE001
            connector_ok = False
    if not connector_ok:
        tools.discard("connector_search")
    if exchange_ok is None:
        try:
            from . import exchanges as _exchanges
            exchange_ok = any(_exchanges.status().values())
        except Exception:  # noqa: BLE001
            exchange_ok = False
    if not exchange_ok:
        tools -= {"exchange_portfolio", "exchange_orders",
                  "exchange_prepare_order", "exchange_execute_order",
                  "exchange_plans"}
    chans = settings.get("channels") if isinstance(settings.get("channels"), dict) else {}
    enabled = sorted(name for name, cfg in chans.items()
                     if isinstance(cfg, dict) and cfg.get("enabled") is True)
    creds: dict = {}
    present = present if callable(present) else (lambda _name: False)
    for name in list(slot_names or []):
        name = str(name)
        creds[name] = bool(present(name))     # bool ONLY — never the value
    w3 = settings.get("web3") if isinstance(settings.get("web3"), dict) else {}
    web3 = {"auto_usd_per_tx": float(w3.get("auto_usd_per_tx") or 0.0),
            "auto_usd_daily": float(w3.get("auto_usd_daily") or 0.0),
            "order_gate": "prepare -> console approval -> execute",
            "withdrawals": False, "onchain_transfers": False,
            "paid_actions": False}
    return {"tools": sorted(tools), "channels": enabled,
            "credentials": creds, "web3": web3,
            "mcp": "disabled",                 # community MCP: permanent red line
            "generated_at": round(time.time(), 3)}


# --------------------------------------------------------------- slicing ----
def set_current(mission_: dict, now: float | None = None) -> dict | None:
    """Promote the first pending subtask to in_progress — but only when no
    task is in flight already (a mid-slice retry keeps its target). Returns
    the current task; None when nothing is pending (all done / all blocked /
    all degraded). Car 2: starting a subtask while the mission sits in
    `degraded` returns it to `running` (the next-subtask edge — a degraded
    point is a record, never a stall)."""
    tasks = mission_.get("tasks") or []
    running = next((t for t in tasks if t.get("status") == "in_progress"), None)
    if running is None:
        for task in tasks:
            if task.get("status") == "pending":
                task["status"] = "in_progress"
                running = task
                break
    if running is not None and mission_.get("state") == "degraded":
        transition(mission_, "running",
                   time.time() if now is None else now,
                   reason="subtask started")
    return running


def current_task(mission_: dict) -> dict | None:
    for task in mission_.get("tasks") or []:
        if task.get("status") in ("in_progress", "pending"):
            return task
    return None


def _task_rows(mission_: list[dict], statuses: tuple[str, ...]) -> list[str]:
    return [f"{t.get('id')}: {str(t.get('text', ''))[:160]}"
            for t in mission_.get("tasks") or [] if t.get("status") in statuses]


def _fmt_manifest(manifest: dict) -> str:
    if not isinstance(manifest, dict) or not manifest:
        return "(no capability manifest recorded — verify every capability before use)"
    creds = manifest.get("credentials") or {}
    present = sorted(k for k, v in creds.items() if v)
    absent = sorted(k for k, v in creds.items() if not v)
    return (f"tools: {', '.join(manifest.get('tools') or []) or 'none'}\n"
            f"channels enabled: {', '.join(manifest.get('channels') or []) or 'none'}\n"
            f"credentials present (key names only): {', '.join(present) or 'none'}\n"
            f"credentials absent (key names only): {', '.join(absent) or 'none'}\n"
            f"web3: {json.dumps(manifest.get('web3') or {}, ensure_ascii=False)}\n"
            f"community MCP: {manifest.get('mcp', 'disabled')} (never enabled)")


def _fmt_methods(mission_: dict) -> str:
    rows = mission_.get("methods") or []
    if not rows:
        return "(empty — create one when you hit a blocker)"
    return "\n".join(
        f"- [{'tried' if r.get('marked') else 'open'}] {str(r.get('text', ''))[:200]}"
        for r in rows[:50])


def build_slice_prompt(mission_: dict, mode: str = "exec") -> str:
    """One slice's user message (the constitution stays in the system stack,
    built by assembly.build_system_stack — the goal text only ever enters the
    USER layer). Compact by design: goal + current subtask + gaps + manifest +
    method list, never the full history (context-budget rule, design 6)."""
    mid = mission_.get("id", "?")
    lines = [f"MISSION {mid} — autonomous {'planning' if mode == 'plan' else 'execution'} "
             f"slice {int((mission_.get('counters') or {}).get('slices') or 0)}"
             f"/{(mission_.get('budget') or {}).get('max_slices', '?')}",
             "", "Ultimate goal:", str(mission_.get("goal", "")), ""]
    tasks = mission_.get("tasks") or []
    if mode == "plan" or not tasks:
        lines += [
            "Planning: decompose the goal into a SERIAL queue of actionable",
            f"subtasks. One line per subtask, exactly: {MARK_SUBTASK} <text>",
            "Group subtasks into phases when the goal has them: one line",
            f"exactly: {MARK_MILESTONE} <title> (a leading '-' bullet is",
            "tolerated); every SUBTASK after it belongs to that milestone.",
            "Without any MILESTONE line the plan is a single milestone.",
            "Then stop. Execution begins on the next slice.", ""]
    else:
        cur = current_task(mission_)
        done_n = sum(1 for t in tasks if t.get("status") == "done")
        lines.append(f"Progress: {done_n}/{len(tasks)} subtasks done.")
        if cur is not None:
            lines += ["", f"Current subtask [{cur.get('id')}]: {cur.get('text')}", ""]
        else:
            blocked = _task_rows(mission_, ("blocked",))
            lines += ["", "No pending subtask. Blocked items:", *["  " + b for b in blocked],
                      "For each, decide: if the blocker is cleared output "
                      f"{MARK_UNBLOCK} <tid>; if it still needs a person output "
                      f"{MARK_DECISION} <what> once and move on.", ""]
            degraded_rows = _task_rows(mission_, ("degraded",))
            if degraded_rows:
                lines += ["Degraded items (fallback in effect — keep them "
                          "reported as gaps, do not re-ask; finish the goal "
                          "honestly):",
                          *["  " + d for d in degraded_rows], ""]
        pending = _task_rows(mission_, ("pending",))
        if pending:
            lines += ["Queued subtasks:", *["  " + p for p in pending], ""]
    gaps = mission_.get("gaps") or []
    lines.append("Known gaps (declared fallbacks — NEVER re-ask about these):")
    lines += [f"  - {str(g)[:300]}" for g in gaps[:20]] or ["  (none)"]
    lines += ["", "Capability manifest — use ONLY what is listed here",
              "(F1: never use, probe or request an unlisted capability):",
              _fmt_manifest(mission_.get("manifest") or {})]
    lines += ["", "Method-exhaustion list (an untried combination is still an "
                  "opportunity; a blocker is done only when its list is worked "
                  "through):", _fmt_methods(mission_)]
    lines += ["", "Rules:", f"- {NO_ANSWER_DECLARATION}",
              "- A missing capability is a declared fallback path plus a "
              f"{MARK_GAP} line — never a pause, never a question.",
              f"- When the current subtask completes, end your reply with a "
              f"line: {MARK_TASK_DONE}",
              f"- When the whole goal is achieved, end with: {MARK_MISSION_DONE}",
              f"- Record shortfalls as: {MARK_GAP} <what is missing and which "
              "fallback you used>",
              f"- When blocked, add attempts as {MARK_METHOD} <attempt> and "
              f"mark progress as {MARK_TRIED} <attempt>; raise blockers as "
              f"{MARK_BLOCKER} <text>.",
              f"- Escalate ONLY PAID / AUTHORIZED / DECISION-bound items as "
              f"{MARK_DECISION} <what> — the item suspends, you continue the "
              "other subtasks immediately.",
              "- Keep work inside the configured write scope; destructive or "
              "irreversible actions are declared, not performed."]
    return "\n".join(lines)


def parse_slice(text: str) -> dict:
    """Slice output -> marker marks (fail-soft: prose without markers yields
    empty marks — the subtask simply stays in flight). `plan_lines` keeps the
    MILESTONE/SUBTASK lines IN ORDER so the plan can group subtasks under the
    milestone they were written after; `milestones` is the bare title list."""
    out: dict = {"tasks": [], "task_done": False, "mission_done": False,
                 "gaps": [], "decisions": [], "blockers": [], "methods": [],
                 "tried": [], "unblock": [], "milestones": [],
                 "plan_lines": []}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or len(line) > 400:
            continue
        if MARK_MISSION_DONE in line:
            out["mission_done"] = True
        if MARK_TASK_DONE in line:
            out["task_done"] = True
        low = line.lstrip("-* \t")          # tolerate bullets before prefixes
        for key, prefix in (("tasks", MARK_SUBTASK),
                            ("milestones", MARK_MILESTONE),
                            ("gaps", MARK_GAP),
                            ("decisions", MARK_DECISION),
                            ("blockers", MARK_BLOCKER),
                            ("methods", MARK_METHOD), ("tried", MARK_TRIED),
                            ("unblock", MARK_UNBLOCK)):
            if low.startswith(prefix):
                val = low[len(prefix):].strip()[:MAX_MARK_TEXT]
                if val:
                    out[key].append(val)
                    if key == "tasks":
                        out["plan_lines"].append(("task", val))
                    elif key == "milestones":
                        out["plan_lines"].append(("milestone", val))
                break
    return out


def apply_slice(mission_: dict, marks: dict, now: float, *,
                mode: str = "exec") -> list[dict]:
    """Fold one slice's marks into the mission (mutates) and return the
    report events it earned. Plan mode creates the serial queue (falling back
    to the goal itself when the model produced no SUBTASK lines) and never
    honors MISSION DONE — there is nothing to finish before the plan exists."""
    events: list[dict] = []
    tasks = mission_.setdefault("tasks", [])
    plan = mission_.get("plan") if isinstance(mission_.get("plan"), dict) else None
    if mode == "plan":
        marks = dict(marks)
        marks["mission_done"] = False
        # Structured plan (car 2): walk the ordered plan lines, grouping each
        # SUBTASK under the MILESTONE it follows. No MILESTONE at all (or no
        # tasks) collapses to one bucket titled "Plan" — car-1 protocol
        # compatibility; the preview semantics stay F2 (show = execute).
        groups: list[dict] = [{"title": None, "tasks": []}]
        for kind, val in (marks.get("plan_lines") or []):
            if kind == "milestone":
                title = scrub(str(val).strip())[:MAX_TASK_TEXT]
                head = groups[-1]
                if head["title"] is None and not head["tasks"]:
                    head["title"] = title
                else:
                    groups.append({"title": title, "tasks": []})
                continue
            if len(tasks) >= MAX_TASKS:
                continue
            text = scrub(str(val).strip())[:MAX_TASK_TEXT]
            if not text:
                continue
            row = {"id": f"t{len(tasks) + 1}", "text": text,
                   "status": "pending"}
            tasks.append(row)
            groups[-1]["tasks"].append(row)
        if not tasks:
            tasks.append({"id": "t1",
                          "text": str(mission_.get("goal", ""))[:MAX_TASK_TEXT],
                          "status": "pending"})
            groups = [{"title": "Plan", "tasks": [tasks[0]]}]
        milestones: list[dict] = []
        n = 0
        for g in groups:
            if not g["tasks"]:
                continue
            n += 1
            milestones.append({"id": f"m{n}",
                               "title": g["title"] or "Plan",
                               "subtasks": [t["id"] for t in g["tasks"]]})
        mission_["plan"] = {"milestones": milestones, "generated_at": now}
        events.append({"kind": "plan_ready", "level": "INFO",
                       "fact": ZH["plan_ready"] + " — "
                               + ZH["total_tasks"].format(n=len(tasks)),
                       "impact": ZH["enter_watch"],
                       "next": ZH["advance"].format(tid=tasks[0].get("id", "t1"))})
    else:
        for text in marks["tasks"]:
            text = scrub(str(text).strip())[:MAX_TASK_TEXT]
            if text and len(tasks) < MAX_TASKS:
                row = {"id": f"t{len(tasks) + 1}", "text": text,
                       "status": "pending"}
                tasks.append(row)
                # a mid-mission decomposition joins the last milestone (plans
                # that predate car 2 simply have no plan to join)
                if plan and plan.get("milestones"):
                    plan["milestones"][-1]["subtasks"].append(row["id"])
    # gaps: dedupe by normalized text, one degraded event per NEW gap.
    # Every model-derived string is scrubbed at storage (credential shapes
    # and machine paths never persist — registry and reports stay clean).
    gaps = mission_.setdefault("gaps", [])
    seen = {str(g).strip().lower() for g in gaps}
    new_gap = False
    for text in marks["gaps"]:
        text = scrub(text)
        key = text.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        gaps.append(text)
        new_gap = True
        events.append({"kind": "degraded", "level": "WARN",
                       "fact": ZH["degraded_fact"].format(g=text[:200]),
                       "impact": ZH["degraded_impact"],
                       "next": ZH["degraded_next"]})
    # method-exhaustion list: add pending attempts, mark the worked-through ones
    methods = mission_.setdefault("methods", [])
    known = {str(r.get("text", "")).strip().lower() for r in methods}
    for text in marks["methods"]:
        text = scrub(text)
        key = text.strip().lower()
        if key and key not in known:
            known.add(key)
            methods.append({"text": text, "marked": False})
    for text in marks["tried"]:
        text = scrub(text)
        key = text.strip().lower()
        for row in methods:
            row_key = str(row.get("text", "")).strip().lower()
            if row_key == key or key in row_key or row_key in key:
                row["marked"] = True
    # blockers: record + report (all/milestones tier, throttle-protected)
    blockers = mission_.setdefault("blockers", [])
    for text in marks["blockers"]:
        text = scrub(text)
        blockers.append(text[:MAX_MARK_TEXT])
        events.append({"kind": "blocker", "level": "WARN",
                       "fact": ZH["blocker_fact"].format(b=text[:200]),
                       "impact": ZH["blocker_impact"].format(n=len(methods)),
                       "next": ZH["blocker_next"]})
    # escalation: suspend the current subtask (C5 — others keep running)
    cur = current_task(mission_)
    for _text in marks["decisions"]:
        _text = scrub(_text)
        target = cur if cur is not None else None
        if target is not None and target.get("status") == "in_progress":
            target["status"] = "blocked"
            cur = None
        events.append({"kind": "decision", "level": "WARN",
                       "fact": ZH["decision_fact"].format(d=_text[:200]),
                       "impact": ZH["decision_impact"],
                       "next": ZH["decision_next"]})
    for tid in marks["unblock"]:
        for task in tasks:
            if task.get("id") == tid and task.get("status") == "blocked":
                task["status"] = "pending"
                if mission_.get("state") == "degraded":
                    transition(mission_, "running", now, reason="unblock")
    # subtask completion: current in_progress (or first pending) -> done
    if marks["task_done"] and mode != "plan":
        target = next((t for t in tasks if t.get("status") == "in_progress"), None)
        if target is None:
            target = next((t for t in tasks if t.get("status") == "pending"), None)
        if target is not None:
            target["status"] = "done"
            done_n = sum(1 for t in tasks if t.get("status") == "done")
            nxt = next((t for t in tasks if t.get("status") == "pending"), None)
            events.append({
                "kind": "task_done", "level": "INFO",
                "fact": ZH["task_done"] + f" [{target.get('id')}] "
                        + str(target.get("text", ""))[:160],
                "impact": f"{ZH['progress']} {done_n}/{len(tasks)}",
                "next": (ZH["continue"] + " " + str(nxt.get("id")))
                        if nxt is not None else ZH["report_progress"]})
    # degraded entry (car 2): a NEW gap on the still-unfinished current
    # subtask marks that subtask degraded and enters `degraded` (from
    # `running` only — the machine owns every move). The queue, the leg and
    # every other subtask keep going: one degraded point never stalls (F4).
    if new_gap and mode != "plan":
        cur_deg = next((t for t in tasks if t.get("status") == "in_progress"),
                       None)
        if cur_deg is not None:
            cur_deg["status"] = "degraded"
            if mission_.get("state") == "running":
                transition(mission_, "degraded", now, reason="gap declared")
    return events


def should_finish(mission_: dict, marks: dict) -> bool:
    """True when the goal is achieved: the model said so, or every subtask
    is done (an empty queue never counts — the plan owns the first turn)."""
    if marks.get("mission_done"):
        return bool(mission_.get("tasks"))
    tasks = mission_.get("tasks") or []
    return bool(tasks) and all(t.get("status") == "done" for t in tasks)


# --------------------------------------------------------------- reporter ----
_PATH_RE = re.compile(
    r"/(?:mnt|home|root|etc|var|opt|usr|srv|tmp|dev|proc|sys|run|media)"
    r"(?:/[^\s\"'`)\]]+)*")
_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")


def scrub(text: str) -> str:
    """Reporter egress filter (single choke point): token/bearer/key shapes
    and absolute machine paths leave as placeholders. Applied to EVERY
    report right before delivery — mission text is model-derived, so this is
    the zero-credential / observation-scrub guarantee for the mission car."""
    text = _TOKEN_RE.sub("[redacted]", str(text))
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _KEY_RE.sub("[redacted]", text)
    return _PATH_RE.sub("[path]", text)


def fmt_event(mission_id: str, ev: dict) -> str:
    """One event -> the remonstrance line: [MISSION <id>] LEVEL fact -> ..."""
    head = f"[MISSION {mission_id}] {ev.get('level', 'INFO')}"
    return scrub(f"{head} {ev.get('fact', '')} {ARROW} "
                 f"{ev.get('impact', '')} {ARROW} {ev.get('next', '')}")


def fmt_final(mission_: dict, done: bool) -> str:
    """Terminal report — the fixed four sections (completed / degraded
    gaps / unfinished / next steps; a sealed pack lists its evidence under
    unfinished). Section labels come from ZH at call time."""
    mid = mission_.get("id", "?")
    tasks = mission_.get("tasks") or []
    done_rows = _task_rows(mission_, ("done",))
    open_rows = _task_rows(mission_,
                           ("pending", "in_progress", "degraded", "blocked"))
    gaps = [str(g)[:200] for g in (mission_.get("gaps") or [])]
    done_n = len(done_rows)
    sec_done = ZH["tasks_done"].format(d=done_n, t=len(tasks))
    if done_rows:
        sec_done += " | " + " | ".join(r[:160] for r in done_rows[:5])
    sec_gaps = " | ".join(gaps[:8]) if gaps else ZH["none"]
    if done:
        title, level = ZH["done_report"], "INFO"
        sec_open = " | ".join(r[:160] for r in open_rows[:8]) if open_rows else ZH["none"]
        sec_next = ZH["suggest_gaps"] if gaps else ZH["none"]
    else:
        title, level = ZH["sealed_report"], "WARN"
        parts = [str(e)[:MAX_EVIDENCE_CHARS] for e in (mission_.get("evidence") or [])]
        if open_rows:
            parts.append(ZH["pending_note"].format(n=len(open_rows)))
        sec_open = " | ".join(parts) if parts else ZH["none"]
        sec_next = ZH["suggest_seal"]
    body = "\n".join([
        f"{ZH['sec_done']}: {sec_done}",
        f"{ZH['sec_gaps']}: {sec_gaps}",
        f"{ZH['sec_open']}: {sec_open}",
        f"{ZH['sec_next']}: {sec_next}"])
    return scrub(f"[MISSION {mid}] {level} {title}\n{body}")


def emit(mission_: dict, kind: str, fragment: str, deliver, now: float) -> str:
    """Tier gate + throttle + same-window merge for one report.

    Returns "sent" (delivery attempted — including log-only fallback),
    "queued" (tier-eligible but inside the min_interval window; merged into
    the next send) or "suppressed" (tier does not carry this kind — dropped,
    never queued). Escalation and terminal fragments bypass the throttle so
    a budget seal or a decision request can never be swallowed. Never
    raises: a delivery failure is an honest "error" label, execution
    continues (F5: no channel, no stall)."""
    tier = mission_.get("reporting") or DEFAULT_REPORTING
    if tier not in REPORTING_TIERS:
        tier = DEFAULT_REPORTING
    if tier not in REPORTABLE.get(kind, frozenset()):
        return "suppressed"
    min_interval = MIN_INTERVAL.get(tier, 0.0)
    last = mission_.get("last_report_at")
    due = (kind in THROTTLE_BYPASS or min_interval <= 0 or last is None
           or now - float(last) >= min_interval)
    queue = mission_.setdefault("report_queue", [])
    if not due:
        queue.append(fragment)
        return "queued"
    text = "\n".join([*queue, fragment]) if queue else fragment
    mission_["report_queue"] = []
    mission_["last_report_at"] = now
    try:
        deliver(scrub(text))
    except Exception:  # noqa: BLE001 — the reporter never breaks execution
        return "error"
    return "sent"


# ------------------------------------------------------------- settings -----
def validate_mission_settings(doc) -> tuple[dict, list[str]]:
    """Strict validation for the settings `mission` partition (evolution
    parity): unknown keys, out-of-enum tiers and out-of-range budget guards
    are refused with chat-safe messages; absent keys fall back to
    DEFAULT_SETTINGS at read time (the console always renders them)."""
    if not isinstance(doc, dict):
        return {}, ["mission must be an object"]
    errors: list[str] = []
    clean: dict = {}
    for key in doc:
        if key not in DEFAULT_SETTINGS:
            errors.append(f"unknown mission key: {key}")
    if "reporting" in doc:
        val = doc["reporting"]
        if val in REPORTING_TIERS:
            clean["reporting"] = val
        else:
            errors.append("mission.reporting must be one of: "
                          + "|".join(REPORTING_TIERS))
    for key in ("max_slices", "max_daily_model_calls"):
        if key not in doc:
            continue
        val = doc[key]
        lo, hi = BUDGET_BOUNDS[key]
        bad = (isinstance(val, bool) or not isinstance(val, (int, float))
               or (isinstance(val, float) and not val.is_integer())
               or not lo <= float(val) <= hi)
        if bad:
            errors.append(f"mission.{key} must be an integer in "
                          f"{int(lo)}..{int(hi)}")
        else:
            clean[key] = int(val)
    if "max_wall_hours" in doc:
        val = doc["max_wall_hours"]
        lo, hi = BUDGET_BOUNDS["max_wall_hours"]
        bad = (isinstance(val, bool) or not isinstance(val, (int, float))
               or not lo <= float(val) <= hi
               or round(float(val), 1) != float(val))
        if bad:
            errors.append(
                f"mission.max_wall_hours must be a number in "
                f"{lo}..{int(hi)} with at most 1 decimal")
        else:
            clean["max_wall_hours"] = float(val)
    return clean, errors


# ------------------------------------------------------------ detail view ---
def plan_detail(mission_: dict) -> dict | None:
    """Structured plan projection for GET /api/mission?id= — milestones with
    LIVE subtask rows (the flat tasks[] stays the single source of status;
    `in_progress` projects as the board's `running` word), plus the method
    exhaustion lists (methods/blockers, scrubbed at storage). Returns None
    for a mission that predates car 2 or was never planned — the board
    shows "no plan yet" honestly instead of inventing one."""
    plan = mission_.get("plan")
    if not isinstance(plan, dict):
        return None
    tasks = {t.get("id"): t for t in mission_.get("tasks") or []
             if isinstance(t, dict)}
    milestones: list[dict] = []
    raw = plan.get("milestones")
    for m in raw if isinstance(raw, list) else []:
        if not isinstance(m, dict):
            continue
        subs: list[dict] = []
        for tid in m.get("subtasks") or []:
            t = tasks.get(tid)
            if t is None:
                continue
            status = str(t.get("status") or "pending")
            if status == "in_progress":
                status = "running"
            subs.append({"id": str(t.get("id", "")),
                         "text": str(t.get("text", "")),
                         "status": status})
        milestones.append({"id": str(m.get("id", "")),
                           "title": str(m.get("title", "")),
                           "subtasks": subs})
    methods = [{"text": str(r.get("text", "")),
                "marked": bool(r.get("marked"))}
               for r in mission_.get("methods") or [] if isinstance(r, dict)]
    blockers = [str(b) for b in mission_.get("blockers") or []]
    return {"milestones": milestones, "methods": methods,
            "blockers": blockers, "generated_at": plan.get("generated_at")}


def event_text(rec: dict) -> str:
    """One event-stream row -> a short human line for the detail view.
    Best-effort by design: an unknown shape degrades to its scalar fields."""
    kind = str(rec.get("kind", ""))
    if kind == "slice":
        line = (f"slice #{rec.get('n')} mode={rec.get('mode')} "
                f"stop={rec.get('stop')} iters={rec.get('iters')}")
        if rec.get("delegate"):
            line += f" delegate={rec['delegate']}"
        prev = rec.get("preview")
        if prev:
            line += f" | {prev}"
        return line
    if kind == "report":
        return (f"report {rec.get('event')} delivered={rec.get('delivered')} "
                f"tier={rec.get('tier')}")
    if kind == "state":
        return f"state -> {rec.get('to')} via {rec.get('via')}"
    if kind == "sealed":
        return f"sealed guard={rec.get('guard')}"
    tail = ", ".join(f"{k}={rec[k]}" for k in rec
                     if k not in ("ts", "mission", "kind"))
    return tail[:400]


def detail_events(registry_path: str | Path, mission_id: str,
                  limit: int = 20) -> list[dict]:
    """Tail of the event stream for ONE mission: [{ts, kind, text}] with
    text scrubbed (the stream is model-derived). The stream file is shared
    by all missions, so rows are filtered by id; a missing/unreadable stream
    answers [] (fail-soft, like every catalog read)."""
    try:
        lines = events_path(registry_path).read_text(
            encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict] = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("mission") == mission_id:
            rows.append(rec)
    out: list[dict] = []
    for rec in rows[-int(limit):]:
        out.append({"ts": rec.get("ts"), "kind": str(rec.get("kind", "")),
                    "text": scrub(event_text(rec))})
    return out


def budget_used(mission_: dict, now: float | None = None) -> dict:
    """Honest budget usage: slice + daily counters from the registry, wall
    hours as created -> ended (terminal missions) or created -> now."""
    counters = mission_.get("counters") or {}
    created = mission_.get("created_at")
    created = float(created) if isinstance(created, (int, float)) else 0.0
    end = mission_.get("ended_at")
    if not isinstance(end, (int, float)):
        end = time.time() if now is None else now
    return {"slices": int(counters.get("slices") or 0),
            "wall_hours": round(max(0.0, float(end) - created) / 3600.0, 2),
            "daily_calls": int(counters.get("daily_calls") or 0)}


def delegate_count(tool_log) -> int:
    """How many `delegate` dispatches a slice performed (run_turn tool_log)
    — recorded on the slice event so the board can show parallel subagent
    use without parsing previews. Fail-soft on any shape."""
    try:
        return sum(1 for e in tool_log or []
                   if isinstance(e, dict) and e.get("name") == "delegate")
    except TypeError:
        return 0


# ----------------------------------------------------------------- rows ------
def public_row(mission_: dict) -> dict:
    """Console/API view — a fixed field whitelist (cron collect parity):
    no origin (personal chat id), no manifest, no internal paths, and never
    a credential of any kind. `goal` is user input and echoes back."""
    tasks = mission_.get("tasks") or []
    done_n = sum(1 for t in tasks if t.get("status") == "done")
    cur = current_task(mission_)
    counters = mission_.get("counters") or {}
    return {
        "id": str(mission_.get("id", "")),
        "goal": str(mission_.get("goal", "")),
        "state": str(mission_.get("state", "")),
        "reporting": str(mission_.get("reporting") or DEFAULT_REPORTING),
        "created_at": mission_.get("created_at"),
        "updated_at": mission_.get("updated_at"),
        "ended_at": mission_.get("ended_at"),
        "last_report_at": mission_.get("last_report_at"),
        "progress": {"done": done_n, "total": len(tasks),
                     "degraded": sum(1 for t in tasks
                                     if t.get("status") == "degraded"),
                     "current": ({"id": cur.get("id"),
                                  "status": cur.get("status")}
                                 if cur is not None else None)},
        "slices": int(counters.get("slices") or 0),
        "daily_calls": int(counters.get("daily_calls") or 0),
        "gaps": len(mission_.get("gaps") or []),
        "blocked": sum(1 for t in tasks if t.get("status") == "blocked"),
        "budget": {**DEFAULT_BUDGET,
                   **{k: v for k, v in (mission_.get("budget") or {}).items()
                      if k in DEFAULT_BUDGET}},
        "evidence": [str(e) for e in (mission_.get("evidence") or [])],
    }
