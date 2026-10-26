// Pure response-header rules for the web app and the API (no I/O). Used by scripts/header_check.mjs and
// src/security/headerRules.test.ts. Keep in step with src/security/headers.ts and api/security.py.

/** Parse a CSP header into { directive: [sources] }. */
export function parseCsp(value) {
  const out = {};
  for (const part of String(value ?? '').split(';')) {
    const [name, ...sources] = part.trim().split(/\s+/);
    if (name) out[name.toLowerCase()] = sources;
  }
  return out;
}

const get = (h, name) => {
  const k = Object.keys(h).find((key) => key.toLowerCase() === name.toLowerCase());
  return k === undefined ? undefined : String(h[k]);
};

/**
 * @param {'html'|'asset'|'api'} kind
 * @param {Record<string,string>} headers
 * @returns {string[]} problems (empty = ok)
 */
export function checkHeaders(kind, headers) {
  const p = [];
  const need = (name, ok, why) => {
    const v = get(headers, name);
    if (v === undefined) p.push(`missing ${name}`);
    else if (!ok(v)) p.push(`${name}: ${why} (got "${v}")`);
  };
  need('X-Content-Type-Options', (v) => v.toLowerCase() === 'nosniff', 'must be nosniff');
  need('Referrer-Policy', (v) => v.toLowerCase() === 'no-referrer', 'must be no-referrer');
  need('X-Frame-Options', (v) => v.toUpperCase() === 'DENY', 'must be DENY');
  need('Permissions-Policy', (v) => /camera=\(\)/.test(v) && /microphone=\(\)/.test(v) && /geolocation=\(\)/.test(v), 'must deny camera, microphone, geolocation');
  need('Content-Security-Policy', () => true, '');
  const csp = parseCsp(get(headers, 'Content-Security-Policy'));
  const has = (d, src) => (csp[d] ?? []).includes(src);
  if (!has('frame-ancestors', "'none'")) p.push("CSP: frame-ancestors must be 'none'");
  if (kind === 'api') {
    if (!has('default-src', "'none'")) p.push("CSP: API default-src must be 'none'");
    need('Cache-Control', (v) => /\bno-store\b/.test(v), 'API responses must be no-store');
    if (get(headers, 'Server') !== undefined) p.push('Server header must not be sent');
  } else {
    const script = csp['script-src'] ?? csp['default-src'] ?? [];
    for (const bad of ["'unsafe-inline'", "'unsafe-eval'", "'wasm-unsafe-eval'", '*', 'data:', 'http:', 'https:'])
      if (script.includes(bad)) p.push(`CSP: script-src must not allow ${bad}`);
    if (!script.some((s) => /^'nonce-[A-Za-z0-9+/_=-]{16,}'$/.test(s))) p.push('CSP: script-src needs a nonce');
    if (!has('object-src', "'none'")) p.push("CSP: object-src must be 'none'");
    if (!has('base-uri', "'none'")) p.push("CSP: base-uri must be 'none'");
    if (!has('require-trusted-types-for', "'script'")) p.push("CSP: require-trusted-types-for 'script' missing (Trusted Types)");
    const tt = csp['trusted-types'] ?? [];
    if (tt.some((s) => s === '*' || s === "'allow-duplicates'") && kind === 'html') p.push('CSP: trusted-types must name policies, not * or allow-duplicates (prod)');
    if (kind === 'html') need('Cache-Control', (v) => /\bno-store\b/.test(v), 'HTML must be no-store');
    if (kind === 'asset') need('Cache-Control', (v) => /\bno-store\b/.test(v) || /\bimmutable\b/.test(v), 'hashed assets: no-store or immutable');
  }
  return p;
}

/** The nonce in a CSP header, or null. */
export function cspNonce(value) {
  const src = (parseCsp(value)['script-src'] ?? []).find((s) => s.startsWith("'nonce-"));
  return src ? src.slice(7, -1) : null;
}

/**
 * Problems with an HTML body vs its CSP header: inline <script>/<style> must carry the response nonce, and any nonce
 * attribute present must equal it (the per-response placeholder swap worked).
 */
export function checkHtmlNonce(html, cspHeader) {
  const p = [];
  const nonce = cspNonce(cspHeader);
  if (!nonce) return ['no nonce in CSP'];
  if (html.includes('__CC_CSP_NONCE__')) p.push('nonce placeholder left in HTML');
  for (const m of html.matchAll(/<(script|style|link)\b[^>]*>/gi)) {
    const n = /\bnonce="([^"]*)"/.exec(m[0])?.[1];
    const inline = m[1].toLowerCase() === 'style' || (m[1].toLowerCase() === 'script' && !/\bsrc=/.test(m[0]));
    if (n !== undefined ? n !== nonce : inline) p.push(`<${m[1]}> without the response nonce: ${m[0].slice(0, 80)}`);
  }
  return p;
}
