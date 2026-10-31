"""mycore.session — JSONL turn persistence (P4-1b).

File format: one JSON object per line, UTF-8.
  line 1 (file creation): {"meta": {"created": <epoch>, "model": str, "format": 1}}
  following lines:        {"role": ..., "content": ..., "ts": <epoch>, ...}
                           (tool-call turns keep tool_calls / tool_call_id)

Design rules:
  - append-only; each append flushes (a crash loses at most the current line)
  - corrupt lines are skipped and counted (`Session.corrupt`) — honest
    reporting instead of a hard crash on a half-written tail line
  - messages round-trip as plain dicts: whatever the loop puts in a message
    (role/content/tool_calls/tool_call_id/name) comes back out
English comments per repo i18n policy.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

MSG_KEYS = ("role", "content", "name", "tool_calls", "tool_call_id", "ts")


class Session:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.corrupt = 0          # lines skipped at last load (honest stat)

    # ------------------------------------------------------------ write ----
    @classmethod
    def create(cls, path: str | Path, meta: dict | None = None) -> "Session":
        """Create/overwrite a session file with its meta line."""
        s = cls(path)
        s.path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"meta": {"created": time.time(), "format": 1,
                        **({"model": meta["model"]} if meta and meta.get("model") else {})}}
        s.path.write_text(json.dumps(doc, ensure_ascii=False) + "\n", encoding="utf-8")
        return s

    def append(self, message: dict) -> None:
        """Append one message (role/content/...) as a JSONL line; flushed.

        A crash can leave a partial tail line without a newline — heal it first
        (terminate the orphan line as its own corrupt entry) so this append
        starts on a fresh line instead of gluing onto garbage.
"""
        if not isinstance(message, dict) or not message.get("role"):
            raise ValueError("message must be a dict with a 'role'")
        line = {k: message[k] for k in MSG_KEYS if k in message}
        line.setdefault("ts", time.time())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_line_start()
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            fh.flush()

    def _ensure_line_start(self) -> None:
        """Terminate a partial (newline-less) tail line left by a crash."""
        try:
            if self.path.stat().st_size == 0:
                return
            with open(self.path, "rb") as fh:
                fh.seek(-1, 2)
                if fh.read(1) != b"\n":
                    with open(self.path, "ab") as fa:
                        fa.write(b"\n")
        except OSError:
            return

    def append_many(self, messages: list[dict]) -> None:
        """Append a run-turn delta (assistant + tool messages) in one flush."""
        if not messages:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_line_start()
        with open(self.path, "a", encoding="utf-8") as fh:
            for m in messages:
                if not isinstance(m, dict) or not m.get("role"):
                    continue
                line = {k: m[k] for k in MSG_KEYS if k in m}
                line.setdefault("ts", time.time())
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            fh.flush()

    # ------------------------------------------------------------- read ----
    def load(self) -> list[dict]:
        """All messages (meta line dropped). Corrupt lines skipped + counted."""
        self.corrupt = 0
        if not self.path.is_file():
            return []
        out: list[dict] = []
        for raw in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not raw.strip():
                continue
            try:
                doc = json.loads(raw)
            except ValueError:
                self.corrupt += 1
                continue
            if not isinstance(doc, dict):
                self.corrupt += 1
                continue
            if "meta" in doc and "role" not in doc:
                continue                              # header line
            if not doc.get("role"):
                self.corrupt += 1
                continue
            out.append({k: doc[k] for k in MSG_KEYS if k in doc})
        return out

    def meta(self) -> dict:
        """Header dict ({} when absent/invalid)."""
        if not self.path.is_file():
            return {}
        try:
            first = self.path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
            doc = json.loads(first)
        except (OSError, ValueError, IndexError):
            return {}
        return doc.get("meta", {}) if isinstance(doc, dict) else {}

    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0


# ------------------------------------------- Wave 6 delta2: X12/X13 faces ---
# The class above is deliberately minimal (append-only write + load). The
# module faces below add the three maintenance/read surfaces the upstream
# parity audit flagged as missing: age pruning (X12, opt-in from settings),
# list + substring search and single-session export (X13). Design rules hold
# throughout:
#   - fail-soft or explicitly typed errors (ValueError / FileNotFoundError),
#     never an OSError escaping to an HTTP 500;
#   - an id can never escape `directory` (grammar + resolve parent check);
#   - summary/search scan at most SCAN_CAP chars per file and say so
#     (`partial: true`) instead of pretending the file was fully read.

SESSION_SUFFIX = ".jsonl"
LIST_LIMIT_DEFAULT = 200
LIST_LIMIT_MAX = 500
SCAN_CAP = 1 << 20                  # chars read per file for summary/search
SUMMARY_MAX = 120                   # summary excerpt length
# id grammar: one or two path segments (the second is the archive/ level),
# each starting alnum — "..", absolute paths and separators cannot match.
_SID_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}"
    r"(/[A-Za-z0-9][A-Za-z0-9._-]{0,95})?")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")


def _session_files(directory: str | Path) -> list[tuple[Path, str]]:
    """All session files as (path, id) — top level plus archive/, newest
    callers sort themselves. A missing directory is an empty list."""
    root = Path(directory)
    out: list[tuple[Path, str]] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return out
    for p in entries:
        if p.is_file() and p.suffix == SESSION_SUFFIX:
            out.append((p, p.stem))
    arch = root / "archive"
    try:
        entries = list(arch.iterdir())
    except OSError:
        entries = []
    for p in entries:
        if p.is_file() and p.suffix == SESSION_SUFFIX:
            out.append((p, f"archive/{p.stem}"))
    return out


def _msg_text(msg: dict) -> str:
    """One message as plain text: a str content verbatim; a multimodal block
    list as its text parts plus an honest '[image]' marker per image part
    (the marker mirrors what the console persists — no image byte here)."""
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif block.get("type") == "image_url":
                parts.append("[image]")
        return " ".join(parts)
    return ""


def _scan_docs(path: Path) -> tuple[list[dict], bool]:
    """Parse a session file up to SCAN_CAP chars: (docs, partial). Lines that
    do not parse (a cut tail inside the cap window) are skipped — this is a
    scan for summary/search, not Session.load's integrity report."""
    docs: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(SCAN_CAP)
            partial = bool(fh.read(1))
    except OSError:
        return [], False
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            doc = json.loads(raw)
        except ValueError:
            continue
        if isinstance(doc, dict) and ("role" in doc or "meta" in doc):
            docs.append(doc)
    return docs, partial


def _summary_of(docs: list[dict]) -> str:
    """First user (else first assistant) text, whitespace-collapsed, control
    chars stripped, capped at SUMMARY_MAX. "" = genuinely nothing readable."""
    chosen = ""
    for want in ("user", "assistant"):
        for doc in docs:
            if doc.get("role") != want:
                continue
            text = " ".join(_msg_text(doc).split())
            if text:
                chosen = text
                break
        if chosen:
            break
    chosen = _CTRL_RE.sub(" ", chosen)
    if len(chosen) > SUMMARY_MAX:
        chosen = chosen[:SUMMARY_MAX].rstrip() + "…"
    return chosen


def list_sessions(directory: str | Path, *, q: str | None = None,
                  limit: int = LIST_LIMIT_DEFAULT) -> dict:
    """X13 read face: session rows newest-first.

    Row: {id, when (mtime epoch), size, summary[, hits, partial]}. `q` is a
    case-insensitive SUBSTRING scan over message text — honestly labelled in
    the result (`search.method: "substring"`): there is no full-text index and
    we never claim one. `count` = matched total (rows returned may be fewer;
    `truncated` says so). Fail-soft: an unreadable file becomes a missing row,
    never a raised error."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = LIST_LIMIT_DEFAULT
    limit = max(1, min(limit, LIST_LIMIT_MAX))
    qf = str(q).casefold() if q else None
    files = _session_files(directory)
    files.sort(key=lambda pair: _mtime(pair[0]), reverse=True)
    rows: list[dict] = []
    matched = 0
    for path, sid in files:
        docs, partial = _scan_docs(path)
        if qf is not None:
            hits = sum(1 for d in docs if qf in _msg_text(d).casefold())
            if not hits:
                continue
        else:
            hits = 0
        matched += 1
        if len(rows) >= limit:
            continue
        row: dict = {"id": sid, "when": _mtime(path), "size": _size(path),
                     "summary": _summary_of(docs)}
        if partial:
            row["partial"] = True
        if qf is not None:
            row["hits"] = hits
        rows.append(row)
    out: dict = {"sessions": rows, "count": matched,
                 "truncated": matched > len(rows), "limit": limit}
    if qf is not None:
        out["search"] = {"q": str(q), "method": "substring",
                         "case_insensitive": True}
    return out


def _mtime(path: Path) -> int:
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def export_session(directory: str | Path, sid: str, fmt: str = "json") -> tuple[str, str]:
    """X13 export: one session as (text, content_type).

    Raises ValueError for an id/format the contract refuses and
    FileNotFoundError for an unknown id — the HTTP layer maps them to
    400/404. The id grammar plus a resolved-parent check make a traversal
    impossible even if the grammar is ever loosened. Export carries message
    content the console already reads (chat history contract) — never a
    credential slot value; corrupt lines are skipped and counted honestly."""
    sid = str(sid or "").strip()
    if not _SID_RE.fullmatch(sid):
        raise ValueError("session id must be [A-Za-z0-9._-] segments with "
                         "at most one archive/ level")
    fmt = str(fmt or "").strip().lower()
    if fmt not in ("json", "md"):
        raise ValueError("format must be one of: json | md")
    root = Path(directory)
    target = root / f"{sid}{SESSION_SUFFIX}"
    try:
        resolved = target.resolve()
        base = root.resolve()
        ok_parent = resolved.parent == base or resolved.parent == base / "archive"
    except OSError:
        raise FileNotFoundError(f"no session {sid}") from None
    if not ok_parent or not resolved.is_file():
        raise FileNotFoundError(f"no session {sid}")
    sess = Session(resolved)
    meta = sess.meta()
    msgs = sess.load()
    if fmt == "json":
        doc = {"id": sid, "meta": meta, "count": len(msgs),
               "corrupt": sess.corrupt, "messages": msgs}
        return json.dumps(doc, ensure_ascii=False, indent=2) + "\n", \
            "application/json; charset=utf-8"
    lines = [f"# session {sid}", ""]
    if meta:
        bits = []
        if isinstance(meta.get("created"), (int, float)):
            bits.append("created " + time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(meta["created"])))
        if meta.get("model"):
            bits.append(f"model {meta['model']}")
        if bits:
            lines.append(" · ".join(bits))
            lines.append("")
    if sess.corrupt:
        lines.append(f"> {sess.corrupt} corrupt line(s) skipped at export")
        lines.append("")
    for m in msgs:
        role = str(m.get("role") or "?")
        if role == "tool":
            name = m.get("name") or m.get("tool_call_id") or ""
            label = f"tool {name}" if name else "tool"
        else:
            label = role
        lines.append(f"**{label}:** {_msg_text(m)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n", "text/markdown; charset=utf-8"


def prune_old(directory: str | Path, max_age_days: int, *,
              keep: tuple[str, ...] | list[str] = (),
              now: float | None = None) -> dict:
    """X12: delete *.jsonl session files older than `max_age_days`.

    Retention boundary is INCLUSIVE: a file whose age is exactly the
    threshold (mtime == now - days) is KEPT — only strictly older files go.
    `keep` holds ids (stems) that are never touched — the console protects
    its live "web" session with it. archive/ files are pruned by the same
    rule (their stems include the archive stamp, so a stem match still
    applies). Non-.jsonl files (.model overrides, stray notes) are never
    touched. Fail-soft: per-file OSError counts as an error, never raises.
    Returns {removed, kept, freed, errors} — names are never needed by the
    caller and stay out of the result on purpose."""
    if isinstance(max_age_days, bool) or not isinstance(max_age_days, int) \
            or max_age_days <= 0:
        raise ValueError("max_age_days must be a positive integer")
    cutoff = (time.time() if now is None else float(now)) - max_age_days * 86400
    keep_set = {str(k) for k in keep}
    removed = kept = errors = 0
    freed = 0
    for path, sid in _session_files(directory):
        if sid in keep_set or Path(sid).stem in keep_set:
            kept += 1
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            errors += 1
            continue
        if mtime >= cutoff:               # at-or-younger than the threshold
            kept += 1
            continue
        try:
            freed += path.stat().st_size
            path.unlink()
            removed += 1
        except OSError:
            errors += 1
    return {"removed": removed, "kept": kept, "freed": freed,
            "errors": errors}
