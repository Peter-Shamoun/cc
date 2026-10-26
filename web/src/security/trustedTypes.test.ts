import { afterEach, describe, expect, it, vi } from 'vitest';
import { _resetTrustedTypes, escapeHtml, installTrustedTypes, shikiHtml, tooltipHtml, TT_POLICY } from './trustedTypes';
import { contentSecurityPolicy } from './headers';

type Rules = { createHTML(s: string): string | null };
function fakeTrustedTypes() {
  const policies = new Map<string, Rules>();
  const tt = {
    createPolicy: vi.fn((name: string, rules: Rules) => {
      if (policies.has(name)) throw new TypeError(`Policy ${name} exists`);
      policies.set(name, rules);
      return { createHTML: (s: string) => ({ trusted: rules.createHTML(s), policy: name }) };
    }),
  };
  vi.stubGlobal('trustedTypes', tt);
  return { tt, policies };
}

afterEach(() => { vi.unstubAllGlobals(); _resetTrustedTypes(); });

describe('Trusted Types', () => {
  it('the CSP requires Trusted Types and names only our policies (dev may allow duplicates for HMR)', () => {
    expect(contentSecurityPolicy('n')).toContain("require-trusted-types-for 'script'; trusted-types cc-html default;");
    expect(contentSecurityPolicy('n')).not.toContain('allow-duplicates');
    expect(contentSecurityPolicy('n', { dev: true })).toContain("trusted-types cc-html default 'allow-duplicates'");
  });

  it('the default policy accepts only the empty string', () => {
    const { policies } = fakeTrustedTypes();
    installTrustedTypes();
    installTrustedTypes(); // second install (HMR) doesn't throw
    const d = policies.get('default')!;
    expect(d.createHTML('')).toBe('');
    expect(d.createHTML('<b>x</b>')).toBeNull();
    expect(d.createHTML('<img src=x onerror=alert(1)>')).toBeNull();
  });

  it('Shiki markup is minted by cc-html; anything that is not Shiki output is refused', () => {
    fakeTrustedTypes();
    const ok = '<pre class="shiki" style="--shiki-light:#000" tabindex="0"><code><span class="line"><span>SELECT &lt;script&gt; onerror=&quot;x&quot;</span></span></code></pre>';
    expect(shikiHtml(ok)).toEqual({ trusted: ok, policy: TT_POLICY });
    for (const bad of ['<img src=x onerror=alert(1)>', '<pre><script>alert(1)</script></pre>', '<pre><a href="javascript:alert(1)">x</a></pre>', '<pre><span onclick="x">a</span></pre>', '<pre><svg onload=1></svg></pre>'])
      expect(shikiHtml(bad)).toBeNull();
  });

  it('falls back to plain text when the CSP refuses the policy name', () => {
    vi.stubGlobal('trustedTypes', { createPolicy: () => { throw new TypeError('refused'); } });
    expect(shikiHtml('<pre class="shiki"><code>x</code></pre>')).toBeNull();
  });

  it('without Trusted Types (jsdom, Firefox) Shiki markup passes through as a string', () => {
    expect(shikiHtml('<pre class="shiki"><code>x</code></pre>')).toBe('<pre class="shiki"><code>x</code></pre>');
  });

  it('tooltips escape every key and value, drop image, and are minted by cc-html', () => {
    fakeTrustedTypes();
    const out = tooltipHtml({ title: '<b>T</b>', image: 'https://evil.example/x.png', 'pay<er>': '"Northwind" & <script>', n: 3, o: { a: 1 } }) as unknown as { trusted: string; policy: string };
    expect(out.policy).toBe(TT_POLICY);
    expect(out.trusted).toBe('<h2>&lt;b&gt;T&lt;/b&gt;</h2><table><tr><td class="key">pay&lt;er&gt;</td><td class="value">&quot;Northwind&quot; &amp; &lt;script&gt;</td></tr><tr><td class="key">n</td><td class="value">3</td></tr><tr><td class="key">o</td><td class="value">{&quot;a&quot;:1}</td></tr></table>');
    expect(out.trusted).not.toContain('evil.example');
  });

  it('tooltip arrays, scalars and empty objects', () => {
    expect(tooltipHtml(['<a>', 1])).toBe('[&lt;a&gt;, 1]');
    expect(tooltipHtml("O'Brien <x>")).toBe('O&#39;Brien &lt;x&gt;');
    expect(tooltipHtml({})).toBe('{}');
    expect(escapeHtml('&')).toBe('&amp;');
  });
});
