import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { isIP } from "node:net";
import { privateOperationsPlugin } from "./operations-access.mjs";

function exactHost(value, name) {
  const host = value.toLowerCase();
  const valid = host.length <= 253 && host.split(".").every((part) =>
    /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(part));
  if (!valid || (/^[0-9.]+$/.test(host) && isIP(host) !== 4)) {
    throw new Error(`${name} must contain explicit IPv4 addresses or hostnames without ports, URLs or wildcards`);
  }
  return host;
}

function loopbackApiTarget(value) {
  const match = /^http:\/\/127\.0\.0\.1:([0-9]{1,5})$/.exec(value);
  if (!match || Number(match[1]) < 1 || Number(match[1]) > 65535) {
    throw new Error('WENSHU_API_PROXY_TARGET must be http://127.0.0.1 with an explicit valid port');
  }
  return value;
}

const frontendHost = exactHost(process.env.WENSHU_FRONTEND_HOST?.trim() || "127.0.0.1", "WENSHU_FRONTEND_HOST");
const hostSetting = process.env.WENSHU_ALLOWED_HOSTS?.trim();
const allowedHosts = [...new Set(["terminal.local", ...(hostSetting
  ? hostSetting.split(",").map((value) => exactHost(value.trim(), "WENSHU_ALLOWED_HOSTS"))
  : [])])];
// The backend still checks the browser Origin; rewriting Host only lets the
// same-origin frontend proxy reach the API's loopback listener.
const apiProxy = { "/api": { target: loopbackApiTarget(process.env.WENSHU_API_PROXY_TARGET?.trim() || "http://127.0.0.1:8787"), changeOrigin: true } };

export default defineConfig({
  build: {
    outDir: "dist/client",
    rollupOptions: { input: { main: "index.html", operations: "operations.html" } },
  },
  optimizeDeps: {
    include: ["react", "react-dom/client"],
  },
  server: {
    host: frontendHost,
    allowedHosts,
    proxy: apiProxy,
    warmup: {
      clientFiles: ["./src/main.jsx"],
    },
  },
  preview: { host: frontendHost, allowedHosts, proxy: apiProxy },
  plugins: [privateOperationsPlugin(), react()],
});
