import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { AssistantTurn } from './Message';
import { newAssistantViewV1 } from '../api/contract';
import { rateLimitLabel } from '../lib/useChat';

afterEach(cleanup);
const noop = () => {};
const turn = (status: string | null) => <AssistantTurn msg={{ ...newAssistantViewV1('a1'), status }} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />;

describe('429 countdown live region', () => {
  it('announces the wait politely and hides the ticking visual label from AT', () => {
    const { rerender } = render(turn(null));
    const live = screen.getByTestId('transport-announcer');
    expect(live.getAttribute('role')).toBe('status');
    expect(live.getAttribute('aria-live')).toBe('polite');
    expect(live.textContent).toBe('');
    rerender(turn(rateLimitLabel(8000)));
    expect(live.textContent).toBe('Busy right now. Retrying automatically in 8 seconds.');
    expect(screen.getByText(rateLimitLabel(8000)).getAttribute('aria-hidden')).toBe('true');
    rerender(turn(rateLimitLabel(7000)));
    expect(live.textContent).toBe('Busy right now. Retrying automatically in 8 seconds.'); // unchanged: no per-tick chatter
    rerender(turn(rateLimitLabel(5000)));
    expect(live.textContent).toBe('Retrying in 5 seconds.');
    rerender(turn('Running query'));
    expect(live.textContent).toBe('');
  });
});
