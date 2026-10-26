/// <reference types="vitest/config" />
import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import fs from 'node:fs';
import path from 'node:path';
import { NONCE_PLACEHOLDER, newNonce, securityHeaders } from './src/security/headers.ts';
import { bootShellHtml } from './scripts/boot_shell.mjs';
import { applySri } from './scripts/sri.mjs';

/** Boot shell (QA-P8): see scripts/boot_shell.mjs. Applied to built and dev HTML alike. */
function bootShellPlugin(root: string): Plugin {
  return {
    name: 'cc-boot-shell',
    transformIndexHtml: { order: 'post', handler: (html) => bootShellHtml(html, fs.readFileSync(path.join(root, 'public/theme-init.js'), 'utf8')) },
  };
}

/**
 * Subresource Integrity (build only): see scripts/sri.mjs. Runs after the files are written, so the hashes cover the
 * exact bytes served; preview and production servers must not rewrite /assets/ files (compression is fine).
 */
function sriPlugin(): Plugin {
  return {
    name: 'cc-sri',
    apply: 'build',
    enforce: 'post',
    writeBundle(opts, bundle) {
      const dir = opts.dir ?? 'dist';
      const index = path.join(dir, 'index.html');
      if (!fs.existsSync(index)) return;
      const read = (p: string) => { const f = path.join(dir, p); return fs.existsSync(f) ? fs.readFileSync(f) : undefined; };
      const js = Object.keys(bundle).filter((f) => f.startsWith('assets/') && f.endsWith('.js')).map((f) => `/${f}`);
      fs.writeFileSync(index, applySri(fs.readFileSync(index, 'utf8'), read, js));
    },
  };
}

const env = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env ?? {};

/**
 * Strict CSP everywhere the app is served. Build output carries NONCE_PLACEHOLDER (via html.cspNonce);
 * preview swaps in a fresh nonce per HTML response, as a production server must (docs/frontend.md).
 * Dev uses one nonce per server start (Vite's dev preamble and injected <style> tags carry it).
 */
function securityHeadersPlugin(devNonce: string): Plugin {
  return {
    name: 'cc-security-headers',
    configureServer(server) {
      server.middlewares.use((_req, res, next) => {
        for (const [k, v] of Object.entries(securityHeaders(devNonce, { dev: true }))) res.setHeader(k, v);
        next();
      });
    },
    configurePreviewServer(server) {
      const dist = path.resolve(server.config.root, server.config.build.outDir);
      server.middlewares.use((req, res, next) => {
        const url = (req.url ?? '/').split('?')[0];
        const isAsset = url.startsWith('/assets/');
        const isHtml = !isAsset && (url === '/' || url.endsWith('.html') || !path.extname(url));
        const nonce = newNonce();
        for (const [k, v] of Object.entries(securityHeaders(nonce, { noStore: !isAsset }))) res.setHeader(k, v);
        if (isAsset) {
          // A missing hashed asset (e.g. a stale chunk after a deploy) must 404, not fall back to index.html: the SPA
          // fallback would serve HTML as JS, un-nonced (placeholder left in), with a cacheable status.
          const file = path.join(dist, decodeURIComponent(url));
          if (!file.startsWith(dist + path.sep) || !fs.existsSync(file)) {
            res.statusCode = 404;
            res.setHeader('Cache-Control', 'no-store');
            res.setHeader('Content-Type', 'text/plain; charset=utf-8');
            res.end(req.method === 'HEAD' ? undefined : 'Not found');
            return;
          }
          res.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
        }
        if (isHtml && (req.method === 'GET' || req.method === 'HEAD') && !url.startsWith('/api/')) {
          const html = fs.readFileSync(path.join(dist, 'index.html'), 'utf8').split(NONCE_PLACEHOLDER).join(nonce);
          res.setHeader('Content-Type', 'text/html; charset=utf-8');
          res.end(req.method === 'HEAD' ? undefined : html);
          return;
        }
        next();
      });
    },
  };
}

export default defineConfig(({ command }) => {
  // A token in the bundle is public. Only the demo API's synthetic `demo:<customer>:<user>` shape may be built in;
  // the real backend uses the cc_session cookie (src/auth/session.ts).
  const demoToken = env.VITE_ANALYST_DEMO_TOKEN;
  if (command === 'build' && demoToken && !/^demo:[a-z0-9_]{2,40}:[A-Za-z0-9_.@+-]{1,80}$/.test(demoToken)) {
    throw new Error('VITE_ANALYST_DEMO_TOKEN is not a demo token; refusing to bake a credential into the bundle');
  }
  const devNonce = newNonce();
  const api = env.CC_API_URL ?? 'http://127.0.0.1:8787';
  return {
    plugins: [react(), tailwindcss(), securityHeadersPlugin(devNonce), bootShellPlugin(path.dirname(new URL(import.meta.url).pathname)), sriPlugin()],
    html: { cspNonce: command === 'serve' && !env.CC_PREVIEW ? devNonce : NONCE_PLACEHOLDER },
    server: { port: 5173, host: '127.0.0.1', proxy: { '/api': { target: api, changeOrigin: true } } },
    preview: { host: '127.0.0.1', proxy: { '/api': { target: api, changeOrigin: true } } },
    build: { chunkSizeWarningLimit: 4000 },
    test: {
      environment: 'jsdom',
      testTimeout: 20000,
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
    },
  };
});
