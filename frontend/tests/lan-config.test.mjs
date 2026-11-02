import assert from "node:assert/strict";
import test from "node:test";

let sequence = 0;
async function configured(environment = {}) {
  const keys = ["WENSHU_FRONTEND_HOST", "WENSHU_ALLOWED_HOSTS", "WENSHU_API_PROXY_TARGET", "WENSHU_OPERATIONS_ALLOWED_HOSTS"];
  const previous = Object.fromEntries(keys.map((key) => [key, process.env[key]]));
  try {
    for (const key of keys) {
      if (key in environment) process.env[key] = environment[key];
      else delete process.env[key];
    }
    return (await import(`../vite.config.mjs?test=${++sequence}`)).default;
  } finally {
    for (const key of keys) {
      if (previous[key] === undefined) delete process.env[key];
      else process.env[key] = previous[key];
    }
  }
}

test("default dev and preview stay on loopback with a loopback API proxy", async () => {
  const config = await configured();
  for (const mode of [config.server, config.preview]) {
    assert.equal(mode.host, "127.0.0.1");
    assert.deepEqual(mode.allowedHosts, ["terminal.local"]);
    assert.deepEqual(mode.proxy["/api"], { target: "http://127.0.0.1:8787", changeOrigin: true });
  }
});

test("dev and preview use only explicitly supplied hosts", async () => {
  const config = await configured({ WENSHU_FRONTEND_HOST: "192.0.2.10", WENSHU_ALLOWED_HOSTS: " APP.TEST, 192.0.2.10,app.test " });
  for (const mode of [config.server, config.preview]) {
    assert.equal(mode.host, "192.0.2.10");
    assert.deepEqual(mode.allowedHosts, ["terminal.local", "app.test", "192.0.2.10"]);
  }
});

test("invalid host settings fail configuration loading", async () => {
  for (const key of ["WENSHU_FRONTEND_HOST", "WENSHU_ALLOWED_HOSTS"]) {
    for (const value of ["*", "*.example.com", ".example.com", "http://app.test", "app.test:4173", "app.test,,localhost", "999.1.2.3", "app.test evil", "-app.test"]) {
      await assert.rejects(configured({ [key]: value }), new RegExp(key));
    }
  }
});

test("invalid operations hosts fail configuration without using the public host allowlist", async () => {
  await assert.rejects(configured({ WENSHU_ALLOWED_HOSTS: 'public.example', WENSHU_OPERATIONS_ALLOWED_HOSTS: '*' }), /WENSHU_OPERATIONS_ALLOWED_HOSTS/);
});

test("a separate deployment proxies dev and preview to its own explicit loopback API port", async () => {
  const config = await configured({ WENSHU_API_PROXY_TARGET: " http://127.0.0.1:18888 " });
  for (const mode of [config.server, config.preview]) {
    assert.deepEqual(mode.proxy["/api"], { target: "http://127.0.0.1:18888", changeOrigin: true });
  }
  const unchanged = await configured();
  assert.equal(unchanged.server.proxy["/api"].target, "http://127.0.0.1:8787");
});

test("API targets reject other hosts, protocols, credentials, paths and invalid ports", async () => {
  for (const target of [
    "http://app.test:8787", "http://localhost:8787", "http://192.0.2.10:8787", "http://0.0.0.0:8787",
    "https://127.0.0.1:8787", "http://127.0.0.1", "http://127.0.0.1:0", "http://127.0.0.1:65536",
    "http://127.0.0.1:8787/api", "http://127.0.0.1:8787?other=1", "http://127.0.0.1:8787#other",
    "http://user@127.0.0.1:8787", "http://127.0.0.1.evil:8787", "http://2130706433:8787",
    "http://127.1:8787", "http://[::1]:8787",
  ]) {
    await assert.rejects(configured({ WENSHU_API_PROXY_TARGET: target }), /WENSHU_API_PROXY_TARGET/);
  }
});
