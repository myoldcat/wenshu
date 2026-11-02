import assert from 'node:assert/strict';
import { test, beforeEach } from 'node:test';
import { webcrypto } from 'node:crypto';
import worker from './worker.mjs';
if (!globalThis.crypto) globalThis.crypto = webcrypto;
const base = 'https://demo.example.org';
const password = 'test-only-not-a-real-secret-2026';
let forwarded;
let env;
beforeEach(() => {
  forwarded = [];
  env = { DEMO_HOST: 'demo.example.org', DEMO_PASSWORD: password, LOGIN_LIMITER: { limit: async () => ({ success: true }) } };
  globalThis.fetch = async request => {
    forwarded.push(request);
    return new Response(new URL(request.url).pathname, { headers: { 'Cache-Control': 'public, max-age=3600', 'Access-Control-Allow-Origin': '*' } });
  };
});
function req(path, options = {}) { return new Request(base + path, options); }
async function login(extra = {}, pass = password) {
  return worker.fetch(req('/_demo/login', {
    method: 'POST', headers: { Origin: base, 'CF-Connecting-IP': '192.0.2.1', 'Content-Type': 'application/x-www-form-urlencoded', ...extra },
    body: new URLSearchParams({ password: pass, next: '/?demo=1' }),
  }), env);
}
async function session() {
  const result = await login(); assert.equal(result.status, 303);
  return result.headers.get('Set-Cookie').split(';')[0];
}
test('every unauthenticated page, asset, API and HTTP method is stopped before upstream', async () => {
  for (const path of ['/', '/api/health', '/api/documents', '/src/main.jsx', '/assets/app.js', '/download/private.pdf', '/%61pi/documents', '/api%2fdocuments']) {
    for (const method of ['GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'OPTIONS']) {
      const response = await worker.fetch(req(path, { method, headers: { Origin: base } }), env);
      assert.equal(response.status, 401, `${method} ${path}`);
      assert.match(response.headers.get('Cache-Control'), /no-store/);
    }
  }
  assert.equal(forwarded.length, 0);
});
test('HTML navigation shows escaped form, including a link opened from another site', async () => {
  const response = await worker.fetch(req('/?x=%22%3E%3Cscript%3E', { headers: { Accept: 'text/html', 'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document' } }), env);
  assert.equal(response.status, 200);
  const html = await response.text(); assert.match(html, /演示口令/); assert.doesNotMatch(html, /value="[^"\n]*<script>/);
  assert.equal(forwarded.length, 0);
});
test('Access cookies and headers are never accepted as demo authentication', async () => {
  const response = await worker.fetch(req('/api/documents', { headers: { Cookie: 'CF_Authorization=forged', 'cf-access-jwt-assertion': 'forged', 'x-demo-auth': 'yes' } }), env);
  assert.equal(response.status, 401); assert.equal(forwarded.length, 0);
});
test('valid session allows pages and API, strips gateway cookies and prevents shared caching', async () => {
  const cookie = await session();
  assert.match(cookie, /^__Host-wenshu_demo=/);
  for (const path of ['/', '/api/health', '/src/main.jsx']) {
    const response = await worker.fetch(req(path, { headers: { Cookie: `${cookie}; CF_Authorization=old; app=value`, 'cf-access-jwt-assertion': 'spoofed' } }), env);
    assert.equal(response.status, 200); assert.match(response.headers.get('Cache-Control'), /no-store/);
    assert.equal(response.headers.get('Access-Control-Allow-Origin'), null);
    const upstream = forwarded.at(-1);
    assert.equal(upstream.url, base + path); assert.equal(upstream.headers.get('Cookie'), 'app=value'); assert.equal(upstream.headers.get('cf-access-jwt-assertion'), null);
  }
});
test('cookie is Secure, HttpOnly, Strict, host-only and expires in four hours', async () => {
  const response = await login();
  const value = response.headers.get('Set-Cookie');
  for (const fragment of ['Path=/', 'Max-Age=14400', 'HttpOnly', 'Secure', 'SameSite=Strict']) assert.ok(value.includes(fragment));
  assert.ok(!value.includes('Domain=')); assert.equal(response.headers.get('Location'), '/?demo=1');
});
test('wrong password, tampered signature and duplicate cookie cannot reach origin', async () => {
  assert.equal((await login({}, 'wrong-password')).status, 401);
  const cookie = await session();
  const last = cookie.at(-1); const tampered = cookie.slice(0, -1) + (last === 'A' ? 'B' : 'A');
  for (const value of [tampered, `${cookie}; ${cookie}`, '__Host-wenshu_demo=yes', cookie.replace('v1.', 'v2.')]) {
    assert.equal((await worker.fetch(req('/api/health', { headers: { Cookie: value } }), env)).status, 401);
  }
  assert.equal(forwarded.length, 0);
});
test('password rotation immediately invalidates existing session', async () => {
  const cookie = await session(); env.DEMO_PASSWORD = 'different-test-only-secret-2026';
  assert.equal((await worker.fetch(req('/api/health', { headers: { Cookie: cookie } }), env)).status, 401);
});
test('expired sessions cannot reach the origin', async () => {
  const cookie = await session(); const original = Date.now;
  try { Date.now = () => original() + 14401000; assert.equal((await worker.fetch(req('/api/health', { headers: { Cookie: cookie } }), env)).status, 401); }
  finally { Date.now = original; }
});
test('login, logout and API writes require same-origin browser requests', async () => {
  const cookie = await session();
  for (const origin of ['', 'https://evil.example', 'null']) {
    for (const path of ['/_demo/login', '/_demo/logout', '/api/__probe__']) {
      const response = await worker.fetch(req(path, { method: 'POST', headers: { Origin: origin, Cookie: cookie } }), env);
      assert.equal(response.status, 403);
    }
  }
  assert.equal(forwarded.length, 0);
});
test('rate limiting and limiter failures fail closed', async () => {
  env.LOGIN_LIMITER.limit = async () => ({ success: false });
  assert.equal((await login()).status, 429);
  env.LOGIN_LIMITER.limit = async () => { throw new Error('offline'); };
  assert.equal((await login()).status, 503);
  delete env.LOGIN_LIMITER;
  assert.equal((await worker.fetch(req('/'), env)).status, 503);
});
test('missing/weak secret fails closed, unexpected host does not proxy, HTTP upgrades to HTTPS', async () => {
  for (const pass of [undefined, '', '123456']) { env.DEMO_PASSWORD = pass; assert.equal((await worker.fetch(req('/'), env)).status, 503); }
  env.DEMO_PASSWORD = password;
  assert.equal((await worker.fetch(new Request('https://alternate.workers.dev/api/health'), env)).status, 404);
  assert.equal((await worker.fetch(new Request('https://demo.example.org:8443/'), env)).status, 404);
  assert.equal((await worker.fetch(new Request('http://demo.example.org/'), env)).status, 308);
  assert.equal(forwarded.length, 0);
});
test('oversized forms are rejected, external redirects collapse to root', async () => {
  const response = await worker.fetch(req('/_demo/login', { method: 'POST', headers: { Origin: base, 'CF-Connecting-IP': '192.0.2.1', 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'password=' + 'a'.repeat(5000) }), env);
  assert.equal(response.status, 400);
  for (const next of ['//evil.example/', '/\\evil.example/', '/_demo/logout', 'https://evil.example/']) {
    const result = await worker.fetch(req('/_demo/login', { method: 'POST', headers: { Origin: base, 'CF-Connecting-IP': '192.0.2.1', 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ password, next }) }), env);
    assert.equal(result.headers.get('Location'), '/');
  }
});
test('logout clears only gateway cookie and origin failures never pass through', async () => {
  const result = await worker.fetch(req('/_demo/logout', { method: 'POST', headers: { Origin: base } }), env);
  assert.equal(result.status, 303); assert.match(result.headers.get('Set-Cookie'), /Max-Age=0/);
  const cookie = await session(); globalThis.fetch = async () => { throw new Error('origin offline'); };
  assert.equal((await worker.fetch(req('/api/health', { headers: { Cookie: cookie } }), env)).status, 503);
});
test('authenticated API writes preserve method, body and query string', async () => {
  const cookie = await session();
  let seen;
  globalThis.fetch = async request => {
    seen = { url: request.url, method: request.method, body: await request.text(), type: request.headers.get('Content-Type') };
    return new Response('{"ok":true}', { headers: { 'Content-Type': 'application/json' } });
  };
  const response = await worker.fetch(req('/api/__synthetic_probe__?v=1', { method: 'POST', headers: { Origin: base, Cookie: cookie, 'Content-Type': 'application/json' }, body: '{"probe":true}' }), env);
  assert.equal(response.status, 200);
  assert.deepEqual(seen, { url: base + '/api/__synthetic_probe__?v=1', method: 'POST', body: '{"probe":true}', type: 'application/json' });
});
test('SSE response streams immediately and WebSocket handshake keeps its upgrade object', async () => {
  const cookie = await session(); let controller;
  const stream = new ReadableStream({ start(value) { controller = value; value.enqueue(new TextEncoder().encode('data: first\n\n')); } });
  globalThis.fetch = async () => new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } });
  const response = await worker.fetch(req('/api/__synthetic_stream__', { headers: { Cookie: cookie } }), env);
  const reader = response.body.getReader();
  assert.equal(new TextDecoder().decode((await reader.read()).value), 'data: first\n\n');
  controller.close(); assert.equal((await reader.read()).done, true);
  const upgrade = { status: 101, webSocket: { synthetic: true } };
  globalThis.fetch = async () => upgrade;
  assert.equal(await worker.fetch(req('/__synthetic_ws__', { headers: { Cookie: cookie, Origin: base, Upgrade: 'websocket' } }), env), upgrade);
});
test('public login can be opened with incomplete or opaque navigation metadata, without opening the API', async () => {
  for (const headers of [
    { 'Sec-Fetch-Site': 'cross-site' },
    { Origin: 'null', 'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Dest': 'empty' },
    { Origin: 'https://external.example', 'Sec-Fetch-Mode': 'no-cors' },
  ]) {
    const response = await worker.fetch(req('/', { headers: { ...headers, Accept: 'text/html' } }), env);
    assert.equal(response.status, 200); assert.match(await response.text(), /演示口令/);
    assert.equal((await worker.fetch(req('/api/health', { headers }), env)).status, 401);
    assert.equal((await worker.fetch(req('/_demo/login', { method: 'POST', headers }), env)).status, 403);
  }
  assert.equal(forwarded.length, 0);
});
test('an authenticated cross-origin read or WebSocket cannot reach the origin', async () => {
  const cookie = await session();
  assert.equal((await worker.fetch(req('/api/health', { headers: { Cookie: cookie, Origin: 'https://other.example.org' } }), env)).status, 403);
  assert.equal((await worker.fetch(req('/__synthetic_ws__', { headers: { Cookie: cookie, Upgrade: 'websocket' } }), env)).status, 403);
  assert.equal(forwarded.length, 0);
});
test('HTML form login preserves same-origin metadata while rejecting opaque-origin submissions', async () => {
  const response = await worker.fetch(req('/_demo/login'), env);
  assert.equal(response.headers.get('Referrer-Policy'), 'same-origin');
  assert.equal((await login({ Origin: 'null' })).status, 403);
  assert.equal((await login()).status, 303);
});
test('origin fetch uses no-store without the incompatible Cloudflare TTL override', async () => {
  const cookie = await session(); let options;
  globalThis.fetch = async (request, init) => { options = init; return new Response('ok'); };
  assert.equal((await worker.fetch(req('/', { headers: { Cookie: cookie } }), env)).status, 200);
  assert.deepEqual(options, { cache: 'no-store' });
});

test('missing or invalid explicit host never derives a deployment from request headers', async () => {
  for (const host of [undefined, '', '*', '*.example.org', 'https://demo.example.org', 'demo.example.org:443',
    'demo.example.org/', 'user@demo.example.org', 'demo.example.org evil', '999.1.2.3', '127.1']) {
    env.DEMO_HOST = host;
    const response = await worker.fetch(req('/', { headers: { Host: 'demo.example.org', 'X-Forwarded-Host': 'demo.example.org', Accept: 'text/html' } }), env);
    assert.equal(response.status, 503, String(host));
    assert.match(response.headers.get('Cache-Control'), /no-store/);
  }
  assert.equal(forwarded.length, 0);
});

test('configured host normalization preserves host-bound session signatures', async () => {
  env.DEMO_HOST = ' DEMO.EXAMPLE.ORG ';
  const cookie = await session();
  env.DEMO_HOST = 'other.example.org';
  assert.equal((await worker.fetch(new Request('https://other.example.org/api/health', { headers: { Cookie: cookie } }), env)).status, 401);
  assert.equal((await worker.fetch(req('/api/health', { headers: { Cookie: cookie } }), env)).status, 404);
  assert.equal(forwarded.length, 0);
});
