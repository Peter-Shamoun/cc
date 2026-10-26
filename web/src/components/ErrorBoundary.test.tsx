import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { ErrorBoundary } from './ErrorBoundary';
import { _resetErrorReporting, recentErrors } from '../lib/errorReporting';
import { browserFamily, buildDiagnostics, isChunkLoadError, routeKind } from '../lib/diagnostics';

// Synthetic PHI-shaped values only.
const SECRET = 'Patient Jane Q Synthetic, MRN 4455667788, SSN 123-45-6789, DOB 01/02/1960, jane@example.com';
const PHI = /Jane|Synthetic|4455667788|123-45-6789|01\/02\/1960|example\.com/;
const Boom = ({ msg = SECRET }: { msg?: string }) => { throw new Error(msg); };

let quiet: ReturnType<typeof vi.spyOn>;
beforeEach(() => { quiet = vi.spyOn(console, 'error').mockImplementation(() => {}); }); // React's dev logging
afterEach(() => { cleanup(); quiet.mockRestore(); _resetErrorReporting(); window.location.hash = ''; vi.unstubAllGlobals(); });

describe('crash screen', () => {
  it('shows reload, new chat and diagnostics with no message text or PHI', () => {
    window.location.hash = '#/c/c_9f8e7d6c5b';
    render(<ErrorBoundary><Boom /></ErrorBoundary>);
    const box = screen.getByTestId('app-crash');
    expect(box.textContent).not.toMatch(PHI);
    expect(screen.getByRole('button', { name: 'Reload' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Start a new chat' })).toBeTruthy();
    const diag = screen.getByTestId('crash-diagnostics').textContent!;
    const rec = recentErrors().at(-1)!;
    expect(diag).toContain(`Reference: ${rec.id}`);
    expect(diag).toMatch(/^Error: Error \(render\)$/m);
    expect(diag).toMatch(/^Route: conversation$/m);
    expect(diag).not.toContain('c_9f8e7d6c5b'); // ids stay out of pastes too
    expect(diag).not.toContain(navigator.userAgent); // family + major only
    expect(diag).not.toContain(rec.message); // even the redacted message is left out
    expect(diag).not.toMatch(PHI);
  });

  it('copies exactly the shown diagnostics and announces it, with a fallback message on failure', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    render(<ErrorBoundary><Boom /></ErrorBoundary>);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Copy diagnostics' })); });
    expect(writeText).toHaveBeenCalledWith(screen.getByTestId('crash-diagnostics').textContent);
    expect(writeText.mock.calls[0][0]).not.toMatch(PHI);
    expect(screen.getByText('Copied')).toBeTruthy();
    writeText.mockRejectedValueOnce(new Error('denied'));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Copy diagnostics' })); });
    expect(screen.getByText(/Couldn't copy/)).toBeTruthy();
  });

  it('explains a failed lazy-chunk load as a connection/update problem', () => {
    render(<ErrorBoundary><Boom msg="Failed to fetch dynamically imported module: https://x/assets/MarkdownImpl-abc.js" /></ErrorBoundary>);
    expect(screen.getByRole('heading').textContent).toBe("Couldn't load part of the app");
    expect(recentErrors().at(-1)!.context).toMatchObject({ component: 'App', code: 'chunk_load' });
  });

  it('section scope shows an inline card, and "Try again" re-renders the children', () => {
    function Flaky() {
      const [ok, setOk] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOk(true)}>fix</button>
          <ErrorBoundary scope="section" name="ArtifactPanel">{ok ? <p>recovered</p> : <Boom />}</ErrorBoundary>
        </>
      );
    }
    render(<Flaky />);
    const card = screen.getByTestId('section-crash');
    expect(card.textContent).toMatch(/couldn't be displayed/);
    expect(card.textContent).not.toMatch(PHI);
    expect(screen.queryByTestId('app-crash')).toBeNull();
    expect(recentErrors().at(-1)!.context).toEqual({ component: 'ArtifactPanel' });
    fireEvent.click(screen.getByText('fix'));
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(screen.getByText('recovered')).toBeTruthy();
  });
});

describe('diagnostics helpers', () => {
  it('reduces the user agent to family, major version and OS', () => {
    expect(browserFamily('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.7390.54 Safari/537.36 Edg/141.0.3537.57')).toBe('Edge 141 · Windows');
    expect(browserFamily('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Safari/605.1.15')).toBe('Safari 18 · macOS');
    expect(browserFamily('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/141.0 Mobile/15E148 Safari/604.1')).toBe('Chrome 141 · iOS');
    expect(browserFamily('Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0')).toBe('Firefox 133 · Linux');
    expect(browserFamily('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/141.0.0.0 Safari/537.36')).toBe('Chrome (headless) 141 · Linux');
  });
  it('names the route kind, never the id', () => {
    expect(routeKind('#/c/c_123abc', '')).toBe('conversation');
    expect(routeKind('', '')).toBe('new chat');
    expect(routeKind('#/weird/thing', '?share=s_1')).toBe('other (shared)');
  });
  it('detects chunk-load failures across browsers', () => {
    expect(isChunkLoadError(new TypeError('Failed to fetch dynamically imported module: /assets/a.js'))).toBe(true);
    expect(isChunkLoadError(new TypeError('error loading dynamically imported module'))).toBe(true);
    expect(isChunkLoadError(new TypeError('Importing a module script failed.'))).toBe(true);
    expect(isChunkLoadError(new Error(SECRET))).toBe(false);
  });
  it('lists other recent errors by id and class only', () => {
    render(<ErrorBoundary><Boom /></ErrorBoundary>);
    const first = recentErrors().at(-1)!;
    render(<ErrorBoundary><Boom msg="second Jane" /></ErrorBoundary>);
    const text = buildDiagnostics(recentErrors().at(-1)!);
    expect(text).toContain(`${first.id} Error (render)`);
    expect(text).not.toMatch(PHI);
  });
});
