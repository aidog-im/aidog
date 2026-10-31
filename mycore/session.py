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
import os
import re
import sqlite3
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
# list + real FTS5 full-text search (X13, honest substring fallback) and
# single-session export (X13). Design rules hold
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

# ------------------------------------- FTS5 full-text session search -------
# Owner decision (2026-10-27): `?q=` runs REAL full-text search over a
# SQLite FTS5 index; the old substring scan survives only as an honest,
# labelled fallback. Design rules:
#   - INDEX LOCATION: `<state root>/session_search.sqlite3` — the directory
#     CONTAINING sessions/, NEVER inside it (X12 prune touches only .jsonl
#     and _session_files must not see the index), and outside every probe
#     byte-identity surface (secrets.env / web/settings.json / profiles/** /
#     data/images / the checkpoints index).
#   - LAZY SYNC at query time, keyed by (mtime ms, size): new/changed files
#     are (re)indexed; rows of vanished files are dropped BEFORE matching,
#     so pruned or resume-rotated content can never be matched again.
#   - one row per message; text normalized by _fts_norm (casefold + a space
#     around every character of a no-space script that unicode61 cannot
#     split into tokens); sessions rank by their best FTS5 BM25 score,
#     ties newest first.
#   - HONEST DEGRADATION: no FTS5 build, an index still unusable after one
#     rebuild, or a query with nothing indexable (punctuation-only) runs the
#     original substring scan and SAYS SO (`method:"substring",
#     fallback:true, reason:...`); success reports `method:"fts5",
#     engine:"fts5", fallback:false`.
INDEX_NAME = "session_search.sqlite3"
_FTS5_AVAILABLE = True          # monkeypatch seam: degradation test flips it
# No-space scripts: han ideographs (incl. ext A/B+), kana, hangul — runs of
# these carry no spaces, so unicode61 would tokenize a whole run as ONE
# token and a mid-run query could never match without the spacing below.
_NSS_CLASS = ("\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
              "\u3040-\u30ff"
              "\uac00-\ud7af\u1100-\u11ff"
              "\U00020000-\U0003134f")
_NSS_RE = re.compile("[" + _NSS_CLASS + "]+")
_WORD_RE = re.compile(r"\w+", re.UNICODE)


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


def _fts_norm(text: str) -> str:
    """Index/query normalization: casefold, then isolate every character of a
    no-space script (han/kana/hangul) with spaces so ONE character becomes
    ONE FTS5 token — unicode61 would otherwise tokenize a whole run as a
    single token and a mid-run query could never match."""
    return _NSS_RE.sub(lambda m: " " + " ".join(m.group(0)) + " ",
                       text.casefold())


def _fts_expr(qf: str) -> str | None:
    """FTS5 MATCH expression for a casefolded query; None when the query has
    nothing FTS can index (punctuation/underscore only) — the caller then
    runs the substring scan, which still matches those characters.

    Terms are ANDed. A word-ish term matches as a PREFIX phrase (a typed
    fragment still finds the word). Each word-run segment made only of
    no-space-script characters matches as an exact adjacent-token phrase —
    substring-style matching for scripts unicode61 cannot split."""
    terms: list[str] = []
    for seg in _WORD_RE.findall(qf):          # raw word runs of the query
        phrase: list[str] = []
        for tok in _WORD_RE.findall(_fts_norm(seg)):
            if len(tok) == 1 and _NSS_RE.fullmatch(tok):
                phrase.append(tok)            # one no-space-script char
                continue
            if phrase:
                terms.append('"' + " ".join(phrase) + '"')
                phrase = []
            if tok.strip("_"):
                terms.append('"%s"*' % tok)
        if phrase:
            terms.append('"' + " ".join(phrase) + '"')
    return " ".join(terms) or None


# --------------------------- car 5-D7 item 4: fold-time recall ------------
# "Recall before re-send" (design §4.2 item 5): when loop.run_turn folds old
# turns away, ONLY the snippets that match the live task are re-injected as a
# `[recalled ...]` system marker — the folded turns are not re-sent whole.
# It reuses the session search's own FTS5 engine, query grammar and BM25
# ranking (`_fts_norm` / `_fts_expr` vocabulary), but over an IN-MEMORY window
# of the just-folded messages: no new on-disk index, no new process, no new
# dependency. Honest degradation is the same ladder as `?q=`: no FTS5 build
# here -> a casefolded substring pass reported as method "substring"; a task
# line with nothing indexable -> method "none" and zero hits (never invented).
RECALL_LIMIT = 3                # snippets re-injected per fold
RECALL_SNIPPET_CHARS = 240      # per snippet
RECALL_SNIPPET_LEAD = 80        # context kept before the first hit
RECALL_MAX_PROBES = 24          # task-line probes sent to the MATCH expression
_FTS5_RECALL_AVAILABLE = True   # monkeypatch seam: the degradation test flips it

_RECALL_STOP = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
    "has", "his", "her", "was", "one", "our", "out", "get", "how", "now",
    "new", "old", "see", "way", "who", "did", "its", "let", "say", "she",
    "too", "use", "that", "with", "have", "this", "will", "your", "from",
    "they", "know", "want", "been", "much", "some", "time", "very",
    "when", "come", "here", "just", "like", "make", "many", "over",
    "such", "take", "than", "them", "well", "were", "what", "into"})


def _recall_probes(query: str) -> list[str]:
    """Indexable probes of the LIVE task line: latin/number word runs (>=3
    chars, stopwords dropped, prefix-matchable) plus no-space-script runs cut
    into 2-grams — unicode61 cannot tokenize a han/kana/hangul run into words,
    so an adjacent-token PHRASE is the only form that recalls a Chinese
    fragment (the same reasoning as `_fts_expr`). De-duplicated, ordered,
    bounded by RECALL_MAX_PROBES (long runs are thinned on a stride instead of
    truncated, so a long task line keeps its whole span)."""
    latin: list[str] = []
    cjk: list[str] = []
    seen: set[str] = set()
    for run in _WORD_RE.findall(str(query).casefold()):
        for sub in re.findall("[" + _NSS_CLASS + "]+|[^" + _NSS_CLASS + "]+", run):
            if _NSS_RE.search(sub):
                grams = [sub[i:i + 2] for i in range(len(sub) - 1)] if len(sub) > 1 else [sub]
                for g in grams:
                    if g not in seen:
                        seen.add(g)
                        cjk.append(g)
            elif len(sub) >= 3 and sub not in _RECALL_STOP and sub not in seen:
                seen.add(sub)
                latin.append(sub)
    budget = max(0, RECALL_MAX_PROBES - len(latin))
    if len(cjk) > budget:
        step = max(1, -(-len(cjk) // max(1, budget)))   # ceil division
        cjk = cjk[::step][:budget]
    return latin + cjk


def _recall_expr(probes: list[str]) -> str | None:
    """FTS5 MATCH expression over `probes`, ORed (a task line matches on ANY
    of its terms; ANDing would return nothing realistic). Latin terms are
    PREFIX phrases (`"tok"*`), no-space-script probes are adjacent-token
    PHRASES over the normalized text. None when there is nothing to match."""
    parts: list[str] = []
    for p in probes:
        if _NSS_RE.search(p):
            norm = " ".join(_fts_norm(p).split())
            if norm:
                parts.append('"%s"' % norm)
        else:
            parts.append('"%s"*' % p)
    return " OR ".join(dict.fromkeys(parts)) or None


def _recall_match(texts: list[str], expr: str, limit: int
                  ) -> dict[int, float] | None:
    """BM25 over an in-memory FTS5 table: {candidate index: score} (lower =
    better), or None when FTS5 cannot run in this python — the caller then
    uses the substring pass. Never raises; the table lives only for this call."""
    global _FTS5_RECALL_AVAILABLE
    if not _FTS5_RECALL_AVAILABLE:
        return None
    conn = None
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE recall_fts USING "
                     "fts5(text, tokenize='unicode61')")
        conn.executemany("INSERT INTO recall_fts(text) VALUES (?)",
                         [(_fts_norm(t),) for t in texts])
        out: dict[int, float] = {}
        for rowid, score in conn.execute(
                "SELECT rowid, bm25(recall_fts) FROM recall_fts "
                "WHERE recall_fts MATCH ? ORDER BY bm25(recall_fts) LIMIT ?",
                (expr, max(1, int(limit)))):
            out[int(rowid) - 1] = float(score)
        return out
    except sqlite3.OperationalError as e:
        if "fts5" in str(e).lower():        # no such module -> permanent here
            _FTS5_RECALL_AVAILABLE = False
        return None
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def _recall_snippet(text: str, probes: list[str]) -> str:
    """One bounded excerpt of `text`, centred on the EARLIEST probe hit when it
    sits past the opening (a 12k-char tool result recalls its relevant line,
    not its first 240 chars). Whitespace-collapsed, control chars stripped."""
    flat = " ".join(_CTRL_RE.sub(" ", text).split())
    if not flat:
        return ""
    cf = flat.casefold()
    best = -1
    for p in probes:
        pos = cf.find(p)
        if pos >= 0 and (best < 0 or pos < best):
            best = pos
    start = best - RECALL_SNIPPET_LEAD if best > RECALL_SNIPPET_LEAD else 0
    cut = flat[start:start + RECALL_SNIPPET_CHARS]
    more = len(flat) - start > len(cut)
    return (("…" if start else "") + cut + ("…" if more else ""))


def recall_snippets(candidates: list[dict], query: str, *,
                    limit: int = RECALL_LIMIT) -> dict:
    """Car 5-D7 item 4 — FTS5 recall over the messages a fold just dropped.

    -> `{method, hits:[{role, text, score}], probes}` where method is
      * `fts5`      — BM25 ranking from the in-memory FTS5 pass,
      * `substring` — honest fallback (no FTS5 in this python),
      * `none`      — empty task line, no candidates or no indexable probe.
    Never raises: every failure degrades to `none`/`substring` with zero hits
    rather than breaking the turn. Snippets are bounded (RECALL_* constants)
    and role-tagged so the model can see WHERE a fact came from."""
    try:
        probes = _recall_probes(query)
        entries: list[tuple[dict, str]] = []
        for m in candidates:
            if not isinstance(m, dict):
                continue
            txt = _msg_text(m)
            if txt.strip():
                entries.append((m, txt))
    except Exception:  # noqa: BLE001 — recall is an optimisation, never a fault
        return {"method": "none", "hits": [], "probes": 0}
    if not probes or not entries:
        return {"method": "none", "hits": [], "probes": len(probes)}
    texts = [t for _m, t in entries]
    expr = _recall_expr(probes)
    matched: dict[int, float] = {}
    method = "substring"
    if expr is not None:
        got = _recall_match(texts, expr, max(1, limit * 4))
        if got is not None:
            matched = got
            method = "fts5"
    scored: list[tuple[int, float, int]] = []
    for i, t in enumerate(texts):
        cf = t.casefold()
        sub = sum(1 for p in probes if p in cf)
        bm = matched.get(i)
        if sub == 0 and bm is None:
            continue
        # rank: most probe evidence first, BM25 breaks ties (lower wins),
        # then source order — deterministic for identical input
        scored.append((-(sub + (2 if bm is not None else 0)),
                       bm if bm is not None else 0.0, i))
    scored.sort()
    hits = []
    for _neg, bm, i in scored[:max(0, int(limit))]:
        snip = _recall_snippet(texts[i], probes)
        if snip:
            hits.append({"role": entries[i][0].get("role", "?"),
                         "text": snip, "score": round(bm, 3)})
    if not hits:
        return {"method": "none", "hits": [], "probes": len(probes)}
    return {"method": method, "hits": hits, "probes": len(probes)}


def _fts_index_path(directory: str | Path) -> Path:
    """`<parent of sessions>/session_search.sqlite3` - never inside the
    sessions directory itself."""
    return Path(directory).parent / INDEX_NAME


def _fts_drop_index(directory: str | Path) -> None:
    """Best-effort removal of a broken index (plus journal/wal siblings) so
    the next attempt rebuilds it from the live files. Fail-soft."""
    base = _fts_index_path(directory)
    for cand in (base, Path(str(base) + "-journal"),
                 Path(str(base) + "-wal"), Path(str(base) + "-shm")):
        try:
            cand.unlink()
        except OSError:
            pass


def _fts_schema(conn) -> None:
    """Create the two tables if missing (a corrupt file never gets this far —
    sqlite raises before)."""
    conn.execute("CREATE TABLE IF NOT EXISTS session_index ("
                 "path TEXT PRIMARY KEY, root TEXT NOT NULL, "
                 "mtime INTEGER NOT NULL, size INTEGER NOT NULL)")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS session_fts USING "
                 "fts5(text, root UNINDEXED, path UNINDEXED, "
                 "tokenize='unicode61')")
    conn.commit()


def _fts_sync(conn, root: str, stat: dict[str, tuple[int, int]]) -> None:
    """Incremental sync for ONE sessions root, keyed by (mtime ms, size):
    rows for files that vanished are dropped BEFORE anything is searchable
    (prune / resume rotation can never leak), changed or new files are
    re-scanned over the same SCAN_CAP window the substring scan reads."""
    known = {p: (m, s) for p, m, s in conn.execute(
        "SELECT path, mtime, size FROM session_index WHERE root=?", (root,))}
    for path in known:
        if path not in stat:
            conn.execute("DELETE FROM session_fts WHERE path=?", (path,))
            conn.execute("DELETE FROM session_index WHERE path=?", (path,))
    for path, (mtime, size) in stat.items():
        if known.get(path) == (mtime, size):
            continue
        conn.execute("DELETE FROM session_fts WHERE path=?", (path,))
        conn.execute("DELETE FROM session_index WHERE path=?", (path,))
        docs, _partial = _scan_docs(Path(path))
        rows = []
        for doc in docs:
            if "role" not in doc:
                continue
            text = _fts_norm(_msg_text(doc))
            if text.strip():
                rows.append((text, root, path))
        conn.executemany(
            "INSERT INTO session_fts(text, root, path) VALUES (?,?,?)", rows)
        conn.execute(
            "INSERT INTO session_index(path, root, mtime, size) "
            "VALUES (?,?,?,?)", (path, root, mtime, size))
    conn.commit()


def _fts_attempt(directory: str | Path, root: str,
                 stat: dict[str, tuple[int, int]], expr: str
                 ) -> list[tuple[str, int, float]]:
    """One open → schema → sync → match pass over the index. Returns
    [(absolute path, hits, bm25 score)] best-first (BM25 ascending; a
    session's rows collapse to its FIRST = best row, extra rows add hits).
    Raises sqlite3.Error/OSError for the caller's rebuild/degrade ladder."""
    conn = sqlite3.connect(str(_fts_index_path(directory)), timeout=5.0)
    try:
        _fts_schema(conn)
        _fts_sync(conn, root, stat)
        order: list[str] = []
        hits: dict[str, int] = {}
        scores: dict[str, float] = {}
        for path, score in conn.execute(
                "SELECT path, bm25(session_fts) AS score FROM session_fts "
                "WHERE session_fts MATCH ? AND root=? ORDER BY score",
                (expr, root)):
            if path in hits:
                hits[path] += 1
            else:
                hits[path] = 1
                scores[path] = score
                order.append(path)
        return [(p, hits[p], scores[p]) for p in order]
    finally:
        conn.close()


def _fts_lookup(directory: str | Path, files: list[tuple[Path, str]],
                qf: str) -> tuple[list[tuple[Path, str, int, int, int,
                                            float]] | None, str]:
    """FTS5 search for the casefolded `qf` over `files` [(path, id)]:
    -> ([ (path, id, when, size, hits, score) ... ] best-first, "") on
    success, or (None, reason) when list_sessions must degrade to the
    substring scan. reason ∈ fts5_unavailable | unindexable_query |
    index_unusable. Fail-soft by design: ANY failure degrades, never raises."""
    global _FTS5_AVAILABLE
    if not _FTS5_AVAILABLE:
        return None, "fts5_unavailable"
    expr = _fts_expr(qf)
    if expr is None:
        return None, "unindexable_query"
    root = os.path.abspath(str(directory))
    stat: dict[str, tuple[int, int]] = {}
    info: dict[str, tuple[str, int, int]] = {}
    for path, sid in files:
        try:
            st = path.stat()
        except OSError:                     # vanished after the listing
            continue
        ap = os.path.abspath(str(path))
        stat[ap] = (int(st.st_mtime * 1000), st.st_size)
        info[ap] = (sid, int(st.st_mtime), st.st_size)
    rebuilt = False
    while True:                             # at most: plain → after rebuild
        try:
            matched = _fts_attempt(directory, root, stat, expr)
            break
        except sqlite3.OperationalError as e:
            if "fts5" in str(e).lower():   # no such module: fts5 → honest
                _FTS5_AVAILABLE = False    # permanent degradation this run
                return None, "fts5_unavailable"
            if rebuilt:
                return None, "index_unusable"
            rebuilt = True
            _fts_drop_index(directory)      # corrupt/locked → rebuild once
        except (sqlite3.Error, OSError):
            if rebuilt:
                return None, "index_unusable"
            rebuilt = True
            _fts_drop_index(directory)
    rows: list[tuple[Path, str, int, int, int, float]] = []
    for ap, hits, score in matched:
        if ap not in info or not os.path.isfile(ap):
            continue        # deleted between sync and now — never surface
        sid, when, size = info[ap]
        rows.append((Path(ap), sid, when, size, hits, score))
    rows.sort(key=lambda r: (r[5], -r[2]))  # BM25 best first, ties newest
    return rows, ""


def list_sessions(directory: str | Path, *, q: str | None = None,
                  limit: int = LIST_LIMIT_DEFAULT) -> dict:
    """X13 read face: session rows.

    Without `q`: newest-first rows {id, when, size, summary[, partial]},
    `count` = all sessions and NO `search` key (contract unchanged).

    With `q` (casefolded by the caller's contract): REAL FTS5 full-text
    search — rows in BM25 order, `hits` = matching messages, and an honest
    search block `{q, method:"fts5", engine:"fts5", fallback:false,
    case_insensitive:true}`; any degradation runs the original substring
    scan and says so instead (`method:"substring", fallback:true,
    reason:...`). `count` = matched sessions (rows returned may be fewer;
    `truncated` says so). Fail-soft: an unreadable file becomes a missing
    row, never a raised error."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = LIST_LIMIT_DEFAULT
    limit = max(1, min(limit, LIST_LIMIT_MAX))
    qf = str(q).casefold() if q else None
    files = _session_files(directory)
    files.sort(key=lambda pair: _mtime(pair[0]), reverse=True)

    if qf is None:
        rows: list[dict] = []
        for path, sid in files:
            if len(rows) >= limit:
                break
            docs, partial = _scan_docs(path)
            row: dict = {"id": sid, "when": _mtime(path), "size": _size(path),
                         "summary": _summary_of(docs)}
            if partial:
                row["partial"] = True
            rows.append(row)
        return {"sessions": rows, "count": len(files),
                "truncated": len(files) > len(rows), "limit": limit}

    matched, reason = _fts_lookup(directory, files, qf)
    rows = []
    if matched is not None:
        search: dict = {"q": str(q), "method": "fts5", "engine": "fts5",
                        "fallback": False, "case_insensitive": True}
        count = len(matched)
        for path, sid, when, size, hits, _score in matched[:limit]:
            docs, partial = _scan_docs(path)
            row = {"id": sid, "when": when, "size": size,
                   "summary": _summary_of(docs)}
            if partial:
                row["partial"] = True
            row["hits"] = hits
            rows.append(row)
    else:
        # honest degradation: the original substring scan, labelled as such
        search = {"q": str(q), "method": "substring", "fallback": True,
                  "case_insensitive": True, "reason": reason}
        count = 0
        for path, sid in files:
            docs, partial = _scan_docs(path)
            hits = sum(1 for d in docs if qf in _msg_text(d).casefold())
            if not hits:
                continue
            count += 1
            if len(rows) >= limit:
                continue
            row = {"id": sid, "when": _mtime(path), "size": _size(path),
                   "summary": _summary_of(docs)}
            if partial:
                row["partial"] = True
            row["hits"] = hits
            rows.append(row)
    return {"sessions": rows, "count": count,
            "truncated": count > len(rows), "limit": limit,
            "search": search}


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


def _resolve_session_path(directory: str | Path, sid: str) -> Path:
    """Grammar-checked id -> existing session file. Shared by
    export_session and the console resume face (X20) so one rule governs
    both: the id grammar plus a resolved-parent check make a traversal
    impossible even if the grammar is ever loosened. Raises FileNotFoundError
    naming the id (honest 404 in the HTTP layer)."""
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
    return resolved


def session_file(directory: str | Path, sid: str) -> Path:
    """X20 resume resolver: one session id (root or archive/ level) -> its
    file, with the SAME grammar and parent double-check export uses — one
    accept/reject rule, so a resume can never leave the sessions directory.
    Raises ValueError (bad grammar, -> 400) or FileNotFoundError (unknown id,
    -> 404)."""
    sid = str(sid or "").strip()
    if not _SID_RE.fullmatch(sid):
        raise ValueError("session id must be [A-Za-z0-9._-] segments with "
                         "at most one archive/ level")
    return _resolve_session_path(directory, sid)


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
    resolved = _resolve_session_path(directory, sid)
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
