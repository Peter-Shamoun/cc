import { afterEach, describe, expect, it, vi } from 'vitest';
import { clearFeedbackForTests, feedbackReasons, recordFeedback, setFeedbackCapabilities, setFeedbackSink, storedVote, toFeedbackRequest } from './feedback';
import { HttpAnalystAdapter } from '../adapters/http';
import { DEMO_CAPABILITIES } from '../contract/capabilities';

afterEach(() => { clearFeedbackForTests(); vi.unstubAllGlobals(); });

describe('answer feedback → contract 1.7.0', () => {
  it('maps votes to FeedbackRequest (null clears), trimming the comment', () => {
    expect(toFeedbackRequest({ messageId: 'm', vote: null, at: 0 })).toBeNull();
    expect(toFeedbackRequest({ messageId: 'm', vote: 'up', at: 0 })).toEqual({ rating: 'up', reasons: [] });
    expect(toFeedbackRequest({ messageId: 'm', vote: 'down', reasons: ['wrong_numbers'], detail: '  off by 10  ', at: 0 }))
      .toEqual({ rating: 'down', reasons: ['wrong_numbers'], comment: 'off by 10' });
    expect(toFeedbackRequest({ messageId: 'm', vote: 'down', detail: '   ', at: 0 })).toEqual({ rating: 'down', reasons: [] });
  });
  it('reads stored feedback (1.7.0 object or legacy string)', () => {
    expect(storedVote({ messageId: 'm', rating: 'down', reasons: [], updatedAt: 'x' })).toBe('down');
    expect(storedVote('up')).toBe('up');
    expect(storedVote(undefined)).toBeNull();
    expect(storedVote({ rating: 'meh' })).toBeNull();
  });
  it('reason chips follow the server capabilities, demo list until loaded', () => {
    expect(feedbackReasons('down').map((r) => r.id)).toContain('wrong_numbers');
    setFeedbackCapabilities({ ...DEMO_CAPABILITIES, feedbackReasons: [{ id: 'x', label: 'X', rating: 'down' }] });
    expect(feedbackReasons('down')).toEqual([{ id: 'x', label: 'X', rating: 'down' }]);
    expect(feedbackReasons('up')).toEqual([]);
  });
  it('the http adapter PUTs the body and DELETEs on clear, ids URL-encoded, comment only in the body', async () => {
    const calls: [string, RequestInit][] = [];
    vi.stubGlobal('fetch', vi.fn(async (u: string, init: RequestInit) => {
      calls.push([u, init]);
      return new Response(init.method === 'PUT' ? JSON.stringify({ messageId: 'm/1', rating: 'down', reasons: ['wrong_numbers'], updatedAt: 'now' }) : '{"cleared":true}', { status: 200 });
    }));
    const a = new HttpAnalystAdapter('');
    const sink = vi.fn((f) => a.feedback('c1', f.messageId, toFeedbackRequest(f)));
    setFeedbackSink(sink);
    recordFeedback({ messageId: 'm/1', vote: 'down', reasons: ['wrong_numbers'], detail: 'synthetic note' });
    recordFeedback({ messageId: 'm/1', vote: null });
    await vi.waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[0][0]).toBe('/api/conversations/c1/messages/m%2F1/feedback');
    expect(calls[0][1].method).toBe('PUT');
    expect(JSON.parse(String(calls[0][1].body))).toEqual({ rating: 'down', reasons: ['wrong_numbers'], comment: 'synthetic note' });
    expect(calls[0][0]).not.toContain('synthetic');
    expect(calls[1][1].method).toBe('DELETE');
  });
});
