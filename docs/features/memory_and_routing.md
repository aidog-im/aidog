# Feature spec — Memory (dual layer) & Skill routing (deterministic pre-router)

Owner-approved pain points addressed: memory wall (upstream issue #5320, 2200-char wall),
default-load bloat / token waste (prior art #9157), cold-start/model-swap skill
selection. Status: **both live in P2**.

## 1. Dual-layer memory

| Layer | Where | Budget | Access |
|---|---|---|---|
| Curated top slots | `<home>/MEMORY.md` (≤2200 chars), `<home>/USER.md` (≤1375) — `<home>` = the resolved memory home (chain below) | enforced by tool `status` (exit 2 on breach) | memory tool loads every turn |
| Full archive | `<home>/ARCHIVE/MEMORY_full.md` + `ARCHIVE/daily/*.md` + `USER_full.md` | unlimited | file tools on demand |

- Source of truth (dual-brain phase): pi project memory — **one-way snapshot**
  (`python3 deploy/aidog_memory.py sync`, idempotent).
- Curated content authored in repo: `overlay/memory/*_curated.md` (versioned).
- Productization path: at P5 (`~/.aidog/`) the same layout becomes the user's
  own memory home; sync becomes `aidog memory sync/refresh` (self-contained,
  no cross-project source).

### Memory home — portable 4-step chain (P5-4)

`aidog_memory.py sync|status` (DST) and `aidog import` destinations resolve the
memory home through the same chain — first hit wins:

| # | Step | Label printed by `status` |
|---|---|---|
| 1 | env `AIDOG_MEMORY_HOME` | `env AIDOG_MEMORY_HOME` |
| 2 | `MEMORY_HOME` in `deploy/memory_sources.local` (gitignored machine config) | `local MEMORY_HOME` |
| 3 | `$HOME/.aidog/memories` — **post-swap default**; this step hits only when the portable home already exists (materialized by an earlier `sync`/`import`) | `default ~/.aidog/memories` |
| 4 | `<AIDOG_PROFILE_DIR>/memories` — **RETIRED at P5-5**: legacy transition fallback, read only while `deploy/upstream_shim.env` existed (deleted in that same car); post-swap this step never matches | `legacy shim profile` |
| — | no shim → step 3 becomes the final fallback; `sync` creates the home (`mkdir -p`) so a fresh machine never fails | `default ~/.aidog/memories` |

- `status` prints the **resolved** home as its first line:
  `home: <path> (source=<label>)` (paths are fine in output; secrets never).
- Post-P5-5 (shim gone, no override) the identical chain lands on **`$HOME/.aidog/memories`**;
  no code edit was needed at deletion time. (While the shim existed — transition —
  the resolved home stayed the legacy profile path.)
- Single source of truth: `deploy/aidog_memory.py resolve_memory_home()`;
  `aidog_persist.py` imports it (export source chain step 4 + import `memory/`
  destination). Bundle layout and `manifest format_version 1` are unchanged.
- **Rename migration (`~/.legacy` → `~/.aidog`, pre-rename leftover)**: nothing
  is moved automatically (the product never writes outside its homes by
  surprise). One-line manual migration:
  `mv ~/.legacy/memory ~/.aidog/memory` and `mv ~/.legacy/persona ~/.aidog/persona`.
  Until migrated, point the chain at the old location with env
  `AIDOG_MEMORY_HOME` (home) / `AIDOG_MEMORY_SRC` (export source) or the same
  keys in `deploy/memory_sources.local` — env override always wins.
  Persona extraction keeps a legacy-compat read: it restores to
  `$HOME/.aidog/persona`, using `$HOME/.legacy/persona` only while the default
  does not exist yet, and prints the one-line migration hint when it does.

## 2. Skill routing (deterministic pre-router)

- Upstream already lazy-loads skill **bodies** (`skill_view` on demand —
  docs/user-guide/features/skills.md:152); descriptions stay resident as cheap
  triggers. Bloat pain is therefore mostly solved upstream — **do not market it**.
- Our addition: `lib/skill_route.py` (wrapper `lib/skill_route.sh`) —
  deterministic TOP3 router (stdlib, reproducible scoring, exit 0=hit / 3=no-hit
  / 2=bad-args) that replaces "model intuition" when the model swaps or
  cold-starts; TOP1 must-load, max 2 bodies per turn.
- **Vendored at P4 (2026-10-18)**: genericized copy now lives in `lib/` —
  English output, self-derived repo paths, skill roots from `--dirs` /
  `AIDOG_SKILL_DIRS` / `SKILL_DIRS` in `deploy/memory_sources.local` /
  `overlay/skills` / profile config `external_dirs:` (legacy, shim-gated —
  the shim was deleted at P5-5, so this step never matches post-swap).
  No cross-project exec; ranking verified equivalent to the transition
  original (same TOP3 order on shared queries).

  **Root resolution order (P5-4)**: `--dirs` > env `AIDOG_SKILL_DIRS` > local
  `SKILL_DIRS` (os.pathsep-separated) > repo-embedded `<repo>/overlay/skills`
  > legacy profile `external_dirs:` (shim-gated — dies with P5-5). Existing
  dirs only, deduplicated; scoring/ranking behavior is unchanged.

### Skill frontmatter compatibility (car 1-D3, 2026-10-20)

`lib/skill_route.py` parses `SKILL.md` frontmatter with a **conservative YAML
subset**, field-wise aligned with the `SKILL.md` + YAML frontmatter convention
that the in-repo skills-ecosystem survey records as the de-facto standard
(anthropics/skills, `docs/research/` survey §B [S1]) and with the
agentskills.io open standard [S2]. **Spec caveat**: this car had no network,
so field details come from that survey, not a live spec fetch — re-verify
against the survey/spec before widening the surface.

- **Aligned/parsed fields**: `name` and `description` as plain, `'…'` / `"…"`
  quoted, multi-line quoted, and `|` / `>` block scalars (with `+`/`-`
  chomping and indentation indicators). `license`, `allowed-tools` and
  `version` additionally surface **per hit** in `--json` output, present-only
  (a skill that declares none simply lacks the key); no existing JSON key is
  renamed, removed or reordered for skills that declare nothing.
- **Tolerance contract (never fatal)**: unknown keys (`unknown-field: …`),
  nested-looking indents (`metadata:` sub-lines are skipped, not interpreted),
  comments and malformed lines never raise — `parse_frontmatter_fields`
  fail-softs to `{}`. Absent **or unterminated** frontmatter keeps the
  pre-existing behavior exactly: H1 + first prose line fallback
  (`fallback_desc`), name falls back to the directory name.
- **Ranking unchanged**: corpus stays `name + description + directory name`
  and the score formula is untouched; a multi-line description enters the
  corpus verbatim (no truncation, no first-line-only shortcut). Regression
  wall: `python3 tests/skill_route_p4.py` = **30/30** (the original 15
  contract checks unchanged, 15 new frontmatter checks added).
- **Progressive disclosure (body lazy-load) is already in force** — evidence:
  - `mycore/assembly.py:12` — skill descriptions stay OUT of the system
    prompt by design; the loop calls the `skill_route` tool instead (lazy bodies);
  - `mycore/assembly.py:75` — system rule: call `skill_route` **before**
    reading any SKILL.md (TOP1 only, max 2 bodies per turn);
  - `mycore/tools.py:292` / `:295` — the tool returns the verdict the loop
    reads before any body load (registration blurb `:416`: "TOP3 before
    loading any SKILL.md body");
  - this document:55 — upstream body loading is on demand (`skill_view`);
  - `lib/skill_route.py:2` — "TOP3 before any SKILL.md body load".
  So a third-party skill's frontmatter alone drives selection; its body is
  read only after TOP1 is chosen — the frontmatter subset above is therefore
  the full surface that needs standards alignment today.
- **Honest boundaries**: parser/field compatibility only — **no runtime
  auto-install**, no registry integration (skills stay owner-installed from
  trusted sources: browse-then-adopt, catalogue ≠ endorsement); no full YAML
  validation (subset parser, fail-soft by design); nested structured values
  are tolerated but not interpreted.

## 3. GA checklist (deltas from transition)

- [x] memory sync self-contained (env `AIDOG_MEMORY_SRC` > `deploy/memory_sources.local`
      > portable default `$HOME/.aidog/memory` materialized by `./aidog import` —
      no machine-specific absolute path required on a fresh machine; P4 2026-10-18,
      see "Cross-machine portability" below)
- [x] memory **home** repo-agnostic (P5-4 2026-10-19: env `AIDOG_MEMORY_HOME` >
      local `MEMORY_HOME` > `$HOME/.aidog/memories` > legacy shim profile
      (**RETIRED at P5-5** — `deploy/upstream_shim.env` deleted, so that step
      never matches; see "Memory home" above)
- [x] router vendored + skill paths config-driven (`--dirs` / `AIDOG_SKILL_DIRS` /
      local `SKILL_DIRS` / profile `skills.external_dirs` (shim-gated) /
      `overlay/skills`; GA next step: read `config.yaml skills.extraDirs`
      directly once the product config schema lands)
- [ ] budgets surfaced on the web console (status API already exposes numbers)
- [x] skill count surfaced on the web console — Overview `#capGrid`
      (frontend car 0.5.39, names/counts only; evidence
      `tmp/e2e_frontend_ui.js` `U5` — the catalog itself stays value-free
      per `tmp/e2e_frontend_coverage.py` `L1` — see
      [frontend_coverage.md](frontend_coverage.md))

## Cross-machine portability (P4, 2026-10-18)

Owner decree: the memory sync source must be **self-contained** (a fresh machine
must not depend on machine-specific absolute paths of another project), and the
**private memory repository** is the single source of truth across machines.
Both are implemented by the `./aidog` CLI (root shim → `deploy/aidog_persist.py`,
stdlib only).

### Source-resolution chain

Export (and `aidog_memory.py sync`) resolve the memory source through this chain;
the first step that yields a value wins:

| # | Step | Applies to |
|---|---|---|
| 1 | explicit `--src DIR` / `--persona DIR` | `aidog export` only |
| 2 | env `AIDOG_MEMORY_SRC` / `AIDOG_MEMORY_PERSONA` | export + sync |
| 3 | `MEMORY_SRC` / `MEMORY_PERSONA` in `deploy/memory_sources.local` (gitignored machine config) | export + sync |
| 4 | portable default `$HOME/.aidog/memory` (materialized by a previous `aidog import`) | export only |
| 5 | memory-home chain (step 4 of it): `<AIDOG_PROFILE_DIR>/memories` → `source_kind=profile-fallback` — **RETIRED at P5-5, only while `deploy/upstream_shim.env` existed; never matches post-swap** (see "Memory home" above) | export only |

Rules: a configured-but-missing path (steps 1–3) is a **hard error** — never
silently export the wrong snapshot, never invent paths; step 4 only applies when
the directory exists; `aidog_memory.py sync` uses steps 1–3 and errors when none
resolves. `deploy/memory_sources.local` is written by `aidog import` **only when
it does not exist** (an existing machine-local config is never clobbered; its
pointers are printed instead).

### CLI

```bash
./aidog export [--src DIR] [--persona DIR] [--out DIR] [--tag NAME]
./aidog import <tarball> [--dest-profile DIR] [--source-home DIR] [--persona-home DIR]
./aidog list [--dir DIR]          # default dir: <repo>/exports
./aidog --help
```

Exit codes: `0` ok · `1` source/verification/config error · `3` credential-gate
refusal (export only). All output is English; secrets are never printed
(file + pattern label / counts only).

### Bundle layout

```
exports/aidog_memory_<YYYYMMDD-HHMM>[_tag].tar.gz      # UTC stamp
exports/aidog_memory_<YYYYMMDD-HHMM>[_tag].tar.gz.sha256   # sidecar: "<sha>  <name>"
└── inside the tarball:
    manifest.json   # format_version 1, created_at (UTC ISO), tag, source_kind
                    # (pi-memory | profile-fallback), totals, and per-file
                    # {path, size, sha256} — RELATIVE bundle paths only,
                    # no absolute machine paths (artifact may be shared in-repo)
    memory/**       # full source snapshot (pi memory home or profile memories,
                    #   ARCHIVE/ layout preserved)
    persona/**      # optional persona references
```

Import verifies, in order, before writing **anything**: sidecar sha256 (when
present) → hardened tar reader (refuses absolute paths, `..`, symlinks/hardlinks,
foreign prefixes — tar-slip defense) → per-file sha256 against `manifest.json`
(no missing, no extra, no size/hash mismatch). On any failure: exit 1, nothing
extracted. On success it restores `memory/` into the resolved memory home
(same 4-step chain as `aidog_memory.py`; `--dest-profile DIR` forces
`DIR/memories`) and into the source home (`--source-home`, default
`$HOME/.aidog/memory`), restores `persona/` into `--persona-home` (default
`$HOME/.aidog/persona`, legacy `$HOME/.legacy/persona` only while the default is
absent), writes `deploy/memory_sources.local` when absent, then runs
`aidog_memory.status` for the budget report (files restored + budget ok/OVER).

### Credential gate (export)

Every payload file is scanned before the tarball is written; a hit refuses the
export (exit 3, **no tarball**) and prints `REFUSED <file> [<label>]` — the
matched value is never echoed.

| Refused (credential-shaped) | Allowed (private memory repo bundle) |
|---|---|
| `filled-credential`, `personal-tg` labels from `deploy/check_publish.py` `leak_patterns()` (single source of truth) | machine paths (`/mnt/...`, `/root/...`) |
| `ghp_` / `github_pat_` / `AKIA` keys | upstream brand words |
| `sk-` API keys | repo / project names |
| bot-token shape `123456789:AAAA...` | ordinary prose, memory content |
| inline `TOKEN|PAT|SECRET|PASSWORD|API_KEY = <value>` assignments | |

Machine paths, brand words and repo names are deliberately allowed: this bundle
goes only to the **private** memory repository (the public-repo gate
(`check_publish.py`) still applies separately to the product repo itself).

### Fresh-machine revival flow

1. Clone the product repo (private origin).
2. Clone the private memory repository (holds raw memory + `exports/*.tar.gz`).
3. `./aidog import <memory repo>/…/aidog_memory_*.tar.gz` — verifies sidecar +
   per-file hashes, restores the memory home (4-step chain above), materializes
   the self-contained source at `$HOME/.aidog/memory`, and writes
   `deploy/memory_sources.local` (only if absent).
4. `python3 deploy/aidog_memory.py sync` — refreshes the curated budgeted
   snapshot from the now-local source (and creates the home on a fresh machine).
5. `./aidog list` — audit bundles (OK/BAD per bundle; exit 1 if any BAD).

Tests: `python3 tests/memory_persist_p4.py` (offline, writes only under /tmp;
58 checks — roundtrip, credential gate, tar-slip, missing source, config
non-clobber, list verify, `aidog_memory` import contract, P5-4 memory-home
chain).
