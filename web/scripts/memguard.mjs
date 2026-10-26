#!/usr/bin/env node
/**
 * Box-wide guard for heavy test runs (Playwright, vite builds, perf audits). Every team shares one box; parallel
 * Chromium + build runs exhausted its memory (OOM kills mid-run on 25 Oct). Wrap heavy commands:
 *
 *   node scripts/memguard.mjs [--min-gb=3] [--slots=2] [--timeout=1800] [--label=platform] -- npx playwright test ...
 *   npm run guarded -- npx playwright test ...          (same, defaults)
 *
 * 1. Slot: at most `slots` guarded jobs run at once, box-wide (lock files in $CC_GUARD_DIR, default /tmp/cc_heavy).
 *    A slot whose owner pid is gone (killed, OOM) is reclaimed, so a crash never wedges the box.
 * 2. Memory: waits until MemAvailable >= min-gb (the RESOURCE RULES threshold), re-checking every 5 s.
 * 3. Runs the command, forwards SIGINT/SIGTERM, frees the slot on exit, exits with the command's code.
 * Gives up after --timeout seconds with exit code 75 (EX_TEMPFAIL) and says who holds the slots.
 * Env overrides: CC_GUARD_MIN_GB, CC_GUARD_SLOTS, CC_GUARD_TIMEOUT, CC_GUARD_DIR, CC_GUARD_LABEL. CC_GUARD_OFF=1 bypasses.
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/** MemAvailable from /proc/meminfo text, in GiB (null if absent). */
export function availableGiB(meminfo) {
  const m = /^MemAvailable:\s+(\d+)\s*kB/m.exec(meminfo);
  return m ? Number(m[1]) / 1048576 : null;
}

export function readAvailableGiB(read = () => fs.readFileSync('/proc/meminfo', 'utf8')) {
  try { return availableGiB(read()); } catch { return null; }
}

export function pidAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try { process.kill(pid, 0); return true; } catch (e) { return e?.code === 'EPERM'; }
}

/**
 * Try to take one of `slots` lock files in `dir`. Returns { slot, file, release } or { holders } when all are busy.
 * Creation is O_EXCL, so two jobs never get the same slot; a stale file (dead pid) is removed and retried.
 */
export function tryAcquire({ dir, slots, info, isAlive = pidAlive, fsx = fs }) {
  fsx.mkdirSync(dir, { recursive: true, mode: 0o777 });
  const holders = [];
  for (let i = 0; i < slots; i++) {
    const file = path.join(dir, `slot-${i}.json`);
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        fsx.writeFileSync(file, JSON.stringify(info), { flag: 'wx' });
        return { slot: i, file, release: () => { try { const cur = JSON.parse(fsx.readFileSync(file, 'utf8')); if (cur.pid === info.pid && cur.at === info.at) fsx.unlinkSync(file); } catch { /* already gone */ } } };
      } catch (e) {
        if (e?.code !== 'EEXIST') throw e;
        let cur = null;
        try { cur = JSON.parse(fsx.readFileSync(file, 'utf8')); } catch { /* half-written or vanished: treat as stale */ }
        if (cur && isAlive(cur.pid)) { holders.push(cur); break; }
        try { fsx.unlinkSync(file); } catch { /* raced with another reclaimer */ }
      }
    }
  }
  return { holders };
}

export function parseArgs(argv, env = process.env) {
  const sep = argv.indexOf('--');
  const flags = sep >= 0 ? argv.slice(0, sep) : [];
  const cmd = sep >= 0 ? argv.slice(sep + 1) : argv.filter((a) => !a.startsWith('--'));
  const get = (k, d) => { const f = flags.find((a) => a.startsWith(`--${k}=`)); return f ? f.slice(k.length + 3) : d; };
  return {
    minGiB: Number(get('min-gb', env.CC_GUARD_MIN_GB ?? 3)),
    slots: Math.max(1, Number(get('slots', env.CC_GUARD_SLOTS ?? 2))),
    timeoutS: Number(get('timeout', env.CC_GUARD_TIMEOUT ?? 1800)),
    label: get('label', env.CC_GUARD_LABEL ?? path.basename(path.resolve(process.cwd(), '..'))),
    dir: env.CC_GUARD_DIR ?? '/tmp/cc_heavy',
    cmd,
  };
}

/** Wait for a slot and for memory; resolves to the acquired slot or throws after the timeout. */
export async function waitForTurn(o, { now = Date.now, sleep = (ms) => new Promise((r) => setTimeout(r, ms)), avail = readAvailableGiB, log = (s) => process.stderr.write(s + '\n'), acquire = tryAcquire } = {}) {
  const t0 = now();
  let lastMsg = '';
  for (;;) {
    const a = avail();
    if (a === null || a >= o.minGiB) {
      const got = acquire({ dir: o.dir, slots: o.slots, info: { pid: process.pid, label: o.label, cmd: o.cmd.join(' ').slice(0, 160), at: new Date(now()).toISOString() } });
      if (got.release) {
        // Re-check after taking the slot: another job may have started between the check and the lock.
        const b = avail();
        if (b === null || b >= o.minGiB) return got;
        got.release();
      } else {
        const msg = `[memguard] waiting: ${o.slots} heavy job slot(s) busy: ${got.holders.map((h) => `${h.label} pid ${h.pid}`).join(', ')}`;
        if (msg !== lastMsg) log((lastMsg = msg));
      }
    } else {
      const msg = `[memguard] waiting: ${a.toFixed(1)} GB available < ${o.minGiB} GB`;
      if (msg !== lastMsg) log((lastMsg = msg));
    }
    if (now() - t0 > o.timeoutS * 1000) throw Object.assign(new Error(`[memguard] gave up after ${o.timeoutS}s: ${lastMsg}`), { code: 75 });
    await sleep(5000);
  }
}

async function main() {
  const o = parseArgs(process.argv.slice(2));
  if (!o.cmd.length) { process.stderr.write('usage: memguard.mjs [--min-gb=3] [--slots=2] [--timeout=1800] [--label=x] -- <command...>\n'); process.exit(64); }
  let got = null;
  if (process.env.CC_GUARD_OFF !== '1' && process.env.CC_GUARD_HELD !== '1') {
    try { got = await waitForTurn(o); } catch (e) { process.stderr.write(e.message + '\n'); process.exit(e.code ?? 75); }
  }
  // Nested guards (Playwright's global setup, a script that calls memguard again) must not take a second slot.
  const child = spawn(o.cmd[0], o.cmd.slice(1), { stdio: 'inherit', shell: false, env: { ...process.env, CC_GUARD_HELD: got ? '1' : (process.env.CC_GUARD_HELD ?? '') } });
  const fwd = (sig) => () => child.kill(sig);
  process.on('SIGINT', fwd('SIGINT'));
  process.on('SIGTERM', fwd('SIGTERM'));
  child.on('exit', (code, signal) => { got?.release?.(); process.exit(code ?? (signal ? 128 + 15 : 1)); });
  child.on('error', (e) => { got?.release?.(); process.stderr.write(`[memguard] ${e.message}\n`); process.exit(127); });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
