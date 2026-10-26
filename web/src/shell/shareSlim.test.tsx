import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { ShareDialog } from './ShareDialog';

afterEach(cleanup);

// QA-S36: ChatGPT/Claude share dialogs are a title, a quiet preview, one link group and plain actions, not a stack of cards.
describe('share dialog, slim', () => {
  const props = () => ({ title: 'Denial rate by payer', firstQuestion: 'Synthetic question', messageCount: 2, onClose: vi.fn(), onCopyMarkdown: vi.fn(), onDownloadMarkdown: vi.fn(), onPrint: vi.fn() });
  it('has at most one bordered group (the link), and the preview is borderless', () => {
    render(<ShareDialog {...props()} />);
    const dlg = screen.getByRole('dialog', { name: 'Share chat' });
    expect(screen.getByTestId('share-preview').className).not.toMatch(/\bborder\b/);
    const bordered = [...dlg.querySelectorAll('section *, [data-testid="share-preview"]')].filter((el) => /(^|\s)border(\s|$)/.test(el.className) && !/border-t|border-b/.test(el.className));
    expect(bordered).toHaveLength(1);
  });
  it('export is a plain list of text rows that still work', () => {
    const p = props();
    render(<ShareDialog {...p} />);
    const list = screen.getByTestId('share-export-list');
    expect(list.className).not.toMatch(/grid/);
    const rows = within(list).getAllByTestId('share-export-row');
    expect(rows.map((r) => r.textContent)).toEqual(['Copy as Markdown', 'Download Markdown.md', 'Print or save as PDF']);
    for (const r of rows) expect(r.className).not.toMatch(/\bborder\b/);
    fireEvent.click(rows[0]);
    expect(p.onCopyMarkdown).toHaveBeenCalledOnce();
  });
});
