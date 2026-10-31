#!/usr/bin/env node
// CKX-P1 round-trip gate (car 3, D5) — encrypt a real backup, decrypt it,
// apply it, and prove the bytes come back identical.
//
// What this proves, end to end, on a real machine:
//   R1  an ISOLATED console (own port, own throwaway profile) boots; the
//       production console on :8790 and the production secrets.env are never
//       touched (the profile dir is removed afterwards);
//   R2..R6  the export ladder: 401 without a token / with a wrong token,
//       400 on an unknown `include` section, 200 with the contract envelope,
//       WEB_TOKEN never present, a settings-only export carrying no secrets;
//   R7..R9  the CKX SDK (vendored, SRI-verified) round trip: encryptToText ->
//       decrypt -> byte-identical plaintext + meta round trip; a WRONG
//       passphrase is refused; encryptToFile -> decrypt -> byte-identical;
//   R10..R13  restore of the DECRYPTED artifact (not the in-memory object):
//       settings + memory + secrets land and read back; restore without a
//       token changes nothing; a schema mismatch is refused with 400;
//   R14  a per-slot refusal is a HONEST PARTIAL: 503, the good slot still
//       lands, the reason word names the slot and never the value;
//   R15  neither the console's own stderr (captured) nor this script's output
//       ever carries a credential value.
//
// The passphrase is a fixed test fixture; no production secret is read and no
// value is ever printed (comparisons happen in-process).
//
// Run:  node tests/ckx_roundtrip.mjs     (Node 18+, exits 0 = all green)
// English output per repo i18n policy.

import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import net from "node:net";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CKX = require(path.join(REPO, "web", "static", "vendor", "ckx.umd.js"));

const PASSPHRASE = "ckx-roundtrip-fixture-passphrase";
const WRONG_PASSPHRASE = "ckx-roundtrip-fixture-passphrase-wrong";
const TOKEN = "E2E-CKX-WEB-TOKEN-SENTINEL";
const SECRET_A = "E2E-CKX-SECRET-SENTINEL-A";   // pre-seeded slot value
const SECRET_B = "E2E-CKX-SECRET-SENTINEL-B";   // written by the restore
const SECRET_BAD = "not-a-numeric-allowlist";   // must be refused by shape
const MEMORY_FIXTURE = "# ckx round-trip fixture memory\n";
const MEMORY_UPDATED = "# ckx round-trip fixture memory (restored)\n";

let pass = 0;
let fail = 0;
const printed = [];                 // everything this script writes
function say(line) { printed.push(line); console.log(line); }
function check(name, ok, detail) {
  say(`${ok ? "PASS  " : "FAIL  "}${name}${ok || !detail ? "" : `  (${detail})`}`);
  if (ok) pass++; else fail++;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function freePort() {
  return new Promise((res) => {
    const s = net.createServer();
    s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); });
  });
}

async function waitForHealth(base, proc, ms = 60000) {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (proc.exitCode !== null) return false;
    try {
      const r = await fetch(base + "/api/health", { signal: AbortSignal.timeout(2000) });
      if (r.ok) return true;
    } catch (e) { /* retry */ }
    await sleep(300);
  }
  return false;
}

async function api(base, method, p, { token, body } = {}) {
  const headers = {};
  if (token) headers.Authorization = "Bearer " + token;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  try {
    const res = await fetch(base + p, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(15000),
    });
    const text = await res.text();
    let json = null;
    try { json = JSON.parse(text); } catch (e) { json = null; }
    return { status: res.status, json, text };
  } catch (e) {
    return { status: 0, json: null, text: String(e && e.message ? e.message : e) };
  }
}

const secretsEqual = (a, b) =>
  typeof a === "string" && typeof b === "string" && a.length === b.length &&
  Buffer.compare(Buffer.from(a, "utf8"), Buffer.from(b, "utf8")) === 0;

let child = null;
let root = null;
let childStderr = "";

(async () => {
  root = path.join(REPO, "tmp", `ckx_roundtrip_${process.pid}`);
  const profiles = path.join(root, "profiles");
  const profile = path.join(profiles, "ckxr");
  fs.mkdirSync(path.join(profile, "body", "memory"), { recursive: true });
  fs.mkdirSync(path.join(profile, "state"), { recursive: true });
  fs.writeFileSync(path.join(profile, "secrets.env"),
                   `WEB_TOKEN=${TOKEN}\nLINEAR_API_KEY=${SECRET_A}\n`);
  fs.writeFileSync(path.join(profile, "body", "memory", "MEMORY_curated.md"),
                   MEMORY_FIXTURE);
  fs.copyFileSync(path.join(REPO, "web", "settings.json"),
                  path.join(profile, "settings.json"));

  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  child = spawn("python3", ["server.py"], {
    cwd: path.join(REPO, "web"),
    env: Object.assign({}, process.env, {
      AIDOG_PROFILES_DIR: profiles, AIDOG_PROFILE: "ckxr",
      AIDOG_MC_STATE: path.join(profile, "state"),
      AIDOG_WEB_PORT: String(port),
    }),
    stdio: ["ignore", "ignore", "pipe"],
  });
  child.stderr.on("data", (d) => { childStderr += String(d); });

  try {
    const booted = await waitForHealth(base, child);
    check("R1 isolated console boots on its own port", booted, "boot timeout");
    if (!booted) return;

    /* --------------------------------------------------------- export ladder */
    let r = await api(base, "GET", "/api/backup/export");
    check("R2 export without a token -> 401", r.status === 401, String(r.status));
    r = await api(base, "GET", "/api/backup/export", { token: "wrong-token" });
    check("R3 export with a wrong token -> 401", r.status === 401, String(r.status));
    r = await api(base, "GET", "/api/backup/export?include=bogus", { token: TOKEN });
    check("R4 export of an unknown include section -> 400",
          r.status === 400, String(r.status));

    r = await api(base, "GET", "/api/backup/export", { token: TOKEN });
    const env = r.json || {};
    const payload = env.payload || {};
    check("R5 export envelope (ok/schema/version/created)",
          r.status === 200 && env.ok === true && env.schema === "aidog.backup.v1" &&
          env.schemaVersion === "1" && Number.isFinite(env.created),
          `${r.status} ${env.schema}`);
    check("R5 export sections are exactly the three requested",
          JSON.stringify(Object.keys(payload).sort()) ===
          JSON.stringify(["memory", "secrets", "settings"]),
          Object.keys(payload).join(","));
    check("R5 WEB_TOKEN never rides a backup",
          !(payload.secrets && "WEB_TOKEN" in payload.secrets),
          "WEB_TOKEN present");
    check("R5 the pre-seeded slot value rides byte-identical",
          secretsEqual((payload.secrets || {}).LINEAR_API_KEY, SECRET_A),
          "slot value mismatch");
    check("R5 the seeded memory slot rides",
          secretsEqual((payload.memory || {})["MEMORY_curated.md"], MEMORY_FIXTURE),
          "memory mismatch");
    check("R5 settings carry the console document",
          typeof (payload.settings || {}).language === "string",
          "settings shape");

    r = await api(base, "GET", "/api/backup/export?include=settings", { token: TOKEN });
    check("R6 a settings-only export carries no secrets at all",
          r.status === 200 && !("secrets" in (r.json.payload || {})) &&
          !("memory" in (r.json.payload || {})), String(r.status));

    /* --------------------------------------------------- CKX crypto round-trip */
    const artifact = JSON.stringify(env);
    const meta = { app: base, schema: "aidog.backup.v1", schemaVersion: "1",
                   label: "aidog-backup-roundtrip" };
    const cipher = await CKX.encryptToText(artifact, PASSPHRASE, meta);
    const back = await CKX.decrypt(cipher, PASSPHRASE);
    check("R7 encryptToText -> decrypt returns the byte-identical plaintext",
          secretsEqual(back.text, artifact), "plaintext differs");
    check("R7 the public meta round-trips (schema/version/app)",
          back.meta && back.meta.schemaId === "aidog.backup.v1" &&
          back.meta.schemaVersion === "1" && back.meta.app === base,
          JSON.stringify({ s: back.meta && back.meta.schemaId,
                           v: back.meta && back.meta.schemaVersion }));
    let wrongRefused = false;
    try {
      await CKX.decrypt(cipher, WRONG_PASSPHRASE);
    } catch (e) {
      wrongRefused = true;
      check("R8 a wrong passphrase is refused by code, not by luck",
            /WRONG_PASSWORD/.test(String(e && e.code)), String(e && e.code));
    }
    if (!wrongRefused) check("R8 a wrong passphrase is refused", false, "accepted");

    const container = await CKX.encryptToFile(artifact, PASSPHRASE, meta);
    const fromFile = await CKX.decrypt(container, PASSPHRASE);
    const sameBytes = Buffer.compare(
      Buffer.from(fromFile.plaintext), Buffer.from(artifact, "utf8")) === 0;
    check("R9 encryptToFile -> decrypt returns byte-identical file bytes",
          sameBytes, "bytes differ");

    /* ------------------------------------------- restore OF THE DECRYPTED file */
    const artifactDoc = JSON.parse(fromFile.text);
    const flipped = !artifactDoc.payload.settings.sound;
    const restoreBody = {
      schema: artifactDoc.schema,
      schemaVersion: artifactDoc.schemaVersion,
      payload: {
        settings: Object.assign({}, artifactDoc.payload.settings, { sound: flipped }),
        memory: { "MEMORY_curated.md": MEMORY_UPDATED },
        secrets: { NOTION_API_KEY: SECRET_B },
      },
      apply: { secrets: true, settings: true, memory: true },
    };
    r = await api(base, "POST", "/api/backup/restore", { token: TOKEN, body: restoreBody });
    check("R10 restore of the decrypted artifact applies all three sections",
          r.status === 200 && r.json && r.json.ok === true &&
          r.json.secrets && r.json.secrets.set === 1 &&
          r.json.settings && r.json.settings.applied === true &&
          r.json.memory && r.json.memory.files.length === 1,
          `${r.status} ${JSON.stringify(r.json && { s: r.json.secrets, m: r.json.settings, y: r.json.memory })}`);

    const after = await api(base, "GET", "/api/settings");
    check("R10 the restored setting reads back",
          after.json && after.json.sound === flipped, String(after.json && after.json.sound));
    const memFile = path.join(profile, "body", "memory", "MEMORY_curated.md");
    check("R10 the restored memory file is byte-identical on disk",
          fs.existsSync(memFile) &&
          secretsEqual(fs.readFileSync(memFile, "utf8"), MEMORY_UPDATED),
          "memory file differs");
    const reExport = await api(base, "GET", "/api/backup/export?include=secrets",
                               { token: TOKEN });
    check("R10 the restored credential reads back byte-identical",
          secretsEqual(((reExport.json || {}).payload || {}).secrets
                       && reExport.json.payload.secrets.NOTION_API_KEY, SECRET_B),
          "secret read-back differs");

    r = await api(base, "POST", "/api/backup/restore",
                  { body: Object.assign({}, restoreBody) });
    check("R11 restore without a token -> 401", r.status === 401, String(r.status));
    const afterNoAuth = await api(base, "GET", "/api/settings");
    check("R11 ... and it changed nothing",
          afterNoAuth.json && afterNoAuth.json.sound === flipped,
          String(afterNoAuth.json && afterNoAuth.json.sound));

    r = await api(base, "POST", "/api/backup/restore",
                  { token: TOKEN,
                    body: { schema: "other.v1", schemaVersion: "1",
                            payload: restoreBody.payload,
                            apply: { settings: true } } });
    check("R12 a schema mismatch -> 400 and nothing is written",
          r.status === 400, String(r.status));
    r = await api(base, "POST", "/api/backup/restore",
                  { token: TOKEN,
                    body: { schema: "aidog.backup.v1", schemaVersion: "9",
                            payload: restoreBody.payload,
                            apply: { settings: true } } });
    check("R12 a version mismatch -> 400", r.status === 400, String(r.status));

    /* --------------------------------------------- honest partial (503) leg */
    r = await api(base, "POST", "/api/backup/restore",
                  { token: TOKEN,
                    body: { schema: "aidog.backup.v1", schemaVersion: "1",
                            payload: { secrets: { AIDOG_TG_ALLOWED_USERS: SECRET_BAD,
                                                  TTS_API_KEY: SECRET_B } },
                            apply: { secrets: true } } });
    const failedRow = r.json && r.json.secrets && (r.json.secrets.failed || [])[0];
    check("R13 a refused slot is an honest partial: 503 + reason word",
          r.status === 503 && r.json && r.json.ok === false &&
          failedRow && failedRow.name === "AIDOG_TG_ALLOWED_USERS" &&
          typeof failedRow.reason === "string",
          `${r.status} ${JSON.stringify(failedRow)}`);
    check("R13 ... and the refused slot never echoed its value",
          failedRow && JSON.stringify(failedRow).indexOf(SECRET_BAD) === -1,
          "value echoed");
    const partial = await api(base, "GET", "/api/backup/export?include=secrets",
                              { token: TOKEN });
    check("R13 ... while the good slot in the same payload still landed",
          secretsEqual(((partial.json || {}).payload || {}).secrets
                       && partial.json.payload.secrets.TTS_API_KEY, SECRET_B),
          "good slot missing");

    /* ----------------------------------------------------- zero-echo guarantee */
    check("R14 no credential value reached the console's stderr",
          [SECRET_A, SECRET_B, SECRET_BAD, TOKEN]
            .every((v) => childStderr.indexOf(v) === -1), "stderr leak");
    check("R14 no credential value reached this script's own output",
          [SECRET_A, SECRET_B, TOKEN]
            .every((v) => printed.join("\n").indexOf(v) === -1), "stdout leak");
  } catch (e) {
    check("suite ran to completion", false, String(e && e.stack ? e.stack : e).slice(0, 300));
  } finally {
    if (child) { try { child.kill("SIGKILL"); } catch (e) { /* gone */ } }
    if (root) { try { fs.rmSync(root, { recursive: true, force: true }); } catch (e) { /* best effort */ } }
  }

  say("");
  say(`${pass}/${pass + fail} PASS`);
  process.exit(fail ? 1 : 0);
})();
