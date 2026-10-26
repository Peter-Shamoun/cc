import type { ID, Message } from '../lib/contract';
import type { SearchHit, TranscriptStore } from './types';

/** `ephemeral` transcript store: memory only, gone on reload. Nothing touches browser storage. */
export class MemoryTranscriptStore implements TranscriptStore {
  readonly mode = 'ephemeral' as const;
  private convs = new Map<ID, { id: ID; title: string; updatedAt: string; pinned?: boolean; messages: Message[] }>();
  async listConversations() {
    const items = [...this.convs.values()]
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
      .map(({ id, title, updatedAt, pinned }) => ({ id, title, updatedAt, pinned: !!pinned }));
    return { items };
  }
  async getConversation(id: ID) {
    const c = this.convs.get(id);
    return c ? { id: c.id, messages: structuredClone(c.messages) } : undefined;
  }
  async appendMessage(conversationId: ID, msg: Message, title?: string) {
    const c = this.convs.get(conversationId) ?? { id: conversationId, title: title ?? 'New chat', updatedAt: '', messages: [] };
    c.messages.push(structuredClone(msg));
    c.updatedAt = new Date().toISOString();
    this.convs.set(conversationId, c);
  }
  async truncateAfter(conversationId: ID, messageId: ID) {
    const c = this.convs.get(conversationId);
    if (!c) return;
    const i = c.messages.findIndex((m) => m.id === messageId);
    if (i >= 0) c.messages = c.messages.slice(0, i + 1);
  }
  async deleteConversation(id: ID) {
    this.convs.delete(id);
  }
  async renameConversation(id: ID, title: string) {
    const c = this.convs.get(id);
    if (c && title.trim()) c.title = title.trim().slice(0, 120);
  }
  async replaceMessages(conversationId: ID, msgs: Message[]) {
    const c = this.convs.get(conversationId);
    if (c) c.messages = structuredClone(msgs);
  }
  async setRetention() {}
  async setPinned(id: ID, pinned: boolean) {
    const c = this.convs.get(id);
    if (c) c.pinned = pinned;
  }
  async search(query: string, limit = 30): Promise<SearchHit[]> {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    const hits: SearchHit[] = [];
    const convs = [...this.convs.values()].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    for (const c of convs) {
      const t = c.title.toLowerCase().indexOf(q);
      if (t >= 0) { hits.push({ id: c.id, title: c.title, updatedAt: c.updatedAt, snippet: c.title, matchStart: t, matchLength: q.length }); continue; }
      for (const m of c.messages) {
        const text = messageText(m);
        const i = text.toLowerCase().indexOf(q);
        if (i < 0) continue;
        const from = Math.max(0, i - 40);
        const lead = from > 0 ? '…' : '';
        const snippet = lead + text.slice(from, i + q.length + 80).replace(/\s+/g, ' ');
        // offsets are against the collapsed snippet; recompute so highlighting stays exact
        const start = snippet.toLowerCase().indexOf(q);
        hits.push({ id: c.id, title: c.title, updatedAt: c.updatedAt, messageId: m.id, snippet, matchStart: start, matchLength: q.length });
        break;
      }
      if (hits.length >= limit) break;
    }
    return hits;
  }
}

/** Plain text of a message for search: user text, assistant text parts and table/chart titles. */
export function messageText(m: Message): string {
  if (m.role === 'user') return m.text;
  return m.parts
    .map((p) => (p.type === 'text' ? p.text : 'title' in p && typeof p.title === 'string' ? p.title : ''))
    .filter(Boolean)
    .join('\n')
    // search over what the reader sees, not markdown syntax
    .replace(/[*_`#>|]+/g, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1');
}
