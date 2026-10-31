'use strict';
// Console supervisor for the I'm AiDog desktop shell.
// Pure Node -- NO Electron imports. Probe GET {base}/api/health:
//   healthy  -> { mode: 'attached', base }          (never killed by us)
//   unhealthy -> spawn `python3 server.py` (cwd <repo>/web), poll health
//                up to ~10s -> { mode: 'spawned', base, child }
//   failure -> throws with a clear message.
// All collaborators are injectable (fetchImpl / spawnImpl / logger) so
// node --test can exercise every branch with fakes -- no real ports.

const { baseUrl, portFromEnv } = require('./repo');

const PROBE_TIMEOUT_MS = 1500;
const READY_TIMEOUT_MS = 10000;
const READY_INTERVAL_MS = 250;

function defaultLogger() {
  /* silent by default */
}

// Probe {base}/api/health. Resolves true only on a 2xx JSON-ish answer.
async function probeHealth(base, fetchImpl) {
  try {
    const res = await fetchImpl(`${base}/api/health`, {
      method: 'GET',
      signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
    });
    return !!(res && res.ok);
  } catch {
    return false;
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

/**
 * ensureConsole({ repo, base, fetchImpl, spawnImpl, logger, readyTimeoutMs, readyIntervalMs })
 * - base: optional override; default derived from AIDOG_WEB_PORT of process.env.
 * Returns { mode, base, child? } -- child only when mode === 'spawned'.
 */
async function ensureConsole(opts = {}) {
  const env = opts.env || process.env;
  const fetchImpl = opts.fetchImpl || ((...a) => fetch(...a));
  const spawnImpl = opts.spawnImpl || require('child_process').spawn;
  const logger = opts.logger || defaultLogger;
  const base = opts.base || baseUrl(env);
  const repo = opts.repo;
  const readyTimeoutMs = opts.readyTimeoutMs ?? READY_TIMEOUT_MS;
  const readyIntervalMs = opts.readyIntervalMs ?? READY_INTERVAL_MS;

  if (await probeHealth(base, fetchImpl)) {
    logger(`console healthy at ${base} -- attaching`);
    return { mode: 'attached', base };
  }

  if (!repo) {
    throw new Error(
      `Local console at ${base} is not reachable and no install directory is known to start one.`
    );
  }

  logger(`console unhealthy at ${base} -- spawning python3 server.py in ${repo}/web`);
  const child = spawnImpl('python3', ['server.py'], {
    cwd: require('path').join(repo, 'web'),
    env: { ...env, AIDOG_WEB_PORT: portFromEnv(env) },
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  child.on?.('error', (err) => logger(`spawn error: ${err.message}`));

  const deadline = Date.now() + readyTimeoutMs;
  for (;;) {
    if (await probeHealth(base, fetchImpl)) {
      return { mode: 'spawned', base, child };
    }
    if (Date.now() >= deadline) {
      try {
        child.kill();
      } catch {
        /* best effort */
      }
      throw new Error(
        `Spawned console in ${repo}/web did not become healthy at ${base} within ${readyTimeoutMs}ms.`
      );
    }
    await sleep(readyIntervalMs);
  }
}

/**
 * stopSpawned(state) -- kills ONLY a child this shell spawned.
 * `state` is the object returned by ensureConsole; attached mode (no child)
 * is a no-op by design: never touch a pre-existing server.
 */
function stopSpawned(state) {
  if (!state || state.mode !== 'spawned' || !state.child) return false;
  try {
    state.child.kill();
    return true;
  } catch {
    return false;
  }
}

module.exports = { ensureConsole, stopSpawned, probeHealth, PROBE_TIMEOUT_MS, READY_TIMEOUT_MS };
