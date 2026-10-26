import { describe, expect, it } from 'vitest';
import { trackInputModality } from './inputModality';

describe('input modality (QA-S15)', () => {
  it('Tab marks keyboard navigation; typing does not; a pointer press clears it', () => {
    const html = document.documentElement;
    const stop = trackInputModality();
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }));
    expect(html.hasAttribute('data-kbd')).toBe(false);
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' }));
    expect(html.hasAttribute('data-kbd')).toBe(true);
    window.dispatchEvent(new Event('pointerdown'));
    expect(html.hasAttribute('data-kbd')).toBe(false);
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', shiftKey: true }));
    expect(html.hasAttribute('data-kbd')).toBe(true);
    stop();
    expect(html.hasAttribute('data-kbd')).toBe(false);
  });
});
