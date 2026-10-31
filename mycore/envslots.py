"""mycore.envslots — credential-slot fallback readers shared by the runtime
legs (chat service / scheduler delivery / watchdog alerts).

Contract (frontend coverage car 1.6, `MYCORE_WD_CHAT` no-op fix):
  * process env always wins when it is non-empty (an operator override is
    never silently ignored by a file value);
  * an empty/unset env falls back to the own-line slot in the profile (or
    repo) `secrets.env` — the same file the console writes;
  * both empty -> an honest empty string. Callers keep their own fail-closed
    validation (e.g. the chat id must be a plain integer), so an empty or
    garbage result degrades to log-only, never to a wrong send.

Values read here are for immediate use only: never logged, never embedded in
an error, repr, status payload or chat message. English source per repo i18n
policy.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
from lib import profile_paths as pp  # noqa: E402  (profile path seam)

# The one slot this module resolves today; callers keep their own regex over
# the returned text (the value itself is never echoed anywhere).
WD_CHAT_SLOT = "MYCORE_WD_CHAT"


def secrets_file(path: Path | str | None = None) -> Path:
    """secrets.env of the active profile (repo fallback). `path` is a test
    seam: production callers never pass it."""
    return Path(path) if path is not None else pp.secrets_path(REPO)


def slot_value(name: str, path: Path | str | None = None) -> str:
    """Own-line read of one slot (glue-pit safe: `^NAME=<non-space...>`).
    Absent/unreadable file or absent slot -> "" (honest empty)."""
    try:
        text = secrets_file(path).read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(rf"(?m)^{re.escape(name)}=(\S[^\r\n]*)$", text)
    return m.group(1).strip() if m else ""


def wd_chat_target(path: Path | str | None = None) -> str:
    """Fallback delivery chat id: env MYCORE_WD_CHAT when non-empty, else the
    secrets.env slot of the same name, else "" (log-only by design).

    Env wins unconditionally when set — even if the operator's value is
    garbage, the caller's validation refuses it instead of quietly falling
    back to a file value the operator meant to override."""
    env = os.environ.get(WD_CHAT_SLOT, "").strip()
    if env:
        return env
    return slot_value(WD_CHAT_SLOT, path)
