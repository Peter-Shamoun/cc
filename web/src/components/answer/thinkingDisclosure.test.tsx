import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { StepTimeline, currentActivity } from './StepTimeline';
import type { Step } from './provisional';

afterEach(() => { cleanup(); vi.useRealTimers(); });

const sql = (id: string, state: Step['state'], label = `Query ${id}`, durationMs?: number): Step => ({ id, kind: 'sql', state, label, durationMs });

describe('calmer thinking disclosure', () => {
  it('currentActivity: running step, else latest reasoning note, else last step, else "Thinking"; trailing ellipsis dropped', () => {
    expect(currentActivity([], [])).toBe('Thinking');
    expect(currentActivity([sql('a', 'ok', 'Read schema')], [])).toBe('Read schema');
    expect(currentActivity([sql('a', 'ok')], [{ summary: 'Looking at denials…\nmore' }])).toBe('Looking at denials');
    expect(currentActivity([sql('a', 'ok'), sql('b', 'running', 'Running query…')], [{ summary: 'note' }])).toBe('Running query');
  });

  it('is collapsed while it runs, the header names the current step, and clicking expands the steps', () => {
    const { rerender } = render(<StepTimeline live steps={[sql('a', 'running', 'Reading schema')]} />);
    const btn = screen.getByTestId('step-summary');
    expect(btn).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByTestId('step-activity')).toHaveTextContent(/^Reading schema$/);
    expect(document.querySelector('.step-body')).toHaveAttribute('hidden');
    rerender(<StepTimeline live steps={[sql('a', 'ok', 'Reading schema'), sql('b', 'running', 'Counting denials')]} />);
    expect(screen.getByTestId('step-activity')).toHaveTextContent(/^Counting denials$/);
    expect(btn).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(btn);
    expect(btn).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getAllByTestId('step')).toHaveLength(2);
    fireEvent.click(btn);
    expect(btn).toHaveAttribute('aria-expanded', 'false');
  });

  it('stays as the reader left it when it finishes, and then reads "Thought for Ns"', () => {
    const { rerender } = render(<StepTimeline live steps={[sql('a', 'running')]} />);
    fireEvent.click(screen.getByTestId('step-summary'));
    rerender(<StepTimeline live={false} steps={[sql('a', 'ok', 'Query a', 3200)]} />);
    expect(screen.getByTestId('step-summary')).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByTestId('step-activity').textContent).toMatch(/^Thought for 3\.2s/);
  });

  it('no spinner, no shimmer, no ticking timer: only a fade class on the live line', () => {
    vi.useFakeTimers();
    render(<StepTimeline live steps={[sql('a', 'running', 'Counting denials')]} />);
    const btn = screen.getByTestId('step-summary');
    const before = btn.textContent;
    act(() => { vi.advanceTimersByTime(3000); });
    expect(btn.textContent).toBe(before);
    expect(btn.textContent).toBe('Counting denials');
    expect(document.querySelector('.animate-spin, .shimmer, [class*="shimmer"]')).toBeNull();
    expect(screen.getByTestId('step-activity')).toHaveClass('cc-step-fade');
  });

  it('the done header has no fade class', () => {
    render(<StepTimeline live={false} steps={[sql('a', 'ok', 'q', 1200)]} />);
    expect(screen.getByTestId('step-activity')).not.toHaveClass('cc-step-fade');
  });

  it('adds no live region of its own (QA-S16: the shell status region announces the turn once); the timer never shows', () => {
    vi.useFakeTimers();
    const { container, rerender } = render(<StepTimeline live steps={[sql('a', 'running', 'Reading schema')]} />);
    expect(container.querySelector('[aria-live]')).toBeNull();
    act(() => { vi.advanceTimersByTime(2000); });
    expect(screen.getByTestId('step-activity')).toHaveTextContent(/^Reading schema$/);
    rerender(<StepTimeline live={false} steps={[sql('a', 'ok', 'Reading schema', 400), sql('b', 'ok', 'Counting denials', 900)]} />);
    expect(container.querySelector('[aria-live]')).toBeNull();
  });

  it('reduced motion turns the header fade off (global animation reset covers .cc-step-fade)', () => {
    const css = readFileSync(resolve(__dirname, '../../index.css'), 'utf8');
    expect(css).toMatch(/\.cc-step-fade\s*\{[^}]*animation:\s*cc-fade 200ms/);
    const rm = css.slice(css.indexOf('prefers-reduced-motion: reduce'));
    expect(rm).toMatch(/animation(-duration)?:\s*(none|0|0\.01ms)/);
    // Shimmer-free everywhere: the pulsing text effect is gone from the stylesheet (status lines and tool rows too).
    expect(css).not.toMatch(/cc-shimmer|\.shimmer\b/);
  });
});
