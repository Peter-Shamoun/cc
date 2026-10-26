import { modKey } from './primitives';

/** One table drives both the key handler docs and the Shortcuts dialog. Bindings mirror ChatGPT's where one exists. */
export type ShortcutGroup = 'Chats' | 'Navigation' | 'Composer';
export const SHORTCUTS: { label: string; keys: () => string[]; group: ShortcutGroup }[] = [
  { label: 'New chat', keys: () => [modKey(), 'Shift', 'O'], group: 'Chats' },
  { label: 'Search chats', keys: () => [modKey(), 'K'], group: 'Navigation' },
  { label: 'Toggle sidebar', keys: () => [modKey(), 'Shift', 'S'], group: 'Navigation' },
  { label: 'Focus chat input', keys: () => ['Shift', 'Esc'], group: 'Composer' },
  { label: 'Copy last response', keys: () => [modKey(), 'Shift', 'C'], group: 'Chats' },
  { label: 'Export chat as Markdown', keys: () => [modKey(), 'Shift', 'E'], group: 'Chats' },
  { label: 'Settings', keys: () => [modKey(), ','], group: 'Navigation' },
  { label: 'Show shortcuts', keys: () => [modKey(), '/'], group: 'Navigation' },
  { label: 'Stop generating', keys: () => ['Esc'], group: 'Composer' },
  { label: 'Slash commands', keys: () => ['/'], group: 'Composer' },
  { label: 'New line in message', keys: () => ['Shift', 'Enter'], group: 'Composer' },
  { label: 'Close panel or dialog', keys: () => ['Esc'], group: 'Navigation' },
];
