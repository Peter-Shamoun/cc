import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { afterEach, describe, expect, it } from 'vitest';
import { availableGiB, parseArgs, tryAcquire, waitForTurn, type GuardOpts } from '../../scripts/memguard.mjs';

const dirs: string[] = [];
const tmp = () => { const d = fs.mkdtempSync(path.join(os.tmpdir(), 'memguard-')); dirs.push(d); return d; };
afterEach(() => { for (const d of dirs.splice(0)) fs.rmSync(d, { recursive: true, force: true }); });
const info = (pid: number) => ({ pid, label: 'platform', cmd: 'playwright test', at: new Date().toISOString() });

describe('memguard: box-wide guard for heavy test runs', () => {
  it('reads MemAvailable in GiB', () => {
    expect(availableGiB('MemTotal:       16000000 kB\nMemFree:  100 kB\nMemAvailable:    3145728 kB\n')).toBe(3);
    expect(availableGiB('MemTotal: 1 kB\n')).toBeNull();
  });

  it('hands out at most N slots box-wide; a released slot is reusable', () => {
    const dir = tmp(); const alive = () => true;
    const a = tryAcquire({ dir, slots: 2, info: info(101), isAlive: alive });
    const b = tryAcquire({ dir, slots: 2, info: info(102), isAlive: alive });
    const c = tryAcquire({ dir, slots: 2, info: info(103), isAlive: alive });
    expect('slot' in a && 'slot' in b && [a.slot, b.slot]).toEqual([0, 1]);
    expect(c.release).toBeUndefined();
    expect('holders' in c && c.holders.map((h) => h.pid)).toEqual([101, 102]);
    a.release!();
    const d = tryAcquire({ dir, slots: 2, info: info(104), isAlive: alive });
    expect('slot' in d && d.slot).toBe(0);
  });

  it('reclaims a slot whose owner died (OOM-killed run), and a half-written lock file', () => {
    const dir = tmp();
    fs.writeFileSync(path.join(dir, 'slot-0.json'), JSON.stringify(info(999_999)));
    const got = tryAcquire({ dir, slots: 1, info: info(5), isAlive: (pid) => pid !== 999_999 });
    expect('slot' in got && got.slot).toBe(0);
    got.release!();
    fs.writeFileSync(path.join(dir, 'slot-0.json'), '{"pid":');
    expect(tryAcquire({ dir, slots: 1, info: info(6), isAlive: () => true }).release).toBeTypeOf('function');
  });

  it('release only removes its own lock (a reclaimed slot now owned by someone else stays)', () => {
    const dir = tmp();
    const mine = tryAcquire({ dir, slots: 1, info: info(7), isAlive: () => true });
    fs.writeFileSync(path.join(dir, 'slot-0.json'), JSON.stringify(info(8)));
    mine.release!();
    expect(JSON.parse(fs.readFileSync(path.join(dir, 'slot-0.json'), 'utf8')).pid).toBe(8);
  });

  it('waits while memory is under the threshold, then takes a slot; gives up with code 75 after the timeout', async () => {
    const dir = tmp(); const logs: string[] = [];
    let t = 0; const mem = [2.1, 2.5, 3.4, 3.4];
    const o: GuardOpts = { minGiB: 3, slots: 1, timeoutS: 60, label: 'platform', dir, cmd: ['x'] };
    const got = await waitForTurn(o, { now: () => t, sleep: async (ms) => { t += ms; }, avail: () => mem.shift() ?? 3.4, log: (s) => logs.push(s) });
    expect(got.slot).toBe(0);
    expect(t).toBe(10_000);
    expect(logs).toEqual(['[memguard] waiting: 2.1 GB available < 3 GB', '[memguard] waiting: 2.5 GB available < 3 GB']);
    t = 0;
    await expect(waitForTurn({ ...o, timeoutS: 12 }, { now: () => t, sleep: async (ms) => { t += ms; }, avail: () => 1, log: () => {} })).rejects.toMatchObject({ code: 75 });
    got.release();
  });

  it('gives the slot back if memory dropped between the check and the lock', async () => {
    const dir = tmp(); const mem = [4, 1, 4, 4]; let t = 0;
    const got = await waitForTurn({ minGiB: 3, slots: 1, timeoutS: 60, label: 'p', dir, cmd: ['x'] }, { now: () => t, sleep: async (ms) => { t += ms; }, avail: () => mem.shift() ?? 4, log: () => {} });
    expect(t).toBe(5000);
    expect(fs.readdirSync(dir)).toEqual(['slot-0.json']);
    got.release();
    expect(fs.readdirSync(dir)).toEqual([]);
  });

  it('parses flags and env defaults', () => {
    expect(parseArgs(['--min-gb=4', '--slots=1', '--', 'npx', 'playwright', 'test'], {})).toMatchObject({ minGiB: 4, slots: 1, cmd: ['npx', 'playwright', 'test'] });
    expect(parseArgs(['--', 'x'], { CC_GUARD_MIN_GB: '5', CC_GUARD_DIR: '/tmp/z' })).toMatchObject({ minGiB: 5, slots: 2, timeoutS: 1800, dir: '/tmp/z' });
  });

  it('CLI: runs the command in a slot, passes its exit code through, frees the slot, marks children as guarded', () => {
    const dir = tmp();
    const script = path.resolve(__dirname, '../../scripts/memguard.mjs');
    const r = spawnSync(process.execPath, [script, '--min-gb=0', '--', process.execPath, '-e', 'console.log(process.env.CC_GUARD_HELD + " " + require("fs").readdirSync(process.argv[1]).join(","));process.exit(3)', dir], { env: { ...process.env, CC_GUARD_DIR: dir, CC_GUARD_HELD: '' }, encoding: 'utf8' });
    expect(r.status).toBe(3);
    expect(r.stdout.trim()).toBe('1 slot-0.json');
    expect(fs.readdirSync(dir)).toEqual([]);
  });
});
