import type { Part as PartV1 } from '../../api/contract';

/**
 * Render-side guard for wire parts. The reducers trust the contract, but a server bug, a proxy that mangles
 * JSON or a newer server can still hand us a table with `rows: null`, text that is a number, or a part with
 * no type. Coerce each part to the shape the views read (or drop it) so one odd part never takes down the
 * whole answer, the Copy text, or the artifact panel. Memoized per parts array, so it's free on re-render.
 */
type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown, d = ''): string => (typeof v === 'string' ? v : typeof v === 'number' || typeof v === 'boolean' || typeof v === 'bigint' ? String(v) : d);
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

function table(p: Obj): Obj {
  const rawRows = arr(p.rows);
  let cols = arr(p.columns).filter(isObj).map((c, i) => {
    const key = str(c.key, `c${i + 1}`) || `c${i + 1}`;
    return { ...c, key, label: str(c.label, key) || key, type: str(c.type, 'string') || 'string' };
  });
  if (!cols.length) {
    // Columns missing or not a list: derive them from the rows so the data still shows.
    const width = Math.max(0, ...rawRows.map((r) => (Array.isArray(r) ? r.length : isObj(r) ? Object.keys(r).length : 0)));
    const firstObj = rawRows.find(isObj);
    cols = firstObj
      ? Object.keys(firstObj).map((k) => ({ key: k, label: k, type: 'string' }))
      : Array.from({ length: width }, (_, i) => ({ key: `c${i + 1}`, label: `Column ${i + 1}`, type: 'string' }));
  }
  const rows = rawRows.flatMap((r) => (Array.isArray(r) ? [r] : isObj(r) ? [cols.map((c) => r[c.key] ?? null)] : []));
  const rowCount = typeof p.rowCount === 'number' && Number.isFinite(p.rowCount) && p.rowCount >= rows.length ? p.rowCount : rows.length;
  return { ...p, columns: cols, rows, rowCount };
}

function one(p: unknown, i: number): PartV1 | null {
  if (!isObj(p) || typeof p.type !== 'string' || !p.type) return null;
  const id = str(p.id) || `part-${i}`;
  const q: Obj = { ...p, id };
  switch (p.type) {
    case 'text': case 'reasoning': q.text = str(p.text); break;
    case 'table': return table(q) as unknown as PartV1;
    case 'code': q.code = str(p.code); q.language = str(p.language, 'text') || 'text'; break;
    case 'callout':
      q.variant = str(p.variant, 'note') || 'note';
      q.items = arr(p.items).flatMap((it) => (isObj(it) ? [{ ...it, text: str(it.text) }] : typeof it === 'string' ? [{ text: it }] : []));
      break;
    case 'abstain':
      q.category = str(p.category, 'unsupported');
      q.reason = str(p.reason);
      if (p.details !== undefined) q.details = str(p.details) || undefined;
      q.suggestions = arr(p.suggestions).flatMap((s, k) => (isObj(s) && str(s.text) ? [{ ...s, id: str(s.id) || `s${k}`, text: str(s.text) }] : typeof s === 'string' && s ? [{ id: `s${k}`, text: s }] : []));
      break;
    case 'clarification':
      q.question = str(p.question);
      q.choices = arr(p.choices).flatMap((c, k) => (isObj(c) && str(c.label) ? [{ ...c, id: str(c.id) || `c${k}`, label: str(c.label) }] : []));
      break;
    case 'error':
      q.title = str(p.title, 'Something went wrong') || 'Something went wrong';
      q.message = str(p.message);
      break;
  }
  return q as unknown as PartV1;
}

const memo = new WeakMap<object, PartV1[]>();
export function sanitizeParts(parts: unknown): PartV1[] {
  if (!Array.isArray(parts)) return [];
  const hit = memo.get(parts);
  if (hit) return hit;
  const out = parts.flatMap((p, i) => { const q = one(p, i); return q ? [q] : []; });
  memo.set(parts, out);
  return out;
}
