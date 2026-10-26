import { describe, expect, it } from 'vitest';
import { redact, safeError } from './redact';
import vectors from './redact.vectors.json';

// Shared with api/tests/test_redact.py: client and API redact identically. Synthetic data only.
describe('redact shared vectors', () => {
  it.each(vectors.cases)('$in', ({ in: input, out }) => {
    expect(redact(input)).toBe(out);
  });
  it('truncates', () => {
    expect(redact('x '.repeat(1000)).length).toBeLessThanOrEqual(301);
  });
});

describe('safeError', () => {
  it('keeps name and file:line:col frames, redacts the message', () => {
    const e = new TypeError('bad "Jane Doe" 123-45-6789');
    e.stack = 'TypeError: bad "Jane Doe" 123-45-6789\n    at render (https://app.example/assets/index-Dfviy12e.js?v=1:12:345)\n    at x (http://localhost:5230/src/components/Composer.tsx:50:2)';
    const s = safeError(e);
    expect(s).toEqual({ name: 'TypeError', message: 'bad [text] [ssn]', frames: ['index-Dfviy12e.js:12:345', 'Composer.tsx:50:2'] });
    expect(JSON.stringify(s)).not.toMatch(/Jane|123-45|example|localhost/);
  });
  it('never stringifies thrown objects', () => {
    expect(safeError({ patient: 'Jane Doe' })).toEqual({ name: 'NonError', message: 'thrown object', frames: [] });
  });
  it('rejects odd names', () => {
    const e = new Error('x');
    e.name = 'Jane Doe <script>';
    expect(safeError(e).name).toBe('Error');
  });
});
