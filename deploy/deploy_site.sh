#!/usr/bin/env bash
# deploy_site.sh — publish site/ to Cloudflare Pages and keep aidog.im pointed
# at it. Idempotent maintainer op; safe to re-run any time.
#
#   bash deploy/deploy_site.sh          # deploy + ensure domains/DNS + verify
#   bash deploy/deploy_site.sh --check  # verify only (no deploy, no writes)
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
  # ---- 1) upload site/ -----------------------------------------------------
  command -v wrangler >/dev/null || die "wrangler not installed"
  wrangler pages deploy site/ --project-name "$PROJECT" --branch main
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
fail=0
for url in https://aidog.im/ https://www.aidog.im/; do
  code=$(curl -s -o /tmp/deploy_site_check.html -w '%{http_code}' --max-time 25 "$url" || echo 000)
  if [ "$code" = "200" ] && grep -q "<title>I'm AiDog" /tmp/deploy_site_check.html; then
    echo "DEPLOY_SITE: VERIFY OK $url (200, title I'm AiDog)"
  else
    echo "DEPLOY_SITE: VERIFY FAIL $url (http $code)" >&2
    fail=1
  fi
done
# assets reachable over the real domain
for a in assets/style.css assets/main.js i18n/en.json; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "https://aidog.im/$a" || echo 000)
  [ "$code" = "200" ] || { echo "DEPLOY_SITE: asset FAIL /$a (http $code)" >&2; fail=1; }
done
[ "$fail" -eq 0 ] && echo "DEPLOY_SITE: ALL VERIFIED"
exit "$fail"
