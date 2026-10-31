#!/usr/bin/env node

// packages/ckx-mcp/src/mcp.mjs
import readline from "node:readline";
import fs from "node:fs";

// packages/ckx/dist/ckx.esm.js
var Pt = Object.defineProperty;
var vt = (e, t, r) => t in e ? Pt(e, t, { enumerable: true, configurable: true, writable: true, value: r }) : e[t] = r;
var $ = (e, t, r) => vt(e, typeof t != "symbol" ? t + "" : t, r);
var Ae = { t: (e) => e, changeLanguage: async () => {
}, on: () => {
}, off: () => {
}, language: "en", languages: ["en"], isInitialized: true, use: () => Ae, init: () => Ae };
var _ = Ae;
var Xt = (e) => {
  let t = new Uint8Array(e.length / 8);
  for (let n = 0; n < t.length; n++) {
    let a = e.slice(n * 8, (n + 1) * 8);
    t[n] = parseInt(a, 2);
  }
  return new TextDecoder().decode(t);
};
var Je = (e) => {
  let t = e.data, r = "";
  for (let u = 0; u < t.length; u += 4) {
    for (let p = 0; p < 3 && r.length < 64; p++) r += (t[u + p] & 1).toString();
    if (r.length >= 64) break;
  }
  let n = r.slice(0, 32), a = r.slice(32, 64);
  if (n !== "01010011010101000100010101000111") return null;
  let o = parseInt(a, 2);
  if (isNaN(o) || o <= 0 || o > t.length * 3) return null;
  let i = "", s = 64 + o, l = 0;
  for (let u = 0; u < t.length; u += 4) {
    for (let p = 0; p < 3 && (l >= 64 && l < s && (i += (t[u + p] & 1).toString()), l++, !(l >= s)); p++) ;
    if (l >= s) break;
  }
  try {
    return Xt(i);
  } catch (u) {
    return console.error("Failed to decode binary string", u), null;
  }
};
var y = "INVALID_PARAMS";
var Qe = Object.freeze({ CKX_NO_CARRIER: "ckx.err.no.carrier", CKX_PAYLOAD_TOO_LARGE: "ckx.err.payload.too.large", CKX_UNSUPPORTED_FORMAT_VERSION: "ckx.err.unsupported", CKX_WRONG_PASSWORD: "ckx.err.wrong.password", CKX_PASSWORD_REQUIRED: "ckx.err.password.required", CKX_RENAMED_CARRIER: "ckx.err.renamed.carrier", CKX_SET_NAME_MISMATCH: "ckx.err.set.name.mismatch", CKX_SCHEMA_MISMATCH: "ckx.err.schema.mismatch", CKX_CAPABILITY_REQUIRED: "ckx.err.capability", CKX_CARRIER_DAMAGED: "ckx.err.carrier.damaged", CKX_TIMEOUT: "ckx.err.timeout", INVALID_PARAMS: "ckx.err.params" });
var Lt = /* @__PURE__ */ new Set(["CKX_TIMEOUT"]);
var N = class extends Error {
  constructor(r, n, a) {
    super(n);
    $(this, "code");
    $(this, "messageKey");
    $(this, "detail");
    $(this, "retryable");
    this.name = "CkxError", this.code = r, this.messageKey = Qe[r] ?? String(r), this.detail = a, this.retryable = Lt.has(r);
  }
};
var c = (e, t, r) => new N(e, t, r);
var Hr = new Uint8Array([83, 65, 70, 69]);
var et = new Uint8Array([73, 77, 71, 86]);
var xe = 8;
var M = 8;
var Y = 16;
var x = 41;
var te = (e, t) => Math.floor(e * t * 3 / 8);
var z = (e, t) => te(e, t) - M;
var Ze = 8192;
var O = (e) => {
  let t = "";
  for (let r = 0; r < e.length; r += Ze) t += String.fromCharCode(...e.subarray(r, r + Ze));
  return btoa(t).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
};
var k = (e) => {
  let t = e.replace(/\s+/g, "").replace(/-/g, "+").replace(/_/g, "/"), r = t + "=".repeat((4 - t.length % 4) % 4), n;
  try {
    n = atob(r);
  } catch {
    throw c("CKX_NO_CARRIER", "carrier is not valid base64url", { field: "carrier", hint: "not-valid-base64url" });
  }
  let a = new Uint8Array(n.length);
  for (let o = 0; o < n.length; o++) a[o] = n.charCodeAt(o);
  return a;
};
var we = (e) => {
  let { width: t, height: r, data: n } = e, a = t * r, o = 0, i = 0, s = 0, l = true, u = new Uint8Array(M), p = null, m = 0;
  for (let d = 0; d < a; d++) {
    let h = d * 4;
    for (let f = 0; f < 3; f++) if (s = s << 1 | n[h + f] & 1, i++, i === 8) {
      if (i = 0, l) {
        if (u[o++] = s, o === M) {
          for (let b = 0; b < 4; b++) if (u[b] !== et[b]) return null;
          m = new DataView(u.buffer).getUint32(4, true);
          let w = z(t, r);
          if (m <= 0 || m > w) throw c("CKX_CARRIER_DAMAGED", `IMGV length ${m} out of range`, { field: "payload", limit: w, actual: m, hint: "imgv-length-out-of-range" });
          p = new Uint8Array(m), o = 0, l = false;
        }
      } else if (p && (p[o++] = s, o === m)) return p;
      s = 0;
    }
    if (!l && p && o === m) return p;
  }
  if (l) return null;
  throw c("CKX_CARRIER_DAMAGED", "IMGV payload truncated", { field: "payload", hint: "imgv-payload-truncated" });
};
var Ee = (e) => Je(e);
var T = (e) => e.length >= 4 && e[0] === 83 && e[1] === 65 && e[2] === 70 && e[3] === 69;
var be = { getExports: () => null, isReady: () => false, loadProModule: async () => false, shouldUseWasm: () => false, requireWasmInProduction: () => {
} };
var nt = (e) => {
  let t = e.reduce((a, o) => a + o.length, 0), r = new Uint8Array(t), n = 0;
  for (let a of e) r.set(a, n), n += a.length;
  return r;
};
var Re = async (e) => {
  let t = await crypto.subtle.digest("SHA-256", e);
  return new Uint8Array(t);
};
var it = async (e, t, r, n, a, o = 6e5) => {
  let i = new TextEncoder(), s = [];
  if (e && s.push(await Re(i.encode(e))), n && n.length > 0 && s.push(await Re(n)), a && s.push(await Re(i.encode(a))), s.length === 0) throw new Error(_.t("errors.utils.noKeyMaterial") || "No key material provided");
  let l = nt(s), u = await crypto.subtle.importKey("raw", l, "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey({ name: "PBKDF2", salt: t, iterations: o, hash: "SHA-256" }, u, { name: "AES-GCM", length: 256 }, false, r);
};
var S = new Uint8Array([83, 65, 70, 69]);
var at = 2;
var H = 3;
var Ft = 6e5;
var Vt = 1e5;
var ot = async (e, t, r, n = Ft, a, o) => {
  if (!(a || o)) {
    let f = be.getExports();
    if (f) try {
      return f.encrypt_binary_wasm(e, t, r, n);
    } catch (w) {
      throw console.error("WASM encryption failed:", w), new Error(_.t("errors.wasmCritical") || "WASM execution failed");
    }
    else throw new Error(_.t("errors.wasmSecurity") || "WASM security module required for encryption");
  }
  let s = crypto.getRandomValues(new Uint8Array(16)), l = crypto.getRandomValues(new Uint8Array(12)), u = await it(t, s, ["encrypt"], r, a, n), p = await crypto.subtle.encrypt({ name: "AES-GCM", iv: l }, u, e), m = new Uint8Array(37);
  m.set(S, 0);
  let d = o ? H : at;
  m[4] = d, new DataView(m.buffer).setUint32(5, n, true), m.set(s, 9), m.set(l, 25);
  let h = [m];
  if (d === H && o) {
    let f = JSON.stringify(o), w = new TextEncoder().encode(f), b = new Uint8Array(4);
    new DataView(b.buffer).setUint32(0, w.length, true), h.push(b), h.push(w);
  }
  return h.push(new Uint8Array(p)), nt(h);
};
var _e = (e) => {
  if (e.length < 37 || e[0] !== S[0] || e[1] !== S[1] || e[2] !== S[2] || e[3] !== S[3]) return null;
  if (e[4] === H) try {
    let r = new DataView(e.buffer, e.byteOffset, e.byteLength).getUint32(37, true);
    if (e.length < 41 + r) return null;
    let n = e.slice(41, 41 + r), a = new TextDecoder().decode(n);
    return JSON.parse(a);
  } catch (r) {
    return console.warn("Failed to parse metadata", r), null;
  }
  return null;
};
var st = async (e, t, r, n) => {
  if (e.length < 28) throw new Error(_.t("errors.utils.dataTooShort") || "Data too short");
  if (!(n || e.length >= 5 && e[4] === H)) {
    let m = be.getExports();
    if (m) try {
      return m.decrypt_binary_wasm(e, t, r);
    } catch (d) {
      console.error("WASM decryption failed:", d);
      {
        let f = (d?.message || d?.toString() || "").toLowerCase();
        throw f.includes("decrypt") || f.includes("authentication") || f.includes("tag") || f.includes("corrupted") || f.includes("invalid") || f.includes("password") ? new Error(_.t("errors.utils.decryptionFailed") || "Decryption failed: Wrong password, missing Key File, or corrupted data") : new Error(_.t("errors.wasmCritical") || "WASM execution failed");
      }
    }
    else throw new Error(_.t("errors.wasmSecurity") || "WASM security module required for decryption");
  }
  let o, i, s, l = Vt, u = false;
  if (e.length >= 37) {
    let m = e.slice(0, 4);
    m[0] === S[0] && m[1] === S[1] && m[2] === S[2] && m[3] === S[3] && (u = true);
  }
  if (u) {
    let m = e[4];
    l = new DataView(e.buffer, e.byteOffset, e.byteLength).getUint32(5, true), o = e.slice(9, 25), i = e.slice(25, 37);
    let d = 37;
    if (m === H) d = 41 + new DataView(e.buffer, e.byteOffset, e.byteLength).getUint32(37, true);
    else if (m !== at) throw new Error(_.t("errors.utils.unsupportedVersion", { version: m }) || `Unsupported encryption version: ${m}`);
    s = e.slice(d);
  } else o = e.slice(0, 16), i = e.slice(16, 28), s = e.slice(28);
  let p = await it(t, o, ["decrypt"], r, n, l);
  try {
    let m = await crypto.subtle.decrypt({ name: "AES-GCM", iv: i }, p, s);
    return new Uint8Array(m);
  } catch {
    throw new Error(_.t("errors.utils.decryptionFailed") || "Decryption failed: Wrong password, missing Key File, or corrupted data");
  }
};
var ct = (e) => String(e ?? "").normalize("NFC").toLowerCase();
var ne = "1";
var D = 4096;
var lt = 6e5;
var ie = 5 * 1024 * 1024;
var Se = "strict-name";
var Gt = "CKXF|fn|";
var ut = (e) => Gt + ct(e);
var ae = (e, t) => {
  let r = { ckx: ne, app: e.app, createdAt: (/* @__PURE__ */ new Date()).toISOString() };
  return e.schema && (r.schemaId = e.schema), e.schemaVersion && (r.schemaVersion = e.schemaVersion), e.label && (r.label = e.label), t.length && (r.ckx_caps = t), r;
};
var $t = new TextEncoder();
var W = (e) => {
  let t = $t.encode(JSON.stringify(e));
  if (t.length > D) throw c("CKX_PAYLOAD_TOO_LARGE", `meta ${t.length} B > ${D} B`, { field: "meta", limit: D, actual: t.length, hint: "shorten-label-or-schemaId" });
  return t;
};
var I = (e) => {
  if (!e || typeof e != "object") throw c("CKX_CARRIER_DAMAGED", "meta is not an object", { field: "meta" });
  let t = e;
  if (t.ckx !== ne) throw c("CKX_UNSUPPORTED_FORMAT_VERSION", `meta.ckx=${String(t.ckx)}`, { field: "formatVersion", actual: String(t.ckx), hint: `supported=${ne}` });
  if (typeof t.app != "string" || !t.app) throw c("CKX_CARRIER_DAMAGED", "meta.app missing", { field: "meta.app" });
  return t;
};
var oe = async (e, t, r, n = {}) => {
  if (!t) throw c("CKX_PASSWORD_REQUIRED", "password is empty", { field: "password" });
  let a;
  if (typeof n.strictName == "string" && n.strictName.length > 0) {
    a = ut(n.strictName);
    let l = Array.isArray(r.ckx_caps) ? r.ckx_caps.slice() : [];
    l.includes(Se) || l.push(Se), r.ckx_caps = l;
  }
  let o = W(r), i = await ot(e, t, void 0, lt, a, r);
  if (!T(i) || i[4] !== 3) throw c("CKX_CARRIER_DAMAGED", "produced container is not SAFE v3");
  let s = x + o.length + e.length + 16;
  if (i.length !== s) throw c("CKX_CARRIER_DAMAGED", `container length ${i.length} != ${s}`, { field: "container", limit: s, actual: i.length });
  return i;
};
var se = (e) => {
  if (e.length < x || !T(e) || e[4] !== 3) return null;
  let t = new DataView(e.buffer, e.byteOffset, e.byteLength).getUint32(37, true);
  if (e.length < x + t) return null;
  let r = _e(e);
  if (!r) return null;
  try {
    return I(r);
  } catch {
    return null;
  }
};
var pt = async (e, t, r = {}) => {
  if (!e || e.length === 0) throw c("CKX_NO_CARRIER", "empty carrier", { field: "carrier", actual: 0 });
  if (!t) throw c("CKX_PASSWORD_REQUIRED", "password is empty", { field: "password" });
  if (e.length < x) throw e.length < 5 || !T(e) ? c("CKX_NO_CARRIER", `carrier too short (${e.length} B)`, { field: "carrier", limit: x, actual: e.length, hint: "not-a-ckx-carrier" }) : c("CKX_CARRIER_DAMAGED", `truncated SAFE header (${e.length} B)`, { field: "carrier", limit: x, actual: e.length, hint: "truncated-header" });
  if (!T(e)) throw c("CKX_NO_CARRIER", "missing SAFE magic", { field: "carrier", hint: "not-a-ckx-carrier" });
  let n = e[4];
  if (n !== 3) throw c("CKX_UNSUPPORTED_FORMAT_VERSION", `SAFE version ${n}`, { field: "formatVersion", actual: n, hint: "supported=3" });
  let a = new DataView(e.buffer, e.byteOffset, e.byteLength).getUint32(37, true);
  if (x + a + 16 > e.length) throw c("CKX_CARRIER_DAMAGED", `metaLen ${a} exceeds carrier length`, { field: "metaLen", limit: Math.max(0, e.length - x - 16), actual: a, hint: "truncated-meta" });
  let o = se(e);
  if (!o) {
    let l = _e(e);
    throw l && I(l), c("CKX_CARRIER_DAMAGED", "meta is unreadable", { field: "meta", hint: "unreadable-meta" });
  }
  if (r.schema && o.schemaId !== r.schema) throw c("CKX_SCHEMA_MISMATCH", `expected schemaId=${r.schema}, got ${String(o.schemaId)}`, { field: "schemaId", limit: void 0, actual: String(o.schemaId ?? ""), hint: `expected=${r.schema}` });
  let i;
  if (Array.isArray(o.ckx_caps) && o.ckx_caps.includes(Se)) {
    if (typeof r.strictName != "string" || !r.strictName) throw c("CKX_SET_NAME_MISMATCH", "strict matching carrier requires the original filename", { field: "filename", hint: "original-filename-required" });
    i = ut(r.strictName);
  }
  let s;
  try {
    s = await st(e, t, void 0, i);
  } catch {
    throw c("CKX_WRONG_PASSWORD", "decryption failed", { hint: "wrong-password-or-altered-carrier" });
  }
  return { plaintext: s, meta: o };
};
var ce = (e) => {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(e);
  } catch {
    return null;
  }
};
var j = "CKVIDEOVAULT";
var mt = new TextEncoder().encode(j);
var Pe = new Uint8Array([67, 75, 88, 65]);
var F = 1;
var E = 32;
var ve = 12;
var Be = 12;
var ue = 32;
var Xe = 48;
var Ne = 37;
var q = D;
var U = 101;
var P = U + Be;
var ht = "asym";
var gt = "1";
var Te = 16;
var zt = new TextEncoder().encode("CKX-ASYM-v1|wrap");
var L = 4;
var Ue = 5;
var Oe = 37;
var De = 49;
var Le = 97;
var J = 101;
var Ht = (e) => e && typeof e.name == "string" ? e.name : "";
var At = (e) => e && typeof e.message == "string" ? e.message : "";
var V = (e) => {
  let t = Ht(e);
  return !!(t === "NotSupportedError" || t === "TypeError" && /not supported|unsupported|unrecognized|unknown/i.test(At(e)));
};
var v = () => c("CKX_UNSUPPORTED_FORMAT_VERSION", "X25519 is not supported by this runtime", { hint: "x25519-unsupported" });
var xt = () => {
  let e = globalThis.crypto;
  if (!e || !e.subtle || typeof e.getRandomValues != "function") throw c("CKX_UNSUPPORTED_FORMAT_VERSION", "WebCrypto (crypto.subtle) is not available in this runtime", { hint: "webcrypto-unavailable" });
  return e;
};
var C = () => xt().subtle;
var Wt = new TextDecoder();
var jt = new TextEncoder();
var Ct = (e) => Wt.decode(e);
var wt = (e, t) => {
  let r = new Uint8Array(e.length + t.length);
  return r.set(e, 0), r.set(t, e.length), r;
};
var Me = (e) => {
  let t = new Uint8Array(e);
  return xt().getRandomValues(t), t;
};
var pe = (e, t) => {
  if (typeof e != "string" || e.length === 0) throw c(y, `${t} must be a non-empty base64url string`, { field: t });
  try {
    return k(e);
  } catch {
    throw c(y, `${t} is not valid base64url`, { field: t, hint: "invalid-base64url" });
  }
};
var qt = (e) => {
  let t = 0;
  for (let r = 0; r < e.length; r++) t |= e[r];
  return t === 0;
};
var kt = (e) => {
  if (e instanceof Uint8Array) return e;
  if (typeof e == "string") return k(e);
  throw c(y, "container must be a Uint8Array or a base64url string", { field: "container" });
};
var Jt = (e) => {
  if (typeof e == "string") return jt.encode(e);
  if (e instanceof Uint8Array) return e;
  throw c(y, "payload must be a string or Uint8Array", { field: "payload" });
};
var A = (e) => e;
var me = async (e, t) => {
  try {
    return await C().importKey("raw", A(e), { name: "X25519" }, true, []);
  } catch (r) {
    throw V(r) ? v() : t === "recipient" ? c(y, "recipient public key is not a valid X25519 public key", { field: "recipientPublicKey", hint: "invalid-x25519-public-key" }) : c("CKX_CARRIER_DAMAGED", "container ephemeral public key is not a valid X25519 public key", { field: "epk", hint: "invalid-ephemeral-public-key" });
  }
};
var Fe = async (e, t, r) => {
  let n;
  try {
    n = new Uint8Array(await C().deriveBits({ name: "X25519", public: t }, e, 256));
  } catch (a) {
    throw V(a) ? v() : r === "recipient" ? c(y, "recipient public key is a low-order point (X25519 derivation failed)", { field: "recipientPublicKey", hint: "low-order-point" }) : c("CKX_CARRIER_DAMAGED", "container ephemeral public key is a low-order point", { field: "epk", hint: "low-order-point" });
  }
  if (n.length !== E || qt(n)) throw r === "recipient" ? c(y, "recipient public key yields an all-zero shared secret (low-order point)", { field: "recipientPublicKey", hint: "low-order-point" }) : c("CKX_CARRIER_DAMAGED", "container ephemeral public key yields an all-zero shared secret", { field: "epk", hint: "low-order-point" });
  return n;
};
var Et = async (e) => {
  let t = new Uint8Array(32);
  t[0] = 9;
  let r = await me(t, "ephemeral");
  return Fe(e, r, "ephemeral");
};
var le = (e, t) => C().importKey("raw", A(e), { name: "AES-GCM" }, false, t);
var bt = async (e, t) => {
  let r = await C().importKey("raw", A(e), { name: "HKDF" }, false, ["deriveBits"]), n = new Uint8Array(await C().deriveBits({ name: "HKDF", hash: "SHA-256", salt: A(t), info: A(zt) }, r, 256));
  if (n.length !== ue) throw v();
  return n;
};
var Rt = async () => {
  try {
    return await C().generateKey({ name: "X25519" }, true, ["deriveBits"]);
  } catch (e) {
    throw V(e) ? v() : c(y, `X25519 key generation failed: ${At(e)}`, { field: "keypair" });
  }
};
var Qt = async () => {
  let e = await Rt();
  return { publicKey: await _t(e.publicKey), privateKey: await St(e.privateKey) };
};
var _t = async (e) => {
  try {
    return O(new Uint8Array(await C().exportKey("raw", e)));
  } catch (t) {
    throw V(t) ? v() : c(y, "unable to export X25519 public key (not extractable or not a public key)", { field: "publicKey" });
  }
};
var St = async (e) => {
  try {
    return O(new Uint8Array(await C().exportKey("pkcs8", e)));
  } catch (t) {
    throw V(t) ? v() : c(y, "unable to export X25519 private key (not extractable or not a private key)", { field: "privateKey" });
  }
};
var Ve = async (e) => {
  let t = pe(e, "recipientPrivateKey");
  try {
    return await C().importKey("pkcs8", A(t), { name: "X25519" }, true, ["deriveBits"]);
  } catch (r) {
    throw V(r) ? v() : c(y, "recipient private key is not a valid X25519 PKCS#8 key", { field: "recipientPrivateKey", hint: "invalid-x25519-private-key" });
  }
};
var er = async (e) => {
  let t = await Ve(e);
  return O(await Et(t));
};
var tr = async (e) => {
  let t = pe(e, "recipientPublicKey");
  if (t.length !== E) throw c(y, `recipient public key must be ${E} bytes (raw X25519), got ${t.length}`, { field: "recipientPublicKey", limit: E, actual: t.length });
  let r = new Uint8Array(await C().digest("SHA-256", A(t))), n = "";
  for (let a = 0; a < 8; a++) n += r[a].toString(16).padStart(2, "0");
  return n;
};
var rr = (e) => {
  if (!e || typeof e != "object") throw c(y, "meta must be an object with a non-empty app", { field: "meta" });
  if (typeof e.app != "string" || e.app.trim() === "") throw c(y, "meta.app must be a non-empty string (your site origin)", { field: "meta.app" });
  let t = ae({ app: e.app, schema: e.schema ?? null, schemaVersion: e.schemaVersion ?? null, label: e.label ?? null }, e.caps ?? []);
  return e.createdAt && (t.createdAt = e.createdAt), t[ht] = gt, I(t), t;
};
var nr = async (e, t, r) => {
  let n = Jt(e), a = pe(t, "recipientPublicKey");
  if (a.length !== E) throw c(y, `recipient public key must be ${E} bytes (raw X25519), got ${a.length}`, { field: "recipientPublicKey", limit: E, actual: a.length });
  let o = rr(r), i = W(o), s = await me(a, "recipient"), l = await Rt(), u = new Uint8Array(await C().exportKey("raw", l.publicKey));
  if (u.length !== E) throw c(y, "ephemeral public key has unexpected length", { field: "epk" });
  let p = await Fe(l.privateKey, s, "recipient"), m = await bt(p, wt(u, a)), d = await le(m, ["encrypt"]), h = Me(ue), f = await le(h, ["encrypt"]), w = Me(ve), b = Me(Be), g = new Uint8Array(P + i.length);
  g.set(Pe, 0), g[L] = F, g.set(u, Ue), g.set(w, Oe), new DataView(g.buffer, g.byteOffset, g.byteLength).setUint32(Le, i.length, true), g.set(i, J), g.set(b, U + i.length);
  let he = g.subarray(0, Ne), Z = new Uint8Array(await C().encrypt({ name: "AES-GCM", iv: A(w), additionalData: A(he) }, d, A(h)));
  if (Z.length !== Xe) throw c("CKX_CARRIER_DAMAGED", "wrapped CEK has unexpected length", { field: "wrappedCEK" });
  g.set(Z, De);
  let ge = g, ee = new Uint8Array(await C().encrypt({ name: "AES-GCM", iv: A(b), additionalData: A(ge) }, f, A(n))), R = new Uint8Array(g.length + ee.length);
  if (R.set(g, 0), R.set(ee, g.length), !B(R) || R[L] !== F) throw c("CKX_CARRIER_DAMAGED", "produced container is not CKXA v1");
  let X = P + i.length + n.length + Te;
  if (R.length !== X) throw c("CKX_CARRIER_DAMAGED", `container length ${R.length} != ${X}`, { field: "container", limit: X, actual: R.length });
  return R;
};
var B = (e) => e.length >= Pe.length && e[0] === 67 && e[1] === 75 && e[2] === 88 && e[3] === 65;
var ye = (e) => {
  let t;
  try {
    t = kt(e);
  } catch {
    return null;
  }
  if (t.length < U || !B(t) || t[L] !== F) return null;
  let r = new DataView(t.buffer, t.byteOffset, t.byteLength).getUint32(Le, true);
  if (r === 0 || r > q || U + r > t.length) return null;
  try {
    return I(JSON.parse(Ct(t.subarray(J, J + r))));
  } catch {
    return null;
  }
};
var ir = async (e, t, r = {}) => {
  let n = kt(e);
  if (!n || n.length === 0) throw c("CKX_NO_CARRIER", "empty carrier", { field: "carrier", actual: 0 });
  if (n.length < 5 || !B(n)) throw c("CKX_NO_CARRIER", "missing CKXA magic", { field: "carrier", hint: "not-a-ckx-carrier" });
  if (n.length < U) throw c("CKX_CARRIER_DAMAGED", `truncated CKXA header (${n.length} B)`, { field: "carrier", limit: U, actual: n.length, hint: "truncated-header" });
  if (n[L] !== F) throw c("CKX_UNSUPPORTED_FORMAT_VERSION", `CKXA version ${n[L]}`, { field: "formatVersion", actual: n[L], hint: `supported=${F}` });
  let o = new DataView(n.buffer, n.byteOffset, n.byteLength).getUint32(Le, true);
  if (o > q) throw c("CKX_CARRIER_DAMAGED", `metaLen ${o} exceeds ${q} B`, { field: "metaLen", limit: q, actual: o, hint: "meta-too-large" });
  if (P + o + Te > n.length) throw c("CKX_CARRIER_DAMAGED", `metaLen ${o} exceeds carrier length`, { field: "metaLen", limit: Math.max(0, n.length - P - Te), actual: o, hint: "truncated-meta" });
  let i;
  try {
    i = I(JSON.parse(Ct(n.subarray(J, J + o))));
  } catch (X) {
    throw X?.code === "CKX_UNSUPPORTED_FORMAT_VERSION" ? X : c("CKX_CARRIER_DAMAGED", "meta is unreadable", { field: "meta", hint: "unreadable-meta" });
  }
  if (r.schema && i.schemaId !== r.schema) throw c("CKX_SCHEMA_MISMATCH", `expected schemaId=${r.schema}, got ${String(i.schemaId)}`, { field: "schemaId", actual: String(i.schemaId ?? ""), hint: `expected=${r.schema}` });
  let s = await Ve(t), l = await Et(s), u = n.subarray(Ue, Ue + E), p = await me(u, "ephemeral"), m = await Fe(s, p, "ephemeral"), d = await bt(m, wt(u, l)), h = await le(d, ["decrypt"]), f = n.subarray(Oe, Oe + ve), w = n.subarray(De, De + Xe), b = n.subarray(0, Ne), g;
  try {
    g = new Uint8Array(await C().decrypt({ name: "AES-GCM", iv: A(f), additionalData: A(b) }, h, A(w)));
  } catch {
    throw c("CKX_WRONG_PASSWORD", "decryption failed", { hint: "wrong-recipient-key-or-altered-carrier" });
  }
  if (g.length !== ue) throw c("CKX_CARRIER_DAMAGED", "unwrapped CEK has unexpected length", { field: "cek" });
  let he = n.subarray(U + o, P + o), Z = n.subarray(0, P + o), ge = n.subarray(P + o), ee = await le(g, ["decrypt"]), R;
  try {
    R = new Uint8Array(await C().decrypt({ name: "AES-GCM", iv: A(he), additionalData: A(Z) }, ee, A(ge)));
  } catch {
    throw c("CKX_WRONG_PASSWORD", "decryption failed", { hint: "wrong-recipient-key-or-altered-carrier" });
  }
  return { plaintext: R, text: ce(R), meta: i };
};
var $e = Object.freeze(["text", "image", "file", "video"]);
var Kt = Object.freeze(["shard", "strictMatching", "bulk", "whiteLabel", "schema", "sla"]);
var ar = new Set($e);
var or = /* @__PURE__ */ new Set([...$e, ...Kt]);
var Ge = (e) => e;
var sr = "https://cryptokey.im";
var cr = 8e3;
var de = (e) => {
  let t = [];
  if (Array.isArray(e)) for (let r of e) {
    let n = String(r);
    or.has(n) && !t.includes(n) && t.push(n);
  }
  return t.sort();
};
var lr = (e) => (typeof e == "string" && e.trim() ? e.trim() : sr).replace(/\/+$/, "");
var ur = (e) => typeof e == "function" ? e : typeof fetch == "function" ? fetch : null;
var Mt = async (e, t, r, n) => {
  let a, o;
  try {
    typeof AbortController == "function" && (o = new AbortController(), a = setTimeout(() => o?.abort(), n));
    let i = await e(t, o ? { ...r, signal: o.signal } : r);
    if (!i || !i.ok) return null;
    let s = await i.json();
    return s && typeof s == "object" ? s : null;
  } catch {
    return null;
  } finally {
    a !== void 0 && clearTimeout(a);
  }
};
var Q = /* @__PURE__ */ new Map();
var It = async (e) => {
  try {
    let t = k(e);
    return t.length !== 32 ? null : await crypto.subtle.importKey("raw", Ge(t), { name: "Ed25519" }, false, ["verify"]);
  } catch {
    return null;
  }
};
var pr = async (e, t, r, n) => {
  if (typeof t == "string" && t.trim()) {
    let l = `inline:${t.trim()}`, u = Q.get(l);
    if (u) return u;
    let p = await It(t.trim());
    return p && Q.set(l, p), p;
  }
  let a = Q.get(e);
  if (a) return a;
  if (!r) return null;
  let o = await Mt(r, `${e}/v1/ckx/release`, { method: "GET" }, n), i = o && typeof o.licensePublicKey == "string" ? o.licensePublicKey : null;
  if (!i) return null;
  let s = await It(i);
  return s && Q.set(e, s), s;
};
var mr = async (e, t, r, n) => {
  let a = e.split(".");
  if (a.length !== 3 || a[0] !== "ckx1") return null;
  let o;
  try {
    o = k(a[2]);
  } catch {
    return null;
  }
  let i = false;
  try {
    i = await crypto.subtle.verify({ name: "Ed25519" }, t, Ge(o), Ge(new TextEncoder().encode(a[1])));
  } catch {
    return null;
  }
  if (!i) return null;
  let s;
  try {
    s = JSON.parse(new TextDecoder().decode(k(a[1])));
  } catch {
    return null;
  }
  if (!s || typeof s != "object") return null;
  let l = s;
  if (l.v !== 1 || typeof l.sub != "string" || l.sub !== r) return null;
  let u = typeof l.exp == "number" ? l.exp : null;
  return u !== null && n >= u ? null : { features: de(l.features), expiresAt: u };
};
var yr = async (e, t, r, n, a) => {
  let o = { app: t };
  r && (o.token = r);
  let i = await Mt(n, `${e}/v1/ckx/license/verify`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(o) }, a);
  if (!i || i.ok !== true) return null;
  let s = typeof i.source == "string" ? i.source : "none", l = s === "token" || s === "kv" || s === "revoked" || s === "none" ? s : "none", u = { pro: i.pro === true, features: de(i.features), expiresAt: typeof i.expiresAt == "number" ? i.expiresAt : null, source: l };
  return typeof i.token == "string" && i.token && (u.token = i.token), u;
};
var dr = async (e) => {
  if (!e || typeof e != "object") throw c(y, "ckxLicense.verify expects an options object with app");
  let t = typeof e.app == "string" ? e.app.trim() : "";
  if (!t) throw c(y, "ckxLicense.verify requires a non-empty app identifier");
  let r = Date.now(), n = typeof e.timeoutMs == "number" && Number.isFinite(e.timeoutMs) && e.timeoutMs > 0 ? e.timeoutMs : cr, a = lr(e.endpoint), o = typeof e.token == "string" && e.token.trim() ? e.token.trim() : null, i = ur(e.fetch), s = null;
  if (o) {
    let l = await pr(a, e.publicKey, i, n);
    if (l) {
      let u = await mr(o, l, t, r);
      u && (s = { pro: true, features: u.features, expiresAt: u.expiresAt, source: "token" });
    }
  }
  if (e.online !== false && i) {
    let l = await yr(a, t, o, i, n);
    if (l) return l.pro === true && (l.expiresAt === null || l.expiresAt > r) ? { pro: true, features: l.features, expiresAt: l.expiresAt ?? s?.expiresAt ?? null, source: s ? "token" : "kv", ...l.token ? { token: l.token } : {} } : { pro: false, features: [], expiresAt: l.expiresAt, source: l.source === "none" ? "none" : "revoked" };
  }
  return s || { pro: false, features: [], expiresAt: null, source: "none" };
};
var fr = (e) => Array.isArray(e) ? de(e) : e && typeof e == "object" && Array.isArray(e.features) ? de(e.features) : [];
var hr = (e, t) => {
  let r = new Set(fr(e)), n = Array.isArray(t) ? t : [t];
  return n.length === 0 ? true : n.every((a) => {
    let o = String(a);
    return ar.has(o) || r.has(o);
  });
};
var gr = { verify: dr, can: hr, freeFeatures: $e, proFeatures: Kt, clearCache: () => {
  Q.clear();
} };
var Ut = new TextEncoder();
var Mr = (e) => Ut.encode(e);
var Ot = (e) => {
  if (typeof e == "string") return Ut.encode(e);
  if (e instanceof Uint8Array) return e;
  throw c(y, "payload must be a string or Uint8Array");
};
var We = (e) => {
  if (!e || typeof e != "object") throw c(y, "meta must be an object with a non-empty app");
  if (typeof e.app != "string" || e.app.trim() === "") throw c(y, "meta.app must be a non-empty string (your site origin)");
  let t = ae({ app: e.app, schema: e.schema ?? null, schemaVersion: e.schemaVersion ?? null, label: e.label ?? null }, e.caps ?? []);
  return e.createdAt && (t.createdAt = e.createdAt), I(t), t;
};
var fe = (e) => e instanceof Uint8Array;
var je = (e) => !!e && typeof e == "object" && "data" in e && "width" in e && "height" in e;
var Or = (e) => {
  if (typeof e == "number") {
    let n = Math.floor(e);
    if (!Number.isFinite(n) || n <= 0) throw c(y, `invalid carrier size ${String(e)}`);
    return { width: n, height: n };
  }
  let { width: t, height: r } = e;
  if (!Number.isInteger(t) || !Number.isInteger(r) || t <= 0 || r <= 0) throw c(y, `invalid carrier size ${t}\xD7${r}`);
  return { width: t, height: r };
};
var Dt = (e) => {
  if (typeof e == "string") return { container: k(e), medium: "text" };
  if (fe(e)) {
    if (e.length === 0) throw c("CKX_NO_CARRIER", "empty carrier", { field: "carrier", actual: 0 });
    if (!T(e)) throw c("CKX_NO_CARRIER", "bytes are not a CKX/SAFE container (PNG decoding is caller-side)", { field: "carrier", hint: "not-a-ckx-carrier" });
    return { container: e, medium: "file" };
  }
  if (je(e)) {
    let t = we(e);
    if (t) return { container: t, medium: "image-binary" };
    let r = Ee(e);
    if (typeof r == "string") return { container: k(r), medium: "image-text" };
    throw c("CKX_NO_CARRIER", "image carries no CKX payload", { field: "carrier", hint: "not-a-ckx-carrier" });
  }
  throw c(y, "unsupported carrier type");
};
var Rn = (e, t = {}) => {
  let { width: r, height: n } = Or(e), a = t.binary ?? false, o = a ? z(r, n) : te(r, n), i = a ? Math.max(0, o) : Math.max(0, Math.floor((o - xe) * 3 / 4)), s = Math.max(0, i - x - Y - Math.max(0, t.metaBytes ?? 0));
  return { medium: a ? "image-binary" : "image-text", capacityBytes: Math.max(0, o), maxContainerBytes: i, maxPayloadBytes: s };
};
var Sn = async (e, t, r) => {
  if (typeof e != "string") throw c(y, "encryptToText expects a string payload");
  let n = Mr(e);
  if (n.length > ie) throw c("CKX_PAYLOAD_TOO_LARGE", `text ${n.length} B > ${ie} B`, { field: "payload", limit: ie, actual: n.length, hint: "use-encrypt-to-file" });
  let a = await oe(n, t, We(r));
  return O(a);
};
var Dr = async (e, t, r) => oe(Ot(e), t, We(r));
var vr = async (e, t, r = {}) => {
  let { container: n, medium: a } = Dt(e), { plaintext: o, meta: i } = await pt(n, t, { schema: r.schema ?? null });
  return { plaintext: o, text: ce(o), meta: i, medium: a };
};
var Dn = (e) => {
  try {
    let { container: t } = Dt(e), r = se(t);
    if (r) return r;
    if (B(t)) return ye(t);
  } catch {
  }
  try {
    if (typeof e == "string") {
      let t = k(e);
      return B(t) ? ye(t) : null;
    }
    if (fe(e) && B(e)) return ye(e);
  } catch {
  }
  return null;
};
var Br = (e) => {
  if (typeof e == "string") try {
    let t = k(e);
    return { container: t, medium: "text", reason: t.length ? "ok" : "empty" };
  } catch {
    return { container: null, medium: "text", reason: "not-a-ckx-carrier" };
  }
  if (fe(e)) return { container: e, medium: "file", reason: e.length ? "ok" : "empty" };
  if (je(e)) {
    let t = we(e);
    if (t) return { container: t, medium: "image-binary", reason: t.length ? "ok" : "empty" };
    let r = Ee(e);
    if (typeof r == "string") try {
      let n = k(r);
      return { container: n, medium: "image-text", reason: n.length ? "ok" : "empty" };
    } catch {
      return { container: null, medium: "image-text", reason: "not-a-ckx-carrier" };
    }
    return { container: null, medium: null, reason: "not-a-ckx-carrier" };
  }
  return { container: null, medium: null, reason: "not-a-ckx-carrier" };
};
var Pn = (e) => {
  let t = (p, m, d = {}) => ({ ok: false, reason: p, medium: m, containerBytes: d.containerBytes ?? null, metaBytes: d.metaBytes ?? null, payloadBytes: d.payloadBytes ?? null, meta: d.meta ?? null }), r = Br(e);
  if (!r.container) return t(r.reason, r.medium);
  let n = r.container, a = r.medium, o = n.length;
  if (o === 0) return t("empty", a, { containerBytes: o });
  if (o < 5 || !T(n)) return t("not-a-ckx-carrier", a, { containerBytes: o });
  if (o < x) return t("truncated-header", a, { containerBytes: o });
  if (n[4] !== 3) return t("unsupported-format-version", a, { containerBytes: o });
  let s = new DataView(n.buffer, n.byteOffset, n.byteLength).getUint32(37, true);
  if (s > D) return t("meta-too-large", a, { containerBytes: o, metaBytes: s });
  if (x + s + Y > o) return t("truncated-meta", a, { containerBytes: o, metaBytes: s });
  let l;
  try {
    let p = n.subarray(x, x + s);
    l = I(JSON.parse(new TextDecoder().decode(p)));
  } catch (p) {
    let m = p && typeof p == "object" ? p.code : null;
    return t(m === "CKX_UNSUPPORTED_FORMAT_VERSION" ? "unsupported-format-version" : "invalid-meta", a, { containerBytes: o, metaBytes: s });
  }
  let u = o - x - s - Y;
  return u <= 0 ? t("no-payload", a, { containerBytes: o, metaBytes: s, payloadBytes: 0, meta: l }) : { ok: true, reason: "ok", medium: a, containerBytes: o, metaBytes: s, payloadBytes: u, meta: l };
};

// packages/ckx-mcp/src/mcp.mjs
var PROTOCOL_VERSION = "2024-11-05";
var SERVER_INFO = { name: "ckx-mcp", version: "0.1.0" };
var resolveSdkVersion = () => {
  if ("0.6.0") return String("0.6.0");
  try {
    return JSON.parse(fs.readFileSync(new URL("../../ckx/package.json", import.meta.url), "utf8")).version;
  } catch {
    return "0.0.0";
  }
};
var SDK_VERSION = resolveSdkVersion();
var DEFAULT_APP = "https://agent.local";
var POLICY_ENDPOINT = "https://cryptokey.im/v1/ckx/embed/policy";
var SECRETS = [];
var redact = (s) => {
  let out = String(s ?? "");
  for (const sec of SECRETS) {
    if (sec) out = out.split(sec).join("[redacted]");
  }
  return out;
};
var paramError = (message) => Object.assign(new Error(redact(message)), { code: "INVALID_PARAMS" });
var writeMsg = (obj) => {
  process.stdout.write(JSON.stringify(obj) + "\n");
};
var resolvePassword = (args) => {
  if (typeof args.passwordEnv === "string" && args.passwordEnv.length > 0) {
    const v2 = process.env[args.passwordEnv];
    if (typeof v2 !== "string" || v2.length === 0) {
      throw paramError(`environment variable ${args.passwordEnv} is not set`);
    }
    SECRETS.push(v2);
    return v2;
  }
  if (typeof args.password === "string" && args.password.length > 0) {
    SECRETS.push(args.password);
    return args.password;
  }
  throw paramError("missing password: provide passwordEnv (recommended) or password");
};
var hasText = (args) => typeof args.text === "string";
var hasB64 = (args) => typeof args.dataBase64 === "string";
var resolveRecipientPrivateKey = (args) => {
  if (typeof args.privateKeyEnv === "string" && args.privateKeyEnv.length > 0) {
    const v2 = process.env[args.privateKeyEnv];
    if (typeof v2 !== "string" || v2.length === 0) {
      throw paramError(`environment variable ${args.privateKeyEnv} is not set`);
    }
    SECRETS.push(v2);
    return v2.trim();
  }
  if (typeof args.recipientPrivateKey === "string" && args.recipientPrivateKey.length > 0) {
    SECRETS.push(args.recipientPrivateKey);
    return args.recipientPrivateKey.trim();
  }
  throw paramError("missing recipient private key: provide privateKeyEnv (recommended) or recipientPrivateKey");
};
var readPayload = (args) => {
  if (hasText(args) === hasB64(args)) {
    throw paramError("exactly one of text / dataBase64 is required");
  }
  if (hasText(args)) return { bytes: new TextEncoder().encode(args.text), text: args.text };
  return { bytes: new Uint8Array(Buffer.from(args.dataBase64, "base64")), text: null };
};
var readCarrier = (args) => {
  if (!hasText(args) && !hasB64(args)) {
    throw paramError("exactly one of text / dataBase64 is required");
  }
  if (hasText(args) && hasB64(args)) {
    throw paramError("provide only one of text / dataBase64");
  }
  if (hasText(args)) return args.text;
  return new Uint8Array(Buffer.from(args.dataBase64, "base64"));
};
var buildMeta = (args) => {
  const meta = { app: typeof args.app === "string" && args.app ? args.app : DEFAULT_APP };
  if (typeof args.schema === "string") meta.schema = args.schema;
  if (typeof args.schemaVersion === "string") meta.schemaVersion = args.schemaVersion;
  if (typeof args.label === "string") meta.label = args.label;
  return meta;
};
var offlineCapabilities = () => {
  const lic = gr || {};
  const freeFeatures = Array.isArray(lic.freeFeatures) ? [...lic.freeFeatures] : ["text", "image", "file", "video"];
  const proFeatures = Array.isArray(lic.proFeatures) ? [...lic.proFeatures] : ["shard", "strictMatching", "bulk", "whiteLabel", "schema", "sla"];
  return { sdkVersion: SDK_VERSION, formatVersion: ne, freeFeatures, proFeatures };
};
var toolEncrypt = async (args) => {
  const password = resolvePassword(args);
  const { bytes, text } = readPayload(args);
  const medium = args.medium === void 0 ? "file" : args.medium;
  if (medium !== "file" && medium !== "text") throw paramError("medium must be 'file' or 'text'");
  const meta = buildMeta(args);
  if (medium === "text") {
    let payloadText = text;
    if (payloadText === null) {
      try {
        payloadText = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      } catch {
        throw paramError("text medium only supports UTF-8 text payloads");
      }
    }
    const b64 = await Sn(payloadText, password, meta);
    return { medium: "text", text: b64, bytes: b64.length };
  }
  const container = await Dr(text !== null ? text : bytes, password, meta);
  return { medium: "file", dataBase64: Buffer.from(container).toString("base64"), bytes: container.length };
};
var toolDecrypt = async (args) => {
  const password = resolvePassword(args);
  const carrier = readCarrier(args);
  const schema = typeof args.schema === "string" ? args.schema : null;
  const r = await vr(carrier, password, { schema });
  const out = { text: r.text, bytes: r.plaintext.length, meta: r.meta, medium: r.medium };
  if (r.text === null) out.dataBase64 = Buffer.from(r.plaintext).toString("base64");
  return out;
};
var toolReadMeta = async (args) => {
  const carrier = readCarrier(args);
  return { meta: Dn(carrier) };
};
var toolEstimate = async (args) => {
  let source;
  if (typeof args.size === "number") source = args.size;
  else if (typeof args.width === "number" && typeof args.height === "number") {
    source = { width: args.width, height: args.height };
  } else {
    throw paramError("provide size or width+height");
  }
  const opts = { binary: args.binary === true };
  if (typeof args.metaBytes === "number") opts.metaBytes = args.metaBytes;
  return Rn(source, opts);
};
var toolCapabilities = async (args) => {
  const origin = typeof args.origin === "string" && args.origin.length > 0 ? args.origin : null;
  if (origin === null) return offlineCapabilities();
  const url = `${POLICY_ENDPOINT}?origin=${encodeURIComponent(origin)}`;
  let res;
  try {
    res = await fetch(url, { headers: { accept: "application/json" } });
  } catch {
    throw Object.assign(new Error("policy request failed (network unreachable)"), { code: "CKX_TIMEOUT" });
  }
  if (!res || !res.ok) {
    throw Object.assign(new Error(`policy request failed (HTTP ${res ? res.status : 0})`), { code: "CKX_HTTP_ERROR" });
  }
  try {
    return await res.json();
  } catch {
    throw Object.assign(new Error("policy response is not valid JSON"), { code: "CKX_HTTP_ERROR" });
  }
};
var toolVerifyIntegrity = async (args) => {
  const carrier = readCarrier(args);
  return Pn(carrier);
};
var toolGenerateRecipientKeypair = async () => {
  const pair = await Qt();
  SECRETS.push(pair.privateKey);
  return { publicKey: pair.publicKey, privateKey: pair.privateKey };
};
var toolEncryptTo = async (args) => {
  if (typeof args.recipientPublicKey !== "string" || args.recipientPublicKey.length === 0) {
    throw paramError("recipientPublicKey is required (base64url raw 32-byte X25519 public key)");
  }
  const { bytes, text } = readPayload(args);
  const meta = buildMeta(args);
  const container = await nr(text !== null ? text : bytes, args.recipientPublicKey.trim(), meta);
  return { dataBase64: Buffer.from(container).toString("base64"), bytes: container.length };
};
var toolDecryptAs = async (args) => {
  const privateKey = resolveRecipientPrivateKey(args);
  const carrier = readCarrier(args);
  const schema = typeof args.schema === "string" ? args.schema : null;
  const r = await ir(carrier, privateKey, { schema });
  const out = { text: r.text, bytes: r.plaintext.length, meta: r.meta };
  if (r.text === null) out.dataBase64 = Buffer.from(r.plaintext).toString("base64");
  return out;
};
var toolRecipientPublicKey = async (args) => {
  const privateKey = resolveRecipientPrivateKey(args);
  const publicKey = await er(privateKey);
  const fingerprint = await tr(publicKey);
  return { publicKey, fingerprint };
};
var TOOL_DEFS = [
  {
    name: "ckx_encrypt",
    description: 'Encrypt a payload into a CKX carrier. medium="file" returns a SAFE v3 container (dataBase64); medium="text" returns a base64url string (text payloads only, \u22645 MiB). Password via passwordEnv (recommended).',
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string", description: "UTF-8 text payload" },
        dataBase64: { type: "string", description: "binary payload as standard base64" },
        medium: { type: "string", enum: ["file", "text"], description: 'default "file"' },
        app: { type: "string", description: `meta.app; default ${DEFAULT_APP}` },
        schema: { type: "string" },
        schemaVersion: { type: "string" },
        label: { type: "string" },
        password: { type: "string", description: "inline password (prefer passwordEnv)" },
        passwordEnv: { type: "string", description: "env var name holding the password (recommended)" }
      },
      additionalProperties: false
    }
  },
  {
    name: "ckx_decrypt",
    description: "Decrypt a CKX carrier (text string or base64 container). Returns { text, dataBase64?, bytes, meta, medium }. Password via passwordEnv (recommended).",
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string", description: "base64url text carrier" },
        dataBase64: { type: "string", description: "container bytes as standard base64" },
        schema: { type: "string", description: "require this schemaId" },
        password: { type: "string" },
        passwordEnv: { type: "string" }
      },
      additionalProperties: false
    }
  },
  {
    name: "ckx_read_meta",
    description: "Read public metadata from a CKX carrier without decrypting. Returns { meta } (may be null).",
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string" },
        dataBase64: { type: "string" }
      },
      additionalProperties: false
    }
  },
  {
    name: "ckx_estimate",
    description: "Estimate image carrier capacity. Provide size (square) or width+height.",
    inputSchema: {
      type: "object",
      properties: {
        size: { type: "integer", minimum: 1 },
        width: { type: "integer", minimum: 1 },
        height: { type: "integer", minimum: 1 },
        binary: { type: "boolean" },
        metaBytes: { type: "integer", minimum: 0 }
      },
      additionalProperties: false
    }
  },
  {
    name: "ckx_capabilities",
    description: "CKX capability catalog. Without origin: offline SDK catalog { sdkVersion, freeFeatures, proFeatures }. With origin: fetch the public embed policy for that origin.",
    inputSchema: {
      type: "object",
      properties: {
        origin: { type: "string", description: "e.g. https://example.com" }
      },
      additionalProperties: false
    }
  },
  {
    name: "ckx_verify_integrity",
    description: "Password-free structural self-check of a CKX carrier (no decryption). Returns { ok, reason, medium, bytes, meta }.",
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string" },
        dataBase64: { type: "string" }
      },
      additionalProperties: false
    }
  },
  {
    name: "ckx_generate_recipient_keypair",
    description: "Generate an X25519 recipient keypair for asymmetric (CKXA) encryption. Returns { publicKey, privateKey }. The private key is returned once \u2014 store it securely and never share it.",
    inputSchema: {
      type: "object",
      properties: {},
      additionalProperties: false
    }
  },
  {
    name: "ckx_encrypt_to",
    description: "Asymmetric recipient encryption into a CKXA v1 container (no shared password). Provide text or dataBase64 plus the recipient public key. Returns { dataBase64, bytes }.",
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string", description: "UTF-8 text payload" },
        dataBase64: { type: "string", description: "binary payload as standard base64" },
        recipientPublicKey: {
          type: "string",
          description: "base64url raw 32-byte X25519 public key (required)"
        },
        app: { type: "string", description: `meta.app; default ${DEFAULT_APP}` },
        schema: { type: "string" },
        schemaVersion: { type: "string" },
        label: { type: "string" }
      },
      required: ["recipientPublicKey"],
      additionalProperties: false
    }
  },
  {
    name: "ckx_decrypt_as",
    description: "Decrypt a CKXA v1 container with a recipient private key. Returns { text, dataBase64?, bytes, meta }. Private key via privateKeyEnv (recommended) or recipientPrivateKey.",
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string", description: "base64url text carrier" },
        dataBase64: { type: "string", description: "container bytes as standard base64" },
        recipientPrivateKey: {
          type: "string",
          description: "base64url PKCS#8 X25519 private key (prefer privateKeyEnv)"
        },
        privateKeyEnv: {
          type: "string",
          description: "env var name holding the private key (recommended)"
        },
        schema: { type: "string", description: "require this schemaId" }
      },
      additionalProperties: false
    }
  },
  {
    name: "ckx_recipient_public_key",
    description: "Derive the public key and fingerprint from a recipient private key. Returns { publicKey, fingerprint }. Private key via privateKeyEnv (recommended) or recipientPrivateKey.",
    inputSchema: {
      type: "object",
      properties: {
        recipientPrivateKey: { type: "string" },
        privateKeyEnv: { type: "string" }
      },
      additionalProperties: false
    }
  }
];
var TOOL_HANDLERS = {
  ckx_encrypt: toolEncrypt,
  ckx_decrypt: toolDecrypt,
  ckx_read_meta: toolReadMeta,
  ckx_estimate: toolEstimate,
  ckx_capabilities: toolCapabilities,
  ckx_verify_integrity: toolVerifyIntegrity,
  ckx_generate_recipient_keypair: toolGenerateRecipientKeypair,
  ckx_encrypt_to: toolEncryptTo,
  ckx_decrypt_as: toolDecryptAs,
  ckx_recipient_public_key: toolRecipientPublicKey
};
var handleToolCall = async (params) => {
  const name = params && typeof params.name === "string" ? params.name : "";
  const args = params && typeof params.arguments === "object" && params.arguments !== null ? params.arguments : {};
  const handler = TOOL_HANDLERS[name];
  if (!handler) {
    return { rpcError: { code: -32602, message: `Unknown tool: ${name}` } };
  }
  try {
    const out = await handler(args);
    return { result: { content: [{ type: "text", text: JSON.stringify(out) }] } };
  } catch (err) {
    const code = err && typeof err.code === "string" ? err.code : "CKX_ERROR";
    const message = redact(err && err.message ? err.message : String(err));
    return { result: { content: [{ type: "text", text: JSON.stringify({ code, message }) }], isError: true } };
  }
};
var handleMessage = async (raw) => {
  let msg;
  try {
    msg = JSON.parse(raw);
  } catch {
    writeMsg({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } });
    return;
  }
  if (!msg || typeof msg !== "object" || typeof msg.method !== "string") {
    if (msg && msg.id !== void 0) {
      writeMsg({ jsonrpc: "2.0", id: msg.id, error: { code: -32602, message: "Invalid Request" } });
    }
    return;
  }
  const { id, method, params } = msg;
  const isNotification = id === void 0;
  const respond = (payload) => {
    if (!isNotification) writeMsg({ jsonrpc: "2.0", id, ...payload });
  };
  try {
    if (method === "initialize") {
      respond({
        result: {
          protocolVersion: PROTOCOL_VERSION,
          capabilities: { tools: {} },
          serverInfo: SERVER_INFO
        }
      });
      return;
    }
    if (method === "notifications/initialized") return;
    if (method === "ping") {
      respond({ result: {} });
      return;
    }
    if (method === "tools/list") {
      respond({ result: { tools: TOOL_DEFS } });
      return;
    }
    if (method === "tools/call") {
      const call = await handleToolCall(params);
      if (call.rpcError) respond({ error: call.rpcError });
      else respond({ result: call.result });
      return;
    }
    respond({ error: { code: -32601, message: `Method not found: ${method}` } });
  } catch (err) {
    const message = redact(err && err.message ? err.message : String(err));
    respond({ error: { code: -32603, message } });
  }
};
var rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
var chain = Promise.resolve();
rl.on("line", (line) => {
  const trimmed = line.trim();
  if (!trimmed) return;
  chain = chain.then(() => handleMessage(trimmed)).catch(() => {
  });
});
rl.on("close", () => {
  chain.finally(() => {
    process.exitCode = 0;
  });
});
