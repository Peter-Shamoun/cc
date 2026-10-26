import { describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from '../App';
import { bucket, groupThreads } from '../components/Sidebar';
import { MemoryTranscriptStore } from '../adapters/memoryStore';
import { matchSlash } from './slash';
import { transcriptMarkdown, exportFilename } from './exportMarkdown';
import { getDraft, setDraft, moveDraft, NEW_CHAT_KEY } from './drafts';

vi.mock('../adapters/mock', async (orig) => {
  const m = await orig<typeof import('../adapters/mock')>();
  return { ...m, MockAnalystAdapter: class extends m.MockAnalystAdapter { constructor() { super(0); } } };
});
vi.mock('vega-embed', () => ({ default: async () => ({ view: { finalize() {}, toImageURL: async () => '' } }) }));
vi.mock('shiki', () => ({ createHighlighter: async () => ({ codeToHtml: (c: string) => `<pre>${c}</pre>` }) }));

describe('sidebar grouping', () => {
  const now = new Date(2026, 9, 25, 15, 0);
  const at = (d: number, h = 9) => new Date(2026, 9, 25 - d, h).toISOString();
  it('buckets by local calendar day', () => {
    expect(bucket(at(0, 1), now)).toBe('Today');
    expect(bucket(at(1, 23), now)).toBe('Yesterday');
    expect(bucket(at(5), now)).toBe('Previous 7 days');
    expect(bucket(at(20), now)).toBe('Previous 30 days');
    expect(bucket(new Date(2026, 1, 3).toISOString(), now)).toBe('February');
    expect(bucket(new Date(2024, 1, 3).toISOString(), now)).toBe('2024');
  });
  it('puts pinned chats first', () => {
    const g = groupThreads([{ id: 'a', title: 'A', updatedAt: at(0) }, { id: 'b', title: 'B', updatedAt: at(3), pinned: true }], now);
    expect(g.map(([k]) => k)).toEqual(['Pinned', 'Today']);
  });
});

describe('memory store search + pin', () => {
  it('finds text inside messages, not only titles, with a snippet', async () => {
    const s = new MemoryTranscriptStore();
    await s.appendMessage('c1', { id: 'u1', role: 'user', text: 'denials', createdAt: '' }, 'Denials');
    await s.appendMessage('c1', { id: 'a1', role: 'assistant', parts: [{ type: 'text', id: 't', text: 'Cascade Medicaid has the highest CO-197 share this quarter.' }], createdAt: '' } as never);
    const hits = await s.search('co-197');
    expect(hits).toHaveLength(1);
    expect(hits[0].messageId).toBe('a1');
    expect(hits[0].snippet.slice(hits[0].matchStart, hits[0].matchStart + hits[0].matchLength)).toBe('CO-197');
    expect((await s.search('denials'))[0].messageId).toBeUndefined();
    await s.setPinned('c1', true);
    expect((await s.listConversations()).items[0].pinned).toBe(true);
  });
});

describe('slash commands', () => {
  it('matches by prefix and hides once a space is typed', () => {
    expect(matchSlash('/de')?.map((c) => c.name)).toContain('denials');
    expect(matchSlash('/denials x')).toBeNull();
    expect(matchSlash('hello')).toBeNull();
  });
});

describe('export', () => {
  it('writes a markdown transcript and a safe filename', () => {
    const md = transcriptMarkdown('Denials Q3', [
      { id: 'u', role: 'user', text: 'Rate by payer?', createdAt: '' },
      { id: 'a', role: 'assistant', createdAt: '', parts: [{ type: 'text', id: 't', text: '**14%** overall' }] } as never,
    ], new Date('2026-10-25T00:00:00Z'));
    expect(md).toContain('# Denials Q3');
    expect(md).toContain('## You\n\nRate by payer?');
    expect(md).toContain('**14%** overall');
    expect(exportFilename('Denials: Q3 / payer?')).toBe('denials-q3-payer.md');
  });
});

describe('drafts', () => {
  it('are per chat, memory only, and follow a new chat to its id', () => {
    setDraft(NEW_CHAT_KEY, 'half typed');
    moveDraft(NEW_CHAT_KEY, 'c9');
    expect(getDraft('c9')).toBe('half typed');
    expect(getDraft(NEW_CHAT_KEY)).toBe('');
    expect(Object.keys(localStorage).some((k) => localStorage.getItem(k)?.includes('half typed'))).toBe(false);
    expect(Object.keys(sessionStorage)).toHaveLength(0);
  });
});

describe('app shell', () => {
  it('deletes with undo, pins, opens settings and switches theme', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
    render(<App />);
    const box = screen.getByRole('textbox', { name: 'Message' });
    fireEvent.change(box, { target: { value: 'Which CARC codes drive Cascade Medicaid denials?' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(screen.getByTestId('msg-actions')).toBeInTheDocument(), { timeout: 5000 });
    expect(window.location.hash).toMatch(/^#\/c\/c_/);
    // pin via the "…" menu
    fireEvent.click(screen.getByRole('button', { name: /Options for/ }));
    await act(async () => fireEvent.click(screen.getByRole('menuitem', { name: 'Pin' })));
    await waitFor(() => expect(screen.getByRole('region', { name: 'Pinned' })).toBeInTheDocument());
    // delete -> hidden, undo -> back
    fireEvent.click(screen.getByRole('button', { name: /Options for/ }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete' }));
    expect(screen.queryByTestId('thread-item')).toBeNull();
    expect(screen.getByText('What should we look into?')).toBeInTheDocument();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'Message' })));
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(screen.getByTestId('thread-item')).toBeInTheDocument();
    // undo reopens the chat that was open
    await waitFor(() => expect(screen.getByTestId('msg-actions')).toBeInTheDocument());
    // settings: theme radio
    fireEvent.keyDown(window, { key: ',', ctrlKey: true });
    fireEvent.click(screen.getByRole('radio', { name: /Dark/ }));
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    fireEvent.click(screen.getByRole('radio', { name: /Light/ }));
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    fireEvent.keyDown(window, { key: 'Escape' });
    // slash menu runs an action
    const b2 = screen.getByRole('textbox', { name: 'Message' });
    fireEvent.change(b2, { target: { value: '/shortc' } });
    expect(screen.getByTestId('slash-menu')).toBeInTheDocument();
    fireEvent.keyDown(b2, { key: 'Enter' });
    expect(screen.getByTestId('shortcuts-dialog')).toBeInTheDocument();
    expect(Object.keys(localStorage).every((k) => k.startsWith('cc.pref.'))).toBe(true);
  });
});

describe('shell QA-S1..S8', () => {
  it('lists a new chat with a short title at once, has an h1, announces completion, and Up edits the last question', async () => {
    cleanup();
    render(<App />);
    const box = screen.getByRole('textbox', { name: 'Message' });
    fireEvent.change(box, { target: { value: 'What was our denial rate by payer last quarter?' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    // sidebar shows the chat while the first answer is still streaming
    await waitFor(() => expect(screen.getAllByText('Denial rate by payer').length).toBeGreaterThan(0));
    expect(screen.getByRole('log', { name: 'Messages' }).getAttribute('aria-live')).toBe('polite');
    expect(screen.getByRole('heading', { level: 1, name: 'Denial rate by payer' })).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId('status-live').textContent).toBe('Answer complete'), { timeout: 60000 });
    const follow = screen.getByRole('textbox', { name: 'Message' });
    fireEvent.keyDown(follow, { key: 'ArrowUp' });
    await waitFor(() => expect(screen.getByTestId('msg-user-editing')).toBeTruthy());
    // header title menu renames the open chat everywhere (Claude-style)
    fireEvent.click(screen.getByRole('button', { name: /^Chat options: / }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Rename' }));
    const ren = await screen.findByRole('textbox', { name: 'Rename chat' });
    fireEvent.change(ren, { target: { value: 'Payer denials Q3' } });
    fireEvent.keyDown(ren, { key: 'Enter' });
    await waitFor(() => expect(screen.getByRole('heading', { level: 1, name: 'Payer denials Q3' })).toBeTruthy());
    expect(screen.getByRole('button', { name: 'Chat options: Payer denials Q3' })).toBeTruthy();
  }, 120000);
});
