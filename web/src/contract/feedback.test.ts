import { describe, expect, it } from 'vitest';
import { DEMO_CAPABILITIES, feedbackReasonsFor } from './capabilities';
import { feedbackBody, feedbackUrl, sendFeedback } from './feedback';

describe('answer feedback (1.7.0)', () => {
  it('builds the path with encoded ids', () => {
    expect(feedbackUrl('c_1', 'm 2', '/x')).toBe('/x/api/conversations/c_1/messages/m%202/feedback');
  });
  it('normalises the body', () => {
    expect(feedbackBody({ rating: 'down', reasons: ['other', 'other'], comment: '  ' })).toEqual({ rating: 'down', reasons: ['other'] });
    expect(() => feedbackBody({ rating: 'up', comment: 'x'.repeat(1001) })).toThrow(RangeError);
  });
  it('has reason chips per rating in the demo capabilities', () => {
    expect(feedbackReasonsFor(DEMO_CAPABILITIES, 'up').map((r) => r.id)).toContain('correct');
    expect(feedbackReasonsFor(DEMO_CAPABILITIES, 'down').every((r) => r.rating === 'down')).toBe(true);
    expect(feedbackReasonsFor({ ...DEMO_CAPABILITIES, feedbackReasons: undefined }, 'down')).toEqual([]);
  });
  it('PUTs and DELETEs', async () => {
    const calls: [string, RequestInit | undefined][] = [];
    const fake = (async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      return new Response(JSON.stringify({ messageId: 'm_1', rating: 'up', reasons: [], updatedAt: 'z' }), { status: 200 });
    }) as unknown as typeof fetch;
    const out = await sendFeedback('c_1', 'm_1', { rating: 'up' }, { fetchImpl: fake, init: { headers: { Authorization: 'Bearer t' } } });
    expect(out?.rating).toBe('up');
    expect(calls[0][1]?.method).toBe('PUT');
    const sent = (calls[0][1]?.headers ?? {}) as Record<string, string>;
    expect(sent.Authorization).toBe('Bearer t');
    expect(await sendFeedback('c_1', 'm_1', null, { fetchImpl: fake })).toBeNull();
    expect(calls[1][1]?.method).toBe('DELETE');
    const bad = (async () => new Response('{}', { status: 404 })) as unknown as typeof fetch;
    await expect(sendFeedback('c_1', 'm_9', { rating: 'down' }, { fetchImpl: bad })).rejects.toThrow('404');
  });
});
