/**
 * Short chat titles, generated client-side from the first question (ChatGPT/Claude show
 * "Denial rate by payer", not the truncated question). Pure string work: nothing leaves the
 * browser and nothing is stored beyond the in-memory transcript. A server/contract title,
 * when one exists, should replace this.
 */
const LEAD = [
  /^(hey|hi|hello|ok|okay|so|please)[,!.\s]+/i,
  /^(can|could|would|will) you (please )?(tell me|show me|give me|list|find|pull|get|check|compute|calculate|break down|plot|chart|explain|summari[sz]e)?\s*/i,
  /^(please )?(show|give|tell|list|find|pull|get|plot|chart|compute|calculate|break down|summari[sz]e|explain|compare|describe)( me| us)?\s+/i,
  /^i('d| would) like (to see |to know )?/i,
  /^(i want|i need) (to see |to know )?/i,
  /^(what|which|who|where|how)('s| is| are| was| were| does| do| did) (the |our |my |a |an )?/i,
  /^how (many|much) /i,
  /^(what|which) /i,
  /^(why|when) (is|are|was|were|did|does|do) (the |our |my )?/i,
  /^(is|are|was|were|do|does|did) (there |the |our |my )?/i,
];
const ARTICLE = /^(the|our|my|a|an|all|me|us)\s+/i;
const TRAIL = /\s+(for me|please|thanks|thank you|right now|today|again)$/i;
const STOP_END = /\s+(by|for|of|in|on|at|to|and|or|with|the|a|an|from|vs|per|over)$/i;

export function shortTitle(text: string, maxWords = 6, maxChars = 40): string {
  let t = text.replace(/\s+/g, ' ').trim();
  if (!t) return 'New chat';
  // first sentence / line only
  t = t.split(/(?<=[?.!])\s|\n/)[0].replace(/[?.!:;,]+$/g, '').trim();
  for (let pass = 0; pass < 3; pass++) {
    const before = t;
    for (const re of LEAD) t = t.replace(re, '');
    t = t.replace(ARTICLE, '').replace(TRAIL, '');
    if (t === before) break;
  }
  const words = t.split(' ').filter(Boolean);
  let out = '';
  for (const w of words.slice(0, maxWords)) {
    const next = out ? `${out} ${w}` : w;
    if (next.length > maxChars) break;
    out = next;
  }
  if (!out) out = (words[0] ?? text).slice(0, maxChars);
  while (STOP_END.test(out)) out = out.replace(STOP_END, '');
  out = out.replace(/[,;:\-–—]+$/, '').trim();
  if (!out) return text.slice(0, maxChars);
  // Sentence case, but keep acronyms/ids (CARC, 837P, Q3) intact.
  return out[0].toUpperCase() + out.slice(1);
}
