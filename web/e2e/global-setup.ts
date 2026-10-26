import { execSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
/** Build the two app variants once (sequentially, to keep memory low). E2E_SKIP_BUILD=1 reuses e2e/.dist. */
export default function globalSetup() {
  if (process.env.E2E_SKIP_BUILD) return;
  for (const [name, env] of [['live', { VITE_ANALYST_BASE: '/', VITE_ANALYST_DEMO_TOKEN: 'demo:acme_labs:qa' }], ['mock', { VITE_ANALYST_BASE: '' }]] as const) {
    execSync(`npx vite build --outDir e2e/.dist/${name} --emptyOutDir --logLevel warn`, { cwd: web, stdio: 'inherit', env: { ...process.env, ...env } });
  }
}
