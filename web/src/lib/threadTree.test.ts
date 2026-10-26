import { describe, expect, it } from 'vitest';
import { foldThread } from './threadTree';

const u = (id: string, parentId?: string) => ({ id, role: 'user' as const, ...(parentId ? { parentId } : {}) });
const a = (id: string, parentId?: string) => ({ id, role: 'assistant' as const, ...(parentId ? { parentId } : {}) });
const ids = (ms: { id: string }[]) => ms.map((m) => m.id);

describe('foldThread', () => {
  it('a linear transcript folds to itself', () => {
    const msgs = [u('u1'), a('a1', 'u1'), u('u2'), a('a2', 'u2')];
    expect(ids(foldThread(msgs).visible)).toEqual(['u1', 'a1', 'u2', 'a2']);
    expect(foldThread(msgs).branches).toEqual({});
  });

  it('a regenerated answer replaces the old one', () => {
    const msgs = [u('u1'), a('a1', 'u1'), a('a1b', 'u1')];
    expect(ids(foldThread(msgs).visible)).toEqual(['u1', 'a1b']);
  });

  it('a turn after a regenerate follows the new answer', () => {
    const msgs = [u('u1'), a('a1', 'u1'), a('a1b', 'u1'), u('u2'), a('a2', 'u2')];
    expect(ids(foldThread(msgs).visible)).toEqual(['u1', 'a1b', 'u2', 'a2']);
  });

  it('an edited question becomes a version at that index, with each version\'s newest tail', () => {
    const msgs = [u('u1'), a('a1', 'u1'), u('u2'), a('a2', 'u2'), u('u3'), a('a3', 'u3'), u('u2e', 'a1'), a('a2e', 'u2e')];
    const { visible, branches } = foldThread(msgs);
    expect(ids(visible)).toEqual(['u1', 'a1', 'u2e', 'a2e']);
    expect(branches[2].idx).toBe(1);
    expect(branches[2].versions.map(ids)).toEqual([['u2', 'a2', 'u3', 'a3'], ['u2e', 'a2e']]);
  });

  it('ignores a parentId that is unknown or points forward', () => {
    const msgs = [u('u1'), a('a1', 'nope'), u('u2', 'a9')];
    expect(ids(foldThread(msgs).visible)).toEqual(['u1', 'a1', 'u2']);
  });
});
