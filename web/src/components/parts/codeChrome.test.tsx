import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { CodeBlock } from './CodeBlock';

afterEach(cleanup);
const LONG = `SELECT ${Array.from({ length: 60 }, (_, i) => `col_${i}`).join(', ')} FROM claims`;

describe('code block chrome (ChatGPT/Claude observed)', () => {
  it('header: language at left, an icon-only "Copy code" that turns into a check (ChatGPT hands-on), secondary actions', async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText: write } });
    render(<CodeBlock code="SELECT 1" language="sql" />);
    expect(screen.getByTestId('code-lang')).toHaveTextContent(/^sql$/);
    const copy = screen.getByRole('button', { name: 'Copy code' });
    // No visible words in the header's copy control: just the icon.
    expect(copy.textContent?.trim()).toBe('');
    expect(copy.querySelector('svg.lucide-copy')).not.toBeNull();
    fireEvent.click(copy);
    await waitFor(() => expect(copy).toHaveAttribute('aria-label', 'Copied'));
    expect(copy.querySelector('svg.lucide-check')).not.toBeNull();
    expect(copy.querySelector('svg.lucide-copy')).toBeNull();
    expect(screen.getByTestId('copy-status')).toHaveTextContent('Copied');
    expect(write).toHaveBeenCalledWith('SELECT 1');
    expect(screen.getByRole('button', { name: 'Wrap lines' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download' })).toBeInTheDocument();
  });
  it('a plain fence shows no "text" label; title joins the language', () => {
    render(<CodeBlock code="hello" language="text" />);
    expect(screen.getByTestId('code-lang').textContent).toBe('');
    expect(screen.getByLabelText('Plain code')).toBeInTheDocument();
    cleanup();
    render(<CodeBlock code="SELECT 1" language="sql" title="query" />);
    expect(screen.getByTestId('code-lang')).toHaveTextContent('sql · query');
  });
  it('long lines scroll inside the block by default; Wrap lines soft-wraps them and toggles back', () => {
    render(<CodeBlock code={LONG} language="sql" />);
    const body = screen.getByLabelText('sql code');
    expect(body.className).toMatch(/overflow-auto/);
    expect(body.dataset.wrap).toBeUndefined();
    fireEvent.click(screen.getByRole('button', { name: 'Wrap lines' }));
    expect(body.dataset.wrap).toBe('true');
    expect(screen.getByRole('button', { name: "Don't wrap lines" })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: "Don't wrap lines" }));
    expect(body.dataset.wrap).toBeUndefined();
    expect(readFileSync('src/index.css', 'utf8')).toMatch(/\.shiki-wrap\[data-wrap\] pre, \.shiki-wrap\[data-wrap\] code \{ white-space: pre-wrap;/);
  });
});
