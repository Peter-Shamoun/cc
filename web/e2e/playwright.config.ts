import { defineConfig, devices } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * CC e2e (QA team). Runs against the demo backend only: FastAPI in demo mode
 * (ScriptedLLM, synthetic DuckDB, no paid APIs) plus two Vite servers:
 *   live = VITE_ANALYST_BASE=/ (proxied to the API), mock = offline fixture adapter.
 * Ports default to the QA range (8140 api, 5240 live, 5241 mock); override with E2E_* env vars.
 */
const API_PORT = Number(process.env.E2E_API_PORT ?? 8140);
const LIVE_PORT = Number(process.env.E2E_LIVE_PORT ?? 5240);
const MOCK_PORT = Number(process.env.E2E_MOCK_PORT ?? 5241);
const repoRoot = path.resolve(__dirname, '..', '..');
const py =
  process.env.CC_PYTHON ??
  ['/workspace/cc_fe/venvA/bin/python', path.join(repoRoot, '.venv/bin/python')].find((p) => fs.existsSync(p)) ??
  'python3';
const LIVE = `http://127.0.0.1:${LIVE_PORT}/`;
const MOCK = `http://127.0.0.1:${MOCK_PORT}/`;
const chrome = process.env.CHROME_PATH ?? (fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined);


export default defineConfig({
  testDir: '.',
  outputDir: '../test-results/e2e',
  snapshotPathTemplate: '{testDir}/__screenshots__/{testFilePath}/{arg}-{projectName}{ext}',
  timeout: 60_000,
  expect: { timeout: 20_000, toHaveScreenshot: { maxDiffPixelRatio: 0.02, animations: 'disabled', caret: 'hide' } },
  fullyParallel: true,
  workers: process.env.CI ? 2 : 4,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['json', { outputFile: '../test-results/e2e/results.json' }]],
  use: {
    baseURL: LIVE,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: { executablePath: chrome },
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 }, channel: undefined }, grepInvert: /@mobile|@perf/ },
    { name: 'mobile', use: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true }, grep: /@mobile|@both/ },
    { name: 'perf', use: { viewport: { width: 1440, height: 900 } }, grep: /@perf/ },
  ],
  webServer: [
    {
      command: `${py} -m uvicorn api.app:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: repoRoot,
      env: { CC_API_MODE: 'demo', CC_API_LLM: 'scripted' },
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    {
      command: `npx vite --port ${LIVE_PORT} --strictPort --host 127.0.0.1`,
      cwd: path.resolve(__dirname, '..'),
      env: { VITE_ANALYST_BASE: '/', VITE_ANALYST_DEMO_TOKEN: 'demo:acme_labs:qa', CC_API_URL: `http://127.0.0.1:${API_PORT}` },
      url: LIVE,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    {
      command: `npx vite --port ${MOCK_PORT} --strictPort --host 127.0.0.1`,
      cwd: path.resolve(__dirname, '..'),
      env: { VITE_ANALYST_BASE: '' },
      url: MOCK,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
});
