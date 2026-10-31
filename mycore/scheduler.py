"""mycore.scheduler — the natural-language cron scheduler (Phase B2).

Design anchor (task 2, owner-approved 2026-10-20): prior-art products ship
"natural-language cron" as a headline feature; ours is a SYSTEM — a job
registry that survives restarts, one shared spec parser for both entry
points (the /cron service command and the model `cron` tool), and a due
leg in the chat-service poll loop that executes and delivers (or logs,
honestly, when no target is configured).

Entry points (dual-driven, same registry):
  * command  /cron · /cron add [message|prompt] <spec> :: <text> · /cron del <id>
             (structured management, parsed by the service — no model call)
  * model    the `cron` tool (build_registry): the model translates plain
             speech ("remind me every day at 8") into a spec string, so the
             natural-language surface costs zero parsing code here.
  * ops      python3 -m mycore.scheduler --list|--add|--del [--json]

Schedule spec (one grammar for all entries; Beijing time, fixed +08:00,
no DST — same convention as mycore.evolution):
    every <N>m | every <N>h     interval  (1..10080 minutes)
    daily HH:MM                 every day
    weekly <mon..sun> HH:MM     every week on that weekday
    once YYYY-MM-DD HH:MM       one-shot (a past time is refused at create)

Job kinds:
    message   the stored text is delivered as-is (a reminder)
    prompt    the stored text runs ONE model turn (tools + constitution,
              no session persistence — each fire is a fresh turn) and the
              result is delivered

Due semantics (catch-up, at most once per window, no fire bursts — the
evolution cron leg's philosophy applied per job):
    interval  now >= (last_fired or created_at) + N*60; the anchor moves to
              the actual fire time, so downtime yields ONE late fire.
    daily     now >= the latest occurrence <= now, and both last_fired and
              created_at are older than that occurrence. The single rule
              covers: created after today's time (waits for tomorrow),
              a missed window (one catch-up fire on the next opportunity),
              already fired today (silent until tomorrow).
    weekly    same rule against the latest target-weekday occurrence
              (walking back at most 7 days).
    once      now >= at and not done -> fires exactly once, then done=true.

Execution order in the due leg: persist the mark FIRST, then execute —
a crash mid-fire can lose one delivery but never duplicates it.

Delivery (why the notebook says "MYCORE_WD_CHAT unset -> log, no error"):
    job.origin as int (the chat that created it)   -> _send
    job.origin as str "route:target" (a channel   -> same-route channel send
      turn endpoint; an unknown route or a failed
      send falls back down this list)
    else env MYCORE_WD_CHAT (int)                    -> _send
    else                                             -> log only
State: <state>/cron_jobs.json — env AIDOG_MC_STATE when set, else
<repo>/data/mycore_service (next to the chat-service runtime state,
gitignored). Corrupt/garbage entries are dropped on load (fail-soft);
an unwritable file is an honest error, never a silent success.

Caps: MAX_JOBS=50 · MAX_TEXT=4000 chars · interval 1..10080 minutes.
English-only source (repo i18n policy); user-visible strings resolve
through the locale packs in the service layer.
Exit codes (CLI): 0 ok · 1 io/operation failed · 2 bad arguments.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
from lib import profile_paths as pp  # noqa: E402  (car F: profile state seam)

BEIJING = timezone(timedelta(hours=8), "+08:00")   # fixed offset, no DST
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
KINDS = ("message", "prompt")
SOURCES = ("command", "model", "ops")

STATE_FILE_NAME = "cron_jobs.json"
MAX_JOBS = 50
MAX_TEXT = 4000
MAX_INTERVAL_MIN = 10080                 # 7 days

_TIME_RE = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
_DATE_RE = re.compile(r"([0-9]{4})-([01][0-9])-([0-3][0-9])")
_INTERVAL_RE = re.compile(r"([0-9]+)(m|h)$", re.I)
# --origin forms: a numeric chat id, or a channel endpoint "route:target"
_ROUTE_ORIGIN_RE = re.compile(r"[a-z][a-z0-9_]*:.+")


class SpecError(ValueError):
    """Raised by parse_spec/create_job for expected refusals (honest text,
    no machine paths — the message may surface in a chat reply)."""


# ------------------------------------------------------------- spec parse ---
def _parse_time(token: str) -> str:
    if not _TIME_RE.fullmatch(token or ""):
        raise SpecError(f'bad time {token!r} — expected HH:MM (00:00-23:59)')
    return token


def parse_spec(spec: str) -> dict:
    """One spec grammar -> job schedule fields ({'type': ...}).

    Raises SpecError with an operator-facing hint on any deviation; the
    /cron command and the model tool surface the same text, so a model
    mistaking 'every 30 min' for 'every 30m' gets corrected in-line."""
    tokens = (spec or "").split()
    if not tokens:
        raise SpecError("empty schedule — forms: every 30m | daily 08:00 | "
                        "weekly mon 08:00 | once 2026-10-26 09:00")
    head = tokens[0].lower()
    if head == "every":
        if len(tokens) != 2:
            raise SpecError("usage: every <N>m | every <N>h")
        m = _INTERVAL_RE.fullmatch(tokens[1])
        if not m:
            raise SpecError(f"bad interval {tokens[1]!r} — expected 30m or 2h")
        minutes = int(m.group(1)) * (60 if m.group(2).lower() == "h" else 1)
        if not 1 <= minutes <= MAX_INTERVAL_MIN:
            raise SpecError(f"interval out of range — 1..{MAX_INTERVAL_MIN} minutes")
        return {"type": "interval", "minutes": minutes}
    if head == "daily":
        if len(tokens) != 2:
            raise SpecError("usage: daily HH:MM")
        return {"type": "daily", "time": _parse_time(tokens[1])}
    if head == "weekly":
        if len(tokens) != 3:
            raise SpecError("usage: weekly <mon..sun> HH:MM")
        wd = tokens[1].lower()
        if wd not in WEEKDAYS:
            raise SpecError(f"bad weekday {tokens[1]!r} — "
                            + ",".join(WEEKDAYS))
        return {"type": "weekly", "weekday": wd, "time": _parse_time(tokens[2])}
    if head == "once":
        if len(tokens) != 3:
            raise SpecError("usage: once YYYY-MM-DD HH:MM")
        m = _DATE_RE.fullmatch(tokens[1])
        if not m:
            raise SpecError(f"bad date {tokens[1]!r} — expected YYYY-MM-DD")
        try:
            datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            raise SpecError(f"bad date {tokens[1]!r} — not a real day") from None
        return {"type": "once",
                "at": f"{tokens[1]} {_parse_time(tokens[2])}"}
    raise SpecError(f"unknown schedule {tokens[0]!r} — forms: every 30m | "
                    "daily 08:00 | weekly mon 08:00 | once 2026-10-26 09:00")


def describe(job: dict) -> str:
    """Schedule fields -> the canonical spec string (list display/echo)."""
    typ = job.get("type")
    if typ == "interval":
        m = int(job.get("minutes") or 0)
        return f"every {m // 60}h" if m and m % 60 == 0 else f"every {m}m"
    if typ == "daily":
        return f"daily {job.get('time', '')}"
    if typ == "weekly":
        return f"weekly {job.get('weekday', '')} {job.get('time', '')}"
    if typ == "once":
        return f"once {job.get('at', '')}"
    return str(typ or "?")


# ------------------------------------------------------------------ times ---
def _bj(now: float) -> datetime:
    return datetime.fromtimestamp(now, BEIJING)


def _at_epoch(at: str) -> float | None:
    """'YYYY-MM-DD HH:MM' (Beijing) -> epoch, None when unparseable."""
    try:
        return datetime.strptime(at, "%Y-%m-%d %H:%M") \
                       .replace(tzinfo=BEIJING).timestamp()
    except (ValueError, TypeError):
        return None


def _hhmm(value: str) -> tuple[int, int]:
    try:
        return int(value[:2]), int(value[3:5])
    except (ValueError, TypeError, IndexError):
        return (-1, -1)                       # never due (fail-closed)


def occurrence(job: dict, now: float) -> float | None:
    """The latest scheduled occurrence <= now (epoch), None when none yet.

    daily: today's HH:MM if passed, else yesterday's (which the created_at
    comparison then vetoes for fresh jobs). weekly: the target weekday on or
    before today, walking back one more week when today's time is pending."""
    typ = job.get("type")
    if typ == "once":
        return _at_epoch(job.get("at") or "")
    bnow = _bj(now)
    hh, mm = _hhmm(job.get("time") or "")
    if (hh, mm) < (0, 0):
        return None
    day = bnow.date()
    if typ == "weekly":
        target = WEEKDAYS.index(job.get("weekday")) \
            if job.get("weekday") in WEEKDAYS else -1
        if target < 0:
            return None
        day = day - timedelta(days=(bnow.weekday() - target) % 7)
    for candidate in (day, day - timedelta(days=1) if typ == "daily" else None):
        if candidate is None:
            continue
        dt = datetime.combine(candidate, datetime.min.time(),
                              tzinfo=BEIJING) + timedelta(hours=hh, minutes=mm)
        if dt.timestamp() <= now:
            return dt.timestamp()
    return None


# ------------------------------------------------------------------- due ----
def is_due(job: dict, now: float) -> bool:
    """True when this job's window has arrived and not yet been taken."""
    if not isinstance(job, dict) or job.get("done"):
        return False
    typ = job.get("type")
    if typ == "interval":
        try:
            minutes = int(job.get("minutes"))
        except (ValueError, TypeError):
            return False
        base = job.get("last_fired") or job.get("created_at") or 0
        return now >= float(base) + minutes * 60
    occ = occurrence(job, now)
    if occ is None:
        return False
    if typ == "once":
        return now >= occ
    anchor = max(float(job.get("last_fired") or 0),
                 float(job.get("created_at") or 0))
    return anchor < occ


def next_fire(job: dict, now: float) -> float | None:
    """Display-only next scheduled fire (epoch); None = nothing ahead."""
    if not isinstance(job, dict) or job.get("done"):
        return None
    typ = job.get("type")
    if typ == "interval":
        try:
            minutes = int(job.get("minutes"))
        except (ValueError, TypeError):
            return None
        base = float(job.get("last_fired") or job.get("created_at") or now)
        return base + minutes * 60
    if is_due(job, now):
        return now
    if typ == "once":
        return _at_epoch(job.get("at") or "")
    hh, mm = _hhmm(job.get("time") or "")
    if (hh, mm) < (0, 0):
        return None
    bnow = _bj(now)
    day = bnow.date()
    if typ == "weekly":
        target = WEEKDAYS.index(job.get("weekday")) \
            if job.get("weekday") in WEEKDAYS else -1
        if target < 0:
            return None
        day = day - timedelta(days=(bnow.weekday() - target) % 7)
    dt = datetime.combine(day, datetime.min.time(),
                          tzinfo=BEIJING) + timedelta(hours=hh, minutes=mm)
    if dt.timestamp() <= now:
        dt += timedelta(days=1 if typ == "daily" else 7)
    return dt.timestamp()


def fmt_beijing(epoch: float | None) -> str:
    """Epoch -> 'YYYY-MM-DD HH:MM' (Beijing); '-' when unknown."""
    if epoch is None:
        return "-"
    return _bj(epoch).strftime("%Y-%m-%d %H:%M")


def fmt_delta(target: float | None, now: float) -> str:
    """Relative countdown to `target` — language-neutral, locale-free units
    ('+2h13m', '+45s', '+3d4h'); '' = unknown (nothing scheduled).
    A target already reached renders '+0s' (due now: the scheduler fires it
    at the next tick, so the countdown floor is zero, never negative)."""
    if target is None:
        return ""
    secs = int(round(target - now))
    if secs <= 0:
        return "+0s"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    nz = [(v, u) for v, u in ((days, "d"), (hours, "h"), (mins, "m"),
                              (secs, "s")) if v]
    out = f"{nz[0][0]}{nz[0][1]}"
    if len(nz) > 1:
        out += f"{nz[1][0]}{nz[1][1]}"
    return "+" + out


def mark_fired(job: dict, now: float) -> None:
    """Advance the job past this fire (mutates; caller persists)."""
    if job.get("type") == "once":
        job["done"] = True
    job["last_fired"] = now


# --------------------------------------------------------------- registry ---
def default_jobs_path() -> Path:
    return pp.state_dir(REPO) / STATE_FILE_NAME


def _valid_job(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    if raw.get("type") not in ("interval", "daily", "weekly", "once"):
        return False
    if not isinstance(raw.get("id"), str) or not raw["id"]:
        return False
    return isinstance(raw.get("text"), str) and bool(raw["text"].strip())


def load_jobs(path: str | Path) -> list[dict]:
    """Registry -> job list. Absent/corrupt file or garbage entries are
    dropped (fail-soft: a bad line must not take the scheduler down)."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, list):
        return []
    return [j for j in jobs if _valid_job(j)]


def save_jobs(path: str | Path, jobs: list[dict]) -> None:
    """Atomic persist (tmp + os.replace); OSError propagates to the caller
    (an honest failure — the caller maps it to an error line/log)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps({"jobs": jobs}, ensure_ascii=False, indent=1)
                   + "\n", encoding="utf-8")
    os.replace(tmp, path)


def new_id(jobs: list[dict]) -> str:
    """Short unique id (6 hex chars, collision-checked against the table)."""
    taken = {j.get("id") for j in jobs}
    while True:
        cand = uuid.uuid4().hex[:6]
        if cand not in taken:
            return cand


def create_job(jobs: list[dict], spec: str, text: str, *,
               kind: str = "message", source: str = "command",
               origin: int | str | None = None,
               now: float | None = None) -> dict:
    """Validate + append one job (mutates `jobs`, returns it).

    Refuses (SpecError): bad spec, once in the past, empty/oversized text,
    unknown kind, or a full table. The new job's `id` is assigned here.
    `origin` is an int chat id or a str channel endpoint "route:target"
    (anything else becomes None — JSON serialization keeps both)."""
    fields = parse_spec(spec)
    if kind not in KINDS:
        raise SpecError(f"unknown kind {kind!r} — message | prompt")
    body = (text or "").strip()
    if not body:
        raise SpecError("empty job text")
    if len(body) > MAX_TEXT:
        raise SpecError(f"job text too long — {MAX_TEXT} chars max")
    if len(jobs) >= MAX_JOBS:
        raise SpecError(f"job limit reached ({MAX_JOBS}) — delete one first")
    now = time.time() if now is None else now
    if fields["type"] == "once":
        at = _at_epoch(fields["at"])
        if at is None:
            raise SpecError("bad once time")
        if at <= now:
            raise SpecError("once time has already passed")
    job = {"id": new_id(jobs), **fields, "text": body, "kind": kind,
           "origin": origin if isinstance(origin, (int, str)) else None,
           "source": source if source in SOURCES else "command",
           "created_at": now, "last_fired": None, "done": False}
    jobs.append(job)
    return job


def delete_job(jobs: list[dict], job_id: str) -> bool:
    """Remove by id (mutates); False when nothing matched."""
    for i, j in enumerate(jobs):
        if j.get("id") == job_id:
            del jobs[i]
            return True
    return False


def due_jobs(jobs: list[dict], now: float) -> list[dict]:
    """Jobs whose window has arrived, in registry order."""
    return [j for j in jobs if is_due(j, now)]


# ---------------------------------------------------------- origin context --
_ORIGIN: dict[str, int | str | None] = {}


def set_origin(chat_id: int | str | None) -> None:
    """Remember which conversation this turn belongs to (single-threaded
    poll loop; the web console leaves it unset -> jobs get origin=None).
    Values: an int Telegram chat id, or a str channel endpoint
    "route:target" — a job created in a channel turn answers on it."""
    _ORIGIN["chat"] = chat_id


def clear_origin() -> None:
    _ORIGIN.pop("chat", None)


def get_origin() -> int | str | None:
    value = _ORIGIN.get("chat")
    return value if isinstance(value, (int, str)) else None


# ------------------------------------------------------------------ CLI -----
def _parse_origin(value: str) -> int | str:
    """argparse type for --origin: a numeric chat id (-> int) or a channel
    endpoint "route:target" (-> str). Anything else raises
    ArgumentTypeError, which argparse reports as exit code 2 with this
    honest message (no machine paths, no credentials)."""
    raw = (value or "").strip()
    if re.fullmatch(r"[0-9]+", raw):
        return int(raw)
    if _ROUTE_ORIGIN_RE.fullmatch(raw):
        return raw
    raise argparse.ArgumentTypeError(
        f"bad origin {value!r} — expected a numeric chat id or "
        f"route:target (e.g. discord:C1)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mycore.scheduler",
        description="natural-language cron: job registry (list/add/del)")
    ap.add_argument("--list", action="store_true", help="list jobs (default)")
    ap.add_argument("--add", metavar="SPEC", help="create a job from a spec")
    ap.add_argument("--text", default="", help="job text (with --add)")
    ap.add_argument("--kind", default="message", choices=KINDS)
    ap.add_argument("--source", default="ops", help="command|model|ops")
    ap.add_argument("--origin", type=_parse_origin, default=None,
                    help="origin chat id or route:target channel endpoint")
    ap.add_argument("--del", dest="delete", metavar="ID", help="delete a job")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args(argv)

    path = default_jobs_path()
    if args.add:
        try:
            jobs = load_jobs(path)
            job = create_job(jobs, args.add, args.text, kind=args.kind,
                             source=args.source, origin=args.origin)
            save_jobs(path, jobs)
        except SpecError as e:
            print(f"add failed: {e}", file=sys.stderr)
            return 2
        except OSError as e:
            print(f"add failed: {type(e).__name__}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(job, ensure_ascii=False, indent=2))
        else:
            print(f"created {job['id']}: {describe(job)} · {job['kind']}"
                  f" · next {fmt_beijing(next_fire(job, time.time()))}")
        return 0
    if args.delete:
        try:
            jobs = load_jobs(path)
            ok = delete_job(jobs, args.delete)
            if ok:
                save_jobs(path, jobs)
        except OSError as e:
            print(f"del failed: {type(e).__name__}", file=sys.stderr)
            return 1
        if not ok:
            print(f"no job with id {args.delete}", file=sys.stderr)
            return 1
        print(f"deleted {args.delete}")
        return 0

    jobs = load_jobs(path)
    now = time.time()
    if args.json:
        print(json.dumps(
            [{"id": j.get("id"), "spec": describe(j), "kind": j.get("kind"),
              "type": j.get("type"), "text": j.get("text"),
              "origin": j.get("origin"), "source": j.get("source"),
              "done": bool(j.get("done")),
              "next": fmt_beijing(next_fire(j, now)),
              "due": is_due(j, now)} for j in jobs],
            ensure_ascii=False, indent=2))
    else:
        if not jobs:
            print("cron: no jobs")
        for j in jobs:
            state = " (done)" if j.get("done") else ""
            print(f"{j['id']} · {describe(j)} · {j.get('kind')} · "
                  f"next {fmt_beijing(next_fire(j, now))}{state} · "
                  f"{j.get('text', '')[:40]}")
        print(f"cron: {len(jobs)} job(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
