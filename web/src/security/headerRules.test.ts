import { describe, expect, it } from 'vitest';
import { checkHeaders, checkHtmlNonce, cspNonce, parseCsp } from '../../scripts/header_rules.mjs';
import { securityHeaders } from './headers';

const NONCE = 'AbCdEfGhIjKlMnOpQrStUvWx';
const API = {
  'cache-control': 'no-store', 'content-security-policy': "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; sandbox",
  'referrer-policy': 'no-referrer', 'x-content-type-options': 'nosniff', 'x-frame-options': 'DENY',
  'permissions-policy': 'camera=(), microphone=(), geolocation=()',
};

describe('header rules (scripts/header_check.mjs)', () => {
  it('accepts what src/security/headers.ts emits for HTML', () => {
    expect(checkHeaders('html', securityHeaders(NONCE))).toEqual([]);
    expect(checkHeaders('asset', { ...securityHeaders(NONCE, { noStore: false }), 'Cache-Control': 'public, max-age=31536000, immutable' })).toEqual([]);
  });

  it('accepts the API policy (api/security.py)', () => {
    expect(checkHeaders('api', API)).toEqual([]);
  });

  it('flags weakened policies', () => {
    const h = securityHeaders(NONCE);
    expect(checkHeaders('html', { ...h, 'Content-Security-Policy': h['Content-Security-Policy'].replace('script-src', "script-src 'unsafe-inline'") }))
      .toContain("CSP: script-src must not allow 'unsafe-inline'");
    expect(checkHeaders('html', { ...h, 'Content-Security-Policy': h['Content-Security-Policy'].replace("frame-ancestors 'none'", "frame-ancestors *") }))
      .toContain("CSP: frame-ancestors must be 'none'");
    const { 'Cache-Control': _cc, ...noCache } = h;
    expect(checkHeaders('html', noCache)).toContain('missing Cache-Control');
    expect(checkHeaders('html', { ...h, 'X-Content-Type-Options': 'sniff' })[0]).toMatch(/nosniff/);
    expect(checkHeaders('html', { ...h, 'Referrer-Policy': 'origin' })[0]).toMatch(/no-referrer/);
    expect(checkHeaders('api', { ...API, server: 'uvicorn' })).toContain('Server header must not be sent');
    expect(checkHeaders('api', { ...API, 'cache-control': 'max-age=60' })[0]).toMatch(/no-store/);
  });

  it('checks the HTML nonce against the header', () => {
    const csp = securityHeaders(NONCE)['Content-Security-Policy'];
    expect(cspNonce(csp)).toBe(NONCE);
    expect(parseCsp(csp)['object-src']).toEqual(["'none'"]);
    expect(checkHtmlNonce(`<script src="/theme-init.js"></script><script type="module" nonce="${NONCE}" src="/assets/a.js"></script>`, csp)).toEqual([]);
    expect(checkHtmlNonce('<script>alert(1)</script>', csp)[0]).toMatch(/without the response nonce/);
    expect(checkHtmlNonce('<style nonce="other"></style>', csp)[0]).toMatch(/without the response nonce/);
    expect(checkHtmlNonce('<script nonce="__CC_CSP_NONCE__"></script>', csp)).toContain('nonce placeholder left in HTML');
  });
});
