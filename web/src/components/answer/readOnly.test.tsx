import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { AssistantTurn } from '../Message';
import { newAssistantViewV1, reduceV1, type AssistantViewV1, type StreamEvent } from '../../api/contract';

afterEach(cleanup);
const noop = () => {};
const view = (parts: unknown[]) => [
  { type: 'message.start', messageId: 'a1', conversationId: 'c1' },
  ...parts.map((part) => ({ type: 'part', part })),
  { type: 'message.end', messageId: 'a1', finishReason: 'stop', usage: { queries: 1, elapsedMs: 10 } },
].reduce((m, e) => reduceV1(m, e as StreamEvent), newAssistantViewV1('a1') as AssistantViewV1);

const parts = [
  { type: 'text', id: 't1', text: 'Denial rate is 12%.' },
  { type: 'callout', id: 'c1', variant: 'assumption', title: 'Assumptions', items: [{ id: 'a', text: 'Last quarter means Q3.', editable: true }] },
  { type: 'error', id: 'e1', code: 'timeout', title: 'Query timed out', message: 'Try again.', retryable: true },
];

describe('AssistantTurn readOnly (shared view)', () => {
  it('editable view shows actions, Change and Retry', () => {
    render(<AssistantTurn msg={view(parts)} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} onSend={noop} />);
    expect(screen.queryByTestId('msg-actions')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Change' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Retry/ })).toBeTruthy();
  });
  it('readOnly hides the action row, feedback, Change and Retry; content stays', () => {
    render(<AssistantTurn msg={view(parts)} isLast readOnly onOpen={noop} onRegenerate={noop} onEdit={noop} onSend={noop} />);
    expect(screen.getByText('Denial rate is 12%.')).toBeTruthy();
    expect(screen.getByText('Last quarter means Q3.')).toBeTruthy();
    expect(screen.queryByTestId('msg-actions')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Good response' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Change' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull();
  });
});
