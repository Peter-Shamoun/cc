import { execSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs, waitForTurn } from '../scripts/memguard.mjs';
const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
/**
 * Box-wide memory guard first: every team shares one box, and parallel Playwright + build runs OOM-killed it.
 * Wait for one of the heavy-job slots and >= 3 GB available (scripts/memguard.mjs), hold it for the whole run,
 * release it in the returned teardown. Skipped when an outer `memguard.mjs` already holds a slot (CC_GUARD_HELD)
 * or with CC_GUARD_OFF=1.
 * Then build the two app variants once (sequentially, to keep memory low). E2E_SKIP_BUILD=1 reuses e2e/.dist.
 */
export default async function globalSetup() {
  let release: (() => void) | undefined;
  if (process.env.CC_GUARD_OFF !== '1' && process.env.CC_GUARD_HELD !== '1') {
    const o = parseArgs(['--label=' + (process.env.CC_GUARD_LABEL ?? path.basename(path.resolve(web, '..'))), '--', 'playwright', ...process.argv.slice(2)]);
    release = (await waitForTurn(o)).release;
    process.env.CC_GUARD_HELD = '1'; // builds below and child processes don't take a second slot
  }
  try {
    if (!process.env.E2E_SKIP_BUILD) {
      for (const [name, env] of [['live', { VITE_ANALYST_BASE: '/', VITE_ANALYST_DEMO_TOKEN: 'demo:acme_labs:qa' }], ['mock', { VITE_ANALYST_BASE: '' }]] as const) {
        execSync(`npx vite build --outDir e2e/.dist/${name} --emptyOutDir --logLevel warn`, { cwd: web, stdio: 'inherit', env: { ...process.env, ...env } });
      }
    }
  } catch (e) {
    release?.();
    throw e;
  }
  return release;
}
