/**
 * Security headers for the web app (CONTRACT.md §10). Pure so vite.config.ts and tests share it.
 *
 * Production/preview CSP is strict: no inline script except the hashed theme bootstrap in index.html,
 * no eval (charts run Vega with `ast: true`, i.e. the CSP-safe expression interpreter), no third-party
 * origins. Dev adds only what Vite HMR needs (React refresh preamble, websocket).
 * `style-src 'unsafe-inline'` stays because Shiki/Vega/Streamdown set style attributes.
 */
export interface CspOptions {
  dev?: boolean;
  /** 'sha256-…' sources for inline <script> blocks in index.html. */
  scriptHashes?: string[];
  /** Extra connect-src origins (e.g. an API on another origin). */
  connect?: string[];
}

export function buildCsp({ dev = false, scriptHashes = [], connect = [] }: CspOptions = {}): string {
  const hashes = scriptHashes.map((h) => `'${h}'`);
  const directives: Record<string, string[]> = {
    'default-src': ["'self'"],
    'script-src': ["'self'", ...hashes, ...(dev ? ["'unsafe-inline'"] : [])],
    'style-src': ["'self'", "'unsafe-inline'"],
    'img-src': ["'self'", 'data:', 'blob:'],
    'font-src': ["'self'", 'data:'],
    'connect-src': ["'self'", ...connect, ...(dev ? ['ws:', 'wss:'] : [])],
    'worker-src': ["'self'", 'blob:'],
    'object-src': ["'none'"],
    'base-uri': ["'none'"],
    'form-action': ["'self'"],
    'frame-ancestors': ["'none'"],
    'manifest-src': ["'self'"],
  };
  if (dev) {
    // A hash plus 'unsafe-inline' makes browsers ignore 'unsafe-inline'; dev needs Vite's inline preamble.
    directives['script-src'] = ["'self'", "'unsafe-inline'"];
  }
  return Object.entries(directives).map(([k, v]) => `${k} ${v.join(' ')}`).join('; ');
}

export function securityHeaders(opts: CspOptions = {}): Record<string, string> {
  return {
    'Content-Security-Policy': buildCsp(opts),
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'X-Frame-Options': 'DENY',
    'Cross-Origin-Opener-Policy': 'same-origin',
    'Cross-Origin-Resource-Policy': 'same-origin',
    'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=(), clipboard-read=(), interest-cohort=()',
  };
}
