import type { ID, Message } from '../lib/contract';
import type { TranscriptStore } from './types';

/** `ephemeral` transcript store: memory only, gone on reload. Nothing touches browser storage. */
export class MemoryTranscriptStore implements TranscriptStore {
  readonly mode = 'ephemeral' as const;
  private convs = new Map<ID, { id: ID; title: string; updatedAt: string; messages: Message[] }>();
  async listConversations() {
    const items = [...this.convs.values()]
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
      .map(({ id, title, updatedAt }) => ({ id, title, updatedAt }));
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
  async setRetention() {}
}
