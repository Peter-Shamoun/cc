import { defineConfig, devices } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * ClaimClarity e2e (QA team). Runs against the demo backend only: FastAPI in demo mode
 * (ScriptedLLM, synthetic DuckDB, no paid APIs) served from static builds via request routing (no web server).
 * Ports default to the QA range (8140 api, 5240 live, 5241 mock); override with E2E_* env vars.
 */
const API_PORT = Number(process.env.E2E_API_PORT ?? 8140);
const repoRoot = path.resolve(__dirname, '..', '..');
const py =
  process.env.CC_PYTHON ??
  ['/workspace/cc_fe/venvA/bin/python', path.join(repoRoot, '.venv/bin/python')].find((p) => fs.existsSync(p)) ??
  'python3';
const LIVE = `http://127.0.0.1:${API_PORT}/`;
const chrome = process.env.CHROME_PATH ?? (fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined);


export default defineConfig({
  testDir: '.',
  outputDir: '../test-results/e2e',
  snapshotPathTemplate: '{testDir}/__screenshots__/{testFilePath}/{arg}-{projectName}{ext}',
  timeout: 90_000,
  expect: { timeout: 30_000, toHaveScreenshot: { maxDiffPixelRatio: 0.02, animations: 'disabled', caret: 'hide' } },
  fullyParallel: true,
    // BRIEF resource rules: Playwright runs with one worker on the shared box.
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['json', { outputFile: '../test-results/e2e/results.json' }]],
  use: {
    baseURL: LIVE,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: { executablePath: chrome },
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 }, channel: undefined }, grepInvert: /@mobile|@perf|@chaos/ },
    { name: 'mobile', use: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true }, grep: /@mobile|@both/ },
    { name: 'chaos', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 }, channel: undefined }, grep: /@chaos/ },
    { name: 'perf', use: { viewport: { width: 1440, height: 900 } }, grep: /@perf/ },
    // Cross-browser (QA): core flows only, never pixel baselines. Opt-in: `npm run e2e:xb` (one worker, run sequentially).
    { name: 'firefox', use: { ...devices['Desktop Firefox'], viewport: { width: 1440, height: 900 }, launchOptions: {} }, grepInvert: /@mobile|@perf|@chaos/, testIgnore: /visual/ },
    { name: 'webkit', use: { ...devices['Desktop Safari'], viewport: { width: 1440, height: 900 }, launchOptions: {} }, grepInvert: /@mobile|@perf|@chaos/, testIgnore: /visual/ },
    { name: 'mobile-webkit', use: { ...devices['iPhone 13'], viewport: { width: 390, height: 844 }, launchOptions: {} }, grep: /@mobile|@both/, testIgnore: /visual/ },
  ],
  // Only ONE server (BRIEF resource rules): the demo API. The built app is served to the browser by
  // Playwright routing (e2e/static.mjs); global-setup builds e2e/.dist/{live,mock}.
  globalSetup: './global-setup.ts',
  webServer: {
    command: `${py} -m uvicorn api.app:app --no-server-header --host 127.0.0.1 --port ${API_PORT}`,
    cwd: repoRoot,
    env: { CC_API_MODE: 'demo', CC_API_LLM: 'scripted' },
    url: `http://127.0.0.1:${API_PORT}/api/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
