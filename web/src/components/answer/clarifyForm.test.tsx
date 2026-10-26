import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { ClarifyForm, DOCK_ID, shortHeader, toAnswers } from './ClarifyForm';
import { validateClarifyAnswers } from '../../contract/clarify';
import { CLARIFY_FORM } from '../../fixtures/clarifyForm';
import { AssistantTurn } from '../Message';
import type { AssistantViewV1 } from '../../api/contract';

vi.mock('vega-embed', () => ({ default: async () => ({ view: { finalize() {}, toImageURL: async () => '' } }) }));
vi.mock('shiki', () => ({ createHighlighter: async () => ({ codeToHtml: (c: string) => `<pre>${c}</pre>` }) }));
afterEach(() => { cleanup(); document.getElementById(DOCK_ID)?.remove(); });

const tabs = () => screen.queryAllByTestId('clarify-tab');
const options = () => screen.getAllByTestId('clarify-option');

describe('multi-question clarify, Claude-style (one at a time, docked above the composer)', () => {
  it('shows one question at a time with short header tabs, 2–4 options with one-line descriptions, Other and Skip', () => {
    render(<ClarifyForm part={CLARIFY_FORM} active onReply={() => {}} />);
    expect(tabs().map((t) => t.textContent)).toEqual(['Metric', 'Period', 'Split by']);
    expect(tabs()[0]).toHaveAttribute('aria-selected', 'true');
    expect(tabs()[1]).toBeDisabled();
    expect(screen.getAllByTestId('clarify-question')).toHaveLength(1);
    expect(screen.getByTestId('clarify-question')).toHaveTextContent('Which denial measure?');
    expect(options()).toHaveLength(2);
    expect(options()[0]).toHaveTextContent('Claim count rate');
    expect(options()[0]).toHaveTextContent('Denied claims ÷ adjudicated claims');
    expect(screen.getByRole('radiogroup')).toBeInTheDocument();
    expect(screen.getByTestId('clarify-other')).toHaveAttribute('placeholder', 'Other…');
    expect(screen.getByTestId('clarify-skip')).toBeInTheDocument();
    expect(screen.getByText(/Or type a reply below/)).toBeInTheDocument();
  });

  it('single-select advances on click; multi-select toggles then Submit; resume carries every choice id and a readable text', () => {
    const reply = vi.fn();
    render(<ClarifyForm part={CLARIFY_FORM} active onReply={reply} />);
    fireEvent.click(options()[1]);
    expect(screen.getByTestId('clarify-question')).toHaveTextContent('Which period?');
    expect(tabs()[0].querySelector('svg')).not.toBeNull(); // answered tab gets a quiet check
    fireEvent.click(screen.getByTestId('clarify-skip'));
    expect(screen.getByTestId('clarify-question')).toHaveTextContent(/Break it down by/);
    expect(screen.getByRole('group', { name: /Break it down by/ })).toBeInTheDocument();
    expect(screen.getByTestId('clarify-next')).toBeDisabled();
    fireEvent.click(options()[0]);
    fireEvent.click(options()[2]);
    expect(options()[0]).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(options()[2]);
    fireEvent.click(options()[2]);
    expect(screen.getByTestId('clarify-next')).toHaveTextContent('Submit');
    fireEvent.click(screen.getByTestId('clarify-next'));
    // Contract 1.23.0: resume.answers, one per question; the visible turn is clarifySummary.
    expect(reply).toHaveBeenCalledWith('Metric: Dollar rate; Period: no preference; Split by: Payer, Denial reason',
      { clarificationId: 'clf_demo1', answers: [{ questionId: 'metric', choiceIds: ['dollar_rate'] }, { questionId: 'period', skipped: true }, { questionId: 'split', choiceIds: ['payer', 'carc'] }] });
    expect(validateClarifyAnswers(CLARIFY_FORM.questions, reply.mock.calls[0][1].answers)).toBeNull();
    // Settled: the panel goes; one muted line echoes the answers.
    expect(screen.queryAllByTestId('clarify-option')).toHaveLength(0);
    expect(screen.getByTestId('clarify-answered')).toHaveTextContent('Metric: Dollar rate');
  });

  it('"Other" free text answers the question with Enter; earlier tabs can be revisited', () => {
    const reply = vi.fn();
    render(<ClarifyForm part={{ ...CLARIFY_FORM, questions: CLARIFY_FORM.questions.slice(0, 2) }} active onReply={reply} />);
    fireEvent.change(screen.getByTestId('clarify-other'), { target: { value: 'Paid-to-billed ratio' } });
    expect(screen.getByTestId('clarify-next')).toHaveTextContent('Next');
    fireEvent.submit(screen.getByTestId('clarify-other').closest('form')!);
    expect(screen.getByTestId('clarify-question')).toHaveTextContent('Which period?');
    fireEvent.click(tabs()[0]);
    expect(screen.getByTestId('clarify-question')).toHaveTextContent('Which denial measure?');
    expect(screen.getByTestId('clarify-other')).toHaveValue('Paid-to-billed ratio');
    fireEvent.click(options()[0]);
    fireEvent.click(options()[0]);
    expect(reply.mock.calls[0][0]).toBe('Metric: Claim count rate; Period: Q3 2026');
  });

  it('keyboard: number keys pick, arrows move between options and Other', () => {
    const reply = vi.fn();
    render(<ClarifyForm part={{ ...CLARIFY_FORM, questions: [CLARIFY_FORM.questions[1]] }} active onReply={reply} />);
    expect(tabs()).toHaveLength(0); // a single question has no tab strip
    const panel = screen.getByTestId('clarify-form');
    options()[0].focus();
    fireEvent.keyDown(panel, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(options()[1]);
    fireEvent.keyDown(panel, { key: 'ArrowUp' });
    fireEvent.keyDown(panel, { key: 'ArrowUp' });
    expect(document.activeElement).toBe(screen.getByTestId('clarify-other'));
    options()[0].focus();
    fireEvent.keyDown(panel, { key: '3' });
    expect(reply.mock.calls[0][0]).toBe('Period: Last 12 months');
  });

  it('caps at 4 questions × 4 options and 12-character headers', () => {
    const many = { ...CLARIFY_FORM, questions: Array.from({ length: 6 }, (_, i) => ({ id: `q${i}`, header: `Header number ${i}`, question: `Q${i}?`, options: Array.from({ length: 6 }, (_, j) => ({ id: `o${j}`, label: `O${j}`, description: `D${j}` })) })) };
    render(<ClarifyForm part={many} active />);
    expect(tabs()).toHaveLength(4);
    expect(options()).toHaveLength(4);
    expect(shortHeader('Header number 0')).toHaveLength(12);
    expect(tabs()[0].textContent).toBe('Header numb…');
  });

  it('docks into #cc-composer-dock when the shell provides it; otherwise sits sticky at the end of the answer', () => {
    const { unmount } = render(<ClarifyForm part={CLARIFY_FORM} active />);
    expect(screen.getByTestId('clarify-form').className).toMatch(/sticky bottom-2/);
    unmount();
    const dock = document.createElement('div');
    dock.id = DOCK_ID;
    document.body.appendChild(dock);
    const { container } = render(<ClarifyForm part={CLARIFY_FORM} active />);
    expect(within(dock).getByTestId('clarify-form')).toHaveAttribute('data-docked', 'true');
    expect(container.querySelector('[data-testid="clarify-form"]')).toBeNull();
    expect(container).toHaveTextContent('A few details'); // the intro stays in the message
  });

  it('inactive, answered or read-only: no controls, just the muted outcome; neutral styling throughout', () => {
    render(<ClarifyForm part={{ ...CLARIFY_FORM, answered: true }} active />);
    expect(screen.queryAllByRole('button')).toHaveLength(0);
    expect(screen.getByTestId('clarify-answered')).toHaveTextContent('Answered');
    cleanup();
    const { container } = render(<ClarifyForm part={CLARIFY_FORM} active />);
    expect(container.innerHTML).not.toMatch(/gradient|shadow-|purple|blue-|indigo|rounded-full/);
  });

  it('toAnswers: question order, empty = skipped, single-select Other replaces a pick, always valid for the server', () => {
    const qs = CLARIFY_FORM.questions;
    expect(toAnswers(qs, {})).toEqual(qs.map((q) => ({ questionId: q.id, skipped: true })));
    const out = toAnswers(qs, {
      metric: { picked: ['count_rate'], other: ' Paid ratio ', skipped: false },
      period: { picked: ['q3', 'ytd'], other: '', skipped: false },
      split: { picked: ['payer'], other: 'Region', skipped: false },
    });
    expect(out).toEqual([
      { questionId: 'metric', otherText: 'Paid ratio' },
      { questionId: 'period', choiceIds: ['q3'] },
      { questionId: 'split', choiceIds: ['payer'], otherText: 'Region' },
    ]);
    expect(validateClarifyAnswers(qs, out)).toBeNull();
    expect(toAnswers(qs, { metric: { picked: ['count_rate'], other: '', skipped: true } })[0]).toEqual({ questionId: 'metric', skipped: true });
  });

  it('AssistantTurn renders the Claude-style form for a clarification with questions[], the single card otherwise', () => {
    const msg = { id: 'a1', role: 'assistant', parts: [CLARIFY_FORM], streaming: false, steps: [], escalations: [] } as unknown as AssistantViewV1;
    render(<AssistantTurn msg={msg} isLast onOpen={() => {}} onRegenerate={() => {}} onEdit={() => {}} onReply={() => {}} />);
    expect(screen.getByTestId('clarify-form')).toBeInTheDocument();
    expect(screen.queryByTestId('clarify')).toBeNull();
  });
});
