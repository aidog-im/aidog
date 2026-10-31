# AGENTS.md — repository rules for I'm AiDog

> This is the **public, user-facing** rule book for this repository. The
> owner's private decrees, machine wiring and publication rulings live in the
> development repository and are never exported here — see
> `deploy/publish_manifest.json` for what is excluded and why.
>
> An AI agent working in a checkout of this repository should read this file,
> `README.md` (or `README_cn.md`) and `.github/CONTRIBUTING.md`.

## What this repository is

I'm AiDog is a personal embodied-AI product: a self-hosted companion that runs
on your own machine, talks to you over a chat channel, and keeps its memory in
plain files you own.

- **It is permanently free.** No paid tier, no in-app purchase, no usage fee,
  no paid feature gate — ever.
- The published tree here is a **generated, desensitized export** of the
  development repository (see the publication gate below), so what you clone is
  a release snapshot, not a working development checkout.

## Repository layout

```
mycore/     kernel layer   — the self-developed loop, tools, compaction,
                             channels and service (standard library only)
overlay/    body layer     — constitution (SOUL.md), curated memory, skills,
                             locales, hooks, plugins, skin; kernel-independent
adapters/   business layer — chain access and project adapters (pure Python)
deploy/     installer, doctor, watchdog, upgrade SOP
web/        console server and static console UI
site/       marketing site source
tests/      endpoint/CLI/E2E suites
docs/       capability documents (docs/features/*) and reference material
```

The three layers are the point: the kernel is replaceable, the body is not.
Customization belongs in `overlay/`, never in a patched kernel.

## Install, verify, upgrade

```bash
git clone https://github.com/AiDog-im/AiDog.git aidog && cd aidog
bash deploy/install.sh          # provision + boot + verify, idempotent
python3 deploy/aidog_doctor.py  # exit 0 = no FAIL
```

Upgrades come from releases, never from building source yourself:

```bash
python3 deploy/aidog_update.py check   # compare against the release manifest
python3 deploy/aidog_update.py apply   # dirty-tree guard → fetch → ff-merge → restart
```

`apply` refuses to run with uncommitted changes so you decide what happens to
your edits. Version truth is `VERSION` (semver) + `release.json`; both are
committed together and the manifest must exist on the remote when a version
ships.

## Context files (how this file is — and is not — used)

The runtime can inject instruction files from **directories you configure
explicitly**: `AGENTS.md`, `CLAUDE.md` and `AGENTS.local.md`, resolved from
`AIDOG_CONTEXT_DIRS` (or the console's `mycore.context_dirs`), an
`os.pathsep`-separated list.

- It is **off by default**: no configuration means no change to the prompt.
- **This repository's own root `AGENTS.md` is deliberately not auto-discovered.**
  Cloning the project does not silently load its rule book into every turn;
  you opt a directory in.
- A project without any of these files is the normal state, not an error.
- `@<path>` lines inside an opted-in file expand to the referenced file's
  content (depth 1, bounded), so a rule book can pull in a companion document.

## Source language and internationalization

- **All source code is English**: identifiers, comments, docstrings, log
  messages.
- **All documentation is English**, with two deliberate exceptions:
  `README_cn.md` and the Chinese locale pack.
- **UI strings live in locale packs** (`overlay/locales/`): `en` is the default
  locale, `zh` ships alongside it, and adding a language means adding a pack —
  no code change.
- Locale resolution is user setting > default (`en`).

If you add a user-visible string, add it to both packs. The suite
`python3 tests/source_lang_check.py` enforces this.

## Credential discipline

- `secrets.env` (mode 0600, gitignored) is the only credential store; the
  tracked `secrets.env.example` lists slot names with empty values.
- **Never echo a credential value.** Report the location and, if asked, the
  length — never the content, in any output, log or document.
- Runtime state stays separate from code: `data/`, `profiles/`, `web/settings.json`
  and `web/.state.json` are gitignored and never shipped.

## PUBLIC-REPO GATE

This checkout is produced by a gate, not pushed to directly:

1. The development repository's `origin` points at an owner-only vault; a
   tracked `pre-push` hook refuses every destination that is not that vault.
2. The public repository is rebuilt from a desensitized snapshot: manifest
   classification (deny wins, unknown is dropped) → ordered transformation
   rules → **leak scan and structural verification over the result** → commit
   and matching tag. A single hit aborts before anything leaves the machine.
3. `.github/workflows/public-leak-guard.yml` rescans every push with
   fragment-assembled signatures; on a hit it raises an alert without
   reproducing the matched text and rolls `main` back to the previous clean
   commit.

Because of (2), the gate tooling itself — the publish gate, the transform
table, the export writer, the push hooks and their specification — is
deliberately **not part of this snapshot**. Commands in the documentation that
name those tools are maintainer steps and run in the development checkout.

**Do not edit this repository by hand expecting the change to stick.** Changes
are made in the development repository and re-exported by its maintainer.

## Where to read next

- `README.md` / `README_cn.md` — what it does, quick start, screenshots.
- `.github/CONTRIBUTING.md` — the test suites a change must pass.
- `docs/features/*` — one document per capability.
- `docs/security_checklist.md` — the security properties the gates enforce.
- `overlay/constitution/SOUL.md` — the companion's constitution (identity,
  boundaries, reporting protocol).
