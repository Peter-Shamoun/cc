import { CornerDownRight } from 'lucide-react';

/**
 * Suggested follow-ups under the last answer (ChatGPT-style: a few plain rows, no chips, no sparkle).
 * Provisional until the contract adds them: a `follow_ups` part `{ items: [{ id, text }] }`. The demo server can
 * script them; the real analyzer must emit them without an extra paid call (BACKEND-FIT). At most 3, last turn only,
 * hidden while streaming. One click sends the text as the next question.
 */
export type FollowUpsPart = { type: 'follow_ups'; id: string; items: { id: string; text: string }[] };
export const isFollowUps = (p: { type: string }): p is FollowUpsPart => p.type === 'follow_ups' && Array.isArray((p as FollowUpsPart).items);
const MAX = 3;

export function FollowUps({ part, onSend }: { part: FollowUpsPart; onSend: (text: string) => void }) {
  const items = part.items.filter((i) => i.text?.trim()).slice(0, MAX);
  if (!items.length) return null;
  return (
    <nav aria-label="Suggested follow-ups" className="mt-3 font-sans text-sm" data-testid="follow-ups">
      <ul className="divide-y divide-line border-t border-line">
        {items.map((i) => (
          <li key={i.id}>
            <button type="button" onClick={() => onSend(i.text.trim())}
              className="flex w-full items-center gap-2.5 px-1 py-2 text-left text-fg-muted hover:text-fg" data-testid="follow-up">
              <CornerDownRight size={14} className="shrink-0 text-fg-subtle" aria-hidden />
              <span className="min-w-0 truncate">{i.text.trim()}</span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
