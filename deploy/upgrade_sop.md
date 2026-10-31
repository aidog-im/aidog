# Upgrade SOP — 上游内核 kernel (Phase 1)

> **HISTORICAL / ARCHIVED at P5-5 mycore swap (2026-10-19)**: the transitional
> kernel track is retired — the gateway (aidog_gw) and its watchdog (aidog_wd)
> no longer run, `deploy/upstream_shim.env` (which held `$AIDOG_UPSTREAM_TREE`)
> is deleted at the swap, and supervision moved to the mycore watchdog.
> Kept for history; do not follow for current operations.

Goal: upgrade the transitional kernel without touching our overlays, data, or identity.
Source: upstream `getting-started/updating.md` + `user-guide/configuration.md`
(installed copy: `$AIDOG_UPSTREAM_TREE/website/docs/...`, path in `deploy/upstream_shim.env` — HISTORICAL: that file is deleted at P5-5).

## Cadence

- Pin **stable** channel; evaluate upgrades **quarterly** (not per-release).
- Before any upgrade: read upstream release notes for retired config/behavior.

## Pre-flight (mandatory)

1. `upstream backup` → zip of `~/.upstream` (or set `updates.pre_update_backup: full`).
2. Snapshot our repo state: `git status --short` from the repo root (clean tree).
3. Export config: `upstream -p aidog config get ...` for keys we tune
   (skills.external_dirs, telemetry, updates).

## Upgrade

```bash
upstream update            # 8-stage: snapshot → fetch → syntax gate (auto-rollback)
                         # → deps → config migrate → desktop rebuild → gateway restart
```

- If it prompts about stashed local changes: **abort** — that means the
  overlay-only discipline was violated somewhere; investigate before proceeding.
  (Upstream resets in-source edits: issue #74885, PR #75763.)

## Post-flight verification checklist

- [ ] `upstream --version` reports expected release
- [ ] `upstream -p aidog config get skills.external_dirs` still points to our skills dir
- [ ] `upstream -p aidog skills list` shows the 6 external skills enabled
- [ ] `upstream config get telemetry.shared_metrics` still `enabled: false / send: false`
- [ ] `git status` (from the product repo root) unchanged — we never write upstream source
- [ ] Overlay re-load if upstream schema changed: diff `SOUL.md` placeholders
- [ ] Smoke: `upstream -p aidog -z "ping" ...` quick reply (see `tests/ab_protocol.md`)

## Rollback

- Upstream auto-rolls back on syntax gate failure.
- Manual: reinstall previous tag in `$AIDOG_UPSTREAM_TREE` (path lived in `deploy/upstream_shim.env` — HISTORICAL, deleted at P5-5; stable channel
  pins exact commit) + restore `~/.upstream` backup zip via `upstream import`.
- MIT license guarantees permanent use of already-held versions (no vendor lock-in).

## Never do

- Never edit files under `$AIDOG_UPSTREAM_TREE/` (upstream source).
- Never run `upstream update` from inside the source tree with local patches.
- Never let upstream prompts auto-install without reading the release note.
