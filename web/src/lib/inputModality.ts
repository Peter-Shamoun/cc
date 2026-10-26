/**
 * Tracks whether the user is navigating by keyboard: `html[data-kbd]` is set on Tab (and the
 * Shift+Esc "focus composer" shortcut) and cleared on any pointer press. CSS uses it where
 * :focus-visible is too eager, e.g. text fields, which match it even after a mouse click.
 * Typing in a field doesn't count as keyboard navigation.
 */
export function trackInputModality(root: HTMLElement = document.documentElement): () => void {
  const key = (e: KeyboardEvent) => {
    if (e.key === 'Tab' || (e.key === 'Escape' && e.shiftKey) || e.key === 'F6') root.setAttribute('data-kbd', '');
  };
  const pointer = () => root.removeAttribute('data-kbd');
  window.addEventListener('keydown', key, true);
  window.addEventListener('pointerdown', pointer, true);
  return () => {
    window.removeEventListener('keydown', key, true);
    window.removeEventListener('pointerdown', pointer, true);
    root.removeAttribute('data-kbd');
  };
}
