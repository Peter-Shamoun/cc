import type { Column, TablePart } from './contract';

export const MASK = '••••';

/** Is this column's data visible in the current session? (v0: PHI is never allowed client-side.) */
export function visible(c: Column, allowPhi = false) {
  return !c.phi || allowPhi;
}

function esc(v: unknown): string {
  if (v === null || v === undefined) return '';
  const s = String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Tabs and newlines would shift columns when pasted into a spreadsheet. */
function tsvField(v: unknown): string {
  if (v === null || v === undefined) return '';
  return String(v).replace(/[\t\r\n]+/g, ' ');
}

/** RFC 4180 CSV of loaded rows with raw values. PHI columns are dropped unless allowed (§6.6). */
export function tableToCsv(t: Pick<TablePart, 'columns' | 'rows'>, allowPhi = false): string {
  const idx = t.columns.map((c, i) => [c, i] as const).filter(([c]) => visible(c, allowPhi));
  const head = idx.map(([c]) => esc(c.label)).join(',');
  const body = t.rows.map((r) => idx.map(([, i]) => esc(r[i])).join(','));
  return [head, ...body].join('\r\n');
}

/** Tab-separated for pasting into Excel or Sheets. PHI columns are dropped unless allowed. */
export function tableToTsv(t: Pick<TablePart, 'columns' | 'rows'>, allowPhi = false): string {
  const idx = t.columns.map((c, i) => [c, i] as const).filter(([c]) => visible(c, allowPhi));
  const head = idx.map(([c]) => tsvField(c.label)).join('\t');
  const body = t.rows.map((r) => idx.map(([, i]) => tsvField(r[i])).join('\t'));
  return [head, ...body].join('\n');
}

/**
 * RFC 4180 CSV for a list of records (chart "Download data"). Columns are the
 * union of keys in first-seen order, not just the first row. Nulls are empty.
 */
export function recordsToCsv(rows: readonly Record<string, unknown>[]): string {
  const keys: string[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    if (!row || typeof row !== 'object') continue;
    for (const key of Object.keys(row)) {
      if (!seen.has(key)) {
        seen.add(key);
        keys.push(key);
      }
    }
  }
  if (keys.length === 0) return '';
  const head = keys.map((key) => esc(key)).join(',');
  const body = rows.map((row) => keys.map((key) => esc(row && typeof row === 'object' ? row[key] : '')).join(','));
  return [head, ...body].join('\r\n');
}

export function isNumeric(c: Column) {
  return c.type === 'integer' || c.type === 'number' || c.type === 'currency' || c.type === 'percent';
}

export function formatCell(v: unknown, c: Column, allowPhi = false): string {
  if (c.phi && !allowPhi) {
    const s = String(v ?? '');
    return `${MASK} ${s.slice(-2)}`.trimEnd();
  }
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v !== 'number') return String(v);
  if (!Number.isFinite(v)) return '—';
  switch (c.type) {
    case 'percent':
      return `${(v * 100).toFixed(1)}%`;
    case 'currency':
      return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
    case 'integer':
      return Math.round(v).toLocaleString('en-US');
    case 'number':
      return v.toLocaleString('en-US', { maximumFractionDigits: 3 });
    default:
      return String(v);
  }
}

/** Spec §4.2 filename: claimclarity_<slug>_<yyyy-mm-dd>.csv (no PHI in names). */
export function csvFilename(title: string | undefined, d = new Date()) {
  const slug = (title ?? 'table').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 48) || 'table';
  return `claimclarity_${slug}_${d.toISOString().slice(0, 10)}.csv`;
}

/** Download via a short-lived blob: URL; data never appears in a URL path or query. */
export function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export function downloadText(filename: string, content: string, mime = 'text/csv') {
  downloadBlob(filename, new Blob([content], { type: `${mime};charset=utf-8` }));
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
