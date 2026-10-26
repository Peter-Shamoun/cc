import { BarChart3, FileSearch, TrendingUp, Users, type LucideIcon } from 'lucide-react';

export interface Starter { title: string; hint: string; prompt: string; icon: LucideIcon }

/** Four claims questions for an empty chat. Prompts match the demo fixtures, so every card has a scripted answer. */
export const STARTERS: Starter[] = [
  { title: 'Denial rate by payer', hint: 'Last quarter, and which payers drive it', prompt: 'What was our denial rate by payer last quarter?', icon: BarChart3 },
  { title: 'Denial trend', hint: 'Charted for our top 3 payers', prompt: 'Plot the monthly denial rate trend for our top 3 payers', icon: TrendingUp },
  { title: 'Cascade denial codes', hint: 'The CARC codes behind its denials', prompt: 'Which CARC codes drive Cascade Medicaid denials?', icon: FileSearch },
  { title: 'Who are our top providers?', hint: 'By paid amount or by claim volume', prompt: 'Who are our top providers?', icon: Users },
];

/**
 * Suggestions for an empty chat, kept quiet (Claude/ChatGPT, observed): on desktop one centred row of small outlined
 * chips (muted icon + short label) under the composer, not a grid of cards. On phones, one swipeable row of two-line
 * cards above the composer with an edge fade. They appear with a short fade, no staggered rise. Each button's
 * accessible name is its title; the hint is phone-only decoration.
 */
export function StarterCards({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div role="group" aria-label="Suggestions" data-testid="starter-cards"
      className="mt-4 flex flex-wrap justify-center gap-2 motion-safe:animate-[cc-fade_150ms_ease-out_both] max-sm:order-first max-sm:-mx-4 max-sm:mb-3 max-sm:mt-0 max-sm:flex-nowrap max-sm:justify-start max-sm:overflow-x-auto max-sm:px-4 max-sm:pb-1 max-sm:scrollbar-none max-sm:[mask-image:linear-gradient(to_right,transparent,#000_1rem,#000_calc(100%-2rem),transparent)]">
      {STARTERS.map((s) => (
        <button key={s.prompt} type="button" onClick={() => onPick(s.prompt)} aria-label={s.title} data-testid="starter-card"
          className="group flex min-w-0 items-center gap-2 rounded-lg border border-line px-3 py-1.5 text-left text-[13px] text-fg-muted transition-colors duration-100 hover:bg-hover hover:text-fg max-sm:shrink-0 max-sm:items-start max-sm:gap-2.5 max-sm:rounded-xl max-sm:px-3.5 max-sm:py-2.5">
          <s.icon size={15} strokeWidth={1.75} aria-hidden className="shrink-0 text-fg-subtle transition-colors group-hover:text-fg-muted max-sm:mt-0.5" />
          <span className="min-w-0">
            <span className="block whitespace-nowrap max-sm:text-sm max-sm:font-medium max-sm:text-fg">{s.title}</span>
            <span aria-hidden className="hidden whitespace-nowrap text-[13px] text-fg-subtle max-sm:block">{s.hint}</span>
          </span>
        </button>
      ))}
    </div>
  );
}
