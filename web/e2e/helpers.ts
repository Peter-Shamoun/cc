import { test } from '@playwright/test';
import { expect, type Page, type BrowserContext } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const __dirname = path.dirname(fileURLToPath(import.meta.url));

import { LIVE as L, MOCK as M } from './static.mjs';
export const LIVE: string = L;
export const MOCK: string = M;
export const CONTRACT_DIR = path.resolve(__dirname, '..', '..', 'api', 'contract');

export type Theme = 'light' | 'dark';

/** Pin theme/palette before the app boots (prefs live under cc.pref.*; no PHI). */
export async function prefs(ctx: BrowserContext, theme: Theme = 'light', palette = 'neutral') {
  await ctx.addInitScript(([t, p]) => {
    localStorage.setItem('cc.pref.theme', t);
    localStorage.setItem('cc.pref.palette', p);
  }, [theme, palette]);
}

export async function open(page: Page, url: string, theme: Theme = 'light', palette = 'neutral') {
  await prefs(page.context(), theme, palette);
  await page.goto(url);
  await expect(composer(page)).toBeVisible();
}

export const composer = (page: Page) => page.getByRole('textbox', { name: 'Message' });
export const sendBtn = (page: Page) => page.getByRole('button', { name: 'Send message' });
export const stopBtn = (page: Page) => page.getByRole('button', { name: /^stop/i });
export const lastAssistant = (page: Page) => page.getByTestId('msg-assistant').last();

/** Type a prompt, press Enter, and (by default) wait for the turn to finish. */
export async function ask(page: Page, prompt: string, { wait = true } = {}) {
  await composer(page).fill(prompt);
  await composer(page).press('Enter');
  if (wait) await waitIdle(page);
}

export async function waitIdle(page: Page, timeout = 45_000) {
  // While streaming the send button turns into a stop button; it comes back when the turn ends.
  await expect(stopBtn(page)).toHaveCount(0, { timeout });
  await expect(sendBtn(page)).toBeVisible({ timeout });
}

/** Events of the response stream contract, one object per SSE `data:` line. */
export type Ev = Record<string, unknown> & { type: string };
export const sse = (events: Ev[]) => events.map((e, i) => `id: ${i}\ndata: ${JSON.stringify(e)}\n\n`).join('');

/** Serve a canned SSE stream for the next /api/chat calls on the live app. */
export async function injectStream(page: Page, events: Ev[] | ((n: number) => Ev[])) {
  let n = 0;
  await page.route('**/api/chat', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    const evs = typeof events === 'function' ? events(n++) : events;
    await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-store' }, body: sse(evs) });
  });
}

/** Wrap parts in message.start ... message.end. */
export function turn(parts: Ev[], extra: { id?: string; end?: Record<string, unknown>; start?: Record<string, unknown> } = {}): Ev[] {
  const id = extra.id ?? `m_${Math.random().toString(36).slice(2, 10)}`;
  return [
    { type: 'message.start', messageId: id, conversationId: 'c_e2e', ...extra.start },
    ...parts,
    { type: 'message.end', messageId: id, finishReason: 'stop', usage: { queries: 1, elapsedMs: 1234 }, ...extra.end },
  ];
}
export const textPart = (id: string, text: string): Ev[] => [
  { type: 'text.start', partId: id },
  { type: 'text.delta', partId: id, delta: text },
  { type: 'text.end', partId: id },
];

/**
 * Contract-outcome examples. The Contract team publishes canonical streams at
 * api/contract/examples/<outcome>.jsonl (one event per line). Tests for an outcome run only once
 * its example exists, so the suite stays green while the contract is still landing; the QA
 * scorecard counts skipped outcomes as gaps.
 */
export function contractExample(outcome: string): Ev[] | null {
  for (const ext of ['.jsonl', '.ndjson', '.json']) {
    const f = path.join(CONTRACT_DIR, 'examples', outcome + ext);
    if (!fs.existsSync(f)) continue;
    const raw = fs.readFileSync(f, 'utf8').trim();
    if (ext === '.json') {
      const j = JSON.parse(raw);
      return Array.isArray(j) ? j : j.events;
    }
    return raw.split('\n').filter((l) => l.trim() && !l.trim().startsWith('//')).map((l) => JSON.parse(l));
  }
  return null;
}

/** Deterministic visuals: no motion, no caret, hide the live-timer style counters. */
export async function freeze(page: Page) {
  // The app ships a strict CSP (no inline <style>), so page.addStyleTag is refused. A constructed
  // stylesheet (CSSOM) is not subject to style-src, and reduced motion covers anything it misses.
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.evaluate(() => {
    const sheet = new CSSStyleSheet();
    sheet.replaceSync('*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}');
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
  });
}

/**
 * Known gap ratchet: the test is expected to fail until the owning team fixes it. When it starts
 * passing Playwright reports "expected to fail", which means: delete the gap() line (and close the
 * backlog item named in `id`).
 */
export function gap(id: string) {
  test.info().annotations.push({ type: 'gap', description: id });
  test.fail(true, id);
}
