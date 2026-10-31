# Manual Verification Guide (owner spot-check)

> Purpose: a human-readable acceptance pass that complements the automated
> E2E suites. Run it after a fresh install or when you want to confirm the
> product is actually usable end to end. Every step states the expected
> result; anything else is a defect worth filing.
>
> Time budget: ~15 minutes for the core path (§1–§6).

## 0. What automated E2E already covers (so you know what to skip)

The repo's E2E scripts run against the **local checkout** with real
processes — they do **not** simulate a fresh GitHub download. Covered:

| Script | Legs | Proves |
|---|---|---|
| `tmp/e2e_car_f.py` | 18 | profile isolation on a live console (byte-identical default layer) |
| `tmp/e2e_channels.py` | 12 | webhook/Email wire-level outbound + inbound, bearer gates |
| `tmp/e2e_channels_dom.py` | 46 | DingTalk/Feishu/WeChat request shapes, HMAC, polling |
| `tmp/e2e_car4a.py` | 39 | OpenAI-compatible API, hooks, MCP, capability catalog |

Suite gates (run on a clean tree before every release): see the **Quality
Gates** table in the root README. This document is the **human layer on
top**: install path, console walk-through, chat, Telegram, upgrade.

## 1. Fresh-machine install (the GitHub download path)

Prerequisites: Linux with `python3` ≥ 3.10, `tmux`, `git`; free port 8790.

```bash
git clone https://github.com/aidog-im/aidog.git aidog && cd aidog
bash deploy/install.sh
```

Expected: preflight → provision (only missing files are created; existing
values are never overwritten) → `aidog` on PATH → boot → verification
report. Exit codes:

| rc | Meaning | What you do |
|---|---|---|
| `0` | fully ready (doctor clean, console healthy) | go to §2 |
| `2` | installed; credential/model slots pending | run §1.1 wizard, re-run `install.sh` |
| `1` | hard failure | read the failing stage in the report; fix prerequisite (port/python/tmux) and re-run — the script is idempotent |

Nothing in the output may ever show a secret **value** — slot names only.
Seeing a raw token anywhere = defect.

### 1.1 Guided configuration

```bash
aidog setup
```

Expected: it prompts **only for slots that are still empty** (hidden input,
every write read back and validated). Minimum to become usable:

| Slot | Where to get it | Fills |
|---|---|---|
| bot token | Telegram @BotFather | chat channel |
| allowlist chat id | @userinfobot on your account | who the bot obeys (fail-closed) |
| model endpoint + key | any OpenAI-compatible provider | the brain |

Flags: `--check` = read-only status report (exit 0/1/2 = ready/pending/error),
`--yes` = non-interactive, `--no-boot` = provision without starting.

Re-run `bash deploy/install.sh` afterwards — target rc=`0`.

## 2. Health gates (30 seconds)

```bash
python3 deploy/aidog_doctor.py        # expect: 9 checks · 0 FAIL · 0 WARN*
bash deploy/boot_services.sh status   # expect: console + service + watchdog green
bash deploy/run_service.sh --check    # expect: rc=0
```

\* When host egress is fully down, doctor honestly reports one environment
WARN (`telegram.api=pending`) — that is by design, never a fake PASS.

## 3. Console walk-through (browser)

Open **http://127.0.0.1:8790** (loopback only — a remote machine must fail
to connect; that is part of the test).

| # | Step | Expected |
|---|---|---|
| 1 | Status panel | services green, model name shown, version = `VERSION` file |
| 2 | Settings → change language `en`→`zh`, save, refresh | value persists, UI switches back; secrets section shows **slot names + filled/empty state only** |
| 3 | Settings → type a wrong-typed value (e.g. a string where a number is required) | rejected with a precise field error, nothing written |
| 4 | Usage & cost panel | usage aggregates load (metrics URL configured) or an honest empty state — never fabricated numbers |
| 5 | Events panel | recent events listed; empty = honest empty |
| 6 | CHAT card → send one message | **streamed** reply arrives = model chain live |
| 7 | DevTools console | no JS errors on any view |

If you changed the port or profile, use the URL you provisioned; the
console shows the active profile name in the header when `AIDOG_PROFILE`
is set.

## 4. Telegram round-trip

1. From your **allowlisted** account send `/help` → command list replies.
2. Send a plain question → streamed/normal reply arrives.
3. Send `/status` → runtime status summary.
4. From a **non-allowlisted** account (second account or a group the bot can
   see) send anything → **no response at all** (allowlist fail-closed).
5. Optional voice: send a voice note → bot replies with the transcription
   result path (requires STT model present; absent = honest failure note).

## 5. Channel smoke (optional, needs channel credentials)

```bash
# console route table — every route carries honest status text
curl -s http://127.0.0.1:8790/api/channels | python3 -m json.tool
# capability catalog — names/counts only, never secrets
curl -s http://127.0.0.1:8790/api/capabilities | python3 -m json.tool
```

Expected: 9 route rows; unconfigured routes say *why* (missing slot / not
enabled) without echoing values; `whatsapp` marks inbound as structural;
`signal` marks inbound as best-effort local daemon.

## 6. Upgrade path

```bash
python3 deploy/aidog_update.py check    # expect: up to date: <ver> (origin: <ver>)
```

With a newer release published and a **clean** worktree:

```bash
python3 deploy/aidog_update.py apply    # fetch → --ff-only → service restart
```

Expected: refuses to run with uncommitted changes (dirty-worktree guard) —
verify that too: edit any tracked file, re-run, expect a clear refusal.

## 7. Desktop shell (optional)

```bash
cd desktop && npm test               # expect: 15/15 node --test
npx electron . --selftest --no-sandbox   # expect: single SELFTEST JSON, rc=0
```

Linux packaging was verified via `electron-builder --linux dir tar.gz`;
Win/macOS builds are honestly **not built yet**.

## 8. Failure triage

| Symptom | Likely cause | Fix |
|---|---|---|
| `install.sh` rc=2 forever | slots still empty | `aidog setup`, fill the named slots |
| console not opening | port busy / service down | `boot_services.sh status`, free port 8790 |
| doctor WARN `telegram.api=pending` | host egress down | environment issue; known-honest state |
| chat card no reply | model endpoint/key invalid | Settings → model section; check slot names |
| settings not applied after locale change | console process caches locale packs | restart web session: `tmux kill-session -t aidog_web && bash deploy/boot_services.sh start` |
| `aidog update apply` refuses | dirty worktree by design | commit/stash first, or decide consciously |

## 9. Red lines to watch while testing

1. **Zero secret echo** — a raw token/key in any output, log, status, or
   error is a defect.
2. **Zero paid touchpoints** — any paywall, tier, or usage fee is a defect
   (owner decree).
3. **Fail-closed defaults** — allowlists, profile names, settings validation
   must reject rather than guess.
4. **Honest degradation** — missing credentials report *pending by name*,
   never fake success.
