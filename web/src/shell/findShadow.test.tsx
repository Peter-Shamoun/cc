import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { createRef } from 'react';
import { ThreadView, plainText, type ThreadActions } from './ThreadView';
import type { ViewMessage } from '../lib/useChat';

const actions: ThreadActions = {
  editAt: () => {}, switchVersion: () => {}, versionOf: () => undefined, openArtifact: () => {},
  regenerate: () => {}, editIntoComposer: () => {}, send: () => {},
};
const user = (i: number): ViewMessage => ({ id: `u${i}`, role: 'user', text: `Synthetic question number ${i} about **denials**`, createdAt: '2026-01-01T00:00:00Z' });

describe('find shadows for virtualized turns (QA-S13)', () => {
  it('turns outside the virtual window stay findable as aria-hidden plain text', async () => {
    const rect = HTMLElement.prototype.getBoundingClientRect;
    HTMLElement.prototype.getBoundingClientRect = () => ({ x: 0, y: 0, top: 0, left: 0, right: 720, bottom: 800, width: 720, height: 800, toJSON: () => ({}) }) as DOMRect;
    const oh = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight');
    const ow = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth');
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => 800 });
    Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 720 });
    const scrollRef = createRef<HTMLDivElement>();
    const msgs = Array.from({ length: 60 }, (_, i) => user(i));
    const { container } = render(<div ref={scrollRef} style={{ height: 800, overflow: 'auto' }}><ThreadView messages={msgs} busy={false} actions={actions} scrollRef={scrollRef} /></div>);
    const full = container.querySelectorAll('[data-message-id]');
    const shadows = container.querySelectorAll('[data-find-shadow]');
    // jsdom has no layout, so the window may be empty here; the e2e covers the real window.
    expect(shadows.length).toBeGreaterThan(0);
    // Every turn is present exactly once: full row or shadow.
    expect(full.length + shadows.length).toBe(60);
    const last = container.querySelector('[data-find-shadow="u59"]');
    expect(last?.textContent).toBe('Synthetic question number 59 about denials');
    expect(last?.getAttribute('aria-hidden')).toBe('true');
    expect(last?.querySelector('button, a, [tabindex]')).toBeNull();
    HTMLElement.prototype.getBoundingClientRect = rect;
    if (oh) Object.defineProperty(HTMLElement.prototype, 'offsetHeight', oh);
    if (ow) Object.defineProperty(HTMLElement.prototype, 'offsetWidth', ow);
  });

  it('short threads render no shadows', () => {
    const scrollRef = createRef<HTMLDivElement>();
    const { container } = render(<div ref={scrollRef}><ThreadView messages={[user(1), user(2)]} busy={false} actions={actions} scrollRef={scrollRef} /></div>);
    expect(container.querySelectorAll('[data-find-shadow]')).toHaveLength(0);
  });

  it('plainText strips markdown syntax', () => {
    expect(plainText({ id: 'x', role: 'user', text: '# Title\n- **Aetna** 8.2% `code` [link](http://x.test)', createdAt: '' } as ViewMessage))
      .toBe('Title\nAetna 8.2% code link');
  });
});
