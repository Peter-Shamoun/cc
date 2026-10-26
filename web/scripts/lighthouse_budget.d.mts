export interface PresetBudget { categories: Record<string, number>; metrics: Record<string, number> }
export interface LhReport { runtimeError?: { code: string; message?: string }; finalDisplayedUrl?: string; categories: Record<string, { score: number | null }>; audits: Record<string, { numericValue?: number; details?: { items?: Record<string, unknown>[] } }> }
export interface Row { preset: string; key: string; value: number | null; limit: number; ok: boolean; kind: 'category' | 'metric' }
export function median(xs: number[]): number;
export function loadByteWeight(report: LhReport): number | null;
export function evaluate(preset: string, reports: LhReport[], budget: PresetBudget): { rows: Row[]; errors: string[] };
export function formatRows(rows: Row[]): string;
