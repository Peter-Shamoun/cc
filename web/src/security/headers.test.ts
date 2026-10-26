import fs from 'node:fs';
import { describe, expect, it } from 'vitest';
import path from 'node:path';
import { contentSecurityPolicy, newNonce, securityHeaders, NONCE_PLACEHOLDER } from './headers';

const root = path.resolve(__dirname, '../..');
const directive = (csp: string, name: string) => csp.split('; ').find((d) => d.startsWith(name + ' ')) ?? '';

describe('security headers', () => {
  const csp = contentSecurityPolicy('abc');
  it('scripts: self + nonce only, never unsafe-inline or unsafe-eval', () => {
    const s = directive(csp, 'script-src');
    expect(s).toContain("'nonce-abc'");
    expect(s).not.toContain('unsafe-inline');
    expect(s).not.toMatch(/'unsafe-eval'/);
  });
  it('style elements need self or the nonce; only attributes may be inline', () => {
    expect(directive(csp, 'style-src-elem')).toBe("style-src-elem 'self' 'nonce-abc'");
    expect(directive(csp, 'style-src')).not.toContain('unsafe-inline');
    expect(directive(csp, 'style-src-attr')).toBe("style-src-attr 'unsafe-inline'");
  });
  it('locks framing, plugins, base and outbound connections', () => {
    for (const d of ["frame-ancestors 'none'", "object-src 'none'", "base-uri 'none'", "connect-src 'self'", "default-src 'self'"]) expect(csp).toContain(d);
    expect(csp).not.toMatch(/https?:\/\//);
  });
  it('dev adds websocket HMR, production upgrades requests', () => {
    expect(directive(contentSecurityPolicy('n', { dev: true }), 'connect-src')).toContain('ws:');
    expect(csp).toContain('upgrade-insecure-requests');
  });
  it('sets the full header set, no-store by default', () => {
    const h = securityHeaders('n');
    expect(h['Cache-Control']).toBe('no-store');
    expect(h['Referrer-Policy']).toBe('no-referrer');
    expect(h['X-Content-Type-Options']).toBe('nosniff');
    expect(h['X-Frame-Options']).toBe('DENY');
    for (const f of ['camera=()', 'microphone=()', 'geolocation=()', 'payment=()', 'usb=()']) expect(h['Permissions-Policy']).toContain(f);
    expect(securityHeaders('n', { noStore: false })['Cache-Control']).toBeUndefined();
  });
  it('nonces are random and base64', () => {
    const a = newNonce();
    expect(a).toMatch(/^[A-Za-z0-9+/]{24}$/);
    expect(newNonce()).not.toBe(a);
    expect(NONCE_PLACEHOLDER).toMatch(/^__\w+__$/);
  });
});

describe('CSP-compatible sources', () => {
  it('index.html has no inline scripts or styles', () => {
    const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
    expect(html).not.toMatch(/<script(?![^>]*\bsrc=)[^>]*>/);
    expect(html).not.toMatch(/<style\b/);
    expect(html).not.toMatch(/\son[a-z]+=/i);
  });
  it('no eval/new Function and no raw <style> injection in app source', () => {
    const files: string[] = [];
    const walk = (d: string) => fs.readdirSync(d, { withFileTypes: true }).forEach((e) => {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p); else if (/\.(ts|tsx)$/.test(e.name) && !/\.test\./.test(e.name)) files.push(p);
    });
    walk(path.join(root, 'src'));
    for (const f of files) {
      const src = fs.readFileSync(f, 'utf8');
      expect(src, f).not.toMatch(/\beval\(|new Function\(/);
      expect(src, f).not.toMatch(/createElement\(['"]style['"]\)/);
    }
  });
  it('Vega runs on the AST interpreter without injected styles', () => {
    const chart = fs.readFileSync(path.join(root, 'src/components/parts/ChartView.tsx'), 'utf8');
    expect(chart).toMatch(/ast: true/);
    expect(chart).toMatch(/defaultStyle: false/);
    expect(chart).toMatch(/disableDefaultStyle: true/);
  });
});

describe('no WebAssembly', () => {
  it('script-src has no wasm-unsafe-eval and CodeBlock uses the Shiki JS engine, not the full bundle', async () => {
    const { contentSecurityPolicy } = await import('./headers');
    expect(contentSecurityPolicy('n')).not.toMatch(/wasm-unsafe-eval/);
    const src = (await import('../components/parts/CodeBlock.tsx?raw')).default;
    expect(src).toMatch(/shiki\/engine\/javascript/);
    expect(src).not.toMatch(/import\('shiki'\)/);
  });
});
