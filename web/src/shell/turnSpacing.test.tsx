import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { ThreadView, type ThreadActions } from './ThreadView';
import type { ViewMessage } from '../lib/useChat';

afterEach(cleanup);
const actions: ThreadActions = {
  editAt: () => {}, switchVersion: () => {}, versionOf: () => undefined, openArtifact: () => {},
  regenerate: () => {}, editIntoComposer: () => {}, send: () => {},
};
const at = '2026-01-01T00:00:00Z';
const u = (id: string) => ({ id, role: 'user', text: `Synthetic question ${id}`, createdAt: at }) as ViewMessage;
const a = (id: string) => ({ id, role: 'assistant', parts: [{ type: 'text', id: `${id}t`, text: 'Synthetic answer.' }], steps: [], escalations: [], lastSeq: -1, streaming: false, status: null, openText: [], createdAt: at }) as unknown as ViewMessage;

// Answer's 22:54 proposal: q→a stays 24px (gap-6); an answer→next question gets +16px (40px), like Claude/ChatGPT.
describe('turn spacing', () => {
  it('only a question that follows an answer gets the extra exchange gap', () => {
    const { container } = render(<ThreadView messages={[u('u1'), a('a1'), u('u2'), u('u3')]} busy={false} actions={actions} />);
    const row = (id: string) => container.querySelector(`[data-message-id="${id}"]`)!;
    expect(row('u1').className).not.toMatch(/pt-4/);
    expect(row('u2').className).toMatch(/\bpt-4\b/);
    expect(row('u2')).toHaveAttribute('data-after-answer', 'true');
    expect(row('u3').className).not.toMatch(/pt-4/);
    expect(row('a1').className).not.toMatch(/pt-4/);
  });
});
