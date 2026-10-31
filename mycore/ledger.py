"""mycore.ledger — anonymous daily usage report (D2 ledger car).

Once per UTC day the service aggregates a whitelist-exact, anonymous usage
report and POSTs it to the product's public Pages endpoint, where it lands in
the Cloudflare D1 ledger that feeds the site's models chart.

Design contract (docs/design/usage_ledger_design.md):
  * INERT BY DEFAULT — nothing exists until `activate()` runs (service / cli
    startup, next to meter.activate). An inactive ledger no-ops everywhere,
    so offline tests have zero file and zero network side effects.
  * FAIL-CLOSED SETTINGS — the MODULE default is enabled=False with an empty
    endpoint; the product seed (web/settings.example.json, provisioned by
    install.sh) is what makes fresh installs report ("collect by default",
    owner 2026-10-31). A settings document without a `ledger` block can never
    send anything.
  * PRIVACY WHITELIST — the payload is exactly {v, install_id, day, requests,
    tokens{in,out}, models[{model,requests,in,out}], scenarios{...}}. Model
    ENDPOINT URLs, prompts, file names, paths, credentials and chat ids never
    enter this module's output (the meter row's `ep` field is read only to be
    discarded).
  * DETERMINISTIC SCENARIOS — turn classification comes from the tool log
    (no dispatch = chat; http_fetch/browser_* = research; any other tool =
    coding) plus explicit events (stt = voice, community publish =
    publishing). No heuristics, no content inspection.
  * NEVER RAISES INTO A CALL PATH — a full disk, an unreadable meter file or
    a dead network degrade to a dropped report, never a chat failure.
  * ATTEMPT-ONCE-A-DAY — the daily leg marks its window BEFORE sending, so a
    broken endpoint costs one quiet attempt per day, never a retry storm.

English-only source (repo i18n policy).
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
from lib import profile_paths as pp  # noqa: E402  (profile path seam)

DEFAULT_SETTINGS: dict = {"enabled": False, "endpoint": ""}
ENDPOINT_MAX = 200                 # settings-bound endpoint cap (chars)
PAYLOAD_MAX_BYTES = 8000           # hard cap per outgoing report body
MODELS_MAX = 32                    # per-report model rows
MODEL_NAME_MAX = 100
CHECK_SEC = 600                    # leg throttle (seconds)
TIMEOUT = 10.0                     # POST timeout (seconds)
STATE_FILE = "state.json"          # {"last_report": "YYYY-MM-DD"}
ID_FILE = "install_id"             # random UUID4, resettable by deletion
TURNS_DIR = "turns"

SCENARIOS = ("chat", "coding", "research", "voice", "publishing")
# Tool names that mark a turn as research rather than coding (the fetch and
# browser faces). Anything else with a dispatch is coding by contract.
RESEARCH_TOOLS = frozenset({
    "http_fetch", "browser_open", "browser_snapshot", "browser_click",
    "browser_type", "browser_screenshot"})

_lock = threading.Lock()
_dir: Path | None = None           # None = inactive (the default)
_send_override = None              # test seam: replaces the HTTP transport


# ------------------------------------------------------------- lifecycle --

def activate(directory: Path | str | None = None) -> Path:
    """Enable reporting under `<state>/ledger/` (or `directory`, a test
    seam). Idempotent; returns the active directory. Call at process startup
    next to meter.activate()."""
    global _dir
    d = Path(directory) if directory is not None else (
        pp.state_dir(REPO) / "ledger")
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / TURNS_DIR).mkdir(parents=True, exist_ok=True)
    except OSError:
        return d
    with _lock:
        _dir = d
    return d


def deactivate() -> None:
    """Return to the inert default (tests)."""
    global _dir
    with _lock:
        _dir = None


def active() -> bool:
    with _lock:
        return _dir is not None


def active_dir() -> Path | None:
    with _lock:
        return _dir


def set_send(fn) -> None:
    """Test seam: replace the POST transport with
    `fn(url, method, headers, data, timeout) -> (status, body)` (the same
    signature community/decision seams use). None restores the default."""
    global _send_override
    _send_override = fn


# ------------------------------------------------------------ identity ----

def install_id() -> str:
    """The install's random, resettable identity. Generated on first use and
    persisted (mode 0600); deleting the file resets it. Empty string while
    inactive — never a fabricated id."""
    with _lock:
        d = _dir
    if d is None:
        return ""
    path = d / ID_FILE
    try:
        val = path.read_text(encoding="utf-8").strip()
        if val:
            return val[:64]
    except OSError:
        pass
    val = str(uuid.uuid4())
    try:
        path.write_text(val + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError:
        return ""                  # cannot persist -> report nothing rather
    return val                     # than report a new identity every day


def install_id_prefix() -> str:
    """First 8 chars of the install id for the console status row ('' when
    inactive or not yet generated). Not a secret — a local cross-check
    handle for the owner's own CF console."""
    iid = install_id()
    return iid[:8] if iid else ""


def install_id_prefix_at(state_dir: Path | str | None = None) -> str:
    """Read-only prefix from a state directory (console status face). Never
    generates an id — a console that has never seen the service reports ''."""
    d = Path(state_dir) if state_dir is not None else (
        pp.state_dir(REPO) / "ledger")
    try:
        val = (d / ID_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return val[:8] if val else ""


# --------------------------------------------------------- turn signals ---

def _day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _append_row(folder: Path, row: dict) -> None:
    """One bounded JSONL append; every failure is a silent drop."""
    try:
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return
    if len(line) > 512:
        return
    day = row.get("day") or _day_of(time.time())
    try:
        with open(folder / f"{day}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def scenario_of(tool_log: list) -> str:
    """Deterministic scenario for one completed turn (owner-ruled mapping)."""
    names = [t.get("name") for t in tool_log
             if isinstance(t, dict) and isinstance(t.get("name"), str)]
    if not names:
        return "chat"
    if any(n in RESEARCH_TOOLS for n in names):
        return "research"
    return "coding"


def note_turn(tool_log: list) -> None:
    """Record one completed turn's scenario. Inactive or bad input = no-op;
    never raises (service TURN_END calls this unconditionally)."""
    with _lock:
        d = _dir
    if d is None:
        return
    try:
        scenario = scenario_of(tool_log if isinstance(tool_log, list) else [])
    except (TypeError, ValueError):
        return
    now = time.time()
    _append_row(d / TURNS_DIR,
                {"ts": round(now, 3), "day": _day_of(now),
                 "scenario": scenario})


def note_event(kind: str) -> None:
    """Record an explicit scenario event (`voice` from stt.transcribe,
    `publishing` from the community publish path). Unknown kinds ignored."""
    if kind not in SCENARIOS:
        return
    with _lock:
        d = _dir
    if d is None:
        return
    now = time.time()
    _append_row(d / TURNS_DIR, {"ts": round(now, 3), "day": _day_of(now),
                                "scenario": kind})


# ------------------------------------------------------------- reporting --

def _read_jsonl(path: Path, cap_bytes: int = 256 * 1024) -> list[dict]:
    """Bounded tail read of one JSONL file; torn/corrupt lines skipped."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > cap_bytes:
                fh.seek(size - cap_bytes)
                raw = fh.read()
                cut = raw.find(b"\n")
                raw = raw[cut + 1:] if cut >= 0 else b""
            else:
                raw = fh.read()
    except OSError:
        return []
    rows: list[dict] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict):
            rows.append(doc)
    return rows


def _meter_rows(day: str) -> list[dict]:
    """Today's meter rows, read straight from the meter's active directory.
    The `ep` (endpoint URL) field is NEVER copied anywhere — only model name,
    ok flag and token counts leave this function."""
    try:
        from . import meter
        d = meter.active_dir()
        if d is None:
            return []
        return _read_jsonl(Path(d) / f"{day}.jsonl")
    except Exception:  # noqa: BLE001 — meter trouble must not break a leg
        return []


def build_report(day: str | None = None, state_dir: Path | None = None
                 ) -> dict | None:
    """The whitelist-exact payload for one UTC day, or None when there is
    nothing to report (no meter rows and no turn rows — a fresh install
    reports nothing rather than a wall of zeros).

    Key set is frozen: {"v","install_id","day","requests","tokens","models",
    "scenarios"}. A test asserts it byte-for-byte."""
    with _lock:
        d = _dir
    if d is None:
        return None
    day = day or _day_of(time.time())
    iid = install_id()
    if not iid:
        return None

    models: dict[str, dict] = {}
    requests = 0
    for row in _meter_rows(day):
        if row.get("ok") is not True:
            continue                      # attempts are not usage
        name = str(row.get("model") or "unknown")[:MODEL_NAME_MAX]
        requests += 1
        slot = models.setdefault(name, {"model": name, "requests": 0,
                                        "in": 0, "out": 0})
        slot["requests"] += 1
        if type(row.get("in")) is int:
            slot["in"] += max(0, row["in"])
        if type(row.get("out")) is int:
            slot["out"] += max(0, row["out"])
    model_rows = sorted(models.values(), key=lambda m: -m["requests"])[:MODELS_MAX]

    scenarios = {s: 0 for s in SCENARIOS}
    for row in _read_jsonl(Path(d) / TURNS_DIR / f"{day}.jsonl"):
        s = row.get("scenario")
        if s in scenarios:
            scenarios[s] += 1

    tok_in = sum(m["in"] for m in model_rows)
    tok_out = sum(m["out"] for m in model_rows)
    if not requests and not any(scenarios.values()):
        return None

    return {
        "v": 1,
        "install_id": iid,
        "day": day,
        "requests": requests,
        "tokens": {"in": tok_in, "out": tok_out},
        "models": model_rows,
        "scenarios": scenarios,
    }


def _default_post(url: str, method: str, headers: dict, data: bytes,
                  timeout: float) -> tuple[int, str]:
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (int(resp.status), resp.read(4096).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:      # status still matters
        return (int(e.code), "")
    except Exception:                        # noqa: BLE001 — offline/dead DNS
        return (0, "")


def send(payload: dict, endpoint: str, timeout: float = TIMEOUT) -> bool:
    """POST one report. True = the endpoint answered 2xx. Every other
    outcome (no endpoint, oversize body, network error, non-2xx) is False —
    silent at the call site; the caller logs one line if it wants to."""
    if not endpoint or not isinstance(endpoint, str):
        return False
    try:
        body = json.dumps(payload, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return False
    if len(body) > PAYLOAD_MAX_BYTES:
        return False
    post = _send_override or _default_post
    try:
        status, _ = post(endpoint, "POST",
                         {"content-type": "application/json; charset=utf-8"},
                         body, timeout)
    except Exception:                        # noqa: BLE001 — seam safety
        return False
    return isinstance(status, int) and 200 <= status < 300


def _state_read(d: Path) -> dict:
    try:
        doc = json.loads((d / STATE_FILE).read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _state_write(d: Path, doc: dict) -> None:
    try:
        (d / STATE_FILE).write_text(json.dumps(doc) + "\n", encoding="utf-8")
    except OSError:
        pass


def effective_settings(settings_doc: dict) -> dict:
    """The live `ledger` partition. Anything unusable collapses to the
    module default (enabled False, empty endpoint) — fail closed."""
    raw = settings_doc.get("ledger") if isinstance(settings_doc, dict) else None
    if not isinstance(raw, dict):
        return dict(DEFAULT_SETTINGS)
    endpoint = raw.get("endpoint")
    endpoint = endpoint.strip() if isinstance(endpoint, str) else ""
    return {"enabled": raw.get("enabled") is True,
            "endpoint": endpoint[:ENDPOINT_MAX]}


def last_report_day(state_dir: Path | None = None) -> str:
    """The last UTC day a report was attempted ('' = never). Reads the state
    directory directly so the console can answer without activating."""
    d = state_dir or (pp.state_dir(REPO) / "ledger")
    val = _state_read(Path(d)).get("last_report")
    return val if isinstance(val, str) else ""


def maybe_report(settings_doc: dict, state_dir: Path | None = None,
                 now: float | None = None) -> bool:
    """The daily leg. Fires at most once per UTC day (window marked before
    the send), only when enabled with a non-empty endpoint, and only when
    there is data to report. Returns True only on a delivered report."""
    with _lock:
        d = _dir
    if d is None:
        return False
    cfg = effective_settings(settings_doc)
    if not cfg["enabled"] or not cfg["endpoint"]:
        return False
    day = _day_of(time.time() if now is None else now)
    st = _state_read(d)
    if st.get("last_report") == day:
        return False
    payload = build_report(day=day)
    if payload is None:
        return False                        # nothing to say today -> silent
    _state_write(d, {**st, "last_report": day})   # attempt-once window first
    return send(payload, cfg["endpoint"])
