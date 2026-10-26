/**
 * document.title is a PHI sink: browsers store titles in history, sync them and show them in tab strips during screen
 * shares. The shared view must not put the chat's title (which echoes the question) there, and no source file may
 * assign document.title from data.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/react';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

const PHI_TITLE = 'Claims for Jane Q. Synthetic MBR-998877';

vi.mock('../shell/AppShell', async (orig) => {
  const m = await orig<typeof import('../shell/AppShell')>();
  const { HttpAnalystAdapter: H } = await import('../adapters/http');
  return {
    ...m,
    makeAdapter: () => {
      const a = new H('');
      (a as unknown as { loadShared: () => Promise<unknown> }).loadShared = async () => ({
        shareId: 'sh_x', title: PHI_TITLE, includeData: false, createdAt: '2026-10-25T20:00:00Z', messages: [],
      });
      return a;
    },
  };
});

afterEach(() => { cleanup(); document.title = 'CC'; });

describe('document.title never carries chat content', () => {
  it('the shared view keeps a fixed title even when the chat title has a name in it', async () => {
    const { SharedView } = await import('../shell/SharedView');
    const { findByTestId } = render(<SharedView shareId="sh_x" />);
    await findByTestId('shared-view');
    await waitFor(() => expect(document.body.textContent).toContain(PHI_TITLE)); // shown on the page, which is fine
    expect(document.title).toBe('Shared chat · CC');
    expect(document.title).not.toContain('Synthetic');
  });

  it('no source file assigns document.title or names a performance mark from a template or variable', () => {
    const hits: string[] = [];
    const walk = (d: string) => {
      for (const f of readdirSync(d)) {
        const p = join(d, f);
        if (statSync(p).isDirectory()) { walk(p); continue; }
        if (!/\.(ts|tsx)$/.test(f) || /\.test\.tsx?$/.test(f)) continue;
        for (const m of readFileSync(p, 'utf8').matchAll(/document\.title\s*=\s*([^;\n]+)/g)) {
          if (!/^'[^'$]*'$/.test(m[1].trim())) hits.push(`${p}: ${m[0]}`);
        }
        // Performance marks/measures stay in the timeline (and in DevTools/RUM exports): literal names only.
        for (const m of readFileSync(p, 'utf8').matchAll(/performance\.(?:mark|measure)\(\s*([^,)]+)/g)) {
          if (!/^'[^'$]*'$/.test(m[1].trim())) hits.push(`${p}: ${m[0]}`);
        }
      }
    };
    walk(join(__dirname, '..'));
    expect(hits).toEqual([]);
  });
});
