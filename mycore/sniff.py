"""mycore.sniff — the SNIFF sweep engine (read-only sensing, P0).

Sniff is the product's nose: a source registry (home, web, and the read-side
mcp lane over borrowed bridges), a bounded rule engine over the readings a
sweep just produced, and a durable, deduplicated findings log. Design
authority: `docs/design/sniff_engine_plan.md`.

Contracts enforced here (and asserted by mycore/tests/run.py):

* READ-ONLY by construction. Every request this module issues is a GET with
  no body. There is no service-call, command or actuation code path at all —
  the absence is a test assertion, the same trick the exchange withdrawal
  surface uses. Actuation is a later, approval-gated phase; it does not
  exist in P0.
* THE MCP LANE IS OBSERVED, NEVER DRIVEN. An `mcp` source parses the bridge
  registry (`mycore.mcp`) and counts this process's live sessions for one
  named server — configuration and status only. It calls no tool, spawns no
  process and opens no connection of its own (also asserted by tests).
  A bridge's data reaches a sweep through the `home`/`web` lanes; driving a
  borrowed server is a later, approval-gated phase.
* NO eval. Rules parse through a fixed lexer + recursive-descent parser over
  a closed operator set (`== != > < >= <= in changed`, plus a trailing
  `for <N>m` sustain window). The first parse error is reported honestly
  with the rule id and the offending position; free text never executes.
* HONEST STATUS. never-configured (missing credential slot) / pending /
  reachable / stale / error / rate-capped / disabled. A source we did not
  reach is never reported as reachable, and a failed sweep yields NO
  readings — stale data must never trigger a fresh finding.
* SECRETS. Settings carry slot NAMES only (settings-never-store-a-value
  grammar); values are read from secrets.env at call time and scrubbed out
  of every error string.
* LOCAL STATE. `<state>/sniff_state.json` (sweep bookkeeping) and
  `<state>/sniff_findings.json` (bounded findings log) — gitignored runtime
  state; personal signals never leave the machine through this module.

English comments per repo i18n policy (tests/source_lang_check.py).
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from lib import profile_paths as pp  # noqa: E402  (profile settings/state seam)

from . import mcp  # noqa: E402  (the bridge registry the mcp lane reads)
from . import mission  # noqa: E402  (one quiet-hours grammar, cron parity)

REPO = Path(__file__).resolve().parent.parent

# transport: (url, headers, payload_bytes, timeout) -> response text.
# SNIFF always passes b"" — a read sends no body (asserted by tests).
Transport = Callable[[str, dict, bytes, float], str]

# ----------------------------------------------------------------- bounds ---
ERR_SNIPPET = 160            # upstream/transport error text cap (honest, bounded)
SUMMARY_CAP = 240            # finding summary cap
TRIGGER_CAP = 160            # finding trigger (value signature) cap
MAX_SOURCES = 16
MAX_RULES = 64
MAX_ENTITIES = 64            # glob patterns per home source
MAX_READINGS = 300           # readings kept per source sweep
MAX_REGEX_CHARS = 200
MAX_WEB_SCAN_CHARS = 1_000_000   # page slice handed to the extractor
MAX_WHEN_CHARS = 500
MAX_LIST_ITEMS = 20
MAX_FINDINGS = 500           # bounded findings log (oldest dropped)
MAX_PAYLOAD_FIELDS = 12
PAYLOAD_CAP = 160
MAX_DELIVERY_CHARS = 3500    # one whole spoken batch (head + lines + tail)
DELIVERY_LINE_CAP = 200       # one finding's line inside a batch
# Rows a single batch may carry: every line can hit DELIVERY_LINE_CAP, so the
# bound holds without measuring the joined text (head/tail keep 400 chars).
BATCH_ROW_CAP = (MAX_DELIVERY_CHARS - 400) // (DELIVERY_LINE_CAP + 1)
DEFAULT_INTERVAL_S = 300
MIN_INTERVAL_S = 10
MAX_INTERVAL_S = 86400
DEFAULT_RATE_CAP = 30        # HTTP calls per source per minute
MAX_RATE_CAP = 600
DEFAULT_DIGEST_CAP = 5
MAX_DIGEST_CAP = 50
STALE_AFTER_S = 3600.0       # reachable -> stale after one hour without a read
HTTP_TIMEOUT_S = 10.0
RATE_WINDOW_S = 60.0

SEVERITIES = ("info", "warn", "high")
SOURCE_KINDS = ("home", "web", "mcp")
SEVERITY_RANK = {"info": 0, "warn": 1, "high": 2}

CONFIG_KEYS = frozenset({"enabled", "pause", "interval_s", "quiet_hours",
                         "digest_cap", "sources", "rules"})
SOURCE_KEYS = {
    "home": frozenset({"id", "kind", "enabled", "rate_cap_per_min", "url",
                       "token", "entities"}),
    "web": frozenset({"id", "kind", "enabled", "rate_cap_per_min", "url",
                      "regex", "field"}),
    # The mcp lane reads the bridge registry: no url (the name resolves it),
    # no rate cap (it issues no HTTP call of its own).
    "mcp": frozenset({"id", "kind", "enabled", "server"}),
}
RULE_KEYS = frozenset({"id", "source", "when", "severity", "speak"})

STATE_FILE = "sniff_state.json"
FINDINGS_FILE = "sniff_findings.json"

# Console/settings default partition (mission/evolution parity): the key set
# the settings reader always resolves to, so absence == these defaults == a
# silent, empty, enabled board that has no sources and therefore sweeps
# nothing. `pause` is the global PAUSE (off by default).
DEFAULT_SETTINGS: dict = {
    "enabled": True,
    "pause": False,
    "interval_s": DEFAULT_INTERVAL_S,
    "quiet_hours": "",
    "digest_cap": DEFAULT_DIGEST_CAP,
    "sources": [],
    "rules": [],
}

# Honest status vocabulary (console + tick reports use exactly these words).
ST_DISABLED = "disabled"
ST_PENDING = "pending"                  # configured, never swept yet
ST_REACHABLE = "reachable"
ST_STALE = "stale"
ST_ERROR = "error"
ST_NEVER = "never-configured"
ST_CAPPED = "rate-capped"
ST_DETACHED = "detached"                # mcp: declared, no live session

_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_SLOT_RE = re.compile(r"[A-Z][A-Z0-9_]{1,63}")
_FIELD_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
# Bridge-name grammar: byte-for-byte the mycore.mcp `name` pattern (the
# core suite asserts the two agree), used with fullmatch().
_SERVER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}")
# Lexer patterns are used with Pattern.match(text, pos): a leading "^" would
# anchor at the REAL string start, not at pos — so no anchors here, and the
# validation patterns above are applied with fullmatch().
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")

_BJ = timezone(timedelta(hours=8))      # owner timezone, cron/quiet parity


class SniffError(RuntimeError):
    """Chat-safe configuration/parsing failure. Entry numbers, field names
    and rule ids only — never a credential, never a settings value."""


# --------------------------------------------------------------- slot read ---
def _slot(name: str) -> str:
    """Resolve one credential slot: process env first, then the own-line slot
    in <profile>/secrets.env (channels/connectors style). The value is for
    immediate use only — never logged, never embedded in an error."""
    if not name:
        return ""
    val = os.environ.get(name, "").strip()
    if val:
        return val
    try:
        text = pp.secrets_path(REPO).read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(rf"(?m)^{re.escape(name)}=(\S[^\r\n]*)$", text)
    return match.group(1).strip() if match else ""


def _scrub(text: str, secret: str) -> str:
    """Bound an error line and redact any accidental credential echo."""
    out = str(text)[:ERR_SNIPPET]
    if secret:
        out = out.replace(secret, "***")
    return out


# ------------------------------------------------------------ config parse ---
def _first_unknown(keys, allowed) -> str:
    extra = sorted(set(keys) - set(allowed))
    return extra[0] if extra else ""


def _bool(block: dict, key: str, default: bool, prefix: str) -> bool:
    val = block.get(key, default)
    if not isinstance(val, bool):
        raise SniffError(f"{prefix}.{key} must be true or false")
    return val


def _int(block: dict, key: str, default: int, lo: int, hi: int,
         prefix: str) -> int:
    val = block.get(key, default)
    if isinstance(val, bool) or not isinstance(val, int) or not lo <= val <= hi:
        raise SniffError(f"{prefix}.{key} must be an integer {lo}..{hi}")
    return val


def _require_url(value: object, prefix: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SniffError(f"{prefix}url is required")
    url = value.strip()
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        raise SniffError(f"{prefix}url must be http(s) with a host") from None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise SniffError(f"{prefix}url must be http(s) with a host")
    return url.rstrip("/")


def parse_source(entry: object, *, index: int) -> dict:
    """Parse one source registry entry. First error raises SniffError with a
    chat-safe reason (entry number + field name only)."""
    prefix = f"source {index}: "
    if not isinstance(entry, dict):
        raise SniffError(f"{prefix}must be an object")
    kind = entry.get("kind")
    if not isinstance(kind, str) or kind not in SOURCE_KINDS:
        raise SniffError(f"{prefix}kind must be one of {'|'.join(SOURCE_KINDS)}")
    unknown = _first_unknown(entry, SOURCE_KEYS[kind])
    if unknown:
        raise SniffError(f"{prefix}unknown field '{unknown}'")
    sid = entry.get("id")
    if not isinstance(sid, str) or not _ID_RE.fullmatch(sid):
        raise SniffError(f"{prefix}id must be a slug (a-z, 0-9, -, _, max 32)")
    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        raise SniffError(f"{prefix}enabled must be true or false")
    out = {"id": sid, "kind": kind, "enabled": enabled}
    if kind == "mcp":
        # `server` names a bridge in settings `mycore.mcp`. The lane reads
        # that registry plus this process's live sessions — never a tool —
        # so there is no url to require and no call to meter.
        server = entry.get("server")
        if not isinstance(server, str) or not _SERVER_RE.fullmatch(server):
            raise SniffError(f"{prefix}server must be a bridge name like "
                             "ha-mcp (letters, digits, dot, underscore, dash)")
        out["server"] = server
        return out
    cap = entry.get("rate_cap_per_min", DEFAULT_RATE_CAP)
    if isinstance(cap, bool) or not isinstance(cap, int) \
            or not 1 <= cap <= MAX_RATE_CAP:
        raise SniffError(f"{prefix}rate_cap_per_min must be an integer "
                         f"1..{MAX_RATE_CAP}")
    out["rate_cap_per_min"] = cap
    out["url"] = _require_url(entry.get("url"), prefix)
    if kind == "home":
        token = entry.get("token", "")
        if token is None:
            token = ""
        if not isinstance(token, str):
            raise SniffError(f"{prefix}token must be a slot name "
                             "(settings never store a value)")
        if token and not _SLOT_RE.fullmatch(token):
            raise SniffError(f"{prefix}token must be a slot name like HA_TOKEN "
                             "(settings never store a value)")
        entities = entry.get("entities", ["*"])
        if not isinstance(entities, list) or not entities:
            raise SniffError(f"{prefix}entities must be a non-empty list of "
                             "patterns")
        if len(entities) > MAX_ENTITIES:
            raise SniffError(f"{prefix}at most {MAX_ENTITIES} entity patterns")
        clean: list[str] = []
        for pat in entities:
            if not isinstance(pat, str) or not pat.strip() or len(pat) > 128:
                raise SniffError(f"{prefix}entity patterns must be non-empty "
                                 "strings (max 128 chars)")
            clean.append(pat.strip())
        out["token"] = token
        out["entities"] = clean
    else:                                   # kind == "web"
        pattern = entry.get("regex", "")
        if pattern is None:
            pattern = ""
        if not isinstance(pattern, str) or len(pattern) > MAX_REGEX_CHARS:
            raise SniffError(f"{prefix}regex must be a string of at most "
                             f"{MAX_REGEX_CHARS} chars")
        if pattern:
            try:
                re.compile(pattern)
            except re.error as e:
                raise SniffError(f"{prefix}bad regex ({e.msg})") from None
        field = entry.get("field", "value")
        if not isinstance(field, str) or not _FIELD_RE.fullmatch(field):
            raise SniffError(f"{prefix}field must be a lowercase slug "
                             "(a-z, 0-9, _)")
        out["regex"] = pattern
        out["field"] = field
    return out


# ------------------------------------------------------------- rule lexer ---
_KEYWORDS = ("and", "or", "in", "changed", "for")
_OPS = (">=", "<=", "==", "!=", "<", ">")
_DUR_UNITS = {"m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
              "h": 3600, "hour": 3600, "hours": 3600}
MAX_DURATION_S = 86400                   # `for` at most 24h


def _lex(text: str) -> list[tuple[str, object, int]]:
    """Fixed lexer. Tokens are (kind, value, offset); any character outside
    the grammar is a first-error stop. No look-ahead beyond one token."""
    toks: list[tuple[str, object, int]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "[":
            toks.append(("[", ch, i))
            i += 1
            continue
        if ch == "]":
            toks.append(("]", ch, i))
            i += 1
            continue
        if ch == ",":
            toks.append((",", ch, i))
            i += 1
            continue
        two = text[i:i + 2]
        if two in _OPS:
            toks.append(("op", two, i))
            i += 2
            continue
        if ch in "<>":
            toks.append(("op", ch, i))
            i += 1
            continue
        if ch in "'\"":
            end = text.find(ch, i + 1)
            if end < 0:
                raise SniffError(f"unterminated string at position {i}")
            val = text[i + 1:end]
            if not val:
                raise SniffError(f"empty string at position {i}")
            toks.append(("str", val, i))
            i = end + 1
            continue
        m = _TIME_RE.match(text, i)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            if hh > 23 or mm > 59:
                raise SniffError(f"bad time literal at position {i}")
            toks.append(("time", hh * 60 + mm, i))
            i = m.end()
            continue
        m = _NUM_RE.match(text, i)
        if m:
            toks.append(("num", float(m.group()), i))
            i = m.end()
            continue
        m = _NAME_RE.match(text, i)
        if m:
            word = m.group()
            low = word.lower()
            if low in _KEYWORDS:
                toks.append(("kw", low, i))
            else:
                toks.append(("name", word, i))
            i = m.end()
            continue
        raise SniffError(f"unexpected character {ch!r} at position {i}")
    return toks


class _Parser:
    """Recursive-descent parser: expr := or ['for' dur] with `and` binding
    tighter than `or`. Reports the FIRST error with the offending position."""

    def __init__(self, toks: list[tuple[str, object, int]], text: str):
        self.toks = toks
        self.text = text
        self.i = 0

    def peek(self) -> tuple[str, object, int] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> tuple[str, object, int]:
        tok = self.peek()
        if tok is None:
            pos = len(self.text)
            raise SniffError(f"unexpected end of rule at position {pos}")
        self.i += 1
        return tok

    def fail(self, message: str) -> SniffError:
        tok = self.peek()
        pos = tok[2] if tok else len(self.text)
        return SniffError(f"{message} at position {pos}")

    def at_keyword(self, word: str) -> bool:
        tok = self.peek()
        return bool(tok and tok[0] == "kw" and tok[1] == word)

    def expr(self):
        node = self.or_expr()
        if self.at_keyword("for"):
            self.take()
            node = ("for", self.duration(), node)
        return node

    def duration(self) -> int:
        tok = self.peek()
        if not tok or tok[0] != "num":
            raise self.fail("expected a number of minutes after 'for'")
        self.take()
        minutes = tok[1]
        if minutes != int(minutes) or minutes <= 0:
            raise SniffError(f"'for' needs a positive whole number of minutes "
                             f"at position {tok[2]}")
        unit = self.peek()
        if not unit or unit[0] != "name":
            raise self.fail("expected a unit after 'for <n>' (m, min or h)")
        self.take()
        factor = _DUR_UNITS.get(str(unit[1]).lower())
        if factor is None:
            raise SniffError(f"unknown time unit {str(unit[1])!r} at position "
                             f"{unit[2]} (use m, min or h)")
        seconds = int(minutes) * factor
        if seconds > MAX_DURATION_S:
            raise SniffError(f"'for' at most {MAX_DURATION_S // 3600} hours "
                             f"at position {tok[2]}")
        return seconds

    def or_expr(self):
        node = self.and_expr()
        while self.at_keyword("or"):
            self.take()
            node = ("or", node, self.and_expr())
        return node

    def and_expr(self):
        node = self.atom()
        while self.at_keyword("and"):
            self.take()
            node = ("and", node, self.atom())
        return node

    def atom(self):
        left = self.operand()
        tok = self.peek()
        if tok is None:
            raise self.fail("expected an operator after the left-hand value")
        kind = tok[0]
        if kind == "op":
            self.take()
            return ("cmp", tok[1], left, self.operand())
        if kind == "kw" and tok[1] == "in":
            self.take()
            return ("in", left, self.list_operand())
        if kind == "kw" and tok[1] == "changed":
            self.take()
            if left[0] != "name":
                raise SniffError(f"'changed' needs a reading name at position "
                                 f"{tok[2]}")
            if left[1] == "time":
                raise SniffError("'time' cannot be used with 'changed'")
            return ("changed", left[1])
        raise self.fail("expected an operator (==, !=, <, <=, >, >=, in or "
                        "changed)")

    def operand(self):
        tok = self.peek()
        if tok is None:
            raise self.fail("expected a value or reading name")
        kind = tok[0]
        if kind == "num":
            self.take()
            return ("lit", tok[1])
        if kind == "time":
            self.take()
            return ("lit", tok[1])
        if kind == "str":
            self.take()
            return ("lit", tok[1])
        if kind == "name":
            self.take()
            return ("name", tok[1])
        if kind == "[":
            raise SniffError(f"lists are only allowed after 'in' at position "
                             f"{tok[2]}")
        raise self.fail("expected a value or reading name")

    def list_operand(self):
        tok = self.take() if self.peek() else None
        if not tok or tok[0] != "[":
            raise self.fail("expected '[' after 'in'")
        items: list[object] = []
        if self.peek() and self.peek()[0] == "]":  # empty list
            raise self.fail("empty list after 'in'")
        while True:
            tok = self.peek()
            if not tok or tok[0] not in ("num", "time", "str"):
                raise self.fail("expected a literal inside the list")
            self.take()
            items.append(tok[1])
            if len(items) > MAX_LIST_ITEMS:
                raise SniffError(f"at most {MAX_LIST_ITEMS} list items")
            nxt = self.peek()
            if not nxt:
                raise self.fail("expected ',' or ']' after the list item")
            if nxt[0] == ",":
                self.take()
                continue
            if nxt[0] == "]":
                self.take()
                return tuple(items)
            raise self.fail("expected ',' or ']' after the list item")

    def expect_end(self) -> None:
        tok = self.peek()
        if tok is not None:
            raise SniffError(f"unexpected {tok[1]!r} at position {tok[2]} "
                             "(rules are a single condition)")


def parse_when(text: str) -> tuple:
    """Parse a rule condition into a tuple AST. First error raises
    SniffError naming the position; nothing in the text is ever executed."""
    if not isinstance(text, str) or not text.strip():
        raise SniffError("rule.when must be a non-empty string")
    if len(text) > MAX_WHEN_CHARS:
        raise SniffError(f"rule.when longer than {MAX_WHEN_CHARS} chars")
    parser = _Parser(_lex(text.strip()), text.strip())
    ast = parser.expr()
    parser.expect_end()
    return ast


def parse_rule(entry: object, *, index: int, source_ids: set) -> dict:
    """Parse one rule. First error raises SniffError with entry + rule id."""
    prefix = f"rule {index}"
    if not isinstance(entry, dict):
        raise SniffError(f"{prefix}: must be an object")
    unknown = _first_unknown(entry, RULE_KEYS)
    if unknown:
        raise SniffError(f"{prefix}: unknown field '{unknown}'")
    rid = entry.get("id")
    if not isinstance(rid, str) or not _ID_RE.fullmatch(rid):
        raise SniffError(f"{prefix}: id must be a slug (a-z, 0-9, -, _, max 32)")
    where = f"{prefix} ({rid})"
    source = entry.get("source")
    if not isinstance(source, str) or source not in source_ids:
        raise SniffError(f"{where}: source must name a configured source")
    when = entry.get("when")
    try:
        ast = parse_when(when)
    except SniffError as e:
        raise SniffError(f"{where}: {e}") from None
    severity = entry.get("severity", "info")
    if severity not in SEVERITIES:
        raise SniffError(f"{where}: severity must be one of "
                         f"{'|'.join(SEVERITIES)}")
    speak = entry.get("speak", False)
    if not isinstance(speak, bool):
        raise SniffError(f"{where}: speak must be true or false")
    return {"id": rid, "source": source, "when": str(when).strip(),
            "severity": severity, "speak": speak, "ast": ast}


def parse_config(block: object, *, strict_rules: bool = True) -> dict:
    """Parse settings `mycore.sniff`.

    Sources and scalars are strict (first error raises — the console write
    gate refuses a bad document whole). `strict_rules=False` (the runtime
    sweep) isolates a bad rule instead: it is excluded and reported in
    `rule_errors`, so one hand-edited line never silences the board.
    """
    if not isinstance(block, dict):
        raise SniffError("mycore.sniff must be an object")
    unknown = _first_unknown(block, CONFIG_KEYS)
    if unknown:
        raise SniffError(f"mycore.sniff: unknown field '{unknown}'")
    prefix = "mycore.sniff"
    quiet = block.get("quiet_hours", "")
    if quiet is None:
        quiet = ""
    if not isinstance(quiet, str):
        raise SniffError(f"{prefix}.quiet_hours must be \"\" or HH:MM-HH:MM")
    reason = mission.validate_quiet_hours(quiet)
    if reason:
        raise SniffError(f"{prefix}.{reason}" if reason.startswith("quiet")
                         else f"{prefix}: {reason}")
    raw_sources = block.get("sources", [])
    if raw_sources is None:
        raw_sources = []
    if not isinstance(raw_sources, list):
        raise SniffError(f"{prefix}.sources must be a list")
    if len(raw_sources) > MAX_SOURCES:
        raise SniffError(f"{prefix}: at most {MAX_SOURCES} sources")
    sources = [parse_source(entry, index=i)
               for i, entry in enumerate(raw_sources, 1)]
    ids = [s["id"] for s in sources]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise SniffError(f"{prefix}: duplicate source id '{dupes[0]}'")
    raw_rules = block.get("rules", [])
    if raw_rules is None:
        raw_rules = []
    if not isinstance(raw_rules, list):
        raise SniffError(f"{prefix}.rules must be a list")
    if len(raw_rules) > MAX_RULES:
        raise SniffError(f"{prefix}: at most {MAX_RULES} rules")
    rules: list[dict] = []
    rule_errors: dict[str, str] = {}
    for i, entry in enumerate(raw_rules, 1):
        try:
            rules.append(parse_rule(entry, index=i, source_ids=set(ids)))
        except SniffError as e:
            if strict_rules:
                raise
            key = (str(entry.get("id")) if isinstance(entry, dict)
                   and isinstance(entry.get("id"), str) else f"rule {i}")
            rule_errors[key] = str(e)
    rids = [r["id"] for r in rules]
    dup_rules = sorted({r for r in rids if rids.count(r) > 1})
    if dup_rules:
        raise SniffError(f"{prefix}: duplicate rule id '{dup_rules[0]}'")
    return {
        "enabled": _bool(block, "enabled", True, prefix),
        "pause": _bool(block, "pause", False, prefix),
        "interval_s": _int(block, "interval_s", DEFAULT_INTERVAL_S,
                           MIN_INTERVAL_S, MAX_INTERVAL_S, prefix),
        "quiet_hours": quiet,
        "digest_cap": _int(block, "digest_cap", DEFAULT_DIGEST_CAP, 1,
                           MAX_DIGEST_CAP, prefix),
        "sources": sources,
        "rules": rules,
        "rule_errors": rule_errors,
    }


# ------------------------------------------------------------- evaluation ---
def _num(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        out = float(value)
        return out if math.isfinite(out) else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            out = float(text)
        except ValueError:
            return None
        return out if math.isfinite(out) else None
    return None


def _norm(value: object) -> str:
    if isinstance(value, str):
        return value.strip().casefold()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _equal(left: object, right: object) -> bool:
    ln, rn = _num(left), _num(right)
    if ln is not None and rn is not None:
        return ln == rn
    return _norm(left) == _norm(right)


def _value(node: tuple, readings: dict, now_min: int) -> object:
    if node[0] == "lit":
        return node[1]
    name = node[1]
    if name == "time":                    # minutes since midnight, Beijing
        return now_min
    return readings.get(name)             # missing reading -> None


def _cmp(op: str, left: object, right: object) -> bool:
    if left is None or right is None:
        return False                      # no reading, no match (honest)
    if op == "==":
        return _equal(left, right)
    if op == "!=":
        return not _equal(left, right)
    ln, rn = _num(left), _num(right)
    if ln is None or rn is None:
        return False                      # ordering needs two numbers
    if op == ">":
        return ln > rn
    if op == "<":
        return ln < rn
    if op == ">=":
        return ln >= rn
    return ln <= rn


def _eval(node: tuple, readings: dict, prev: dict, now_min: int) -> bool:
    kind = node[0]
    if kind == "cmp":
        return _cmp(node[1], _value(node[2], readings, now_min),
                    _value(node[3], readings, now_min))
    if kind == "in":
        left = _value(node[1], readings, now_min)
        if left is None:
            return False
        return any(_equal(left, item) for item in node[2])
    if kind == "changed":
        name = node[1]
        if name not in readings or name not in prev:
            return False                  # first read is the baseline
        return not _equal(readings[name], prev[name])
    if kind == "and":
        return (_eval(node[1], readings, prev, now_min)
                and _eval(node[2], readings, prev, now_min))
    if kind == "or":
        return (_eval(node[1], readings, prev, now_min)
                or _eval(node[2], readings, prev, now_min))
    return False


def evaluate(ast: tuple, *, readings: dict, prev: dict, now: float,
             now_min: int, since: float | None = None) -> tuple[bool, float | None]:
    """Evaluate a parsed rule against one sweep's readings.

    Returns (matched, sustain_since). `sustain_since` carries the `for <N>m`
    window: None means "no window, or the condition just broke"; a number is
    the timestamp the condition started holding continuously. The first
    sweep of a window never matches — a condition must hold for N minutes
    before it earns a finding.
    """
    node = ast
    window = 0
    if node[0] == "for":
        window = int(node[1])
        node = node[2]
    matched = _eval(node, readings, prev, now_min)
    if not window:
        return matched, None
    if not matched:
        return False, None
    if since is None:
        return False, now                 # window opens on this sweep
    if now - float(since) >= window:
        return True, since                # sustained; keep the anchor
    return False, since


def rule_names(ast: tuple) -> tuple[str, ...]:
    """Reading names a rule references (the payload whitelist source).
    The special `time` ident is not a reading and is excluded."""
    names: list[str] = []

    def walk(node: tuple) -> None:
        if not isinstance(node, tuple) or not node:
            return
        kind = node[0]
        if kind == "name":
            if node[1] != "time" and node[1] not in names:
                names.append(node[1])
        elif kind == "changed":
            if node[1] not in names:
                names.append(node[1])
        elif kind == "for":
            walk(node[2])
        elif kind in ("and", "or"):
            walk(node[1])
            walk(node[2])
        elif kind == "cmp":
            walk(node[2])
            walk(node[3])
        elif kind == "in":
            walk(node[1])

    walk(ast)
    return tuple(names)


# ---------------------------------------------------------------- findings ---
def findings_path() -> Path:
    return pp.state_dir(REPO) / FINDINGS_FILE


def state_path() -> Path:
    return pp.state_dir(REPO) / STATE_FILE


def sanitize_payload(payload: object) -> dict:
    """Field-whitelist + truncate: scalars only, at most MAX_PAYLOAD_FIELDS
    entries, strings cut to PAYLOAD_CAP. What a rule referenced is all a
    finding may carry (privacy: personal signals stay minimal and local)."""
    if not isinstance(payload, dict):
        return {}
    out: dict = {}
    for key in list(payload)[:MAX_PAYLOAD_FIELDS]:
        if not isinstance(key, str):
            continue
        val = payload[key]
        if isinstance(val, bool) or val is None or isinstance(val, (int, float)):
            out[key[:64]] = val
        else:
            out[key[:64]] = str(val)[:PAYLOAD_CAP]
    return out


def make_signature(source: str, rule: str, trigger: str) -> str:
    """Dedup key: source + rule + value signature (short, stable, non-reversible)."""
    raw = f"{source}\x1f{rule}\x1f{trigger}".encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:16]


def upsert_finding(rows: list[dict], *, source: str, rule: str,
                   severity: str, summary: str, trigger: str, payload: dict,
                   now: float) -> tuple[list[dict], bool]:
    """Insert or refresh one finding. Same signature = same observation:
    first_seen is preserved, last_seen/count advance, severity escalates to
    the loudest seen. Returns (rows, created)."""
    if severity not in SEVERITIES:
        severity = "info"
    sig = make_signature(source, rule, trigger)
    for row in rows:
        if row.get("signature") == sig:
            row["last_seen"] = now
            try:
                row["count"] = int(row.get("count", 1)) + 1
            except (TypeError, ValueError):
                row["count"] = 1
            old = row.get("severity")
            if old not in SEVERITIES or SEVERITY_RANK[severity] > SEVERITY_RANK.get(old, 0):
                row["severity"] = severity
            return rows, False
    rows.append({
        "signature": sig,
        "source": source,
        "rule": rule,
        "severity": severity,
        "summary": str(summary)[:SUMMARY_CAP],
        "trigger": str(trigger)[:TRIGGER_CAP],
        "payload": sanitize_payload(payload),
        "first_seen": now,
        "last_seen": now,
        "count": 1,
        "ack": False,
        "delivered": False,
    })
    if len(rows) > MAX_FINDINGS:
        del rows[:len(rows) - MAX_FINDINGS]
    return rows, True


def load_findings(path: Path | None = None) -> list[dict]:
    """Findings log -> list. Absent/corrupt file or a non-list document is an
    honest empty log (fail-soft: a bad line must not take the board down)."""
    try:
        doc = json.loads((path or findings_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = doc.get("findings") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows[:MAX_FINDINGS]:
        if isinstance(row, dict) and isinstance(row.get("signature"), str):
            out.append(row)
    return out


def save_findings(rows: list[dict], path: Path | None = None) -> None:
    """Atomic persist (tmp + os.replace); OSError propagates to the caller."""
    target = path or findings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps({"findings": rows[:MAX_FINDINGS]},
                              ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    os.replace(tmp, target)


def ack_finding(rows: list[dict], signature: str) -> bool:
    """Mark one finding acknowledged. True = found and marked."""
    for row in rows:
        if row.get("signature") == signature:
            row["ack"] = True
            return True
    return False


# ---------------------------------------------------------------- delivery ---
def quiet_now(window: str, now: float) -> bool:
    """Is `now` inside the quiet window? Same grammar and clock as the
    cron/mission lanes (mission._quiet_active): Beijing time, midnight
    wrapping, ""/malformed = off — a hand-edited window never swallows a
    batch, it just waits for the first non-quiet window."""
    return bool(mission._quiet_active(window, now))


def _delivery_line(row: dict) -> str:
    """One bounded line for a spoken finding: `[severity] source — summary`.
    Payloads never speak (privacy: a batch carries the rule's own words, not
    the readings behind them)."""
    line = (f"[{row.get('severity') or 'info'}] {row.get('source') or '?'} — "
            f"{row.get('summary') or row.get('trigger') or ''}")
    return line[:DELIVERY_LINE_CAP]


def delivery_lines(rows: list[dict]) -> list[str]:
    """The spoken lines for one taken batch (pure formatting)."""
    return [_delivery_line(row) for row in rows]


def mark_delivered(findings: list[dict], rows: list[dict]) -> int:
    """Flip `delivered` on one batch. Returns how many rows changed (rows
    already delivered are never re-flipped, so a repeat observation of the
    same signature never speaks twice)."""
    sigs = {row.get("signature") for row in rows}
    changed = 0
    for row in findings:
        if row.get("signature") in sigs and not row.get("delivered"):
            row["delivered"] = True
            changed += 1
    return changed


def delivery_take(cfg: dict, *, now: float,
                  findings: list[dict] | None = None) -> dict:
    """Take this window's spoken batch out of the findings log.

    Selection gates (in order): per-rule `speak`, quiet hours, then
    `digest_cap` — oldest first, bounded by BATCH_ROW_CAP, the remainder
    stays for the next window. The batch is marked delivered and PERSISTED
    here, before the caller sends anything: the cron/mission mark-first
    philosophy (a crash loses one batch, never duplicates one). `enabled`
    and `pause` are the CALLER's gates — the service leg returns before this
    seam when the board is off or paused.

    Returns {"lines", "count", "more", "held", "error"}: `held` is
    "quiet" (batch waits for the first non-quiet window) or "persist" (the
    mark could not be written — nothing is sent, nothing is lost) or "".

    Ownership rule (tick parity): pass `findings` for a hermetic call
    (nothing is read, nothing is written); passing None loads the log and
    persists the marks."""
    own = findings is None
    findings = load_findings() if findings is None else findings
    if not isinstance(findings, list):
        findings = []
    speak = {r["id"] for r in cfg.get("rules", []) if r.get("speak")}
    rows = [row for row in findings
            if not row.get("delivered") and row.get("rule") in speak]
    rows.sort(key=lambda row: _num(row.get("first_seen")) or 0.0)
    if not rows:
        return {"lines": [], "count": 0, "more": 0, "held": "", "error": ""}
    if quiet_now(str(cfg.get("quiet_hours") or ""), now):
        return {"lines": [], "count": 0, "more": len(rows),
                "held": "quiet", "error": ""}
    cap = max(1, min(int(cfg.get("digest_cap", DEFAULT_DIGEST_CAP)),
                     BATCH_ROW_CAP))
    batch, rest = rows[:cap], rows[cap:]
    marked = mark_delivered(findings, batch)
    if own:
        try:
            save_findings(findings)
        except OSError as e:  # noqa: BLE001 — the marks stay in memory only
            for row in batch:                 # roll back: nothing was sent
                row["delivered"] = False
            return {"lines": [], "count": 0, "more": len(rows),
                    "held": "persist",
                    "error": f"persist findings failed: "
                             f"{type(e).__name__}"[:ERR_SNIPPET]}
    return {"lines": delivery_lines(batch), "count": marked,
            "more": len(rest), "held": "", "error": ""}


# ------------------------------------------------------------ state bookkeeping ---
def load_state(path: Path | None = None) -> dict:
    """Sweep bookkeeping -> dict with the documented defaults filled in."""
    doc: dict = {}
    try:
        raw = json.loads((path or state_path()).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            doc = raw
    except (OSError, ValueError):
        doc = {}
    state = {
        "last_swept": float(doc.get("last_swept") or 0.0),
        "sources": doc.get("sources") if isinstance(doc.get("sources"), dict) else {},
        "sustain": doc.get("sustain") if isinstance(doc.get("sustain"), dict) else {},
        "prev": doc.get("prev") if isinstance(doc.get("prev"), dict) else {},
    }
    return state


def save_state(state: dict, path: Path | None = None) -> None:
    target = path or state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    os.replace(tmp, target)


def default_state() -> dict:
    return {"last_swept": 0.0, "sources": {}, "sustain": {}, "prev": {}}


# ----------------------------------------------------------------- sources ---
def _urllib_transport(url: str, headers: dict, _payload: bytes,
                      timeout: float) -> str:
    """Default read transport. The payload argument is ignored on purpose:
    SNIFF never sends a body (read-only contract)."""
    req = urllib.request.Request(url, data=None, method="GET")
    for key, val in headers.items():
        req.add_header(key, val)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        snippet = ""
        try:
            snippet = e.read().decode("utf-8", "replace").strip()[:ERR_SNIPPET]
        except OSError:
            pass
        raise SniffError(f"HTTP {e.code} {e.reason}: {snippet}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SniffError(f"transport failure: {type(e).__name__}") from None


def _read_value(state: object) -> object:
    """Entity state -> a reading: a finite number stays a number, anything
    else stays the trimmed string (honest: we never guess units)."""
    if isinstance(state, (int, float)) and not isinstance(state, bool):
        return state
    text = "" if state is None else str(state).strip()
    num = _num(text)
    if num is not None and text not in (".", "-"):
        return num
    return text


def _sweep_home(src: dict, *, transport: Transport,
                slot: Callable[[str], str]) -> tuple[dict, str, str]:
    """GET <url>/api/states, filter by entity globs -> readings.
    -> (readings, status, error)."""
    token = slot(src.get("token", "")) if src.get("token") else ""
    if not token:
        return {}, ST_NEVER, ""
    url = src["url"] + "/api/states"
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/json"}
    try:
        raw = transport(url, headers, b"", HTTP_TIMEOUT_S)
        doc = json.loads(raw)
        if not isinstance(doc, list):
            raise SniffError("home source: unexpected response shape")
    except Exception as e:  # noqa: BLE001 — one failed source never breaks a sweep
        return {}, ST_ERROR, _scrub(str(e), token)
    patterns = [p.casefold() for p in src.get("entities", ["*"])]
    readings: dict = {}
    for item in doc:
        if not isinstance(item, dict):
            continue
        eid = str(item.get("entity_id") or "").strip()
        if not eid:
            continue
        if not any(fnmatch.fnmatchcase(eid.casefold(), pat)
                   for pat in patterns):
            continue
        readings[eid] = _read_value(item.get("state"))
        if len(readings) >= MAX_READINGS:
            break
    return readings, ST_REACHABLE, ""


def _sweep_web(src: dict, *, transport: Transport) -> tuple[dict, str, str]:
    """GET one URL -> `page_hash` plus the optional regex extraction.
    -> (readings, status, error)."""
    url = src["url"]
    headers = {"Accept": "text/html,application/json,*/*",
               "User-Agent": "aidog-sniff/1"}
    try:
        body = transport(url, headers, b"", HTTP_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        return {}, ST_ERROR, _scrub(str(e), "")
    if not isinstance(body, str):
        return {}, ST_ERROR, "web source: non-text response"
    readings = {"page_hash": hashlib.sha256(
        body.encode("utf-8", "replace")).hexdigest()[:12]}
    pattern = src.get("regex") or ""
    if pattern:
        match = re.search(pattern, body[:MAX_WEB_SCAN_CHARS])
        if match:
            readings[src.get("field", "value")] = _read_value(
                match.group(1) if match.lastindex else match.group(0))
    return readings, ST_REACHABLE, ""


def _sweep_mcp(src: dict, *, raw: str,
               sessions: Callable[[str], list]) -> tuple[dict, str, str]:
    """Read one borrowed bridge through the read-side lane: parse the
    registry text (settings `mycore.mcp`) and count this process's live
    sessions for the named server. Configuration and status only — no tool
    is called, no process is spawned, nothing leaves the machine.
    -> (readings, status, error)."""
    if not isinstance(raw, str) or not raw.strip():
        return {}, ST_NEVER, ""         # no registry at all = nothing declared
    try:
        cfgs = mcp.parse_config(raw)
    except ValueError as e:              # one broken registry, honest error
        return {}, ST_ERROR, str(e)[:ERR_SNIPPET]
    named = next((c for c in cfgs if c.name == src["server"]), None)
    if named is None:
        return {}, ST_NEVER, ""         # this bridge is simply not declared
    live = list(sessions(src["server"]))
    slots = sum(1 for val in list(named.env.values())
                + list(named.headers.values())
                if isinstance(val, str) and val.startswith("$"))
    readings = {
        "servers": len(cfgs),                     # bridges declared overall
        "transport": str(named.transport),        # "stdio" | "http"
        "sessions": len(live),                    # live sessions right now
        "tools": sum(len(getattr(s, "tools", None) or []) for s in live),
        "filters": len(named.allow) + len(named.deny),
        "slots": slots,                           # $SLOT references (names only)
    }
    return readings, (ST_REACHABLE if live else ST_DETACHED), ""


def source_status(src: dict, entry: dict | None, now: float) -> dict:
    """Console-facing honest status for one source. `pending` means
    configured but never swept; `stale` means the last successful read is
    older than STALE_AFTER_S. A missing slot surfaces as never-configured."""
    if not src.get("enabled", True):
        status = ST_DISABLED
    elif not entry or not entry.get("status"):
        status = ST_PENDING
    else:
        status = str(entry.get("status"))
        last_ok = _num(entry.get("last_ok")) or 0.0
        if status == ST_REACHABLE and now - last_ok > STALE_AFTER_S:
            status = ST_STALE
    last_ok = _num((entry or {}).get("last_ok")) or 0.0
    return {
        "id": src.get("id"),
        "kind": src.get("kind"),
        "status": status,
        "reads": int(_num((entry or {}).get("reads")) or 0),
        "last_ok": last_ok,
        "age_s": (now - last_ok) if last_ok else None,
        "error": str((entry or {}).get("last_error") or "")[:ERR_SNIPPET],
    }


# ------------------------------------------------------------------- tick ---
def settings_block(repo: Path | None = None) -> dict:
    """The `mycore.sniff` settings partition. {} on any read/parse failure
    (fail closed on absence, never crash — the service reader's contract)."""
    try:
        doc = json.loads(pp.settings_path(repo or REPO).read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict):
        return {}
    mc = doc.get("mycore")
    block = mc.get("sniff") if isinstance(mc, dict) else None
    return block if isinstance(block, dict) else {}


def _now_min(now: float) -> int:
    """Minutes since midnight, Beijing time (owner timezone, cron parity)."""
    dt = datetime.fromtimestamp(now, _BJ)
    return dt.hour * 60 + dt.minute


def _base_report(now: float, skipped: str = "", error: str = "") -> dict:
    return {"at": now, "skipped": skipped, "error": error, "sources": {},
            "rules_checked": 0, "rules_matched": 0, "rule_errors": {},
            "findings_new": 0, "findings_dup": 0, "findings_total": 0}


def tick(now: float | None = None, *, block: dict | None = None,
         transport: Transport | None = None,
         slot: Callable[[str], str] | None = None,
         state: dict | None = None,
         findings: list[dict] | None = None,
         force: bool = False,
         mcp_raw: str | None = None) -> dict:
    """One sniff sweep: read every enabled source, evaluate the rules,
    record findings, report honestly.

    * `block`   — the raw settings `mycore.sniff` dict (None = read settings).
    * `state` / `findings` — pass containers to run hermetically (tests);
      passing None means "load from the state files and persist back", so the
      caller only ever sees one ownership rule: what you did not pass, this
      function persists.
    * `force`   — the console's "sweep now" beats the interval gate (the
      per-source rate cap still applies; force never means "unlimited").
    * `mcp_raw` — the raw `mycore.mcp` bridge registry the `mcp` lane reads
      (None = resolve it exactly the way mycore.mcp itself does, and only
      when an mcp source is actually configured). The lane parses that text
      and counts live sessions; it never calls a tool.
    * Never raises: a bad config, a dead source, a failed persist — all come
      back as an honest report key.

    Read-only: every request is a GET with an empty payload; no actuation
    path exists in this module (asserted by the test suite).
    """
    t = float(now if now is not None else time.time())
    own_state = state is None
    own_findings = findings is None
    state = load_state() if state is None else state
    findings = load_findings() if findings is None else findings
    if not isinstance(state, dict):
        state = default_state()
    if not isinstance(findings, list):
        findings = []
    report = _base_report(t)

    raw = block if block is not None else settings_block()
    try:
        cfg = parse_config(raw, strict_rules=False)
    except SniffError as e:
        report["skipped"] = "config"
        report["error"] = str(e)[:ERR_SNIPPET]
        return report
    report["rule_errors"] = dict(cfg["rule_errors"])
    if not cfg["enabled"]:
        report["skipped"] = "disabled"
        return report
    if cfg["pause"]:
        report["skipped"] = "paused"
        return report
    last = float(state.get("last_swept") or 0.0)
    if not force and last > 0 and t >= last and t - last < cfg["interval_s"]:
        report["skipped"] = "interval"
        return report
    if mcp_raw is None and any(s["kind"] == "mcp" for s in cfg["sources"]):
        mcp_raw = mcp.raw_config(REPO)

    transport = transport or _urllib_transport
    slot = slot or _slot
    sources = state.setdefault("sources", {})
    prev = state.setdefault("prev", {})
    sustain = state.setdefault("sustain", {})
    now_min = _now_min(t)
    readings_by_source: dict[str, dict] = {}
    matched = 0
    new = dup = 0

    for src in cfg["sources"]:
        sid = src["id"]
        entry = sources.setdefault(sid, {"reads": 0, "calls": [],
                                         "last_ok": 0.0, "last_error": "",
                                         "status": ""})
        calls = [c for c in entry.get("calls", []) if _num(c) is not None
                 and t - float(c) < RATE_WINDOW_S]
        entry["calls"] = calls
        if not src["enabled"]:
            report["sources"][sid] = {"status": ST_DISABLED, "readings": 0,
                                      "error": ""}
            continue
        is_mcp = src["kind"] == "mcp"
        if not is_mcp and len(calls) >= src["rate_cap_per_min"]:
            report["sources"][sid] = {"status": ST_CAPPED, "readings": 0,
                                      "error": ""}
            continue
        if src["kind"] == "home":
            readings, status, error = _sweep_home(src, transport=transport,
                                                  slot=slot)
        elif is_mcp:
            readings, status, error = _sweep_mcp(
                src, raw=mcp_raw or "", sessions=mcp.live_sessions)
        else:
            readings, status, error = _sweep_web(src, transport=transport)
        if status != ST_NEVER and not is_mcp:
            calls.append(t)              # the mcp lane meters no HTTP call
        entry["status"] = status
        entry["last_error"] = error
        # A declared bridge whose read succeeded yields readings whether it
        # is attached (reachable) or not (detached): `sessions == 0` is a
        # first-class reading, and a finding must be able to say so. An
        # unreadable source (never-configured / error) still feeds rules
        # nothing — same honesty rule as home and web.
        if status in (ST_REACHABLE, ST_DETACHED):
            entry["last_ok"] = t
            entry["reads"] = int(_num(entry.get("reads")) or 0) + len(readings)
            prev[sid] = dict(readings)
            readings_by_source[sid] = readings
        else:
            # Never evaluate a rule against data we did not just read.
            readings_by_source[sid] = {}
        report["sources"][sid] = {"status": status,
                                  "readings": len(readings),
                                  "error": error[:ERR_SNIPPET]}

    for rule in cfg["rules"]:
        rid = rule["id"]
        readings = readings_by_source.get(rule["source"], {})
        try:
            ok, since = evaluate(rule["ast"], readings=readings,
                                 prev=prev.get(rule["source"], {}), now=t,
                                 now_min=now_min, since=sustain.get(rid))
        except Exception as e:  # noqa: BLE001 — one bad rule never breaks a sweep
            report["rule_errors"][rid] = (
                f"evaluation failure: {type(e).__name__}"[:ERR_SNIPPET])
            continue
        if since is None:
            sustain.pop(rid, None)
        else:
            sustain[rid] = since
        report["rules_checked"] += 1
        if not ok:
            continue
        matched += 1
        names = rule_names(rule["ast"])
        trigger = " ".join(f"{n}={readings[n]}" for n in names
                           if n in readings)[:TRIGGER_CAP] or "condition"
        payload = {n: readings[n] for n in names if n in readings}
        summary = f"{rule['when']} -> {trigger}"
        findings, created = upsert_finding(
            findings, source=rule["source"], rule=rid,
            severity=rule["severity"], summary=summary, trigger=trigger,
            payload=payload, now=t)
        if created:
            new += 1
        else:
            dup += 1

    state["last_swept"] = t
    report.update({
        "rules_matched": matched,
        "rule_errors": report["rule_errors"],
        "findings_new": new,
        "findings_dup": dup,
        "findings_total": len(findings),
    })
    # Persistence is the only step that can touch the filesystem; a full or
    # read-only disk must not break the sweep — the report says so instead.
    if own_state:
        try:
            save_state(state)
        except OSError as e:  # noqa: BLE001
            report["error"] = f"persist state failed: {type(e).__name__}"[:ERR_SNIPPET]
    if own_findings:
        try:
            save_findings(findings)
        except OSError as e:  # noqa: BLE001
            report["error"] = f"persist findings failed: {type(e).__name__}"[:ERR_SNIPPET]
    return report
