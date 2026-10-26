import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { StarterCards } from './StarterCards';

const dims = (el: HTMLElement, d: { scrollLeft: number; clientWidth: number; scrollWidth: number }) => {
  for (const [k, v] of Object.entries(d)) Object.defineProperty(el, k, { configurable: true, value: v });
};

describe('StarterCards edge fade (QA-S11)', () => {
  it('fades only the edge with more cards behind it', () => {
    render(<StarterCards onPick={vi.fn()} />);
    const row = screen.getByTestId('starter-cards');
    expect(row).not.toHaveAttribute('data-fade');
    dims(row, { scrollLeft: 0, clientWidth: 390, scrollWidth: 900 });
    fireEvent.scroll(row);
    expect(row).toHaveAttribute('data-fade', 'end');
    dims(row, { scrollLeft: 200, clientWidth: 390, scrollWidth: 900 });
    fireEvent.scroll(row);
    expect(row).toHaveAttribute('data-fade', 'start end');
    dims(row, { scrollLeft: 510, clientWidth: 390, scrollWidth: 900 });
    fireEvent.scroll(row);
    expect(row).toHaveAttribute('data-fade', 'start');
    expect(row.style.getPropertyValue('--cc-fade')).toMatch(/^linear-gradient\(to right, transparent, .*#000\)$/);
  });
});
