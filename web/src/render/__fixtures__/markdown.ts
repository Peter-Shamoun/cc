export interface MarkdownFixture {
  id: string;
  markdown: string;
  /** Partials are rendered with the streaming caret and remend repair. */
  streaming?: boolean;
  /** Plain text that must survive rendering. */
  contains?: string;
  /** Raw marker that repair should have closed (absent from the visible text). */
  excludes?: string;
  math?: 'inline' | 'display';
  rtl?: boolean;
  /** A fenced block's visible source, held as text (never parsed as HTML). */
  code?: string;
}

const LONG = 'denial-rate-token-' + 'x'.repeat(4000);
const LONG_URL = 'https://example.com/claims/' + 'a'.repeat(3500);

export const markdownFixtures: MarkdownFixture[] = [
  {
    id: 'unclosed-fence',
    streaming: true,
    markdown: '```python\nprint("still streaming")',
    contains: 'print("still streaming")',
    excludes: '```',
    code: 'print("still streaming")',
  },
  {
    id: 'unclosed-bold',
    streaming: true,
    markdown: 'Denials rose **sharply',
    contains: 'sharply',
    excludes: '**',
  },
  {
    id: 'unclosed-italic',
    streaming: true,
    markdown: 'A *partial emphasis',
    contains: 'partial emphasis',
  },
  {
    id: 'unclosed-link',
    streaming: true,
    markdown: 'See the [method note](https://example.com/methods',
    contains: 'method note',
  },
  {
    id: 'half-table',
    streaming: true,
    markdown: '| Payer | Rate |\n| --- | --- |\n| Cascade |',
    contains: 'Cascade',
  },
  {
    id: 'half-table-header-only',
    streaming: true,
    markdown: '| CARC | Share |\n| --- | --- |',
    contains: 'CARC',
  },
  {
    id: 'nested-lists',
    markdown: '- Payers\n  - Cascade\n    - CO-16 missing NPI\n    - CO-197 no auth\n  - Summit\n',
    contains: 'CO-16 missing NPI',
  },
  {
    id: 'nested-ordered',
    markdown: '1. Audit\n   1. Referring NPI\n      1. Loop 2310A\n2. File window\n',
    contains: 'Loop 2310A',
  },
  {
    id: 'task-list',
    markdown: '- [x] Mask member ids\n- [ ] Re-run September\n',
    contains: 'Mask member ids',
  },
  {
    id: 'long-line',
    markdown: `The export key is ${LONG} and then the sentence continues.`,
    contains: LONG,
  },
  {
    id: 'emoji',
    markdown: 'Status 👨‍👩‍👧 cleared 👍🏻 and the flag is 🇺🇸. Trend ↑ 13.7%.',
    contains: '👨‍👩‍👧',
  },
  {
    id: 'rtl-hebrew',
    markdown: 'שיעור הדחייה עלה ברבעון.',
    contains: 'שיעור',
    rtl: true,
  },
  {
    id: 'rtl-arabic',
    markdown: 'ارتفع معدل الرفض في الربع الثالث.',
    contains: 'الرفض',
    rtl: true,
  },
  {
    id: 'bidi-override',
    markdown: 'Account \u202Eevil\u202C is not the label. The figure is 12.',
    contains: 'Account',
  },
  {
    id: 'long-url',
    markdown: `Details live at ${LONG_URL} for this export.`,
    contains: 'Details live at',
  },
  {
    id: 'inline-math',
    markdown: 'The energy is $E = mc^2$ in a vacuum.',
    contains: 'vacuum',
    math: 'inline',
  },
  {
    id: 'display-math',
    markdown: 'The integral is\n\n$$\n\\int_0^1 x \\, dx\n$$\n\nafter the break.',
    contains: 'after the break',
    math: 'display',
  },
  {
    id: 'unclosed-math',
    streaming: true,
    markdown: '$$\n\\int_0^1 x \\, dx',
    math: 'display',
  },
  {
    id: 'currency-not-math',
    markdown: 'Paid $1,234.56 yesterday. The model is $E = mc^2$.',
    contains: '$1,234.56',
    math: 'inline',
  },
  {
    id: 'currency-outside-code',
    markdown: 'Paid $1,234.56.\n\n```sql\nSELECT $1\n```\n',
    contains: '$1,234.56',
    code: 'SELECT $1',
  },
  {
    id: 'html-script',
    markdown: 'Before <script>alert(1)</script> after',
    contains: '<script>',
  },
  {
    id: 'html-img-onerror',
    markdown: 'Before <img src=x onerror="alert(1)"> after',
    contains: '<img',
  },
  {
    id: 'html-svg-onload',
    markdown: 'Before <svg onload="alert(1)"><circle></circle></svg> after',
    contains: '<svg',
  },
  {
    id: 'html-iframe',
    markdown: 'Before <iframe src="https://evil.test/embed"></iframe> after',
    contains: '<iframe',
  },
  {
    id: 'html-event-handler',
    markdown: '<div onclick="alert(1)">hi</div>',
    contains: 'onclick',
  },
  {
    id: 'html-form-meta',
    markdown: '<form action="https://evil.test"><input name="phi"></form><meta http-equiv="refresh" content="0;url=https://evil.test"><base href="https://evil.test/"><link rel="stylesheet" href="https://evil.test/a.css"><style>body{background:url("https://evil.test/p.png")}</style>',
    contains: '<form',
  },
  {
    id: 'javascript-link',
    markdown: 'Please [click](javascript:alert(1)) here.',
    contains: 'click',
  },
  {
    id: 'javascript-link-case',
    markdown: '[click](JaVaScRiPt:alert(1))',
    contains: 'click',
  },
  {
    id: 'javascript-html-anchor',
    markdown: '<a href="javascript:alert(1)">click</a>',
    contains: 'javascript:',
  },
  {
    id: 'data-url-link',
    markdown: '[x](data:text/html,<script>alert(1)</script>)',
    contains: 'x',
  },
  {
    id: 'vbscript-link',
    markdown: '[x](vbscript:msgbox(1))',
    contains: 'x',
  },
  {
    id: 'protocol-relative-link',
    markdown: '[x](//evil.test/phish)',
    contains: 'x',
  },
  {
    id: 'remote-markdown-image',
    markdown: 'Plot: ![denial bars](https://evil.test/track.png)',
    contains: 'denial bars',
  },
  {
    id: 'math-href-injection',
    markdown: '$\\href{javascript:alert(1)}{click}$',
    math: 'inline',
  },
  {
    id: 'html-in-fence',
    markdown: '```html\n<script>alert(1)</script>\n<img src=x onerror="alert(1)">\n```',
    code: '<script>alert(1)</script>',
    contains: '<script>alert(1)</script>',
  },
  {
    id: 'mixed-partial',
    streaming: true,
    markdown: [
      'שיעור הדחייה עלה.',
      '',
      '## Partial answer',
      '',
      '- item',
      '  - nested **bold',
      '',
      '| a | b |',
      '| --- | --- |',
      '| 1 |',
      '',
      '<script>alert(1)</script>',
      '[click](javascript:alert(1))',
      '',
      '```js',
      'console.log(1)',
    ].join('\n'),
    contains: 'Partial answer',
    rtl: true,
  },
  {
    id: 'blockquote-nested',
    markdown: '> Caveat\n>\n> - September is open\n> - Rates can move\n',
    contains: 'September is open',
  },
  {
    id: 'safe-https-link',
    markdown: 'Read [the note](https://example.com/note?q=1) now.',
    contains: 'the note',
  },
];
