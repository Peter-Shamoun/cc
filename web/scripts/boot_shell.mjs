// Boot-shell HTML transform (QA-P8), used by vite.config.ts on index.html after Vite has injected assets and nonces.
// 1. Inline public/theme-init.js (single source). Vite's html.cspNonce stamps the nonce on it after this hook;
//    if a nonce is already present (other pipelines), it is copied.
// 2. Move render-blocking stylesheets from <head> to the end of <body>, so the static boot shell above them paints
//    from the HTML alone. Module scripts still wait for the stylesheet, so React never renders unstyled.
// The CSP stays nonce-only; checkHtmlNonce (header_rules.mjs) verifies every inline element at serve time.
export function bootShellHtml(html, themeInit) {
  const src = themeInit.trim();
  if (/<\/script/i.test(src)) throw new Error('theme-init.js must not contain </script');
  const nonce = /<script[^>]*\snonce="([^"]+)"/.exec(html)?.[1];
  if (!/<script src="\/theme-init\.js"[^>]*><\/script>/.test(html)) throw new Error('boot shell: <script src="/theme-init.js"> not found');
  let out = html.replace(/<script src="\/theme-init\.js"[^>]*><\/script>/, () => `<script${nonce ? ` nonce="${nonce}"` : ''}>${src}</script>`);
  const sheets = [];
  out = out.replace(/\s*<link rel="stylesheet"[^>]*>/g, (m) => { sheets.push(m.trim()); return ''; });
  if (sheets.length) out = out.replace(/\s*<\/body>/, () => `\n    ${sheets.join('\n    ')}\n  </body>`);
  return out;
}
