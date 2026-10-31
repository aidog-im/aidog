#!/usr/bin/env python3
"""aidog_memory — productized dual-layer memory manager (budget-aware snapshot sync).

Feature spec (docs/features/memory_and_routing.md):
  Layer 1 (curated, budget-limited): memories/MEMORY.md  <= 2200 chars
                                       memories/USER.md   <= 1375 chars
  Layer 2 (archive, unlimited):       memories/ARCHIVE/*  read via file tools
  Source of truth during dual-brain phase: pi project memory (one-way snapshot).

Memory home resolution (P5-4, docs/features/memory_and_routing.md) — the
DST side of sync/status follows a portable 4-step chain, first hit wins:
  1. env AIDOG_MEMORY_HOME
  2. MEMORY_HOME in deploy/memory_sources.local (gitignored machine config)
  3. $HOME/.aidog/memories — post-swap default; step hits only when the
     portable home already exists (materialized by sync/import)
  4. <AIDOG_PROFILE_DIR>/memories from deploy/upstream_shim.env — LEGACY
     transition fallback, honored ONLY while that shim file exists (P5-5
     deletes it; afterwards step 3 becomes the final fallback and `sync`
     creates the home on fresh machines).
This module is the single source of truth for that chain: aidog_persist.py
imports resolve_memory_home() instead of re-implementing it.

Usage:
  python3 aidog_memory.py sync     # refresh snapshot from source (idempotent)
  python3 aidog_memory.py status   # budget report + provenance (no content echo)
Exit codes: 0 ok · 1 source/target error · 2 budget exceeded.
English comments per repo i18n policy.
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import re as _re

REPO = Path(__file__).resolve().parent.parent  # self-deriving: no machine path


def _local_kv(name: str) -> str | None:
    """Read deploy/memory_sources.local (gitignored machine-local config)."""
    try:
        for line in (REPO / "deploy/memory_sources.local").read_text().splitlines():
            m = _re.match(r"^\s*([A-Z0-9_]+)=(.*?)\s*$", line)
            if m and m.group(1) == name and m.group(2) and not m.group(2).startswith("#"):
                return m.group(2)
    except OSError:
        return None
    return None


def _src_kv(name: str) -> str | None:
    """Resolve a memory source key: env AIDOG_<name> first, then the local file.

    Self-containment chain (owner decree, cross-machine revival): environment
    override > deploy/memory_sources.local > unset (None). Set-but-empty env
    counts as unset.
    """
    v = os.environ.get("AIDOG_" + name)
    if isinstance(v, str) and v.strip():
        return v.strip()
    return _local_kv(name)


SRC = Path(_src_kv("MEMORY_SRC") or "")
PERSONA = Path(_src_kv("MEMORY_PERSONA") or "")


def _legacy_profile_dir() -> Path | None:
    """LEGACY transition profile dir (deploy/upstream_shim.env → AIDOG_PROFILE_DIR).

    Honored ONLY while the shim file exists — it is deleted in the P5-5
    same-car swap; every caller must treat a None return as "no legacy home".
    No machine path is ever written here: the value is parsed from the shim.
    """
    try:
        for _line in (REPO / "deploy/upstream_shim.env").read_text().splitlines():
            _m = _re.match(r'^\s*export\s+AIDOG_PROFILE_DIR="([^"]+)"\s*$', _line)
            if _m:
                return Path(_m.group(1))
    except OSError:
        pass
    return None


def resolve_memory_home() -> tuple[Path, str]:
    """-> (memory home, source label) via the portable 4-step chain (first hit wins).

    1. env AIDOG_MEMORY_HOME  2. local MEMORY_HOME  3. $HOME/.aidog/memories
    (only when it already exists)  4. legacy shim profile memories (only while
    deploy/upstream_shim.env exists) → final fallback: the post-swap default,
    which `sync` creates (mkdir parents) on fresh machines.
    Pure function of env/config/filesystem: no prints (import-safe).
    """
    v = os.environ.get("AIDOG_MEMORY_HOME")
    if isinstance(v, str) and v.strip():
        return Path(v.strip()), "env AIDOG_MEMORY_HOME"
    local = _local_kv("MEMORY_HOME")
    if local:
        return Path(local), "local MEMORY_HOME"
    default = Path(os.path.expanduser("~/.aidog/memories"))
    if default.is_dir():
        return default, "default ~/.aidog/memories"
    legacy = _legacy_profile_dir()
    if legacy is not None:
        return legacy / "memories", "legacy shim profile"
    return default, "default ~/.aidog/memories"


DST, DST_SOURCE = resolve_memory_home()
BUDGETS = {"MEMORY.md": 2200, "USER.md": 1375}


def sync() -> int:
    if not str(SRC) or not (SRC / "MEMORY.md").is_file():
        print("memory source not configured — create deploy/memory_sources.local "
              "(see memory_sources.example)")
        return 1
    # write-path safety: a fresh machine has no home yet — create it (parents)
    # instead of failing; this is the ONLY place the product materializes DST.
    try:
        DST.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"cannot create memory home {DST}: {type(e).__name__}")
        return 1
    (DST / "ARCHIVE" / "daily").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC / "MEMORY.md", DST / "ARCHIVE" / "MEMORY_full.md")
    if (SRC / "daily").is_dir():
        for f in (SRC / "daily").glob("*.md"):
            shutil.copy2(f, DST / "ARCHIVE" / "daily" / f.name)
    if (PERSONA / "profile.md").is_file():
        shutil.copy2(PERSONA / "profile.md", DST / "ARCHIVE" / "USER_full.md")
    shutil.copy2(REPO / "overlay/memory/MEMORY_curated.md", DST / "MEMORY.md")
    shutil.copy2(REPO / "overlay/memory/USER_curated.md", DST / "USER.md")
    (DST / "SNAPSHOT_INFO.md").write_text(
        "# Snapshot provenance\n"
        f"- Source: pi-side project memory (authoritative, one-way snapshot)\n"
        f"- Top slots curated from repo overlay/memory/*_curated.md; full text in ARCHIVE/\n"
        f"- Refresh: python3 {REPO}/deploy/aidog_memory.py sync\n"
        f"- Synced (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
    )
    print("sync ok")
    return status()


def status() -> int:
    rc = 0
    print(f"home: {DST} (source={DST_SOURCE})")
    for name, limit in BUDGETS.items():
        p = DST / name
        if not p.is_file():
            print(f"FAIL {name}: missing")
            rc = 1
            continue
        n = len(p.read_text())
        level = "ok" if n <= limit else "OVER"
        if n > limit:
            rc = 2
        print(f"{level:4} {name}: {n}/{limit} chars")
    arch = DST / "ARCHIVE" / "MEMORY_full.md"
    print(f"{'ok' if arch.is_file() else 'FAIL'}  ARCHIVE/MEMORY_full.md: "
          f"{arch.stat().st_size // 1024}KB" if arch.is_file() else "FAIL  ARCHIVE missing")
    daily = DST / "ARCHIVE" / "daily"
    n_daily = len(list(daily.glob('*.md'))) if daily.is_dir() else 0
    print(f"ok   ARCHIVE/daily: {n_daily} files")
    info = DST / "SNAPSHOT_INFO.md"
    if info.is_file():
        for line in info.read_text().splitlines():
            if line.startswith("- Synced"):
                print(line)
    return rc


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "sync":
        return sync()
    if cmd == "status":
        return status()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
