import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Menu, menuTop } from './primitives';
import { SettingsDialog } from './SettingsDialog';
import { ThemeProvider } from '../lib/theme';

afterEach(cleanup);

// 10:48 directive parity pass 2: menus never run off-screen, denser picker rows, settings values as plain text.
describe('parity pass 2', () => {
  const r = { top: 40, bottom: 80 };
  it('menuTop: opens on the preferred side when it fits', () => {
    expect(menuTop(r, 300, 'bottom', 900)).toEqual({ top: 84 });
    expect(menuTop({ top: 800, bottom: 840 }, 300, 'top', 900)).toEqual({ top: 496 });
  });
  it('menuTop: flips when only the other side fits', () => {
    expect(menuTop({ top: 800, bottom: 840 }, 300, 'bottom', 900)).toEqual({ top: 496 });
    expect(menuTop(r, 300, 'top', 900)).toEqual({ top: 84 });
  });
  it('menuTop: a menu taller than either side scrolls on the roomier side instead of running off-screen', () => {
    expect(menuTop(r, 1200, 'bottom', 900)).toEqual({ top: 84, maxHeight: 808 });
    expect(menuTop({ top: 860, bottom: 890 }, 1200, 'bottom', 900)).toEqual({ top: 8, maxHeight: 848 });
    expect(menuTop({ top: 4, bottom: 8 }, 1200, 'bottom', 10).maxHeight).toBe(0);
  });
  it('the menu scrolls inside itself and picker descriptions are compact', () => {
    const anchor = document.createElement('button');
    document.body.append(anchor);
    render(<Menu anchor={anchor} label="Product" onClose={vi.fn()} items={[{ label: 'Standard', description: 'One model explores the data.', meta: 'Low cost', checked: true, onSelect: vi.fn() }]} />);
    const menu = screen.getByRole('menu');
    expect(menu.className).toMatch(/overflow-y-auto/);
    expect(screen.getByRole('menuitemradio').className).toMatch(/py-1\.5/);
    expect(screen.getByTestId('menu-desc').className).toMatch(/leading-\[18px\]/);
    anchor.remove();
  });
  it('settings status values are plain muted text, not pills', () => {
    render(<ThemeProvider><SettingsDialog onClose={vi.fn()} storeMode="ephemeral" enterToSend setEnterToSend={vi.fn()} onExportAll={vi.fn()} threadCount={0} initialTab="data" /></ThemeProvider>);
    const values = screen.getAllByTestId('settings-value');
    expect(values.map((v) => v.textContent)).toEqual(['Memory only', 'Memory only']);
    for (const v of values) expect(v.className).not.toMatch(/rounded-full|bg-muted/);
  });
});
