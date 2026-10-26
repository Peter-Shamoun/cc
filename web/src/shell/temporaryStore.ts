import type { ID, Message } from '../lib/contract';
import { MemoryTranscriptStore } from '../adapters/memoryStore';
import type { SearchHit } from '../adapters/types';

/**
 * Temporary chat (QA-S22, ChatGPT-style) on the in-memory transcript store: while `temporary` is on, the next
 * new conversation is kept out of the sidebar list and search, and `discardTemporary()` drops it for good
 * (leaving temporary mode, starting another chat). Only offered with this store: the live API records runs
 * server-side, so "not saved" would not be true there until the contract has a flag for it.
 */
export class TemporaryAwareStore extends MemoryTranscriptStore {
  temporary = false;
  private temp = new Set<ID>();
  private seen = new Set<ID>();
  /** Discarded temporary ids: a run that ends after the user left must not write the chat back. */
  private dropped = new Set<ID>();
  isTemporary(id: ID | null | undefined) { return !!id && this.temp.has(id); }
  override async listConversations() {
    const { items } = await super.listConversations();
    return { items: items.filter((t) => !this.temp.has(t.id)) };
  }
  override async appendMessage(conversationId: ID, msg: Message, title?: string) {
    if (this.dropped.has(conversationId)) return;
    if (!this.seen.has(conversationId)) {
      this.seen.add(conversationId);
      if (this.temporary) this.temp.add(conversationId);
    }
    return super.appendMessage(conversationId, msg, title);
  }
  override async search(query: string, limit?: number): Promise<SearchHit[]> {
    const max = limit ?? 30;
    const hits = await super.search(query, max + this.temp.size);
    return hits.filter((h) => !this.temp.has(h.id)).slice(0, max);
  }
  /** Forget every temporary conversation (nothing of it stays in memory). */
  async discardTemporary() {
    const ids = [...this.temp];
    this.temp.clear();
    ids.forEach((id) => this.dropped.add(id));
    await Promise.all(ids.map((id) => super.deleteConversation(id)));
  }
}
