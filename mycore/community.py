"""mycore.community — GitHub Discussions channel shared by the console POST
routes and the `community` agent tool (in-app community car, 0.5.54).

Owner decisions encoded here (2026-10-27):
  * ONE credential slot: ``AIDOG_COMMUNITY_PAT`` — deliberately separate from
    ``AIDOG_GH_PAT`` (that one exists only so `aidog update` can read the
    release repository with a higher anonymous rate limit; a public repo
    reads fine without it, so ordinary users never need it). The community
    slot is optional too, but WITHOUT it the console can only READ the public
    discussion list — posting, replying and category loading all answer an
    honest ``no_token`` the UI renders as a setup hint, never as a failure.
  * The agent posts DIRECTLY (owner choice: no prepare/approve gate for
    community posts), bounded by a shared 30s write interval and hard length
    caps enforced in this module, so the console and the agent cannot race
    past the same limiter (the interval is persisted in the state dir so it
    spans both processes).
  * ZERO CREDENTIAL ECHO: the token is read for one request header and
    dropped. No return value, error, log line or repr ever contains it —
    errors are category WORDS plus a short upstream-safe detail.

Endpoint seams for tests: ``AIDOG_GH_API_URL`` (default https://api.github.com,
GraphQL served at ``<base>/graphql``) and ``AIDOG_COMMUNITY_REPO``
(default ``AiDog-im/AiDog``). Transport is a module-level callable so the
core suite runs with zero network.

English source per repo i18n policy.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from lib import profile_paths as pp  # noqa: E402  (profile path seam)

REPO = Path(__file__).resolve().parent.parent

SLOT = "AIDOG_COMMUNITY_PAT"
DEFAULT_API = "https://api.github.com"
DEFAULT_REPO = "AiDog-im/AiDog"

TITLE_MAX = 120
BODY_MAX = 2000
WRITE_MIN_INTERVAL = 30.0       # seconds between any two writes, both processes
TIMEOUT = 8.0                   # one outbound attempt, hard ceiling
MAX_BYTES = 200 * 1024
CATEGORY_TTL = 600.0            # categories cache window (seconds)
REPO_NODE_TTL = 24 * 3600.0     # repository node id cache window (seconds)

# every failure surfaces as one of these words (the UI maps them through
# comm.reason.* / verify.* packs; anything else would leak upstream detail)
REASONS = ("no_token", "no_scope", "rate_limited", "not_found",
           "bad_input", "busy", "upstream_error", "network")


class CommunityError(Exception):
    """Honest, word-classified community failure. `detail` is a short
    upstream-safe sentence (status code / GraphQL message) — never a token,
    never a URL query, never a traceback."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason if reason in REASONS else "upstream_error"
        self.detail = detail[:240]


# ------------------------------------------------------------------ config --

def api_base() -> str:
    return (os.environ.get("AIDOG_GH_API_URL") or "").strip().rstrip("/") \
        or DEFAULT_API


def repo_slug() -> str:
    raw = (os.environ.get("AIDOG_COMMUNITY_REPO") or "").strip()
    slug = raw or DEFAULT_REPO
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", slug):
        return DEFAULT_REPO
    owner, _, name = slug.partition("/")
    return f"{owner}/{name}"


def read_token() -> str:
    """The slot value (env wins over secrets.env, same precedence as every
    other slot reader). The string leaves this frame only as an
    Authorization header built by `_request`."""
    env = os.environ.get(SLOT, "").strip()
    if env:
        return env
    try:
        text = pp.secrets_path(REPO).read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(rf"(?m)^{re.escape(SLOT)}=(\S[^\r\n]*)$", text)
    return m.group(1).strip() if m else ""


def _state_file() -> Path:
    return pp.state_dir(REPO) / "community_last_write"


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------- transport --

def _urllib_transport(url: str, method: str, headers: dict,
                      payload: bytes | None, timeout: float) -> tuple[int, str]:
    """Default transport: (status, body-text). Never raises for HTTP errors —
    the caller classifies. Transport-level failures raise OSError/URLError."""
    req = urllib.request.Request(url, data=payload, method=method)
    req.add_header("User-Agent", "aidog-community")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(MAX_BYTES).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read(MAX_BYTES).decode("utf-8", "replace")
        except OSError:
            body = ""
        return e.code, body


_transport = _urllib_transport


def set_transport(fn) -> None:
    """Test seam: swap the transport (same callable contract as the default).
    Production callers never touch this."""
    global _transport
    _transport = fn


def _classify_status(status: int, body: str) -> str:
    if status in (401, 403):
        low = body.lower()
        if "rate limit" in low or "secondary rate" in low:
            return "rate_limited"
        # 401 "Bad credentials" and a 403 scope refusal both mean the same
        # thing to the operator: this token cannot do the job (fix the token
        # or grant it Discussions access) — one word, one remedy.
        return "no_scope"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"
    if 400 <= status < 500:
        return "bad_input"
    return "upstream_error"


def _gql_errors(body: str) -> str:
    """First GraphQL error message, or "" — GitHub messages are safe text
    (they never echo the presented token)."""
    try:
        doc = json.loads(body)
    except ValueError:
        return ""
    errs = doc.get("errors") if isinstance(doc, dict) else None
    if isinstance(errs, list) and errs and isinstance(errs[0], dict):
        return str(errs[0].get("message") or "")[:240]
    return ""


def _request(url: str, method: str = "GET", token: str = "",
             payload: dict | None = None) -> str:
    """One outbound call -> body text. Raises CommunityError with a category
    word on any failure. `token` is read here, used here, dropped here."""
    headers = {"Accept": "application/vnd.github+json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        status, body = _transport(url, method, headers, data, TIMEOUT)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise CommunityError("network", "could not reach GitHub") from None
    if status < 200 or status >= 300:
        reason = _classify_status(status, body)
        detail = _gql_errors(body) or f"HTTP {status}"
        raise CommunityError(reason, detail)
    return body


# ------------------------------------------------------------- rate limit ---

_write_lock = threading.Lock()


def _last_write() -> float:
    try:
        return float(_state_file().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0.0


def seconds_until_writable() -> float:
    """Seconds until the next write is allowed (0 = now). The timestamp lives
    in the state dir so the console and the chat service share one window."""
    with _write_lock:
        left = WRITE_MIN_INTERVAL - (_now() - _last_write())
    return max(0.0, left)


def _mark_written() -> None:
    path = _state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(_now()), encoding="utf-8")
    except OSError:
        # a read-only state dir degrades to the in-process lock only — the
        # limiter must never crash a post that GitHub would accept.
        pass


def _gate_write() -> None:
    with _write_lock:
        left = WRITE_MIN_INTERVAL - (_now() - _last_write())
        if left > 0:
            raise CommunityError("busy",
                                 f"wait {int(left) + 1}s before the next post")
        # reserve the window BEFORE the outbound call so a slow GitHub cannot
        # be raced by a second writer; a failed post still consumes it (the
        # cheap side of failing open on rate, the safe side for the owner).
        _mark_written()


# --------------------------------------------------------------- reads -------

def list_discussions(limit: int = 20) -> list[dict]:
    """Anonymous public-discussion list (no token ever rides this call).
    Returns GitHub's own REST items; callers map them."""
    limit = max(1, min(50, int(limit or 20)))
    url = (f"{api_base()}/repos/{repo_slug()}/discussions"
           f"?per_page={limit}&state=open&sort=updated&order=desc")
    body = _request(url)
    try:
        doc = json.loads(body)
    except ValueError:
        raise CommunityError("upstream_error", "invalid JSON") from None
    if not isinstance(doc, list):
        raise CommunityError("upstream_error", "unexpected payload")
    return doc


def replies(number: int) -> list[dict]:
    """Anonymous read of one discussion's comments (public repo)."""
    try:
        n = int(number)
    except (TypeError, ValueError):
        raise CommunityError("bad_input", "number must be an integer") from None
    if n <= 0:
        raise CommunityError("bad_input", "number must be positive")
    url = (f"{api_base()}/repos/{repo_slug()}/discussions/{n}"
           f"/comments?per_page=50")
    body = _request(url)
    try:
        doc = json.loads(body)
    except ValueError:
        raise CommunityError("upstream_error", "invalid JSON") from None
    if not isinstance(doc, list):
        raise CommunityError("upstream_error", "unexpected payload")
    return doc


_cat_cache: dict = {"ts": 0.0, "items": []}
_repo_node: dict = {"id": "", "ts": 0.0}
_cache_lock = threading.Lock()


def categories(force: bool = False) -> list[dict]:
    """[{id, name, slug}] via GraphQL — requires the slot (there is no REST
    route for categories). Uncached/absent token -> CommunityError(no_token),
    which the console renders as a setup hint."""
    token = read_token()
    if not token:
        raise CommunityError("no_token", "community token not configured")
    with _cache_lock:
        if (not force and _cat_cache["items"]
                and _now() - _cat_cache["ts"] < CATEGORY_TTL):
            return list(_cat_cache["items"])
    owner, _, name = repo_slug().partition("/")
    query = (f'query {{ repository(owner: "{owner}", name: "{name}") '
             f"{{ discussionCategories(first: 20) "
             f"{{ nodes {{ id name slug }} }} }} }}")
    body = _request(f"{api_base()}/graphql", "POST", token,
                    {"query": query, "variables": {}})
    detail = _gql_errors(body)
    if detail:
        raise CommunityError("no_scope", detail)
    try:
        doc = json.loads(body)
        nodes = doc["data"]["repository"]["discussionCategories"]["nodes"]
    except (ValueError, KeyError, TypeError):
        raise CommunityError("upstream_error", "unexpected payload") from None
    items = [{"id": str(c.get("id") or ""), "name": str(c.get("name") or ""),
              "slug": str(c.get("slug") or "")}
             for c in (nodes if isinstance(nodes, list) else [])
             if isinstance(c, dict) and c.get("id")]
    with _cache_lock:
        _cat_cache.update(ts=_now(), items=items)
    return items


def _repo_node_id(token: str) -> str:
    with _cache_lock:
        if _repo_node["id"] and _now() - _repo_node["ts"] < REPO_NODE_TTL:
            return _repo_node["id"]
    body = _request(f"{api_base()}/repos/{repo_slug()}", token=token)
    try:
        node = str(json.loads(body).get("node_id") or "")
    except (ValueError, AttributeError, TypeError):
        node = ""
    if not node:
        raise CommunityError("upstream_error", "repository node id missing")
    with _cache_lock:
        _repo_node.update(id=node, ts=_now())
    return node


def _resolve_category(token: str, ref: str) -> str:
    """Map a node id / slug / display name onto a GraphQL category node id."""
    ref = str(ref or "").strip()
    if not ref:
        raise CommunityError("bad_input", "category is required")
    items = categories()
    for c in items:
        if ref in (c["id"], c["slug"], c["name"]):
            return c["id"]
    items = categories(force=True)      # stale cache: one refresh, then fail
    for c in items:
        if ref in (c["id"], c["slug"], c["name"]):
            return c["id"]
    raise CommunityError("bad_input", "unknown category")


def _clean_title(value) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        raise CommunityError("bad_input", "title is required")
    if len(text) > TITLE_MAX:
        raise CommunityError("bad_input", f"title too long (max {TITLE_MAX})")
    return text


def _clean_body(value) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        raise CommunityError("bad_input", "body is required")
    if len(text) > BODY_MAX:
        raise CommunityError("bad_input", f"body too long (max {BODY_MAX})")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in text):
        raise CommunityError("bad_input", "body has control characters")
    return text


def _gql(token: str, query: str, variables: dict) -> dict:
    body = _request(f"{api_base()}/graphql", "POST", token,
                    {"query": query, "variables": variables})
    detail = _gql_errors(body)
    if detail:
        low = detail.lower()
        if any(w in low for w in ("scope", "permission", "access",
                                  "forbidden", "authorized")):
            raise CommunityError("no_scope", detail)
        raise CommunityError("bad_input", detail)
    try:
        doc = json.loads(body)
    except ValueError:
        raise CommunityError("upstream_error", "invalid JSON") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("data"), dict):
        raise CommunityError("upstream_error", "unexpected payload")
    return doc["data"]


# --------------------------------------------------------------- writes ------

def _note_publishing() -> None:
    """D2 ledger: one `publishing` scenario event per successful write.
    Lazy import + silent no-op — an inert or missing ledger never touches
    the community path."""
    try:
        from . import ledger as _ledger
        _ledger.note_event("publishing")
    except Exception:  # noqa: BLE001 — reporting never breaks a write
        pass


def create(title, body, category) -> dict:
    """Create one discussion -> {number, url}. Direct post (owner decision),
    bounded by the shared write interval and the length caps above."""
    token = read_token()
    if not token:
        raise CommunityError("no_token", "community token not configured")
    clean_title = _clean_title(title)
    clean_body = _clean_body(body)
    cat_id = _resolve_category(token, category)
    _gate_write()
    repo_id = _repo_node_id(token)
    data = _gql(
        token,
        "mutation($rid: ID!, $cid: ID!, $title: String!, $body: String!) {"
        " createDiscussion(input: {repositoryId: $rid, categoryId: $cid,"
        " title: $title, body: $body}) { discussion { number url } } }",
        {"rid": repo_id, "cid": cat_id, "title": clean_title,
         "body": clean_body})
    try:
        disc = data["createDiscussion"]["discussion"]
        _note_publishing()
        return {"number": int(disc["number"]),
                "url": str(disc.get("url") or "")}
    except (KeyError, TypeError, ValueError):
        raise CommunityError("upstream_error", "missing discussion id") from None


def comment(number, body) -> dict:
    """Reply to one discussion -> {number, comment_id}. Same gate as create."""
    token = read_token()
    if not token:
        raise CommunityError("no_token", "community token not configured")
    clean_body = _clean_body(body)
    try:
        n = int(number)
    except (TypeError, ValueError):
        raise CommunityError("bad_input", "number must be an integer") from None
    if n <= 0:
        raise CommunityError("bad_input", "number must be positive")
    owner, _, name = repo_slug().partition("/")
    # resolve the discussion node id first (addDiscussionComment takes the
    # node id, not the number — one extra read, no cached state to go stale)
    lookup = (f'query {{ repository(owner: "{owner}", name: "{name}") '
              f"{{ discussion(number: {n}) {{ id }} }} }}")
    data = _gql(token, lookup, {})
    try:
        disc_id = data["repository"]["discussion"]["id"]
    except (KeyError, TypeError):
        raise CommunityError("not_found", "discussion not found") from None
    if not disc_id:
        raise CommunityError("not_found", "discussion not found")
    _gate_write()
    data = _gql(
        token,
        "mutation($id: ID!, $body: String!) { addDiscussionComment(input:"
        " {discussionId: $id, body: $body}) { comment { id } } }",
        {"id": disc_id, "body": clean_body})
    try:
        cid = data["addDiscussionComment"]["comment"]["id"]
    except (KeyError, TypeError):
        raise CommunityError("upstream_error", "missing comment id") from None
    _note_publishing()
    return {"number": n, "comment_id": str(cid)}


# ----------------------------------------------------------- verification ----

def verify_token(token: str = "") -> dict:
    """Slot health check used by the console's credential VERIFY buttons:
    {ok, login?, scope?, reason?, detail?}. Proves the token resolves to a
    user AND can read this repository's discussions — exactly what posting
    needs. Values never leave this frame except the login (public data)."""
    tok = (token or "").strip() or read_token()
    if not tok:
        return {"ok": False, "reason": "no_token",
                "detail": "set the community GitHub token first"}
    try:
        body = _request(f"{api_base()}/user", token=tok)
        login = str(json.loads(body).get("login") or "")
    except CommunityError as e:
        return {"ok": False, "reason": e.reason, "detail": e.detail}
    except (ValueError, AttributeError, TypeError):
        return {"ok": False, "reason": "upstream_error",
                "detail": "unexpected /user payload"}
    if not login:
        return {"ok": False, "reason": "upstream_error",
                "detail": "no login in /user response"}
    owner, _, name = repo_slug().partition("/")
    query = (f'query {{ repository(owner: "{owner}", name: "{name}") '
             f"{{ discussionCategories(first: 1) {{ totalCount }} }} }}")
    try:
        body = _request(f"{api_base()}/graphql", "POST", tok,
                        {"query": query, "variables": {}})
    except CommunityError as e:
        return {"ok": False, "login": login, "reason": e.reason,
                "detail": e.detail or
                          "token cannot read this repository's discussions"}
    detail = _gql_errors(body)
    if detail:
        return {"ok": False, "login": login, "reason": "no_scope",
                "detail": detail}
    return {"ok": True, "login": login, "scope": "discussions:read"}


def reset_for_tests() -> None:
    """Drop every cache and the rate timestamp (test seam only)."""
    global _transport
    with _cache_lock:
        _cat_cache.update(ts=0.0, items=[])
        _repo_node.update(id="", ts=0.0)
    try:
        _state_file().unlink()
    except OSError:
        pass
    _transport = _urllib_transport
