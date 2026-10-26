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
