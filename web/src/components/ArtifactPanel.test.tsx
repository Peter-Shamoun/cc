import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ArtifactPanel } from './ArtifactPanel';
import type { Artifact } from './Message';

const artifact: Artifact = {
  title: 'Denials by payer',
  related: [],
  part: { type: 'table', id: 't1', title: 'Denials by payer', columns: [{ key: 'payer', label: 'Payer', type: 'string' }, { key: 'n', label: 'Claims', type: 'number' }], rows: [['Northwind', 12], ['Bluebird', 7]], rowCount: 2 },
};

function mockPhone(phone: boolean) {
  window.matchMedia = vi.fn().mockImplementation((q: string) => ({
    matches: phone && q.includes('max-width'), media: q, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

afterEach(() => { cleanup(); localStorage.clear(); });

describe('ArtifactPanel shell behaviour', () => {
  it('desktop: keyboard-resizable separator with ARIA values, width saved as a fraction pref only', () => {
    mockPhone(false);
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1600 });
    render(<ArtifactPanel artifact={artifact} onClose={() => {}} />);
    const sep = screen.getByRole('separator', { name: 'Resize panel' });
    expect(sep.getAttribute('aria-valuenow')).toBe('800');
    fireEvent.keyDown(sep, { key: 'ArrowLeft' });
    expect(sep.getAttribute('aria-valuenow')).toBe('824');
    fireEvent.keyDown(sep, { key: 'End' });
    expect(sep.getAttribute('aria-valuenow')).toBe('360');
    expect(localStorage.getItem('cc.pref.artifactWidth')).toBe('0.225');
    expect(Object.keys(localStorage)).toEqual(['cc.pref.artifactWidth']);
    cleanup();
    render(<ArtifactPanel artifact={artifact} onClose={() => {}} />);
    expect(screen.getByRole('separator', { name: 'Resize panel' }).getAttribute('aria-valuenow')).toBe('360');
    expect(screen.getByRole('button', { name: 'Full screen' })).toBeTruthy();
  });

  it('phone: a full-screen modal sheet, no resize handle, Esc closes', () => {
    mockPhone(true);
    const onClose = vi.fn();
    render(<ArtifactPanel artifact={artifact} onClose={onClose} />);
    const sheet = screen.getByRole('dialog', { name: 'Artifact: Denials by payer' });
    expect(sheet.getAttribute('aria-modal')).toBe('true');
    expect(screen.queryByRole('separator', { name: 'Resize panel' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Full screen' })).toBeNull();
    expect(sheet.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
});
