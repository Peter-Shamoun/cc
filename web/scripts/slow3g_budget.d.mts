export interface FormLimits { fcp: number; composer: number }
export const BUDGET: { desktop: FormLimits; mobile: FormLimits };
export function evaluate(report: unknown, budget?: Record<string, Record<string, number>>): string[];
