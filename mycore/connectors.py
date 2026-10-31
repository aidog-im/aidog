"""mycore.connectors — read-only BYOK connectors (car 4c): Linear issues +
Notion pages behind one `search()` entry (official-SDK-verified protocols).

Linear  (SDK source proven): POST https://api.linear.app/graphql with
`Authorization: <key>` RAW (no "Bearer" — that form is for OAuth tokens) and
a GraphQL issueSearch(query, first) projection over the always-present
fields identifier/title/url.

Notion  (JS SDK proven): POST https://api.notion.com/v1/search with
`Authorization: Bearer <key>` + `Notion-Version: 2025-09-03` (the SDK's
current default) and body {"query", "page_size"}; a result's title is the
concatenation of every properties entry with type "title", falling back to
object + url.

Endpoint override seams for the offline E2E mock: env AIDOG_LINEAR_URL /
AIDOG_NOTION_URL. Transport is injectable with mycore.model's callable
style (url, headers, payload_bytes, timeout) -> response text, so tests run
with zero network. No key -> ConnectorError naming the slot; upstream error
text is truncated to 160 chars. Secrets never surface. English comments
per repo i18n policy.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Callable

ERR_SNIPPET = 160          # upstream error text cap (honest, bounded)

# transport: (url, headers, payload_bytes, timeout) -> response text
Transport = Callable[[str, dict, bytes, float], str]


class ConnectorError(RuntimeError):
    """Honest connector failure; never contains a credential."""


def _slot(name: str) -> str:
    """Own-line slot read (channels._slot style): process env first, then
    <repo>/secrets.env. Value for immediate use only — never surfaced."""
    val = os.environ.get(name, "").strip()
    if val:
        return val
    try:
        from pathlib import Path
        repo = Path(__file__).resolve().parent.parent
        text = (repo / "secrets.env").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(rf"(?m)^{re.escape(name)}=(\S[^\r\n]*)$", text)
    return m.group(1).strip() if m else ""


def status() -> dict:
    """Slot PRESENCE only — {"linear": bool, "notion": bool}; values never
    leave this function."""
    return {"linear": bool(_slot("LINEAR_API_KEY")),
            "notion": bool(_slot("NOTION_API_KEY"))}


# -------------------------------------------------------------- transport ---

def _urllib_transport(url: str, headers: dict, payload: bytes,
                      timeout: float) -> str:
    req = urllib.request.Request(url, data=payload, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        snippet = ""
        try:
            snippet = e.read().decode("utf-8", "replace").strip()[:ERR_SNIPPET]
        except OSError:
            pass
        raise ConnectorError(f"HTTP {e.code} {e.reason}: {snippet}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ConnectorError(f"transport failure: {type(e).__name__}") from None


def _doc(raw: str) -> dict:
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise ConnectorError("connector returned invalid JSON") from None
    if not isinstance(parsed, dict):
        raise ConnectorError("connector returned unexpected JSON")
    return parsed


def _upstream_error(doc: dict) -> str:
    """One bounded line from a provider error payload (≤160 chars)."""
    for key in ("message", "error", "errors"):
        val = doc.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:ERR_SNIPPET]
        if isinstance(val, dict):
            msg = val.get("message")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()[:ERR_SNIPPET]
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, dict):
                msg = first.get("message") or first.get("type")
                if isinstance(msg, str) and msg.strip():
                    return msg.strip()[:ERR_SNIPPET]
            if isinstance(first, str) and first.strip():
                return first.strip()[:ERR_SNIPPET]
    return "unknown upstream error"


# --------------------------------------------------------------- sources ----

_LINEAR_QUERY = """
query IssueSearch($q: String!, $n: Int!) {
  issueSearch(query: $q, first: $n) {
    nodes { identifier title url }
  }
}
""".strip()


def _search_linear(query: str, limit: int, timeout: float,
                   transport: Transport) -> str:
    key = _slot("LINEAR_API_KEY")
    if not key:
        raise ConnectorError("linear not configured (set LINEAR_API_KEY in secrets.env)")
    url = os.environ.get("AIDOG_LINEAR_URL", "").strip() or "https://api.linear.app/graphql"
    headers = {"Content-Type": "application/json", "Authorization": key}
    payload = json.dumps({"query": _LINEAR_QUERY,
                          "variables": {"q": query, "n": limit}}).encode("utf-8")
    doc = _doc(transport(url, headers, payload, timeout))
    if doc.get("errors"):
        raise ConnectorError(f"linear: {_upstream_error(doc)}")
    nodes = ((doc.get("data") or {}).get("issueSearch") or {}).get("nodes")
    if not isinstance(nodes, list):
        raise ConnectorError("linear: unexpected response shape")
    lines = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        ident = str(node.get("identifier") or "").strip()
        title = str(node.get("title") or "").strip()
        url_ = str(node.get("url") or "").strip()
        lines.append(" · ".join(x for x in (ident, title, url_) if x))
    return "\n".join(lines)


def _notion_title(item: dict) -> str:
    props = item.get("properties")
    if isinstance(props, dict):
        for val in props.values():
            if isinstance(val, dict) and val.get("type") == "title":
                arr = val.get("title")
                if isinstance(arr, list):
                    text = "".join(str(seg.get("plain_text") or "")
                                   for seg in arr if isinstance(seg, dict))
                    if text.strip():
                        return text.strip()
    return ""


def _search_notion(query: str, limit: int, timeout: float,
                   transport: Transport) -> str:
    key = _slot("NOTION_API_KEY")
    if not key:
        raise ConnectorError("notion not configured (set NOTION_API_KEY in secrets.env)")
    url = os.environ.get("AIDOG_NOTION_URL", "").strip() or "https://api.notion.com/v1/search"
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {key}",
               "Notion-Version": "2025-09-03"}
    payload = json.dumps({"query": query, "page_size": limit}).encode("utf-8")
    doc = _doc(transport(url, headers, payload, timeout))
    if doc.get("code") and doc.get("message"):
        raise ConnectorError(f"notion: {_upstream_error(doc)}")
    results = doc.get("results")
    if not isinstance(results, list):
        raise ConnectorError("notion: unexpected response shape")
    lines = []
    for item in results:
        if not isinstance(item, dict):
            continue
        title = _notion_title(item)
        obj = str(item.get("object") or "object").strip()
        url_ = str(item.get("url") or "").strip()
        lines.append(" · ".join(x for x in (title or obj, url_) if x))
    return "\n".join(lines)


# ----------------------------------------------------------------- search ---

SOURCES = ("auto", "linear", "notion")


def search(query: str, *, source: str = "auto", limit: int = 5,
           timeout: float = 20, transport: Transport | None = None) -> str:
    """Read-only search across the configured connector(s). `auto` queries
    every slot that exists (sections labelled per source); an explicit
    `source` fails honestly when its slot is missing. Empty result set is an
    honest empty string with a header line, never fabricated content."""
    if not isinstance(query, str) or not query.strip():
        raise ConnectorError("query must be a non-empty string")
    if source not in SOURCES:
        raise ConnectorError(f"source must be one of {'|'.join(SOURCES)}")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ConnectorError("limit must be an integer 1..20")
    transport = transport or _urllib_transport
    q = query.strip()
    st = status()

    if source != "auto":
        fn = _search_linear if source == "linear" else _search_notion
        out = fn(q, limit, timeout, transport)
        return out if out else f"[{source}] no results for '{q}'"

    wanted = [name for name in ("linear", "notion") if st.get(name)]
    if not wanted:
        raise ConnectorError(
            "connectors not configured (set LINEAR_API_KEY / NOTION_API_KEY in secrets.env)")
    sections = []
    for name in wanted:
        fn = _search_linear if name == "linear" else _search_notion
        body = fn(q, limit, timeout, transport)
        sections.append(f"[{name}] {body if body else f'no results for {q!r}'}")
    return "\n".join(sections)
