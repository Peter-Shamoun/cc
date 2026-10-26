/** Bidirectional controls that can spoof a link label or leak direction into chrome. */
const BIDI = /[\u202A-\u202E\u2066-\u2069\u200E\u200F\uFEFF]/g;

/**
 * Allow only http(s), mailto, in-page hashes, and same-document relative paths.
 * `javascript:`, `data:`, `vbscript:`, `file:`, `blob:`, and protocol-relative
 * `//host` links are dropped. ChatGPT and Claude leave the label as text.
 */
export function safeHref(href: unknown): string | null {
  if (typeof href !== 'string') return null;
  const trimmed = href.replace(BIDI, '').trim();
  if (!trimmed || trimmed.length > 8000) return null;
  if (trimmed.startsWith('#') && !trimmed.includes(':')) return trimmed;
  if (trimmed.startsWith('/') && !trimmed.startsWith('//') && !trimmed.startsWith('/\\')) return trimmed;
  if (!/^[a-z][a-z0-9+.-]*:/i.test(trimmed) && !trimmed.startsWith('//') && !trimmed.startsWith('\\')) {
    return trimmed.includes('\\') ? null : trimmed;
  }
  try {
    const url = new URL(trimmed);
    if (url.protocol === 'http:' || url.protocol === 'https:' || url.protocol === 'mailto:') return trimmed;
  } catch {
    /* not a URL */
  }
  return null;
}
