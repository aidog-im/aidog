# Discussions

This repository uses **GitHub Discussions** as the public place to talk about
the product: report odd behaviour, propose ideas, ask usage questions, show
what you built, or just talk. Threads are mirrored into the product console's
Community panel through an anonymous read of this page, so keep titles and
categories meaningful — they are what other users see first.

By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Categories

| Category | Use it for |
| --- | --- |
| 🐛 **Bugs** | Something in the product does not work as documented or as expected. |
| 💡 **Ideas** | Feature requests, UX proposals, and product direction discussion. |
| ❓ **Q&A** | "How do I …?" questions about installing, configuring, or operating the product. |
| 📣 **Show and tell** | Setups, integrations, skills, and results you want to share. |
| 🤝 **General** | Everything else that does not fit the categories above. |

Category names may be localized by GitHub's own labels; the mapping is
one-to-one with the five purposes above.

## Reporting a bug

Bugs belong in a **🐛 Bugs** discussion using the template below. Reports that
skip the template get a polite request to fill it in before triage can start.

Required information:

1. **Version** — the exact release tag or `aidog --version` output.
2. **Steps to reproduce** — numbered, minimal, copy-pasteable.
3. **Expected vs actual** — what you expected, and what happened instead.
4. **Environment** — OS and version, install method (package, script, source),
   and any relevant configuration **with all credentials removed**.

> 🔒 **Never post credentials, tokens, API keys, private conversation logs, or
> personal data in a discussion.** Discussions are public. Security issues go
> through [SECURITY.md](SECURITY.md) instead — privately, never here.

## Triage commitment

- A maintainer performs a first pass over new discussions **once per working
  session** (at least once per working day while a release cycle is active).
- A **confirmed product defect** is labelled and enters the **current
  iteration**; if it cannot be fixed in the current iteration it is recorded
  with a reason and an owner-visible milestone.
- Questions and ideas get a reply or a duplicate-link within the same first
  pass whenever feasible.
- Stale threads with no response after 30 days may be closed with a pointer to
  the relevant documentation; reopening is always allowed.

## Confidentiality and security

- Anything that could be exploited, or that exposes user data, must **not** be
  discussed publicly. Follow [SECURITY.md](SECURITY.md) and report it through
  a private channel (GitHub Security Advisories or a private message to the
  maintainers).
- Internal planning documents, unreleased roadmap details, and credentials
  stay out of discussions by default. If in doubt, open a private report first
  and mention it in the public thread afterwards.

## Contributing code

See [CONTRIBUTING.md](CONTRIBUTING.md) for the pull-request workflow and the
release gates every change must pass.
