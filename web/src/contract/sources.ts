/**
 * Citations (contract 1.8.0, CONTRACT.md §14) and conversation export (§15).
 *
 * `message.end.sources` / `StoredMessage.sources` list the views an answer read (`role: 'answer'`) and the ones it
 * only explored (`role: 'explored'`). Each source links back to the step rows (`stepIds`) and parts (`partIds`) it fed,
 * so a table or chart can show its own "Sources" chips.
 */
import type { Source } from './v1.gen';

/** Sources the answer actually read, in server order. */
export const answerSources = (sources: Source[] | undefined): Source[] =>
  (sources ?? []).filter((s) => s.role === 'answer');

/** Sources that fed one part (table, chart or SQL code part). */
export const sourcesForPart = (sources: Source[] | undefined, partId: string): Source[] =>
  (sources ?? []).filter((s) => s.partIds?.includes(partId));

/** Sources a step row touched (step.sql_running / step.explore). */
export const sourcesForStep = (sources: Source[] | undefined, stepId: string): Source[] =>
  (sources ?? []).filter((s) => s.stepIds?.includes(stepId));

export type ExportFormat = 'markdown' | 'json';

/** GET target for the download button. Send it with the auth header, then save the blob; the server names the file. */
export const exportUrl = (conversationId: string, format: ExportFormat = 'markdown', base = ''): string =>
  `${base}/api/conversations/${encodeURIComponent(conversationId)}/export?format=${format}`;

/** Filename from the Content-Disposition header (fallback when it is missing). */
export function exportFilename(contentDisposition: string | null, format: ExportFormat = 'markdown'): string {
  const m = /filename="([^"]+)"/.exec(contentDisposition ?? '');
  return m ? m[1] : `claimclarity_conversation.${format === 'json' ? 'json' : 'md'}`;
}
