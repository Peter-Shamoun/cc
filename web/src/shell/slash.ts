export interface SlashCommand {
  name: string;
  description: string;
  /** Either replace the composer text with a prompt template, or run a shell action. */
  insert?: string;
  action?: 'new' | 'search' | 'export' | 'settings' | 'shortcuts' | 'theme-light' | 'theme-dark' | 'theme-system';
}

export const SLASH_COMMANDS: SlashCommand[] = [
  { name: 'denials', description: 'Denial rate by payer for a period', insert: 'What was our denial rate by payer last quarter?' },
  { name: 'trend', description: 'Monthly trend of a metric', insert: 'Plot the monthly denial trend for the top 3 payers' },
  { name: 'carc', description: 'Top CARC reason codes for a payer', insert: 'Which CARC codes drive Cascade Medicaid denials?' },
  { name: 'ar', description: 'Open A/R aging buckets', insert: 'Show open A/R by aging bucket (0-30, 31-60, 61-90, 90+)' },
  { name: 'new', description: 'Start a new chat', action: 'new' },
  { name: 'search', description: 'Search all chats', action: 'search' },
  { name: 'export', description: 'Export this chat as Markdown', action: 'export' },
  { name: 'light', description: 'Light theme', action: 'theme-light' },
  { name: 'dark', description: 'Dark theme', action: 'theme-dark' },
  { name: 'system', description: 'Follow the system theme', action: 'theme-system' },
  { name: 'settings', description: 'Open settings', action: 'settings' },
  { name: 'shortcuts', description: 'Keyboard shortcuts', action: 'shortcuts' },
];

/** Commands matching the composer text, or null when the slash menu shouldn't show. */
export function matchSlash(text: string): SlashCommand[] | null {
  const m = /^\/(\w*)$/.exec(text);
  if (!m) return null;
  const q = m[1].toLowerCase();
  return SLASH_COMMANDS.filter((c) => c.name.startsWith(q) || (q.length > 1 && c.description.toLowerCase().includes(q)));
}
