import { describe, expect, it } from 'vitest';
import { buildCsp, securityHeaders } from './securityHeaders';

describe('securityHeaders', () => {
  it('prod CSP is strict: no eval, no unsafe-inline script, no framing, hashes allowed', () => {
    const csp = buildCsp({ scriptHashes: ['sha256-abc'] });
    const script = csp.split('; ').find((d) => d.startsWith('script-src'))!;
    expect(script).toBe("script-src 'self' 'sha256-abc'");
    expect(csp).not.toContain('unsafe-eval');
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("base-uri 'none'");
    expect(csp).not.toMatch(/https?:\/\//);
  });
  it('dev CSP only adds HMR needs', () => {
    const csp = buildCsp({ dev: true, scriptHashes: ['sha256-abc'] });
    expect(csp).toContain("script-src 'self' 'unsafe-inline'");
    expect(csp).toContain('connect-src \'self\' ws: wss:');
    expect(csp).not.toContain('unsafe-eval');
  });
  it('sends no-store and the hardening headers', () => {
    const h = securityHeaders();
    expect(h['Cache-Control']).toBe('no-store');
    expect(h['X-Frame-Options']).toBe('DENY');
    expect(h['X-Content-Type-Options']).toBe('nosniff');
    expect(h['Referrer-Policy']).toBe('no-referrer');
    expect(h['Permissions-Policy']).toContain('camera=()');
  });
});
