"""mycore.decision — Jev SystemOne decision client (car 4, owner ruling D6).

A decision is not a chat. Jev (jevmodel.org) is a discriminative model: it
takes one `state` plus 1-8 TYPED questions and answers with distributions —
it never generates text. This module is the product's only client for it and
it deliberately does NOT go through mycore.model.ChatClient (an OpenAI chat
transport would be the wrong shape and the wrong billing model).

Wire contract (fetched from https://jevmodel.org/docs on 2026-10-29):
  POST {base}/v1/systemone
      Authorization: Bearer <slot value>   Content-Type: application/json
      Idempotency-Key: <<=100 chars>       (a retry with the SAME key settles
                                            against the original billing record)
  body  {"model": "jev-latest"?, "state": str|object|array (<= 1,000,000
        serialized chars), "questions": {name: {type, instructions, criteria?}}}
        type = noul (P(yes)) | choice (criteria: object of 2-20 option keys)
              | score (criteria: ordered 2-10 levels)
        name = [A-Za-z0-9_] <= 64 chars, 1-8 questions, instructions <= 1800,
        serialized criteria <= 2000 per question.
  200  {"model", "answers": {name: {choice|probabilities|confidence |
        noul | score}}, "usage": {"input_tokens", "output_tokens"}}
  errs {"error": {"type", "message"}} with status
        401 auth | 402 credits | 422 invalid (never billed) | 429 rate
        (120 req/min) | 502 upstream (refunded) — ONLY 429/502 are retried,
        once, with the same Idempotency-Key; 401/402/422 never retry.

Discipline (mirrors mycore.community's transport seam and mycore.model's
zero-echo rule):
  * the key lives in the dedicated secrets.env slot DECISION_API_KEY, read
    per call, and NEVER reaches a log line, a meter row, an exception message
    or a repr — presence only everywhere else;
  * the STATE and the question text never reach a log line, a meter row or an
    error message: only lengths, question names, usage and reason WORDS do;
  * an in-process transport seam (set_transport) makes every path testable
    offline; production callers never pass it;
  * honest degradation is the CALLER contract: this module raises
    DecisionError(reason) and every call site falls back to "no fast path" —
    never to a guess. Nothing here may gate a permission (Jev's confidence is
    calibration, not correctness — it may reorder attention, never approvals).

Config (settings `mycore.decision`, fail-soft to defaults):
  enabled (bool, default false = every call site stays exactly as it was),
  url ("" = https://jevmodel.org), model ("" = jev-latest),
  router_model ("" = the model-router call site is off).

English-only source (repo i18n policy).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid

from lib import profile_paths as pp

from . import meter

DEFAULT_BASE = "https://jevmodel.org"
DEFAULT_MODEL = "jev-latest"
KEY_ENV = "DECISION_API_KEY"      # the ONE slot that may hold a Jev key
API_PATH = "/v1/systemone"
TIMEOUT = 8.0                     # seconds — a decision must never stall a turn
MAX_QUESTIONS = 8
MAX_STATE_CHARS = 1_000_000       # API limit; our own call sites stay far below
MAX_NAME_LEN = 64
MAX_INSTRUCTIONS = 1800
MAX_CRITERIA_CHARS = 2000
CHOICE_MIN, CHOICE_MAX = 2, 20
SCORE_MIN, SCORE_MAX = 2, 10
IDEM_MAX = 100
RETRY_STATUS = frozenset({429, 502})   # the ONLY statuses the docs ask to retry
# A decision state stays small on purpose: billing counts the state ONCE PER
# QUESTION (estimate = sum ceil((state+question)/4)), so a bloated state is
# pure waste. Call-site states are capped well under this ceiling.
STATE_SOFT_CAP = 4000

NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")
_URL_RE = re.compile(r"^https?://[^/\s?#]+", re.IGNORECASE)
QUESTION_TYPES = ("noul", "choice", "score")
# Server error `type` values are a closed set — mapping them to reason WORDS
# keeps every failure classifiable without ever echoing the message text
# (a server message may quote the request back, and the request is ours).
_REASON_BY_TYPE = {"authentication_error": "auth",
                   "insufficient_credits": "credits",
                   "invalid_request_error": "invalid",
                   "rate_limit_error": "rate",
                   "upstream_error": "upstream"}
_REASON_BY_STATUS = {400: "invalid", 401: "auth", 402: "credits",
                     403: "auth", 404: "invalid", 405: "invalid",
                     422: "invalid", 429: "rate", 500: "upstream",
                     502: "upstream", 503: "upstream", 504: "upstream"}


class DecisionError(RuntimeError):
    """A decision could not be made. `reason` is a WORD (never a sentence,
    never input text, never a URL): disabled | no_key | bad_request |
    unreachable | auth | credits | invalid | rate | upstream."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------- config ----

def _settings() -> dict:
    """Read web/settings.json (profile-aware) -> dict; missing/corrupt = {}.
    The test seam (set_settings_for_tests) wins when installed."""
    if _settings_override is not None:
        return _settings_override
    try:
        doc = json.loads(pp.settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def config(settings: dict | None = None) -> dict:
    """Resolved decision configuration, fail-soft: anything unusable collapses
    to the safe default (off / stock base / stock model), never an exception.

    Returns {enabled, url, model, router_model, key_set, active, reason} where
    `active` is the single gate every call site consults and `reason` is the
    word that explains why it is not ("" when active)."""
    block = {}
    if settings is None:
        settings = _settings()
    if isinstance(settings, dict):
        raw = settings.get("mycore")
        if isinstance(raw, dict):
            block = raw.get("decision")
            if not isinstance(block, dict):
                block = {}
    enabled = block.get("enabled") is True
    url = block.get("url")
    url = url.strip().rstrip("/") if isinstance(url, str) else ""
    if url and not _URL_RE.match(url):
        url, enabled = "", False
    model = block.get("model")
    model = model.strip() if isinstance(model, str) else ""
    if model and not MODEL_RE.match(model):
        model = ""
    router = block.get("router_model")
    router = router.strip() if isinstance(router, str) else ""
    if router and not MODEL_RE.match(router):
        router = ""
    key_set = bool(_slot_value(KEY_ENV))
    reason = ""
    if not enabled:
        reason = "disabled"
    elif not key_set:
        reason = "no_key"
    return {"enabled": enabled, "url": url or DEFAULT_BASE,
            "model": model or DEFAULT_MODEL, "router_model": router,
            "key_set": key_set, "active": enabled and key_set,
            "reason": reason}


def _slot_value(name: str) -> str:
    """Own-line read of one secrets.env slot. The VALUE is for immediate use
    inside ask() only and never leaves that frame."""
    env = os.environ.get(name, "").strip()
    if env:
        return env
    try:
        text = pp.secrets_path().read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(rf"(?m)^{re.escape(name)}=(\S[^\r\n]*)$", text)
    return m.group(1).strip() if m else ""


# ------------------------------------------------------------- transport ----

def _urllib_transport(url: str, method: str, headers: dict,
                      payload: bytes | None, timeout: float) -> tuple[int, str]:
    """Default transport -> (status, body-text). HTTP error statuses come back
    as data (their JSON envelope is the classification source); a transport
    failure answers (0, "") — no exception text can travel further, because an
    exception string may name the URL."""
    req = urllib.request.Request(url, data=payload, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — body is best effort only
            body = ""
        return e.code, body
    except Exception:  # noqa: BLE001 — class name only, never the message
        return 0, ""


_transport = _urllib_transport


def set_transport(fn) -> None:
    """Test seam: swap the transport (same (status, text) contract)."""
    global _transport
    _transport = fn


def reset_for_tests() -> None:
    """Restore the production transport, the production settings read and
    the in-process counters."""
    global _transport, _stats, _settings_override
    _transport = _urllib_transport
    _stats = {"asked": 0, "ok": 0, "fail": 0, "tokens_in": 0, "questions": 0}
    _settings_override = None


_settings_override: dict | None = None


def set_settings_for_tests(doc: dict | None) -> None:
    """Test seam: pin the document `config()` reads (None restores the
    production read). Needed because the four call sites read config with no
    arguments — a test must be able to turn the client ON without editing
    web/settings.json. Production callers never pass it."""
    global _settings_override
    _settings_override = doc


# ------------------------------------------------------------- bookkeeping ---

_lock = threading.Lock()
_stats = {"asked": 0, "ok": 0, "fail": 0, "tokens_in": 0, "questions": 0}


def stats() -> dict:
    """In-process counters for the console: counts and tokens ONLY — never a
    state length, a question name, or anything the caller asked."""
    with _lock:
        return dict(_stats)


def _note(key: str, delta: int = 1) -> None:
    with _lock:
        _stats[key] = _stats.get(key, 0) + delta


# ------------------------------------------------------------- validation ----

def _serialize_state(state) -> tuple[str, int]:
    """State -> (wire text, length). Accepts str | dict | list, refuses
    anything else or anything over the API's 1,000,000 char ceiling."""
    if isinstance(state, str):
        text = state
    elif isinstance(state, (dict, list)):
        try:
            text = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as e:
            raise DecisionError("bad_request",
                                f"state not serializable: {type(e).__name__}") from None
    else:
        raise DecisionError("bad_request", "state must be a string, object or array")
    if not text.strip():
        raise DecisionError("bad_request", "state is empty")
    if len(text) > MAX_STATE_CHARS:
        raise DecisionError("bad_request",
                            f"state longer than {MAX_STATE_CHARS} chars")
    return text, len(text)


def _validate_criteria(qtype: str, criteria) -> object:
    """Normalize + bound one question's criteria. Returns the wire value or
    None when the type takes no criteria. Raises bad_request on any shape the
    API would 422 (checked locally so a malformed ask never spends a request)."""
    if qtype == "noul":
        if criteria is None:
            return None
        if not isinstance(criteria, (dict, list)):
            raise DecisionError("bad_request", "noul criteria must be an object or list")
        if len(json.dumps(criteria, ensure_ascii=False)) > MAX_CRITERIA_CHARS:
            raise DecisionError("bad_request", "criteria too long")
        return criteria
    if qtype == "choice":
        if not isinstance(criteria, dict):
            raise DecisionError("bad_request", "choice criteria must be an object")
        if not (CHOICE_MIN <= len(criteria) <= CHOICE_MAX):
            raise DecisionError("bad_request",
                                f"choice needs {CHOICE_MIN}-{CHOICE_MAX} options")
        out = {}
        for key, desc in criteria.items():
            if not isinstance(key, str) or not key or not isinstance(desc, str):
                raise DecisionError("bad_request", "choice options must be string pairs")
            out[key] = desc
        if len(json.dumps(out, ensure_ascii=False)) > MAX_CRITERIA_CHARS:
            raise DecisionError("bad_request", "criteria too long")
        return out
    # score
    if not isinstance(criteria, (list, tuple)):
        raise DecisionError("bad_request", "score criteria must be a list")
    if not (SCORE_MIN <= len(criteria) <= SCORE_MAX):
        raise DecisionError("bad_request",
                            f"score needs {SCORE_MIN}-{SCORE_MAX} levels")
    levels = [str(x) for x in criteria]
    if len(json.dumps(levels, ensure_ascii=False)) > MAX_CRITERIA_CHARS:
        raise DecisionError("bad_request", "criteria too long")
    return levels


def _build_body(state, questions: dict, model: str) -> tuple[bytes, int, int]:
    """Validate the whole request locally and return (body, state_len,
    question_count). Everything that could be a 422 fails HERE with
    bad_request — a refused ask never reaches the network and never spends a
    token."""
    if not isinstance(questions, dict) or not questions:
        raise DecisionError("bad_request", "questions must be a non-empty object")
    if len(questions) > MAX_QUESTIONS:
        raise DecisionError("bad_request", f"at most {MAX_QUESTIONS} questions")
    wire_state, state_len = _serialize_state(state)
    out: dict = {}
    for name, spec in questions.items():
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise DecisionError("bad_request", "question name must match [A-Za-z0-9_] (<=64)")
        if not isinstance(spec, dict):
            raise DecisionError("bad_request", f"question {name} must be an object")
        unknown = [k for k in spec if k not in ("type", "instructions", "criteria")]
        if unknown:
            raise DecisionError("bad_request", f"question {name}: unknown field")
        qtype = spec.get("type")
        if qtype not in QUESTION_TYPES:
            raise DecisionError("bad_request",
                                f"question {name}: type must be one of {'|'.join(QUESTION_TYPES)}")
        instructions = spec.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            raise DecisionError("bad_request", f"question {name}: instructions required")
        if len(instructions) > MAX_INSTRUCTIONS:
            raise DecisionError("bad_request",
                                f"question {name}: instructions too long")
        entry: dict = {"type": qtype, "instructions": instructions}
        criteria = _validate_criteria(qtype, spec.get("criteria"))
        if criteria is not None:
            entry["criteria"] = criteria
        out[name] = entry
    payload = {"state": wire_state, "questions": out}
    if model:
        payload["model"] = model
    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as e:
        raise DecisionError("bad_request",
                            f"request not serializable: {type(e).__name__}") from None
    return body, state_len, len(out)


def _classify(status: int, text: str) -> str:
    """(status, body) -> a reason WORD. The server's `error.type` (a closed
    enum) wins; otherwise the status maps through _REASON_BY_STATUS. The
    `error.message` is deliberately ignored — it may quote our request back."""
    if status == 0:
        return "unreachable"
    etype = ""
    try:
        doc = json.loads(text)
        err = doc.get("error") if isinstance(doc, dict) else None
        if isinstance(err, dict) and isinstance(err.get("type"), str):
            etype = err["type"]
    except (ValueError, AttributeError):
        etype = ""
    return _REASON_BY_TYPE.get(etype) or _REASON_BY_STATUS.get(status, "upstream")


# ------------------------------------------------------------------- ask ----

def ask(state, questions: dict, *, model: str | None = None,
        idem_key: str | None = None, settings: dict | None = None) -> dict:
    """One decision request. Returns {"model", "answers", "usage"}.

    Raises DecisionError for every failure (bad_request before any network I/O,
    disabled/no_key from the config gate, then the transport's reason word).
    One retry — same Idempotency-Key — for 429/502/transport only."""
    cfg = config(settings)
    if not cfg["active"]:
        raise DecisionError("disabled" if cfg["reason"] == "disabled" else "no_key",
                            "decision endpoint not configured")
    wire_model = model if isinstance(model, str) and model else cfg["model"]
    body, state_len, n_questions = _build_body(state, questions, wire_model)
    key = _slot_value(KEY_ENV)
    if not key:
        raise DecisionError("no_key", "decision key slot is empty")
    if idem_key is None:
        idem_key = uuid.uuid4().hex          # one per ask: a retry never double-bills
    elif not isinstance(idem_key, str) or len(idem_key) > IDEM_MAX:
        raise DecisionError("bad_request", f"idempotency key must be <= {IDEM_MAX} chars")
    url = cfg["url"] + API_PATH
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {key}",
               "Idempotency-Key": idem_key}
    _note("asked")
    _note("questions", n_questions)
    last_reason = "unreachable"
    for attempt in (0, 1):
        started = time.time()
        try:
            status, text = _transport(url, "POST", headers, body, TIMEOUT)
        except Exception:  # noqa: BLE001 — a custom seam must not break a turn
            status, text = 0, ""
        elapsed = int((time.time() - started) * 1000)
        if status == 200:
            try:
                doc = json.loads(text)
            except ValueError:
                doc = None
            answers = doc.get("answers") if isinstance(doc, dict) else None
            if not isinstance(answers, dict):
                last_reason = "upstream"
            else:
                usage = doc.get("usage") if isinstance(doc.get("usage"), dict) else {}
                tokens = usage.get("input_tokens")
                tokens = tokens if isinstance(tokens, int) else 0
                _note("ok")
                _note("tokens_in", max(0, tokens))
                meter.record(cfg["url"], wire_model, ok=True, latency_ms=elapsed,
                             in_tok=tokens if isinstance(tokens, int) else None,
                             out_tok=(usage.get("output_tokens")
                                      if isinstance(usage.get("output_tokens"), int)
                                      else None))
                return {"model": doc.get("model") or wire_model,
                        "answers": answers,
                        "usage": {"input_tokens": tokens,
                                  "output_tokens": (usage.get("output_tokens")
                                                    if isinstance(usage.get("output_tokens"), int)
                                                    else 0)}}
        last_reason = _classify(status, text)
        meter.record(cfg["url"], wire_model, ok=False, latency_ms=elapsed,
                     error_class=last_reason)
        if status not in RETRY_STATUS and status != 0:
            break                            # 401/402/422/... : never retried
        if attempt == 0:
            time.sleep(0.25)                 # one short pause (docs: short waits
            continue                         # usually beat a minute-wide window)
    _note("fail")
    raise DecisionError(last_reason)


# ------------------------------------------------------- purpose wrappers ----
# Each wrapper is a THIN, testable shaping of ask(). Every one of them is
# allowed to raise DecisionError — the call site decides whether a missing
# fast path means "keep today's behavior" (call sites 2-4) or an honest
# "unavailable" answer for the operator (call site 1).

INTENT_TABS = {
    "finance": "Prices, markets, exchange plans, approvals and money tools.",
    "home": "Smart-home devices, sensors and household automations (SNIFF board).",
    "vehicle": "Vehicle, telematics and driving data (SNIFF board).",
}


def apply_router(cfg, facts: dict | None = None, *,
                 settings: dict | None = None) -> str:
    """Call site 4 glue — try the cheap model for THIS turn, in place.

    Returns the model id actually in use. Every non-success path leaves `cfg`
    exactly as it was: router off, endpoint off, Jev said main, weak
    calibration, or the client unreachable — a turn never grows a failure
    surface and never learns WHY it did not route (the caller's job is to
    serve the turn, not to debug a synapse).
    """
    try:
        cfg_conf = config(settings)
        cheap = cfg_conf.get("router_model") or ""
        if not cheap or not cfg_conf.get("active"):
            return cfg.model
        ctx = dict(facts or {})
        ctx.setdefault("source", "console")
        ctx["main_model"] = cfg.model
        # meter facts make the router's own claim checkable afterwards: the
        # usage table still reports per-model tokens (counts only).
        try:
            usage = meter.read(days=1)
            if isinstance(usage, dict) and usage.get("available"):
                ctx["today_requests"] = usage.get("requests")
                ctx["today_in_tokens"] = usage.get("in_tokens")
        except Exception:  # noqa: BLE001 — meter is a fact source, never a gate
            pass
        chosen = route_model(cfg.model, cheap, ctx, settings=settings)
        if chosen:
            cfg.model = chosen
    except Exception:  # noqa: BLE001 — class name only; a turn must proceed
        return getattr(cfg, "model", "")
    return cfg.model


def route_intent(text: str, *, settings: dict | None = None) -> dict:
    """Call site 1 — which Extensions tab owns this request (choice).

    Raises DecisionError; the caller surfaces the reason WORD verbatim instead
    of guessing. The state carries a bounded excerpt of the operator's own
    text — nothing else leaves this machine."""
    if not isinstance(text, str) or not text.strip():
        raise DecisionError("bad_request", "text is required")
    excerpt = text.strip()[:1200]
    doc = ask({"request": excerpt},
              {"tab": {"type": "choice",
                       "instructions": "Which Extensions section owns this request?",
                       "criteria": dict(INTENT_TABS)}},
              settings=settings)
    ans = doc["answers"].get("tab") or {}
    choice = ans.get("choice")
    if choice not in INTENT_TABS:
        raise DecisionError("upstream", "answer outside the option set")
    conf = ans.get("confidence")
    return {"tab": choice, "confidence": (float(conf) if isinstance(conf, (int, float))
                                          else 0.0),
            "source": "decision"}


def plan_guard(facts: dict, *, settings: dict | None = None) -> dict:
    """Call site 2 — the order-plan guardrail (noul + score).

    Returns {within_limits, risk, risk_level, confidence}. A CALLER may only
    use this to DOWNGRADE (auto -> pending); it never approves anything and a
    low-confidence answer must fall through to the human gate that already
    exists."""
    if not isinstance(facts, dict) or not facts:
        raise DecisionError("bad_request", "facts are required")
    state = json.dumps({"facts": facts}, ensure_ascii=False,
                       separators=(",", ":"))[:STATE_SOFT_CAP]
    doc = ask(state, {
        "within_limits": {"type": "noul",
                          "instructions": ("Is this order inside the owner's "
                                           "declared limits and intent?")},
        "risk": {"type": "score",
                 "instructions": "How risky is this order for the owner?",
                 "criteria": ["negligible", "low", "moderate", "high", "severe"]},
    }, settings=settings)
    within = doc["answers"].get("within_limits") or {}
    risk = doc["answers"].get("risk") or {}
    noul = within.get("noul")
    score = risk.get("score")
    conf = risk.get("confidence") if isinstance(risk.get("confidence"), (int, float)) \
        else within.get("confidence") if isinstance(within.get("confidence"), (int, float)) \
        else 0.0
    return {"within_limits": bool(noul) if isinstance(noul, bool) else True,
            "risk": float(score) if isinstance(score, (int, float)) else 0.0,
            "confidence": float(conf)}


def triage(rows: list[dict], *, settings: dict | None = None) -> dict:
    """Call site 3 — SNIFF finding triage (one choice question per row).

    `rows` are finding dicts carrying at least `summary`/`source`/`severity`;
    AT MOST MAX_QUESTIONS of them are asked in one request (the API cap).
    Returns {signature: {"verdict", "confidence", "rank"}} where rank is the
    numeric sort key derived from the distribution (urgent > notable > noise).
    Raises DecisionError; the caller keeps the existing order on failure."""
    if not isinstance(rows, list) or not rows:
        raise DecisionError("bad_request", "rows are required")
    slice_ = rows[:MAX_QUESTIONS]
    state = json.dumps([{"severity": r.get("severity"), "source": r.get("source"),
                         "summary": r.get("summary"), "rule": r.get("rule")}
                        for r in slice_], ensure_ascii=False,
                       separators=(",", ":"))[:STATE_SOFT_CAP]
    questions = {}
    for i in range(len(slice_)):
        questions[f"f{i}"] = {
            "type": "choice",
            "instructions": "How should this finding be triaged for delivery?",
            "criteria": {"urgent": "act on it now / speak first",
                         "notable": "worth mentioning in the digest",
                         "noise": "skip it, the owner does not need this"},
        }
    doc = ask(state, questions, settings=settings)
    out: dict = {}
    weights = {"urgent": 3.0, "notable": 2.0, "noise": 1.0}
    for i, row in enumerate(slice_):
        ans = doc["answers"].get(f"f{i}")
        if not isinstance(ans, dict):
            continue
        verdict = ans.get("choice")
        if verdict not in weights:
            continue
        probs = ans.get("probabilities")
        rank = weights[verdict]
        if isinstance(probs, dict):
            # expectation over the option set keeps a shaky 0.4/0.4/0.2 answer
            # below a firm "urgent" — confidence is calibration, not correctness
            exp = 0.0
            for key, w in weights.items():
                p = probs.get(key)
                if isinstance(p, (int, float)):
                    exp += w * float(p)
            if exp:
                rank = exp
        sig = row.get("signature")
        if isinstance(sig, str) and sig:
            out[sig] = {"verdict": verdict, "rank": rank,
                        "confidence": (float(ans["confidence"])
                                       if isinstance(ans.get("confidence"), (int, float))
                                       else 0.0)}
    return out


def route_model(main_model: str, cheap_model: str, facts: dict, *,
                settings: dict | None = None) -> str | None:
    """Call site 4 — cheap model or main model for THIS request (choice).

    Returns the cheap model id ONLY when Jev chose it with enough confidence;
    every other outcome (unavailable, low confidence, Jev said main) returns
    None, which means "keep the configured model" — the safe direction. The
    caller never learns WHY, because a turn must not grow a failure surface."""
    if not isinstance(main_model, str) or not main_model:
        raise DecisionError("bad_request", "main model is required")
    if not isinstance(cheap_model, str) or not cheap_model or cheap_model == main_model:
        raise DecisionError("bad_request", "a distinct cheap model is required")
    state = json.dumps({"facts": facts}, ensure_ascii=False,
                       separators=(",", ":"))[:STATE_SOFT_CAP]
    doc = ask(state, {
        "model": {"type": "choice",
                  "instructions": ("Which model should serve this request: the "
                                   "capable main model or the cheap one?"),
                  "criteria": {"main": f"capable model ({main_model})",
                               "cheap": f"cheap model ({cheap_model})"}},
    }, settings=settings)
    ans = doc["answers"].get("model") or {}
    if ans.get("choice") != "cheap":
        return None
    conf = ans.get("confidence")
    if not isinstance(conf, (int, float)) or float(conf) < 0.6:
        return None                       # calibration is weak: stay on main
    return cheap_model
