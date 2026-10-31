#!/usr/bin/env python3
"""lib.i18n — locale pack discovery + lookup (stdlib only, zero dependencies).

Single source of truth for UI strings: `overlay/locales/<lang>.json`
(filename stem = language code). Adding a language = dropping one JSON file
next to the others — no code changes anywhere.

Design (repo i18n policy, AGENTS.md):
  - keys are English and dot-separated (`cmd.help.body`, `app.title`);
    only VALUES are localized;
  - JSON, not YAML: the product is stdlib-only (no PyYAML);
  - lookup falls back per key to the default pack (`en`) and then to the
    caller-supplied fallback, and records every fallback in `missing()` so
    drift is diagnosable instead of silent;
  - `validate()` reports key-set parity across packs (used by
    tests/source_lang_check.py and by `python3 lib/i18n.py --validate`).

Usage:
    python3 lib/i18n.py --validate     # parity report; exit 1 on drift

Language resolution order (callers): explicit user setting > default `en`.
English output per repo i18n policy.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOCALES_DIR = REPO / "overlay" / "locales"
DEFAULT_LANG = "en"

# File stem = language code; keep the glob safe and predictable.
LANG_CODE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,15}$")

_packs: dict[str, dict[str, str]] | None = None
_errors: list[str] = []       # load-time problems (bad file / bad shape)
_missing: list[str] = []      # "lang:key" fallback events (drift diagnostics)


def load(force: bool = False) -> dict[str, dict[str, str]]:
    """Discover `overlay/locales/*.json` (cached). Returns {lang: {key: str}}."""
    global _packs, _errors
    if _packs is not None and not force:
        return _packs
    packs: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    if not LOCALES_DIR.is_dir():
        errors.append(f"missing locale dir: {LOCALES_DIR}")
    else:
        for path in sorted(LOCALES_DIR.glob("*.json")):
            code = path.stem
            if not LANG_CODE_RE.match(code):
                errors.append(f"{path.name}: filename is not a valid language code")
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                errors.append(f"{path.name}: unreadable ({type(e).__name__})")
                continue
            if not isinstance(doc, dict):
                errors.append(f"{path.name}: top level must be a JSON object")
                continue
            clean: dict[str, str] = {}
            for k, v in doc.items():
                if isinstance(v, str):
                    clean[str(k)] = v
                else:
                    errors.append(f"{path.name}: non-string value at key {k!r}")
            packs[code] = clean
    _packs, _errors = packs, errors
    return packs


def reset_diagnostics() -> None:
    """Clear recorded fallbacks (tests / repeated validations)."""
    _missing.clear()


def errors() -> list[str]:
    """Load-time problems — packs skipped or values rejected (honest detail)."""
    load()
    return list(_errors)


def missing() -> list[str]:
    """`lang:key` entries where a lookup had to fall back (never silent)."""
    return list(_missing)


def languages() -> list[str]:
    """Discovered language codes, sorted (default first when present)."""
    codes = sorted(load())
    if DEFAULT_LANG in codes:
        codes.remove(DEFAULT_LANG)
        codes.insert(0, DEFAULT_LANG)
    return codes


def has_lang(code: str) -> bool:
    return isinstance(code, str) and code in load()


def pack(code: str) -> dict[str, str] | None:
    """Shallow copy of one pack (None when absent)."""
    p = load().get(code)
    return dict(p) if p is not None else None


def get(lang: str, key: str, fallback: str | None = None) -> str:
    """Value for `key` in `lang`, then in `en`, then `fallback`, else `key`.

    Every taken fallback is appended to `missing()` as `lang:key` (or
    `en:key`) so a broken/short pack shows up in validate() and in tests.
    """
    packs = load()
    target = packs.get(lang) if isinstance(lang, str) else None
    if target is not None and key in target:
        return target[key]
    if lang != DEFAULT_LANG:
        _missing.append(f"{lang}:{key}")
    base = packs.get(DEFAULT_LANG)
    if base is not None and key in base:
        return base[key]
    _missing.append(f"{DEFAULT_LANG}:{key}")
    if fallback is not None:
        return fallback
    return key


def fmt(lang_code: str, key: str, fallback: str | None = None, **kw) -> str:
    """get() + str.format, fail-soft: a malformed translation falls back to
    the English template, and a still-broken one returns the raw string.

    The language argument is named `lang_code` (not `lang`) on purpose: `kw`
    may legitimately carry a `{lang}` placeholder for a template.
    """
    raw = get(lang_code, key, fallback)
    if not kw:
        return raw
    try:
        return raw.format(**kw)
    except (KeyError, IndexError, ValueError):
        en = get(DEFAULT_LANG, key, fallback)
        try:
            return en.format(**kw)
        except (KeyError, IndexError, ValueError):
            return raw


def validate() -> dict:
    """Key-set parity report across packs (reference = default pack)."""
    packs = load()
    ref = packs.get(DEFAULT_LANG)
    report: dict = {
        "default": DEFAULT_LANG,
        "languages": sorted(packs),
        "counts": {c: len(p) for c, p in sorted(packs.items())},
        "errors": list(_errors),
        "missing_pack": [] if ref is not None else [DEFAULT_LANG],
        "missing_keys": {},    # lang -> keys absent vs the default pack
        "extra_keys": {},      # lang -> keys the default pack lacks
        "ok": True,
    }
    if ref is None:
        report["ok"] = False
        return report
    for code, p in sorted(packs.items()):
        if code == DEFAULT_LANG:
            continue
        absent = sorted(set(ref) - set(p))
        extra = sorted(set(p) - set(ref))
        if absent:
            report["missing_keys"][code] = absent
        if extra:
            report["extra_keys"][code] = extra
    report["ok"] = not (report["errors"] or report["missing_keys"]
                        or report["extra_keys"])
    return report


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--validate" not in argv:
        print("usage: python3 lib/i18n.py --validate")
        return 2
    rep = validate()
    print(f"LOCALE PACKS — {'OK' if rep['ok'] else 'DRIFT'} "
          f"({', '.join(f'{c}={n}' for c, n in rep['counts'].items()) or 'none'})")
    for e in rep["errors"]:
        print(f"  ERROR    {e}")
    for code in rep["missing_pack"]:
        print(f"  MISSING  default pack {code}.json")
    for code, keys in rep["missing_keys"].items():
        print(f"  MISSING  {code}: {len(keys)} key(s): {', '.join(keys[:8])}")
    for code, keys in rep["extra_keys"].items():
        print(f"  EXTRA    {code}: {len(keys)} key(s): {', '.join(keys[:8])}")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
