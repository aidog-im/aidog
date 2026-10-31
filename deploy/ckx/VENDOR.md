# Vendored CKX agent tools (cryptokey.im)

Third-party code vendored verbatim under `deploy/ckx/` so the agent backup
path (CKX-P2, `backup_run`) never fetches code from the network at runtime.

| File | Source URL | Downloaded | Bytes | sha256 |
|---|---|---|---|---|
| `ckx.mjs` | `https://cryptokey.im/ckx/tools/ckx.mjs` | 2026-10-30 | 56602 | `be4b863775d56fd370803b3af9bb6f5e1a193dffdd93b671f60875f0d77c53bf` |
| `ckx-mcp.mjs` | `https://cryptokey.im/ckx/tools/ckx-mcp.mjs` | 2026-10-30 | 50810 | `281695ff6c60fddbe6d47dc1aeab2721914d033663fc03a7683db64deb435ff7` |

- **License**: Apache-2.0 (their published distribution states it for the
  CKX SDK package; the agent tooling is distributed the same way). Zero
  runtime dependencies, single-file ESM.
- **Versions**: CLI `ckx.mjs --version` = `0.1.0`; the bundled SDK reports
  `sdkVersion 0.6.0` from its health payload.
- **Runtime requirement**: Node.js **>= 20** (single-file Node ESM). Our code
  probes `node --version` before ever invoking these files and degrades
  honestly to the manual browser card (CKX-P1) when the probe fails.
- **Source URLs are unversioned** — they change on an upstream release.
  Re-vendor by re-downloading and recording a NEW sha256 + date in this
  table; never silently overwrite (see `THIRD_PARTY` provenance discipline).

## Why these two files

- `ckx.mjs` — CLI: `encrypt`, `decrypt`, `meta`, `verify`, `estimate`,
  `capabilities`. Passwords are accepted **only** via `--password-env`,
  `--password-file` or `--password-stdin` — **never `argv`** — so a process
  listing never shows the passphrase. Our `mycore/ckxbackup.py` follows that
  contract exactly: the passphrase goes into the child environment only and
  is never interpolated into a command line.
- `ckx-mcp.mjs` — MCP stdio server exposing `ckx_encrypt`, `ckx_decrypt`,
  `ckx_read_meta`, `ckx_estimate`, `ckx_capabilities`,
  `ckx_verify_integrity`, plus the recipient-keypair family
  (`ckx_generate_recipient_keypair`, `ckx_encrypt_to`, `ckx_decrypt_as`,
  `ckx_recipient_public_key`). The passphrase is a `passwordEnv` tool
  argument — the NAME of an environment variable, never the value.

## Network posture (what actually leaves this machine)

- Local `encrypt` / `decrypt` / `verify` run **client-side and offline**: the
  `.ckx` container is self-contained. Their health payload lists the
  *server-assisted* feature set as `register`, `capability`, `license`,
  `usage`, `audit`, `schema` — none of which is our backup path. A vendor
  outage therefore never blocks a backup or a restore.
- `GET /v1/ckx/usage` answers 405 and `GET /v1/ckx/audit` answers 401 for an
  unauthenticated caller, so **their free-plan call quota (1000 calls) cannot
  be read from here**. Our quota meter is therefore a **conservative proxy
  we keep ourselves**: one counted call per *initiated* encrypt in
  `backup_run`, warn at 800, refuse at 1000, resettable by the operator.
  The upstream billing record remains authoritative; if ours disagrees,
  theirs wins and ours is the one to reset.

## Trust model

These files run as ordinary subprocesses of this product with no extra
privilege. What they see is decided by us:

- the plaintext only ever exists in a `0600` temp file that is deleted in a
  `finally:` block (plus an equally short-lived decrypt file during the
  round-trip check);
- the passphrase only ever exists in the child process environment;
- the tool output we consume is metadata only — file name, size, sha256,
  verified/roundtrip booleans. Payload bytes and the passphrase are never
  echoed into a result string, a log line or the model context.
