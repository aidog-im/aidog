# Feature spec — Skills ecosystem (official skill catalog and the dual gates)

Status: **assets landed** (release 0.5.47, car 4 lane M: standalone repo bootstrap + first-batch intake +
official catalog page + catalog schema validation E2E).
Design anchors: `docs/design/autonomous_ops_design.md` §4 (three-layer structure / standalone repo /
source migration / official catalog endpoint) and §5, car 4 row. This page is **the 33rd capability document**, covering
the three-layer structure, the standalone repo and its dual gates, the first-batch list, the official catalog page and its
catalog sync mechanism, plus a preview of the `GET /api/skills/remote` endpoint.

**Boundary**: this lane lands **assets and documentation only** — the standalone repo, the official catalog page, the catalog copy,
this page, and the E2E. **The product-side read endpoint `GET /api/skills/remote` is implemented in release 0.5.47 lane M2**
(`web/server.py` and the corresponding E2E run serially after car3, see §6).

Companion pages: [capabilities.md](capabilities.md) (capability catalog contract),
[memory_and_routing.md](memory_and_routing.md) (`skill_route` deterministic pre-filtering,
isomorphic with the "progressive disclosure" of this spec), [tool_toggles.md](tool_toggles.md) (toggle surface for the tools listed in `requires`),
[missions.md](missions.md) (night-watch missions consume skills via the capability manifest),
[site_visibility.md](site_visibility.md) (rendering and assertion surface of the official catalog page),
[frontend_coverage.md](frontend_coverage.md) (console skill view
matrix).

## 1. Three-layer structure

```
Layer 1  this repo, overlay/skills/            core skills shipped with the product (5, all passing lint + skill_route pre-filter)
Layer 2  the standalone skills repo (public GitHub)   single source of truth for officially recommended skills: catalog + category directories + dual gates + versions
Layer 3  the aidog.im catalog page             static JSON catalog: browsable by users; the product reads the same JSON
```

- Layer 1 is the **distribution surface** (present on install); Layer 2 is the **truth surface** (listed only after review); Layer 3
  is the **discovery surface** (readable by both humans and models).
- All three layers use the same format: directory name = frontmatter `name`, and `description` is the sole
  retrieval entry point (discovery reads only name + description, and loads the body only on a hit).

## 2. Standalone repo layout and catalog fields

```
aidog-skills/
├── catalog.json                 # machine-readable master catalog (site and product read the same file)
├── skills/<category>/<name>/SKILL.md   [+ scripts/ references/ assets/ examples/]
├── tools/lint.py                # machine gate
├── REVIEW.md                    # human gate (deny-by-default checklist)
├── THIRD_PARTY.md               # attribution list for upstream recreations
└── README.md                    # English: public-repo statement + review process + sync mechanism
```

**A catalog entry has exactly the seven fields from design doc §4.4** (none invented):

| Field | Type | Description |
|---|---|---|
| `slug` | string | lowercase hyphenated id = directory name = frontmatter `name` |
| `name` | string | display name (currently the same value as `slug`) |
| `category` | enum | `automation-ops` / `research` / `dev` / `content` / `trading-readonly` / `reporting` |
| `description` | string | ≤1024, one sentence, verbatim identical to frontmatter `description` |
| `version` | semver | skill revision (aligned with frontmatter `metadata.version`) |
| `requires` | string[] | the **tool names or credential slots** required (drawn from the `KNOWN_TOOL_NAMES` / `SECRET_GROUPS` vocabularies) |
| `install_status` | enum | `bundled` (shipped with the product) / `catalog` (installable from the catalog) |

The top-level shape is fixed as `{"skills": [...]}`. `requires` is the hook §4.4 describes for "widening the
application surface according to what you already have": the site annotates prerequisites with it, and the product
aligns it with the capability manifest (a skill installed without its tools just idles).

## 3. Dual gates (machine + human, deny-by-default)

1. **Machine gate `tools/lint.py`**: passes only when it prints `=== N/N PASS ===`.
   - Structure/spec: three-way consistency across catalog ↔ frontmatter ↔ directory, `name ≤64`,
     `description ≤1024`, semver, resolvable relative links, SKILL.md byte budget.
   - **Leak scan (over every text file in the repo, including all files other than itself)**: local machine paths
     (mount points / root home directory / home / drive letters), credential shapes (`KEY=value`, private key blocks,
     common token prefixes), personal messaging handles, email addresses (RFC 2606 reserved domains exempt),
     internal codenames and cross-repo private names, upstream brand words — **0 hits** (brand words are written as
     concatenated constants and self-exempted, the same philosophy as the product's `deploy/check_publish.py`
     — development-repository tooling, see the note in §8).
2. **Human gate `REVIEW.md`** (all four sections checked before release):
   - **A desensitization gate**: item-by-item confirmation of paths / credentials / identity / internal codenames / brand words;
   - **B quality gate**: `description` must state "when to use it" and include a negative trigger example (description quality =
     routing quality), progressive disclosure, commands that actually run, experience entries dated, generic first person;
   - **C license gate**: first-party skills carry zero third-party text; upstream recreations must **ship a per-skill license file**
     and be registered in `THIRD_PARTY.md` (source URL + access date + list of changes); no clean
     license, no intake;
   - **D security gate**: land files but never execution, trusted sources read-only, approval boundaries untouched, `requires`
     honest.
3. Version discipline: content change → bump `metadata.version` → update `catalog.json` within the same
   commit → sync the site copy (§5).

## 4. First-batch intake list (12: 5 from this repo + 6 desensitized recreations + 1 upstream recreation)

| # | slug | Category | Origin | One-liner |
|---|---|---|---|---|
| 1 | `binance-read-only` | trading-readonly | verbatim from this repo | Read-only Binance access, minimal-permission USER_DATA keys and auth diagnostics; order placement only through the approval gate |
| 2 | `kraken-read-only` | trading-readonly | verbatim from this repo | Read-only Kraken access and nonce/API-Sign auth troubleshooting; never used for trading |
| 3 | `okx-read-only` | trading-readonly | verbatim from this repo | Read-only OKX access and passphrase/timestamp-offset troubleshooting; never used for trading |
| 4 | `systematic-debugging` | dev | verbatim from this repo | The iron rule of debugging any bug: root cause first, then fix |
| 5 | `writing-plans` | dev | verbatim from this repo | Write an implementation plan before touching code on multi-step tasks |
| 6 | `growth-ops` | automation-ops | desensitized recreation | Free-distribution playbook: channel matrix, verification-email loop, directory-submission SOP, night-watch submissions, prior art monitoring, metrics meta-rules |
| 7 | `site-ops` | automation-ops | desensitized recreation | Site operations on edge platforms: scoped token role tables, DNS vs proxied differences, locked release chains and ghost-path pitfalls |
| 8 | `mail-assistant` | automation-ops | desensitized recreation | Read-only inbox triage into four classes, language-mirroring reply drafts that are never sent, activation-link verification, to-do escalation |
| 9 | `research-brief` | research | desensitized recreation | Three-tier fact-check report pipeline: multi-source verification, ≥2 independent sources, tri-color annotation, citations always attached, six-step comparison selection |
| 10 | `analysis-pipeline` | content | desensitized recreation | Long-form deep-analysis pipeline: worthiness scorecard, evidence archiving, anti-poisoning iron rules, bilingual, lint, publish package |
| 11 | `voice-reply` | reporting | desensitized recreation | Voice only on explicit request: free TTS, text always attached, inbound voice transcribed first, silent fallback to plain text on failure |
| 12 | `webapp-testing` | dev | upstream recreation | Playwright local web-testing toolkit (**Apache-2.0**, per-skill license file + attribution in `THIRD_PARTY.md`) |

**Recreation policy** (for the sibling project's operational-engineering skills): semantic porting + rewriting in
generic first person; per piece, strip local absolute paths, personal identity and persona fields, real handles, credentials, internal codenames,
cross-repo private links; **identity/persona skills are excluded** (personal assets, already adjudicated by the research doc).
Upstream recreations take only public skills that **ship their license file with the skill**; whole directories are copied together with `LICENSE.txt`,
and any changes (if any) are declared item by item in `THIRD_PARTY.md`.

## 5. Official catalog page and catalog sync mechanism

- **Page**: `site/skills.html` (Draft E language family, bilingual, a standalone site page, not loading
  `assets/main.js` — in-page navigation points to `index.html#…` anchors; it ships its own lightweight script for
  locale-pack loading, hamburger navigation, pattern and catalog rendering).
- **Data**: at runtime the page reads `site/skills/catalog.json` (**the copy committed in this repo**) and
  renders it grouped by the six categories of §4.2; each entry card shows slug / one-liner description / version / install status /
  `requires`. Language keys live only in the `skills.*` namespace of `site/i18n/{en,zh}.json`
  (this car adds 36 keys; the site pack goes **217 → 253**, and the en/zh key sets are always identical).
- **Sync mechanism (proposed policy, carried into the doc by the parent)**: the standalone repo's `catalog.json` is the
  master, and `site/skills/catalog.json` is a **byte-for-byte copy**; every catalog change is followed by
  `cp aidog-skills/catalog.json site/skills/catalog.json` (or an equivalent scripted
  `sync` step), then a run of `tmp/e2e_skills_catalog.py` — it asserts the copy's schema is
  valid, the page renders the same number of entries as the copy, and leaks score 0 hits. **Manual cp + E2E assertions**
  is the current policy (a scripted sync can be a small later improvement; it needs the parent's nod before a new file is added).

## 6. `GET /api/skills/remote` (**implemented in release 0.5.47 lane M2**; documentation preview only here)

- **Shape (preview, finalized at M2)**: `GET /api/skills/remote` →
  `200 {"source":"remote"|"cache","fetched_at":<iso>,"count":<n>,"skills":[...]}`
  — the `skills` array is the catalog entries (the seven fields of §2, verbatim).
- **fail-soft contract**: network unavailable → read the built-in cache (the catalog copy shipped with the release)
  and honestly label it `source:"cache"`; **never blocks the conversation, never exits with 5xx**; an entry is missing or
  its schema does not match → drop that entry and continue (partial success beats overall failure).
- **Ownership**: `web/server.py` belongs to car3 lane K, so this endpoint is serialized into Wave 3 (M2)
  for implementation; this lane lands no endpoint code.

## 7. Red lines

1. **Land files only, never execution**: skills land as text; installation = copying files into the skill directory;
   never auto-execute, never pull down remote code, never introduce app-store-style auto-installation.
2. **Trusted sources read-only**: skills and the catalog are read only from the official repo; the existing red line of
   permanently disabling community MCPs carries over to the skills surface.
3. **Zero credential echo**: skill copy mentions only slot names and `len > 0`; the catalog holds no values, no
   handles, no local paths (the machine gate proves 0 hits).
4. **Approval boundaries untouched**: actions that spend money, publish externally, send outbound messages, or change
   credentials still go through the human approval gate; credential slots in `requires` are filled in only by the user.
5. **Private repo = public-release desensitization standard**: per the established policy of 2026-10-25, private-repo
   content is also desensitized to the public-release standard and can be adjudicated to go public at any time.

## 8. Acceptance gates for this car

*Development-repository only: `deploy/check_publish.py` is release tooling of the development checkout,
never part of a user install.*

```bash
cd aidog-skills && python3 tools/lint.py   # standalone repo: === 300/300 PASS === (including 0 leak hits)
python3 tmp/e2e_skills_catalog.py  # catalog schema + page rendering + site gate regression (PASS n/n)
node tmp/shoot_site.js             # 62 (existing site gate, must not be broken by the new page)
node tmp/e2e_site_redesign.js      # 59 (existing site gate)
python3 tests/source_lang_check.py # 0 violations (this lane added no .py sources)
python3 deploy/check_publish.py    # READY(0)
```

E2E assertion surface: catalog required fields present, categories valid, no empty entries, 0 leak hits; the page has
**0 horizontal overflow** at both the 1440/390 viewports, zero pageerror, a working bilingual toggle, and rendered entry count =
catalog entry count; the existing site gates 62/60 stay green.
