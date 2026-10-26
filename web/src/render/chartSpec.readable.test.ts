import { describe, expect, it } from 'vitest';
import { readableBars } from './chartSpec';

const bar = (labels: string[]) => ({
  mark: { type: 'bar', tooltip: true },
  encoding: {
    x: { field: 'payer', type: 'nominal', title: 'Payer name', sort: '-y' },
    y: { field: 'rate', type: 'quantitative', title: 'Denial rate', axis: { format: '.1%' } },
  },
  width: 'container',
  height: 280,
  data: { values: labels.map((payer, i) => ({ payer, rate: i / 10 })) },
});

describe('readableBars (QA-A12)', () => {
  it('flips many or long category labels to horizontal bars, keeping sort by value', () => {
    const out = readableBars(bar(['Humana', 'Aetna', 'Medicare', 'Cigna', 'UnitedHealthcare', 'BCBS', 'Medicaid'])) as any;
    expect(out.encoding.y.field).toBe('payer');
    expect(out.encoding.y.sort).toBe('-x');
    expect(out.encoding.y.axis.labelAngle).toBe(0);
    expect(out.encoding.x.field).toBe('rate');
    expect(out.encoding.x.axis.format).toBe('.1%');
    expect(out.height).toBe(7 * 28);
  });

  it('flips a few long labels too', () => {
    const out = readableBars(bar(['UnitedHealthcare', 'Aetna'])) as any;
    expect(out.encoding.y.field).toBe('payer');
  });

  it('keeps few short labels vertical with level labels', () => {
    const out = readableBars(bar(['CO-45', 'CO-97', 'PR-1'])) as any;
    expect(out.encoding.x.field).toBe('payer');
    expect(out.encoding.x.axis.labelAngle).toBe(0);
    expect(out.height).toBe(280);
  });

  it('leaves line charts, layered specs, and horizontal bars alone', () => {
    const line = { ...bar(['a', 'b', 'c', 'd', 'e', 'f']), mark: 'line' };
    expect(readableBars(line)).toBe(line);
    const layered = { ...bar(['Humana', 'Aetna', 'Medicare', 'Cigna', 'UnitedHealthcare', 'BCBS']), layer: [] };
    expect(readableBars(layered)).toBe(layered);
    const b = bar(['UnitedHealthcare']);
    const horiz = { ...b, encoding: { x: b.encoding.y, y: b.encoding.x } };
    expect(readableBars(horiz)).toBe(horiz);
  });
});
