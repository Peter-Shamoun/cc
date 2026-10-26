import type { Message, Part } from '../lib/contract';
import type { AssistantView } from '../lib/contract';
import type { AssistantViewV1 } from '../api/contract';
import { messageMarkdown } from '../components/Message';

/**
 * Transcript → Markdown for Share/Export. Reuses the per-answer Copy serialiser (which drops
 * PHI-flagged table columns), so an export never contains more than Copy would.
 */
export function transcriptMarkdown(title: string, messages: (Message | AssistantView | AssistantViewV1)[], when = new Date()): string {
  const out = [`# ${title.replace(/\n/g, ' ')}`, '', `_Exported from ClaimClarity Analyst on ${when.toISOString().slice(0, 10)}. Synthetic demo data._`, ''];
  for (const m of messages) {
    if (m.role === 'user') out.push('## You', '', m.text, '');
    else {
      const body = messageMarkdown({ ...(m as AssistantView), parts: m.parts as Part[] } as unknown as AssistantView);
      out.push('## ClaimClarity', '', body || '_(no text)_', '');
    }
  }
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trimEnd() + '\n';
}

/** Filesystem-safe slug from a title (titles are server-scrubbed or user-typed; ids never leak here). */
export function exportFilename(title: string): string {
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60) || 'chat';
  return `${slug}.md`;
}
