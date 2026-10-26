import fs from 'node:fs';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { adoptBootDraft, restoreBootFocus, BOOT_INPUT_ID } from './bootShell';
import { NEW_CHAT_KEY, clearDrafts, getDraft, setDraft } from '../shell/drafts';

const html = fs.readFileSync(path.resolve(__dirname, '../../index.html'), 'utf8');
const themeInit = fs.readFileSync(path.resolve(__dirname, '../../public/theme-init.js'), 'utf8');

afterEach(() => { clearDrafts(); document.body.innerHTML = ''; });

describe('boot shell hand-off (QA-P8)', () => {
  it('moves text typed before JS into the in-memory new-chat draft and clears the static node', () => {
    document.body.innerHTML = `<textarea id="${BOOT_INPUT_ID}"></textarea>`;
    const el = document.getElementById(BOOT_INPUT_ID) as HTMLTextAreaElement;
    el.value = 'synthetic question';
    adoptBootDraft();
    expect(getDraft(NEW_CHAT_KEY)).toBe('synthetic question');
    expect(el.value).toBe('');
  });

  it('never overwrites an existing draft and is a no-op without the boot node', () => {
    setDraft(NEW_CHAT_KEY, 'kept');
    document.body.innerHTML = `<textarea id="${BOOT_INPUT_ID}">other</textarea>`;
    adoptBootDraft();
    expect(getDraft(NEW_CHAT_KEY)).toBe('kept');
    document.body.innerHTML = '';
    expect(() => adoptBootDraft()).not.toThrow();
  });

  it('carries focus and caret into the React composer only if the boot composer had focus', () => {
    document.body.innerHTML = `<textarea id="${BOOT_INPUT_ID}"></textarea>`;
    (document.getElementById(BOOT_INPUT_ID) as HTMLTextAreaElement).focus();
    adoptBootDraft();
    document.body.innerHTML = '<form data-testid="composer"><textarea>abc</textarea></form>';
    restoreBootFocus();
    const t = document.querySelector('textarea')!;
    expect(document.activeElement).toBe(t);
    expect(t.selectionStart).toBe(3);
    (t as HTMLTextAreaElement).blur();
    restoreBootFocus();
    expect(document.activeElement).not.toBe(t);
  });
});

describe('index.html boot shell', () => {
  it('has a real Message textbox matching the React composer, and the app greeting', () => {
    expect(html).toMatch(new RegExp(`<textarea id="${BOOT_INPUT_ID}" aria-label="Message" placeholder="Ask about your claims"`));
    expect(html).toContain('What should we look into?');
  });

  it('is static: no inline event handlers, no URLs beyond the entry, no storage of what is typed', () => {
    expect(html).not.toMatch(/\son[a-z]+=/i);
    expect(html.replace("xmlns='http://www.w3.org/2000/svg'", '')).not.toMatch(/https?:\/\//);
    expect(themeInit).not.toMatch(/setItem|sessionStorage|document\.cookie/);
    expect(themeInit).not.toMatch(/<\/script/i);
    // Only allowlisted pref keys are read before React (spec §6.2).
    for (const m of themeInit.matchAll(/getItem\('cc\.pref\.(\w+)'\)/g)) expect(['theme', 'palette', 'sidebarCollapsed']).toContain(m[1]);
  });
});
