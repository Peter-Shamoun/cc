// Serve the built web app to the browser via Playwright routing, so e2e/review runs need no web server
// (BRIEF resource rules: one server per team; the only server is the demo API).
//   LIVE = http://127.0.0.1:<api>/   static from .dist/live, /api/* goes to the real demo API (same origin, real SSE)
//   MOCK = http://localhost:<api>/   static from .dist/mock (offline MockAnalystAdapter; never hits the network)
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
export const API_PORT = Number(process.env.E2E_API_PORT ?? 8140);
export const LIVE = `http://127.0.0.1:${API_PORT}/`;
export const MOCK = `http://localhost:${API_PORT}/`;
export const DIST = path.join(here, '.dist');
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ico': 'image/x-icon', '.wasm': 'application/wasm', '.txt': 'text/plain', '.map': 'application/json' };

function serve(dir) {
  return async (route) => {
    const u = new URL(route.request().url());
    let f = path.join(dir, decodeURIComponent(u.pathname));
    if (!f.startsWith(dir) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) f = path.join(dir, 'index.html');
    await route.fulfill({ status: 200, body: fs.readFileSync(f), headers: { 'content-type': TYPES[path.extname(f)] ?? 'application/octet-stream', 'cache-control': 'no-store' } });
  };
}

export async function distRoutes(context) {
  const live = new URL(LIVE), mock = new URL(MOCK);
  await context.route((u) => u.host === live.host && !u.pathname.startsWith('/api/'), serve(path.join(DIST, 'live')));
  await context.route((u) => u.host === mock.host, serve(path.join(DIST, 'mock')));
}
