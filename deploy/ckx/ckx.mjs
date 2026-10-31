#!/usr/bin/env node

// packages/ckx-cli/src/cli.mjs
import fs from "node:fs";
import process from "node:process";

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
var Ie = 12;
var K = 20;
var mt = new TextEncoder().encode(j);
var Ke = (e) => e instanceof Uint8Array;
var yt = (e) => {
  if (!Ke(e) || e.length < K) return null;
  let t = e.length, r = t - K;
  for (let i = 0; i < Ie; i++) if (e[r + i] !== mt[i]) return null;
  let a = new DataView(e.buffer, e.byteOffset + r + Ie, 8).getBigUint64(0, true), o = t - K;
  return a <= 0n || a > BigInt(o) ? null : Number(a);
};
var ft = (e) => {
  let t = yt(e);
  if (t === null) return null;
  let r = e.length - K - t;
  return e.slice(r, r + t);
};
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
var Pr = (e) => {
  if (e.length < K) return null;
  let t = e.length, r = t - K;
  for (let i = 0; i < j.length; i++) if (e[r + i] !== j.charCodeAt(i)) return null;
  let a = new DataView(e.buffer, e.byteOffset + r + j.length, 8).getBigUint64(0, true), o = t - K;
  return a <= 0n || a > BigInt(o) ? null : { offset: o - Number(a), length: Number(a) };
};
var Kn = (e) => {
  let t = e instanceof Uint8Array ? e : new Uint8Array(e), r = Pr(t);
  if (!r) return { hasTrailer: false, containerBytes: null, trailerOffset: null, meta: null };
  let n = null;
  try {
    n = se(t.subarray(r.offset, r.offset + r.length));
  } catch {
    n = null;
  }
  return { hasTrailer: true, containerBytes: r.length, trailerOffset: r.offset, meta: n };
};
var vr = async (e, t, r = {}) => {
  let { container: n, medium: a } = Dt(e), { plaintext: o, meta: i } = await pt(n, t, { schema: r.schema ?? null });
  return { plaintext: o, text: ce(o), meta: i, medium: a };
};
var On = async (e, t, r = {}) => {
  let n = ft(e);
  if (!n) throw c("CKX_NO_CARRIER", "no CKX hidden data in video trailer", { field: "carrier", hint: "no-video-trailer" });
  let { plaintext: a, text: o, meta: i } = await vr(n, t, r);
  return { text: o, bytes: a, meta: i };
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

// packages/ckx-cli/src/cli.mjs
var VERSION = "0.1.0";
var DEFAULT_APP = "https://agent.local";
var POLICY_ENDPOINT = "https://cryptokey.im/v1/ckx/embed/policy";
var resolveSdkVersion = () => {
  if ("0.6.0") return String("0.6.0");
  try {
    return JSON.parse(fs.readFileSync(new URL("../../ckx/package.json", import.meta.url), "utf8")).version;
  } catch {
    return "0.0.0";
  }
};
var SDK_VERSION = resolveSdkVersion();
var SECRETS = [];
var redact = (s) => {
  let out = String(s ?? "");
  for (const sec of SECRETS) {
    if (sec) out = out.split(sec).join("[redacted]");
  }
  return out;
};
var UsageError = class extends Error {
};
var usageError = (msg) => new UsageError(redact(msg));
var codedError = (code, message) => Object.assign(new Error(redact(message)), { code });
var USAGE = `CKX \u667A\u80FD\u4F53 CLI \u2014\u2014 \u96F6\u77E5\u8BC6\u52A0\u89E3\u5BC6\uFF08${VERSION}\uFF09

\u7528\u6CD5\uFF1Ackx <\u547D\u4EE4> [\u9009\u9879]

\u547D\u4EE4\uFF1A
  encrypt       \u52A0\u5BC6\u8F7D\u8377 \u2192 CKX \u8F7D\u4F53 / \u5BC6\u6587\u4E32
  decrypt       \u89E3\u5BC6 CKX \u8F7D\u4F53 / \u5BC6\u6587\u4E32
  meta          \u53EA\u8BFB\u516C\u5F00\u5143\u6570\u636E\uFF08\u4E0D\u89E3\u5BC6\uFF09
  verify        \u514D\u5BC6\u7801\u7ED3\u6784\u81EA\u68C0
  estimate      \u4F30\u7B97\u56FE\u7247\u8F7D\u4F53\u5BB9\u91CF
  capabilities  \u80FD\u529B\u76EE\u5F55\uFF08\u79BB\u7EBF\uFF0C\u6216\u67E5\u8BE2\u7EBF\u4E0A embed policy\uFF09
  keygen        \u751F\u6210 X25519 \u6536\u4EF6\u4EBA\u5BC6\u94A5\u5BF9\uFF08\u516C\u94A5\u53EF\u516C\u5F00\uFF0C\u79C1\u94A5\u52A1\u5FC5\u4FDD\u5BC6\uFF09
  encrypt-to    \u7528\u6536\u4EF6\u4EBA\u516C\u94A5\u52A0\u5BC6\uFF08\u975E\u5BF9\u79F0\uFF0C\u65E0\u9700\u5171\u4EAB\u53E3\u4EE4\uFF09\u2192 CKXA \u5BB9\u5668
  decrypt-as    \u7528\u6536\u4EF6\u4EBA\u79C1\u94A5\u89E3\u5BC6 CKXA \u5BB9\u5668
  recipient-pub \u7531\u79C1\u94A5\u63A8\u5BFC\u516C\u94A5 + \u6307\u7EB9

\u5168\u5C40\u9009\u9879\uFF1A
  --help        \u663E\u793A\u672C\u5E2E\u52A9
  --version     \u663E\u793A\u7248\u672C

encrypt \u9009\u9879\uFF1A
  --in <path|->               \u8F93\u5165\u6587\u4EF6\uFF08- \u8868\u793A stdin\uFF09
  --text                      \u628A\u8F93\u5165\u5B57\u8282\u6309 UTF-8 \u6587\u672C\u5904\u7406
  --out <path|->              \u8F93\u51FA\uFF08- \u8868\u793A stdout\uFF09
  --password-env VAR          \u4ECE\u73AF\u5883\u53D8\u91CF\u8BFB\u53D6\u53E3\u4EE4\uFF08\u63A8\u8350\uFF09
  --password-file PATH        \u4ECE\u6587\u4EF6\u8BFB\u53D6\u53E3\u4EE4\uFF08\u53BB\u5C3E\u90E8\u6362\u884C\uFF09
  --password-stdin            \u4ECE stdin \u8BFB\u53D6\u53E3\u4EE4
  --app ORIGIN                \u5E94\u7528\u6807\u8BC6\uFF0C\u5199\u5165 meta.app\uFF08\u9ED8\u8BA4 ${DEFAULT_APP}\uFF09
  --schema ID                 \u7B7E\u7EA6 schema \u6807\u8BC6
  --schema-version V          schema \u7248\u672C
  --label S                   \u6807\u7B7E
  --medium file|text          \u4ECB\u8D28\uFF1Afile=SAFE v3 \u5BB9\u5668\u5B57\u8282\uFF08\u9ED8\u8BA4\uFF09\uFF1Btext=base64url \u6587\u672C\u4E32\uFF08\u4EC5\u6587\u672C\u8F7D\u8377\uFF09

decrypt \u9009\u9879\uFF1A
  --in <path|->               \u8F93\u5165\uFF08\u81EA\u52A8\u8BC6\u522B\u6587\u672C\u4E32 / \u5BB9\u5668\u5B57\u8282 / \u89C6\u9891\u5C3E\u6302\uFF09
  --out <path|->              \u8F93\u51FA\uFF08\u9ED8\u8BA4 stdout\uFF0C\u539F\u59CB\u660E\u6587\u5B57\u8282\uFF09
  --schema ID                 \u53EA\u63A5\u53D7\u8BE5 schemaId\uFF08\u4E0D\u7B26\u62A5\u9519\uFF09
  \u4EE5\u53CA\u4E09\u79CD\u53E3\u4EE4\u6765\u6E90\uFF08\u540C encrypt\uFF09

meta / verify \u9009\u9879\uFF1A
  --in <path|->

estimate \u9009\u9879\uFF1A
  --size N                    \u6B63\u65B9\u5F62\u8FB9\u957F
  --width W --height H        \u663E\u5F0F\u5C3A\u5BF8
  --binary                    \u4E8C\u8FDB\u5236\uFF08IMGV\uFF09\u4ECB\u8D28
  --meta-bytes N              \u9884\u4F30\u5143\u6570\u636E\u5B57\u8282\u6570\uFF08\u9ED8\u8BA4 0\uFF09

capabilities \u9009\u9879\uFF1A
  --origin URL                \u67E5\u8BE2\u8BE5 origin \u7684\u7EBF\u4E0A\u80FD\u529B\u6863
  --json                      \u4EE5 JSON \u8F93\u51FA\uFF08\u9ED8\u8BA4\u5373 JSON\uFF09

keygen \u9009\u9879\uFF1A
  --out <prefix>              \u5199 <prefix>.pub(0644) \u4E0E <prefix>.key(0600)\uFF1B\u7F3A\u7701\u628A\u516C\u79C1\u94A5\u6253\u5230 stdout

encrypt-to \u9009\u9879\uFF1A
  --to <pubkey|@file>         \u6536\u4EF6\u4EBA\u516C\u94A5\uFF08base64url 43 \u5B57\u7B26\uFF09\u6216 @file\uFF08\u8BFB\u6587\u4EF6\uFF0C\u53BB\u6389\u9996\u5C3E\u7A7A\u767D\uFF09
  --in <path|->               \u8F93\u5165\u6587\u4EF6\uFF08- \u8868\u793A stdin\uFF09
  --out <path|->              \u8F93\u51FA CKXA \u5BB9\u5668\u5B57\u8282\uFF08- \u8868\u793A stdout\uFF09
  --text                      \u628A\u8F93\u5165\u5B57\u8282\u6309 UTF-8 \u6587\u672C\u5904\u7406
  --app ORIGIN                \u5E94\u7528\u6807\u8BC6\uFF0C\u5199\u5165 meta.app\uFF08\u9ED8\u8BA4 ${DEFAULT_APP}\uFF09
  --schema ID                 \u7B7E\u7EA6 schema \u6807\u8BC6
  --schema-version V          schema \u7248\u672C
  --label S                   \u6807\u7B7E

decrypt-as \u9009\u9879\uFF1A
  --in <path|->               \u8F93\u5165\uFF08CKXA \u5BB9\u5668\u5B57\u8282\u6216 base64url \u6587\u672C\u4E32\uFF09
  --out <path|->              \u8F93\u51FA\u660E\u6587\uFF08\u9ED8\u8BA4 stdout\uFF09
  --schema ID                 \u53EA\u63A5\u53D7\u8BE5 schemaId\uFF08\u4E0D\u7B26\u62A5\u9519\uFF09
  \u4EE5\u53CA\u4E09\u79CD\u79C1\u94A5\u6765\u6E90\uFF08\u4F18\u5148\u7EA7 env > file > stdin\uFF1B**\u7EDD\u4E0D**\u7ECF argv\uFF09\uFF1A
  --key-env VAR               \u4ECE\u73AF\u5883\u53D8\u91CF\u8BFB\u53D6\u79C1\u94A5
  --key-file PATH             \u4ECE\u6587\u4EF6\u8BFB\u53D6\u79C1\u94A5
  --key-stdin                 \u4ECE stdin \u8BFB\u53D6\u79C1\u94A5

recipient-pub \u9009\u9879\uFF1A
  \u4E09\u79CD\u79C1\u94A5\u6765\u6E90\uFF08\u540C decrypt-as\uFF09

\u793A\u4F8B\uFF1A
  ckx encrypt --in secret.txt --out secret.ckx --password-env CKX_PW
  ckx decrypt --in secret.ckx --out plain.txt --password-env CKX_PW
  ckx meta --in secret.ckx
  ckx capabilities
  ckx keygen --out ~/.ckx/recipient
  ckx encrypt-to --to @~/.ckx/recipient.pub --in note.txt --text --out note.ckxa
  ckx decrypt-as --key-file ~/.ckx/recipient.key --in note.ckxa
  ckx recipient-pub --key-file ~/.ckx/recipient.key
`;
var FLAG_TABLES = {
  encrypt: /* @__PURE__ */ new Map([
    ["in", "value"],
    ["text", "bool"],
    ["out", "value"],
    ["password-env", "value"],
    ["password-file", "value"],
    ["password-stdin", "bool"],
    ["app", "value"],
    ["schema", "value"],
    ["schema-version", "value"],
    ["label", "value"],
    ["medium", "value"],
    ["help", "bool"]
  ]),
  decrypt: /* @__PURE__ */ new Map([
    ["in", "value"],
    ["out", "value"],
    ["schema", "value"],
    ["password-env", "value"],
    ["password-file", "value"],
    ["password-stdin", "bool"],
    ["help", "bool"]
  ]),
  meta: /* @__PURE__ */ new Map([["in", "value"], ["help", "bool"]]),
  verify: /* @__PURE__ */ new Map([["in", "value"], ["help", "bool"]]),
  estimate: /* @__PURE__ */ new Map([
    ["size", "value"],
    ["width", "value"],
    ["height", "value"],
    ["binary", "bool"],
    ["meta-bytes", "value"],
    ["help", "bool"]
  ]),
  capabilities: /* @__PURE__ */ new Map([["origin", "value"], ["json", "bool"], ["help", "bool"]]),
  keygen: /* @__PURE__ */ new Map([["out", "value"], ["help", "bool"]]),
  "encrypt-to": /* @__PURE__ */ new Map([
    ["to", "value"],
    ["in", "value"],
    ["out", "value"],
    ["text", "bool"],
    ["app", "value"],
    ["schema", "value"],
    ["schema-version", "value"],
    ["label", "value"],
    ["help", "bool"]
  ]),
  "decrypt-as": /* @__PURE__ */ new Map([
    ["key-env", "value"],
    ["key-file", "value"],
    ["key-stdin", "bool"],
    ["in", "value"],
    ["out", "value"],
    ["schema", "value"],
    ["help", "bool"]
  ]),
  "recipient-pub": /* @__PURE__ */ new Map([
    ["key-env", "value"],
    ["key-file", "value"],
    ["key-stdin", "bool"],
    ["help", "bool"]
  ])
};
var parseFlags = (args, flags) => {
  const opts = {};
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (!a.startsWith("--")) throw usageError(`\u591A\u4F59\u7684\u4F4D\u7F6E\u53C2\u6570\uFF1A${a}`);
    let name = a.slice(2);
    let value = null;
    const eq = name.indexOf("=");
    if (eq >= 0) {
      value = name.slice(eq + 1);
      name = name.slice(0, eq);
    }
    const kind = flags.get(name);
    if (kind === void 0) throw usageError(`\u672A\u77E5\u9009\u9879 --${name}`);
    if (kind === "bool") {
      if (value !== null) throw usageError(`\u9009\u9879 --${name} \u4E0D\u63A5\u53D7\u503C`);
      opts[name] = true;
    } else {
      if (value === null) {
        value = args[++i];
        if (value === void 0) throw usageError(`\u9009\u9879 --${name} \u7F3A\u5C11\u503C`);
      }
      opts[name] = value;
    }
  }
  return opts;
};
var requireOpt = (opts, name) => {
  if (opts[name] === void 0) throw usageError(`\u7F3A\u5C11\u5FC5\u9700\u9009\u9879 --${name}`);
};
var readInputBytes = (spec) => spec === "-" ? fs.readFileSync(0) : fs.readFileSync(spec);
var readStdinString = () => fs.readFileSync(0, "utf8");
var writeOutput = (spec, data) => {
  const buf = typeof data === "string" ? Buffer.from(data, "utf8") : Buffer.from(data);
  if (spec === "-") process.stdout.write(buf);
  else fs.writeFileSync(spec, buf);
};
var resolvePassword = (opts) => {
  let pw = null;
  if (opts["password-env"] !== void 0) {
    const name = opts["password-env"];
    const v2 = process.env[name];
    if (typeof v2 !== "string" || v2.length === 0) throw usageError(`\u73AF\u5883\u53D8\u91CF ${name} \u672A\u8BBE\u7F6E\u6216\u4E3A\u7A7A`);
    pw = v2;
  } else if (opts["password-file"] !== void 0) {
    pw = fs.readFileSync(opts["password-file"], "utf8").replace(/\r?\n+$/, "");
    if (pw.length === 0) throw usageError("\u53E3\u4EE4\u6587\u4EF6\u4E3A\u7A7A");
  } else if (opts["password-stdin"]) {
    pw = readStdinString().replace(/\r?\n+$/, "");
    if (pw.length === 0) throw usageError("stdin \u53E3\u4EE4\u4E3A\u7A7A");
  } else {
    throw usageError("\u7F3A\u5C11\u53E3\u4EE4\u6765\u6E90\uFF1A--password-env VAR | --password-file PATH | --password-stdin");
  }
  SECRETS.push(pw);
  return pw;
};
var assertStdinFree = (opts) => {
  if (opts.in === "-" && opts["password-stdin"]) {
    throw usageError("--in - \u4E0E --password-stdin \u4E0D\u80FD\u540C\u65F6\u4F7F\u7528\uFF08\u90FD\u4F1A\u6D88\u8D39 stdin\uFF09");
  }
};
var resolveRecipientPrivateKey = (opts) => {
  let key = null;
  if (opts["key-env"] !== void 0) {
    const name = opts["key-env"];
    const v2 = process.env[name];
    if (typeof v2 !== "string" || v2.length === 0) throw usageError(`\u73AF\u5883\u53D8\u91CF ${name} \u672A\u8BBE\u7F6E\u6216\u4E3A\u7A7A`);
    key = v2.trim();
  } else if (opts["key-file"] !== void 0) {
    key = fs.readFileSync(opts["key-file"], "utf8").trim();
    if (key.length === 0) throw usageError("\u79C1\u94A5\u6587\u4EF6\u4E3A\u7A7A");
  } else if (opts["key-stdin"]) {
    key = readStdinString().trim();
    if (key.length === 0) throw usageError("stdin \u79C1\u94A5\u4E3A\u7A7A");
  } else {
    throw usageError("\u7F3A\u5C11\u79C1\u94A5\u6765\u6E90\uFF1A--key-env VAR | --key-file PATH | --key-stdin");
  }
  SECRETS.push(key);
  return key;
};
var normalizeRecipientPublicKey = (raw) => {
  const stripped = String(raw).replace(/\s+/g, "").replace(/=+$/, "");
  if (!/^[A-Za-z0-9_-]{43}$/.test(stripped)) {
    throw usageError("--to \u516C\u94A5\u975E\u6CD5\uFF1A\u671F\u671B X25519 raw \u516C\u94A5\u7684 base64url\uFF0843 \u5B57\u7B26\uFF09");
  }
  return stripped;
};
var resolveRecipientPublicKey = (spec) => {
  if (spec.startsWith("@")) {
    const p = spec.slice(1);
    if (p.length === 0) throw usageError("--to @<file> \u7F3A\u5C11\u6587\u4EF6\u8DEF\u5F84");
    return normalizeRecipientPublicKey(fs.readFileSync(p, "utf8"));
  }
  return normalizeRecipientPublicKey(spec);
};
var isSafeContainer = (b) => b.length >= 4 && b[0] === 83 && b[1] === 65 && b[2] === 70 && b[3] === 69;
var toCarrier = (bytes) => {
  if (isSafeContainer(bytes)) return bytes;
  let str = null;
  try {
    str = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    str = null;
  }
  if (str !== null) {
    const t = str.trim();
    if (t.length > 0 && /^[A-Za-z0-9_-]+$/.test(t)) return t;
  }
  return bytes;
};
var decodeTextStrict = (bytes) => new TextDecoder("utf-8", { fatal: true }).decode(bytes);
var printJson = (value) => process.stdout.write(JSON.stringify(value, null, 2) + "\n");
var cmdEncrypt = async (args) => {
  const opts = parseFlags(args, FLAG_TABLES.encrypt);
  if (opts.help) return process.stdout.write(USAGE);
  requireOpt(opts, "in");
  requireOpt(opts, "out");
  const medium = opts.medium ?? "file";
  if (medium !== "file" && medium !== "text") throw usageError("--medium \u4EC5\u652F\u6301 file|text");
  assertStdinFree(opts);
  const password = resolvePassword(opts);
  const bytes = readInputBytes(opts.in);
  const meta = { app: opts.app ?? DEFAULT_APP };
  if (opts.schema !== void 0) meta.schema = opts.schema;
  if (opts["schema-version"] !== void 0) meta.schemaVersion = opts["schema-version"];
  if (opts.label !== void 0) meta.label = opts.label;
  if (medium === "text") {
    let text;
    try {
      text = decodeTextStrict(bytes);
    } catch {
      throw usageError("--medium text \u4EC5\u652F\u6301 UTF-8 \u6587\u672C\u8F7D\u8377\uFF1B\u4E8C\u8FDB\u5236\u8BF7\u7528 file medium");
    }
    const b64 = await Sn(text, password, meta);
    writeOutput(opts.out, b64);
    return;
  }
  const payload = opts.text ? decodeTextStrict(bytes) : bytes;
  const container = await Dr(payload, password, meta);
  writeOutput(opts.out, container);
};
var cmdDecrypt = async (args) => {
  const opts = parseFlags(args, FLAG_TABLES.decrypt);
  if (opts.help) return process.stdout.write(USAGE);
  requireOpt(opts, "in");
  assertStdinFree(opts);
  const password = resolvePassword(opts);
  const bytes = readInputBytes(opts.in);
  const schema = opts.schema ?? null;
  let plaintext;
  let vm = null;
  try {
    vm = Kn(bytes);
  } catch {
    vm = null;
  }
  if (vm && vm.hasTrailer) {
    const r = await On(bytes, password, { schema });
    plaintext = r.bytes;
  } else {
    const r = await vr(toCarrier(bytes), password, { schema });
    plaintext = r.plaintext;
  }
  writeOutput(opts.out ?? "-", plaintext);
};
var cmdMeta = async (args) => {
  const opts = parseFlags(args, FLAG_TABLES.meta);
  if (opts.help) return process.stdout.write(USAGE);
  requireOpt(opts, "in");
  const bytes = readInputBytes(opts.in);
  let vm = null;
  try {
    vm = Kn(bytes);
  } catch {
    vm = null;
  }
  const meta = vm && vm.hasTrailer ? vm.meta : Dn(toCarrier(bytes));
  printJson(meta);
};
var cmdVerify = async (args) => {
  const opts = parseFlags(args, FLAG_TABLES.verify);
  if (opts.help) return process.stdout.write(USAGE);
  requireOpt(opts, "in");
  const bytes = readInputBytes(opts.in);
  const result = Pn(toCarrier(bytes));
  printJson(result);
  if (!result.ok) process.exitCode = 1;
};
var cmdEstimate = async (args) => {
  const opts = parseFlags(args, FLAG_TABLES.estimate);
  if (opts.help) return process.stdout.write(USAGE);
  const binary = !!opts.binary;
  const metaBytes = opts["meta-bytes"] !== void 0 ? Number(opts["meta-bytes"]) : 0;
  let source;
  if (opts.size !== void 0) source = Number(opts.size);
  else if (opts.width !== void 0 && opts.height !== void 0) {
    source = { width: Number(opts.width), height: Number(opts.height) };
  } else {
    throw usageError("\u9700\u8981 --size N \u6216 --width W --height H");
  }
  const result = Rn(source, { binary, metaBytes });
  printJson(result);
};
var offlineCapabilities = () => {
  const lic = gr || {};
  const freeFeatures = Array.isArray(lic.freeFeatures) ? [...lic.freeFeatures] : ["text", "image", "file", "video"];
  const proFeatures = Array.isArray(lic.proFeatures) ? [...lic.proFeatures] : ["shard", "strictMatching", "bulk", "whiteLabel", "schema", "sla"];
  return { sdkVersion: SDK_VERSION, formatVersion: ne, freeFeatures, proFeatures };
};
var cmdCapabilities = async (args) => {
  const opts = parseFlags(args, FLAG_TABLES.capabilities);
  if (opts.help) return process.stdout.write(USAGE);
  if (opts.origin === void 0) {
    printJson(offlineCapabilities());
    return;
  }
  const url = `${POLICY_ENDPOINT}?origin=${encodeURIComponent(opts.origin)}`;
  let res;
  try {
    res = await fetch(url, { headers: { accept: "application/json" } });
  } catch {
    throw codedError("CKX_TIMEOUT", "policy \u8BF7\u6C42\u5931\u8D25\uFF08\u7F51\u7EDC\u4E0D\u53EF\u8FBE\uFF09");
  }
  if (!res || !res.ok) {
    throw codedError("CKX_HTTP_ERROR", `policy \u8BF7\u6C42\u5931\u8D25 (HTTP ${res ? res.status : 0})`);
  }
  let data;
  try {
    data = await res.json();
  } catch {
    throw codedError("CKX_HTTP_ERROR", "policy \u54CD\u5E94\u4E0D\u662F\u5408\u6CD5 JSON");
  }
  printJson(data);
};
var cmdKeygen = async (args) => {
  const opts = parseFlags(args, FLAG_TABLES.keygen);
  if (opts.help) return process.stdout.write(USAGE);
  const pair = await Qt();
  SECRETS.push(pair.privateKey);
  if (opts.out !== void 0) {
    const prefix = opts.out;
    const pubPath = `${prefix}.pub`;
    const keyPath = `${prefix}.key`;
    fs.writeFileSync(pubPath, pair.publicKey, { mode: 420 });
    fs.chmodSync(pubPath, 420);
    fs.writeFileSync(keyPath, pair.privateKey, { mode: 384 });
    fs.chmodSync(keyPath, 384);
    printJson({ publicKey: pair.publicKey, path: keyPath });
    return;
  }
  process.stderr.write("[ckx] \u79C1\u94A5\u4EC5\u6B64\u4E00\u6B21\u8F93\u51FA\uFF0C\u8BF7\u59A5\u5584\u4FDD\u5B58\uFF08\u4E22\u5931\u540E\u65E0\u6CD5\u89E3\u5BC6\uFF09\u3002\n");
  printJson({ publicKey: pair.publicKey, privateKey: pair.privateKey });
};
var cmdEncryptTo = async (args) => {
  const opts = parseFlags(args, FLAG_TABLES["encrypt-to"]);
  if (opts.help) return process.stdout.write(USAGE);
  requireOpt(opts, "to");
  requireOpt(opts, "in");
  requireOpt(opts, "out");
  const recipientPublicKey = resolveRecipientPublicKey(opts.to);
  const bytes = readInputBytes(opts.in);
  const meta = { app: opts.app ?? DEFAULT_APP };
  if (opts.schema !== void 0) meta.schema = opts.schema;
  if (opts["schema-version"] !== void 0) meta.schemaVersion = opts["schema-version"];
  if (opts.label !== void 0) meta.label = opts.label;
  const payload = opts.text ? decodeTextStrict(bytes) : bytes;
  const container = await nr(payload, recipientPublicKey, meta);
  writeOutput(opts.out, container);
};
var cmdDecryptAs = async (args) => {
  const opts = parseFlags(args, FLAG_TABLES["decrypt-as"]);
  if (opts.help) return process.stdout.write(USAGE);
  requireOpt(opts, "in");
  if (opts.in === "-" && opts["key-stdin"]) {
    throw usageError("--in - \u4E0E --key-stdin \u4E0D\u80FD\u540C\u65F6\u4F7F\u7528\uFF08\u90FD\u4F1A\u6D88\u8D39 stdin\uFF09");
  }
  const privateKey = resolveRecipientPrivateKey(opts);
  const bytes = readInputBytes(opts.in);
  const schema = opts.schema ?? null;
  const r = await ir(toCarrier(bytes), privateKey, { schema });
  writeOutput(opts.out ?? "-", r.plaintext);
};
var cmdRecipientPub = async (args) => {
  const opts = parseFlags(args, FLAG_TABLES["recipient-pub"]);
  if (opts.help) return process.stdout.write(USAGE);
  const privateKey = resolveRecipientPrivateKey(opts);
  const publicKey = await er(privateKey);
  const fingerprint = await tr(publicKey);
  printJson({ publicKey, fingerprint });
};
var COMMANDS = {
  encrypt: cmdEncrypt,
  decrypt: cmdDecrypt,
  meta: cmdMeta,
  verify: cmdVerify,
  estimate: cmdEstimate,
  capabilities: cmdCapabilities,
  keygen: cmdKeygen,
  "encrypt-to": cmdEncryptTo,
  "decrypt-as": cmdDecryptAs,
  "recipient-pub": cmdRecipientPub
};
var main = async () => {
  const argv = process.argv.slice(2);
  if (argv.length === 0) {
    process.stderr.write(USAGE);
    process.exitCode = 2;
    return;
  }
  const first = argv[0];
  if (first === "--help" || first === "-h") {
    process.stdout.write(USAGE);
    return;
  }
  if (first === "--version" || first === "-v") {
    process.stdout.write(`${VERSION}
`);
    return;
  }
  const run = COMMANDS[first];
  if (!run) {
    process.stderr.write(`[ckx] \u672A\u77E5\u547D\u4EE4\uFF1A${first}

${USAGE}`);
    process.exitCode = 2;
    return;
  }
  await run(argv.slice(1));
};
main().catch((err) => {
  if (err instanceof UsageError) {
    process.stderr.write(`[ckx] ${redact(err.message)}
`);
    process.exitCode = 2;
    return;
  }
  const code = err && typeof err.code === "string" ? err.code : "CKX_ERROR";
  const message = redact(err && err.message ? err.message : String(err));
  process.stderr.write(JSON.stringify({ code, message }, null, 2) + "\n");
  process.exitCode = 1;
});
