# SOURCE — writing-plans

Provenance record for `SKILL.md` in this directory (traceability, not a
product brand surface).

- **Upstream project:** `obra/superpowers` (github.com/obra/superpowers)
- **Upstream file:** `skills/writing-plans/SKILL.md`
- **Raw URL retrieved:** `https://raw.githubusercontent.com/obra/superpowers/main/skills/writing-plans/SKILL.md`
  (fetched 2026-10-22)
- **License:** MIT — "Copyright (c) 2025 Jesse Vincent". Full license text:
  `https://github.com/obra/superpowers/blob/main/LICENSE`. The MIT license
  requires that this copyright notice and permission notice be included in
  copies or substantial portions of the Software; this file serves as that
  notice for the adapted SKILL.md beside it.

## Adaptation notes (what changed vs. upstream)

1. Frontmatter normalized to this repository's skill convention
   (`name` + one-line `description`, generator-owned format parsed by
   `lib/skill_route.py` and `mycore/evolution.py`).
2. All references to upstream-only skills and execution helpers (worktree
   helper, subagent-execution and native-execution skill invocations) were
   rewritten as generic instructions that work in this repository. No
   third-party AI product names or product-specific commands remain in
   `SKILL.md`.
3. Default plan save path changed from the upstream project's directory
   convention to this repository's `docs/plans/`.
4. Otherwise unchanged: task right-sizing, step granularity, header
   template, task structure, self-review and execution-handoff content are
   the upstream author's work; see the license above.
