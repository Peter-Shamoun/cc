/**
 * Trusted Types (CSP `require-trusted-types-for 'script'; trusted-types cc-html default`, see headers.ts).
 *
 * With that CSP, a plain string reaching a DOM-XSS sink throws instead of running. The sinks are innerHTML,
 * outerHTML, insertAdjacentHTML, document.write, script src/text and eval-like calls, in our code or any dependency,
 * so a DOM-XSS bug becomes a broken render. Only two producers may mint TrustedHTML, through the `cc-html` policy:
 *   - shikiHtml():   Shiki's highlighted code (CodeBlock). Shiki builds the markup itself and escapes every token.
 *   - tooltipHtml(): chart tooltips (vega-tooltip's `formatTooltip`). Every key and value is escaped here, and an
 *                    `image` field is dropped (no model-chosen URLs).
 * The `default` policy (the browser's fallback for plain strings) accepts only '' (clearing a container, as
 * vega-embed does on every render) and rejects everything else.
 */
export const TT_POLICY = 'cc-html';

/** A string the browser treats as TrustedHTML when Trusted Types are available (the real type comes from the browser). */
export type SafeHtml = string & { readonly __safeHtml: unique symbol };

type Policy = { createHTML(input: string): unknown };
type Rules = { createHTML(input: string): string | null };
type Factory = { createPolicy(name: string, rules: Rules): Policy };
const factory = () => (globalThis as { trustedTypes?: Factory }).trustedTypes;

let policy: Policy | null | undefined;

function mint(html: string): SafeHtml | null {
  const tt = factory();
  if (!tt) return html as SafeHtml; // no Trusted Types in this browser (or jsdom): the CSP directive is ignored too
  if (policy === undefined) {
    try {
      policy = tt.createPolicy(TT_POLICY, { createHTML: (s) => s });
    } catch {
      policy = null; // name refused by the CSP: callers fall back to plain text
    }
  }
  return policy ? (policy.createHTML(html) as SafeHtml) : null;
}

/** Installs the `default` policy: '' only. Call once before the first render. */
export function installTrustedTypes(): void {
  const tt = factory();
  if (!tt) return;
  try {
    tt.createPolicy('default', { createHTML: (s) => (s === '' ? '' : null) });
  } catch {
    /* already installed (dev HMR) or refused: plain strings stay blocked either way */
  }
}

// Markup Shiki never emits. If it shows up, something other than escaped code got here: render plain text instead.
const FORBIDDEN = /<\s*\/?\s*(script|iframe|object|embed|link|meta|base|form|style|img|svg)\b|\bjavascript:|\son[a-z]+\s*=\s*["']/i;

/** Shiki's markup only: `<pre class="shiki"><code><span style="--shiki-..">escaped text</span>...</code></pre>`. */
export function shikiHtml(html: string): SafeHtml | null {
  // Shiki escapes text content (&lt; &quot;), so a real tag or attribute here means the markup wasn't Shiki's.
  if (!html.startsWith('<pre') || FORBIDDEN.test(html)) return null;
  return mint(html);
}

const ESC: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const escapeHtml = (v: unknown) => String(v).replace(/[&<>"']/g, (c) => ESC[c]);

function stringify(v: unknown, maxDepth: number): string {
  const seen: unknown[] = [];
  try {
    return JSON.stringify(v, function (this: unknown, _k, val: unknown) {
      if (typeof val !== 'object' || val === null) return val;
      const pos = seen.indexOf(this) + 1;
      seen.length = pos;
      if (seen.length > maxDepth) return '[Object]';
      if (seen.includes(val)) return '[Circular]';
      seen.push(val);
      return val;
    });
  } catch {
    return '[Object]';
  }
}

/**
 * vega-tooltip `formatTooltip` (same markup as its default, `<h2>` plus a key/value `<table>` styled in index.css),
 * with our own escaping, no `image`, and TrustedHTML out. Signature matches vega-tooltip's option.
 */
export function tooltipHtml(value: unknown, _valueToHtml?: unknown, maxDepth = 2): string {
  let html: string;
  if (Array.isArray(value)) {
    html = `[${value.map((v) => escapeHtml(typeof v === 'string' ? v : stringify(v, maxDepth))).join(', ')}]`;
  } else if (value && typeof value === 'object') {
    const { title, image: _image, ...rest } = value as Record<string, unknown>;
    html = title ? `<h2>${escapeHtml(title)}</h2>` : '';
    const rows = Object.entries(rest)
      .filter(([, v]) => v !== undefined)
      .map(([k, v]) => `<tr><td class="key">${escapeHtml(k)}</td><td class="value">${escapeHtml(v && typeof v === 'object' ? stringify(v, maxDepth) : v)}</td></tr>`);
    if (rows.length) html += `<table>${rows.join('')}</table>`;
    html ||= '{}';
  } else {
    html = escapeHtml(value);
  }
  // vega-tooltip assigns this to innerHTML; a TrustedHTML object is what the CSP accepts there.
  return (mint(html) ?? escapeHtml('')) as string;
}

/** Tests only. */
export function _resetTrustedTypes() {
  policy = undefined;
}
