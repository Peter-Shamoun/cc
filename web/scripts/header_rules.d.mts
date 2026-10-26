export function parseCsp(value: string | undefined): Record<string, string[]>;
export function checkHeaders(kind: 'html' | 'asset' | 'api', headers: Record<string, string>): string[];
export function cspNonce(value: string | undefined): string | null;
export function checkHtmlNonce(html: string, cspHeader: string | undefined): string[];
