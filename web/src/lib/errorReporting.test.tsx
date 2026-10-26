import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { _resetErrorReporting, installGlobalErrorHandlers, recentErrors, reportError, setReporter, type ErrorRecord } from './errorReporting';
import { ErrorBoundary } from '../components/ErrorBoundary';

const PHI = /Jane|123-45-6789|99887766/;
afterEach(() => _resetErrorReporting());

describe('reportError', () => {
  it('redacts, allowlists context, and feeds the reporter', () => {
    const seen: ErrorRecord[] = [];
    setReporter((r) => seen.push(r));
    const r = reportError(new Error('no row for MRN 99887766'), 'handled', { action: "open 'Jane'", status: 500, question: 'Jane Doe?' });
    expect(r.message).toBe('no row for MRN [number]');
    expect(r.context).toEqual({ action: 'open [text]', status: 500 });
    expect(seen).toEqual([r]);
    expect(JSON.stringify(recentErrors())).not.toMatch(PHI);
  });
  it('keeps a bounded in-memory ring and survives a throwing reporter', () => {
    setReporter(() => { throw new Error('boom'); });
    for (let i = 0; i < 30; i++) reportError(new Error(`e${i}`));
    expect(recentErrors()).toHaveLength(20);
  });
});

describe('global handlers', () => {
  it('capture window errors and rejections, quiet mode prevents the raw console print', () => {
    installGlobalErrorHandlers(window, true);
    const ev = new ErrorEvent('error', { error: new Error('lookup "Jane Doe" 123-45-6789'), cancelable: true });
    window.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
    const rej = new Event('unhandledrejection', { cancelable: true }) as PromiseRejectionEvent;
    Object.assign(rej, { reason: new Error('"Jane"') });
    window.dispatchEvent(rej);
    expect(recentErrors().map((r) => r.kind)).toEqual(['window', 'promise']);
    expect(JSON.stringify(recentErrors())).not.toMatch(PHI);
  });
});

describe('ErrorBoundary', () => {
  it('shows a generic fallback with a reference id and no error text', () => {
    const Boom = () => { throw new Error('Jane Doe 123-45-6789'); };
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {}); // React's own dev logging
    render(<ErrorBoundary><Boom /></ErrorBoundary>);
    spy.mockRestore();
    const box = screen.getByTestId('app-crash');
    expect(box.textContent).toMatch(/Something went wrong/);
    expect(box.textContent).not.toMatch(PHI);
    const last = recentErrors().at(-1)!; // dev React may report the same crash more than once
    expect(box.textContent).toContain(last.id);
    expect(last).toMatchObject({ kind: 'render', context: { component: 'App' } });
  });
});
