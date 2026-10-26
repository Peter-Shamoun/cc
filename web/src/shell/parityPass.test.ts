import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

// 10:48 directive parity pass 1: calmer details where ChatGPT/Claude are calmer than we were.
const src = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8');
describe('parity pass 1 (side by side with ChatGPT/Claude)', () => {
  it('search hits are bold, not a yellow highlighter', () => {
    const s = src('./SearchDialog.tsx');
    expect(s).not.toMatch(/ffd84d|bg-yellow/);
    expect(s).toMatch(/<mark className="bg-transparent font-semibold text-fg"/);
  });
  it('dialog section labels are sentence case, not tracked uppercase', () => {
    for (const f of ['./SettingsDialog.tsx', './SearchDialog.tsx']) expect(src(f)).not.toMatch(/uppercase tracking/);
  });
  it('"Jump to latest" floats clear of the composer with a light shadow', () => {
    const s = src('./AppShell.tsx');
    const btn = s.slice(s.indexOf('data-testid="jump-latest"'), s.indexOf('<ArrowDown'));
    expect(btn).toMatch(/-top-12/);
    expect(btn).not.toMatch(/-top-\[18px\]|shadow-(md|lg|xl)/);
  });
  it('an empty history is quiet (no "No chats yet" line)', () => {
    expect(src('../components/Sidebar.tsx')).not.toMatch(/>No chats yet</);
  });
});
