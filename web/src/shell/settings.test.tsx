import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { SettingsDialog } from './SettingsDialog';
import { ThemeProvider } from '../lib/theme';

afterEach(cleanup);

function open(tab?: 'general' | 'data' | 'shortcuts') {
  const props = { onClose: vi.fn(), storeMode: 'ephemeral', enterToSend: true, setEnterToSend: vi.fn(), onExportAll: vi.fn(), threadCount: 0 };
  render(<ThemeProvider><SettingsDialog {...props} initialTab={tab} /></ThemeProvider>);
  return props;
}

describe('settings layout', () => {
  it('titles the panel with its section and keeps the panel keyboard-reachable', () => {
    open();
    const panel = screen.getByRole('tabpanel');
    expect(within(panel).getByRole('heading', { level: 3, name: 'General' })).toBeInTheDocument();
    expect(panel).toHaveAttribute('tabindex', '0');
    expect(within(panel).getAllByTestId('settings-row')).toHaveLength(3);
  });

  it('groups data controls under Storage and Export, and Export is disabled with no chats', () => {
    open('data');
    const panel = screen.getByRole('tabpanel');
    expect(within(panel).getAllByRole('heading', { level: 4 }).map((h) => h.textContent)).toEqual(['Storage', 'Export']);
    expect(within(panel).getByRole('button', { name: 'Export' })).toBeDisabled();
  });

  it('arrow keys move between sections', () => {
    open();
    fireEvent.keyDown(screen.getByRole('tab', { name: 'General' }), { key: 'ArrowDown' });
    expect(screen.getByRole('tab', { name: 'Data controls' })).toHaveAttribute('aria-selected', 'true');
    expect(within(screen.getByRole('tabpanel')).getByRole('heading', { level: 3 })).toHaveTextContent('Data controls');
  });
});

describe('settings radio groups (QA-S17)', () => {
  it('each group is one Tab stop: only the checked radio is tabbable', () => {
    open();
    for (const name of ['Theme', 'Style']) {
      const radios = within(screen.getByRole('radiogroup', { name })).getAllByRole('radio');
      expect(radios.filter((r) => r.getAttribute('tabindex') === '0')).toHaveLength(1);
      expect(radios.find((r) => r.getAttribute('tabindex') === '0')).toHaveAttribute('aria-checked', 'true');
      expect(radios.filter((r) => r.getAttribute('tabindex') === '-1')).toHaveLength(radios.length - 1);
    }
  });

  it('arrows select and focus the next radio, wrapping; Home/End jump; the tab stop follows', () => {
    open();
    const group = screen.getByRole('radiogroup', { name: 'Theme' });
    const radios = () => within(group).getAllByRole('radio');
    const checked = () => radios().find((r) => r.getAttribute('aria-checked') === 'true')!;
    const start = radios().indexOf(checked());
    fireEvent.keyDown(checked(), { key: 'ArrowRight' });
    const n = radios().length;
    expect(radios().indexOf(checked())).toBe((start + 1) % n);
    expect(document.activeElement).toBe(checked());
    expect(checked()).toHaveAttribute('tabindex', '0');
    fireEvent.keyDown(checked(), { key: 'End' });
    expect(radios().indexOf(checked())).toBe(n - 1);
    fireEvent.keyDown(checked(), { key: 'ArrowDown' });
    expect(radios().indexOf(checked())).toBe(0);
    fireEvent.keyDown(checked(), { key: 'ArrowLeft' });
    expect(radios().indexOf(checked())).toBe(n - 1);
    fireEvent.keyDown(checked(), { key: 'Home' });
    expect(radios().indexOf(checked())).toBe(0);
    expect(document.activeElement).toBe(checked());
    expect(radios().filter((r) => r.getAttribute('tabindex') === '0')).toEqual([checked()]);
  });

  it('mirrors Left/Right in RTL', () => {
    document.documentElement.dir = 'rtl';
    try {
      open();
      const group = screen.getByRole('radiogroup', { name: 'Theme' });
      const radios = within(group).getAllByRole('radio');
      expect(radios.length).toBeGreaterThan(2);
      fireEvent.click(radios[0]);
      fireEvent.keyDown(radios[0], { key: 'ArrowLeft' });
      expect(radios[1]).toHaveAttribute('aria-checked', 'true');
    } finally { document.documentElement.dir = ''; }
  });
});
