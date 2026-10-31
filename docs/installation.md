# Installation — I'm AiDog

One command provisions everything the repository cannot ship (credentials and
machine-local config are gitignored on purpose), boots the runtime, and tells
you — by **name**, never by value — exactly what still needs a human.

## Prerequisites

| Need | Why | Notes |
|---|---|---|
| Linux (or WSL) + bash | runtime (tmux sessions, watchdog) | native filesystem path recommended — network shares (9p/CIFS) reject `chmod` |
| Python **3.10+** | console, services, quality gates | `python3 --version` |
| `tmux` | process supervision | `sudo apt-get install tmux` |
| `git` | clone + version updates | optional; archives can run without it |
| A model endpoint | chat/brain | any OpenAI-compatible endpoint — see [Model backend](#model-backend) |
| A Telegram bot token | chat channel | from @BotFather — see [Credential slots](#credential-slots) |

## Quick start

```bash
git clone https://github.com/AiDog-im/AiDog.git aidog && cd aidog   # public repo: no credentials required
bash deploy/install.sh                           # <- the one command
aidog setup                                      # guided wizard: fill the slots it lists
aidog setup --check                              # any time: read-only status (0/2/1)
```

The installer is **idempotent** — it never overwrites files you already have,
so re-running it is always safe (it doubles as the repair/verify command).

Exit codes: `0` ready · `2` installed but credentials/backend pending
(fail-closed by design, nothing broken) · `1` hard failure.

## Setup wizard

`aidog setup` (no flags) walks every slot interactively instead of leaving you
with `$EDITOR secrets.env`:

- **filled slots are reported and kept** (`NAME set (len N)`) — the wizard
  never overwrites; rotate a value by editing `secrets.env` yourself;
- **empty slots are prompted** — secrets use hidden input on a TTY, and no
  value is ever echoed back (output carries slot names and lengths only);
- **each answer is validated** (token format, comma-separated ids, http(s)
  endpoint …); an invalid answer is retried up to 3× with the reason, then the
  slot stays empty (fail-closed);
- **every write is read back and syntax-checked** (`own-line KEY=value` for
  `secrets.env`, JSON re-parse for `web/settings.json`);
- at the end it offers to run the installer (doctor + boot + verify), or skip
  with `--no-boot`.

Non-interactive variants (CI / scripted):

| Command | Behavior | Exit |
|---|---|---|
| `aidog setup --check` | read-only slot + format report, creates nothing | `0` ready · `2` pending · `1` broken |
| `aidog setup --yes` | the idempotent installer (provision + boot + verify) | installer's `0/2/1` |
| `aidog setup --yes --no-boot` | provision + report only, never boots | `0/2` |

A non-TTY `aidog setup` (no flags) falls back to `--yes`, so scripts keep the
old provision + boot + verify behavior without ever blocking on a prompt.

## What the installer does

1. **Preflight** — Python 3.10+, tmux, port sanity (`:8790` free or already ours).
2. **Provisions machine-local files, only when missing** (all gitignored):
   | File | From template | Purpose |
   |---|---|---|
   | `secrets.env` | `secrets.env.example` | credentials — the only credential store (mode 0600) |
   | `web/settings.json` | `web/settings.example.json` | console settings incl. model endpoint |
   | `deploy/watchdog.mycore.local` | `deploy/watchdog.mycore.example.json` | watchdog targets/alerts |
   | `deploy/watchdog.local` | `deploy/watchdog.local.example` | alert chat id |

   `deploy/memory_sources.local` is intentionally **not** created — its
   template ships placeholder paths; the memory chain uses its documented
   defaults when the file is absent.
3. **Installs the `aidog` command** (`deploy/install_cli.sh`, idempotent).
4. **Boots the runtime** — web console, chat service (fail-closed until
   provisioned), watchdog.
5. **Verifies** — console health, service readiness, model endpoint, empty
   credential slot names, and `deploy/aidog_doctor.py` (run with a bare exit
   code, never through a pipe).

## Credential slots

`secrets.env` is the **only** place values live. Logs, docs, screenshots and
chat output reference slot names and counts only — never values.

**Required** (the installer keeps reporting `pending` until these are set):

| Slot | Needed for | Where to get it |
|---|---|---|
| `WEB_TOKEN` | console write operations | any long random string you invent |
| `MYCORE_TG_TOKEN` | Telegram chat service | @BotFather token (`123456:ABC-...`) |
| `AIDOG_TG_ALLOWED_USERS` | who may talk to the bot (fail-closed when empty) | your Telegram numeric user id (comma-separated for several) |

**Recommended** (features degrade without them — never block `READY`):

| Slot | Missing means |
|---|---|
| `MODEL_API_KEY` | no key needed if your model endpoint is open (the default local gateway is) — set it only when your provider requires one |
| `AIDOG_GH_PAT` | optional — `aidog update` reads the public repo anonymously (a PAT only raises GitHub API rate limits) |

Format rule: **one `KEY=value` per line, no trailing comment on the value
line** — tooling treats a value as present only when it is the sole content
after `=`. Put comments on the line above.

Legacy slots (`GITHUB_PAT`, `TG_BOT_TOKEN`, `MODEL_BASE_URL`, `LEGACY_*`) are
documented inside the template and can stay empty.

## Model backend

The console and service default to `http://127.0.0.1:3460/v1`
(`web/settings.json` → `mycore.base_url`, model `mimo-v2.6-flash`). Point it at
**any OpenAI-compatible endpoint** you run or subscribe to:

```jsonc
// web/settings.json
"mycore": { "base_url": "http://127.0.0.1:3460/v1", "model": "your-model-name" }
```

If nothing listens there, the installer reports the endpoint as unreachable
and exits `2` — that is an honest "no brain configured yet", not a crash.

## After install

| Task | Command |
|---|---|
| Re-verify any time | `aidog setup --check` (read-only) or `aidog setup --yes` (boot + verify) |
| Health report | `python3 deploy/aidog_doctor.py` (exit 0 = no FAIL) |
| Stop / start runtime | `bash deploy/boot_services.sh stop` / `start` (or `status`) |
| Check for a new version | `python3 deploy/aidog_update.py check` (console shows an upgrade banner too) |
| Apply an update | `python3 deploy/aidog_update.py apply` (refuses with uncommitted changes, by design) |
| Talk to your bot | Telegram: send `/start` to your bot, then chat |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `port 8790 is used by another service` | stop the foreign process or run with `AIDOG_WEB_PORT=<free port>` |
| `tmux not found` | install tmux |
| chat service stays `PENDING` | fill `MYCORE_TG_TOKEN` + `AIDOG_TG_ALLOWED_USERS`, re-run `aidog setup` |
| model endpoint unreachable | set `mycore.base_url` to a live OpenAI-compatible endpoint |
| Telegram messages not arriving after reboot | direct egress can be blocked — the runtime routes bot traffic through a local proxy (`HTTPS_PROXY`); set yours in the environment or start `tmux` sessions from a shell that has it |
| update check fails | fill `AIDOG_GH_PAT` (the legacy `GITHUB_PAT` slot is fallback only) |
| doctor WARN after editing files | doctor wants a clean worktree: commit first (`0 WARN` baseline) |

## Uninstall

Stop the runtime and delete the checkout — machine-local state lives beside
the code (`secrets.env`, `web/settings.json`, `deploy/*.local`, `data/`), so
removing the directory removes everything:

```bash
bash deploy/boot_services.sh stop
rm -rf <checkout> ~/.local/bin/aidog
```

> Free forever — no account, no subscription, no paid tier, ever.
