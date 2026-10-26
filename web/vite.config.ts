/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, host: '127.0.0.1', proxy: { '/api': { target: (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env.CC_API_URL ?? 'http://127.0.0.1:8787', changeOrigin: true } } },
  build: { chunkSizeWarningLimit: 4000 },
  test: {
    environment: 'jsdom',
    testTimeout: 20000,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
