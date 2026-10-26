export type LicenceClass = 'permissive' | 'weak-copyleft' | 'copyleft-or-restricted' | 'unknown';
export interface AuditRow { name: string; version: string; license: string; dev: boolean; optional: boolean; cls: LicenceClass }
export const PERMISSIVE: Set<string>;
export const WEAK_COPYLEFT: Set<string>;
export function classify(expr: string | undefined): LicenceClass;
export function audit(lock: { packages?: Record<string, { version?: string; license?: string; dev?: boolean; optional?: boolean; link?: boolean }> }): {
  rows: AuditRow[]; problems: string[]; warnings: string[];
};
