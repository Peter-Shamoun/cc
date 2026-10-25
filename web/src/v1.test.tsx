import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import App from './App';
import { HttpAnalystAdapter } from './adapters/http';
import { MemoryTranscriptStore } from './adapters/memoryStore';
import { Markdown } from './components/parts/Markdown';
import { DataTable } from './components/parts/DataTable';
import type { StreamEvent, TablePart } from './lib/contract';

vi.mock('./adapters/mock', async (orig) => {
  const m = await orig<typeof import('./adapters/mock')>();
  return { ...m, MockAnalystAdapter: class extends m.MockAnalystAdapter { constructor() { super(0); } } };
});
vi.mock('vega-embed', () => ({ default: async () => ({ view: { finalize() {}, toImageURL: async () => '' } }) }));
vi.mock('shiki', () => ({ createHighlighter: async () => ({ codeToHtml: (c: string) => `<pre>${c}</pre>` }) }));

const sse = (evs: object[]) =>
  new Response(new ReadableStream({ start(c) { for (const e of evs) c.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(e)}\n\n`)); c.close(); } }), { status: 200 });

describe('HttpAnalystAdapter (api/ contract)', () => {
  it('posts user turns only with the demo bearer, skips heartbeats, downloads by opaque id', async () => {
    const calls: [string, RequestInit][] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      calls.push([url, init]);
      if (url.includes('/api/download/')) return new Response('a,b\n1,2', { status: 200 });
      return sse([{ type: 'message.start', messageId: 'm1', conversationId: 'c1' }, { type: 'heartbeat' }, { type: 'message.end', messageId: 'm1', finishReason: 'stop' }]);
    }));
    const a = new HttpAnalystAdapter('', 'demo:acme_labs:peter');
    const out: StreamEvent[] = [];
    for await (const e of a.streamTurn({ conversationId: 'c1', messages: [{ id: 'u1', role: 'user', text: 'q' }, { id: 'a1', role: 'assistant', text: 'x' }], signal: new AbortController().signal })) out.push(e);
    expect(out.map((e) => e.type)).toEqual(['message.start', 'message.end']);
    expect(JSON.parse(calls[0][1].body as string).messages).toEqual([{ id: 'u1', role: 'user', text: 'q' }]);
    expect((calls[0][1].headers as Record<string, string>).authorization).toBe('Bearer demo:acme_labs:peter');
    await a.fetchBlob('f_1');
    expect(calls[1][0]).toBe('/api/download/f_1');
    vi.unstubAllGlobals();
  });
});

describe('MemoryTranscriptStore v1', () => {
  it('renames and replaces threads', async () => {
    const s = new MemoryTranscriptStore();
    await s.appendMessage('c', { id: 'u', role: 'user', text: 'hi', createdAt: '' }, 'hi');
    await s.renameConversation('c', '  Denials Q3 ');
    expect((await s.listConversations()).items[0].title).toBe('Denials Q3');
    await s.replaceMessages('c', []);
    expect((await s.getConversation('c'))?.messages).toEqual([]);
  });
});

describe('Streamdown markdown', () => {
  it('repairs unfinished markdown while streaming', () => {
    const { container } = render(<Markdown text={'Denials rose **sharply'} streaming />);
    expect(container.querySelector('[data-streamdown="strong"]')?.textContent).toBe('sharply');
    expect(container.textContent).not.toContain('**');
  });
});

describe('DataTable virtualization', () => {
  it('renders a window of rows for large tables', () => {
    const rows = Array.from({ length: 5000 }, (_, i) => [`P${i}`, i]);
    const part: TablePart = { type: 'table', id: 't', title: 'Big', columns: [{ key: 'p', label: 'Payer', type: 'string' }, { key: 'n', label: 'N', type: 'integer' }], rows, rowCount: 5000 };
    const { container } = render(<DataTable part={part} full />);
    expect(container.querySelectorAll('tbody tr').length).toBeLessThan(200);
  });
});

describe('Edit and branch', () => {
  it('edits a sent message into a second version and switches back', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
    render(<App />);
    const box = screen.getByRole('textbox', { name: 'Message' });
    fireEvent.change(box, { target: { value: 'Which CARC codes drive Cascade Medicaid denials?' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(screen.getByTestId('msg-actions')).toBeInTheDocument(), { timeout: 5000 });
    fireEvent.click(screen.getByRole('button', { name: 'Edit message' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Edit message' }), { target: { value: 'Monthly paid trend' } });
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Send' })));
    await waitFor(() => expect(screen.getByTestId('version-nav')).toHaveTextContent('2/2'), { timeout: 5000 });
    await waitFor(() => expect(screen.getByTestId('msg-actions')).toBeInTheDocument(), { timeout: 5000 });
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Previous version' })));
    expect(screen.getByTestId('version-nav')).toHaveTextContent('1/2');
    expect(screen.getByTestId('msg-user')).toHaveTextContent('Which CARC codes');
    // thumbs-down opens the reason panel
    fireEvent.click(screen.getByRole('button', { name: 'Bad response' }));
    fireEvent.click(screen.getByRole('button', { name: 'Wrong numbers' }));
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    expect(screen.queryByTestId('feedback-panel')).toBeNull();
    expect(screen.getByText('Thanks for the feedback')).toBeInTheDocument();
    // search filters the sidebar by title
    fireEvent.click(screen.getByRole('button', { name: /Search chats/ }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Search chats' }), { target: { value: 'zzz' } });
    expect(screen.getByText(/No chats match/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Clear search' }));
    // Ctrl+K reopens search, Ctrl+Shift+O starts a new chat
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    expect(screen.getByRole('textbox', { name: 'Search chats' })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'O', ctrlKey: true, shiftKey: true });
    await waitFor(() => expect(screen.getByText('What should we look into?')).toBeInTheDocument());
  });
});
