<p align="center">
  <img width="140" src="site/assets/icons/icon-512.png" alt="I'm AiDog logo">
</p>

<h1 align="center">I'm AiDog</h1>

<p align="center">
  <em>Your loyal AI companion</em><br>
  <strong>grown for one, built for anyone</strong><br>
  A personal embodied AI — a constitution for a personality, curated memory
  for a life, and a self-developed core that answers to you alone.<br>
  Not a platform. Not an assistant farm. One companion, grown for one person,
  engineered so anyone can run it.
</p>

<p align="center">
  <a href="https://github.com/aidog-im/aidog/releases"><img src="https://img.shields.io/badge/release-0.5.48-blue?style=flat-square" alt="release 0.5.48"></a>
  <a href="https://aidog.im"><img src="https://img.shields.io/badge/website-aidog.im-2892ff?style=flat-square" alt="website"></a>
  <img src="https://img.shields.io/badge/core_tests-1477%2F1477-brightgreen?style=flat-square" alt="core tests 1477/1477">
  <img src="https://img.shields.io/badge/console_smoke-234%2F234-brightgreen?style=flat-square" alt="console smoke 234/234">
  <img src="https://img.shields.io/badge/permanently-free-ff6f00?style=flat-square" alt="permanently free">
</p>

<p align="center">
  <a href="#about">About</a> ·
  <a href="#highlights">Highlights</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#verify">Verify</a> ·
  <a href="#quality-gates">Quality Gates</a> ·
  <a href="#documentation">Documentation</a> ·
  <a href="#roadmap">Roadmap</a> ·
  <a href="#versioning--updates">Versioning</a> ·
  <a href="#security-posture-honest">Security</a> ·
  <a href="#license--pricing-owner-decree">License</a> ·
  <a href="README_cn.md">简体中文</a>
</p>

> 🔒 **Private repository** during the maturity phase — whether and when it
> becomes public is the owner's case-by-case decision.
> 🟢 **Permanently free — forever, no exceptions.** No paid tier, no usage
> fees, no feature locked behind a payment. Ever.

## About

I'm AiDog is a **personal embodied AI companion** that runs on your own
machine. It is built around three ideas that most assistants skip:

- **A constitution for a personality.** `SOUL.md` sits at the top of every
  reply's context. Red lines, tone, and operating principles are not
  prompt-afterthoughts — they are re-applied on every single turn.
- **Curated memory for a life.** Memory lives in plain Markdown with hard
  budgets (`MEMORY.md`, `USER.md`, archives) and a digital-memory protocol:
  one source of truth, cross-kernel recoverable, write-back on every reflect.
  No vector store you cannot read or audit.
- **A self-developed core.** The runtime kernel (`mycore`) is written from
  scratch in this repository — an honest function-calling loop with explicit
  stop reasons, **29 modules ≈ 15.3k LOC, 1,477 tests**, and zero upstream
  dependencies after the core swap. Swap the kernel, never swap "me".

It talks to you over **Telegram**, a **loopback web console**, a
**desktop HUD shell**, and **voice** — with a 9-route channel gateway,
skills with a deterministic pre-router, natural-language scheduling,
subagents, MCP, and an institutional self-evolution loop (`/learn`,
`/reflect`, lint-gated writes) whose maturity grows with use.

**Web3, gated by a human.** Since v0.5.38 nine venues live in one catalog
behind six `exchange_*` tools — public market data needs no credential,
account and order tools are slot-gated (no slot → tool does not register).
Every order runs `prepare` (dry-run plan) → the owner approves it in the
console → `execute(plan_id)`; caps default to `0`, market orders never
auto-approve, and withdrawals / on-chain transfers do not exist in the
code at all.

Everything is **free by decree**: no subscription, no paid feature gate,
and **no telemetry in the product runtime** — the only counting anywhere is
the marketing site's public visit counter, documented as such. You bring a
model key (or use the zero-key trial path) and the whole product activates.

## Highlights

|  | Capability | What it provides |
|---|---|---|
| 🧬 | **Self-developed core** | `mycore`: FC loop with honest stop reasons, fail-closed service, watchdog, doctor health gate — 29 modules, 1,477 tests, zero upstream |
| 🎭 | **Constitution persona** | `SOUL.md` first in the system stack; red lines and tone enforced every reply, not sampled from a persona prompt |
| 🧠 | **Curated memory** | Bounded Markdown budgets + archive tiers + digital-memory protocol; `memory` suite 58/58 |
| 🧩 | **Skills + deterministic router** | `SKILL.md` frontmatter (industry-standard fields), route pre-filter picks TOP3 *before* any body loads — 30/30 |
| ⏰ | **Natural-language cron** | `/cron ship the report tomorrow 9am` → parsed, validated, scheduled — fail-closed until you enable it |
| 🤝 | **Subagents & execute_code** | ≤3 isolated parallel delegates with fresh context; ≤8-step folded code runs with checkpointed writes |
| 🔌 | **MCP client** | stdio + HTTP servers, tool allow/deny filtering, credential slots, progress streaming |
| 🔗 | **Web3 exchange, approval gate** | **9 venues** in one catalog behind **six `exchange_*` tools** — market data keyless, account/order tools slot-gated (no slot → tool not registered); `prepare` (dry-run plan) → owner approves in the console → `execute(plan_id)` — caps default `0`, market orders **never** auto-approve, withdrawals & on-chain transfers **absent by construction** |
| 📡 | **Channel gateway** | **9 adapter routes + live Telegram** — Discord / Slack / Email / Webhook / DingTalk / Feishu / WeChat / WhatsApp / Signal adapters with per-route honest status |
| 🔑 | **Zero-key onboarding** | Start with a trial model or bring your own key; secrets live in `secrets.env` (0600) and are **never echoed** — slot names only |
| 💬 | **Streaming everywhere** | Provider SSE streaming, Telegram typing heartbeat, console chat cards with streamed replies |
| ⚡ | **Local & portable** | stdlib-only core (voice/image extras lazy-load), service bound to local loopback, SSE streaming throughout, `execute_code` folds multi-step runs into one round trip |
| 🖥️ | **Loopback web console** | Seven views — Overview · Chat · Skills · Exchange · Evolution · Stream · Settings (Usage & cost and Voice are cards): activation checklist with fix deep-links, credential slot editor (32 slots — names + lengths only, values never rendered), cron list with delete, capability grid, channel forms with send test, hooks editor, evolution schedule, Web3 caps — bound to `127.0.0.1`, token-gated writes |
| 🖼️ | **Media built in** | Local STT voice sessions with fuzzy wake phrases, TTS announcements, BYOK image generation, Telegram photo send |
| 🖥️ | **Desktop HUD shell** | Electron frameless shell: transparency slider, always-on-top, tray — Linux packaging verified (Win/macOS not built yet, honestly) |
| 👥 | **Multi-profile isolation** | `AIDOG_PROFILE` selects fully isolated profiles (settings / secrets / state / body) — 63/63 isolation gate + 18-leg real-machine E2E |
| 🩺 | **Self-healing ops** | `aidog doctor` health checks + a one-click **Run doctor** button in the console, restart watchdog, one-command upgrades with a dirty-tree guard, plus in-console **restart / update check / update apply** controls |
| 🆓 | **Permanently free** | Owner decree: zero paid touchpoints in product, docs, or packages — introducing one is treated as a defect |

## Architecture

```text
┌───────────────────────────────────────────────────────────────────┐
│  Entry points                                                     │
│   Telegram · Web console (127.0.0.1) · Desktop HUD · Voice · CLI  │
└──────────────────────────────┬────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────┐
│  Kernel layer — mycore (self-developed, replaceable, zero upstream)│
│   loop · tools · memory · compact · scheduler · subagents · mcp    │
│   checkpoint · modeltier · channels(9) · exchanges                 │
│   stt/imagegen · browser                                           │
└──────────────────────────────┬────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────┐
│  Body layer — overlay/ (owned by this repo, kernel-independent)    │
│   constitution (SOUL) · memory slots · skills · hooks · locales    │
└──────────────────────────────┬────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────┐
│  Business layer — adapters/ · web/ · desktop/ · deploy/            │
│   model provider (BYOK) · channel credentials · console · shells   │
└───────────────────────────────────────────────────────────────────┘
```

Three-layer separation is the whole point: **swap the core, never swap
"me". The kernel is replaceable, the body layer and business assets are ours.

## Quick Start

> 🔒 **Private repo:** `git clone` needs a PAT or SSH key with repo access —
> beware a machine-wide `credential.helper` hijacking GitHub auth. See
> [`docs/installation.md`](docs/installation.md).

```bash
git clone https://github.com/aidog-im/aidog.git aidog && cd aidog  # private repo today
bash deploy/install.sh        # one command: provision + boot + verify
aidog setup                   # guided wizard: fills the slots it lists
```

`deploy/install.sh` is idempotent (safe to re-run) and reports exactly what
is missing — **by slot name, never by value**:

| Exit code | Meaning |
|---|---|
| `0` | Fully ready — doctor clean, service provisioned, console healthy |
| `2` | Installed, but credential / model slots still pending (fail-closed by design) |
| `1` | Hard failure (missing prerequisite, port conflict, boot failure) |

`aidog setup` is the interactive follow-up: it prompts only for slots that
are still empty, hides input, validates every write, and never echoes a
value. Flags: `--check` (read-only report), `--yes` (non-interactive),
`--no-boot` (provision without starting services).

Then:

1. Open the console at **http://127.0.0.1:8790**
2. Settings panel → add your model endpoint + key, Telegram bot token +
   allowlist (or run `aidog setup` instead)
3. Chat card → send one message — a streamed reply means the model chain is
   live
4. Telegram → message your bot `/help` from an allowlisted account

Full guide: [`docs/installation.md`](docs/installation.md).

## Verify

Automated E2E covers the stack on this machine; a **manual spot-check** is
the owner's acceptance layer. Short version:

```bash
python3 deploy/aidog_doctor.py       # health gate: 9 checks · 0 FAIL · 20 SKIP
bash deploy/boot_services.sh status  # console + service + watchdog green
bash deploy/run_service.sh --check   # readiness rc=0
python3 deploy/aidog_update.py check # version channel: up to date
```

Walk the console views (Overview → Settings round-trip → chat card →
Stream → Exchange → Evolution), send a Telegram `/help`, and confirm you
never see a raw secret anywhere. The complete checklist — including a
fresh-machine install path — lives in
**[`docs/manual_verification.md`](docs/manual_verification.md)**.

## Quality Gates

Every release is cut only after these run green on a clean tree:

| Suite | Result |
|---|---|
| `mycore` core suite | **1477/1477** |
| Console smoke (live HTTP) | **234/234** |
| Skill router | **30/30** |
| Memory persistence | **58/58** |
| Setup wizard | **99/99** |
| Profile isolation (offline, subprocess cross-check) | **63/63** |
| Desktop shell (`node --test`) | **15/15** |
| `aidog doctor` | **0 FAIL · 0 WARN** (honest environment WARN only when egress is fully gone) |
| Publish gate (deny-by-default manifest) | **READY** |
| Real-machine E2E | profiles **18/18** · channels **8/8** · domestic channels **44/44** · pipeline **35/35** · web3 read-only **33/33** · web3 approval **61/61** · frontend coverage **62/62** · frontend UI **33/33** · activation **81/81** · activation UI **50/50** · settings matrix **63/63** + UI **20/20** · missions **54/54** · mission-orch **63/63** · mission-pause **73/73** · skills catalog **176/176** · skills remote **58/58** |

Catalog scope: **25 registered tools** · **9 exchange venues** · **17
real-machine E2E legs** — all green.

Locale packs: `en` (default) + `zh`, **658 keys** each, parity lint in CI;
the marketing site ships its own **257-key** bilingual pack.

## Documentation

- [`docs/installation.md`](docs/installation.md) — install, slots, wizard
- [`docs/features/`](docs/features/) — **33 capability documents**, one per
  feature (channels, memory, cron, subagents, MCP, voice, desktop,
  profiles, tool toggles, …), each with its honest boundaries
- [`docs/manual_verification.md`](docs/manual_verification.md) — manual
  acceptance checklist
- [`docs/security_checklist.md`](docs/security_checklist.md) — security
  review list
- **[aidog.im](https://aidog.im)** — product site (bilingual, PWA,
  AI-readable `llms.txt`)

**All 33 at a glance** (grouped, one line per group):

- **Agent loop & tools** — [`acp`](docs/features/acp.md) (ACP v1 stdio server) · [`openai_api_server`](docs/features/openai_api_server.md) (OpenAI-compatible `/v1` gateway) · [`terminal_backends`](docs/features/terminal_backends.md) (7-backend `run_bash` matrix) · [`execute_code`](docs/features/execute_code.md) · [`subagents`](docs/features/subagents.md) · [`mcp`](docs/features/mcp.md) · [`tool_toggles`](docs/features/tool_toggles.md) · [`browser_automation`](docs/features/browser_automation.md) · [`model_failover`](docs/features/model_failover.md) · [`missions`](docs/features/missions.md) (never-sleeps mission loop)
- **Knowledge & memory** — [`memory_and_routing`](docs/features/memory_and_routing.md) · [`checkpoints`](docs/features/checkpoints.md) · [`context_files`](docs/features/context_files.md) (project instruction files) · [`evolution_loop`](docs/features/evolution_loop.md) · [`connectors`](docs/features/connectors.md) (read-only Linear/Notion search)
- **Channels & media** — [`channels`](docs/features/channels.md) · [`channels_domestic`](docs/features/channels_domestic.md) · [`console_chat`](docs/features/console_chat.md) · [`voice_session`](docs/features/voice_session.md) · [`voice_announcements`](docs/features/voice_announcements.md) · [`tts_engines`](docs/features/tts_engines.md) · [`image_generation`](docs/features/image_generation.md)
- **Ops & console** — [`setup_wizard`](docs/features/setup_wizard.md) · [`frontend_coverage`](docs/features/frontend_coverage.md) · [`capabilities`](docs/features/capabilities.md) · [`desktop_client`](docs/features/desktop_client.md) · [`profiles`](docs/features/profiles.md) · [`site_visibility`](docs/features/site_visibility.md) · [`zero_key_onboarding`](docs/features/zero_key_onboarding.md) · [`natural_language_cron`](docs/features/natural_language_cron.md) · [`event_hooks`](docs/features/event_hooks.md)
- **Web3** — [`exchanges`](docs/features/exchanges.md) (nine venues, orders behind a human approval gate)
- **Skills ecosystem** — [`skills_ecosystem`](docs/features/skills_ecosystem.md) (official directory, private skills repo, dual-gate review, remote catalog API)

Design history and third-party research notes stay in the private tree by
policy; they are not exported.

## Roadmap

- **Now** — Web3 phase-2 is live: **W1 + W2 shipped in v0.5.38** (nine-venue
  catalog, `prepare` → owner approval → `execute`); parity hardening and
  polish continue under owner acceptance — every capability ships with tests
  and an honest boundary note.
- **Next** — the remaining Web3 stage (identity & payments: ERC-8004 on-chain
  agent identity, x402 read-only paid calls, official MCP opt-in mounts),
  researched and staged — execution starts only on the owner's explicit go
  order.

## Versioning & Updates

- Version truth = `VERSION` (semver) + `release.json` (date + notes);
  both bump together, every release is tagged `vX.Y.Z` and published as a
  **GitHub Release** (the discovery channel).
- Upgrade: `python3 deploy/aidog_update.py apply` → dirty-tree guard →
  fetch → `--ff-only` → restart runtime. The console shows an upgrade
  banner automatically when a newer release exists.

## Security Posture (honest)

- Loopback by default; console writes are bearer-token gated; credentials
  live only in `secrets.env` (0600) and are **never echoed** — slot names
  only, in logs, status, and errors.
- Fail-closed everywhere it matters: channel allowlists, profile names,
  settings validation, upgrade guard.
- Single-owner design = strangers are not part of the attack surface.
- **Honest limits:** private repository, no third-party audit and no CVE
  disclosure channel *yet* — pre-public stage. We claim a small attack
  surface by design, never "audited".

## Language / i18n

- Source code, comments, and markdown docs: **English** (international
  route). This file has a full Chinese mirror: [`README_cn.md`](README_cn.md).
- UI strings: locale packs under `overlay/locales/` — default **`en`**,
  switchable **`zh`**, new languages added as packs with zero code changes.

## License & Pricing (owner decree)

**Free forever — for any use.** I'm AiDog ships with **zero paid
touchpoints**: no paid tier, no in-app purchases, no usage fees, no feature
locked behind payment. Adding any charge to this product violates the owner
decree and is treated as a defect.
