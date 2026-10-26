/**
 * Security headers for every way the web app is served (vite dev, vite preview, production).
 * Pure module: imported by vite.config.ts and by tests. Keep in step with api/security.py.
 *
 * Scripts: 'self' + a per-response nonce. No 'unsafe-inline', no 'unsafe-eval' (Vega runs on the
 * AST interpreter). No 'wasm-unsafe-eval': Shiki uses its JavaScript regex engine.
 * Styles: <style>/<link> elements need 'self' or the nonce; inline style *attributes* are allowed via
 * style-src-attr because Shiki/KaTeX/Vega emit per-token colours and positions that cannot be hashed.
 */
export const NONCE_PLACEHOLDER = '__CC_CSP_NONCE__';

export const PERMISSIONS_POLICY = [
  'accelerometer=()', 'autoplay=()', 'camera=()', 'display-capture=()', 'encrypted-media=()',
  'geolocation=()', 'gyroscope=()', 'magnetometer=()', 'microphone=()', 'midi=()', 'payment=()',
  'publickey-credentials-get=()', 'screen-wake-lock=()', 'usb=()', 'xr-spatial-tracking=()',
  'clipboard-write=(self)', 'fullscreen=(self)',
].join(', ');

export function contentSecurityPolicy(nonce: string, opts: { dev?: boolean; connect?: string[] } = {}): string {
  const connect = ["'self'", ...(opts.connect ?? []), ...(opts.dev ? ['ws:', 'wss:'] : [])];
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}'`,
    `style-src 'self' 'nonce-${nonce}'`,
    `style-src-elem 'self' 'nonce-${nonce}'`,
    "style-src-attr 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src ${connect.join(' ')}`,
    "worker-src 'self' blob:",
    "media-src 'self' blob:",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    ...(opts.dev ? [] : ['upgrade-insecure-requests']),
  ].join('; ');
}

/** Headers for the HTML document and static assets. `noStore` for HTML and data; hashed assets may cache. */
export function securityHeaders(nonce: string, opts: { dev?: boolean; noStore?: boolean; connect?: string[] } = {}): Record<string, string> {
  return {
    'Content-Security-Policy': contentSecurityPolicy(nonce, opts),
    'Referrer-Policy': 'no-referrer',
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Permissions-Policy': PERMISSIONS_POLICY,
    'Cross-Origin-Opener-Policy': 'same-origin',
    'Cross-Origin-Resource-Policy': 'same-origin',
    ...(opts.noStore === false ? {} : { 'Cache-Control': 'no-store' }),
  };
}

export function newNonce(): string {
  const bytes = new Uint8Array(18);
  globalThis.crypto.getRandomValues(bytes);
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}
