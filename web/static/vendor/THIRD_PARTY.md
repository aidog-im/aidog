# Vendored third-party components

Nothing in this directory is written by this repository. Files are shipped
byte-exact from the upstream release; they are never edited, reformatted or
re-minified locally (the SRI hashes below would stop matching).

## @cryptokey/ckx — CKX client SDK

| Fact | Value |
|---|---|
| Package | `@cryptokey/ckx` |
| Version | **0.6.0** (formatVersion 1) |
| Upstream | `https://cryptokey.im/ckx/sdk/ckx-sdk-latest.zip` + `MANIFEST.json` |
| Files here | `ckx.umd.js` (UMD build, 39173 bytes) · `LICENSE` (Apache-2.0) |
| SRI (sha384, from the upstream manifest) | `ckx.umd.js` = `sha384-EaRGwSoY1maYSfhhg6VljHaz4vPW5TGJuZ3RMxo8wlWna8lKSeZc8V4/IjzvFAzX` |
| License | Apache License 2.0 (see `LICENSE`); the CryptoKey name, logo and trademarks are NOT licensed under it |
| Used by | the console's **Backup & Restore** card (`GET /api/backup/export` → encrypt → `POST /api/backup/restore`), loaded lazily on first use; the same bytes are exercised by `tests/ckx_roundtrip.mjs` under Node 18+ |

## Verification (re-run after any upgrade)

```bash
python3 - <<'PY'
import base64, hashlib, pathlib
want = "sha384-EaRGwSoY1maYSfhhg6VljHaz4vPW5TGJuZ3RMxo8wlWna8lKSeZc8V4/IjzvFAzX"
got = "sha384-" + base64.b64encode(
    hashlib.sha384(pathlib.Path("web/static/vendor/ckx.umd.js").read_bytes()).digest()).decode()
print("MATCH" if got == want else f"MISMATCH {got}")
PY
```

Upgrading = download the *versioned* artifact (`ckx-sdk-0.6.0.zip`, never the
`*-latest` URL alone), check every `MANIFEST.json → files[].sha384` entry, then
update this table and the SRI in one commit. The release endpoint
(`GET /v1/ckx/release → sdk.sri`) publishes the same hash.

This directory is excluded from the source-language lint (`vendor` in
`SKIP_DIR_NAMES`): it is third-party code carrying the upstream's own message
tables. The publish leak scan still runs over it like any other tracked file.
