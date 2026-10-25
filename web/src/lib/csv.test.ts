import { describe, expect, it } from 'vitest';
import { csvFilename, formatCell, tableToCsv, tableToTsv } from './csv';
import type { Column } from './contract';

const cols: Column[] = [
  { key: 'payer', label: 'Payer', type: 'string' },
  { key: 'member', label: 'Member', type: 'string', phi: true },
  { key: 'rate', label: 'Rate', type: 'percent' },
];
const rows = [['A, "Inc"', 'ZZ123456', 0.125]];

describe('csv and formatting', () => {
  it('escapes CSV per RFC 4180 and drops PHI columns', () => {
    const csv = tableToCsv({ columns: cols, rows });
    expect(csv).toBe('Payer,Rate\r\n"A, ""Inc""",0.125');
    expect(csv).not.toContain('ZZ123456');
  });
  it('TSV copy drops PHI columns too', () => {
    expect(tableToTsv({ columns: cols, rows })).not.toContain('ZZ123456');
  });
  it('masks PHI cells and formats numbers', () => {
    expect(formatCell('ZZ123456', cols[1])).toBe('•••• 56');
    expect(formatCell(0.125, cols[2])).toBe('12.5%');
    expect(formatCell(1234.5, { key: 'x', label: 'x', type: 'currency' })).toBe('$1,234.50');
    expect(formatCell(null, cols[0])).toBe('—');
  });
  it('builds PHI-free filenames', () => {
    expect(csvFilename('Denial rate by payer, Q3!', new Date('2026-10-25T12:00:00Z'))).toBe('cc_denial_rate_by_payer_q3_2026-10-25.csv');
  });
});
