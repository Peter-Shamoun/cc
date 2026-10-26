/**
 * Contract 1.16.0 edge-case demo scenarios (EDGE_SCENARIOS) rendered through the real answer surface.
 * Each recorded stream must read cleanly, the way Claude/ChatGPT would show it: quiet stop notes, no red "failed"
 * for a user's own Stop, no chart for an empty result, no slop classes. Synthetic data only.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { AssistantTurn } from '../Message';
import { newAssistantViewV1, reduceV1, type AssistantViewV1, type StreamEvent } from '../../api/contract';
import { EDGE_SCENARIOS } from '../../contract/edgeCases';

afterEach(cleanup);
const EDGE = resolve(__dirname, '../../../../api/contract/examples/edge');
const noop = () => {};
const view = (name: string) => readFileSync(resolve(EDGE, `${name}.jsonl`), 'utf8').split('\n').filter(Boolean)
  .map((l) => JSON.parse(l) as StreamEvent).reduce(reduceV1, newAssistantViewV1('m') as AssistantViewV1);
const show = (name: string) => render(<AssistantTurn msg={view(name)} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} onSend={noop} />);
const SLOP = /bg-gradient|backdrop-blur|shadow-(lg|xl|2xl)|animate-(bounce|ping|pulse)|drop-shadow|text-transparent/;

describe('1.16.0 edge scenarios on the answer surface', () => {
  it.each([...EDGE_SCENARIOS.map((s) => s.scenario as string), 'clarify_nested_2', 'clarify_nested_3'])('%s renders without slop, raw codes or stray running state', (name) => {
    const { container } = show(name);
    expect(container.querySelector('[data-testid="msg-assistant"]')).toBeTruthy();
    // Edge-scroll fades (bg → transparent, functional) and the chart's load skeleton are the only allowed exceptions.
    const probe = container.cloneNode(true) as HTMLElement;
    probe.querySelectorAll('[data-testid^="table-fade"], [data-testid="chart-skeleton"]').forEach((e) => e.remove());
    expect(probe.innerHTML).not.toMatch(SLOP);
    expect(container.textContent).not.toMatch(/undefined|NaN|\[object Object\]|"cancelled"/);
    expect(container.querySelector('[data-testid="step-running-dot"]')).toBeNull();
  });

  it('empty: columns, one quiet "Query returned 0 rows" (the footer does not repeat it), no chart, no "Showing 0 of 0"', () => {
    const { container } = show('empty');
    const t = screen.getByTestId('data-table');
    expect(within(t).getAllByRole('columnheader').length).toBeGreaterThan(0);
    expect(screen.getByTestId('table-empty')).toBeTruthy();
    expect(t.textContent!.match(/0 rows|No rows/g)).toHaveLength(1);
    expect(t.textContent).not.toMatch(/·\s*·|^\s*·|·\s*$/);
    expect(container.textContent).not.toMatch(/Showing 0 of 0/);
    expect(screen.queryByTestId('chart')).toBeNull();
  });

  it('multi_table: two tables, each with its own View SQL and chart, titled', () => {
    show('multi_table');
    expect(screen.getAllByTestId('data-table')).toHaveLength(2);
    expect(screen.getAllByTestId('table-sql')).toHaveLength(2);
    expect(screen.getAllByTestId('chart')).toHaveLength(2);
    expect(screen.getAllByTestId('table-title').map((e) => e.textContent)).toEqual(['By payer', 'By claim status']);
  });

  it('long_sql: View SQL opens a labelled code block with Copy code and Wrap lines', () => {
    show('long_sql');
    fireEvent.click(within(screen.getByTestId('table-sql')).getByRole('button'));
    const block = screen.getAllByTestId('code-block').at(-1)!;
    expect(within(block).getByTestId('code-lang').textContent).toMatch(/SQL/i);
    expect(within(block).getByRole('button', { name: 'Copy code' })).toBeTruthy();
    const wrap = within(block).getByRole('button', { name: 'Wrap lines' });
    fireEvent.click(wrap);
    expect(within(block).getByRole('button', { name: "Don't wrap lines" }).getAttribute('aria-pressed')).toBe('true');
  });

  it('many_repairs: four repairs collapse under one quiet header; no red "failed" once repaired', () => {
    show('many_repairs');
    const head = screen.getByTestId('step-summary');
    expect(head.textContent).not.toMatch(/failed/);
    expect(head.textContent).toMatch(/all failures repaired/);
    expect(screen.getAllByTestId('step').filter((s) => /Repair/.test(s.textContent ?? '')).length).toBeGreaterThanOrEqual(4);
  });

  it('clarify_nested: each ask is a question with choices; the answer comes last', () => {
    show('clarify_nested');
    expect(screen.getByTestId('clarify-question').textContent).toMatch(/claim count or by billed dollars/);
    expect(screen.getAllByTestId('clarify-chip')).toHaveLength(2);
    cleanup();
    show('clarify_nested_2');
    expect(screen.getByTestId('clarify-question').textContent).toMatch(/time window/);
    cleanup();
    show('clarify_nested_3');
    expect(screen.queryByTestId('clarify')).toBeNull();
    expect(screen.getByTestId('data-table')).toBeTruthy();
  });

  it('escalation_abstain: hand-off shown in steps; one plain abstain with rephrase options; no error styling', () => {
    const { container } = show('escalation_abstain');
    expect(screen.getByTestId('escalation')).toBeTruthy();
    expect(screen.getByTestId('abstain')).toBeTruthy();
    expect(screen.getAllByTestId('rephrase-chip').length).toBeGreaterThan(0);
    expect(container.querySelector('[data-testid="abstain"] .text-danger, [data-testid="abstain"].border-danger')).toBeNull();
  });

  const cancels = EDGE_SCENARIOS.filter((s) => s.scenario.startsWith('cancel_')).map((s) => s.scenario as string);
  it.each(cancels)('%s: one quiet "You stopped this response" note, exactly one retry control; Stop is never a failure', (name) => {
    const { container } = show(name);
    expect(screen.getAllByTestId('stopped')).toHaveLength(1);
    const retries = screen.queryAllByRole('button', { name: /^(Retry|Regenerate)$/ });
    expect(retries).toHaveLength(1);
    expect(screen.getByTestId('stopped').textContent).toMatch(/You stopped this response/);
    expect(container.querySelector('[data-testid="error-retry"], [role="alert"]')).toBeNull();
    for (const s of screen.queryAllByTestId('step')) {
      if (/stopped/.test(s.textContent ?? '')) {
        expect(s.textContent).not.toMatch(/Stopped by the user|Ran query|Final query.*stopped$/);
        expect(s.querySelector('[aria-label="failed"]')).toBeNull();
      }
    }
  });

  it('cancel_sql / cancel_candidates: the stopped query counts as stopped, not failed', () => {
    show('cancel_sql');
    expect(screen.getByTestId('step-summary').textContent).toMatch(/1 stopped/);
    expect(screen.getByTestId('step-summary').textContent).not.toMatch(/failed/);
    cleanup();
    show('cancel_candidates');
    expect(screen.getByTestId('step-summary').textContent).toMatch(/2 stopped/);
    expect(screen.getByTestId('step-summary').textContent).not.toMatch(/failed/);
  });

  it('cancel_render: the partial text stays, no table or chart appears', () => {
    show('cancel_render');
    expect(screen.queryByTestId('data-table')).toBeNull();
    expect(screen.queryByTestId('chart')).toBeNull();
  });
});

describe('callouts read as quiet notes (Claude/ChatGPT fidelity)', () => {
  it('no tinted card, no rounded box: a hairline rule and muted text; caveat icon alone is amber', () => {
    show('empty');
    const notes = screen.getAllByTestId('callout');
    expect(notes.length).toBe(2);
    for (const n of notes) {
      expect(n.className).not.toMatch(/bg-|rounded|shadow|border-l-\[3px\]/);
      expect(n.className).toMatch(/border-l-2 border-line-strong/);
      expect(n.getAttribute('style')).toBeNull();
    }
    const caveat = notes.find((n) => n.dataset.variant !== 'assumption')!;
    expect(caveat.querySelector('svg')!.getAttribute('class')).toMatch(/b7791f/);
  });
});

describe('steps header stays calm', () => {
  it('unresolved failures are counted in muted text, not red (red stays on the failing row only)', () => {
    show('cancel_escalation');
    const c = screen.getByTestId('step-failed-count');
    expect(c.textContent).toMatch(/failed/);
    expect(c.className).not.toMatch(/danger/);
  });
});
