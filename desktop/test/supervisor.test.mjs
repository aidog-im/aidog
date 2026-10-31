import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { ensureConsole, stopSpawned } = require('../supervisor.js');

// ---- fakes -------------------------------------------------------------

// fetch fake: healthy only after `healthyAfter` total calls (>=1 means first
// call is unhealthy). Call count shared via the returned handle.
function makeFetch({ healthyAfter = 1, ok = true } = {}) {
  const state = { calls: 0 };
  const fn = async () => {
    state.calls += 1;
    return { ok: state.calls >= healthyAfter && ok, status: 200 };
  };
  return { fn, state };
}

function makeChild() {
  const child = new EventEmitter();
  child.killed = false;
  child.kill = () => {
    child.killed = true;
    return true;
  };
  return child;
}

function makeSpawn() {
  const state = { calls: [] };
  const fn = (cmd, args, opts) => {
    const child = makeChild();
    state.calls.push({ cmd, args, opts, child });
    return child;
  };
  return { fn, state };
}

const silent = () => {};
const BASE = 'http://127.0.0.1:18790';

// ---- attach ------------------------------------------------------------

test('probe healthy -> attached (no spawn)', async () => {
  const fetchFake = makeFetch({ healthyAfter: 1 });
  const spawnFake = makeSpawn();
  const res = await ensureConsole({
    repo: '/some/repo',
    base: BASE,
    fetchImpl: fetchFake.fn,
    spawnImpl: spawnFake.fn,
    logger: silent,
  });
  assert.equal(res.mode, 'attached');
  assert.equal(res.base, BASE);
  assert.equal(spawnFake.state.calls.length, 0);
  assert.equal(res.child, undefined);
});

test('probe healthy hits GET {base}/api/health', async () => {
  const seen = [];
  const fetchFake = makeFetch({ healthyAfter: 1 });
  const spy = async (url, init) => {
    seen.push({ url, method: init && init.method });
    return fetchFake.fn(url, init);
  };
  await ensureConsole({
    repo: '/some/repo',
    base: BASE,
    fetchImpl: spy,
    spawnImpl: makeSpawn().fn,
    logger: silent,
  });
  assert.equal(seen.length, 1);
  assert.equal(seen[0].url, `${BASE}/api/health`);
  assert.equal(seen[0].method, 'GET');
});

// ---- spawn -------------------------------------------------------------

test('probe unhealthy -> spawn python3 server.py in {repo}/web, then healthy', async () => {
  const fetchFake = makeFetch({ healthyAfter: 4 }); // 3 failures then OK
  const spawnFake = makeSpawn();
  const res = await ensureConsole({
    repo: '/some/repo',
    base: BASE,
    env: { AIDOG_WEB_PORT: '18790' },
    fetchImpl: fetchFake.fn,
    spawnImpl: spawnFake.fn,
    logger: silent,
    readyIntervalMs: 5,
  });
  assert.equal(res.mode, 'spawned');
  assert.equal(res.base, BASE);
  assert.equal(spawnFake.state.calls.length, 1);
  const call = spawnFake.state.calls[0];
  assert.equal(call.cmd, 'python3');
  assert.deepEqual(call.args, ['server.py']);
  assert.equal(call.opts.cwd, require('node:path').join('/some/repo', 'web'));
  assert.equal(call.opts.env.AIDOG_WEB_PORT, '18790');
  assert.equal(res.child, call.child);
  assert.equal(fetchFake.state.calls, 4);
});

test('base port derives from AIDOG_WEB_PORT when no base override', async () => {
  const fetchFake = makeFetch({ healthyAfter: 1 });
  const seen = [];
  const spy = async (url, init) => {
    seen.push(url);
    return fetchFake.fn(url, init);
  };
  const res = await ensureConsole({
    repo: '/some/repo',
    env: { AIDOG_WEB_PORT: '18791' },
    fetchImpl: spy,
    spawnImpl: makeSpawn().fn,
    logger: silent,
  });
  assert.equal(res.base, 'http://127.0.0.1:18791');
  assert.equal(seen[0], 'http://127.0.0.1:18791/api/health');
});

// ---- timeout -----------------------------------------------------------

test('spawn never becomes healthy -> throws, child killed', async () => {
  const fetchFake = makeFetch({ healthyAfter: Number.MAX_SAFE_INTEGER });
  const spawnFake = makeSpawn();
  await assert.rejects(
    () =>
      ensureConsole({
        repo: '/some/repo',
        base: BASE,
        fetchImpl: fetchFake.fn,
        spawnImpl: spawnFake.fn,
        logger: silent,
        readyTimeoutMs: 30,
        readyIntervalMs: 5,
      }),
    /did not become healthy/
  );
  assert.equal(spawnFake.state.calls.length, 1);
  assert.equal(spawnFake.state.calls[0].child.killed, true);
});

test('unhealthy + no repo -> throws clear message, nothing spawned', async () => {
  const spawnFake = makeSpawn();
  await assert.rejects(
    () =>
      ensureConsole({
        repo: null,
        base: BASE,
        fetchImpl: makeFetch({ healthyAfter: Number.MAX_SAFE_INTEGER }).fn,
        spawnImpl: spawnFake.fn,
        logger: silent,
      }),
    /no install directory/
  );
  assert.equal(spawnFake.state.calls.length, 0);
});

// ---- stopSpawned -------------------------------------------------------

test('stopSpawned kills only a spawned child', async () => {
  const res = await ensureConsole({
    repo: '/some/repo',
    base: BASE,
    fetchImpl: makeFetch({ healthyAfter: 2 }).fn,
    spawnImpl: makeSpawn().fn,
    logger: silent,
    readyIntervalMs: 5,
  });
  assert.equal(res.mode, 'spawned');
  assert.equal(stopSpawned(res), true);
  assert.equal(res.child.killed, true);
});

test('stopSpawned in attached mode is a no-op', async () => {
  const res = await ensureConsole({
    repo: '/some/repo',
    base: BASE,
    fetchImpl: makeFetch({ healthyAfter: 1 }).fn,
    spawnImpl: makeSpawn().fn,
    logger: silent,
  });
  assert.equal(res.mode, 'attached');
  assert.equal(stopSpawned(res), false);
  assert.equal(stopSpawned(null), false);
  assert.equal(stopSpawned({ mode: 'attached', base: BASE }), false);
});
