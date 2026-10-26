import type { ArtifactPart, ChartPart, CodePart, ImagePart, Part, TablePart } from '../lib/contract';
import { sanitizeParts } from './answer/sanitizeParts';

/** A report artifact's content (contract 1.9 §16: `{markdown}`), given a part-like shape for the panel. */
export type ReportDoc = { type: 'report'; id: string; markdown: string; title?: string };
export type ArtifactContent = TablePart | ChartPart | CodePart | ImagePart | ReportDoc;
/** What the side panel shows. `artifactId` ties versions together (a new version = same id, version + 1). */
export type Artifact = { part: ArtifactContent; title: string; version?: number; artifactId?: string; related: Part[] };

type Msg = { role: string; parts?: unknown[] };

export function contentOf(a: ArtifactPart): ArtifactContent {
  const c = a.content as ArtifactPart['content'];
  return 'type' in c ? c : { type: 'report', id: `${a.id}-report`, markdown: String((c as { markdown?: unknown }).markdown ?? ''), title: a.title };
}

export function toArtifact(a: ArtifactPart, related: Part[]): Artifact {
  return { part: contentOf(a), title: a.title, version: a.version, artifactId: a.artifactId, related };
}

/** Every version of `artifactId` across the thread, ascending; a repeated version keeps its latest copy. */
export function artifactVersions(messages: readonly Msg[], artifactId: string): Artifact[] {
  const byVersion = new Map<number, Artifact>();
  for (const m of messages) {
    if (m.role !== 'assistant' || !Array.isArray(m.parts)) continue;
    const parts = sanitizeParts(m.parts) as Part[];
    for (const p of parts) if (p.type === 'artifact' && p.artifactId === artifactId) byVersion.set(p.version, toArtifact(p, parts));
  }
  return [...byVersion.entries()].sort((a, b) => a[0] - b[0]).map(([, a]) => a);
}

/** Ids of all artifact parts with `autoOpen` in these messages (used to remember what was already there on load). */
export function autoOpenIds(messages: readonly Msg[]): string[] {
  const ids: string[] = [];
  for (const m of messages) {
    if (m.role !== 'assistant' || !Array.isArray(m.parts)) continue;
    for (const p of sanitizeParts(m.parts) as Part[]) if (p.type === 'artifact' && p.autoOpen) ids.push(p.id);
  }
  return ids;
}

/**
 * autoOpen is a side-by-side affordance. On a phone or tablet (coarse pointer, or narrower than the two-pane
 * layout) the panel is a full-screen sheet, so opening it unasked would cover the chat mid-answer (QA-A27).
 * There the artifact card shows inline and opens on tap, as Claude mobile and ChatGPT canvas do.
 */
export function canAutoOpen(): boolean {
  if (typeof window === 'undefined') return false;
  const coarse = typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches;
  return !coarse && window.innerWidth >= 768;
}

/** The newest autoOpen artifact part not in `seen`, with its message's parts (null where autoOpen is off). */
export function nextAutoOpen(messages: readonly Msg[], seen: ReadonlySet<string>): Artifact & { partId: string } | null {
  if (!canAutoOpen()) return null;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role !== 'assistant' || !Array.isArray(m.parts)) continue;
    const parts = sanitizeParts(m.parts) as Part[];
    for (let j = parts.length - 1; j >= 0; j--) {
      const p = parts[j];
      if (p.type === 'artifact' && p.autoOpen && !seen.has(p.id)) return { ...toArtifact(p, parts), partId: p.id };
    }
  }
  return null;
}

/** True while the user is typing somewhere (autoOpen must never steal focus or interrupt typing). */
export function userIsTyping(): boolean {
  const el = document.activeElement as HTMLElement | null;
  return !!el && (el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && !['button', 'checkbox', 'radio'].includes((el as HTMLInputElement).type)) || el.isContentEditable) && ((el as HTMLTextAreaElement).value ?? el.textContent ?? '').length > 0;
}

export const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 60) || 'artifact';
