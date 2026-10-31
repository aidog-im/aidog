# Contributing

Thanks for helping improve this project. This document is the single source of
truth for how changes land: fork, branch, pass the gates, open a PR.

## Workflow

1. **Fork** the repository and create a feature branch from `main`:
   `git checkout -b feature/<short-description>`.
2. **Make the change** following the repository's conventions:
   - source code, comments, docstrings, and log messages in **English**;
   - user-facing strings go through the locale packs in `overlay/locales/`
     (`en` + `zh`, key parity enforced by lint — adding a language means adding
     a pack, never a code change);
   - zero credential echo: secrets are read from `secrets.env` only, never
     printed, logged, or serialized.
3. **Run the gates** (all must be green) — see [Running the gates](#running-the-gates).
4. **Open a pull request** with a clear description of the problem, the
   solution, and the gate results.
5. **Review** — a maintainer reviews for correctness, honesty of failure
   states (no fake success), and scope. Squash-fix commits as requested.

## Commit messages

- English, imperative mood, ≤ 72 characters for the subject line.
- Scope prefix when it helps: `console:`, `server:`, `mycore:`, `docs:`, `tests:`.
- Body explains *why*, not just *what*; reference the discussion or issue
  number when applicable (`Closes #123`).

## Licensing and sign-off

- By contributing you agree that your contribution is licensed under the same
  license as the repository (the license file published at the repository
  root), and you certify that you have the right to submit it.
- DCO-style sign-off is appreciated: add `Signed-off-by: Your Name <your@email>`
  (`git commit -s`) to commits you author.
- Do not include credentials, tokens, private keys, personal data, or
  proprietary third-party code in any commit.

## Running the gates

Run from the repository root, serially; every command must exit 0:

```bash
# 1. JavaScript syntax
node --check web/static/app.js

# 2. Source-language lint + locale pack key parity (en/zh)
python3 tests/source_lang_check.py

# 3. Console endpoint/auth/validation smoke (needs a running console on 8790)
python3 tests/web_console_p3.py

# 4. Core test suite
python3 -m mycore.tests.run

# 5. Frontend UI suites (Playwright)
# Provide PW_PATH=<path-to-playwright-node-module> if it is not resolvable
# via `require("playwright")` in your environment:
PW_PATH=/path/to/playwright node tmp/e2e_frontend_ui.js

# 6. Publish gate (desensitization + manifest)
python3 deploy/check_publish.py
```

A release additionally runs `python3 deploy/pre_release_audit.py`, which must
report **GO** before any tag is cut.

### Honesty rules the gates enforce

- Failure paths answer with an honest state (category reason, empty list,
  `unavailable`) — never a fake success and never a URL/exception/credential
  echo in an error payload.
- New UI copy goes through `t(key, fallback)` with `en` + `zh` pairs; no
  hardcoded user-visible strings in source files.
- Tests must be deterministic: no wall-clock or network flakiness in the
  required suites.
