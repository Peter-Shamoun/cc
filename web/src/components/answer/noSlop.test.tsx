import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { render } from '@testing-library/react';
import { AbstainCard, ClarifyCard, EscalationNote } from './States';
import { ErrorView } from '../parts/misc';
import type { ErrorPart } from '../../api/contract';

// Peter 10:06 PM: the answer column reads like Claude/ChatGPT — plain prose, no boxed cards, no slop.
const BANNED: [RegExp, string][] = [
  [/\bSparkles\b/, 'sparkle icon'],
  [/backdrop-blur|backdrop-filter/, 'glassmorphism'],
  [/shadow-(lg|xl|2xl)\b/, 'heavy drop shadow'],
  [/animate-bounce/, 'bouncy animation'],
  [/\b(purple|violet|indigo|fuchsia)-\d/, 'purple/indigo accent'],
  [/bg-clip-text|text-transparent/, 'gradient text'],
  [/[\u2728\u{1F31F}\u{1F680}\u{1F389}]/u, 'emoji in UI chrome'],
];
const dirs = ['src/components/answer', 'src/components/parts'];
const extra = ['src/components/Message.tsx', 'src/components/ArtifactPanel.tsx'];

describe('answer column: no AI-slop patterns', () => {
  for (const d of dirs) {
    for (const f of readdirSync(d).filter((n) => /\.tsx?$/.test(n) && !n.includes('.test.'))) {
      it(`${d}/${f} uses none of the banned patterns`, () => {
        const src = readFileSync(join(d, f), 'utf8');
        for (const [re, what] of BANNED) expect(src.match(re)?.[0], `${f}: ${what}`).toBeUndefined();
        // Gradients only as the table's horizontal scroll-edge fade.
        const grads = src.match(/gradient/g)?.length ?? 0;
        expect(grads, `${f}: gradient`).toBe(f === 'DataTable.tsx' ? 2 : 0);
      });
    }
  }

  for (const f of extra) {
    it(`${f} uses none of the banned patterns`, () => {
      const src = readFileSync(f, 'utf8');
      for (const [re, what] of BANNED) expect(src.match(re)?.[0], `${f}: ${what}`).toBeUndefined();
      // A mask fade on a collapsed long message is a clip, not a decorative gradient.
      expect(src.replace(/\[mask-image:linear-gradient\([^\]]*\]/g, ''), `${f}: gradient`).not.toMatch(/gradient/);
    });
  }

  const unboxed = (el: HTMLElement) => {
    expect(el.className).not.toMatch(/\bborder\b|\bbg-(elev|muted)\b|rounded-(xl|2xl)|shadow/);
    expect(el.getAttribute('style') ?? '').not.toMatch(/background|border/);
  };
  it('abstain, clarify and error render as prose in the column, not boxed cards', () => {
    const { getByTestId } = render(<>
      <AbstainCard part={{ type: 'abstain', id: 'a', category: 'data_unavailable', reason: 'No appeals data.', suggestions: [{ id: 's', text: 'Denial rate by payer' }] } as never} />
      <ClarifyCard part={{ type: 'clarification', id: 'c', clarificationId: 'k', question: 'Count or dollars?', allowFreeText: true, choices: [{ id: 'a', label: 'Count' }] } as never} active />
      <ErrorView part={{ type: 'error', id: 'e', code: 'internal', title: 'Something went wrong', message: 'Try again.', retryable: true } as ErrorPart} onRetry={() => {}} />
    </>);
    for (const id of ['abstain', 'clarify', 'error']) unboxed(getByTestId(id));
    // Choices and rephrasings are quiet hairline buttons, not pills.
    for (const id of ['clarify-chip', 'rephrase-chip']) expect(getByTestId(id).className).not.toMatch(/rounded-full/);
  });
  it('escalation is a muted text line, not a pill', () => {
    const { getByTestId } = render(<EscalationNote ev={{ type: 'escalation', from: { model: 'claude-haiku-4-5' }, to: { model: 'claude-sonnet-4-5' }, trigger: 'repair_exhausted', reason: 'Repairs ran out.' } as never} />);
    const btn = getByTestId('escalation').querySelector('button')!;
    expect(btn.className).not.toMatch(/rounded-full|\bborder\b|bg-elev/);
    expect(btn.querySelector('.lucide-sparkles')).toBeNull();
  });
});
