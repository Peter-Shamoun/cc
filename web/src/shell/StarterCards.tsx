import { BarChart3, FileSearch, TrendingUp, Users, type LucideIcon } from 'lucide-react';

export interface Starter { title: string; hint: string; prompt: string; icon: LucideIcon }

/** Four claims questions for an empty chat. Prompts match the demo fixtures, so every card has a scripted answer. */
export const STARTERS: Starter[] = [
  { title: 'Denial rate by payer', hint: 'Last quarter, and which payers drive it', prompt: 'What was our denial rate by payer last quarter?', icon: BarChart3 },
  { title: 'Monthly denial trend', hint: 'Charted for our top 3 payers', prompt: 'Plot the monthly denial rate trend for our top 3 payers', icon: TrendingUp },
  { title: 'Why Cascade denies claims', hint: 'The CARC codes behind its denials', prompt: 'Which CARC codes drive Cascade Medicaid denials?', icon: FileSearch },
  { title: 'Who are our top providers?', hint: 'By paid amount or by claim volume', prompt: 'Who are our top providers?', icon: Users },
];

/**
 * Starter cards under the composer (a 2×2 grid). On phones they become one swipeable row above it with an edge fade.
 * The hint is decoration; each button's accessible name is just its title.
 */
export function StarterCards({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div role="group" aria-label="Suggestions" data-testid="starter-cards"
      className="mt-4 grid grid-cols-2 gap-2 max-sm:order-first max-sm:-mx-4 max-sm:mb-3 max-sm:mt-0 max-sm:flex max-sm:overflow-x-auto max-sm:px-4 max-sm:pb-1 max-sm:scrollbar-none max-sm:[mask-image:linear-gradient(to_right,transparent,#000_1rem,#000_calc(100%-2rem),transparent)]">
      {STARTERS.map((s, i) => (
        <button key={s.prompt} type="button" onClick={() => onPick(s.prompt)} aria-label={s.title} style={{ animationDelay: `${i * 40}ms` }} data-testid="starter-card"
          className="group flex min-w-0 items-start gap-3 rounded-2xl border border-line bg-bg px-4 py-3 text-left transition-colors hover:bg-hover motion-safe:animate-[cc-rise_240ms_ease-out_both] max-sm:shrink-0">
          <s.icon size={16} aria-hidden className="mt-0.5 shrink-0 text-fg-subtle transition-colors group-hover:text-fg-muted" />
          <span className="min-w-0">
            <span className="block truncate text-sm font-medium max-sm:overflow-visible max-sm:whitespace-nowrap text-fg">{s.title}</span>
            <span aria-hidden className="block truncate text-[13px] max-sm:overflow-visible max-sm:whitespace-nowrap text-fg-subtle">{s.hint}</span>
          </span>
        </button>
      ))}
    </div>
  );
}
