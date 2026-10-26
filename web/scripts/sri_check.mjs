// Local gate: verify SRI in a built dist (default dist and dist-mock). Exits 1 on any problem.
import fs from 'node:fs';
import path from 'node:path';
import { checkSri } from './sri.mjs';

let bad = 0;
for (const dir of process.argv.slice(2).length ? process.argv.slice(2) : ['dist', 'dist-mock']) {
  const html = fs.readFileSync(path.join(dir, 'index.html'), 'utf8');
  const read = (p) => { const f = path.join(dir, p); return fs.existsSync(f) ? fs.readFileSync(f) : undefined; };
  const js = fs.readdirSync(path.join(dir, 'assets')).filter((f) => f.endsWith('.js')).map((f) => `/assets/${f}`);
  const problems = checkSri(html, read, js);
  console.log(`${dir}: ${problems.length ? `${problems.length} problem(s)` : `ok (${js.length} JS files in import map)`}`);
  for (const p of problems) console.log(`  - ${p}`);
  bad += problems.length;
}
process.exit(bad ? 1 : 0);
