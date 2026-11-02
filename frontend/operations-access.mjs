import { isIP } from 'node:net';

const DEFAULT_OPERATIONS_HOSTS = ['127.0.0.1', 'localhost'];

function configuredOperationsHosts(environment) {
  const raw = environment.WENSHU_OPERATIONS_ALLOWED_HOSTS?.trim();
  const additions = raw ? raw.split(',').map(value => {
    const host = value.trim().toLowerCase();
    const valid = host.length <= 253 && host.split('.').every(part =>
      /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(part));
    if (!valid || (/^[0-9.]+$/.test(host) && isIP(host) !== 4)) {
      throw new Error('WENSHU_OPERATIONS_ALLOWED_HOSTS requires explicit IPv4 addresses or hostnames without ports, URLs or wildcards');
    }
    return host;
  }) : [];
  return new Set([...DEFAULT_OPERATIONS_HOSTS, ...additions]);
}

export function normalizedOperationsPath(rawUrl = '/') {
  let path;
  try { path = rawUrl.startsWith('/') ? rawUrl.split(/[?#]/, 1)[0] : new URL(rawUrl, 'http://operations.invalid').pathname; } catch { return ''; }
  // Vite decodes URL paths before resolving files. Normalize the same forms
  // before deciding whether an operations entry or API path is private.
  for (let depth = 0; depth < 4; depth += 1) {
    let decoded;
    try { decoded = decodeURIComponent(path); } catch { return ''; }
    if (decoded === path) break;
    path = decoded;
  }
  path = path.replace(/\\/g, '/').replace(/\/{2,}/g, '/');
  try { return new URL(path, 'http://operations.invalid').pathname; } catch { return ''; }
}

export function isOperationsPath(rawUrl) {
  const path = normalizedOperationsPath(rawUrl).toLowerCase();
  return path === '/operations.html' || path.startsWith('/operations.html/') || path === '/api/operations' || path.startsWith('/api/operations/');
}

export function operationsHostAllowed(header, environment = process.env) {
  if (typeof header !== 'string') return false;
  const match = /^([^:\s/?#]+)(?::([0-9]{1,5}))?$/.exec(header);
  try {
    const hosts = configuredOperationsHosts(environment);
    return Boolean(match && hosts.has(match[1].toLowerCase()) && (!match[2] || (Number(match[2]) > 0 && Number(match[2]) <= 65535)));
  } catch { return false; }
}

export function enforceOperationsPrivateHost(request, response, next, environment = process.env) {
  if (!isOperationsPath(request.url)) return next();
  response.setHeader('Cache-Control', 'no-store');
  if (operationsHostAllowed(request.headers.host, environment)) return next();
  response.statusCode = 403;
  response.setHeader('Content-Type', 'application/json; charset=utf-8');
  response.end(JSON.stringify({ detail: '写作运维仅限授权内网入口访问。' }));
}

export function privateOperationsPlugin(environment = process.env) {
  configuredOperationsHosts(environment);
  const guard = (request, response, next) => enforceOperationsPrivateHost(request, response, next, environment);
  return {
    name: 'private-writing-operations',
    configureServer(server) { server.middlewares.use(guard); },
    configurePreviewServer(server) { server.middlewares.use(guard); },
  };
}
