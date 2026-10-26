import { NEW_CHAT_KEY, getDraft, setDraft } from '../shell/drafts';

/**
 * Hand-off from the static boot shell in index.html (QA-P8). The boot composer is a real textarea so a
 * reader on a slow link can start typing before the JS arrives; React replaces it on first render. Its text
 * moves into the in-memory new-chat draft (shell/drafts.ts: never storage, never logged), and focus and caret
 * follow it into the React composer.
 */
export const BOOT_INPUT_ID = 'cc-boot-input';
let refocus = false;

export function adoptBootDraft(doc: Document = document): void {
  const el = doc.getElementById(BOOT_INPUT_ID);
  if (!(el instanceof HTMLTextAreaElement)) return;
  // The boot textarea has `autofocus`, so focus alone is not intent. On touch, carry focus over only if the
  // reader actually typed (no surprise keyboard on load: shell-focus.spec, ace3f4b).
  const coarse = doc.defaultView?.matchMedia?.('(pointer: coarse)').matches ?? false;
  refocus = doc.activeElement === el && (!coarse || el.value.length > 0);
  if (el.value && !getDraft(NEW_CHAT_KEY)) setDraft(NEW_CHAT_KEY, el.value);
  el.value = '';
}

export function restoreBootFocus(doc: Document = document): void {
  if (!refocus) return;
  refocus = false;
  const t = doc.querySelector<HTMLTextAreaElement>('[data-testid="composer"] textarea');
  if (!t) return;
  t.focus();
  t.setSelectionRange(t.value.length, t.value.length);
}
