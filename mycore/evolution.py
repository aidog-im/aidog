"""mycore.evolution — the institutional self-evolution loop (Phase A).

Design anchor (docs/design/im_aidog_product_design.md, differentiation #3):
prior-art products ship "/learn" as a feature; this is a SYSTEM — learn → reflect
(daily/weekly/monthly) → evolution log → lint gate, growing more familiar
with use. Feature spec: docs/features/evolution_loop.md.

The four stages, as implemented here:

  1. LEARN   one experience -> one artifact:
             skill   -> overlay/skills/<slug>/SKILL.md  (frontmatter generated)
             memory  -> overlay/memory/EVOLUTION_learned.md (appended entry)
             Every candidate passes the LINT GATE first (credential / token /
             machine-path / personal-handle / structure rules — same
             philosophy as tests/source_lang_check.py and the publish gate)
             and the deterministic skill_route PRE-SCREEN (a near-certain
             duplicate blocks the write; a near-miss is reported as a note).
             Rejects never touch the tree and never enter the log — the
             caller records them in its runtime log only.
  2. REFLECT a period skeleton (daily/weekly/monthly) generated from FACTS
             only: evolution entries in the window, skills, memory volume,
             git commits. Conclusions are never generated — each draft carries
             a "Human conclusion" placeholder for the owner (data skeleton by
             script, prose by the owner).
  3. LOG     every accepted artifact appends exactly one line to
             overlay/evolution/EVOLUTION.md — append-only, therefore
             git-traceable (the institutional record).
  4. GATE    lint runs BEFORE any write; lint_tree() re-verifies the stored
             artifacts on demand (belt-and-braces for CI/manual sweeps).

Scheduling (dual-driven):
  * cron    — the evolution settings partition of web/settings.json
              (enabled=false by default = fail-closed opt-in; all three
              periods fire at daily_at, Beijing time). The chat-service poll
              loop calls is_due() once a minute and reflects when due, with
              catch-up after downtime (a missed window fires on the next
              opportunity, at most once per window key).
  * trigger — explicit /reflect (service), POST /api/evolution (console), or
              `python3 -m mycore.evolution --reflect <period>` (ops). All
              paths share reflect(), so the window key is marked done once.

State: <state>/evolution_state.json — env AIDOG_MC_STATE when set, else
<repo>/data/mycore_service (next to the chat-service runtime state; gitignored).

Determinism: paths are passed (repo=...), time is passed as an epoch (now=...)
and rendered in fixed UTC+08:00 (owner timezone; China has no DST), so every
stage is reproducible under test. English-only source (repo i18n policy).
Exit codes (CLI): 0 ok · 1 violations found (lint-tree) · 2 bad arguments.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

BEIJING = timezone(timedelta(hours=8), "+08:00")   # fixed offset, no DST
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Effective defaults; weekly/monthly reuse daily_at as their fire time.
DEFAULT_SETTINGS: dict = {"enabled": False, "daily_at": "09:10",
                          "weekly_day": "sun", "monthly_day": 1}

MAX_SKILL_BODY = 16000          # chars, per learned skill body
MAX_MEMORY_TEXT = 4000          # chars, per learned memory entry
MAX_DESCRIPTION = 200           # chars, one line
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
TIME_RE = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")

DUP_BLOCK_SCORE = 10            # skill_route TOP1 at/above this = duplicate
NEAR_SCORE = 4                  # at/above this = report as a near-miss note

STATE_FILE_NAME = "evolution_state.json"
PERIODS = ("daily", "weekly", "monthly")

LOG_HEADER = ("# EVOLUTION — institutional self-evolution log\n"
              "<!-- append-only; format: "
              "## <iso8601+08:00> | <kind> | <summary> -->\n\n")
ENTRY_RE = re.compile(r"^## (\S+) \| ([a-z][a-z.]*) \| (.+)$", re.M)

# ------------------------------------------------------------ lint gate ----
# Content rules (credential + redaction class). The publish gate stays the
# final authority for tracked files; these run at WRITE time so an evolution
# artifact can never be the thing that blocks a later release.
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|apikey|secret|token|passwd|password)\b\s*[=:]\s*"
    r"[\"']?[A-Za-z0-9_\-]{12,}")
_SECRET_SHAPE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_\-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9\-]{10,}|AKIA[0-9A-Z]{16})\b")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{20,}")
_WIN_PATH = re.compile(r"[A-Za-z]:\\")
_BOT_HANDLE = re.compile(r"@my_[a-z0-9_]+_bot")
_MACHINE_PATH_PARTS = ("/mnt/", "/home/", "/root/")

RULES = ("secret-assignment", "secret-shape", "private-key", "bearer",
         "machine-path", "windows-path", "bot-handle", "control-char",
         "name-slug", "description", "empty", "size-cap", "frontmatter",
         "exists", "duplicate", "io", "name-mismatch")


@dataclass
class LearnResult:
    """Outcome of one learn attempt. `ok` means the artifact EXISTS (a
    no-op "already learned" for a identical memory counts as ok)."""
    ok: bool
    path: Path | None = None
    errors: list[str] = field(default_factory=list)      # rule ids, sorted
    prescreen: list[str] = field(default_factory=list)   # human-readable notes


@dataclass
class ReflectResult:
    period: str
    path: Path
    created: bool
    facts: dict


def lint_text(text: str) -> list[str]:
    """Content rules for ANY evolution input (ordered, de-duplicated)."""
    hits: list[str] = []
    if _SECRET_ASSIGNMENT.search(text):
        hits.append("secret-assignment")
    if _SECRET_SHAPE.search(text):
        hits.append("secret-shape")
    if _PRIVATE_KEY.search(text):
        hits.append("private-key")
    if _BEARER.search(text):
        hits.append("bearer")
    if any(p in text for p in _MACHINE_PATH_PARTS):
        hits.append("machine-path")
    if _WIN_PATH.search(text):
        hits.append("windows-path")
    if _BOT_HANDLE.search(text):
        hits.append("bot-handle")
    if "\x00" in text:
        hits.append("control-char")
    return hits


def lint_skill(name: str, description: str, body: str) -> list[str]:
    """Full gate for a skill candidate (content + structure)."""
    errs = list(lint_text(f"{name}\n{description}\n{body}"))
    if not SLUG_RE.fullmatch(name or ""):
        errs.append("name-slug")
    desc = description or ""
    if not desc.strip() or "\n" in desc or len(desc) > MAX_DESCRIPTION:
        errs.append("description")
    if not (body or "").strip():
        errs.append("empty")
    elif body.lstrip().startswith("---"):
        errs.append("frontmatter")          # SKILL.md frontmatter is generated
    elif len(body) > MAX_SKILL_BODY:
        errs.append("size-cap")
    return list(dict.fromkeys(errs))


def lint_memory(text: str) -> list[str]:
    """Full gate for a memory candidate."""
    errs = list(lint_text(text or ""))
    if not (text or "").strip():
        errs.append("empty")
    elif len(text) > MAX_MEMORY_TEXT:
        errs.append("size-cap")
    return list(dict.fromkeys(errs))


# ------------------------------------------------------------- paths -------
def skills_dir(repo: Path = REPO) -> Path:
    return repo / "overlay" / "skills"


def evolution_dir(repo: Path = REPO) -> Path:
    return repo / "overlay" / "evolution"


def log_path(repo: Path = REPO) -> Path:
    return evolution_dir(repo) / "EVOLUTION.md"


def reflect_dir(repo: Path = REPO) -> Path:
    return evolution_dir(repo) / "reflect"


def learned_memory_path(repo: Path = REPO) -> Path:
    return repo / "overlay" / "memory" / "EVOLUTION_learned.md"


def default_state_path(repo: Path = REPO) -> Path:
    env = os.environ.get("AIDOG_MC_STATE")
    base = Path(env) if env else repo / "data" / "mycore_service"
    return base / STATE_FILE_NAME


def rel(path: Path, repo: Path = REPO) -> str:
    """Repo-relative POSIX path (machine paths never leave this module)."""
    try:
        return path.resolve().relative_to(Path(repo).resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


# ------------------------------------------------------------ settings -----
def validate_evolution_settings(doc) -> tuple[dict, list[str]]:
    """Strict validation for the settings `evolution` partition (all-or-
    nothing per field: bad fields are rejected, good fields are kept)."""
    if not isinstance(doc, dict):
        return {}, ["evolution must be an object"]
    errors: list[str] = []
    clean: dict = {}
    for key in doc:
        if key not in DEFAULT_SETTINGS:
            errors.append(f"unknown evolution key: {key}")
    if "enabled" in doc:
        if isinstance(doc["enabled"], bool):
            clean["enabled"] = doc["enabled"]
        else:
            errors.append("evolution.enabled must be a boolean")
    if "daily_at" in doc:
        val = doc["daily_at"]
        if isinstance(val, str) and TIME_RE.fullmatch(val):
            clean["daily_at"] = val
        else:
            errors.append('evolution.daily_at must be "HH:MM" (00:00-23:59)')
    if "weekly_day" in doc:
        val = doc["weekly_day"]
        if val in WEEKDAYS:
            clean["weekly_day"] = val
        else:
            errors.append("evolution.weekly_day must be one of: "
                          + ",".join(WEEKDAYS))
    if "monthly_day" in doc:
        val = doc["monthly_day"]
        if isinstance(val, int) and not isinstance(val, bool) and 1 <= val <= 28:
            clean["monthly_day"] = val
        else:
            errors.append("evolution.monthly_day must be an integer in 1..28 "
                          "(28 = max so every month has that day)")
    return clean, errors


def evolution_settings(doc: dict) -> dict:
    """Effective settings merged over DEFAULTS; invalid fields fall back."""
    cfg = dict(DEFAULT_SETTINGS)
    if isinstance(doc, dict) and isinstance(doc.get("evolution"), dict):
        clean, _ = validate_evolution_settings(doc["evolution"])
        cfg.update(clean)
    return cfg


def load_settings_file(repo: Path = REPO) -> dict:
    try:
        doc = json.loads((repo / "web" / "settings.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def config_for(repo: Path = REPO) -> dict:
    return evolution_settings(load_settings_file(repo))


# --------------------------------------------------------- learn: skills ---
def _skill_route_module():
    sys.path.insert(0, str(REPO / "lib"))
    try:
        import skill_route as sr  # noqa: PLC0415 — vendored sibling module
    finally:
        sys.path.pop(0)
    return sr


def prescreen(name: str, description: str,
              roots: list[Path] | None = None) -> tuple[bool, list[str]]:
    """skill_route pre-write screen -> (block, notes).

    TOP1 hitting ANOTHER skill at/above DUP_BLOCK_SCORE is a near-certain
    duplicate (block, the caller maps it to the `duplicate` rule); at/above
    NEAR_SCORE it is reported so the owner can decide; a same-directory hit
    against an EXTERNAL skill root is a name collision (block). Router
    failure degrades to "no screen, noted" — learn must stay available.
    """
    try:
        sr = _skill_route_module()
        hits = sr.route(f"{name} {description}",
                        roots if roots is not None else sr.skill_roots(None), 3)
    except Exception:  # noqa: BLE001 — screen is advisory, learn is not
        return False, ["pre-screen unavailable (router error)"]
    notes: list[str] = []
    for i, h in enumerate(hits):
        if h.get("dir") == name:
            return True, [f"same directory as external skill "
                          f"{h.get('name')} (score {h.get('score')})"]
        score = int(h.get("score") or 0)
        if i == 0 and score >= DUP_BLOCK_SCORE:
            return True, [f"likely duplicate of {h.get('name')} (score {score})"]
        if score >= NEAR_SCORE:
            notes.append(f"near {h.get('name')} (score {score})")
    return False, notes


def learn_skill(name: str, description: str, body: str, *,
                now: float | None = None, repo: Path = REPO,
                roots: list[Path] | None = None) -> LearnResult:
    """Lint + pre-screen + write overlay/skills/<slug>/SKILL.md + log."""
    name = (name or "").strip()
    description = (description or "").strip()
    body = (body or "").strip()
    errs = lint_skill(name, description, body)
    target: Path | None = None
    if not errs and SLUG_RE.fullmatch(name):
        target = skills_dir(repo) / name
        if target.exists():
            errs.append("exists")
    if errs:
        return LearnResult(ok=False, path=None, errors=sorted(errs))
    assert target is not None
    block, notes = prescreen(name, description, roots)
    if block:
        return LearnResult(ok=False, path=None, errors=["duplicate"],
                           prescreen=notes)
    text = (f"---\nname: {name}\ndescription: {description}\n---\n\n"
            f"{body}\n")
    try:
        target.mkdir(parents=True, exist_ok=False)
        (target / "SKILL.md").write_text(text, encoding="utf-8")
    except FileExistsError:
        return LearnResult(ok=False, path=None, errors=["exists"])
    except OSError:
        return LearnResult(ok=False, path=None, errors=["io"])
    md = target / "SKILL.md"
    append_log("learn.skill", rel(md, repo), now=now, repo=repo)
    return LearnResult(ok=True, path=md, errors=[], prescreen=notes)


# -------------------------------------------------------- learn: memory ----
def learn_memory(text: str, *, now: float | None = None,
                 repo: Path = REPO) -> LearnResult:
    """Lint + append one entry to overlay/memory/EVOLUTION_learned.md + log.

    An entry identical to one already present is a no-op success (the
    artifact exists; nothing is written, nothing is logged twice)."""
    text = (text or "").strip()
    errs = lint_memory(text)
    if errs:
        return LearnResult(ok=False, path=None, errors=sorted(errs))
    bnow = _bnow(now if now is not None else time.time())
    target = learned_memory_path(repo)
    try:
        existing = target.read_text(encoding="utf-8")
    except OSError:
        existing = ""
    if text in existing:
        return LearnResult(ok=True, path=target,
                           prescreen=["already learned (identical entry present)"])
    block = "\n- " + "\n  ".join(text.splitlines()) + "\n\n"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        entry_head = f"## {bnow.date().isoformat()} (learn)\n"
        if existing:
            data = existing if existing.endswith("\n") else existing + "\n"
            target.write_text(data + entry_head + block, encoding="utf-8")
        else:
            header = ("# LEARNED MEMORY — entries accepted by the evolution "
                      "lint gate (append-only; assembled after the curated "
                      "slots)\n")
            target.write_text(header + "\n" + entry_head + block,
                              encoding="utf-8")
    except OSError:
        return LearnResult(ok=False, path=None, errors=["io"])
    append_log("learn.memory",
               f"{rel(target, repo)} (+{len(text)} chars)",
               now=now, repo=repo)
    return LearnResult(ok=True, path=target)


# ---------------------------------------------------------------- log ------
def append_log(kind: str, summary: str, *, now: float | None = None,
               repo: Path = REPO) -> Path:
    """Append one entry line to overlay/evolution/EVOLUTION.md (create on
    first use). Summary must be single-line (whitespace is folded)."""
    ts = datetime.fromtimestamp(now if now is not None else time.time(),
                                BEIJING).isoformat(timespec="seconds")
    line = f"## {ts} | {kind} | {' '.join(str(summary).split())}\n"
    path = log_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = path.read_text(encoding="utf-8")
    except OSError:
        data = LOG_HEADER
    if data and not data.endswith("\n"):
        data += "\n"
    path.write_text(data + line, encoding="utf-8")
    return path


def read_log(*, repo: Path = REPO, limit: int | None = None) -> list[dict]:
    """Parsed log entries, oldest -> newest. Unparseable lines are skipped
    (append-only files survive hand edits)."""
    try:
        text = log_path(repo).read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict] = []
    for m in ENTRY_RE.finditer(text):
        ts_raw, kind, summary = m.group(1), m.group(2), m.group(3)
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        out.append({"ts": ts, "raw_ts": ts_raw, "kind": kind,
                    "summary": summary.strip()})
    return out[-limit:] if limit else out


# -------------------------------------------------------------- state ------
def load_state(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict):
        return {}
    return {k: v for k, v in doc.items()
            if k in PERIODS and isinstance(v, str)}


def save_state(path: Path, period: str, key: str, *,
               now: float | None = None) -> dict:
    st = load_state(path)
    st[period] = key
    st["updated"] = datetime.fromtimestamp(
        now if now is not None else time.time(),
        BEIJING).isoformat(timespec="seconds")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(st, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return st


def _bnow(now: float) -> datetime:
    return datetime.fromtimestamp(now, BEIJING)


def period_date_key(period: str, cfg: dict, bnow: datetime) -> str:
    """The state key for the window `now` belongs to (one run per key)."""
    if period == "daily":
        return bnow.date().isoformat()
    if period == "weekly":
        target = bnow.date() - timedelta(
            days=(bnow.weekday() - WEEKDAYS.index(cfg["weekly_day"])) % 7)
        return target.isoformat()
    if period == "monthly":
        return f"{bnow.year:04d}-{bnow.month:02d}"
    raise ValueError(f"unknown period: {period}")


def is_due(cfg: dict, now: float, state: dict) -> list[str]:
    """Periods whose window has arrived and has not run yet (ordered).

    daily  — after daily_at, once per Beijing day;
    weekly — on/after this week's weekly_day occurrence, once per occurrence;
    monthly— on/after this month's monthly_day, once per month.
    A missed window fires on the next opportunity (catch-up, never silent).
    """
    if not cfg.get("enabled"):
        return []
    bnow = _bnow(now)
    clock = str(cfg.get("daily_at", ""))
    if not TIME_RE.fullmatch(clock):                # invalid config: fail closed
        return []
    hh, mm = int(clock[:2]), int(clock[3:])
    after_fire = (bnow.hour, bnow.minute) >= (hh, mm)
    due: list[str] = []
    for period in PERIODS:
        key = period_date_key(period, cfg, bnow)
        if state.get(period) == key:
            continue
        if period == "daily":
            target = bnow.date()
        elif period == "weekly":
            target = date.fromisoformat(key)
        else:
            target = datetime(bnow.year, bnow.month,
                              int(cfg["monthly_day"])).date()
        if bnow.date() > target or (bnow.date() == target and after_fire):
            due.append(period)
    return due


# ------------------------------------------------------------ reflect -------
def window_start(period: str, bnow: datetime) -> datetime:
    if period == "daily":
        day = bnow.date()
    elif period == "weekly":
        day = bnow.date() - timedelta(days=bnow.weekday())   # Monday
    elif period == "monthly":
        day = bnow.date().replace(day=1)
    else:
        raise ValueError(f"unknown period: {period}")
    return datetime.combine(day, datetime.min.time(), tzinfo=BEIJING)


def _git_facts(repo: Path, since: datetime) -> tuple[dict | None, str | None]:
    """-> ({"count", "subjects"}, error) — git history inside the window."""
    since_iso = since.isoformat()
    try:
        cnt = subprocess.run(
            ["git", "-C", str(repo), "rev-list", "--count",
             f"--since={since_iso}", "HEAD"],
            capture_output=True, text=True, timeout=10)
        if cnt.returncode != 0:
            return None, "git history unavailable"
        lst = subprocess.run(
            ["git", "-C", str(repo), "log", "--oneline",
             f"--since={since_iso}", "--max-count=20", "HEAD"],
            capture_output=True, text=True, timeout=10)
        if lst.returncode != 0:
            return None, "git history unavailable"
        subjects = [l for l in lst.stdout.splitlines() if l.strip()]
        return {"count": int(cnt.stdout.strip() or 0),
                "subjects": subjects}, None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None, "git history unavailable"


def _skill_names(repo: Path) -> list[str]:
    try:
        children = sorted(skills_dir(repo).iterdir())
    except OSError:
        return []
    return [d.name for d in children
            if d.is_dir() and (d / "SKILL.md").is_file()]


def collect_facts(repo: Path, start: datetime, bnow: datetime) -> dict:
    """Everything a draft (or the console) may state — observed, never
    concluded."""
    entries = [e for e in read_log(repo=repo) if e["ts"] >= start]
    counts = dict(sorted(Counter(e["kind"] for e in entries).items()))
    memory: dict[str, int] = {}
    for name in ("MEMORY_curated.md", "USER_curated.md",
                 "EVOLUTION_learned.md"):
        p = repo / "overlay" / "memory" / name
        try:
            memory[name] = p.stat().st_size
        except OSError:
            continue
    git, git_err = _git_facts(repo, start)
    try:
        drafts = sorted(p.name for p in reflect_dir(repo).glob("*.md"))
    except OSError:
        drafts = []
    return {
        "window_start": start.isoformat(timespec="minutes"),
        "window_end": bnow.isoformat(timespec="minutes"),
        "entries": counts,
        "skills": _skill_names(repo),
        "memory_bytes": memory,
        "git": git,
        "git_error": git_err,
        "drafts": drafts,
    }


def draft_markdown(period: str, bnow: datetime, facts: dict) -> str:
    lines = [
        f"# Reflect — {period} — {bnow.date().isoformat()} (Beijing)",
        "",
        f"- window: {facts['window_start']} → {facts['window_end']}",
        f"- generated: {bnow.isoformat(timespec='seconds')} · skeleton only — "
        "conclusions are written by the owner",
        "",
        "## Facts",
    ]
    counts = facts.get("entries") or {}
    lines.append("- evolution entries in window: " + (
        " · ".join(f"{k}={v}" for k, v in counts.items()) or "none"))
    skills = facts.get("skills") or []
    if not skills:
        lines.append("- skills: 0")
    else:
        suffix = ", …)" if len(skills) > 12 else ")"
        lines.append(f"- skills: {len(skills)} (" + ", ".join(skills[:12]) + suffix)
    mem = facts.get("memory_bytes") or {}
    lines.append("- memory: " + (" · ".join(f"{k}={v} B" for k, v in mem.items())
                                 or "none"))
    git = facts.get("git")
    if git:
        lines.append(f"- git commits in window: {git['count']}")
        lines += [f"  - {s}" for s in git.get("subjects", [])[:20]]
    else:
        lines.append(f"- git: {facts.get('git_error') or 'unavailable'}")
    drafts = [d for d in (facts.get("drafts") or [])
              if not d.endswith(f".{period}.md")]
    if drafts:
        lines.append(f"- other drafts on file: {', '.join(drafts)}")
    lines += [
        "",
        "## Signals",
        "- _pending — nothing is auto-concluded_",
        "",
        "## Human conclusion (owner)",
        "- _pending — fill in after review; commit this file only once the "
        "conclusion is written._",
        "",
    ]
    return "\n".join(lines)


def reflect(period: str, *, now: float | None = None, repo: Path = REPO,
            state_path: Path | None = None) -> ReflectResult:
    """Generate (once per window) + log + mark the window done."""
    if period not in PERIODS:
        raise ValueError(f"unknown period: {period}")
    now = time.time() if now is None else now
    bnow = _bnow(now)
    cfg = config_for(repo)
    start = window_start(period, bnow)
    path = reflect_dir(repo) / f"{bnow.date().isoformat()}.{period}.md"
    facts = collect_facts(repo, start, bnow)
    created = not path.exists()
    if created:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(draft_markdown(period, bnow, facts),
                        encoding="utf-8")
        append_log(f"reflect.{period}", rel(path, repo), now=now, repo=repo)
    sp = Path(state_path) if state_path is not None else default_state_path(repo)
    save_state(sp, period, period_date_key(period, cfg, bnow), now=now)
    return ReflectResult(period=period, path=path, created=created,
                         facts=facts)


# ------------------------------------------------------------ status --------
def status_summary(*, repo: Path = REPO, now: float | None = None,
                   cfg: dict | None = None,
                   state_path: Path | None = None) -> dict:
    now = time.time() if now is None else now
    cfg = cfg if cfg is not None else config_for(repo)
    sp = Path(state_path) if state_path is not None else default_state_path(repo)
    state = load_state(sp)
    entries = read_log(repo=repo)
    learned = 0
    try:
        learned = learned_memory_path(repo).stat().st_size
    except OSError:
        pass
    try:
        drafts = sorted(p.name for p in reflect_dir(repo).glob("*.md"))
    except OSError:
        drafts = []
    skills = _skill_names(repo)
    return {
        "enabled": bool(cfg.get("enabled")),
        "schedule": {k: cfg[k] for k in DEFAULT_SETTINGS},
        "state": state,
        "due_now": is_due(cfg, now, state),
        "skills": len(skills),
        "skill_names": skills,
        "learned_bytes": learned,
        "log_entries": len(entries),
        "log_tail": [{"ts": e["raw_ts"], "kind": e["kind"],
                      "summary": e["summary"]}
                     for e in entries[-10:]],
        "drafts": drafts,
        "generated_at": datetime.fromtimestamp(now, BEIJING)
                        .isoformat(timespec="seconds"),
    }


# ------------------------------------------------------------ lint tree -----
def _split_frontmatter(text: str) -> tuple[str | None, str | None, str]:
    if not text.startswith("---\n"):
        return None, None, text
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return None, None, text
    name = desc = None
    for line in m.group(1).splitlines():
        mn = re.match(r"^name:\s*(.*)$", line.strip())
        md = re.match(r"^description:\s*(.*)$", line.strip())
        if mn:
            name = mn.group(1).strip().strip("'\"")
        if md:
            desc = md.group(1).strip().strip("'\"")
    return name, desc, m.group(2).strip()


def lint_tree(repo: Path = REPO) -> list[str]:
    """Re-verify every stored evolution artifact -> ["<file>: <rule>", ...].
    Skills are checked with the full skill gate; containers (learned memory,
    log, drafts) with the content rules only — their structure is generator-
    owned and their size is cumulative by design."""
    bad: list[str] = []
    for name in _skill_names(repo):
        md = skills_dir(repo) / name / "SKILL.md"
        try:
            text = md.read_text(encoding="utf-8")
        except OSError as e:
            bad.append(f"{rel(md, repo)}: unreadable ({type(e).__name__})")
            continue
        fm_name, fm_desc, body = _split_frontmatter(text)
        for rule in lint_skill(name, fm_desc or "", body):
            bad.append(f"{rel(md, repo)}: {rule}")
        if fm_name and fm_name != name:
            bad.append(f"{rel(md, repo)}: name-mismatch")
    for path, rules in (
            (learned_memory_path(repo), None),
            (log_path(repo), None)):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for rule in lint_text(text):
            bad.append(f"{rel(path, repo)}: {rule}")
    try:
        drafts = sorted(reflect_dir(repo).glob("*.md"))
    except OSError:
        drafts = []
    for d in drafts:
        try:
            text = d.read_text(encoding="utf-8")
        except OSError:
            continue
        for rule in lint_text(text):
            bad.append(f"{rel(d, repo)}: {rule}")
    return bad


# ---------------------------------------------------------------- CLI ------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mycore.evolution",
        description="institutional self-evolution loop (learn/reflect/log/lint)")
    ap.add_argument("--reflect", choices=PERIODS, metavar="PERIOD",
                    help="generate the period skeleton now (cron-equivalent)")
    ap.add_argument("--status", action="store_true",
                    help="print the evolution status (default action)")
    ap.add_argument("--lint-tree", action="store_true",
                    help="re-verify stored evolution artifacts; rc=1 on hits")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args(argv)
    if args.lint_tree:
        hits = lint_tree()
        if args.json:
            print(json.dumps({"violations": hits, "count": len(hits)},
                             indent=2))
        else:
            for h in hits:
                print(h)
            print(f"lint-tree: {len(hits)} violation(s)")
        return 1 if hits else 0
    if args.reflect:
        try:
            res = reflect(args.reflect)
        except (OSError, ValueError) as e:
            print(f"reflect failed: {type(e).__name__}: {e}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps({"period": res.period,
                              "path": rel(res.path),
                              "created": res.created,
                              "skills": len(res.facts.get("skills", []))},
                             indent=2))
        else:
            print(f"reflect {res.period}: "
                  f"{'drafted' if res.created else 'already on file'} "
                  f"-> {rel(res.path)}")
        return 0
    summ = status_summary()
    if args.json:
        print(json.dumps(summ, ensure_ascii=False, indent=2))
    else:
        sch = summ["schedule"]
        st = summ["state"]
        print(f"evolution: enabled={'on' if summ['enabled'] else 'off'} · "
              f"daily {sch['daily_at']} · weekly {sch['weekly_day']} · "
              f"monthly {sch['monthly_day']}")
        print("state: " + " ".join(
            f"{p}={st.get(p) or '-'}" for p in PERIODS)
            + (" · due now: " + ",".join(summ["due_now"])
               if summ["due_now"] else ""))
        print(f"skills={summ['skills']} · learned={summ['learned_bytes']} B · "
              f"log entries={summ['log_entries']} · drafts={len(summ['drafts'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
