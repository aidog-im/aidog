'use strict';
// Repo resolution for the I'm AiDog desktop shell.
// Priority: AIDOG_REPO env -> dev-relative dir -> cwd and cwd parent ->
//           last-known cache in Electron userData -> null.
// Pure Node (no Electron imports) so node --test can cover the priority
// order with injected candidates and an injected fs-like object.

const path = require('path');

const DEFAULT_PORT = '8790';

function portFromEnv(env) {
  const raw = env && env.AIDOG_WEB_PORT;
  return raw && String(raw).trim() ? String(raw).trim() : DEFAULT_PORT;
}

function baseUrl(env) {
  return `http://127.0.0.1:${portFromEnv(env)}`;
}

// A candidate repo is valid when <dir>/web/server.py exists.
function isRepo(dir, fsLike) {
  if (!dir) return false;
  try {
    return fsLike.statSync(path.join(dir, 'web', 'server.py')).isFile();
  } catch {
    return false;
  }
}

// Pure core: pick the first valid candidate from an ordered list.
// Returns that candidate, or null.
function pickCandidate(candidates, fsLike) {
  for (const c of candidates) {
    if (isRepo(c, fsLike)) return c;
  }
  return null;
}

// Cache file lives in Electron userData (best-effort read/write).
function readCache(cacheDir, fsLike) {
  try {
    const raw = fsLike.readFileSync(path.join(cacheDir, 'repo-cache.json'), 'utf8');
    const parsed = JSON.parse(raw);
    return typeof parsed.repo === 'string' ? parsed.repo : null;
  } catch {
    return null;
  }
}

function writeCache(cacheDir, repo, fsLike) {
  try {
    fsLike.mkdirSync(cacheDir, { recursive: true });
    fsLike.writeFileSync(
      path.join(cacheDir, 'repo-cache.json'),
      JSON.stringify({ repo }),
      'utf8'
    );
    return true;
  } catch {
    return false;
  }
}

// Ordered candidate list for a given environment.
// env: process.env-like; opts: { devDir, cwd, cacheDir, fsLike }
function candidates(env, opts) {
  const fsLike = opts.fsLike || require('fs');
  const list = [];
  if (env && env.AIDOG_REPO) list.push(env.AIDOG_REPO);
  if (opts.devDir) list.push(opts.devDir);
  if (opts.cwd) {
    list.push(opts.cwd);
    list.push(path.dirname(opts.cwd));
  }
  if (opts.cacheDir) {
    const cached = readCache(opts.cacheDir, fsLike);
    if (cached) list.push(cached);
  }
  // de-dupe preserving order
  const seen = new Set();
  return list.filter((c) => c && !seen.has(c) && seen.add(c));
}

/**
 * resolveRepo({ env, devDir, cwd, cacheDir, fsLike })
 * Returns { repo, base } or { repo: null, base }.
 */
function resolveRepo(opts) {
  const env = opts.env || {};
  const fsLike = opts.fsLike || require('fs');
  const base = baseUrl(env);
  const repo = pickCandidate(candidates(env, opts), fsLike);
  if (repo && opts.cacheDir) writeCache(opts.cacheDir, repo, fsLike); // best-effort refresh
  return { repo, base };
}

module.exports = {
  DEFAULT_PORT,
  portFromEnv,
  baseUrl,
  isRepo,
  pickCandidate,
  candidates,
  readCache,
  writeCache,
  resolveRepo,
};
