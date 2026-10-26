import { describe, expect, it } from 'vitest';
import { utcDates } from './chartSpec';
import { fmtUsd, fmtUsdShort } from '../components/answer/normalize';

describe('utcDates: calendar dates never slip a day/month in local time', () => {
  it('prefixes timeUnits with utc (string and object), on channels, tooltips and layers', () => {
    const s = utcDates({
      encoding: {
        x: { field: 'month', type: 'temporal', timeUnit: 'yearmonth' },
        y: { field: 'n', type: 'quantitative' },
        tooltip: [{ field: 'month', type: 'temporal', timeUnit: 'yearmonth' }, { field: 'd', type: 'temporal' }, { field: 'n', type: 'quantitative' }],
      },
      layer: [{ encoding: { x: { field: 'w', type: 'temporal', timeUnit: { unit: 'week', step: 1 } } } }],
    }) as never as { encoding: Record<string, unknown>; layer: { encoding: Record<string, unknown> }[] };
    expect(s.encoding.x).toMatchObject({ timeUnit: 'utcyearmonth' });
    expect(s.encoding.y).toEqual({ field: 'n', type: 'quantitative' });
    expect(s.encoding.tooltip).toEqual([
      { field: 'month', type: 'temporal', timeUnit: 'utcyearmonth' },
      { field: 'd', type: 'temporal', timeUnit: 'utcyearmonthdate' },
      { field: 'n', type: 'quantitative' },
    ]);
    expect(s.layer[0].encoding.x).toMatchObject({ timeUnit: { unit: 'utcweek', step: 1 } });
  });
  it('positional temporal fields without a timeUnit get a utc scale (keeping other scale props); utc units are left alone', () => {
    const s = utcDates({ encoding: { x: { field: 'day', type: 'temporal', scale: { nice: false } }, color: { field: 'm', type: 'temporal', timeUnit: 'utcmonth' } } }) as never as { encoding: Record<string, unknown> };
    expect(s.encoding.x).toEqual({ field: 'day', type: 'temporal', scale: { type: 'utc', nice: false } });
    expect(s.encoding.color).toMatchObject({ timeUnit: 'utcmonth' });
  });
});

describe('footer cost', () => {
  it('rounds to cents with <$0.01 under a cent; the exact figure stays for the popover', () => {
    expect(fmtUsdShort(0.0024)).toBe('<$0.01');
    expect(fmtUsdShort(0.0071)).toBe('<$0.01');
    expect(fmtUsdShort(0.04)).toBe('$0.04');
    expect(fmtUsdShort(1.234)).toBe('$1.23');
    expect(fmtUsdShort(0)).toBe('$0');
    expect(fmtUsd(0.0024)).toBe('$0.0024');
  });
});
