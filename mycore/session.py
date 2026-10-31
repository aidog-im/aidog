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
