import test from 'node:test';
import assert from 'node:assert/strict';
import { enforceOperationsPrivateHost, isOperationsPath, normalizedOperationsPath, operationsHostAllowed, privateOperationsPlugin } from '../operations-access.mjs';

test('only exact private hosts and valid ports are accepted', () => {
  for (const host of ['127.0.0.1', 'localhost:4173']) assert.equal(operationsHostAllowed(host, {}), true, host);
  for (const host of ['public.example', 'app.test:4173', '192.0.2.10:4173', 'user@localhost', 'localhost.', 'localhost:0', 'localhost:70000', 'localhost:4173/ops', '', undefined]) assert.equal(operationsHostAllowed(host, {}), false, String(host));
});

test('private operations require their own explicit host setting', () => {
  const environment = { WENSHU_ALLOWED_HOSTS: 'public.example', WENSHU_OPERATIONS_ALLOWED_HOSTS: ' APP.TEST, 192.0.2.10,app.test ' };
  for (const host of ['APP.TEST:4173', '192.0.2.10:4173', '127.0.0.1', 'localhost']) assert.equal(operationsHostAllowed(host, environment), true, host);
  for (const host of ['public.example', 'app.test.public.example', 'user@app.test', 'app.test.', '192.0.2.11']) assert.equal(operationsHostAllowed(host, environment), false, host);
  assert.equal(operationsHostAllowed('public.example', { WENSHU_ALLOWED_HOSTS: 'public.example' }), false);
});

test('invalid private host configuration fails closed and prevents plugin startup', () => {
  for (const value of ['*', '*.example.com', '.example.com', 'http://app.test', 'app.test:4173', 'app.test,,localhost', '999.1.2.3', 'app.test evil', '-app.test']) {
    const environment = { WENSHU_OPERATIONS_ALLOWED_HOSTS: value };
    assert.equal(operationsHostAllowed('localhost', environment), false, value);
    assert.throws(() => privateOperationsPlugin(environment), /WENSHU_OPERATIONS_ALLOWED_HOSTS/);
  }
});

test('URL decoding, duplicate slashes and dot segments cannot bypass protected paths', () => {
  for (const path of ['/operations.html', '/operations.html?theme=glass', '/%6fperations.html', '/%256fperations.html', '//operations.html', '/foo/../operations.html', '/foo/%2e%2e/operations.html', '/api/operations/writing/projects', '/api/%6fperations/writing/projects', '/api//operations/writing/projects', '/api/x/../operations/writing/projects', '/api%2foperations/writing/projects']) assert.equal(isOperationsPath(path), true, path);
  assert.equal(normalizedOperationsPath('/api//operations/./writing/projects'), '/api/operations/writing/projects');
  for (const path of ['/', '/index.html', '/api/writing/projects', '/src/operations-main.jsx', '/api/operations-other']) assert.equal(isOperationsPath(path), false, path);
});

test('dev and preview install the same pre-proxy guard', () => {
  const plugin = privateOperationsPlugin({}), middleware = [];
  plugin.configureServer({ middlewares: { use: fn => middleware.push(fn) } });
  plugin.configurePreviewServer({ middlewares: { use: fn => middleware.push(fn) } });
  assert.equal(middleware.length, 2);
  assert.equal(middleware[0], middleware[1]);
});

test('middleware rejects public operations paths including encoded forms without changing main application', () => {
  const environment = { WENSHU_ALLOWED_HOSTS: 'public.example', WENSHU_OPERATIONS_ALLOWED_HOSTS: 'app.test,192.0.2.10' };
  const get = (url, host, forwarded = '') => {
    const response = { statusCode: 200, headers: {}, body: '',
      setHeader(name, value) { this.headers[name.toLowerCase()] = value; },
      end(body) { this.body = body; } };
    enforceOperationsPrivateHost({ url, headers: { host, 'x-forwarded-host': forwarded } }, response,
      () => response.end('main-or-private'), environment);
    return { status: response.statusCode, body: response.body, cacheControl: response.headers['cache-control'] };
  };
  for (const path of ['/operations.html', '/%6fperations.html', '/api/%6fperations/writing/projects', '/api//operations/writing/projects']) {
    const result = get(path, 'public.example', 'app.test:4173');
    assert.equal(result.status, 403, path); assert.equal(result.cacheControl, 'no-store');
  }
  assert.equal(get('/operations.html', 'app.test:4173').status, 200);
  assert.equal(get('/api/operations/writing/projects', '192.0.2.10:4173').status, 200);
  assert.equal(get('/', 'public.example').status, 200);
  assert.equal(get('/api/writing/projects', 'public.example').status, 200);
});
