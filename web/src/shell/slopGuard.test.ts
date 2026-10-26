import { readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Peter's 10:06 PM directive: the shell looks like Claude/ChatGPT (neutral palette, one restrained accent, hairlines,
 * small muted icons, fast quiet motion), never like generic "AI slop". This guards the shell's own files against the
 * banned patterns so they can't creep back in: decorative/coloured gradients (same-colour scroll fades are fine), glass/blur, glows, heavy shadows,
 * sparkles, emoji in chrome, staggered/bouncy entrances and stray saturated colours.
 */
const root = resolve(process.cwd(), 'src');
const files = [
  ...readdirSync(join(root, 'shell')).filter((f) => f.endsWith('.tsx') && !f.includes('.test.')).map((f) => join(root, 'shell', f)),
  join(root, 'components', 'Sidebar.tsx'),
];
const BANNED: [string, RegExp][] = [
  ['gradient background/text', /bg-clip-text|text-transparent|\b(?:from|via|to)-(?:purple|violet|indigo|blue|pink|fuchsia|cyan|sky)-/],
  ['glass / backdrop blur', /backdrop-blur|backdrop-filter/],
  ['glow / neon ring', /shadow-(purple|violet|indigo|blue|pink|fuchsia|cyan)-|drop-shadow-\[0_0_|ring-(purple|violet|fuchsia|pink)-/],
  ['heavy drop shadow', /\bshadow-2xl\b/],
  ['sparkle icon', /\bSparkles?\b|WandSparkles|✨/],
  ['bouncy motion', /animate-bounce|animate-\[[^\]]*cubic-bezier\([^)]*1\.[2-9]/],
  ['staggered entrance', /animationDelay:\s*`\$\{i\s*\*/],
  ['saturated purple/indigo/violet', /\b(?:bg|text|border|from|to|via)-(?:purple|violet|indigo|fuchsia)-\d|#(?:4352d6|7c3aed|8b5cf6|6366f1|a855f7)\b/i],
  ['emoji in UI chrome', /[\u{1F300}-\u{1FAFF}\u{2728}\u{1F680}]/u],
];

describe('shell slop guard (Claude/ChatGPT restraint)', () => {
  it('scans the shell sources', () => expect(files.length).toBeGreaterThan(5)); // fewer files since sharing was removed (12:15 AM)
  for (const [name, re] of BANNED) {
    it(`no ${name}`, () => {
      const hits = files.flatMap((f) => readFileSync(f, 'utf8').split('\n').flatMap((l, i) => (re.test(l) && !/slop-ok/.test(l) ? [`${f.slice(root.length + 1)}:${i + 1}`] : [])));
      expect(hits).toEqual([]);
    });
  }
});
