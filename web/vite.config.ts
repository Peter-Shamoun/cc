/// <reference types="vitest/config" />
import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import fs from 'node:fs';
import path from 'node:path';
import { NONCE_PLACEHOLDER, newNonce, securityHeaders } from './src/security/headers.ts';

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
        if (isAsset) res.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
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
  const devNonce = newNonce();
  const api = env.CC_API_URL ?? 'http://127.0.0.1:8787';
  return {
    plugins: [react(), tailwindcss(), securityHeadersPlugin(devNonce)],
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
