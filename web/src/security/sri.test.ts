import { describe, expect, it } from 'vitest';
import { createHash } from 'node:crypto';
import { applySri, checkSri, sriHash } from '../../scripts/sri.mjs';

const files: Record<string, string> = {
  '/assets/index-a.js': 'console.log("entry")',
  '/assets/runtime-b.js': 'export const r = 1',
  '/assets/index-c.css': 'body{color:red}',
  '/assets/lazy-d.js': 'export default 42',
};
const read = (p: string) => files[p];
const js = Object.keys(files).filter((p) => p.endsWith('.js'));
const HTML = `<!doctype html><html><head>
    <script nonce="N">theme()</script>
    <link rel="icon" href="data:image/svg+xml,x" />
    <script type="module" crossorigin src="/assets/index-a.js" nonce="N"></script>
    <link rel="modulepreload" crossorigin href="/assets/runtime-b.js" nonce="N">
  </head><body><div id="root"></div>
    <link rel="stylesheet" crossorigin href="/assets/index-c.css" nonce="N">
  </body></html>`;

describe('Subresource Integrity (scripts/sri.mjs)', () => {
  it('sriHash is base64 sha384', () => {
    expect(sriHash('abc')).toBe(`sha384-${createHash('sha384').update('abc').digest('base64')}`);
  });

  it('stamps integrity on the entry, modulepreload and stylesheet; leaves inline and data: tags alone', () => {
    const out = applySri(HTML, read, js);
    expect(out).toContain(`src="/assets/index-a.js" nonce="N" integrity="${sriHash(files['/assets/index-a.js'])}"`);
    expect(out).toContain(`integrity="${sriHash(files['/assets/runtime-b.js'])}"`);
    expect(out).toContain(`integrity="${sriHash(files['/assets/index-c.css'])}"`);
    expect(out).toMatch(/<script nonce="N">theme\(\)<\/script>/);
    expect(out).toMatch(/<link rel="icon" href="data:image\/svg\+xml,x" \/>/);
    expect(checkSri(out, read, js)).toEqual([]);
  });

  it('adds a nonce-carrying import map with integrity for every JS file (lazy chunks too), before any module script', () => {
    const out = applySri(HTML, read, js);
    const m = /<script type="importmap" nonce="N">(.*?)<\/script>/.exec(out)!;
    expect(JSON.parse(m[1]).integrity['/assets/lazy-d.js']).toBe(sriHash(files['/assets/lazy-d.js']));
    expect(m.index).toBeLessThan(out.indexOf('type="module"'));
    expect(m.index).toBeLessThan(out.indexOf('rel="modulepreload"'));
  });

  it('is idempotent: re-applying replaces old integrity and the old map', () => {
    const once = applySri(HTML, read, js);
    expect(applySri(once, read, js)).toBe(once);
  });

  it('check catches a tampered file, a missing attribute, a missing crossorigin and a late import map', () => {
    const out = applySri(HTML, read, js);
    expect(checkSri(out, (p) => (p === '/assets/lazy-d.js' ? 'evil()' : read(p)), js)).toEqual(['/assets/lazy-d.js: import map integrity mismatch']);
    expect(checkSri(out, (p) => (p === '/assets/index-c.css' ? 'x' : read(p)), js)).toEqual(['/assets/index-c.css: integrity mismatch']);
    expect(checkSri(HTML, read, [])).toEqual(expect.arrayContaining(['/assets/index-a.js: no integrity attribute']));
    expect(checkSri(out.replace(/ crossorigin/g, ''), read, js)).toContain('/assets/index-a.js: no crossorigin attribute');
    const map = /<script type="importmap"[^>]*>.*?<\/script>/.exec(out)![0];
    const late = out.replace(map, '').replace('</body>', `${map}</body>`);
    expect(checkSri(late, read, js)).toContain('import map comes after a module script');
    expect(checkSri(out, read, [...js, '/assets/new-e.js'])).toContain('/assets/new-e.js: not in import map integrity');
  });

  it('fails the build when a referenced asset is missing, and escapes "<" in the map', () => {
    expect(() => applySri(HTML, (p) => (p === '/assets/runtime-b.js' ? undefined : read(p)), js)).toThrow(/runtime-b\.js/);
    const out = applySri(HTML, (p) => read(p) ?? '', [...js, '/assets/</script>.js']);
    expect(out).not.toContain('"/assets/</script>.js"');
    expect(out).toContain('\\u003c/script>');
  });

  it('adds crossorigin when a tag lacks it', () => {
    const out = applySri(HTML.replace('rel="stylesheet" crossorigin', 'rel="stylesheet"'), read, js);
    expect(out).toMatch(/<link crossorigin rel="stylesheet" href="\/assets\/index-c\.css"/);
  });
});
