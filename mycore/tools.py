"""mycore.tools — tool registry + the 11 core tools (design §14.3 + Phase B2
+ car 2: delegate / execute_code).

Registry exposes OpenAI JSON-schema `tools` for the model boundary and a
dispatcher for the loop. Handler contract (README): return `str`; exceptions
become honest `ERROR:` strings — a failing tool never crashes the loop.
Path tools enforce allowed roots (README contract 4). stdlib only.

The 9th tool (`cron`, v0.5.20) is the natural-language entry: the model
translates plain speech into a scheduler spec string (mycore.scheduler owns
one shared grammar, so /cron and the tool can never drift). Its handler
captures `state_dir` at build_registry() time and resolves the default path
lazily at call time (env AIDOG_MC_STATE read when the tool actually runs).
English comments per repo i18n policy.
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Callable

REPO = Path(__file__).resolve().parent.parent

DEFAULT_ALLOWED_ROOTS = (str(REPO), tempfile.gettempdir())
MAX_RESULT_CHARS = 24000          # tool output cap (context budget discipline)
COMPRESS_THRESHOLD = 6000         # results above this compress first (car 4a-M3)
BASH_TIMEOUT = 120


class ToolError(Exception):
    """Raised by handlers for expected failures; rendered as `ERROR: ...`."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict            # JSON schema (type/object/properties/required)
    handler: Callable[..., str]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        # Turn binding (car 2-D6): loop.run_turn records the chat callable +
        # budget of the ACTIVE turn so meta-tools (delegate) can reach the
        # model connection without holding credentials themselves. Written at
        # turn entry (service turns run sequentially), read during dispatch.
        self._turn_chat: Callable[..., dict] | None = None
        self._turn_budget: int | None = None
        # Progress sink (car 4a-M3): web binds an SSE emitter per turn;
        # MCP tool handlers read it during their own call window only.
        self._progress_sink: Callable[[dict], None] | None = None

    def register(self, name: str, description: str, parameters: dict,
                 handler: Callable[..., str]) -> None:
        if name in self._tools:
            raise ValueError(f"duplicate tool name: {name}")
        self._tools[name] = Tool(name, description, parameters, handler)

    def select(self, names: list[str]) -> "ToolRegistry":
        """A NEW registry holding only `names` (shared Tool objects, same
        roots/state closures) — the car 2-D6 sub-agent isolation primitive.
        Unknown names are skipped here; callers validate honestly first."""
        out = ToolRegistry()
        for n in names:
            if n in self._tools:
                out._tools[n] = self._tools[n]
        return out

    def bind_turn(self, chat: Callable[..., dict] | None,
                  budget: int | None = None) -> None:
        """Called by loop.run_turn at turn entry (see class comment)."""
        self._turn_chat = chat
        self._turn_budget = budget

    def turn_binding(self) -> tuple[Callable[..., dict] | None, int | None]:
        """(chat, budget) of the active turn — None/None outside run_turn."""
        return self._turn_chat, self._turn_budget

    def bind_progress(self, fn: Callable[[dict], None] | None) -> None:
        """Bind the progress sink for MCP tool calls (car 4a-M3, mirrors
        bind_turn). fn(payload) receives {server, tool, message, progress}
        dicts; None unbinds. TG never binds one (single-send model, see
        docs/features/mcp.md)."""
        self._progress_sink = fn

    def progress_sink(self) -> Callable[[dict], None] | None:
        """Currently bound progress sink (None = not bound)."""
        return self._progress_sink

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schema(self) -> list[dict]:
        """OpenAI `tools` payload for the model boundary."""
        return [{"type": "function",
                 "function": {"name": t.name, "description": t.description,
                              "parameters": t.parameters}}
                for t in (self._tools[n] for n in self.names())]

    def dispatch(self, name: str, arguments: dict) -> str:
        """Run one tool call. Never raises: failures return honest `ERROR:` text."""
        tool = self._tools.get(name)
        if tool is None:
            return f"ERROR: unknown tool '{name}'"
        if not isinstance(arguments, dict):
            return f"ERROR: arguments for '{name}' must be a JSON object"
        try:
            result = tool.handler(**arguments)
        except TypeError as e:
            # Wrong/missing arguments — surface the signature mismatch, nothing else.
            return f"ERROR: bad arguments for '{name}': {e}"
        except ToolError as e:
            return f"ERROR: {e}"
        except Exception as e:  # noqa: BLE001 — last-resort containment
            return f"ERROR: {type(e).__name__} in '{name}'"
        if not isinstance(result, str):
            result = str(result)
        return finalize_result(result)


# ------------------------------------------------------------ path safety ---

def resolve_inside(path_str: str, roots: tuple[str, ...] | list[str]) -> Path:
    """Resolve `path_str` and require it inside one of `roots` (allowed roots).

    Symlinks are resolved first; a traversal or symlink escape raises ToolError
    with an honest message (contract 4: refusal, not silent truncation).
    """
    if not isinstance(path_str, str) or not path_str.strip():
        raise ToolError("path must be a non-empty string")
    p = Path(path_str).expanduser().resolve()
    for root in roots:
        try:
            if p == Path(root).resolve() or Path(root).resolve() in p.parents:
                return p
        except OSError:
            continue
    raise ToolError(f"outside allowed roots: {p}")


# -------------------------------------------------------------- core tools ---

def _read_file(path: str, offset: int = 0, limit: int = 200,
               roots: tuple[str, ...] | list[str] = DEFAULT_ALLOWED_ROOTS) -> str:
    p = resolve_inside(path, roots)
    if not p.is_file():
        raise ToolError(f"not a file: {p}")
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        raise ToolError(f"cannot read {p.name}: {type(e).__name__}") from None
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ToolError("offset must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 2000:
        raise ToolError("limit must be an integer in 1..2000")
    window = lines[offset:offset + limit]
    out = [f"{i + 1 + offset:>6}\t{line}" for i, line in enumerate(window)]
    return "\n".join(out) if out else f"[empty: {p.name} has {len(lines)} lines]"


def _snapshot_note(path: Path, state_dir: str | Path | None) -> str:
    """Best-effort pre-write snapshot (car 1-D2) -> short result suffix.
    Never raises and never blocks the write — checkpointing is a safety net,
    not a gate; an existing file that could not be snapshotted says so."""
    from . import checkpoint  # local import: keeps the module graph acyclic
    if not path.is_file():
        return ""                              # create — nothing to roll back
    entry = checkpoint.snapshot(path, state_dir)
    if entry is not None:
        return f" · checkpoint #{entry['id']}"
    return " · previous file not checkpointed (too large or unreadable)"


def _write_file(path: str, content: str,
                roots: tuple[str, ...] | list[str] = DEFAULT_ALLOWED_ROOTS,
                state_dir: str | Path | None = None) -> str:
    p = resolve_inside(path, roots)
    if p.is_dir():
        raise ToolError(f"is a directory: {p}")
    if not isinstance(content, str):
        raise ToolError("content must be a string")
    p.parent.mkdir(parents=True, exist_ok=True)
    note = _snapshot_note(p, state_dir)        # previous bytes, before overwrite
    try:
        p.write_text(content, encoding="utf-8")
    except OSError as e:
        raise ToolError(f"cannot write {p.name}: {type(e).__name__}") from None
    return f"wrote {len(content)} chars to {p}{note}"


def _edit_file(path: str, old_text: str, new_text: str,
               roots: tuple[str, ...] | list[str] = DEFAULT_ALLOWED_ROOTS,
               state_dir: str | Path | None = None) -> str:
    """Exact-string replace; `count` defaults to 1 (must match exactly once)."""
    p = resolve_inside(path, roots)
    if not p.is_file():
        raise ToolError(f"not a file: {p}")
    if not isinstance(old_text, str) or not old_text:
        raise ToolError("old_text must be a non-empty string")
    if not isinstance(new_text, str):
        raise ToolError("new_text must be a string")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise ToolError(f"cannot read {p.name}: {type(e).__name__}") from None
    n = text.count(old_text)
    if n == 0:
        raise ToolError("old_text not found in file")
    if n > 1:
        raise ToolError(f"old_text matches {n} times; must be unique (add context)")
    note = _snapshot_note(p, state_dir)        # previous bytes, before replace
    p.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
    return f"edited {p} (1 replacement){note}"


def _run_bash(command: str, timeout: int = BASH_TIMEOUT,
              roots: tuple[str, ...] | list[str] = DEFAULT_ALLOWED_ROOTS) -> str:
    """Run one shell command in the repo root. Output capped; honest on failure."""
    if not isinstance(command, str) or not command.strip():
        raise ToolError("command must be a non-empty string")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= BASH_TIMEOUT:
        raise ToolError(f"timeout must be an integer in 1..{BASH_TIMEOUT}")
    try:
        proc = subprocess.run(command, shell=True, cwd=str(REPO),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ToolError(f"command timed out after {timeout}s") from None
    out = (proc.stdout or "") + (("\n--- stderr ---\n" + proc.stderr) if proc.stderr else "")
    header = f"exit={proc.returncode}"
    return f"{header}\n{out}".rstrip() or f"{header} (no output)"


def _grep(pattern: str, path: str = ".", glob: str = "*",
          roots: tuple[str, ...] | list[str] = DEFAULT_ALLOWED_ROOTS) -> str:
    """Regex content search under `path` (files matching `glob`), max 200 hits."""
    if not isinstance(pattern, str) or not pattern:
        raise ToolError("pattern must be a non-empty string")
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise ToolError(f"bad regex: {e}") from None
    base = resolve_inside(path, roots)
    if base.is_file():
        files = [base]
    else:
        files = [p for p in sorted(base.rglob(glob)) if p.is_file()]
    hits: list[str] = []
    for f in files:
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f}:{i}:{line.strip()[:200]}")
                    if len(hits) >= 200:
                        break
        except (OSError, UnicodeDecodeError):
            continue
        if len(hits) >= 200:
            break
    if not hits:
        return f"no matches for /{pattern}/ under {base}"
    return "\n".join(hits) + ("" if len(hits) < 200 else "\n[stopped at 200 hits]")


def _glob_variants(pattern: str) -> list[str]:
    """Expand recursive `**` wildcards into the forms fnmatch cannot express.

    fnmatch is not path-aware: `**/*.py` contains a literal `/`, so top-level
    files (`model.py`) never match it — bug caught by the P4-2 A/B run
    (tool-23, 2026-10-18), where glob only returned `tests/*.py`. The original
    pattern stays a candidate (it covers the >=1-directory case); the derived
    zero-directory variants cover top-level files. `*` matches across `/` in
    fnmatch, so `tests/*.py` also reaches deeper files.
    """
    out: list[str] = []
    frontier: list[str] = [pattern]
    while frontier:
        p = frontier.pop(0)
        if p in out:
            continue
        out.append(p)
        variants: list[str] = []
        if p == "**":
            variants.append("*")                 # everything under path
        if p.startswith("**/"):
            variants.append(p[3:])               # zero directories at the front
        if "/**/" in p:
            variants.append(p.replace("/**/", "/"))   # zero dirs mid-path
        if p.endswith("/**"):
            variants.append(p[:-2] + "*")        # zero dirs at the tail
        frontier.extend(variants)
    return out


def _glob(pattern: str, path: str = ".",
          roots: tuple[str, ...] | list[str] = DEFAULT_ALLOWED_ROOTS) -> str:
    """Filename glob under `path`, max 200 paths."""
    if not isinstance(pattern, str) or not pattern:
        raise ToolError("pattern must be a non-empty string")
    base = resolve_inside(path, roots)
    if not base.is_dir():
        raise ToolError(f"not a directory: {base}")
    pats = _glob_variants(pattern)
    rels: list[str] = []
    for root, dirs, files in os.walk(base):
        dirs.sort()
        for fn in sorted(files):
            rel = str(Path(root, fn).relative_to(base))
            if any(fnmatch.fnmatch(rel, c) or fnmatch.fnmatch(fn, c) for c in pats):
                rels.append(rel)
                if len(rels) >= 200:
                    break
        if len(rels) >= 200:
            break
    if not rels:
        return f"no files match {pattern} under {base}"
    return "\n".join(rels) + ("" if len(rels) < 200 else "\n[stopped at 200 paths]")


def _http_fetch(url: str, timeout: int = 20,
                max_chars: int = 8000) -> str:
    """GET a URL and return text (cap applied). Loopback/http(s) only."""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ToolError("url must start with http:// or https://")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1 <= max_chars <= 40000:
        raise ToolError("max_chars must be an integer in 1..40000")
    req = urllib.request.Request(url, method="GET",
                                 headers={"User-Agent": "mycore/0.4 (fetch)"})
    try:
        with urllib.request.urlopen(req, timeout=min(timeout, 60)) as resp:
            raw = resp.read(max_chars * 4)
    except urllib.error.HTTPError as e:
        raise ToolError(f"HTTP {e.code} from {url}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ToolError(f"fetch failed: {type(e).__name__}") from None
    text = raw.decode("utf-8", errors="replace")
    return text if len(text) <= max_chars else text[:max_chars] + "\n... [truncated]"


def compress_output(text: str) -> str:
    """Whitespace-preserving compression pass (car 4a-M3), applied to tool
    results above COMPRESS_THRESHOLD before the hard truncation cap:

      * strip trailing whitespace on every line;
      * runs of 3+ consecutive blank lines collapse to one blank line;
      * a run of k identical non-empty lines renders as the first line plus
        `... [×N identical lines]` where N = k-1 (the collapsed duplicates);
        marker lines (starting `... [`) never fold — the pass is idempotent.

    Purely lossless-in-meaning whitespace/echo folding: no content line is
    invented or dropped beyond exact duplicates."""
    lines = [ln.rstrip() for ln in text.split("\n")]
    # 1) blank runs: three or more consecutive blank lines -> one
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i]:
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and not lines[j]:
            j += 1
        run = j - i
        out.extend([""] * (1 if run >= 3 else run))
        i = j
    # 2) identical-line runs (markers excluded so re-runs stay stable)
    folded: list[str] = []
    i = 0
    while i < len(out):
        line = out[i]
        if line and not line.startswith("... ["):
            j = i
            while j + 1 < len(out) and out[j + 1] == line:
                j += 1
            if j > i:
                folded.append(line)
                folded.append(f"... [×{j - i} identical lines]")
                i = j + 1
                continue
        folded.append(line)
        i += 1
    return "\n".join(folded)


def finalize_result(result: str) -> str:
    """Post-process one tool result (car 4a-M3): compress first when above
    COMPRESS_THRESHOLD — with a single honest English note
    `[compressed {before}→{after} chars]`, never silently — then apply the
    hard MAX_RESULT_CHARS truncation unchanged. When truncation fires, both
    notes are preserved (compression note first, truncation marker last)."""
    notes = ""
    if len(result) > COMPRESS_THRESHOLD:
        compact = compress_output(result)
        if len(compact) < len(result):
            notes += (f"\n... [compressed {len(result)}→{len(compact)} "
                      "chars]")
            result = compact
    if len(result) > MAX_RESULT_CHARS:
        return (result[:MAX_RESULT_CHARS] + notes
                + f"\n... [truncated at {MAX_RESULT_CHARS} chars]")
    return result + notes


def _skill_route(query: str, topn: int = 3) -> str:
    """Deterministic skill pre-router (our P4 vendored lib/skill_route.py).

    Returns the router's text verdict — the loop reads it before loading any
    SKILL.md body (lazy-body discipline, docs/features/memory_and_routing.md).
    """
    if not isinstance(query, str) or not query.strip():
        raise ToolError("query must be a non-empty string")
    import sys
    sys.path.insert(0, str(REPO / "lib"))
    try:
        import skill_route as sr  # noqa: PLC0415 — vendored sibling module
    finally:
        sys.path.pop(0)
    roots = sr.skill_roots(None)
    hits = sr.route(query, roots, topn if isinstance(topn, int) and 1 <= topn <= 5 else 3)
    if not hits:
        return "no matching skill — use the generic procedure"
    out = []
    for i, h in enumerate(hits, 1):
        verdict = "TOP1: must load" if i == 1 else "load only if closely related"
        out.append(f"{i}. {h['name']} | matched: {h['reason']} | {verdict}")
    return "\n".join(out)


# --------------------------------------------------------------- build api ---

_STR = {"type": "string"}
_INT = {"type": "integer"}


def _cron(action: str, spec: str | None, text: str | None, kind: str | None,
          job_id: str | None, state_dir: str | Path | None) -> str:
    """The `cron` tool handler (Phase B2). Same spec grammar and registry as
    /cron — only the entry differs. Returns honest strings; refusals become
    ToolError -> `ERROR: <reason>` (the model can then correct itself)."""
    from . import scheduler  # local import: keeps the module graph acyclic

    if not isinstance(action, str):
        raise ToolError("action must be create | list | delete")
    path = (Path(state_dir) / scheduler.STATE_FILE_NAME
            if state_dir is not None else scheduler.default_jobs_path())
    jobs = scheduler.load_jobs(path)
    if action == "list":
        if not jobs:
            return "no scheduled jobs"
        now = time.time()
        lines = []
        for j in jobs:
            state = " (done)" if j.get("done") else ""
            lines.append(f"{j['id']} · {scheduler.describe(j)} · "
                         f"{j.get('kind')} · next "
                         f"{scheduler.fmt_beijing(scheduler.next_fire(j, now))}"
                         f"{state} · {j.get('text', '')[:60]}")
        return "\n".join(lines)
    if action == "delete":
        if not job_id:
            raise ToolError("delete needs the job id (see list)")
        if not scheduler.delete_job(jobs, job_id):
            raise ToolError(f"no job with id {job_id}")
        try:
            scheduler.save_jobs(path, jobs)
        except OSError:
            raise ToolError("job registry is not writable") from None
        return f"deleted {job_id}"
    if action == "create":
        if not spec or not text:
            raise ToolError("create needs spec and text")
        try:
            job = scheduler.create_job(jobs, spec, text, kind=kind or "message",
                                       source="model",
                                       origin=scheduler.get_origin())
            scheduler.save_jobs(path, jobs)
        except scheduler.SpecError as e:
            raise ToolError(str(e)) from None
        except OSError:
            raise ToolError("job registry is not writable") from None
        return (f"scheduled {job['id']}: {scheduler.describe(job)} · "
                f"{job['kind']} · next "
                f"{scheduler.fmt_beijing(scheduler.next_fire(job, time.time()))}")
    raise ToolError(f"action must be create | list | delete (got {action!r}")


_CRON_LOCK = Lock()   # delegate threads may hit the cron tool concurrently


def _cron_locked(action, spec, text, kind, job_id, state_dir):
    """cron tool entry serialized (car 2-D6): delegate threads may hit the
    registry concurrently and the job table is load-modify-save."""
    with _CRON_LOCK:
        return _cron(action, spec, text, kind, job_id, state_dir)


def _delegate(tasks: list, extra_tools: list | None,
              registry: "ToolRegistry") -> str:
    """The `delegate` tool handler (car 2-D6): isolated parallel sub-agents.
    Chat + budget come from the ACTIVE turn's binding (loop.run_turn) — a
    dispatch outside run_turn fails honestly instead of inventing a model."""
    from . import subagent  # local import: keeps the module graph acyclic
    chat, budget = registry.turn_binding()
    return subagent.run_tasks(tasks, registry, extra_tools, chat, budget)


def _execute_code(steps: list | None, code: str | None, timeout: int,
                  registry: "ToolRegistry",
                  roots: tuple[str, ...] | list[str],
                  state_dir: str | Path | None) -> str:
    """The `execute_code` tool handler (car 2-D7): steps fold or one-shot
    Python. ExecError (a caller mistake) maps to the honest `ERROR:` line;
    everything else is the child process's own output."""
    from . import execute_code  # local import: keeps the module graph acyclic
    try:
        return execute_code.execute(steps=steps, code=code, timeout=timeout,
                                    registry=registry, roots=roots,
                                    state_dir=state_dir)
    except execute_code.ExecError as e:
        raise ToolError(str(e)) from None


def build_registry(allowed_roots: tuple[str, ...] | list[str] | None = None,
                   state_dir: str | Path | None = None,
                   mcp_raw: str | None = None) -> ToolRegistry:
    """The 11 core tools (design §14.3 + cron/Phase B2 + car 2 delegate/
    execute_code) plus any configured MCP tools (car 2-D5).
    `allowed_roots`
    overrides path policy (tests use temp dirs; production default = repo +
    system temp). `state_dir` anchors the cron registry AND the pre-write
    checkpoints (service passes its own state dir; None = resolve
    AIDOG_MC_STATE at tool-call time). `mcp_raw` is the MCP server config
    (JSON array; None = resolve env MYCORE_MCP > settings mycore.mcp > off).
    MCP is fail-soft: a bad config or an unreachable server becomes an honest
    note on registry.mcp_notes — the 11 core tools always register."""
    roots = tuple(allowed_roots) if allowed_roots else DEFAULT_ALLOWED_ROOTS
    r = ToolRegistry()
    r.register("read_file", "Read a text file with line numbers.",
               {"type": "object", "properties": {
                   "path": _STR, "offset": _INT,
                   "limit": {"type": "integer", "minimum": 1, "maximum": 2000}},
                "required": ["path"]},
               lambda path, offset=0, limit=200: _read_file(path, offset, limit, roots))
    r.register("write_file", "Create or overwrite a text file.",
               {"type": "object", "properties": {"path": _STR, "content": _STR},
                "required": ["path", "content"]},
               lambda path, content: _write_file(path, content, roots, state_dir))
    r.register("edit_file", "Replace one unique occurrence of old_text in a file.",
               {"type": "object", "properties": {
                   "path": _STR, "old_text": _STR, "new_text": _STR},
                "required": ["path", "old_text", "new_text"]},
               lambda path, old_text, new_text: _edit_file(path, old_text, new_text, roots, state_dir))
    r.register("run_bash", f"Run a shell command in the repo root (<= {BASH_TIMEOUT}s).",
               {"type": "object", "properties": {
                   "command": _STR, "timeout": _INT}, "required": ["command"]},
               lambda command, timeout=BASH_TIMEOUT: _run_bash(command, timeout, roots))
    r.register("grep", "Regex search in files under a path (name glob optional).",
               {"type": "object", "properties": {
                   "pattern": _STR, "path": _STR, "glob": _STR},
                "required": ["pattern"]},
               lambda pattern, path=".", glob="*": _grep(pattern, path, glob, roots))
    r.register("glob", "Find files by glob pattern under a directory.",
               {"type": "object", "properties": {"pattern": _STR, "path": _STR},
                "required": ["pattern"]},
               lambda pattern, path=".": _glob(pattern, path, roots))
    r.register("http_fetch", "GET a URL and return its text (truncated).",
               {"type": "object", "properties": {"url": _STR, "timeout": _INT,
                                                 "max_chars": _INT},
                "required": ["url"]},
               lambda url, timeout=20, max_chars=8000: _http_fetch(url, timeout, max_chars))
    r.register("skill_route", "Deterministic skill pre-router (TOP3 before loading any SKILL.md body).",
               {"type": "object", "properties": {"query": _STR, "topn": _INT},
                "required": ["query"]},
               lambda query, topn=3: _skill_route(query, topn))
    r.register("cron", "Create, list, or delete scheduled jobs (natural-language cron). "
               "Translate the user's words into spec: 'remind me every day at 8' -> "
               "'daily 08:00'; 'every 30 minutes' -> 'every 30m'; 'next Monday 09:00' "
               "-> a 'once' date or 'weekly mon 09:00'. Spec forms: every <N>m|h | "
               "daily HH:MM | weekly <mon..sun> HH:MM | once YYYY-MM-DD HH:MM "
               "(Beijing time). kind='message' delivers text verbatim; kind='prompt' "
               "runs one model turn and delivers its result.",
               {"type": "object", "properties": {
                   "action": {"type": "string",
                              "enum": ["create", "list", "delete"]},
                   "spec": _STR, "text": _STR,
                   "kind": {"type": "string",
                              "enum": ["message", "prompt"]},
                   "id": _STR},
                "required": ["action"]},
               lambda action, spec=None, text=None, kind="message", id=None:
               _cron_locked(action, spec, text, kind, id, state_dir))
    r.register("delegate",
               "Delegate 1-3 independent tasks to isolated sub-agents running "
               "in parallel. Each sub-agent sees NO conversation history (put "
               "everything it needs in the task text) and gets only read-only "
               "tools by default (read_file, grep, glob, http_fetch, "
               "skill_route); name extra tools in `tools` to grant them "
               "explicitly. `delegate` and `execute_code` can never be granted "
               "to a sub-agent. Each tasks item is a string, or an object "
               "{task, model} to run that one sub-agent on a different model "
               "— model must be listed in settings mycore.models (an unknown "
               "or unconfigured list fails that task honestly). Returns one "
               "labelled block per task.",
               {"type": "object", "properties": {
                   "tasks": {"type": "array",
                             "items": {
                                 "anyOf": [
                                     {"type": "string"},
                                     {"type": "object",
                                      "properties": {
                                          "task": _STR, "model": _STR},
                                      "required": ["task"]}]},
                             "minItems": 1, "maxItems": 3,
                             "description": "1-3 independent task "
                                            "descriptions (plain strings, "
                                            "or {task, model} to override "
                                            "the model for that task)"},
                   "tools": {"type": "array", "items": _STR,
                             "description": "extra tool names to grant beyond "
                                            "the read-only default"}},
                "required": ["tasks"]},
               lambda tasks, tools=None: _delegate(tasks, tools, r))
    r.register("execute_code",
               "Fold multi-step work into ONE round trip. Exactly one of: "
               "`steps` = ordered [{name, args}, ...] tool calls (max 8) run "
               "back-to-back with every result folded into one report (no "
               "nested execute_code/delegate); `code` = Python source run "
               "once in a child process with helpers read/grep/glob/write/"
               "edit/run/fetch — allowed-roots enforced, write helpers take "
               "checkpoints, timeout <= 120s, output capped.",
               {"type": "object", "properties": {
                   "steps": {"type": "array",
                             "items": {"type": "object", "properties": {
                                 "name": _STR,
                                 "args": {"type": "object"}},
                                      "required": ["name"]}},
                   "code": _STR,
                   "timeout": {"type": "integer", "minimum": 1,
                               "maximum": 120}}},
               lambda steps=None, code=None, timeout=120:
                   _execute_code(steps, code, timeout, r, roots, state_dir))
    # --- car 2-D5: MCP servers (fail-soft: notes, never a startup crash) ---
    r.mcp_notes: list[str] = []
    r.mcp_sessions: list = []
    try:
        from . import mcp as mcp_mod          # local: optional dependency path
        raw = mcp_mod.raw_config() if mcp_raw is None else mcp_raw
        if raw and raw.strip():
            from .mcp import attach
            attach(r, raw)
    except ValueError as e:                   # config problem: honest note
        r.mcp_notes = [f"mcp config invalid: {e}"]
    return r
