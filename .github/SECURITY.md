# Security Policy

## Reporting a vulnerability

Security issues must **not** be discussed publicly — not in Issues, not in
Discussions, not in chat. Report them privately through one of these channels:

1. **GitHub Security Advisories** (preferred): open a *Security advisory* for
   this repository via the *Security* tab → *Report a vulnerability*. This
   creates a private thread visible only to the maintainers.
2. **Private message to the maintainers**: if the advisory form is unavailable,
   contact the maintainers privately via GitHub (their profile's contact
   options or the organization's private contact channel). Do not post the
   details publicly "just to get attention" — private first, always.

Please include, when possible:

- the affected component and version (release tag or build identifier);
- a description of the issue and its impact;
- minimal reproduction steps or a proof of concept;
- any known workarounds.

## What to expect

- **Acknowledgement** within a few working days of the report.
- **Assessment** with a status update once the issue is reproduced or triaged.
- **Fix and credit**: coordinated disclosure by default; the timeline is
  agreed with the reporter. Fixes land through the normal review gates
  (see [CONTRIBUTING.md](CONTRIBUTING.md)) and are announced in the release
  notes once a patched version ships.
- Reports that do not reproduce or are out of scope get a written
  explanation rather than silence.

## Scope

In scope: vulnerabilities in this project's own code, default configuration,
and shipped scripts/installers — for example injection, path traversal,
credential exposure, authentication or authorization bypass, and unsafe
deserialization.

Out of scope: issues in third-party dependencies (report them upstream),
social-engineering attacks, and vulnerability reports without a demonstrable
impact.

## Credentials and data

Never include real credentials, tokens, API keys, private conversation logs, or
personal data in a report — redact them before attaching examples. If you
believe a credential has been exposed, rotate it immediately; opening a
security advisory is not a substitute for rotation.

## Supported versions

Security fixes are applied to the latest released version. If an older release
is affected, upgrade first; backports are evaluated case by case.
