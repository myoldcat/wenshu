// Runs on a Cloudflare *Route*, in front of the existing Tunnel DNS record.
// Never configure this script as a Custom Domain pointing to itself.
const COOKIE = '__Host-wenshu_demo';
const TTL = 4 * 60 * 60;
const encoder = new TextEncoder();
const security = {
  'Cache-Control': 'private, no-store, max-age=0',
  'CDN-Cache-Control': 'no-store',
  'Cloudflare-CDN-Cache-Control': 'no-store',
  // no-referrer turns Origin into "null" for ordinary HTML form POSTs.
  // same-origin preserves the exact Origin required by the login CSRF check.
  'Referrer-Policy': 'same-origin',
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
};

function reply(body, status = 200, extra = {}) {
  return new Response(body, { status, headers: { ...security, ...extra } });
}
function json(error, status, extra = {}) {
  return reply(JSON.stringify({ error }), status, { 'Content-Type': 'application/json; charset=utf-8', ...extra });
}
function escapeHtml(value) {
  return value.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}
function configuredHost(value) {
  if (typeof value !== 'string') return null;
  const host = value.trim().toLowerCase();
  if (!host || host.length > 253 || !host.split('.').every(part => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(part))) return null;
  try { return new URL(`https://${host}`).hostname === host ? host : null; }
  catch { return null; }
}
function safeNext(value, origin) {
  if (!value || !value.startsWith('/') || /[\\\r\n]/.test(value)) return '/';
  try {
    const next = new URL(value, origin);
    if (next.origin !== origin || next.pathname.startsWith('/_demo/')) return '/';
    return next.pathname + next.search;
  } catch { return '/'; }
}
function loginPage(origin, next = '/', message = '', status = 200) {
  return reply(`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>文枢 · 演示访问</title><style>
  *{box-sizing:border-box}body{margin:0;min-height:100dvh;display:grid;place-items:center;padding:24px;background:#edf3ef;color:#223d34;font:16px/1.6 system-ui,"Microsoft YaHei",sans-serif}main{width:100%;max-width:420px;padding:40px;border:1px solid #d4e1d9;border-radius:24px;background:#fff;box-shadow:0 20px 70px #24463812}.mark{font-size:13px;letter-spacing:3px;color:#638575}h1{font-size:28px;font-weight:600;margin:10px 0}p{color:#667a70;margin:0 0 28px}label{display:block;font-size:14px;margin-bottom:8px}input,button{width:100%;font:inherit;border-radius:10px;padding:12px 14px}input{border:1px solid #b7cbbf;background:#fcfdfb;color:#203f31}input:focus{outline:2px solid #94bba5;outline-offset:2px}button{margin-top:18px;border:0;background:#285f49;color:white;cursor:pointer}button:hover{background:#204c3b}.message{color:#a53728;font-size:14px;margin:14px 0 0}.note{font-size:12px;color:#7b8982;margin:22px 0 0}
  </style><main><div class="mark">WENSHU · DEMO</div><h1>欢迎体验文枢</h1><p>请输入分享者提供的演示口令。</p><form method="post" action="/_demo/login"><label for="password">演示口令</label><input id="password" name="password" type="password" autocomplete="current-password" minlength="16" maxlength="256" required autofocus><input type="hidden" name="next" value="${escapeHtml(safeNext(next, origin))}"><button type="submit">进入文枢</button></form>${message ? `<div class="message" role="alert">${escapeHtml(message)}</div>` : ''}<div class="note">验证后可访问 4 小时。请勿公开转发演示口令。</div></main></html>`, status, {
    'Content-Type': 'text/html; charset=utf-8',
    'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
  });
}
function base64url(bytes) {
  return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function unbase64url(value) {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) throw new Error('invalid encoding');
  const bytes = Uint8Array.from(atob(value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4)), c => c.charCodeAt(0));
  if (base64url(bytes) !== value) throw new Error('non-canonical encoding');
  return bytes;
}
async function digest(value) { return new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(value))); }
async function sessionKey(password, host) {
  // A domain-separated key: rotating the password invalidates existing sessions.
  return crypto.subtle.importKey('raw', await digest(`wenshu-demo-session-v1\0${host}\0${password}`), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']);
}
async function equalPassword(given, actual) {
  const a = await digest(given), b = await digest(actual);
  let difference = 0;
  for (let i = 0; i < a.length; i++) difference |= a[i] ^ b[i];
  return difference === 0;
}
function readCookie(request) {
  const values = (request.headers.get('Cookie') || '').split(';').map(s => s.trim()).filter(s => s.startsWith(COOKIE + '='));
  return values.length === 1 ? values[0].slice(COOKIE.length + 1) : '';
}
async function validSession(request, password, host) {
  try {
    const parts = readCookie(request).split('.');
    if (parts.length !== 5) return false;
    const [version, issued, expires, nonce, signature] = parts;
    const now = Math.floor(Date.now() / 1000);
    if (version !== 'v1' || !/^\d{10}$/.test(issued) || !/^\d{10}$/.test(expires) || !/^[A-Za-z0-9_-]{22}$/.test(nonce)) return false;
    if (+issued > now + 30 || +expires <= now || +expires - +issued !== TTL) return false;
    return await crypto.subtle.verify('HMAC', await sessionKey(password, host), unbase64url(signature), encoder.encode(`${host}\0${parts.slice(0, 4).join('.')}`));
  } catch { return false; }
}
async function issueSession(password, host) {
  const now = Math.floor(Date.now() / 1000);
  const payload = `v1.${now}.${now + TTL}.${base64url(crypto.getRandomValues(new Uint8Array(16)))}`;
  return `${payload}.${base64url(await crypto.subtle.sign('HMAC', await sessionKey(password, host), encoder.encode(`${host}\0${payload}`)))}`;
}
function cookie(value, age) { return `${COOKIE}=${value}; Path=/; Max-Age=${age}; HttpOnly; Secure; SameSite=Strict`; }
async function boundedForm(request) {
  if (!request.headers.get('Content-Type')?.toLowerCase().startsWith('application/x-www-form-urlencoded')) throw new Error('bad form');
  if (Number(request.headers.get('Content-Length')) > 4096) throw new Error('too large');
  const reader = request.body?.getReader();
  if (!reader) throw new Error('empty form');
  const chunks = []; let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read(); if (done) break;
      size += value.length;
      if (size > 4096) { await reader.cancel(); throw new Error('too large'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return new URLSearchParams(new TextDecoder().decode(bytes));
}

async function handle(request, env) {
  const host = configuredHost(env?.DEMO_HOST);
  if (!host) return json('demo_unavailable', 503);
  const configuredOrigin = `https://${host}`;
  const url = new URL(request.url);
  if (url.hostname !== host || url.port) return json('not_found', 404);
  if (url.protocol !== 'https:') return reply(null, 308, { Location: `${configuredOrigin}${url.pathname}${url.search}` });
  if (typeof env.DEMO_PASSWORD !== 'string' || env.DEMO_PASSWORD.length < 16 || env.DEMO_PASSWORD.length > 256 || !env.LOGIN_LIMITER?.limit) return json('demo_unavailable', 503);
  const origin = request.headers.get('Origin');
  const unsafe = !['GET', 'HEAD', 'OPTIONS'].includes(request.method);
  // Writes, including login/logout, always require an exact Origin. Do not
  // reject the public login form based on Fetch Metadata: browser navigation
  // tools and external share links do not all send the same metadata headers.
  if (unsafe && origin !== configuredOrigin) return json('forbidden_origin', 403);

  if (url.pathname === '/_demo/logout') {
    if (request.method !== 'POST') return reply('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>退出演示</title><form method="post"><button>退出文枢演示</button></form></html>', 200, { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'" });
    return reply(null, 303, { Location: '/_demo/login', 'Set-Cookie': cookie('', 0) });
  }
  if (url.pathname === '/_demo/login') {
    if (request.method === 'GET') return loginPage(configuredOrigin, url.searchParams.get('next'));
    if (request.method !== 'POST') return json('method_not_allowed', 405, { Allow: 'GET, POST' });
    // The binding is account-configured, shared across isolates at each CF location.
    const ip = request.headers.get('CF-Connecting-IP');
    if (!ip) return json('demo_unavailable', 503);
    if (!(await env.LOGIN_LIMITER.limit({ key: `wenshu-login:${ip}` })).success) return json('too_many_attempts', 429, { 'Retry-After': '60' });
    let form;
    try { form = await boundedForm(request); } catch { return json('invalid_form', 400); }
    const supplied = form.getAll('password');
    if (supplied.length !== 1 || supplied[0].length > 256 || !(await equalPassword(supplied[0], env.DEMO_PASSWORD))) return loginPage(configuredOrigin, form.get('next'), '演示口令不正确，请重新输入。', 401);
    return reply(null, 303, { Location: safeNext(form.get('next'), configuredOrigin), 'Set-Cookie': cookie(await issueSession(env.DEMO_PASSWORD, host), TTL) });
  }
  if (url.pathname.startsWith('/_demo/')) return json('not_found', 404);
  if (!(await validSession(request, env.DEMO_PASSWORD, host))) {
    if (request.method === 'GET' && !/^\/api(?:\/|$)/i.test(url.pathname) && (request.headers.get('Sec-Fetch-Dest') === 'document' || request.headers.get('Accept')?.includes('text/html'))) return loginPage(configuredOrigin, url.pathname + url.search);
    return json('demo_login_required', 401);
  }
  // Authenticated CORS reads and WebSocket handshakes remain same-origin only.
  if ((origin && origin !== configuredOrigin) || (request.headers.get('Upgrade')?.toLowerCase() === 'websocket' && origin !== configuredOrigin)) return json('forbidden_origin', 403);
  // Only the configured same-host Tunnel origin can be fetched. User input cannot
  // select a hostname/port. A same-zone Route subrequest bypasses this Route.
  const headers = new Headers(request.headers);
  const filteredCookies = (headers.get('Cookie') || '').split(';').map(s => s.trim()).filter(s => s && !s.startsWith(COOKIE + '=') && !s.startsWith('CF_Authorization='));
  if (filteredCookies.length) headers.set('Cookie', filteredCookies.join('; ')); else headers.delete('Cookie');
  for (const name of ['cf-access-jwt-assertion', 'cf-access-authenticated-user-email', 'cf-access-client-id', 'cf-access-client-secret']) headers.delete(name);
  let upstream, response;
  try {
    upstream = new Request(request, { headers, redirect: 'manual' });
    // cache:no-store must not be combined with cf.cacheTtl, even when TTL is 0.
    response = await fetch(upstream, { cache: 'no-store' });
  } catch {
    return reply(JSON.stringify({ error: 'demo_unavailable', stage: 'origin_fetch' }), 503, { 'Content-Type': 'application/json; charset=utf-8' });
  }
  // WebSocket handshake needs its original webSocket object/status preserved.
  if (response.status === 101) return response;
  const result = new Response(response.body, response);
  for (const [name, value] of Object.entries(security)) result.headers.set(name, value);
  result.headers.delete('Access-Control-Allow-Origin');
  result.headers.delete('Access-Control-Allow-Credentials');
  return result;
}

export default {
  async fetch(request, env) {
    try { return await handle(request, env); }
    catch { return json('demo_unavailable', 503); } // Never pass through on failure.
  },
};
