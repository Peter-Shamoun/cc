/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { securityHeaders } from './src/lib/securityHeaders';

type Env = Record<string, string | undefined>;
const env: Env = (globalThis as { process?: { env: Env } }).process?.env ?? {};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    host: '127.0.0.1',
    headers: securityHeaders({ dev: true }),
    proxy: { '/api': { target: env.CC_API_URL ?? 'http://127.0.0.1:8787', changeOrigin: true } },
  },
  preview: {
    host: '127.0.0.1',
    headers: securityHeaders(),
    proxy: { '/api': { target: env.CC_API_URL ?? 'http://127.0.0.1:8787', changeOrigin: true } },
  },
  build: { chunkSizeWarningLimit: 4000 },
  test: {
    environment: 'jsdom',
    testTimeout: 20000,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
