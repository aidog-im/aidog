"""lib.profile_paths — one machine, many dogs: profile path resolution (§13.1, car F).

A profile is a named runtime directory (design §13.1: config/.env/SOUL/
memories/sessions fully isolated, non-SaaS one-machine multi-profile):

    <profiles_root>/<name>/
        settings.json   per-profile product config (same schema as web/settings.json)
        secrets.env     per-profile credential slots (own bot token / WEB_TOKEN / ...)
        state/          per-profile state root (sessions, cron, evolution, checkpoints, log)
        body/           per-profile body layer: constitution/SOUL.md, memory/,
                        skills/ (learned), evolution/
        images/         per-profile generated media
        profile.json    profile metadata (name / web_port / created)

Shared read-only across profiles: overlay/locales, shipped overlay/skills,
the initial SOUL/memory templates that `aidog profile create` seeds from.

Env seams (read on EVERY call — never cached, so tests may flip them):
    AIDOG_PROFILE        profile name; empty/unset = DEFAULT profile, which
                         resolves to the exact pre-car-F paths (web/settings.json,
                         secrets.env, data/mycore_service, overlay/*) — byte-
                         identical legacy behavior, zero migration.
    AIDOG_PROFILES_DIR   profiles root (default <repo>/profiles)
    AIDOG_MC_STATE       explicit state-root override; wins over everything
                         (existing contract — mycore/tests/run.py pins it)

Fail-closed rule: an INVALID profile name raises ProfileError. A typo must
never silently resolve to the default profile's brain — that cross-profile
clobber is exactly what §13.1 forbids (the prior art's documented bug class).

English comments per repo i18n policy.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# Repo root = the parent of lib/ (this file lives at <repo>/lib/profile_paths.py).
REPO = Path(__file__).resolve().parent.parent

PROFILE_ENV = "AIDOG_PROFILE"
PROFILES_DIR_ENV = "AIDOG_PROFILES_DIR"
STATE_ENV = "AIDOG_MC_STATE"      # mirrors mycore.service.STATE_ENV

# Lowercase slug only: safe for tmux session suffixes (`aidog_svc_<name>`),
# shell interpolation and path joins; no separators, no dots, no uppercase.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class ProfileError(ValueError):
    """Invalid AIDOG_PROFILE value — fail-closed, never a silent fallback."""


def profile_name() -> str:
    """Active profile name; "" = default profile. Raises ProfileError when
    AIDOG_PROFILE is set but invalid (fail-closed per module doc)."""
    raw = os.environ.get(PROFILE_ENV, "").strip()
    if not raw:
        return ""
    if not _NAME_RE.match(raw):
        raise ProfileError(
            f"invalid {PROFILE_ENV}={raw!r} — expected [a-z0-9][a-z0-9_-]{{0,31}} "
            "(refusing to resolve: a bad name must never fall through to "
            "another profile's runtime)")
    return raw


def profiles_root(repo: Path = REPO) -> Path:
    """Where profile directories live: env AIDOG_PROFILES_DIR > <repo>/profiles."""
    raw = os.environ.get(PROFILES_DIR_ENV, "").strip()
    return Path(raw) if raw else Path(repo) / "profiles"


def profile_root(repo: Path = REPO) -> Path | None:
    """The active profile's directory, or None for the default profile."""
    name = profile_name()
    return profiles_root(repo) / name if name else None


def settings_path(repo: Path = REPO) -> Path:
    """Product config: <profile>/settings.json, else <repo>/web/settings.json."""
    root = profile_root(repo)
    return (root / "settings.json") if root else Path(repo) / "web" / "settings.json"


def secrets_path(repo: Path = REPO) -> Path:
    """Credential slots: <profile>/secrets.env, else <repo>/secrets.env."""
    root = profile_root(repo)
    return (root / "secrets.env") if root else Path(repo) / "secrets.env"


def state_dir(repo: Path = REPO) -> Path:
    """State root: env AIDOG_MC_STATE > <profile>/state > <repo>/data/mycore_service."""
    raw = os.environ.get(STATE_ENV, "").strip()
    if raw:
        return Path(raw)
    root = profile_root(repo)
    return (root / "state") if root else Path(repo) / "data" / "mycore_service"


def body_root(repo: Path = REPO) -> Path:
    """Body layer root: <profile>/body, else <repo>/overlay (locales, shipped
    skills, hooks and skin stay in the shared overlay — code layer, not profile)."""
    root = profile_root(repo)
    return (root / "body") if root else Path(repo) / "overlay"


def constitution_path(repo: Path = REPO) -> Path:
    """Per-profile SOUL: <body>/constitution/SOUL.md (default = overlay copy)."""
    return body_root(repo) / "constitution" / "SOUL.md"


def memory_slots(repo: Path = REPO) -> tuple[Path, Path]:
    """(MEMORY_curated, USER_curated) under the active body root."""
    mem = body_root(repo) / "memory"
    return (mem / "MEMORY_curated.md", mem / "USER_curated.md")


def learned_path(repo: Path = REPO) -> Path:
    """EVOLUTION_learned.md under the active body root (per profile, car F)."""
    return body_root(repo) / "memory" / "EVOLUTION_learned.md"


def skills_dir(repo: Path = REPO) -> Path:
    """LEARNED skills root (<body>/skills). Shipped skills stay shared in
    <repo>/overlay/skills — skill_route roots include both."""
    return body_root(repo) / "skills"


def evolution_dir(repo: Path = REPO) -> Path:
    """Evolution log/reflect root (<body>/evolution; default = overlay copy)."""
    return body_root(repo) / "evolution"


def media_dir(repo: Path = REPO) -> Path:
    """Generated media: <profile>/images, else <repo>/data/images."""
    root = profile_root(repo)
    return (root / "images") if root else Path(repo) / "data" / "images"
