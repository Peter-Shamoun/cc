import type { Column, TablePart } from './contract';

export const MASK = '••••';

/** Is this column's data visible in the current session? (v0: PHI is never allowed client-side.) */
export function visible(c: Column, allowPhi = false) {
  return !c.phi || allowPhi;
}

/**
 * Spreadsheet formula injection (OWASP "CSV injection"): a text cell starting with = + - @ or a tab/CR runs as a
 * formula in Excel/Sheets (e.g. =HYPERLINK to exfiltrate neighbouring cells). Prefix such text with ' so it stays text.
 * Only strings are touched; numbers (including negatives) keep their numeric value.
 */
export function defuse(v: unknown): unknown {
  return typeof v === 'string' && /^[=+\-@\t\r]/.test(v) ? `'${v}` : v;
}

function esc(v: unknown): string {
  if (v === null || v === undefined) return '';
  const s = String(defuse(v));
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Tabs and newlines would shift columns when pasted into a spreadsheet. */
function tsvField(v: unknown): string {
  if (v === null || v === undefined) return '';
  return String(defuse(v)).replace(/[\t\r\n]+/g, ' ');
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

const NUMERIC_STRING = /^-?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i;

export function formatCell(v: unknown, c: Column, allowPhi = false): string {
  if (c.phi && !allowPhi) {
    const s = String(v ?? '');
    return `${MASK} ${s.slice(-2)}`.trimEnd();
  }
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  // Warehouses often send DECIMAL/BIGINT as strings ("1234.50", "9007199254740993"): format them like numbers.
  if (typeof v === 'string' && isNumeric(c) && NUMERIC_STRING.test(v.trim())) {
    const n = Number(v.trim());
    // Beyond 2^53 a float would change the digits; group the exact string instead.
    if (c.type === 'integer' && !Number.isSafeInteger(n) && /^-?\d+$/.test(v.trim())) return v.trim().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    v = n;
  }
  if (typeof v === 'bigint') return v.toLocaleString('en-US');
  if (Array.isArray(v)) return v.map((x) => (x === null || x === undefined ? '—' : typeof x === 'object' ? JSON.stringify(x) : String(x))).join(', ');
  if (typeof v === 'object') { try { const j = JSON.stringify(v); return j.length > 200 ? `${j.slice(0, 199)}…` : j; } catch { return '—'; } }
  if (typeof v !== 'number') return String(v);
  if (!Number.isFinite(v)) return '—';
  if (Object.is(v, -0)) v = 0;
  const num = v as number;
  switch (c.type) {
    case 'percent': {
      const pct = num * 100;
      // A tiny non-zero share must not read "0.0%".
      if (pct !== 0 && Math.abs(pct) < 0.05) return `${pct < 0 ? '>-' : '<'}0.1%`;
      return `${pct.toFixed(1).replace(/^-0\.0$/, '0.0')}%`;
    }
    case 'currency':
      return (Math.abs(num) < 0.005 ? 0 : num).toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
    case 'integer':
      return (Math.round(num) || 0).toLocaleString('en-US');
    case 'number':
      // Very small magnitudes would round to "0": keep 3 significant digits instead.
      if (num !== 0 && Math.abs(num) < 0.0005) return num.toPrecision(3);
      return num.toLocaleString('en-US', { maximumFractionDigits: 3 });
    default:
      return String(num);
  }
}

/** Spec §4.2 filename: cc_<slug>_<yyyy-mm-dd>.csv (no PHI in names). */
export function csvFilename(title: string | undefined, d = new Date()) {
  const slug = (title ?? 'table').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 48) || 'table';
  return `cc_${slug}_${d.toISOString().slice(0, 10)}.csv`;
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
