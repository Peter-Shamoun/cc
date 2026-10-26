import type { Column, TablePart } from '../../lib/contract';

export interface TableFixture {
  id: string;
  part: TablePart;
  /** Inline (max 400px) unless the expanded view is under test. */
  full?: boolean;
  virtual?: boolean;
  phi?: boolean;
}

const stringCol = (key: string, label: string, extra?: Partial<Column>): Column => ({ key, label, type: 'string', ...extra });

export function wideTable(): TablePart {
  const columns: Column[] = Array.from({ length: 50 }, (_, i) => {
    const type = (['currency', 'percent', 'integer', 'number', 'string'] as const)[i % 5];
    return { key: `c${i}`, label: `Col ${i}`, type };
  });
  const row = columns.map((_, i) => {
    if (i % 5 === 4) return i === 4 ? null : `s${i}`;
    if (i % 5 === 1) return 0.125;
    if (i % 5 === 0) return 1234.5;
    return i * 10;
  });
  return { type: 'table', id: 'wide-50', title: 'Fifty columns', columns, rows: [row, row], rowCount: 2 };
}

export function tallTable(): TablePart {
  const columns: Column[] = [
    { key: 'payer', label: 'Payer', type: 'string' },
    { key: 'n', label: 'Count', type: 'integer' },
    { key: 'rate', label: 'Rate', type: 'percent' },
  ];
  const rows = Array.from({ length: 10_000 }, (_, i) => [
    i % 17 === 0 ? null : `p${i}`,
    i,
    i % 5 === 0 ? null : i / 10_000,
  ]);
  return { type: 'table', id: 'tall-10k', title: 'Ten thousand rows', columns, rows, rowCount: 10_000 };
}

const mixed: TablePart = {
  type: 'table',
  id: 'mixed-types',
  title: 'Numbers beside labels',
  columns: [
    stringCol('code', 'Code'),
    { key: 'count', label: 'Count', type: 'integer' },
    { key: 'paid', label: 'Paid', type: 'currency' },
    { key: 'rate', label: 'Rate', type: 'percent' },
    { key: 'open', label: 'Open', type: 'boolean' },
    { key: 'note', label: 'Note', type: 'string' },
  ],
  rows: [
    ['10', 1000, 1234.5, 0.125, true, 'alpha'],
    ['abc', null, Number.NaN, null, false, '<img src=x onerror="alert(1)">'],
    ['002', Number.POSITIVE_INFINITY, -2, 1, null, ''],
  ],
  rowCount: 3,
};

const hostile: TablePart = {
  type: 'table',
  id: 'csv-hostile',
  title: 'CSV hostile cells',
  columns: [
    stringCol('note', 'Note'),
    { key: 'paid', label: 'Paid', type: 'currency' },
    { key: 'rate', label: 'Rate', type: 'percent' },
    stringCol('member', 'Member', { phi: true }),
  ],
  rows: [
    ['say "hi",\nplease', 1234.5, 0.5, 'SECRET99'],
    [null, 0, 0, null],
    ['=1+1', -2, 1, 'ZZ'],
    ['a\tb', 3, 0.1, 'tabbed'],
  ],
  rowCount: 4,
};

const phi: TablePart = {
  type: 'table',
  id: 'phi-masked',
  title: 'Member sample',
  columns: [
    stringCol('payer', 'Payer'),
    stringCol('member', 'Member', { phi: true }),
    { key: 'rate', label: 'Rate', type: 'percent' },
  ],
  rows: [['Cascade', 'ZZ123456', 0.183]],
  rowCount: 1,
};

const empty: TablePart = {
  type: 'table',
  id: 'empty-result',
  title: 'Empty query',
  columns: [stringCol('payer', 'Payer'), { key: 'n', label: 'N', type: 'integer' }],
  rows: [],
  rowCount: 0,
};

const duplicateKeys: TablePart = {
  type: 'table',
  id: 'duplicate-keys',
  title: 'Duplicate keys',
  columns: [
    stringCol('a', 'Left'),
    { key: 'a', label: 'Right', type: 'integer' },
  ],
  rows: [['x', 42]],
  rowCount: 1,
};

const sortNulls: TablePart = {
  type: 'table',
  id: 'sort-nulls',
  title: 'Sort nulls',
  columns: [{ key: 'n', label: 'N', type: 'number' }],
  rows: [[null], [2], [Number.NaN], [10], [-1]],
  rowCount: 5,
};

export const tableFixtures: TableFixture[] = [
  { id: 'wide-50', part: wideTable() },
  { id: 'tall-10k', part: tallTable(), virtual: true },
  { id: 'mixed-types', part: mixed },
  { id: 'csv-hostile', part: hostile, phi: true },
  { id: 'phi-masked', part: phi, phi: true },
  { id: 'empty-result', part: empty },
  { id: 'duplicate-keys', part: duplicateKeys },
  { id: 'sort-nulls', part: sortNulls },
];
