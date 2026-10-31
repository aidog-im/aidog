"""mycore.checkpoint — pre-write file snapshots + rollback (car 1-D2).

Safety net for local agent writes: `write_file` / `edit_file` snapshot the
PREVIOUS bytes of a file into `<state>/checkpoints/` before mutating it, and
`/checkpoint restore <id>` (service command) or the CLI puts them back.

Design rules (why it looks like mycore.scheduler):
  * stdlib only, own state file, fail-soft where it must never break a write:
    a snapshot failure degrades to "no snapshot" and the write still runs —
    checkpointing is a safety net, not a gate. `restore` IS allowed to fail
    loudly (it is the recovery path — honest errors, never a fake success).
  * one append-only JSONL index (`index.jsonl`) maps id -> original path +
    snapshot file + size + sha256; snapshots are raw BYTES so restore is
    byte-exact for any file the text tools touched.
  * coverage is honest: only the two write tools snapshot (a `run_bash`
    redirect is out of reach of the registry — docs and /help say so).
  * rotation: MAX_ENTRIES newest entries are kept; older snapshot files are
    deleted with their index lines (same philosophy as the evolution archive
    rotation and session archives).

State: env AIDOG_MC_STATE when set, else <repo>/data/mycore_service
(gitignored) -> checkpoints/{index.jsonl, <id>_<hash>_<name>.snap}.
Cap: MAX_SNAPSHOT_BYTES per file (bigger files are skipped with an honest
note in the write result). Text tools only ever wrote utf-8 text anyway.
English-only source (repo i18n policy); user-visible strings resolve through
the locale packs in the service layer.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

STATE_DIR_NAME = "checkpoints"     # under the service state dir
INDEX_NAME = "index.jsonl"
MAX_ENTRIES = 100                 # rotation cap (newest kept)
MAX_SNAPSHOT_BYTES = 1_000_000    # per-file cap (skip honestly, never fail a write)
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
# Car 2-D6: parallel sub-agents may write through the checkpointed tools at
# the same time — id allocation (read index -> next_id -> append) must be
# serialized or two snapshots could claim the same id.
_SNAP_LOCK = threading.Lock()


class CheckpointError(Exception):
    """Honest recovery failure (bad id, missing/corrupt snapshot, io)."""


def default_state_dir() -> Path:
    """Same rule as the cron registry: env AIDOG_MC_STATE, else the repo's
    gitignored runtime state dir (resolved at call time, never at import)."""
    env = os.environ.get("AIDOG_MC_STATE", "").strip()
    if env:
        return Path(env)
    return REPO / "data" / "mycore_service"


def store_dir(state_dir: str | Path | None = None) -> Path:
    base = Path(state_dir) if state_dir is not None else default_state_dir()
    return base / STATE_DIR_NAME


def _read_index(path: Path) -> list[dict]:
    """Parse the JSONL index; corrupt lines are skipped (fail-soft), an
    unreadable/absent file is an empty index."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    entries: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("id"), int) \
                and isinstance(obj.get("file"), str) and isinstance(obj.get("path"), str):
            entries.append(obj)
    return entries


def _write_index(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n"
                   for e in entries)
    path.write_text(body, encoding="utf-8")


def _snap_name(entry_id: int, path: Path) -> str:
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:8]
    safe = _SAFE_NAME.sub("_", path.name)[:48] or "file"
    return f"{entry_id:06d}_{digest}_{safe}.snap"


def snapshot(path: Path, state_dir: str | Path | None = None,
             now: float | None = None) -> dict | None:
    """Snapshot the CURRENT bytes of `path` BEFORE a write. Returns the index
    entry, or None when nothing was snapshotted: file absent (a create needs
    no rollback), not a regular file, over MAX_SNAPSHOT_BYTES, or any io
    failure — never raises (a failing write must not be blocked by its own
    safety net)."""
    try:
        if not path.is_file():
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > MAX_SNAPSHOT_BYTES:
        return None
    store = store_dir(state_dir)
    with _SNAP_LOCK:
        entries = _read_index(store / INDEX_NAME)
        next_id = (max((e["id"] for e in entries), default=0)) + 1
        entry = {"id": next_id, "ts": float(now if now is not None else time.time()),
                 "path": str(path), "file": _snap_name(next_id, path),
                 "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        try:
            store.mkdir(parents=True, exist_ok=True)
            (store / entry["file"]).write_bytes(data)
            with (store / INDEX_NAME).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            return None
        entries.append(entry)
        if len(entries) > MAX_ENTRIES:
            _prune(store, entries)
    return entry


def _prune(store: Path, entries: list[dict]) -> None:
    """Keep the newest MAX_ENTRIES; drop old index lines and their snapshot
    files (best-effort unlink — an orphan .snap is harmless, a lost index line
    would be, so the index is rewritten from what we still have)."""
    keep = entries[-MAX_ENTRIES:]
    for e in entries[:-MAX_ENTRIES]:
        try:
            (store / e["file"]).unlink()
        except OSError:
            continue
    try:
        _write_index(store / INDEX_NAME, keep)
    except OSError:
        pass


def list_entries(state_dir: str | Path | None = None,
                 limit: int = 10) -> list[dict]:
    """Newest-first index entries (limit > 0). An unreadable store = []."""
    entries = _read_index(store_dir(state_dir) / INDEX_NAME)
    entries.sort(key=lambda e: e["id"], reverse=True)
    return entries[:limit] if limit > 0 else entries


def restore(entry_id: int, state_dir: str | Path | None = None,
            now: float | None = None) -> dict:
    """Put the snapshotted bytes back. Before overwriting, the CURRENT file
    is snapshotted too, so a restore is itself undoable. Returns the entry
    used. Raises CheckpointError on: unknown id, missing/corrupt snapshot
    (sha256/size verified), io failure — never a silent no-op."""
    store = store_dir(state_dir)
    entries = _read_index(store / INDEX_NAME)
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if entry is None:
        raise CheckpointError(f"no checkpoint with id {entry_id}")
    snap = store / entry["file"]
    try:
        data = snap.read_bytes()
    except OSError:
        raise CheckpointError(f"snapshot file for #{entry_id} is missing") from None
    if len(data) != entry.get("size", -1) \
            or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
        raise CheckpointError(f"snapshot for #{entry_id} fails its integrity check")
    target = Path(entry["path"])
    # undo-of-the-undo: current content is snapshotted first (best effort)
    if target.exists():
        snapshot(target, state_dir, now=now)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as e:
        raise CheckpointError(
            f"cannot restore {target.name}: {type(e).__name__}") from None
    return entry


def fmt_when(ts: float) -> str:
    """Fixed +08:00 stamp (same convention as the cron scheduler)."""
    from datetime import datetime, timedelta, timezone
    return datetime.fromtimestamp(ts, timezone(timedelta(hours=8))
                                  ).strftime("%Y-%m-%d %H:%M")


def main(argv: list[str] | None = None) -> int:
    """Ops CLI (mirrors mycore.scheduler): --list · --restore ID · --state DIR.
    Exit codes: 0 ok · 1 recovery/io failed · 2 bad arguments."""
    import argparse
    ap = argparse.ArgumentParser(prog="mycore.checkpoint",
                                 description="list or restore file checkpoints")
    ap.add_argument("--list", action="store_true", help="list newest entries")
    ap.add_argument("--restore", default=None, metavar="ID",
                    help="restore one checkpoint by id")
    ap.add_argument("--state", default=None, help="state dir override")
    ap.add_argument("--json", action="store_true", help="machine-readable --list")
    args = ap.parse_args(argv)
    if args.restore is not None:
        if not args.restore.isdigit():
            print("usage: python3 -m mycore.checkpoint --restore <id>", flush=True)
            return 2
        try:
            entry = restore(int(args.restore), args.state)
        except CheckpointError as e:
            print(f"ERROR: {e}", flush=True)
            return 1
        print(f"restored #{entry['id']} -> {entry['path']} ({entry['size']} bytes)")
        return 0
    if args.list or args.json:
        entries = list_entries(args.state, limit=MAX_ENTRIES)
        if args.json:
            print(json.dumps(entries, ensure_ascii=False, indent=2))
        elif not entries:
            print("no checkpoints")
        else:
            for e in entries:
                print(f"#{e['id']}  {fmt_when(e['ts'])}  {e['size']}B  {e['path']}")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
