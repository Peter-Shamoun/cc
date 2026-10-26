import { afterEach, describe, expect, it } from 'vitest';
import { useState } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Dialog } from './primitives';

afterEach(cleanup);

function Harness({ fromMenu }: { fromMenu?: boolean }) {
  const [open, setOpen] = useState(false);
  const [menu, setMenu] = useState(false);
  return (
    <>
      <textarea aria-label="Message" data-focus-fallback />
      <button type="button" onClick={() => (fromMenu ? setMenu(true) : setOpen(true))}>Open</button>
      {menu && <button type="button" onClick={() => { setMenu(false); setOpen(true); }}>Settings item</button>}
      {open && (
        <Dialog title="Search chats" onClose={() => setOpen(false)}>
          {/* oxlint-disable-next-line jsx-a11y/no-autofocus -- mirrors SearchDialog */}
          <input aria-label="Search chats" autoFocus />
        </Dialog>
      )}
    </>
  );
}

describe('dialog focus return', () => {
  it('returns focus to the opener even when an input inside autofocuses first', () => {
    render(<Harness />);
    const opener = screen.getByRole('button', { name: 'Open' });
    opener.focus();
    fireEvent.click(opener);
    expect(screen.getByRole('textbox', { name: 'Search chats' })).toHaveFocus();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(opener).toHaveFocus();
  });

  it('falls back to the composer when the opener is gone (a menu item)', () => {
    render(<Harness fromMenu />);
    fireEvent.click(screen.getByRole('button', { name: 'Open' }));
    const item = screen.getByRole('button', { name: 'Settings item' });
    item.focus();
    fireEvent.click(item);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getByRole('textbox', { name: 'Message' })).toHaveFocus();
  });
});
