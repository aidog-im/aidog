<p align="center">
  <img width="140" src="site/assets/icons/icon-512.png" alt="I'm AiDog logo">
</p>

<h1 align="center">I'm AiDog</h1>

<p align="center">
  <strong>grown for one, built for anyone</strong><br>
  A personal embodied AI — a constitution for a personality, curated memory
  for a life, and a self-developed core that answers to you alone.<br>
  Not a platform. Not an assistant farm. One companion, grown for one person,
  engineered so anyone can run it.
</p>

<p align="center">
  <a href="https://github.com/aidog-im/aidog/releases"><img src="https://img.shields.io/github/v/release/aidog-im/aidog?display_name=tag&sort=semver&style=flat-square&label=release" alt="latest release"></a>
  <a href="https://aidog.im"><img src="https://img.shields.io/badge/website-aidog.im-2892ff?style=flat-square" alt="website"></a>
  <img src="https://img.shields.io/badge/core_tests-1160%2F1160-brightgreen?style=flat-square" alt="core tests 1160/1160">
  <img src="https://img.shields.io/badge/console_smoke-167%2F167-brightgreen?style=flat-square" alt="console smoke 167/167">
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
  stop reasons, **27 modules ≈ 13.3k LOC, 1,160 tests**, and zero upstream
  dependencies after the core swap. Swap the kernel, never swap "me".

It talks to you over **Telegram**, a **loopback web console**, a
**desktop HUD shell**, and **voice** — with a 9-route channel gateway,
skills with a deterministic pre-router, natural-language scheduling,
subagents, MCP, and an institutional self-evolution loop (`/learn`,
`/reflect`, lint-gated writes) whose maturity grows with use.

Everything is **free by decree**: no subscription, no telemetry, no paid
feature gate. You bring a model key (or use the zero-key trial path) and the
whole product activates.

## Highlights

|  | Capability | What it provides |
|---|---|---|
| 🧬 | **Self-developed core** | `mycore`: FC loop with honest stop reasons, fail-closed service, watchdog, doctor health gate — 27 modules, 1,087 tests, zero upstream |
| 🎭 | **Constitution persona** | `SOUL.md` first in the system stack; red lines and tone enforced every reply, not sampled from a persona prompt |
| 🧠 | **Curated memory** | Bounded Markdown budgets + archive tiers + digital-memory protocol; `memory` suite 58/58 |
| 🧩 | **Skills + deterministic router** | `SKILL.md` frontmatter (industry-standard fields), route pre-filter picks TOP3 *before* any body loads — 30/30 |
| ⏰ | **Natural-language cron** | `/cron ship the report tomorrow 9am` → parsed, validated, scheduled — fail-closed until you enable it |
| 🤝 | **Subagents & execute_code** | ≤3 isolated parallel delegates with fresh context; ≤8-step folded code runs with checkpointed writes |
| 🔌 | **MCP client** | stdio + HTTP servers, tool allow/deny filtering, credential slots, progress streaming |
| 📡 | **9-route gateway** | Telegram (live) + Discord / Slack / Email / Webhook / DingTalk / Feishu / WeChat / WhatsApp / Signal adapters with per-route honest status |
| 🔑 | **Zero-key onboarding** | Start with a trial model or bring your own key; secrets live in `secrets.env` (0600) and are **never echoed** — slot names only |
| 💬 | **Streaming everywhere** | Provider SSE streaming, Telegram typing heartbeat, console chat cards with streamed replies |
| 🖥️ | **Loopback web console** | Status · Settings · Usage & cost · Events · Voice panels — bound to `127.0.0.1` by default, token-gated writes |
| 🖼️ | **Media built in** | Local STT voice sessions with fuzzy wake phrases, TTS announcements, BYOK image generation, Telegram photo send |
| 🖥️ | **Desktop HUD shell** | Electron frameless shell: transparency slider, always-on-top, tray — Linux packaging verified (Win/macOS not built yet, honestly) |
| 👥 | **Multi-profile isolation** | `AIDOG_PROFILE` selects fully isolated profiles (settings / secrets / state / body) — 63/63 isolation gate + 18-leg real-machine E2E |
| 🩺 | **Self-healing ops** | `aidog doctor` health checks, restart watchdog, one-command upgrades with a dirty-tree guard |
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
│   checkpoint · modeltier · channels(9) · stt/imagegen · browser     │
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

Walk the console panels (Status → Settings round-trip → Usage → Events →
chat card), send a Telegram `/help`, and confirm you never see a raw secret
anywhere. The complete checklist — including a fresh-machine install path —
lives in **[`docs/manual_verification.md`](docs/manual_verification.md)**.

## Quality Gates

Every release is cut only after these run green on a clean tree:

| Suite | Result |
|---|---|
| `mycore` core suite | **1160/1160** |
| Console smoke (live HTTP) | **167/167** |
| Skill router | **30/30** |
| Memory persistence | **58/58** |
| Setup wizard | **99/99** |
| Profile isolation (offline, subprocess cross-check) | **63/63** |
| Desktop shell (`node --test`) | **15/15** |
| `aidog doctor` | **0 FAIL · 0 WARN** (honest environment WARN only when egress is fully gone) |
| Publish gate (deny-by-default manifest) | **READY** |
| Real-machine E2E | profiles **18/18** · channels **8/8** · domestic channels **44/44** · pipeline **35/35** · web3 read-only **33/33** · web3 approval **61/61** |

Locale packs: `en` (default) + `zh`, **375 keys** each, parity lint in CI;
the marketing site ships its own **165-key** bilingual pack.

## Documentation

- [`docs/installation.md`](docs/installation.md) — install, slots, wizard
- [`docs/features/`](docs/features/) — **30 capability documents**, one per
  feature (channels, memory, cron, subagents, MCP, voice, desktop,
  profiles, tool toggles, …), each with its honest boundaries
- [`docs/manual_verification.md`](docs/manual_verification.md) — manual
  acceptance checklist
- [`docs/security_checklist.md`](docs/security_checklist.md) — security
  review list
- **[aidog.im](https://aidog.im)** — product site (bilingual, PWA,
  AI-readable `llms.txt`)

Design history and third-party research notes stay in the private tree by
policy; they are not exported.

## Roadmap

- **Now** — parity hardening and polish under owner acceptance; every
  capability ships with tests and an honest boundary note.
- **Next** — a **Web3 agent-control substrate** (payments-first HTTP
  exploration, on-chain agent identity), researched and staged — execution
  starts only on the owner's explicit go order.

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
