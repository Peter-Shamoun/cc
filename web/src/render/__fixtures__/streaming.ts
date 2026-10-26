/**
 * Long streaming documents for the prefix harness (answer team, torture pass on PR #106's corpus).
 * Synthetic content only. Each is fed to <Markdown streaming> one prefix at a time.
 */
export interface StreamingDoc { id: string; markdown: string }

const hugeTable = (rows: number, cols: number) => {
  const head = '| ' + Array.from({ length: cols }, (_, c) => `Column ${c + 1}`).join(' | ') + ' |';
  const rule = '|' + Array.from({ length: cols }, (_, c) => (c ? ' ---: ' : ' --- ')).join('|') + '|';
  const body = Array.from({ length: rows }, (_, r) => '| ' + Array.from({ length: cols }, (_, c) => (c ? (r * 37 + c * 11) % 1000 : `SYNTH-PAYER-${r}`)).join(' | ') + ' |');
  return [head, rule, ...body].join('\n');
};

export const streamingDocs: StreamingDoc[] = [
  {
    id: 'answer-with-table-and-code',
    markdown: 'Denial rate rose **sharply** in Q3.\n\n| Payer | Denial rate | Claims |\n| --- | ---: | ---: |\n| SYNTH-PAYER-ALPHA | 12.3% | 1,204 |\n| SYNTH-PAYER-BETA | 9.8% | 877 |\n\n```sql\nSELECT payer, AVG(denied::int) AS denial_rate\nFROM sem.claims\nGROUP BY payer;\n```\n\nThat is all.',
  },
  {
    id: 'deep-nested-lists',
    markdown: '1. First level\n   - second level with `code`\n     - third level **bold**\n       1. fourth ordered\n       2. fourth again\n   - back to second\n2. Next top item\n\n   Continuation paragraph inside item 2.\n\n   ```text\n   fenced inside a list\n   ```\n3. Last item',
  },
  {
    id: 'wide-display-math',
    markdown: 'Weighted denial rate:\n\n$$\n\\text{rate} = \\frac{\\sum_{i=1}^{n} w_i \\cdot d_i}{\\sum_{i=1}^{n} w_i} = \\frac{w_1 d_1 + w_2 d_2 + w_3 d_3 + w_4 d_4 + w_5 d_5 + w_6 d_6 + w_7 d_7 + w_8 d_8 + w_9 d_9 + w_{10} d_{10}}{w_1 + w_2 + w_3 + w_4 + w_5 + w_6 + w_7 + w_8 + w_9 + w_{10}}\n$$\n\nwhere $w_i$ is the claim weight.',
  },
  {
    id: 'long-urls-in-table',
    markdown: '| Source | Link |\n| --- | --- |\n| Docs | https://example.com/' + 'segment/'.repeat(40) + 'end |\n\nSee https://example.com/' + 'q'.repeat(600) + ' for more.',
  },
  {
    id: 'rtl-mixed',
    markdown: 'שיעור הדחייה עלה ל-12.3% ברבעון השלישי.\n\n- תשלום: $1,204.50\n- معدل الرفض **12.3%** في الربع الثالث\n\nEnglish after RTL with an emoji 👩‍⚕️🏥.',
  },
  {
    id: 'emoji-sequences',
    markdown: 'Status: ✅ done · ⚠️ check · 👩🏽‍💻 analyst · 🇺🇸 flag · 1️⃣ keycap · 🏳️‍🌈\n\n| Icon | Meaning |\n| --- | --- |\n| ✅ | ok |\n| ⚠️ | warn |',
  },
  { id: 'huge-table', markdown: 'All payers:\n\n' + hugeTable(150, 9) + '\n\nEnd of table.' },
  {
    id: 'fence-with-backticks',
    markdown: 'Example:\n\n````markdown\n```sql\nSELECT 1;\n```\n````\n\nDone.',
  },
  {
    id: 'blockquote-list-code',
    markdown: '> Note\n> - item one\n> - item `two`\n>\n> ```\n> quoted code\n> ```\n\nAfter quote.',
  },
];
