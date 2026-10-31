import { test } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const repo = require('../repo.js');

// Fake fs: only statSync knows about files listed in `files` (absolute paths).
function fakeFs(files, { onWrite } = {}) {
  const written = new Map();
  return {
    written,
    statSync(p) {
      if (files.has(p)) return { isFile: () => true };
      const err = new Error('ENOENT: ' + p);
      err.code = 'ENOENT';
      throw err;
    },
    readFileSync(p) {
      if (written.has(p)) return written.get(p);
      const err = new Error('ENOENT: ' + p);
      err.code = 'ENOENT';
      throw err;
    },
    mkdirSync() {},
    writeSync() {},
    writeFileSync(p, data) {
      if (onWrite) onWrite(p, data);
      written.set(p, String(data));
    },
  };
}

const SRV = (dir) => path.join(dir, 'web', 'server.py');

test('priority 1: AIDOG_REPO env wins over everything', () => {
  const files = new Set([SRV('/env/repo'), SRV('/dev/repo'), SRV('/cwd/repo')]);
  const fsLike = fakeFs(files);
  const { repo: got } = repo.resolveRepo({
    env: { AIDOG_REPO: '/env/repo' },
    devDir: '/dev/repo',
    cwd: '/cwd/repo',
    cacheDir: '/cache',
    fsLike,
  });
  assert.equal(got, '/env/repo');
});

test('priority 2: dev-relative dir when it has web/server.py', () => {
  const files = new Set([SRV('/dev/repo'), SRV('/cwd/repo')]);
  const fsLike = fakeFs(files);
  const { repo: got } = repo.resolveRepo({
    env: {},
    devDir: '/dev/repo',
    cwd: '/cwd/repo',
    cacheDir: '/cache',
    fsLike,
  });
  assert.equal(got, '/dev/repo');
});

test('priority 3: cwd then cwd parent', () => {
  const fsLike = fakeFs(new Set([SRV('/cwd/repo')]));
  const { repo: got } = repo.resolveRepo({
    env: {},
    devDir: '/dev/repo', // invalid (no server.py)
    cwd: '/cwd/repo',
    cacheDir: '/cache',
    fsLike,
  });
  assert.equal(got, '/cwd/repo');

  const fsLike2 = fakeFs(new Set([SRV('/cwd')]));
  const got2 = repo.resolveRepo({
    env: {},
    devDir: '/dev/repo',
    cwd: '/cwd/repo', // /cwd/repo invalid, parent /cwd valid
    cacheDir: '/cache',
    fsLike: fsLike2,
  });
  assert.equal(got2.repo, '/cwd');
});

test('priority 4: userData cache last', () => {
  const fsLike = fakeFs(new Set([SRV('/cached/repo')]));
  fsLike.written.set('/cache/repo-cache.json', JSON.stringify({ repo: '/cached/repo' }));
  const { repo: got } = repo.resolveRepo({
    env: {},
    devDir: '/dev/repo',
    cwd: '/cwd/repo',
    cacheDir: '/cache',
    fsLike,
  });
  assert.equal(got, '/cached/repo');
});

test('no candidate: returns null (error.html path)', () => {
  const fsLike = fakeFs(new Set());
  const { repo: got, base } = repo.resolveRepo({
    env: {},
    devDir: '/dev/repo',
    cwd: '/cwd/repo',
    cacheDir: '/cache',
    fsLike,
  });
  assert.equal(got, null);
  assert.equal(base, 'http://127.0.0.1:8790');
});

test('base honors AIDOG_WEB_PORT for probe/spawn port', () => {
  assert.equal(repo.baseUrl({ AIDOG_WEB_PORT: '18790' }), 'http://127.0.0.1:18790');
  assert.equal(repo.baseUrl({}), 'http://127.0.0.1:8790');
  assert.equal(repo.baseUrl({ AIDOG_WEB_PORT: '' }), 'http://127.0.0.1:8790');
});

test('successful resolve refreshes the cache file (best-effort)', () => {
  const fsLike = fakeFs(new Set([SRV('/dev/repo')]));
  const { repo: got } = repo.resolveRepo({
    env: {},
    devDir: '/dev/repo',
    cwd: '/cwd/repo',
    cacheDir: '/cache',
    fsLike,
  });
  assert.equal(got, '/dev/repo');
  assert.equal(
    fsLike.written.get('/cache/repo-cache.json'),
    JSON.stringify({ repo: '/dev/repo' })
  );
});
