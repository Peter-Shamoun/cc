import { modKey } from './primitives';

/** One table drives both the key handler docs and the Shortcuts dialog. Bindings mirror ChatGPT's where one exists. */
export const SHORTCUTS: { label: string; keys: () => string[] }[] = [
  { label: 'New chat', keys: () => [modKey(), 'Shift', 'O'] },
  { label: 'Search chats', keys: () => [modKey(), 'K'] },
  { label: 'Toggle sidebar', keys: () => [modKey(), 'Shift', 'S'] },
  { label: 'Focus chat input', keys: () => ['Shift', 'Esc'] },
  { label: 'Copy last response', keys: () => [modKey(), 'Shift', 'C'] },
  { label: 'Export chat as Markdown', keys: () => [modKey(), 'Shift', 'E'] },
  { label: 'Settings', keys: () => [modKey(), ','] },
  { label: 'Show shortcuts', keys: () => [modKey(), '/'] },
  { label: 'Slash commands', keys: () => ['/'] },
  { label: 'New line in message', keys: () => ['Shift', 'Enter'] },
  { label: 'Close panel or dialog', keys: () => ['Esc'] },
];
