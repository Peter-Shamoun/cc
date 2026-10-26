/** QA-A31: Sources only on turns that consulted data (Claude/ChatGPT). */
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { AssistantTurn } from '../Message';
import { consulted } from './Sources';
import { newAssistantViewV1, type AssistantViewV1 } from '../../api/contract';

afterEach(cleanup);
const sources = [{ id: 's1', kind: 'view', name: 'sem.claims', label: 'sem.claims', role: 'answer' }];
const mk = (over: Partial<AssistantViewV1>) => ({ ...newAssistantViewV1('a1'), streaming: false, finishReason: 'stop', sources, ...over }) as unknown as AssistantViewV1;
const noop = () => {};

describe('Sources only when something was consulted (QA-A31)', () => {
  it('consulted(): tables/charts or a returned query count; a clarify, a failed or cancelled query does not', () => {
    expect(consulted(undefined, [{ type: 'text' }])).toBe(false);
    expect(consulted([], [{ type: 'table' }])).toBe(true);
    expect(consulted([{ kind: 'sql', state: 'ok' }], [{ type: 'text' }])).toBe(true);
    expect(consulted([{ kind: 'sql', state: 'error' }], [{ type: 'text' }])).toBe(false);
    expect(consulted([{ kind: 'escalation', state: 'ok' }], [{ type: 'text' }])).toBe(false);
    expect(consulted([{ kind: 'explore', state: 'ok' }], [{ type: 'clarify' }])).toBe(false); // a schema look before a clarify
  });
  it('a clarifying question with server sources shows no Sources chip', () => {
    render(<AssistantTurn msg={mk({ parts: [{ type: 'clarify', id: 'c', question: 'Which period?', options: [], allowFreeText: true } as never] })} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    expect(screen.queryByTestId('sources-chip')).toBeNull();
  });
  it('an answer whose query ran shows the Sources chip', () => {
    render(<AssistantTurn msg={mk({ parts: [{ type: 'text', id: 't', text: 'Denial rate is 12%.' } as never], steps: [{ id: 'q', kind: 'sql', state: 'ok', label: 'Query', events: [] } as never] })} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    expect(screen.getByTestId('sources-chip')).toBeTruthy();
  });
});
