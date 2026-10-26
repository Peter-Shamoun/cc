import { describe, expect, it } from 'vitest';
import { REGENERATE_STYLES, regenerateRequest } from './regenerate';
import type { RegenerateStyle } from './v1.gen';

// every schema style has a menu entry (compile-time exhaustiveness)
const ALL: Record<RegenerateStyle, true> = { default: true, concise: true, detailed: true };

describe('regenerate styles (1.10.0)', () => {
  it('menu styles match the schema enum', () => {
    expect(REGENERATE_STYLES.map((s) => s.style)).toEqual(Object.keys(ALL));
  });
  it('builds a sibling re-run with no user turn', () => {
    expect(regenerateRequest('c_1', 'm_1', 'concise')).toEqual({
      conversationId: 'c_1', messages: [], regenerate: { messageId: 'm_1', style: 'concise' },
    });
  });
  it('omits style for default and carries options', () => {
    expect(regenerateRequest('c_1', 'm_1', 'default', { strategy: 'tool_loop' })).toEqual({
      conversationId: 'c_1', messages: [], regenerate: { messageId: 'm_1' }, options: { strategy: 'tool_loop' },
    });
  });
});
