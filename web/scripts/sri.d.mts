type Read = (path: string) => Uint8Array | string | undefined;
export function sriHash(bytes: Uint8Array | string): string;
export function applySri(html: string, read: Read, jsFiles?: string[]): string;
export function checkSri(html: string, read: Read, jsFiles?: string[]): string[];
