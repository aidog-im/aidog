"""mycore.exchanges — Web3 phase 2, W1 car: READ-ONLY exchange & chain data.

Three hard contracts (owner decrees, 2026-10-24) that this file enforces in
code rather than in configuration:

1. READ-ONLY. Every request here fetches data. There is no order placement,
   no transfer, no withdrawal, no transaction signing anywhere in this
   module — the prepare/execute split of phase 2 is a boundary drawn in
   CODE, so no settings value, env var or model instruction can turn this
   file into a writer. Order placement lands in W2 behind an approval gate.
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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
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
)}

CEX_VENUES = tuple(k for k, v in VENUES.items() if v.kind == "cex")

# Base URL per venue (env AIDOG_EX_<VENUE>_URL overrides — offline E2E seam).
_BASE = {
    "okx": "https://www.okx.com",
    "kraken": "https://api.kraken.com",
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
        row: dict = {"key": key, "label": v.label, "kind": v.kind,
                     "auth": v.auth, "read_only": True,
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


def _okx_private(path_query: str, *, transport: Transport,
                 timeout: float) -> object:
    key = _slot("OKX_API_KEY")
    secret = _slot("OKX_API_SECRET")
    passphrase = _slot("OKX_API_PASSPHRASE")
    if not (key and secret and passphrase):
        raise ExchangeError(
            "okx not configured (set OKX_API_KEY / OKX_API_SECRET / "
            "OKX_API_PASSPHRASE in secrets.env)")
    ts = _okx_timestamp()
    sign = okx_sign(ts, "GET", path_query, "", secret)
    headers = {"OK-ACCESS-KEY": key, "OK-ACCESS-SIGN": sign,
               "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": passphrase}
    doc = _get(_base("okx") + path_query, transport=transport, timeout=timeout,
               headers=headers)
    if isinstance(doc, dict) and str(doc.get("code", "0")) != "0":
        raise ExchangeError(f"okx: {_err(doc)}")
    return doc


def _kraken_private(path: str, *, transport: Transport,
                    timeout: float) -> object:
    """POST <base><path> with a nonce body; path starts at /0/private (the
    signed prefix per the official authentication guide)."""
    key = _slot("KRAKEN_API_KEY")
    secret = _slot("KRAKEN_API_SECRET")
    if not (key and secret):
        raise ExchangeError(
            "kraken not configured (set KRAKEN_API_KEY / KRAKEN_API_SECRET "
            "in secrets.env)")
    nonce = _nonce()
    postdata = urllib.parse.urlencode({"nonce": nonce})
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
    pair = "".join(ch for ch in sym.upper().replace("BTC", "XBT") if ch.isalnum())
    if not pair:
        raise ExchangeError(f"kraken: no tradable pair in {sym}")
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


_MARKET_FN = {
    "okx": _mkt_okx, "kraken": _mkt_kraken, "jupiter": _mkt_jupiter,
    "velora": _mkt_velora, "hyperliquid": _mkt_hyperliquid,
    "dydx": _mkt_dydx, "gmx": _mkt_gmx, "rpc": _mkt_rpc,
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
    fn = _okx_portfolio if venue == "okx" else _kraken_portfolio
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
    fn = _okx_orders if venue == "okx" else _kraken_orders
    return "\n".join(fn(transport, timeout))


def quote(venue: str, symbol: str | None = None, *, timeout: float = 10.0) -> str:
    """Console-facing market probe: same read-only path as ``markets`` with
    a short timeout and an injectable-transport-free signature (the HTTP
    surface never exposes account reads — those are model tools only)."""
    return markets(venue, symbol, timeout=timeout)
