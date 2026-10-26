import { describe, expect, it } from 'vitest';
import { formatCell } from './csv';
import { fmtMs, fmtTokens, fmtUsd, fmtUsdShort } from '../components/answer/normalize';
import type { Column } from './contract';

const col = (type: Column['type']): Column => ({ key: 'k', label: 'K', type });

describe('formatCell edge cases', () => {
  it('numeric strings from DECIMAL/BIGINT columns format like numbers', () => {
    expect(formatCell('1234.5', col('currency'))).toBe('$1,234.50');
    expect(formatCell('0.123', col('percent'))).toBe('12.3%');
    expect(formatCell('1234567', col('integer'))).toBe('1,234,567');
    expect(formatCell('9007199254740993', col('integer'))).toBe('9,007,199,254,740,993');
    expect(formatCell('-12', col('integer'))).toBe('-12');
    expect(formatCell('n/a', col('number'))).toBe('n/a');
    expect(formatCell('1234.5', col('string'))).toBe('1234.5');
  });
  it('NaN, Infinity, -0 and rounding to zero never show odd signs', () => {
    expect(formatCell(Number.NaN, col('number'))).toBe('—');
    expect(formatCell(Number.POSITIVE_INFINITY, col('currency'))).toBe('—');
    expect(formatCell(-0, col('integer'))).toBe('0');
    expect(formatCell(-0.001, col('currency'))).toBe('$0.00');
    expect(formatCell(-0.4, col('integer'))).toBe('0');
    expect(formatCell(-0.0000001, col('percent'))).toBe('>-0.1%');
  });
  it('tiny and huge magnitudes stay readable', () => {
    expect(formatCell(0.0001, col('percent'))).toBe('<0.1%');
    expect(formatCell(0, col('percent'))).toBe('0.0%');
    expect(formatCell(0.00001234, col('number'))).toBe('0.0000123');
    expect(formatCell(1e15, col('currency'))).toBe('$1,000,000,000,000,000.00');
    expect(formatCell(-1234.5, col('currency'))).toBe('-$1,234.50');
    expect(formatCell(BigInt(12345), col('integer'))).toBe('12,345');
  });
  it('objects and arrays never print [object Object]', () => {
    expect(formatCell({ a: 1 }, col('string'))).toBe('{"a":1}');
    expect(formatCell([1, null, 'x'], col('string'))).toBe('1, —, x');
    expect(formatCell({ s: 'x'.repeat(500) }, col('string'))).toHaveLength(200);
    const cyc: Record<string, unknown> = {}; cyc.self = cyc;
    expect(formatCell(cyc, col('string'))).toBe('—');
  });
});

describe('usage formatters edge cases', () => {
  it('fmtMs never shows NaN, negatives, 60s or 1m 60s', () => {
    expect(fmtMs(Number.NaN)).toBe('0ms');
    expect(fmtMs(-20)).toBe('0ms');
    expect(fmtMs(999.7)).toBe('1s');
    expect(fmtMs(9_970)).toBe('10s');
    expect(fmtMs(59_600)).toBe('1m 0s');
    expect(fmtMs(119_700)).toBe('2m 0s');
    expect(fmtMs(1_234)).toBe('1.2s');
    expect(fmtMs(3_725_000)).toBe('1h 2m');
  });
  it('cost and token formatters guard bad input', () => {
    expect(fmtUsdShort(Number.NaN)).toBe('$0');
    expect(fmtUsd(-1)).toBe('$0');
    expect(fmtTokens(Number.NaN)).toBe('0');
    expect(fmtTokens(1_250_000)).toBe('1.3M');
    expect(fmtTokens(999_999)).toBe('1M');
    expect(fmtTokens(12_345)).toBe('12k');
  });
});
