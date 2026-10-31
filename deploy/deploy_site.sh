#!/usr/bin/env bash
# deploy_site.sh — publish site/ to Cloudflare Pages and keep aidog.im pointed
# at it. Idempotent maintainer op; safe to re-run any time.
#
#   bash deploy/deploy_site.sh          # deploy + ensure domains/DNS + verify
#   bash deploy/deploy_site.sh --check  # verify only (no deploy, no writes)
#
# Also idempotently keeps the visit counter alive (docs/features/site_visibility.md):
# ensures KV namespace "aidog-site-visits" + VISITS binding on the Pages project
# before uploading, so site/functions/api/visits.js can increment on /api/visits.
#
# Credential slots (secrets.env, values never echoed):
#   AIDOG_CF_API_TOKEN   Pages edit + zone read (wrangler + Pages domains API)
#   AIDOG_CF_DNS_TOKEN   zone DNS read/write (record ensure)
#   AIDOG_CF_ACCOUNT_ID / AIDOG_CF_ZONE_ID
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT=aidog-site
TARGET=aidog-site.pages.dev
DOMAINS=("aidog.im" "www.aidog.im")
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

die() { echo "DEPLOY_SITE: $*" >&2; exit 1; }

# ---- credentials (subshell load; never print values) -----------------------
[ -f secrets.env ] || die "secrets.env missing"
set -a; . ./secrets.env; set +a
: "${AIDOG_CF_API_TOKEN:?AIDOG_CF_API_TOKEN missing}"
: "${AIDOG_CF_DNS_TOKEN:?AIDOG_CF_DNS_TOKEN missing}"
: "${AIDOG_CF_ACCOUNT_ID:?AIDOG_CF_ACCOUNT_ID missing}"
: "${AIDOG_CF_ZONE_ID:?AIDOG_CF_ZONE_ID missing}"
export CLOUDFLARE_API_TOKEN="$AIDOG_CF_API_TOKEN"
export CLOUDFLARE_ACCOUNT_ID="$AIDOG_CF_ACCOUNT_ID"
API="https://api.cloudflare.com/client/v4"

cfjson() { # cfjson METHOD URL [token] [body]
  local method=$1 url=$2 token=${3:-$AIDOG_CF_API_TOKEN} body=${4:-}
  if [ -n "$body" ]; then
    curl -s --noproxy '*' --max-time 20 -X "$method" \
      -H "Authorization: Bearer $token" -H "Content-Type: application/json" \
      -d "$body" "$url"
  else
    curl -s --noproxy '*' --max-time 20 -X "$method" \
      -H "Authorization: Bearer $token" "$url"
  fi
}

if [ "$CHECK_ONLY" -eq 0 ]; then
  # ---- 0) ensure KV counter namespace + Pages binding (idempotent) ---------
  # Namespace title fixed; binding = VISITS on both environments so the
  # Pages Function sees env.VISITS. Missing permission => WARN + continue:
  # /api/visits then honestly answers 503 and verify reports it as pending.
  KV_TITLE=aidog-site-visits
  kvid=$(cfjson GET "$API/accounts/$AIDOG_CF_ACCOUNT_ID/storage/kv/namespaces" \
    | python3 -c 'import json,sys
for ns in (json.load(sys.stdin).get("result") or []):
    if ns.get("title")=="aidog-site-visits": print(ns["id"]); break' 2>/dev/null || true)
  if [ -z "$kvid" ]; then
    resp=$(cfjson POST "$API/accounts/$AIDOG_CF_ACCOUNT_ID/storage/kv/namespaces" \
      "$AIDOG_CF_API_TOKEN" "{\"title\":\"$KV_TITLE\"}")
    kvid=$(printf '%s' "$resp" | python3 -c 'import json,sys
d=json.load(sys.stdin); print(d["result"]["id"] if d.get("success") else "")' 2>/dev/null || true)
    [ -n "$kvid" ] && echo "DEPLOY_SITE: KV namespace created ($KV_TITLE)"
  else
    echo "DEPLOY_SITE: KV namespace reused ($KV_TITLE)"
  fi
  if [ -z "$kvid" ]; then
    echo "DEPLOY_SITE: WARN KV namespace ensure failed — token likely lacks Workers KV Storage:Edit; /api/visits will stay pending" >&2
  else
    proj=$(cfjson GET "$API/accounts/$AIDOG_CF_ACCOUNT_ID/pages/projects/$PROJECT")
    need=$(printf '%s' "$proj" | python3 -c 'import json,sys
d=json.load(sys.stdin); kvid=sys.argv[1]
dc=(d.get("result") or {}).get("deployment_configs") or {}
ok=all((((dc.get(e) or {}).get("kv_namespaces") or {}).get("VISITS") or {}).get("namespace_id")==kvid for e in ("production","preview"))
print("no" if ok else "yes")' "$kvid" 2>/dev/null || echo unknown)
    if [ "$need" = "yes" ]; then
      body=$(printf '%s' "$proj" | python3 -c 'import json,sys
d=json.load(sys.stdin); kvid=sys.argv[1]
dc=(d.get("result") or {}).get("deployment_configs") or {}
for e in ("production","preview"):
    cfg=dc.setdefault(e, {})
    cfg["kv_namespaces"]={**(cfg.get("kv_namespaces") or {}), "VISITS": {"namespace_id": kvid}}
print(json.dumps({"deployment_configs": dc}))' "$kvid")
      out=$(cfjson PATCH "$API/accounts/$AIDOG_CF_ACCOUNT_ID/pages/projects/$PROJECT" \
        "$AIDOG_CF_API_TOKEN" "$body")
      ok=$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("success"))' 2>/dev/null || echo parse_error)
      echo "DEPLOY_SITE: KV binding VISITS -> $kvid ensure ($ok)"
      [ "$ok" = "True" ] || echo "DEPLOY_SITE: WARN binding patch result=$ok — /api/visits may stay pending" >&2
    else
      echo "DEPLOY_SITE: KV binding VISITS already -> $kvid"
    fi
  fi

  # ---- 1) upload site/ -----------------------------------------------------
  command -v wrangler >/dev/null || die "wrangler not installed"
  # Pages Functions are resolved as <cwd>/functions by `wrangler pages deploy`,
  # so run it from site/ where functions/api/visits.js lives (site/functions is
  # on wrangler's static-asset IGNORE_LIST, so the code is not uploaded twice).
  ( cd site && wrangler pages deploy . --project-name "$PROJECT" --branch main )
  echo "DEPLOY_SITE: site/ uploaded"

  # ---- 2) ensure Pages custom domains (idempotent) -------------------------
  for d in "${DOMAINS[@]}"; do
    out=$(cfjson POST "$API/accounts/$AIDOG_CF_ACCOUNT_ID/pages/projects/$PROJECT/domains" \
      "$AIDOG_CF_API_TOKEN" "{\"name\":\"$d\"}")
    ok=$(printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("success"))' 2>/dev/null || echo parse_error)
    echo "DEPLOY_SITE: domain ensure $d -> $ok (exists/created both fine)"
  done

  # ---- 3) ensure DNS CNAMEs (idempotent; only AIDOG_CF_DNS_TOKEN) ----------
  list=$(cfjson GET "$API/zones/$AIDOG_CF_ZONE_ID/dns_records?per_page=100" "$AIDOG_CF_DNS_TOKEN")
  printf '%s' "$list" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("success") else 1)' \
    || die "DNS read with AIDOG_CF_DNS_TOKEN failed"
  for d in "${DOMAINS[@]}"; do
    python3 - "$d" "$TARGET" <<'PY' || exit 1
import json, sys, subprocess, os
name, target = sys.argv[1], sys.argv[2]
zone = os.environ["AIDOG_CF_ZONE_ID"]; tok = os.environ["AIDOG_CF_DNS_TOKEN"]
def req(method, url, body=None):
    cmd = ["curl", "-s", "--noproxy", "*", "--max-time", "20", "-X", method,
           "-H", f"Authorization: Bearer {tok}", "-H", "Content-Type: application/json"]
    if body is not None:
        cmd += ["-d", json.dumps(body)]
    out = subprocess.run(cmd + [url], capture_output=True, text=True).stdout
    return json.loads(out)
base = f"https://api.cloudflare.com/client/v4/zones/{zone}/dns_records"
recs = req("GET", base + "?per_page=100")["result"]
same = [r for r in recs if r["name"] == name and r["type"] == "CNAME"
        and r["content"] == target and r.get("proxied")]
if same:
    print(f"DEPLOY_SITE: dns {name} already -> {target}")
    sys.exit(0)
for r in recs:
    if r["name"] == name and r["type"] in ("A", "AAAA", "CNAME"):
        d = req("DELETE", base + "/" + r["id"])
        print(f"DEPLOY_SITE: dns removed stale {r['type']} {name} -> {d.get('success')}")
d = req("POST", base, {"type": "CNAME", "name": name, "content": target,
                       "proxied": True, "ttl": 1})
print(f"DEPLOY_SITE: dns created {name} -> {target} -> {d.get('success')}")
sys.exit(0 if d.get("success") else 1)
PY
  done
fi

# ---- 4) verify public endpoints -------------------------------------------
# pget URL OUTFILE -> http status (always via direct connection, no proxy)
pget() { curl -s --noproxy '*' --max-time 25 -o "$2" -w '%{http_code}' "$1" 2>/dev/null || true; }
fail=0
for url in https://aidog.im/ https://www.aidog.im/; do
  code=$(pget "$url" /tmp/deploy_site_check.html)
  if [ "$code" = "200" ] && grep -q "<title>I'm AiDog" /tmp/deploy_site_check.html; then
    echo "DEPLOY_SITE: VERIFY OK $url (200, title I'm AiDog)"
  else
    echo "DEPLOY_SITE: VERIFY FAIL $url (http $code)" >&2
    fail=1
  fi
done
# assets reachable over the real domain
for a in assets/style.css assets/main.js i18n/en.json i18n/zh.json; do
  code=$(pget "https://aidog.im/$a" /tmp/deploy_site_asset)
  [ "$code" = "200" ] || { echo "DEPLOY_SITE: asset FAIL /$a (http $code)" >&2; fail=1; }
done

# --- site-pack parity: en/zh key sets must be identical (mirrors tmp/shoot_site.js)
code_en=$(pget https://aidog.im/i18n/en.json /tmp/deploy_site_en.json)
code_zh=$(pget https://aidog.im/i18n/zh.json /tmp/deploy_site_zh.json)
if [ "$code_en" = "200" ] && [ "$code_zh" = "200" ] && python3 - <<'PYPAR'
import json
en = json.load(open("/tmp/deploy_site_en.json"))
zh = json.load(open("/tmp/deploy_site_zh.json"))
only_en = sorted(set(en) - set(zh))
only_zh = sorted(set(zh) - set(en))
assert len(en) == len(zh) and not only_en and not only_zh, (only_en, only_zh)
print(len(en))
PYPAR
then
  echo "DEPLOY_SITE: VERIFY OK i18n parity (en/zh key sets identical)"
else
  echo "DEPLOY_SITE: VERIFY FAIL i18n parity (http en=$code_en zh=$code_zh or key-set drift)" >&2
  fail=1
fi

# --- AI-readable surface: llms.txt / robots.txt / sitemap / manifest / icons
llms_code=$(pget https://aidog.im/llms.txt /tmp/deploy_site_llms.txt)
if [ "$llms_code" = "200" ] && head -n 1 /tmp/deploy_site_llms.txt | grep -qx "# I'm AiDog"; then
  echo "DEPLOY_SITE: VERIFY OK /llms.txt (200, H1 '# I'm AiDog')"
else
  echo "DEPLOY_SITE: VERIFY FAIL /llms.txt (http $llms_code, H1 mismatch or missing)" >&2
  fail=1
fi
# --- llms.txt must carry the new capability/Web3/gate facts (capability car M1-M5)
if grep -q "## Product" /tmp/deploy_site_llms.txt \
   && grep -q "nine venues" /tmp/deploy_site_llms.txt \
   && grep -q "prepare → human console approval → execute" /tmp/deploy_site_llms.txt \
   && grep -q "ACTIVATION card" /tmp/deploy_site_llms.txt \
   && grep -q "1686/1686" /tmp/deploy_site_llms.txt \
   && grep -q "aidog.im/#capabilities" /tmp/deploy_site_llms.txt; then
  echo "DEPLOY_SITE: VERIFY OK /llms.txt facts (capabilities, web3 gate, activation, gates)"
else
  echo "DEPLOY_SITE: VERIFY FAIL /llms.txt facts (capability/Web3/gate lines missing)" >&2
  fail=1
fi
for a in robots.txt sitemap.xml manifest.webmanifest assets/icons/icon-512.png assets/icons/apple-touch-icon.png; do
  code=$(pget "https://aidog.im/$a" /tmp/deploy_site_asset)
  if [ "$code" = "200" ]; then
    echo "DEPLOY_SITE: VERIFY OK /$a (200)"
  else
    echo "DEPLOY_SITE: VERIFY FAIL /$a (http $code)" >&2
    fail=1
  fi
done
# manifest must parse as JSON
code=$(pget https://aidog.im/manifest.webmanifest /tmp/deploy_site_manifest.json)
if [ "$code" = "200" ]; then
  python3 -c 'import json,sys; d=json.load(open("/tmp/deploy_site_manifest.json")); assert d["name"] and d["icons"]' \
    || { echo "DEPLOY_SITE: VERIFY FAIL /manifest.webmanifest (invalid JSON)" >&2; fail=1; }
fi

# --- visit counter: 200 + increment, or honest 503 (pending, not a failure) -
v1=$(pget "https://aidog.im/api/visits" /tmp/deploy_site_v1.json)
v2=$(pget "https://aidog.im/api/visits" /tmp/deploy_site_v2.json)
if [ "$v1" = "200" ] && [ "$v2" = "200" ]; then
  if python3 - /tmp/deploy_site_v1.json /tmp/deploy_site_v2.json <<'PYV'
import json, sys
a = json.load(open(sys.argv[1])); b = json.load(open(sys.argv[2]))
assert isinstance(a.get("count"), int) and a.get("unit") == "page views", a
assert isinstance(b.get("count"), int) and b.get("unit") == "page views", b
assert b["count"] > a["count"], (a, b)
PYV
  then
    echo "DEPLOY_SITE: VERIFY OK /api/visits (200, count increments)"
  else
    echo "DEPLOY_SITE: VERIFY FAIL /api/visits (200 but shape/increment wrong)" >&2
    fail=1
  fi
  vt=$(curl -s --noproxy '*' --max-time 25 "https://aidog.im/api/visits?format=text" 2>/dev/null || true)
  case "$vt" in
    visits:*) echo "DEPLOY_SITE: VERIFY OK /api/visits?format=text (text/plain)" ;;
    *) echo "DEPLOY_SITE: VERIFY FAIL /api/visits?format=text (got: ${vt:0:40})" >&2; fail=1 ;;
  esac
elif [ "$v1" = "503" ] && python3 - /tmp/deploy_site_v1.json <<'PYV' 2>/dev/null
import json, sys
assert json.load(open(sys.argv[1])).get("error"), "503 without error field"
PYV
then
  echo "DEPLOY_SITE: PENDING /api/visits (503 counter not configured — KV binding not active; not a failure)"
else
  echo "DEPLOY_SITE: VERIFY FAIL /api/visits (http v1=$v1 v2=$v2)" >&2
  fail=1
fi

[ "$fail" -eq 0 ] && echo "DEPLOY_SITE: ALL VERIFIED"
exit "$fail"
