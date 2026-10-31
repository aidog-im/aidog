#!/usr/bin/env python3
"""aidog_persist — I'm AiDog CLI: one-command setup + memory portability.

  ./aidog setup                # interactive setup wizard (fill/validate
                               # credential slots, then optional boot + verify);
                               # --check = status report, --yes = non-interactive
                               # idempotent installer (provision + boot + verify)
  ./aidog profile list         # car F §13.1: default + per-profile runtime dirs
  ./aidog profile create <name>  # scaffold profiles/<name> (seeded, own slots)
  ./aidog profile rm <name> [--force]  # delete a non-default profile dir
  ./aidog export [--src DIR] [--persona DIR] [--out DIR] [--tag NAME]
  ./aidog import <tarball> [--dest-profile DIR] [--source-home DIR] [--persona-home DIR]
  ./aidog list [--dir DIR]

export bundles the authoritative memory source (+ optional persona) into a
tarball for transport through the PRIVATE memory repo; import restores it on
a fresh machine. Source resolution (export): --src > env AIDOG_MEMORY_SRC > MEMORY_SRC in
deploy/memory_sources.local > portable default $HOME/.aidog/memory (materialized
by a previous `aidog import`) > the portable memory-home chain (same 4-step
chain as aidog_memory.py: env AIDOG_MEMORY_HOME > local MEMORY_HOME >
$HOME/.aidog/memories > legacy profile <AIDOG_PROFILE_DIR>/memories while
deploy/upstream_shim.env exists; source_kind=profile-fallback only for the
legacy profile step). Persona: --persona > env AIDOG_MEMORY_PERSONA >
MEMORY_PERSONA in the local file. A configured-but-missing path is a hard error
(never silently export the wrong snapshot; never invent paths).

Credential gate (export): every payload file is scanned BEFORE anything is
written; credential-shaped content refuses the export (exit 3) and matched
values are never echoed. Machine paths, brand words and repo names ARE allowed —
this bundle travels only through the private memory repo.

Import hardening: sidecar sha256 (when present) + per-file sha256 against
manifest.json + tar-slip defense (no absolute paths, no '..', no links);
nothing is extracted until every check passes.

Exit codes: 0 ok · 1 source/verification/config error · 3 credential gate refusal.
English output per repo i18n policy; secrets are never printed (labels only).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parent.parent  # self-deriving: no machine path
DEFAULT_OUT = REPO / "exports"
FORMAT_VERSION = 1


class BundleError(Exception):
    """Any bundle verification failure (message is safe to print)."""


# ---------------------------------------------------------------------------
# config resolution (env > deploy/memory_sources.local > defaults)
# ---------------------------------------------------------------------------

def _local_kv(name: str) -> str | None:
    """Read a key from deploy/memory_sources.local (gitignored machine config)."""
    try:
        for line in (REPO / "deploy/memory_sources.local").read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Z0-9_]+)=(.*?)\s*$", line)
            if m and m.group(1) == name and m.group(2) and not m.group(2).startswith("#"):
                return m.group(2)
    except OSError:
        return None
    return None


def _env_kv(name: str) -> str | None:
    """Env override AIDOG_<KEY> (set-but-empty counts as unset)."""
    v = os.environ.get("AIDOG_" + name)
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


def _memory_home() -> tuple[Path, str]:
    """Portable memory home via the shared 4-step chain (P5-4).

    Single source of truth lives in deploy/aidog_memory.py
    (resolve_memory_home): env AIDOG_MEMORY_HOME > local MEMORY_HOME >
    $HOME/.aidog/memories (when materialized) > legacy shim profile memories
    (only while deploy/upstream_shim.env exists) > post-swap default.
    """
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import aidog_memory  # noqa: PLC0415 — sibling module, same deploy/ dir
    return aidog_memory.resolve_memory_home()


def _resolve_source(explicit: str | None) -> tuple[Path | None, str | None]:
    """-> (source dir, source_kind). None/None when nothing is resolvable."""
    if explicit:
        return Path(explicit).expanduser(), "pi-memory"
    for value in (_env_kv("MEMORY_SRC"), _local_kv("MEMORY_SRC")):
        if value:
            return Path(value).expanduser(), "pi-memory"
    portable = Path(os.path.expanduser("~/.aidog/memory"))
    if portable.is_dir():  # materialized by `aidog import` — self-contained default
        return portable, "pi-memory"
    # step 4 of the source chain: the portable memory-home chain (legacy shim
    # profile only survives while deploy/upstream_shim.env exists).
    home, kind = _memory_home()
    if home.is_dir():
        return home, "profile-fallback" if kind == "legacy shim profile" else "pi-memory"
    return None, None


def _resolve_persona(explicit: str | None) -> Path | None:
    """Persona chain: --persona > env AIDOG_MEMORY_PERSONA > local MEMORY_PERSONA."""
    for value in (explicit, _env_kv("MEMORY_PERSONA"), _local_kv("MEMORY_PERSONA")):
        if value:
            return Path(value).expanduser()
    return None


# ---------------------------------------------------------------------------
# credential gate (labels from deploy/check_publish.py + secret-shape regexes)
# ---------------------------------------------------------------------------

_GATE_KEEP = {"filled-credential", "personal-tg"}
_EXTRA_GATE = [
    ("github-or-aws-key", re.compile(r"(?i)\b(?:ghp_|github_pat_|AKIA)[A-Za-z0-9_-]{10,}\b")),
    ("openai-style-key", re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("bot-token", re.compile(r"(?i)\b\d{8,10}:[A-Za-z0-9_-]{35,}\b")),
    ("inline-secret-assignment",
     re.compile(r"(?i)\b(?:TOKEN|PAT|SECRET|PASSWORD|API_KEY)\s*[=:]\s*\S{8,}")),
]


def _gate_patterns() -> list[tuple[str, re.Pattern]]:
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        from check_publish import leak_patterns  # SSOT for the leak labels
    except Exception:  # gate is development-only; degrade instead of crashing
        return list(_EXTRA_GATE)
    pats = [(n, rx) for n, rx in leak_patterns() if n in _GATE_KEEP]
    return pats + list(_EXTRA_GATE)


def _gate(blobs: dict[str, bytes]) -> int:
    """Scan every file that would go into the bundle. 0 = pass, 3 = refused.

    Refused labels are printed with their bundle-relative path; the matched
    value itself is NEVER echoed (credential discipline).
    """
    pats = _gate_patterns()
    findings: dict[str, set[str]] = {}
    for rel in sorted(blobs):
        text = blobs[rel].decode("utf-8", errors="replace")
        labels = {n for n, rx in pats if rx.search(text)}
        if labels:
            findings[rel] = labels
    if not findings:
        return 0
    print("export refused by credential gate (matched values are never displayed):")
    for rel in sorted(findings):
        for label in sorted(findings[rel]):
            print(f"  REFUSED  {rel}  [{label}]")
    total = sum(len(v) for v in findings.values())
    print(f"blocked {len(findings)} file(s), {total} hit(s): redact the payload, then re-export")
    return 3


# ---------------------------------------------------------------------------
# bundle building / verification
# ---------------------------------------------------------------------------

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _collect(root: Path, prefix: str) -> list[tuple[str, Path]]:
    """Regular files under root -> [(bundle-relative path, source path)], sorted."""
    out: list[tuple[str, Path]] = []
    for p in sorted(root.rglob("*")):
        rel = prefix + p.relative_to(root).as_posix()
        if p.is_symlink():
            print(f"note: skipping symlink {rel}")
            continue
        if p.is_file():
            out.append((rel, p))
    return out


def _build_tarball(manifest: dict, blobs: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        def add(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = int(time.time())
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
        add("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        for name in sorted(blobs):
            add(name, blobs[name])
    return buf.getvalue()


def _safe_relpath(name: str, what: str) -> PurePosixPath:
    """Tar-slip defense for a member/manifest path. Raises BundleError."""
    p = PurePosixPath(name)
    if not name or p.is_absolute() or name.startswith("/") or ".." in p.parts:
        raise BundleError(f"unsafe entry path in {what}: {name!r}")
    if not (name == "manifest.json" or name.startswith("memory/") or name.startswith("persona/")):
        raise BundleError(f"unexpected entry in {what}: {name!r}")
    return p


def _sidecar_state(tar_path: Path) -> tuple[str, str]:
    """-> (ok|missing|bad, detail). Sidecar = '<tarball>.sha256'."""
    side = Path(str(tar_path) + ".sha256")
    if not side.is_file():
        return "missing", ""
    try:
        want = side.read_text(encoding="utf-8").split()[0].lower()
    except (OSError, UnicodeDecodeError, IndexError) as e:
        return "bad", f"sidecar unreadable ({type(e).__name__})"
    if not re.fullmatch(r"[0-9a-f]{64}", want):
        return "bad", "sidecar malformed"
    try:
        got = _sha(tar_path.read_bytes())
    except OSError as e:
        return "bad", f"bundle unreadable ({type(e).__name__})"
    return ("ok", "") if want == got else ("bad", "sidecar sha256 mismatch")


def _open_bundle(tar_path: Path) -> tuple[dict, dict[str, bytes]]:
    """Fully verify a bundle. Raises BundleError before ANY extraction.

    Returns (manifest, payload) where payload maps bundle-relative paths
    (memory/** + persona/**) to raw bytes — all sha256-checked against the
    manifest, with no extra and no missing entries.
    """
    try:
        blob = tar_path.read_bytes()
        tar = tarfile.open(fileobj=io.BytesIO(blob), mode="r:*")
    except (OSError, tarfile.TarError) as e:
        raise BundleError(f"unreadable tarball ({type(e).__name__})") from e
    payload: dict[str, bytes] = {}
    manifest_blob: bytes | None = None
    with tar:
        for m in tar.getmembers():
            name = m.name
            if name == "manifest.json":
                if not m.isreg():
                    raise BundleError("manifest.json is not a regular file")
                if manifest_blob is not None:
                    raise BundleError("duplicate manifest.json")
                manifest_blob = tar.extractfile(m).read()  # type: ignore[union-attr]
                continue
            _safe_relpath(name, "tarball")  # rejects '..', absolute, foreign prefixes
            if m.issym() or m.islnk() or m.isdev():
                raise BundleError(f"special entry refused: {name!r}")
            if m.isdir():
                continue  # layout-only entry, never written as a file
            if not m.isreg():
                raise BundleError(f"special entry refused: {name!r}")
            if name in payload:
                raise BundleError(f"duplicate entry: {name!r}")
            payload[name] = tar.extractfile(m).read()  # type: ignore[union-attr]
    if manifest_blob is None:
        raise BundleError("manifest.json missing")
    try:
        manifest = json.loads(manifest_blob)
    except ValueError as e:
        raise BundleError("manifest.json unreadable") from e
    if not isinstance(manifest, dict) or manifest.get("format_version") != FORMAT_VERSION:
        raise BundleError("unsupported manifest format_version (want 1)")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise BundleError("manifest files list empty")
    seen: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise BundleError("manifest file entry malformed")
        path, size, want = entry.get("path"), entry.get("size"), entry.get("sha256")
        if not isinstance(path, str) or not isinstance(size, int) or size < 0:
            raise BundleError("manifest file entry malformed")
        if not isinstance(want, str) or not re.fullmatch(r"[0-9a-f]{64}", want):
            raise BundleError(f"manifest sha256 malformed for {path!r}")
        _safe_relpath(path, "manifest")
        if path in seen:
            raise BundleError(f"duplicate manifest path: {path!r}")
        seen.add(path)
        if path not in payload:
            raise BundleError(f"payload missing: {path!r}")
        data = payload[path]
        if len(data) != size:
            raise BundleError(f"size mismatch: {path!r}")
        if _sha(data) != want:
            raise BundleError(f"sha256 mismatch: {path!r}")
    extras = set(payload) - seen
    if extras:
        raise BundleError(f"payload entries not listed in manifest: {sorted(extras)[:3]!r}")
    if not any(p.startswith("memory/") for p in payload):
        raise BundleError("bundle has no memory/ payload")
    return manifest, payload


def _extract(payload: dict[str, bytes], prefix: str, root: Path) -> int:
    """Write all payload files under prefix into root (safe relative joins)."""
    n = 0
    for path in sorted(payload):
        if not path.startswith(prefix):
            continue
        rel = PurePosixPath(path[len(prefix):])
        target = root.joinpath(*rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload[path])
        n += 1
    return n


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------

def cmd_export(args: argparse.Namespace) -> int:
    src, kind = _resolve_source(args.src)
    if src is None:
        print("memory source not configured: pass --src, set AIDOG_MEMORY_SRC, "
              "or set MEMORY_SRC in deploy/memory_sources.local (see memory_sources.example)")
        return 1
    if not src.is_dir():
        print(f"memory source not found: {src}")
        return 1

    tag = None
    if args.tag:
        tag = re.sub(r"[^A-Za-z0-9._-]+", "_", args.tag).strip("_")
        if not tag:
            print("--tag contains no usable characters")
            return 1

    persona = _resolve_persona(args.persona)
    if persona is not None and not persona.is_dir():
        if args.persona:  # explicitly requested -> hard error (typo safety)
            print(f"persona source not found: {persona}")
            return 1
        print(f"note: persona source missing ({persona}) — skipping persona/")
        persona = None

    entries = _collect(src, "memory/")
    if not entries:
        print(f"memory source is empty: {src}")
        return 1
    if persona is not None:
        entries += _collect(persona, "persona/")

    # read once: gate exactly what gets packed
    blobs: dict[str, bytes] = {}
    for rel, path in entries:
        try:
            blobs[rel] = path.read_bytes()
        except OSError as e:
            print(f"cannot read {rel}: {type(e).__name__}")
            return 1

    rc = _gate(blobs)
    if rc != 0:
        return rc  # exit 3 — nothing was written

    now = datetime.now(timezone.utc)
    manifest = {
        "format_version": FORMAT_VERSION,
        "tool": "aidog_persist",
        "created_at": now.isoformat(timespec="seconds"),
        "tag": tag,
        "source_kind": kind,
        "files": [{"path": rel, "size": len(data), "sha256": _sha(data)}
                  for rel, data in ((r, blobs[r]) for r in sorted(blobs))],
        "totals": {"files": len(blobs), "bytes": sum(len(d) for d in blobs.values())},
    }
    out_dir = Path(args.out).expanduser() if args.out else DEFAULT_OUT
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"cannot create output dir {out_dir}: {type(e).__name__}")
        return 1
    if not out_dir.is_dir():
        print(f"output path is not a directory: {out_dir}")
        return 1

    base = f"aidog_memory_{now.strftime('%Y%m%d-%H%M')}" + (f"_{tag}" if tag else "")
    name = base + ".tar.gz"
    n = 2
    while (out_dir / name).exists():  # same-minute re-export: never clobber
        name = f"{base}_{n}.tar.gz"
        n += 1
    tarball = _build_tarball(manifest, blobs)
    (out_dir / name).write_bytes(tarball)
    digest = _sha(tarball)
    Path(str(out_dir / name) + ".sha256").write_text(f"{digest}  {name}\n", encoding="utf-8")

    n_mem = sum(1 for f in manifest["files"] if f["path"].startswith("memory/"))
    n_per = len(manifest["files"]) - n_mem
    print(f"export ok: {out_dir / name}")
    print(f"  files: {manifest['totals']['files']} (memory: {n_mem}, persona: {n_per}) "
          f"· bytes: {manifest['totals']['bytes']} · source: {kind}")
    print(f"  sidecar: {name}.sha256")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    tar_path = Path(args.tarball).expanduser()
    if not tar_path.is_file():
        print(f"bundle not found: {tar_path}")
        return 1

    state, detail = _sidecar_state(tar_path)
    if state == "bad":
        print(f"sidecar check failed: {detail} — refusing import")
        return 1
    if state == "missing":
        print("note: no sidecar .sha256 — relying on manifest per-file hashes")

    try:
        manifest, payload = _open_bundle(tar_path)
    except (BundleError, OSError) as e:
        print(f"bundle verification failed: {e} — nothing extracted")
        return 1

    # resolve every destination BEFORE writing anything — the same portable
    # 4-step chain as aidog_memory (env > local > ~/.aidog > legacy shim).
    if args.dest_profile:
        memories = Path(args.dest_profile).expanduser() / "memories"
        home_label = "--dest-profile"
    else:
        memories, home_label = _memory_home()
    source_home = (Path(args.source_home).expanduser() if args.source_home
                   else Path(os.path.expanduser("~/.aidog/memory")))
    persona_home = _resolve_persona_home(args.persona_home)

    n_mem = _extract(payload, "memory/", memories)
    n_per = _extract(payload, "persona/", persona_home)
    n_src = _extract(payload, "memory/", source_home)
    print(f"restored: {n_mem} memory file(s) -> {memories} (home={home_label})")
    if n_per:
        print(f"restored: {n_per} persona file(s) -> {persona_home}")
        if not args.persona_home and persona_home == Path(os.path.expanduser("~/.legacy/persona")):
            print("note: persona home is legacy — migrate with: "
                  "mv ~/.legacy/persona ~/.aidog/persona")
    print(f"source home: {n_src} file(s) -> {source_home} (self-contained)")

    _configure_local(source_home, persona_home)
    budget = _budget_report(memories)
    print(f"import ok: {n_mem + n_per + n_src} file write(s) · budget: {budget}")
    return 0


def _resolve_persona_home(explicit: str | None) -> Path:
    """Persona extraction home: explicit flag > $HOME/.aidog/persona (default)
    > legacy-compat $HOME/.legacy/persona (pre-rename leftover) only while the
    default does NOT exist yet. No file is ever moved — the caller prints a
    one-line migration hint when the legacy location is used."""
    if explicit:
        return Path(explicit).expanduser()
    default = Path(os.path.expanduser("~/.aidog/persona"))
    legacy = Path(os.path.expanduser("~/.legacy/persona"))
    if not default.is_dir() and legacy.is_dir():
        return legacy
    return default


def _configure_local(source_home: Path, persona_home: Path) -> None:
    """Write deploy/memory_sources.local ONLY when absent — never clobber."""
    local = REPO / "deploy/memory_sources.local"
    if local.exists():
        print("memory_sources.local: exists — kept as-is (never clobbered); it points at:")
        try:
            lines = [ln.strip() for ln in local.read_text(encoding="utf-8").splitlines()
                     if ln.strip() and not ln.strip().startswith("#")]
        except OSError:
            lines = ["<unreadable>"]
        for ln in lines:
            print(f"  {ln}")
        return
    local.write_text(f"MEMORY_SRC={source_home}\nMEMORY_PERSONA={persona_home}\n",
                     encoding="utf-8")
    print(f"memory_sources.local: written (MEMORY_SRC={source_home})")


def _budget_report(memories: Path) -> str:
    """Run aidog_memory.status (imported, never duplicated) against the restore target."""
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import aidog_memory as jm
        jm.DST = memories          # report on what THIS import restored
        jm.DST_SOURCE = "import-target"
        print("budget check (aidog_memory status):")
        brc = jm.status()
        return {0: "ok", 2: "OVER"}.get(brc, "incomplete")
    except Exception as e:  # noqa: BLE001 — budget report must not fail an import
        return f"skipped ({type(e).__name__})"


def cmd_list(args: argparse.Namespace) -> int:
    d = Path(args.dir).expanduser() if args.dir else DEFAULT_OUT
    if not d.is_dir():
        print(f"no bundles: {d} (no export directory)")
        return 0
    bundles = sorted(d.glob("*.tar.gz"))
    if not bundles:
        print(f"no bundles: {d}")
        return 0
    bad = 0
    for t in bundles:
        try:
            size = t.stat().st_size
        except OSError as e:
            bad += 1
            print(f"BAD   {t.name}  ({type(e).__name__})")
            continue
        state, detail = _sidecar_state(t)
        if state != "ok":
            bad += 1
            reason = detail if state == "bad" else "no sidecar .sha256"
            print(f"BAD   {t.name}  {size // 1024}KB  ({reason})")
            continue
        try:
            manifest, _ = _open_bundle(t)
        except (BundleError, OSError) as e:
            bad += 1
            print(f"BAD   {t.name}  {size // 1024}KB  ({e})")
            continue
        date = str(manifest.get("created_at") or "")[:19]
        print(f"OK    {t.name}  {date}  {len(manifest['files'])} files  {size // 1024}KB")
    print(f"{len(bundles) - bad} OK · {bad} BAD")
    return 1 if bad else 0


# ---------------------------------------------------------------------------
# profile subcommands (car F §13.1: one machine, many dogs — named runtime dirs)
# ---------------------------------------------------------------------------

_PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_DEFAULT_PROFILE_LABEL = "DEFAULT"   # the implicit no-AIDOG_PROFILE profile


def _profile_paths():
    """Import lib.profile_paths with the repo root on sys.path (aidog_persist
    only guarantees its own deploy/ dir — see the sibling-module pattern)."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from lib import profile_paths  # noqa: PLC0415 — repo root resolved at call time
    return profile_paths


def _profile_meta(root: Path) -> dict:
    """profile.json contents ({} when absent/unreadable — never raises)."""
    try:
        doc = json.loads((root / "profile.json").read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _profile_web_port(root: Path) -> int | None:
    port = _profile_meta(root).get("web_port")
    return port if isinstance(port, int) else None


def _profile_dirs() -> list[tuple[str, Path]]:
    """(name, path) for every validly-named profile dir under profiles_root,
    sorted by name. Invalidly-named dirs are listed but never adopted."""
    pp = _profile_paths()
    root = pp.profiles_root(REPO)
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if p.is_dir() and _PROFILE_NAME_RE.match(p.name):
            out.append((p.name, p))
    return out


def _copy_atomic(src: Path, dst: Path) -> bool:
    """Byte-for-byte copy src -> dst via a temp file + os.replace. False when
    src is missing (callers decide whether that is fatal)."""
    try:
        blob = src.read_bytes()
    except OSError:
        return False
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, dst)
    return True


def cmd_profile_list(_args: argparse.Namespace) -> int:
    # DEFAULT row always describes the implicit no-AIDOG_PROFILE profile, so it
    # is derived from the repo paths directly (env must not skew the listing).
    state_default = REPO / "data" / "mycore_service"
    rows: list[str] = []
    rows.append(f"{_DEFAULT_PROFILE_LABEL:<12} {state_default.relative_to(REPO)} "
                f"secrets={'yes' if (REPO / 'secrets.env').is_file() else 'no'} "
                f"settings={'yes' if (REPO / 'web' / 'settings.json').is_file() else 'no'} "
                "web_port=8790")
    for name, root in _profile_dirs():
        port = _profile_web_port(root)
        rows.append(f"{name:<12} profiles/{name}/state "
                    f"secrets={'yes' if (root / 'secrets.env').is_file() else 'no'} "
                    f"settings={'yes' if (root / 'settings.json').is_file() else 'no'} "
                    f"web_port={port if port is not None else '?'}")
    print(f"{'profile':<12} state-dir  secrets settings web_port")
    for row in rows:
        print(row)
    print(f"{len(rows)} profile(s) · active: {os.environ.get('AIDOG_PROFILE') or _DEFAULT_PROFILE_LABEL}")
    return 0


def cmd_profile_create(args: argparse.Namespace) -> int:
    name = args.name
    if not _PROFILE_NAME_RE.match(name):
        print(f"invalid profile name {name!r}: expected [a-z0-9][a-z0-9_-]{{0,31}}")
        return 1
    if name == _DEFAULT_PROFILE_LABEL.lower() or name == "default":
        print(f"{name!r} is reserved for the default profile — pick another name")
        return 1
    pp = _profile_paths()
    root = pp.profiles_root(REPO) / name
    if root.exists():
        print(f"profile already exists: {root} (refusing to overwrite)")
        return 1

    # Scaffold first, seed second — a crash mid-seed leaves an obvious partial
    # dir (profile.json is written LAST, so list/status never see it half-made).
    for sub in ("state", "body/constitution", "body/memory", "body/skills",
                "body/evolution", "images"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    notes: list[str] = []
    # product config: byte-copy of the repo's own web/settings.json (defaults
    # when absent — the console falls back to built-in defaults).
    if _copy_atomic(REPO / "web" / "settings.json", root / "settings.json"):
        notes.append("settings.json  <- web/settings.json (seed copy)")
    else:
        notes.append("settings.json  <- skipped (no web/settings.json yet; console defaults apply)")
    # credential slots: EMPTY TEMPLATE ONLY — the repo's real secrets.env is
    # never read here (credential discipline: a new dog starts unfed).
    (root / "secrets.env").write_text(
        f"# I'm AiDog profile '{name}' credential slots — fill these, never commit.\n"
        "WEB_TOKEN=\n"
        "MYCORE_TG_TOKEN=\n"
        "AIDOG_TG_ALLOWED_USERS=\n",
        encoding="utf-8")
    notes.append("secrets.env     <- empty template (own bot token / WEB_TOKEN / allowlist)")
    # body layer: shipped templates seeded as THIS profile's own copies (Q3):
    # afterwards they drift independently of overlay/.
    for src, dst, label in (
        (REPO / "overlay" / "constitution" / "SOUL.md",
         root / "body" / "constitution" / "SOUL.md", "SOUL.md"),
        (REPO / "overlay" / "memory" / "MEMORY_curated.md",
         root / "body" / "memory" / "MEMORY_curated.md", "MEMORY_curated.md"),
        (REPO / "overlay" / "memory" / "USER_curated.md",
         root / "body" / "memory" / "USER_curated.md", "USER_curated.md"),
    ):
        if _copy_atomic(src, dst):
            notes.append(f"body           <- {label} (seed copy)")
        else:
            notes.append(f"body           <- {label} missing in overlay (skipped)")

    used = {p for _n, p in _profile_dirs()}
    port = 8791
    while any(_profile_web_port(p) == port for p in used):
        port += 1
    meta = {"name": name, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "web_port": port}
    (root / "profile.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    print(f"profile created: {root}")
    for n in notes:
        print(f"  {n}")
    print(f"  profile.json  <- name/created/web_port={port}")
    print("next steps (values are never echoed by this tool):")
    print(f"  1. edit {root}/secrets.env  — own MYCORE_TG_TOKEN (a second bot) + WEB_TOKEN + allowlist")
    print(f"  2. bash deploy/profile_ctl.sh start {name}")
    print(f"  3. console: http://127.0.0.1:{port}/  (Bearer <that profile's WEB_TOKEN>)")
    print("note: never reuse the default profile's bot token — two pollers on one token fight over offset.")
    return 0


def cmd_profile_rm(args: argparse.Namespace) -> int:
    name = args.name
    if not _PROFILE_NAME_RE.match(name):
        print(f"invalid profile name {name!r}: expected [a-z0-9][a-z0-9_-]{{0,31}}")
        return 1
    if name in (_DEFAULT_PROFILE_LABEL.lower(), "default"):
        print("the default profile has no directory and can never be removed")
        return 1
    root = _profile_paths().profiles_root(REPO) / name
    if not root.is_dir():
        print(f"profile not found: {root}")
        return 1
    # state guard: any file under state/ = live history (sessions/pidfile/log).
    state = root / "state"
    leftovers = [p for p in state.rglob("*") if p.is_file()] if state.is_dir() else []
    if leftovers and not args.force:
        print(f"profile {name!r} has {len(leftovers)} state file(s) — refusing to delete")
        print("  (stop it first: bash deploy/profile_ctl.sh stop " + name
              + "; then re-run with --force to discard its state)")
        return 1
    if not args.dry_run:
        print(f"removing: {root}")
        shutil.rmtree(root)
    else:
        print(f"would remove: {root}")
    print(f"profile removed: {name}")
    return 0


# ---------------------------------------------------------------------------

def cmd_setup(args: argparse.Namespace) -> int:
    """B3: delegate to deploy/aidog_setup.py (interactive wizard; --check/--yes
    /--no-boot flags). `aidog setup` with no flags = wizard when stdin is a
    TTY, otherwise the idempotent installer (scripted runs keep the old
    provision + boot + verify behavior)."""
    import subprocess
    setup_py = Path(__file__).resolve().parent / "aidog_setup.py"
    flags: list[str] = []
    if getattr(args, "check", False):
        flags.append("--check")
    if getattr(args, "yes", False):
        flags.append("--yes")
    if getattr(args, "no_boot", False):
        flags.append("--no-boot")
    if not flags and not sys.stdin.isatty():
        flags.append("--yes")  # scripted: never block on prompts
    return subprocess.call([sys.executable, str(setup_py), *flags])


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="aidog",
        description="I'm AiDog CLI: setup (one-command install/verify) plus "
                    "memory portability — export / import / list manifest-based "
                    "memory bundles (private memory repo transport).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="interactive setup wizard (fill credential slots, validate, boot); --check status only, --yes non-interactive installer")
    s.add_argument("--check", action="store_true",
                   help="report slot status only (exit 0 ready / 2 pending / 1 error)")
    s.add_argument("--yes", action="store_true",
                   help="non-interactive: run deploy/install.sh (provision + boot + verify)")
    s.add_argument("--no-boot", action="store_true",
                   help="skip the boot/verify step (wizard: no final question; --yes: provision + report only)")
    s.set_defaults(func=cmd_setup)

    e = sub.add_parser("export", help="bundle memory (+persona) into a manifest tarball")
    e.add_argument("--src", help="memory source dir (highest precedence)")
    e.add_argument("--persona", help="persona references dir")
    e.add_argument("--out", help=f"output dir (default: {DEFAULT_OUT})")
    e.add_argument("--tag", help="name tag appended to the tarball filename")
    e.set_defaults(func=cmd_export)

    i = sub.add_parser("import", help="verify + restore a memory bundle")
    i.add_argument("tarball", help="path to aidog_memory_*.tar.gz")
    i.add_argument("--dest-profile",
                   help="explicit memories parent dir (default: portable "
                        "memory-home chain — env AIDOG_MEMORY_HOME > local "
                        "MEMORY_HOME > ~/.aidog/memories > legacy shim profile)")
    i.add_argument("--source-home", help="self-contained source home (default: ~/.aidog/memory)")
    i.add_argument("--persona-home",
                   help="persona home (default: ~/.aidog/persona, legacy "
                        "~/.legacy/persona while un-migrated)")
    i.set_defaults(func=cmd_import)

    l = sub.add_parser("list", help="list bundles and verify sidecar + manifest hashes")
    l.add_argument("--dir", help=f"bundle dir (default: {DEFAULT_OUT})")
    l.set_defaults(func=cmd_list)

    pr = sub.add_parser("profile", help="car F §13.1 multi-profile runtime dirs (list / create / rm)")
    psub = pr.add_subparsers(dest="profile_cmd", required=True)

    pl = psub.add_parser("list", help="list the default profile + every profiles/<name> dir")
    pl.set_defaults(func=cmd_profile_list)

    pc = psub.add_parser("create", help="scaffold profiles/<name> (seeded settings + empty secret slots + body templates)")
    pc.add_argument("name", help="profile name: [a-z0-9][a-z0-9_-]{0,31}")
    pc.set_defaults(func=cmd_profile_create)

    prr = psub.add_parser("rm", help="delete a non-default profile dir (refuses when state files exist)")
    prr.add_argument("name", help="profile name: [a-z0-9][a-z0-9_-]{0,31}")
    prr.add_argument("--force", action="store_true",
                     help="discard state files too (without it, any file under state/ blocks the delete)")
    prr.add_argument("--dry-run", action="store_true", help="print the path that would be removed")
    prr.set_defaults(func=cmd_profile_rm)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
