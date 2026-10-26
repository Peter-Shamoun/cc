import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { CodeBlock } from './CodeBlock';

afterEach(cleanup);
const LONG = `SELECT ${Array.from({ length: 60 }, (_, i) => `col_${i}`).join(', ')} FROM claims`;

describe('code block chrome (ChatGPT/Claude observed)', () => {
  it('header: language at left, a labelled "Copy code" that says Copied, secondary actions', async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText: write } });
    render(<CodeBlock code="SELECT 1" language="sql" />);
    expect(screen.getByTestId('code-lang')).toHaveTextContent(/^sql$/);
    const copy = screen.getByRole('button', { name: 'Copy code' });
    expect(copy).toHaveTextContent('Copy code');
    fireEvent.click(copy);
    await waitFor(() => expect(copy).toHaveTextContent('Copied'));
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
