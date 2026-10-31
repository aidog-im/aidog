"""mycore.exchanges — Web3 phase 2, W1 car: READ-ONLY exchange & chain data.

Three hard contracts (owner decrees, 2026-10-24) that this file enforces in
code rather than in configuration:

1. READ-ONLY UNTIL A HUMAN SAYS OTHERWISE (W1 -> W2). W1: every request
   here fetched data. W2 (owner decree 2026-10-25) adds ORDER PLACEMENT and
   nothing else, behind a prepare/execute split drawn in CODE:
   ``prepare_order`` builds a dry-run plan (book, VWAP, fees, thresholds)
   and ``execute_order`` takes the plan id and NOTHING ELSE — every order
   parameter is read back from the stored plan, so no prompt, settings value
   or env var can rewrite an order between approval and execution. Execution
   is fail-closed: status must be `approved` (the OWNER's console decision
   behind the Bearer WEB_TOKEN write gate, or a POSITIVE auto threshold
   re-checked against the day's ledger at execution time), default
   thresholds are 0 = nothing auto-approves, MARKET orders never
   auto-approve. WITHDRAWALS, ON-CHAIN TRANSFERS and TRANSACTION SIGNING DO
   NOT EXIST in this module — no settings value, env var or model
   instruction can turn this file into a fund mover, and the core suite
   asserts that absence against this very source file.
2. ZERO CREDENTIAL ECHO. Slot values are read only to sign a request and
   are never returned, logged, formatted or embedded in an error. Errors
   name the SLOT (``OKX_API_KEY``), never the value.
3. SLOT GATED. Public market data needs no slot. Account reads
   (portfolio / orders) require the venue's full slot set; a missing slot
   raises an honest ExchangeError naming exactly what to set, and
   ``tools.build_registry`` withholds those tools entirely (exchange_note).

Two venue families share one catalog (``VENUES``):
  * CEX (kind="cex") — OKX + Kraken. Market data is public; account reads
    are HMAC-signed with the venue's key and honour the read-only key
    permission the operator configured (Read / Query), never a trade right.
  * Chain (kind="chain") — six keyless, non-custodial market sources
    (Jupiter, Velora, Hyperliquid, dYdX Indexer, GMX, public JSON-RPC).
    There is no credential to gate, so these are built in with NO slot.

Endpoint override seams for the offline E2E mock: ``AIDOG_EX_<VENUE>_URL``.
Transport is injectable (callable style of mycore.connectors plus an
explicit method, because these APIs mix GET with nonce-bearing POST), so
the core suite runs with zero network and zero real keys.

English comments per repo i18n policy.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from lib import profile_paths as pp  # noqa: E402  (per-profile secrets)

ERR_SNIPPET = 160          # upstream error text cap (honest, bounded)
MAX_LINES = 40             # output cap (context budget discipline)
DEFAULT_TIMEOUT = 15.0

# Polite, TRUTHFUL identification: the stdlib default (Python-urllib/3.x)
# is refused by the exchanges' edge (Cloudflare 1010), so we send a real
# product agent string instead of pretending to be a browser — no browser
# spoofing, no credential in the header.
USER_AGENT = "aidog/0.5 (+https://aidog.im; read-only market data)"

# transport: (url, method, headers, payload_bytes|None, timeout) -> text
Transport = Callable[[str, str, dict, "bytes | None", float], str]

# Strict symbol grammar: pair/ticker/mint/address shapes only. A symbol is
# interpolated into a request path, so the alphabet is closed on purpose.
SYMBOL_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,64}$")


class ExchangeError(RuntimeError):
    """Honest exchange failure; never contains a credential."""


# ----------------------------------------------------------------- venues ---

@dataclass(frozen=True)
class Venue:
    key: str
    label: str
    kind: str       # "cex" | "chain"
    auth: str       # "key" | "public"
    slots: tuple[str, ...] = ()
    symbol: str = ""        # default symbol when the caller omits one
    summary: str = ""


VENUES: dict[str, Venue] = {v.key: v for v in (
    Venue("okx", "OKX", "cex", "key",
          ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE"),
          "BTC-USDT", "Spot REST v5, signed with a Read-only API key"),
    Venue("kraken", "Kraken", "cex", "key",
          ("KRAKEN_API_KEY", "KRAKEN_API_SECRET"),
          "XBT/USD", "Spot REST, HMAC-SHA512, Query-only key permission"),
    Venue("jupiter", "Jupiter", "chain", "public", (),
          "SOL", "Solana price API (keyless, 0.5 RPS)"),
    Venue("velora", "Velora", "chain", "public", (),
          "USDC/WETH", "DEX quote API (keyless, partner string identity)"),
    Venue("hyperliquid", "Hyperliquid", "chain", "public", (),
          "BTC", "Perp info endpoint (keyless, public book)"),
    Venue("dydx", "dYdX Indexer", "chain", "public", (),
          "BTC-USD", "v4 Indexer REST (keyless, 100 req/10s/IP)"),
    Venue("gmx", "GMX", "chain", "public", (),
          "BTC", "GMX pairs feed (keyless, no account involved)"),
    Venue("rpc", "Public RPC", "chain", "public", (),
          "base", "JSON-RPC chain head (base | ethereum | solana)"),
    # Third CEX (owner decree 2026-10-25): Binance has NO official MCP/agent
    # API (GitHub org search: 0 repos, verified 2026-10-24) — only the
    # official Skills Hub + REST. This is our OWN thin adapter over the
    # documented REST surface; no third-party server ever sees the key.
    Venue("binance", "Binance", "cex", "key",
          ("BINANCE_API_KEY", "BINANCE_API_SECRET"),
          "BTCUSDT", "Spot REST (official Skills + REST, no official MCP)"),
)}

CEX_VENUES = tuple(k for k, v in VENUES.items() if v.kind == "cex")
# Venues that can carry an ORDER (W2). Still read-only by construction:
# placing one goes through prepare -> human approval -> execute.
ORDERABLE_VENUES = CEX_VENUES

# Base URL per venue (env AIDOG_EX_<VENUE>_URL overrides — offline E2E seam).
_BASE = {
    "okx": "https://www.okx.com",
    "kraken": "https://api.kraken.com",
    "binance": "https://api.binance.com",
    "jupiter": "https://lite-api.jup.ag",
    "velora": "https://api.velora.xyz",
    "hyperliquid": "https://api.hyperliquid.xyz",
    "dydx": "https://indexer.dydx.trade",
    "gmx": "https://arbitrum.gmxapi.io",
    "rpc": "",   # chain-specific, see CHAINS
}


# Base URL per venue. Env override seam for the offline E2E mock:
# AIDOG_EX_<VENUE>_URL (preferred, documented) or the short AIDOG_EX_<VENUE>.
def _base(venue: str) -> str:
    env = (os.environ.get(f"AIDOG_EX_{venue.upper()}_URL", "").strip()
           or os.environ.get(f"AIDOG_EX_{venue.upper()}", "").strip())
    if env:
        return env.rstrip("/")
    return _BASE[venue].rstrip("/")


# ------------------------------------------------------------------ slots ---

def _slot(name: str) -> str:
    """Own-line slot read (same convention as connectors._slot): process env
    first, then <repo>/secrets.env. Value for immediate use only — it is
    never surfaced by any function in this module."""
    val = os.environ.get(name, "").strip()
    if val:
        return val
    try:
        from pathlib import Path
        repo = Path(__file__).resolve().parent.parent
        text = pp.secrets_path(repo).read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(rf"(?m)^{re.escape(name)}=(\S[^\r\n]*)$", text)
    return m.group(1).strip() if m else ""


def status() -> dict:
    """Slot PRESENCE only — {"okx": bool, "kraken": bool}; values never
    leave this function."""
    return {k: all(_slot(s) for s in VENUES[k].slots) for k in CEX_VENUES}


def missing_slots(venue: str) -> tuple[str, ...]:
    """The venue's slots that are currently absent (names only)."""
    return tuple(s for s in VENUES[venue].slots if not _slot(s))


def catalog() -> list[dict]:
    """Capability-directory rows for /api/capabilities and the console
    panel: names, kinds, presence booleans and an honest reason when a key
    venue is not configured. Never a slot VALUE, never a URL."""
    st = status()
    rows = []
    for key, v in VENUES.items():
        # `read_only` is a promise about THIS product's capability, so it
        # follows the venue's real surface: the six chain sources stay
        # read-only, the three CEX can carry an order — and only through the
        # W2 prepare -> console approval -> execute path. Claiming
        # read_only on an orderable venue would be a false promise.
        orderable = key in ORDERABLE_VENUES
        row: dict = {"key": key, "label": v.label, "kind": v.kind,
                     "auth": v.auth, "read_only": not orderable,
                     "orderable": orderable,
                     "symbol": v.symbol, "summary": v.summary}
        if v.auth == "key":
            ready = bool(st.get(key))
            row["configured"] = ready
            if not ready:
                missing = missing_slots(key) or v.slots
                row["reason"] = (f"not configured (set "
                                 f"{' / '.join(missing)} in secrets.env)")
        else:
            row["configured"] = True
        if orderable:
            row["summary"] = (v.summary + " — order placement only via "
                              "prepare -> console approval -> execute")
        rows.append(row)
    return rows


# --------------------------------------------------------------- transport ---

def _urllib_transport(url: str, method: str, headers: dict,
                      payload: "bytes | None", timeout: float) -> str:
    req = urllib.request.Request(url, data=payload, method=method)
    req.add_header("User-Agent", USER_AGENT)
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
        raise ExchangeError(f"HTTP {e.code} {e.reason}: {snippet}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ExchangeError(f"transport failure: {type(e).__name__}") from None


def _doc(raw: str) -> object:
    try:
        return json.loads(raw)
    except ValueError:
        raise ExchangeError("exchange returned invalid JSON") from None


def _err(doc: object) -> str:
    """One bounded line from a provider error payload (<=160 chars)."""
    for key in ("message", "error", "errors", "msg"):
        val = doc.get(key) if isinstance(doc, dict) else None
        if isinstance(val, str) and val.strip():
            return val.strip()[:ERR_SNIPPET]
        if isinstance(val, dict):
            msg = val.get("message")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()[:ERR_SNIPPET]
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, str) and first.strip():
                return first.strip()[:ERR_SNIPPET]
            if isinstance(first, dict):
                msg = first.get("message") or first.get("type") or first.get("code")
                if msg is not None and str(msg).strip():
                    return str(msg).strip()[:ERR_SNIPPET]
    return "unknown upstream error"


def _num(val: object) -> float:
    """Best-effort numeric read; a non-numeric field stays a string downstream."""
    try:
        return float(str(val))
    except (TypeError, ValueError):
        return 0.0


def _fmt(val: object) -> str:
    """Compact, non-lossy number rendering (no scientific notation, no
    trailing zero padding)."""
    try:
        f = float(str(val))
    except (TypeError, ValueError):
        return str(val)
    if f != f or f in (float("inf"), float("-inf")):
        return str(val)
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    s = f"{f:.8f}".rstrip("0").rstrip(".")
    return s or "0"


def _pct(now: float, before: float) -> str:
    if not before:
        return "n/a"
    return f"{(now - before) / before * 100:+.2f}%"


def _symbol(raw: str | None, venue: str) -> str:
    sym = (raw or "").strip() or VENUES[venue].symbol
    if not SYMBOL_RE.match(sym):
        raise ExchangeError(
            f"symbol must match {SYMBOL_RE.pattern} (got a rejected value)")
    # The alphabet alone still allows a traversal shape ("../.."), and a
    # symbol is spliced into a request path: reject it explicitly.
    if ".." in sym or sym.startswith(("/", ".")) or sym.endswith(("/", ".")):
        raise ExchangeError("symbol must not be a path-like value")
    return sym


# ---------------------------------------------------------------- signing ---

def okx_sign(timestamp: str, method: str, path: str, body: str,
             secret: str) -> str:
    """OKX v5 signature (official docs): Base64(HMAC-SHA256(secret,
    timestamp + method + requestPath + body)). Pure — unit-testable."""
    prehash = timestamp + method + path + body
    digest = hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"),
                      hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def kraken_sign(path: str, postdata: str, nonce: str, secret: str) -> str:
    """Kraken Spot REST signature (official docs + official example vector):
    Base64(HMAC-SHA512(path + SHA256(nonce + postdata),
    base64_decode(secret))). Pure — unit-testable."""
    encoded = (nonce + postdata).encode("utf-8")
    message = path.encode("utf-8") + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode("ascii")


_nonce_lock = threading.Lock()
_last_nonce = 0


def _nonce() -> str:
    """Strictly increasing millisecond nonce (Kraken requirement)."""
    global _last_nonce
    with _nonce_lock:
        now = int(time.time() * 1000)
        if now <= _last_nonce:
            now = _last_nonce + 1
        _last_nonce = now
        return str(now)


def _okx_timestamp() -> str:
    """ISO 8601 UTC with millisecond precision (OKX requirement)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ------------------------------------------------------------ http helpers ---

def _get(url: str, *, transport: Transport, timeout: float,
         headers: dict | None = None) -> object:
    return _doc(transport(url, "GET", headers or {}, None, timeout))


def _post_form(url: str, postdata: str, headers: dict, *,
               transport: Transport, timeout: float) -> object:
    hdr = {"Content-Type": "application/x-www-form-urlencoded"}
    hdr.update(headers)
    return _doc(transport(url, "POST", hdr, postdata.encode("utf-8"), timeout))


def _okx_public(path_query: str, *, transport: Transport,
                timeout: float) -> object:
    doc = _get(_base("okx") + path_query, transport=transport, timeout=timeout)
    if isinstance(doc, dict) and str(doc.get("code", "0")) != "0":
        raise ExchangeError(f"okx: {_err(doc)}")
    return doc


def _okx_private(path_query: str, *, body: str = "",
                 transport: Transport, timeout: float) -> object:
    """Signed OKX request. `body` empty = GET; a body means POST and takes
    part in the signature (OKX v5 pre-hash: timestamp+method+path+body),
    which is how the W2 order endpoint is called."""
    key = _slot("OKX_API_KEY")
    secret = _slot("OKX_API_SECRET")
    passphrase = _slot("OKX_API_PASSPHRASE")
    if not (key and secret and passphrase):
        raise ExchangeError(
            "okx not configured (set OKX_API_KEY / OKX_API_SECRET / "
            "OKX_API_PASSPHRASE in secrets.env)")
    ts = _okx_timestamp()
    method = "POST" if body else "GET"
    sign = okx_sign(ts, method, path_query, body, secret)
    headers = {"OK-ACCESS-KEY": key, "OK-ACCESS-SIGN": sign,
               "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": passphrase}
    if body:
        # OKX's order endpoint takes a JSON body (application/json), and the
        # body is part of the v5 pre-hash — sign exactly these bytes.
        hdr = {"Content-Type": "application/json"}
        hdr.update(headers)
        doc = _post_form(_base("okx") + path_query, body, hdr,
                         transport=transport, timeout=timeout)
    else:
        doc = _get(_base("okx") + path_query, transport=transport,
                   timeout=timeout, headers=headers)
    if isinstance(doc, dict) and str(doc.get("code", "0")) != "0":
        raise ExchangeError(f"okx: {_err(doc)}")
    return doc


def _kraken_pair(sym: str) -> str:
    """Kraken's compact pair spelling: separators dropped, BTC -> XBT
    (XBTUSD), exactly what the public Ticker and AddOrder endpoints take."""
    pair = "".join(ch for ch in sym.upper().replace("BTC", "XBT") if ch.isalnum())
    if not pair:
        raise ExchangeError(f"kraken: no tradable pair in {sym}")
    return pair


def _kraken_private(path: str, *, fields: dict | None = None,
                    transport: Transport, timeout: float) -> object:
    """POST <base><path> with a nonce body (`fields` adds the call's own
    parameters — AddOrder in W2); path starts at /0/private (the signed
    prefix per the official authentication guide)."""
    key = _slot("KRAKEN_API_KEY")
    secret = _slot("KRAKEN_API_SECRET")
    if not (key and secret):
        raise ExchangeError(
            "kraken not configured (set KRAKEN_API_KEY / KRAKEN_API_SECRET "
            "in secrets.env)")
    nonce = _nonce()
    payload: dict = {"nonce": nonce}
    if fields:
        payload.update(fields)
    postdata = urllib.parse.urlencode(payload)
    sign = kraken_sign(path, postdata, nonce, secret)
    doc = _post_form(_base("kraken") + path, postdata,
                     {"API-Key": key, "API-Sign": sign},
                     transport=transport, timeout=timeout)
    if isinstance(doc, dict) and doc.get("error"):
        raise ExchangeError(f"kraken: {_err(doc)}")
    return doc


# ------------------------------------------------------------ market legs ---

def _mkt_okx(sym: str, transport: Transport, timeout: float) -> list[str]:
    doc = _okx_public(f"/api/v5/market/ticker?instId={urllib.parse.quote(sym)}",
                      transport=transport, timeout=timeout)
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, list) or not data:
        raise ExchangeError(f"okx: no ticker for {sym}")
    out = []
    for row in data[:MAX_LINES]:
        if not isinstance(row, dict):
            continue
        last, opn = _num(row.get("last")), _num(row.get("open24h"))
        out.append(f"{row.get('instId')} last={_fmt(row.get('last'))} "
                   f"ask={_fmt(row.get('askPx'))} bid={_fmt(row.get('bidPx'))} "
                   f"chg24h={_pct(last, opn)} "
                   f"high24h={_fmt(row.get('high24h'))} "
                   f"low24h={_fmt(row.get('low24h'))} "
                   f"vol24h={_fmt(row.get('vol24h'))}")
    return out


def _mkt_kraken(sym: str, transport: Transport, timeout: float) -> list[str]:
    # Kraken's base asset for BTC is XBT, and the public endpoint takes the
    # compact spelling (XBTUSD / ETHUSD): drop separators, map BTC->XBT.
    pair = _kraken_pair(sym)
    doc = _get(f"{_base('kraken')}/0/public/Ticker?pair={urllib.parse.quote(pair)}",
               transport=transport, timeout=timeout)
    if isinstance(doc, dict) and doc.get("error"):
        raise ExchangeError(f"kraken: {_err(doc)}")
    result = doc.get("result") if isinstance(doc, dict) else None
    if not isinstance(result, dict) or not result:
        raise ExchangeError(f"kraken: no ticker for {sym}")
    out = []
    for name, row in list(result.items())[:MAX_LINES]:
        if not isinstance(row, dict):
            continue
        last = _num((row.get("c") or ["0"])[0])
        opn = _num(row.get("o"))
        high = (row.get("h") or ["0", "0"])
        low = (row.get("l") or ["0", "0"])
        vol = (row.get("v") or ["0", "0"])
        out.append(f"{name} last={_fmt(last)} "
                   f"chg24h={_pct(last, opn)} "
                   f"high24h={_fmt(high[1] if len(high) > 1 else high[0])} "
                   f"low24h={_fmt(low[1] if len(low) > 1 else low[0])} "
                   f"vol24h={_fmt(vol[1] if len(vol) > 1 else vol[0])}")
    return out


# Verified live 2026-10-24 against the price API (the mint resolves to a real
# quoted asset). Only mints that answered a real price are listed — no
# remembered-by-heart mint is trusted, and a raw mint address passes through.
JUPITER_MINTS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "WETH": "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",
}
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _mkt_jupiter(sym: str, transport: Transport, timeout: float) -> list[str]:
    key = sym.upper()
    mint = JUPITER_MINTS.get(key, sym)
    if key not in JUPITER_MINTS and not MINT_RE.match(mint):
        raise ExchangeError(
            "jupiter: symbol must be one of "
            f"{'|'.join(sorted(JUPITER_MINTS))} or a Solana mint address")
    doc = _get(f"{_base('jupiter')}/price/v3?ids={urllib.parse.quote(mint)}",
               transport=transport, timeout=timeout)
    if not isinstance(doc, dict) or not doc:
        raise ExchangeError(f"jupiter: no price for {sym}")
    out = []
    for mint_id, row in list(doc.items())[:MAX_LINES]:
        if not isinstance(row, dict):
            continue
        name = next((n for n, m in JUPITER_MINTS.items() if m == mint_id),
                    mint_id[:8] + "…")
        # priceChange24h arrives as a PERCENT (not a price delta).
        out.append(f"{name} price={_fmt(row.get('usdPrice'))} "
                   f"chg24h={_num(row.get('priceChange24h')):+.2f}% "
                   f"liq_usd={_fmt(row.get('liquidity'))} "
                   f"block={row.get('blockId')}")
    return out


# Verified live 2026-10-24: mainnet token addresses that returned a real quote.
VELORA_TOKENS = {
    "USDC": ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6),
    "USDT": ("0xdAC17F958D2ee523a2206206994597C13D831ec7", 6),
    "WETH": ("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", 18),
    "DAI": ("0x6B175474E89094C44Da98b954EedeAC495271d0F", 18),
}


def _mkt_velora(sym: str, transport: Transport, timeout: float) -> list[str]:
    src_s, _, dst_s = sym.partition("/")
    if not dst_s:
        raise ExchangeError("velora: symbol must look like USDC/WETH")
    src_s, dst_s = src_s.upper(), dst_s.upper()
    if src_s not in VELORA_TOKENS or dst_s not in VELORA_TOKENS:
        raise ExchangeError(
            "velora: symbol must be a pair of "
            f"{'|'.join(sorted(VELORA_TOKENS))}")
    (src_addr, src_dec), (dst_addr, dst_dec) = (VELORA_TOKENS[src_s],
                                                VELORA_TOKENS[dst_s])
    amount = 10 ** src_dec                      # exactly 1 unit of the source
    query = urllib.parse.urlencode({
        "chainId": 1, "partner": "aidog", "srcToken": src_addr,
        "destToken": dst_addr, "srcDecimals": src_dec,
        "destDecimals": dst_dec, "amount": amount, "side": "SELL"})
    doc = _get(f"{_base('velora')}/v2/quote?{query}",
               transport=transport, timeout=timeout)
    if not isinstance(doc, dict) or not doc.get("delta"):
        raise ExchangeError(f"velora: {_err(doc) if doc else 'empty response'}")
    route = ((doc.get("delta") or {}).get("route") or {})
    origin = route.get("origin") or {}
    src_in = origin.get("input") or {}
    dst_out = origin.get("output") or {}
    usd_in, usd_out = _num(src_in.get("amountUSD")), _num(dst_out.get("amountUSD"))
    out_units = _num(dst_out.get("amount")) / (10 ** dst_dec)
    fee_pct = (1 - usd_out / usd_in) * 100 if usd_in else 0.0
    return [f"{src_s}/{dst_s} 1 {src_s} -> {_fmt(out_units)} {dst_s} "
            f"usd_in={_fmt(usd_in)} usd_out={_fmt(usd_out)} "
            f"cost={fee_pct:.2f}%"]


def _mkt_hyperliquid(sym: str, transport: Transport, timeout: float) -> list[str]:
    doc = _post_form(f"{_base('hyperliquid')}/info", json.dumps({"type": "allMids"}),
                     {"Content-Type": "application/json"},
                     transport=transport, timeout=timeout)
    # Observed live 2026-10-24: the endpoint answers a FLAT coin->mid map (no
    # "mids" wrapper); accept the wrapped shape too in case it returns.
    mids = doc.get("mids") if isinstance(doc, dict) else None
    if not isinstance(mids, dict):
        mids = doc if isinstance(doc, dict) else None
    if not isinstance(mids, dict) or not mids:
        raise ExchangeError(f"hyperliquid: {_err(doc) if doc else 'empty response'}")
    coin = sym.upper()
    if coin in mids:
        picks = {coin: mids[coin]}
    else:                                        # substring match, bounded
        hits = {k: v for k, v in mids.items() if coin in k.upper()}
        if not hits:
            raise ExchangeError(f"hyperliquid: no market matching {sym}")
        picks = dict(list(hits.items())[:MAX_LINES])
    return [f"{k} mid={_fmt(v)}" for k, v in picks.items()]


def _mkt_dydx(sym: str, transport: Transport, timeout: float) -> list[str]:
    market = sym.upper().replace("/", "-")
    doc = _get(f"{_base('dydx')}/v4/perpetualMarkets?market={urllib.parse.quote(market)}",
               transport=transport, timeout=timeout)
    markets = doc.get("markets") if isinstance(doc, dict) else None
    if not isinstance(markets, dict) or not markets:
        raise ExchangeError(f"dydx: no market {market}")
    out = []
    for name, row in list(markets.items())[:MAX_LINES]:
        if not isinstance(row, dict):
            continue
        out.append(f"{name} last={_fmt(row.get('oraclePrice'))} "
                   f"chg24h={_fmt(row.get('priceChange24H'))} "
                   f"vol24h={_fmt(row.get('volume24H'))} "
                   f"funding={_fmt(row.get('nextFundingRate'))} "
                   f"oi={_fmt(row.get('openInterest'))} "
                   f"status={row.get('status')}")
    return out


def _mkt_gmx(sym: str, transport: Transport, timeout: float) -> list[str]:
    doc = _get(f"{_base('gmx')}/v1/pairs", transport=transport, timeout=timeout)
    if not isinstance(doc, list) or not doc:
        raise ExchangeError("gmx: empty pairs feed")
    want = sym.upper().replace("/USD", "").replace("-USD", "")
    rows = [r for r in doc
            if isinstance(r, dict)
            and str(r.get("base_currency", "")).upper() == want]
    if not rows:
        raise ExchangeError(f"gmx: no market for {sym}")
    return [f"{r.get('ticker_id')} last={_fmt(r.get('last_price'))} "
            f"high={_fmt(r.get('high'))} low={_fmt(r.get('low'))} "
            f"funding={_fmt(r.get('funding_rate'))} "
            f"oi_usd={_fmt(r.get('open_interest'))} "
            f"liq_usd={_fmt(r.get('liquidity_in_usd'))}"
            for r in rows[:MAX_LINES]]


# Public JSON-RPC chain heads. Every entry answered a real request on
# 2026-10-24; the Cloudflare gateway was dropped because it refused
# eth_blockNumber that day (honest selection, not a remembered list).
CHAINS = {
    "base": ("https://mainnet.base.org", "eth_blockNumber"),
    "ethereum": ("https://ethereum-rpc.publicnode.com", "eth_blockNumber"),
    "solana": ("https://api.mainnet-beta.solana.com", "getSlot"),
}


def _mkt_rpc(sym: str, transport: Transport, timeout: float) -> list[str]:
    chain = sym.lower()
    if chain not in CHAINS:
        raise ExchangeError(f"rpc: chain must be one of {'|'.join(CHAINS)}")
    env = (os.environ.get("AIDOG_EX_RPC_URL", "").strip()
           or os.environ.get("AIDOG_EX_RPC", "").strip())
    base = env.rstrip("/") or CHAINS[chain][0]
    method = CHAINS[chain][1]
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": []})
    doc = _post_form(base, body, {"Content-Type": "application/json"},
                     transport=transport, timeout=timeout)
    if isinstance(doc, dict) and doc.get("error"):
        raise ExchangeError(f"rpc/{chain}: {_err(doc)}")
    result = doc.get("result") if isinstance(doc, dict) else None
    if result is None:
        raise ExchangeError(f"rpc/{chain}: no result")
    if isinstance(result, str) and result.startswith("0x"):
        head = f"{chain} head={int(result, 16)}"
    else:
        head = f"{chain} head={_fmt(result)}"
    return [f"{head} method={method} (public, keyless)"]


def _mkt_binance(sym: str, transport: Transport, timeout: float) -> list[str]:
    doc = _get(f"{_base('binance')}/api/v3/ticker/24hr?symbol={urllib.parse.quote(sym)}",
               transport=transport, timeout=timeout)
    if not isinstance(doc, dict) or doc.get("code") is not None:
        raise ExchangeError(f"binance: {_err(doc) if doc else 'empty response'}")
    last, opn = _num(doc.get("lastPrice")), _num(doc.get("prevClosePrice"))
    return [f"{doc.get('symbol')} last={_fmt(doc.get('lastPrice'))} "
            f"ask={_fmt(doc.get('askPrice'))} bid={_fmt(doc.get('bidPrice'))} "
            f"chg24h={_pct(last, opn)} "
            f"high24h={_fmt(doc.get('highPrice'))} "
            f"low24h={_fmt(doc.get('lowPrice'))} "
            f"vol24h={_fmt(doc.get('volume'))} "
            f"quote_vol={_fmt(doc.get('quoteVolume'))}"]


_MARKET_FN = {
    "okx": _mkt_okx, "kraken": _mkt_kraken, "jupiter": _mkt_jupiter,
    "velora": _mkt_velora, "hyperliquid": _mkt_hyperliquid,
    "dydx": _mkt_dydx, "gmx": _mkt_gmx, "rpc": _mkt_rpc,
    "binance": _mkt_binance,
}


# ---------------------------------------------------------- account legs ----

def _okx_portfolio(transport: Transport, timeout: float) -> list[str]:
    doc = _okx_private("/api/v5/account/balance", transport=transport,
                       timeout=timeout)
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, list) or not data:
        raise ExchangeError("okx: unexpected balance response shape")
    head = data[0] if isinstance(data[0], dict) else {}
    lines = [f"total_eq_usd={_fmt(head.get('totalEq'))} (account equity)"]
    details = head.get("details")
    for row in (details if isinstance(details, list) else []):
        if not isinstance(row, dict):
            continue
        if _num(row.get("bal")) == 0 and _num(row.get("eq")) == 0:
            continue
        lines.append(f"{row.get('ccy')} bal={_fmt(row.get('bal'))} "
                     f"eq_usd={_fmt(row.get('eq'))} "
                     f"avail={_fmt(row.get('availBal'))} "
                     f"frozen={_fmt(row.get('ordFrozen'))}")
    if len(lines) == 1:
        lines.append("no non-zero balances")
    return lines[:MAX_LINES]


def _okx_orders(transport: Transport, timeout: float) -> list[str]:
    doc = _okx_private("/api/v5/trade/orders-pending?instType=SPOT",
                       transport=transport, timeout=timeout)
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, list):
        raise ExchangeError("okx: unexpected orders response shape")
    if not data:
        return ["no open spot orders"]
    lines = []
    for row in data[:MAX_LINES]:
        if not isinstance(row, dict):
            continue
        lines.append(f"{row.get('ordId')} {row.get('instId')} "
                     f"{row.get('side')} px={_fmt(row.get('px'))} "
                     f"sz={_fmt(row.get('sz'))} "
                     f"filled={_fmt(row.get('accFillSz'))} "
                     f"state={row.get('state')}")
    return lines


def _kraken_portfolio(transport: Transport, timeout: float) -> list[str]:
    doc = _kraken_private("/0/private/Balance", transport=transport,
                          timeout=timeout)
    result = doc.get("result") if isinstance(doc, dict) else None
    if not isinstance(result, dict):
        raise ExchangeError("kraken: unexpected balance response shape")
    lines = [f"{k}={_fmt(v)}" for k, v in result.items()
             if _num(v) != 0][:MAX_LINES]
    if not lines:
        lines = ["no non-zero balances"]
    return lines


def _kraken_orders(transport: Transport, timeout: float) -> list[str]:
    doc = _kraken_private("/0/private/OpenOrders", transport=transport,
                          timeout=timeout)
    result = doc.get("result") if isinstance(doc, dict) else None
    open_orders = (result or {}).get("open") if isinstance(result, dict) else None
    if not isinstance(open_orders, dict):
        raise ExchangeError("kraken: unexpected orders response shape")
    if not open_orders:
        return ["no open orders"]
    lines = []
    for txid, row in list(open_orders.items())[:MAX_LINES]:
        if not isinstance(row, dict):
            continue
        descr = row.get("descr") or {}
        lines.append(f"{txid} {descr.get('pair')} {descr.get('type')} "
                     f"{descr.get('ordertype')} px={_fmt(descr.get('price'))} "
                     f"vol={_fmt(row.get('vol'))} status={row.get('status')}")
    return lines


# ------------------------------------------------- Binance (W2 third CEX) --
# Official surface: there is NO official MCP/agent API (org search = 0 on
# 2026-10-24) — only Skills + REST, so this is our own thin adapter.
# Auth: `X-MBX-APIKEY: <key>` header + `signature = HMAC_SHA256(secret,
# queryString)` in HEX, appended to the query string (params travel in the
# URL for both GET and POST; the POST carries no body).


def _binance_request(path: str, params: dict, *, signed: bool,
                     method: str = "GET", transport: Transport,
                     timeout: float) -> object:
    key = _slot("BINANCE_API_KEY")
    secret = _slot("BINANCE_API_SECRET")
    if signed and not (key and secret):
        raise ExchangeError(
            "binance not configured (set BINANCE_API_KEY / "
            "BINANCE_API_SECRET in secrets.env)")
    query = urllib.parse.urlencode(params) if params else ""
    headers: dict = {}
    if signed:
        joiner = "&" if query else ""
        query = (f"{query}{joiner}timestamp={int(time.time() * 1000)}"
                 f"&recvWindow=5000")
        digest = hmac.new(secret.encode("utf-8"), query.encode("utf-8"),
                          hashlib.sha256).hexdigest()
        query = f"{query}&signature={digest}"
        headers["X-MBX-APIKEY"] = key
    url = _base("binance") + path + (f"?{query}" if query else "")
    doc = _doc(transport(url, method, headers, None, timeout))
    if isinstance(doc, dict) and doc.get("code") is not None:
        raise ExchangeError(f"binance: {_err(doc)}")
    return doc


def _binance_portfolio(transport: Transport, timeout: float) -> list[str]:
    doc = _binance_request("/api/v3/account", {}, signed=True,
                           transport=transport, timeout=timeout)
    balances = doc.get("balances") if isinstance(doc, dict) else None
    if not isinstance(balances, list):
        raise ExchangeError("binance: unexpected account response shape")
    lines = ["balances (free + locked; this endpoint carries no USD "
             "valuation — quote them from exchange_markets)"]
    for row in balances[:MAX_LINES * 4]:
        if not isinstance(row, dict):
            continue
        if _num(row.get("free")) == 0 and _num(row.get("locked")) == 0:
            continue
        lines.append(f"{row.get('asset')} free={_fmt(row.get('free'))} "
                     f"locked={_fmt(row.get('locked'))}")
    if len(lines) == 1:
        lines.append("no non-zero balances")
    return lines[:MAX_LINES]


def _binance_orders(transport: Transport, timeout: float) -> list[str]:
    doc = _binance_request("/api/v3/openOrders", {}, signed=True,
                           transport=transport, timeout=timeout)
    if not isinstance(doc, list):
        raise ExchangeError("binance: unexpected openOrders response shape")
    if not doc:
        return ["no open orders"]
    lines = []
    for row in doc[:MAX_LINES]:
        if not isinstance(row, dict):
            continue
        lines.append(f"{row.get('orderId')} {row.get('symbol')} "
                     f"{row.get('side')} {row.get('type')} "
                     f"px={_fmt(row.get('price'))} "
                     f"qty={_fmt(row.get('origQty'))} "
                     f"status={row.get('status')}")
    return lines


# Account legs per venue (keeps portfolio()/orders() dispatch data-driven —
# adding a venue means adding a row, not another if/else chain).
_PORTFOLIO_FN = {"okx": _okx_portfolio, "kraken": _kraken_portfolio,
                 "binance": _binance_portfolio}
_ORDERS_FN = {"okx": _okx_orders, "kraken": _kraken_orders,
              "binance": _binance_orders}


# ------------------------------------------------------------------- API ----

def _check_venue(venue: str) -> Venue:
    v = VENUES.get(venue)
    if v is None:
        raise ExchangeError(
            f"unknown venue {venue!r} (known: {'|'.join(VENUES)})")
    return v


def markets(venue: str, symbol: str | None = None, *,
            transport: Transport | None = None,
            timeout: float = DEFAULT_TIMEOUT) -> str:
    """READ-ONLY market data for one venue. Works for every venue with no
    credential at all — public books only. One compact line per row."""
    v = _check_venue(venue)
    sym = _symbol(symbol, venue)
    transport = transport or _urllib_transport
    rows = _MARKET_FN[venue](sym, transport, timeout)
    if not rows:
        raise ExchangeError(f"{venue}: no rows for {sym}")
    return "\n".join(rows[:MAX_LINES])


def portfolio(venue: str, *, transport: Transport | None = None,
              timeout: float = DEFAULT_TIMEOUT) -> str:
    """READ-ONLY account balances for a CEX venue (slot-gated). Chain venues
    have no account surface in this product — they return an honest error
    naming that, never fabricated data."""
    v = _check_venue(venue)
    if v.kind != "cex":
        raise ExchangeError(
            f"{venue} has no account surface here (chain venues are public "
            "market sources only; balances stay on the exchange venue)")
    transport = transport or _urllib_transport
    fn = _PORTFOLIO_FN[venue]
    return "\n".join(fn(transport, timeout))


def orders(venue: str, *, transport: Transport | None = None,
           timeout: float = DEFAULT_TIMEOUT) -> str:
    """READ-ONLY open orders for a CEX venue (slot-gated). Listing orders is
    a read; placing one does not exist in this module."""
    v = _check_venue(venue)
    if v.kind != "cex":
        raise ExchangeError(
            f"{venue} has no order surface here (chain venues are public "
            "market sources only)")
    transport = transport or _urllib_transport
    fn = _ORDERS_FN[venue]
    return "\n".join(fn(transport, timeout))


def quote(venue: str, symbol: str | None = None, *, timeout: float = 10.0) -> str:
    """Console-facing market probe: same read-only path as ``markets`` with
    a short timeout and an injectable-transport-free signature (the HTTP
    surface never exposes account reads — those are model tools only)."""
    return markets(venue, symbol, timeout=timeout)


# ============================================================ W2: orders ==
#
# W2 (owner decree 2026-10-25) adds ORDER PLACEMENT behind a hard approval
# gate that lives in CODE, not in configuration:
#
#  * prepare / execute are separate functions. ``execute_order`` takes a
#    plan_id and NOTHING else: venue, side, symbol, type, qty and price are
#    read back from the stored plan, so no prompt, settings value or env var
#    can rewrite an order between approval and execution.
#  * ``execute_order`` fails closed. A plan must be status=approved — the
#    OWNER approved it in the console (Bearer WEB_TOKEN write gate, a
#    credential the model never sees), or ``prepare_order`` auto-approved it
#    under a POSITIVE threshold and the day's ledger still allows it when it
#    executes. Default thresholds are 0 -> every plan waits for a human.
#    MARKET orders never auto-approve, whatever the thresholds say.
#  * WITHDRAWALS AND ON-CHAIN TRANSFERS DO NOT EXIST HERE: no function, no
#    endpoint, no settings key can move funds off a venue. The core suite
#    asserts that absence against this very source file.
#
# State is one JSON file, <state_dir>/web3_orders.json: the plans plus a
# Beijing-date daily ledger of everything executed (manual approvals count
# too — a spent day is a smaller tomorrow). Writes are serialised by a
# module lock and land atomically (tmp + os.replace).

PLAN_TTL_SEC = 900            # a plan lives 15 minutes, then expires
PLAN_MAX = 50                 # at most this many plans are kept
STORE_NAME = "web3_orders.json"
BJT = timezone(timedelta(hours=8))   # ledger day = Beijing date (cron parity)
PLAN_ACTIONS = ("approve", "reject", "cancel")
ORDER_TYPES = ("limit", "market")
SIDES = ("buy", "sell")
OPEN_STATUSES = ("pending", "approved")
TERMINAL_STATUSES = ("rejected", "executed", "expired", "cancelled")
THRESHOLD_MAX_USD = 1_000_000.0      # above this a cap is treated as invalid

_store_lock = threading.Lock()


def _maybe_float(v: object) -> float | None:
    """Strict numeric read for provider fields: None when the value is not a
    usable finite number — never a silent 0.0, which would fake a zero fee."""
    try:
        f = float(str(v))
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _bj_day(ts: float | None = None) -> str:
    """Beijing calendar date (YYYY-MM-DD) for the daily ledger — the same
    day boundary the cron scheduler uses, so "today" means the same thing
    everywhere in the product."""
    if ts is None:
        moment = datetime.now(BJT)
    else:
        moment = datetime.fromtimestamp(float(ts), BJT)
    return moment.strftime("%Y-%m-%d")


def thresholds(settings: dict | None = None) -> dict:
    """Auto-approval caps in USD: {"per_tx": float, "daily": float} from
    settings.web3.auto_usd_per_tx / auto_usd_daily. FAIL-CLOSED: a missing
    block, a non-numeric value, a boolean, a negative or an out-of-range
    number all collapse to 0.0 — and 0.0 means nothing auto-approves."""
    if settings is None:
        try:
            settings = json.loads(pp.settings_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
    block = settings.get("web3") if isinstance(settings, dict) else None
    if not isinstance(block, dict):
        block = {}
    out: dict = {}
    for src, key in (("auto_usd_per_tx", "per_tx"),
                     ("auto_usd_daily", "daily")):
        val = block.get(src)
        if isinstance(val, bool) or not isinstance(val, (int, float)) \
                or not (0.0 <= float(val) <= THRESHOLD_MAX_USD):
            out[key] = 0.0
        else:
            out[key] = float(val)
    return out


def _store_path(state_dir: str | Path | None = None) -> Path:
    root = Path(state_dir) if state_dir else pp.state_dir()
    return Path(root) / STORE_NAME


def _load_store(path: Path) -> dict:
    """Plan store reader; a missing or corrupt file reads as an EMPTY store
    (fail-closed for orders: no plan -> nothing executes, never a crash)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {}
    plans = doc.get("plans") if isinstance(doc, dict) else None
    ledger = doc.get("ledger") if isinstance(doc, dict) else None
    return {"plans": plans if isinstance(plans, dict) else {},
            "ledger": ledger if isinstance(ledger, dict) else {}}


def _save_store(path: Path, store: dict) -> None:
    """Atomic write: a reader never observes a half-written plan file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=True, indent=1,
                              sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _spent_today(store: dict) -> float:
    entry = store["ledger"].get(_bj_day())
    if not isinstance(entry, dict):
        return 0.0
    return _maybe_float(entry.get("spent_usd")) or 0.0


def _sweep_expired(store: dict) -> bool:
    """Lazily expire plans whose TTL elapsed (pending/approved only — an
    executed plan keeps its history). Returns True when anything changed."""
    now = time.time()
    changed = False
    for plan in store["plans"].values():
        if not isinstance(plan, dict):
            continue
        if plan.get("status") in OPEN_STATUSES:
            expires = _maybe_float(plan.get("expires_at"))
            if expires is None or expires <= now:
                plan["status"] = "expired"
                plan["reason"] = (f"plan TTL elapsed ({PLAN_TTL_SEC}s); "
                                  "prepare a new plan")
                changed = True
    return changed


def _insert_plan(store: dict, plan: dict) -> None:
    plans = store["plans"]
    if len(plans) >= PLAN_MAX:
        terminal = [pid for pid, p in plans.items()
                    if isinstance(p, dict) and p.get("status") in TERMINAL_STATUSES]
        if not terminal:
            raise ExchangeError(
                f"plan store is full ({PLAN_MAX} live plans); approve, "
                "execute or reject them before preparing another")
        terminal.sort(key=lambda pid: _maybe_float(plans[pid].get("created_at")) or 0.0)
        while len(plans) >= PLAN_MAX and terminal:
            plans.pop(terminal.pop(0), None)
    plans[plan["id"]] = plan


# ------------------------------------------------------------ order books --

def _levels(raw: object) -> list[tuple[float, float]]:
    """Book rows -> [(price, size), ...], dropping anything malformed. A
    corrupt level never becomes a fabricated price: it is skipped."""
    out: list[tuple[float, float]] = []
    if isinstance(raw, (list, tuple)):
        for lvl in raw:
            if not isinstance(lvl, (list, tuple)) or len(lvl) < 2:
                continue
            px, sz = _maybe_float(lvl[0]), _maybe_float(lvl[1])
            if px is None or sz is None or px <= 0 or sz <= 0:
                continue
            out.append((px, sz))
    return out


def _book_okx(sym: str, transport: Transport, timeout: float) -> tuple[list, list]:
    doc = _okx_public(f"/api/v5/market/books?instId={urllib.parse.quote(sym)}&sz=50",
                      transport=transport, timeout=timeout)
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ExchangeError(f"okx: no book for {sym}")
    row = data[0]
    return _levels(row.get("bids")), _levels(row.get("asks"))


def _book_kraken(sym: str, transport: Transport, timeout: float) -> tuple[list, list]:
    pair = _kraken_pair(sym)
    doc = _get(f"{_base('kraken')}/0/public/Depth?pair={urllib.parse.quote(pair)}&count=100",
               transport=transport, timeout=timeout)
    if isinstance(doc, dict) and doc.get("error"):
        raise ExchangeError(f"kraken: {_err(doc)}")
    result = doc.get("result") if isinstance(doc, dict) else None
    row = next(iter(result.values()), None) if isinstance(result, dict) else None
    if not isinstance(row, dict):
        raise ExchangeError(f"kraken: no book for {sym}")
    return _levels(row.get("bids")), _levels(row.get("asks"))


def _book_binance(sym: str, transport: Transport, timeout: float) -> tuple[list, list]:
    doc = _get(f"{_base('binance')}/api/v3/depth?symbol={urllib.parse.quote(sym)}&limit=100",
               transport=transport, timeout=timeout)
    if not isinstance(doc, dict) or doc.get("code") is not None:
        raise ExchangeError(f"binance: {_err(doc) if doc else 'empty response'}")
    return _levels(doc.get("bids")), _levels(doc.get("asks"))


_BOOK_FN = {"okx": _book_okx, "kraken": _book_kraken,
            "binance": _book_binance}


def _fill_quote(side: str, bids: list, asks: list, qty: float,
                price: float | None) -> tuple[float, float | None, float, bool]:
    """Walk one side of the book for `qty` -> (filled, vwap, best, depth_ok).
    A limit price filters the walk to marketable levels, so a resting limit
    honestly reports depth_ok=False instead of a fabricated fill."""
    top = asks if side == "buy" else bids
    if not top:
        return 0.0, None, 0.0, False
    best = (min(px for px, _ in top) if side == "buy"
            else max(px for px, _ in top))
    if side == "buy":
        levels = sorted(asks, key=lambda lv: lv[0])
        if price is not None:
            levels = [lv for lv in levels if lv[0] <= price * (1 + 1e-12)]
    else:
        levels = sorted(bids, key=lambda lv: -lv[0])
        if price is not None:
            levels = [lv for lv in bids if lv[0] >= price * (1 - 1e-12)]
            levels = sorted(levels, key=lambda lv: -lv[0])
    filled = 0.0
    cost = 0.0
    for px, sz in levels:
        if filled >= qty - 1e-12:
            break
        take = min(sz, qty - filled)
        if take <= 0:
            continue
        filled += take
        cost += take * px
    vwap = cost / filled if filled > 0 else None
    return filled, vwap, best, filled >= qty - 1e-12


def _slippage_bps(side: str, vwap: float | None, best: float,
                  filled: float) -> float | None:
    """VWAP distance from the top of book in basis points, POSITIVE when the
    walk is worse for the trader. None when nothing filled."""
    if vwap is None or filled <= 0 or best <= 0:
        return None
    if side == "buy":
        return (vwap - best) / best * 10000.0
    return (best - vwap) / best * 10000.0


# ---------------------------------------------------------------- fees -----
# Fees are quoted only from the venue's own signed endpoints, and only when
# the venue's slots exist. No slot (or a failed quote) = rate None + an honest
# note: the fee is never guessed and never folded into a threshold it did not
# contribute to.

def _fee_okx(sym: str, transport: Transport, timeout: float) -> tuple[float | None, str]:
    missing = missing_slots("okx")
    if missing:
        return None, (f"fee unknown (set {' / '.join(missing)} in secrets.env "
                      "to quote okx fees)")
    try:
        doc = _okx_private(
            f"/api/v5/account/trade-fee?instType=SPOT&instId={urllib.parse.quote(sym)}",
            transport=transport, timeout=timeout)
    except ExchangeError as e:
        return None, f"fee unknown ({e})"
    data = doc.get("data") if isinstance(doc, dict) else None
    row = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
    for key in ("takerFeeRate", "makerFeeRate"):
        rate = _maybe_float(row.get(key))
        if rate is not None and rate >= 0:
            return rate, f"okx spot {key}={_fmt(rate)} (trade-fee endpoint)"
    return None, "fee unknown (trade-fee response carried no numeric rate)"


def _fee_kraken(sym: str, transport: Transport, timeout: float) -> tuple[float | None, str]:
    missing = missing_slots("kraken")
    if missing:
        return None, (f"fee unknown (set {' / '.join(missing)} in secrets.env "
                      "to quote kraken fees)")
    try:
        doc = _kraken_private("/0/private/TradeVolume", transport=transport,
                              timeout=timeout)
    except ExchangeError as e:
        return None, f"fee unknown ({e})"
    result = doc.get("result") if isinstance(doc, dict) else None
    fees = result.get("fees") if isinstance(result, dict) else None
    if isinstance(fees, dict) and fees:
        first = next(iter(fees.values()))
        pct = _maybe_float(first.get("fee")) if isinstance(first, dict) else None
        if pct is not None and pct >= 0:
            return pct / 100.0, (f"kraken account fee {_fmt(pct)}% -> "
                                 f"{pct / 100.0} fraction (TradeVolume)")
    return None, "fee unknown (TradeVolume returned no fee tier)"


def _fee_binance(sym: str, transport: Transport, timeout: float) -> tuple[float | None, str]:
    missing = missing_slots("binance")
    if missing:
        return None, (f"fee unknown (set {' / '.join(missing)} in secrets.env "
                      "to quote binance fees)")
    try:
        doc = _binance_request("/api/v3/account/commission",
                               {"symbol": sym.upper()}, signed=True,
                               transport=transport, timeout=timeout)
    except ExchangeError as e:
        return None, f"fee unknown ({e})"
    rate = _maybe_float(doc.get("commission")) if isinstance(doc, dict) else None
    if rate is None and isinstance(doc, dict):
        rates = doc.get("commissionRates")
        if isinstance(rates, dict):
            rate = _maybe_float(rates.get("taker"))
    if rate is not None and rate >= 0:
        return rate, f"binance spot commission={_fmt(rate)} (commission endpoint)"
    return None, "fee unknown (commission response carried no numeric rate)"


_FEE_FN = {"okx": _fee_okx, "kraken": _fee_kraken, "binance": _fee_binance}


# ------------------------------------------------------------ order sends ---
# Each sender takes the STORED plan only — no caller-supplied parameter
# reaches a venue. A venue rejection is an honest ExchangeError; the plan
# keeps its status (attempts += 1) so a failed send can be retried or
# rejected, and it never touches the ledger.

def _place_okx(plan: dict, transport: Transport, timeout: float) -> str:
    path = "/api/v5/trade/order"
    body: dict = {"instId": plan["symbol"], "tdMode": "cash",
                  "side": plan["side"], "ordType": plan["order_type"],
                  "sz": _fmt(plan.get("qty"))}
    if plan.get("order_type") == "limit":
        body["px"] = _fmt(plan.get("price"))
    doc = _okx_private(path, body=json.dumps(body, separators=(",", ":")),
                       transport=transport, timeout=timeout)
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ExchangeError("okx: unexpected order response shape")
    row = data[0]
    if str(row.get("sCode", "0")) not in ("0", ""):
        raise ExchangeError(
            f"okx order rejected: {row.get('sMsg') or 'unknown venue error'}")
    oid = row.get("ordId")
    if not oid:
        raise ExchangeError("okx: order acknowledged without an order id")
    return str(oid)


def _place_kraken(plan: dict, transport: Transport, timeout: float) -> str:
    path = "/0/private/AddOrder"
    fields: dict = {"pair": _kraken_pair(str(plan["symbol"])),
                    "type": plan["side"],
                    "ordertype": ("limit" if plan.get("order_type") == "limit"
                                  else "market"),
                    "volume": _fmt(plan.get("qty"))}
    if plan.get("order_type") == "limit":
        fields["price"] = _fmt(plan.get("price"))
    doc = _kraken_private(path, fields=fields, transport=transport, timeout=timeout)
    result = doc.get("result") if isinstance(doc, dict) else None
    txids = result.get("txid") if isinstance(result, dict) else None
    if not isinstance(txids, list) or not txids:
        raise ExchangeError("kraken: order acknowledged without a transaction id")
    return str(txids[0])


def _place_binance(plan: dict, transport: Transport, timeout: float) -> str:
    params: dict = {"symbol": str(plan["symbol"]).upper(),
                    "side": str(plan["side"]).upper(),
                    "type": ("LIMIT" if plan.get("order_type") == "limit"
                             else "MARKET"),
                    "quantity": _fmt(plan.get("qty"))}
    if plan.get("order_type") == "limit":
        params["timeInForce"] = "GTC"
        params["price"] = _fmt(plan.get("price"))
    doc = _binance_request("/api/v3/order", params, signed=True, method="POST",
                           transport=transport, timeout=timeout)
    oid = doc.get("orderId") if isinstance(doc, dict) else None
    if oid is None:
        raise ExchangeError("binance: order acknowledged without an order id")
    return str(oid)


_PLACE_FN = {"okx": _place_okx, "kraken": _place_kraken,
             "binance": _place_binance}


# ------------------------------------------------------- approval verdict ---

def _auto_verdict(order_type: str, notional: float, spent: float,
                   th: dict) -> tuple[bool, str]:
    """May this plan approve itself? EVERY condition must hold, in this
    order, and the message says exactly which one failed."""
    if order_type == "market":
        return False, "market orders are never auto-approved (W2 hard rule)"
    if th["per_tx"] <= 0:
        return False, "auto approvals disabled (web3.auto_usd_per_tx = 0)"
    if notional > th["per_tx"]:
        return False, (f"notional_usd {_fmt(notional)} exceeds the per-transaction "
                       f"cap {_fmt(th['per_tx'])}")
    if th["daily"] <= 0:
        return False, "daily cap disabled (web3.auto_usd_daily = 0)"
    if spent + notional > th["daily"]:
        return False, (f"would exceed the daily cap {_fmt(th['daily'])} "
                       f"(already spent {_fmt(spent)} today)")
    return True, (f"limit order within caps (notional {_fmt(notional)} <= "
                  f"per-tx {_fmt(th['per_tx'])}, day "
                  f"{_fmt(spent + notional)} <= {_fmt(th['daily'])})")


# ------------------------------------------------------------ plan state ----

# Car 4 (D6) call site 2 guardrail knobs. `score` is the fractional INDEX over
# the criteria levels the question defines (0 = negligible ... 4 = severe for
# our five levels), so "high or worse" is >= 3.0. Confidence below the floor
# is treated as "no clearance" — money fails closed, never open.
DECISION_MIN_CONFIDENCE = 0.6
DECISION_RISK_MAX = 3.0


def _decision_guard(facts: dict) -> dict:
    """Car 4 (D6) call site 2 — the Jev order-plan guardrail.

    Returns {"downgrade": bool, "reason": str}. It may ONLY remove an auto
    approval: nothing here approves, executes or unblocks a plan. When the
    decision client is configured, anything short of a CONFIDENT "inside the
    declared limits and low risk" answer downgrades auto -> pending, which
    routes the plan to the human gate that already exists (an unreachable or
    unanswerable question is not a clearance). Client off == today's rule,
    unchanged. Facts are numbers and enum words — never a credential, never a
    log line, and the reason string carries no payload."""
    try:
        from . import decision as _deci           # noqa: PLC0415 — lazy import
    except Exception:                             # noqa: BLE001 — fail closed
        return {"downgrade": True,
                "reason": "decision client unavailable"}
    try:
        cfg = _deci.config()
    except Exception:                             # noqa: BLE001
        return {"downgrade": True,
                "reason": "decision configuration unreadable"}
    if not cfg["active"]:
        return {"downgrade": False, "reason": ""}   # opted out == today's rule
    try:
        got = _deci.plan_guard(facts)
    except _deci.DecisionError as e:
        return {"downgrade": True, "reason": f"no decision ({e.reason})"}
    except Exception as e:                        # noqa: BLE001 — class name only
        return {"downgrade": True,
                "reason": f"no decision ({type(e).__name__})"}
    try:
        conf = float(got.get("confidence") or 0.0)
        risk = float(got.get("risk") or 0.0)
    except (TypeError, ValueError):
        conf, risk = 0.0, 0.0
    if not got.get("within_limits"):
        return {"downgrade": True,
                "reason": f"outside the declared limits (confidence {conf:.2f})"}
    if risk >= DECISION_RISK_MAX:
        return {"downgrade": True,
                "reason": (f"risk {risk:g}/10 >= {DECISION_RISK_MAX:g} "
                           f"(confidence {conf:.2f})")}
    if conf < DECISION_MIN_CONFIDENCE:
        return {"downgrade": True,
                "reason": (f"low decision confidence {conf:.2f} < "
                           f"{DECISION_MIN_CONFIDENCE:g}")}
    return {"downgrade": False,
            "reason": (f"within limits (confidence {conf:.2f}, "
                       f"risk {risk:g}/10)")}

def prepare_order(venue: str, side: str, symbol: str | None, order_type: str,
                  qty: object, price: object = None, *,
                  transport: Transport | None = None,
                  timeout: float = DEFAULT_TIMEOUT,
                  state_dir: str | Path | None = None) -> str:
    """W2 DRY RUN. Validates the order, walks the live book for a VWAP and
    a depth verdict, quotes the fee when the venue is configured, applies the
    auto-approval thresholds and stores a plan. Nothing is sent to a venue
    here — sending is ``execute_order``'s job and needs an approved plan."""
    _check_venue(venue)
    if venue not in ORDERABLE_VENUES:
        raise ExchangeError(
            f"{venue} cannot carry an order (chain venues are public market "
            f"sources; orderable venues: {'|'.join(ORDERABLE_VENUES)})")
    side = str(side or "").strip().lower()
    if side not in SIDES:
        raise ExchangeError(f"side must be one of {'|'.join(SIDES)}")
    order_type = str(order_type or "").strip().lower()
    if order_type not in ORDER_TYPES:
        raise ExchangeError(f"order_type must be one of {'|'.join(ORDER_TYPES)}")
    qty_f = _maybe_float(qty)
    if qty_f is None or qty_f <= 0:
        raise ExchangeError("qty must be a positive number")
    if order_type == "limit":
        price_f = _maybe_float(price)
        if price_f is None or price_f <= 0:
            raise ExchangeError("limit orders require a positive price")
    else:
        if price not in (None, "", 0, 0.0):
            raise ExchangeError("market orders take no price")
        price_f = None
    sym = _symbol(symbol, venue)
    missing = missing_slots(venue)
    if missing:
        raise ExchangeError(
            f"{venue} not configured (set {' / '.join(missing)} in secrets.env)")
    transport = transport or _urllib_transport
    bids, asks = _BOOK_FN[venue](sym, transport, timeout)
    if not bids and not asks:
        raise ExchangeError(f"{venue}: empty book for {sym}")
    filled, vwap, best, depth_ok = _fill_quote(side, bids, asks, qty_f, price_f)
    slip = _slippage_bps(side, vwap, best, filled)
    if order_type == "market":
        if vwap is None or filled <= 0:
            raise ExchangeError(
                f"{venue}: no marketable depth for {sym} at the top of book")
        notional = vwap * qty_f
    else:
        notional = (price_f or 0.0) * qty_f
    fee_rate, fee_note = _FEE_FN[venue](sym, transport, timeout)
    fee_usd = fee_rate * notional if fee_rate is not None else None
    th = thresholds()
    # Car 4 (D6) call site 2 — the order-plan guardrail. Computed OUTSIDE the
    # store lock (one decision request is network I/O) and applied inside as
    # a DOWNGRADE only: auto -> pending with the guard's reason, while a
    # pending plan keeps waiting for the owner either way.
    guard = _decision_guard({
        "venue": venue, "side": side, "symbol": sym,
        "order_type": order_type, "qty": qty_f, "price": price_f,
        "notional_usd": notional, "slippage_bps": slip,
        "depth_ok": bool(depth_ok), "fee_rate": fee_rate,
        "auto_thresholds_usd": th,
    })
    with _store_lock:
        path = _store_path(state_dir)
        store = _load_store(path)
        _sweep_expired(store)
        spent = _spent_today(store)
        auto, verdict = _auto_verdict(order_type, notional, spent, th)
        if guard.get("downgrade"):
            if auto:
                auto = False
                verdict = ("decision guardrail: " + guard["reason"] +
                           " — the plan waits for the owner (threshold "
                           f"verdict was: {verdict})")
            else:
                verdict = (verdict + " · decision guardrail: "
                           + guard["reason"])
        plan: dict = {
            "id": "pl_" + secrets.token_hex(6),
            "venue": venue, "side": side, "symbol": sym,
            "order_type": order_type, "qty": qty_f, "price": price_f,
            "notional_usd": notional,
            "vwap": vwap, "slippage_bps": slip, "depth_ok": bool(depth_ok),
            "fee_rate": fee_rate, "fee_usd": fee_usd, "fee_note": fee_note,
            "status": "approved" if auto else "pending",
            "approval": "auto-threshold" if auto else "none",
            "reason": verdict,
            "created_at": time.time(), "expires_at": time.time() + PLAN_TTL_SEC,
            "decided_at": time.time() if auto else None,
            "decided_by": "auto-threshold" if auto else None,
            "order_id": None, "executed_at": None, "attempts": 0,
        }
        _insert_plan(store, plan)
        _save_store(path, store)
        spent = _spent_today(store)
    fee_line = fee_note if fee_usd is None else f"{fee_note} (fee_usd={_fmt(fee_usd)})"
    lines = [
        f"plan {plan['id']} created — status={plan['status']} "
        f"(approval={plan['approval']})",
        f"venue={venue} side={side} symbol={sym} type={order_type} "
        f"qty={_fmt(qty_f)}" + (f" price={_fmt(price_f)}" if price_f is not None else ""),
        f"notional_usd={_fmt(notional)} vwap={_fmt(vwap) if vwap is not None else 'n/a'} "
        f"slippage_bps={f'{slip:.2f}' if slip is not None else 'n/a'} "
        f"depth_ok={'true' if depth_ok else 'false'}",
        f"fee: {fee_line}",
        f"thresholds: per_tx={_fmt(th['per_tx'])} daily={_fmt(th['daily'])} "
        f"spent_today_usd={_fmt(spent)}",
        f"reason: {verdict}",
    ]
    if plan["status"] == "approved":
        lines.append(f"next: run exchange_execute_order with "
                     f"plan_id={plan['id']} (auto-approved within thresholds)")
    else:
        lines.append("next: the OWNER approves or rejects this plan in the "
                     "console (Exchange view -> pending plan); then run "
                     f"exchange_execute_order with plan_id={plan['id']}")
    lines.append(f"plans stay valid for {PLAN_TTL_SEC}s; expiry makes this "
                 "plan unexecutable — prepare a new one")
    return "\n".join(lines)


def execute_order(plan_id: str, *, transport: Transport | None = None,
                  timeout: float = DEFAULT_TIMEOUT,
                  state_dir: str | Path | None = None) -> str:
    """W2 EXECUTE — accepts a plan id and NOTHING ELSE. Every order
    parameter comes from the stored plan. Fail-closed chain, in order:
    plan exists -> not expired -> approved (a pending plan tells you the
    OWNER must approve it in the console) -> auto approvals re-check the
    day's ledger -> only then is the order sent, while the store lock is
    held, so one plan can never be sent twice."""
    pid = str(plan_id or "").strip()
    transport = transport or _urllib_transport
    with _store_lock:
        path = _store_path(state_dir)
        store = _load_store(path)
        plan = store["plans"].get(pid)
        if not isinstance(plan, dict):
            raise ExchangeError(
                f"unknown plan_id {pid!r} (list plans with exchange_plans)")
        status = plan.get("status")
        if status in OPEN_STATUSES:
            expires = _maybe_float(plan.get("expires_at"))
            if expires is None or expires <= time.time():
                plan["status"] = "expired"
                plan["reason"] = (f"plan TTL elapsed ({PLAN_TTL_SEC}s); "
                                  "prepare a new plan")
                _save_store(path, store)
                raise ExchangeError(
                    f"plan expired (TTL {PLAN_TTL_SEC}s elapsed); prepare a new plan")
        if status == "pending":
            raise ExchangeError(
                "awaiting human approval; the OWNER approves this plan in the "
                "console (Exchange view -> pending plan -> approve)")
        if status != "approved":
            raise ExchangeError(f"plan is {status}; only an approved plan executes")
        if plan.get("approval") == "auto-threshold":
            th = thresholds()
            notional = _maybe_float(plan.get("notional_usd")) or 0.0
            auto, verdict = _auto_verdict(str(plan.get("order_type")), notional,
                                          _spent_today(store), th)
            if not auto:
                plan["status"] = "pending"
                plan["approval"] = "none"
                plan["reason"] = f"auto approval void at execution: {verdict}"
                _save_store(path, store)
                raise ExchangeError(
                    "auto approval no longer valid at execution (caps or day's "
                    f"ledger moved): {verdict}; the OWNER must approve it now")
        venue = str(plan.get("venue") or "")
        sender = _PLACE_FN.get(venue)
        if sender is None:
            raise ExchangeError(f"{venue} has no order endpoint wired")
        try:
            order_id = sender(plan, transport, timeout)
        except ExchangeError:
            plan["attempts"] = int(_maybe_float(plan.get("attempts")) or 0) + 1
            _save_store(path, store)
            raise
        now = time.time()
        plan["status"] = "executed"
        plan["order_id"] = str(order_id)
        plan["executed_at"] = now
        plan["attempts"] = int(_maybe_float(plan.get("attempts")) or 0) + 1
        if plan.get("decided_at") is None:
            plan["decided_at"] = now
            plan["decided_by"] = plan.get("decided_by") or "auto-threshold"
        day = _bj_day(now)
        entry = store["ledger"].get(day)
        if not isinstance(entry, dict):
            entry = {"spent_usd": 0.0, "count": 0}
        spent = (_maybe_float(entry.get("spent_usd")) or 0.0) + notional_of(plan)
        entry["spent_usd"] = spent
        entry["count"] = int(_maybe_float(entry.get("count")) or 0) + 1
        store["ledger"][day] = entry
        _save_store(path, store)
    return "\n".join([
        f"plan {pid} executed — order_id={plan['order_id']} venue={venue}",
        f"{plan['side']} {plan['order_type']} {plan['symbol']} "
        f"qty={_fmt(plan.get('qty'))}"
        + (f" price={_fmt(plan.get('price'))}" if plan.get("price") is not None else ""),
        f"notional_usd={_fmt(plan.get('notional_usd'))} sent to {venue} "
        f"(attempts={plan['attempts']})",
        f"ledger day={day} spent_usd={_fmt(spent)} count={entry['count']} "
        "— today's remaining auto budget is smaller now",
    ])


def notional_of(plan: dict) -> float:
    """Plan notional as a number (never a TypeError inside the ledger)."""
    return _maybe_float(plan.get("notional_usd")) or 0.0


def plan_decide(plan_id: str, action: str, *, decided_by: str = "console",
                state_dir: str | Path | None = None) -> dict:
    """CONSOLE-ONLY decision (approve | reject | cancel). Callers: the
    console write gate, which demands the Bearer WEB_TOKEN the model does
    not have. There is no tool wrapper for this function — the model cannot
    approve its own plan."""
    pid = str(plan_id or "").strip()
    action = str(action or "").strip().lower()
    if action not in PLAN_ACTIONS:
        raise ExchangeError(f"action must be one of {'|'.join(PLAN_ACTIONS)}")
    with _store_lock:
        path = _store_path(state_dir)
        store = _load_store(path)
        plan = store["plans"].get(pid)
        if not isinstance(plan, dict):
            raise ExchangeError(f"unknown plan_id {pid!r}")
        _sweep_expired(store)
        status = plan.get("status")
        if status not in OPEN_STATUSES:
            _save_store(path, store)
            raise ExchangeError(
                f"plan is {status}; a {action} decision is no longer possible")
        if action == "approve" and status != "pending":
            _save_store(path, store)
            raise ExchangeError("plan is already approved")
        actor = str(decided_by or "console")[:64]
        now = time.time()
        if action == "approve":
            plan.update(status="approved", approval="manual", decided_at=now,
                        decided_by=actor, reason="approved by the owner in the console")
        elif action == "reject":
            plan.update(status="rejected", decided_at=now, decided_by=actor,
                        reason="rejected in the console")
        else:
            plan.update(status="cancelled", decided_at=now, decided_by=actor,
                        reason="cancelled in the console")
        _save_store(path, store)
        return dict(plan)


def plans_list(*, state_dir: str | Path | None = None) -> list[dict]:
    """Read-only plan listing, newest first, with TTL expiry applied."""
    with _store_lock:
        path = _store_path(state_dir)
        store = _load_store(path)
        if _sweep_expired(store):
            _save_store(path, store)
        plans = [dict(p) for p in store["plans"].values() if isinstance(p, dict)]
    plans.sort(key=lambda p: _maybe_float(p.get("created_at")) or 0.0, reverse=True)
    return plans[:PLAN_MAX]


def plan_summary(*, state_dir: str | Path | None = None) -> dict:
    """Console-facing counts (no credentials, no venue data): how many plans
    wait for a human, how many are ready to execute, and today's spent USD."""
    with _store_lock:
        path = _store_path(state_dir)
        store = _load_store(path)
        if _sweep_expired(store):
            _save_store(path, store)
        plans = [p for p in store["plans"].values() if isinstance(p, dict)]
        pending = sum(1 for p in plans if p.get("status") == "pending")
        approved = sum(1 for p in plans if p.get("status") == "approved")
        spent = _spent_today(store)
    return {"pending_plans": pending, "approved_plans": approved,
            "spent_today_usd": spent, "ledger_day": _bj_day(),
            "plans_total": len(plans)}
