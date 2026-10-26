import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Markdown } from './Markdown';
import { AssistantTurn } from '../Message';
import { newAssistantView, reduce, type StreamEvent } from '../../lib/contract';

afterEach(cleanup);

describe('streaming polish', () => {
  it('fades new words in while streaming and renders plain text once done', () => {
    const { container, rerender } = render(<Markdown text="Denials rose in **Q3** for Cascade" streaming />);
    expect(container.querySelector('[data-sd-animate]')).not.toBeNull();
    expect(container.querySelector('.streaming-caret')).not.toBeNull();
    rerender(<Markdown text="Denials rose in **Q3** for Cascade." />);
    expect(container.querySelector('[data-sd-animate]')).toBeNull();
    expect(container.textContent).toContain('Denials rose in Q3 for Cascade.');
  });
  it('message actions use quick styled tooltips, not native titles', () => {
    let v = newAssistantView('a1');
    v = reduce(v, { type: 'part', part: { type: 'text', id: 'x', text: 'Hi' } } as StreamEvent);
    v = reduce(v, { type: 'message.end', messageId: 'a1', finishReason: 'stop' } as StreamEvent);
    render(<AssistantTurn msg={v} isLast onOpen={() => {}} onRegenerate={() => {}} onEdit={() => {}} />);
    for (const name of ['Copy', 'Good response', 'Bad response', 'Regenerate']) {
      const b = screen.getByRole('button', { name });
      expect(b).toHaveAttribute('data-tip', name);
      expect(b).not.toHaveAttribute('title');
    }
  });
});
