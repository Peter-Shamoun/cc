import { describe, expect, it } from 'vitest';
import { csvFilename } from './csv';
import vectors from './csvFilename.vectors.json';

// Shared with api/tests/test_csv_filename.py so API downloads and client exports get the same names.
describe('csvFilename shared vectors', () => {
  it.each(vectors)('$title -> $name', ({ title, date, name }) => {
    expect(csvFilename(title ?? undefined, new Date(date))).toBe(name);
  });
});
