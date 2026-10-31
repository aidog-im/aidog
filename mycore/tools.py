"""mycore.tools — tool registry + the 8 core tools (design §14.3).

Registry exposes OpenAI JSON-schema `tools` for the model boundary and a
dispatcher for the loop. Handler contract (README): return `str`; exceptions
become honest `ERROR:` strings — a failing tool never crashes the loop.
Path tools enforce allowed roots (README contract 4). stdlib only.
English comments per repo i18n policy.
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parent.parent

DEFAULT_ALLOWED_ROOTS = (str(REPO), tempfile.gettempdir())
MAX_RESULT_CHARS = 24000          # tool output cap (context budget discipline)
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

    def register(self, name: str, description: str, parameters: dict,
                 handler: Callable[..., str]) -> None:
        if name in self._tools:
            raise ValueError(f"duplicate tool name: {name}")
        self._tools[name] = Tool(name, description, parameters, handler)

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
        if len(result) > MAX_RESULT_CHARS:
            result = result[:MAX_RESULT_CHARS] + f"\n... [truncated at {MAX_RESULT_CHARS} chars]"
        return result


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


def _write_file(path: str, content: str,
                roots: tuple[str, ...] | list[str] = DEFAULT_ALLOWED_ROOTS) -> str:
    p = resolve_inside(path, roots)
    if p.is_dir():
        raise ToolError(f"is a directory: {p}")
    if not isinstance(content, str):
        raise ToolError("content must be a string")
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(content, encoding="utf-8")
    except OSError as e:
        raise ToolError(f"cannot write {p.name}: {type(e).__name__}") from None
    return f"wrote {len(content)} chars to {p}"


def _edit_file(path: str, old_text: str, new_text: str,
               roots: tuple[str, ...] | list[str] = DEFAULT_ALLOWED_ROOTS) -> str:
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
    p.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
    return f"edited {p} (1 replacement)"


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


def build_registry(allowed_roots: tuple[str, ...] | list[str] | None = None) -> ToolRegistry:
    """The 8 core tools (design §14.3). `allowed_roots` overrides path policy
    (tests use temp dirs; production default = repo + system temp)."""
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
               lambda path, content: _write_file(path, content, roots))
    r.register("edit_file", "Replace one unique occurrence of old_text in a file.",
               {"type": "object", "properties": {
                   "path": _STR, "old_text": _STR, "new_text": _STR},
                "required": ["path", "old_text", "new_text"]},
               lambda path, old_text, new_text: _edit_file(path, old_text, new_text, roots))
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
    return r
