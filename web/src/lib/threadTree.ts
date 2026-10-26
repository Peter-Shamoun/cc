import type { ID } from './contract';

type Node = { id: ID; role: 'user' | 'assistant'; parentId?: ID; editOf?: ID };

/**
 * The server records a transcript as an append-only list (CONTRACT.md §8): a regenerated answer is
 * appended with `parentId` = its user turn (and `regenerateOf`), an edited question with `parentId` =
 * the message before it, and `editOf` = the question it replaces (contract 1.2: an edited first question
 * has `editOf` and no `parentId`, so it is a new root). Messages with neither follow the message before them.
 *
 * `foldThread` turns that list back into what a chat UI shows: the path to the newest message, plus,
 * for every user turn on it that was edited, the sibling versions (each one's newest continuation),
 * in the shape useChat keeps for "< 2/3 >" (index of the user message -> tails from that index).
 * A plain linear list folds to itself with no branches.
 */
export function foldThread<M extends Node>(msgs: M[]): { visible: M[]; branches: Record<number, { versions: M[][]; idx: number }> } {
  if (msgs.length === 0) return { visible: [], branches: {} };
  const byId = new Map(msgs.map((m) => [m.id, m]));
  const order = new Map(msgs.map((m, i) => [m.id, i]));
  const parent = new Map<ID, ID | null>();
  msgs.forEach((m, i) => {
    const earlier = (id?: ID) => !!id && byId.has(id) && order.get(id)! < i;
    const p = earlier(m.parentId) ? m.parentId!
      : earlier(m.editOf) ? parent.get(m.editOf!) ?? null // a sibling of the edited question (root if that was the first)
      : i > 0 ? msgs[i - 1].id : null;
    parent.set(m.id, p);
  });
  const children = new Map<ID | null, M[]>();
  for (const m of msgs) {
    const p = parent.get(m.id)!;
    children.set(p, [...(children.get(p) ?? []), m]);
  }
  // newest leaf under a node = follow its newest child (children are in append order)
  const tail = (m: M): M[] => {
    const out = [m];
    for (let k = children.get(m.id); k?.length; k = children.get(out[out.length - 1].id)) out.push(k[k.length - 1]);
    return out;
  };
  const pathTo = (leaf: M): M[] => {
    const out: M[] = [];
    for (let m: M | undefined = leaf; m; m = byId.get(parent.get(m.id) ?? '')) out.unshift(m);
    return out;
  };
  const visible = pathTo(msgs[msgs.length - 1]);
  const branches: Record<number, { versions: M[][]; idx: number }> = {};
  visible.forEach((m, i) => {
    if (m.role !== 'user') return;
    const sibs = (children.get(parent.get(m.id)!) ?? []).filter((s) => s.role === 'user');
    if (sibs.length < 2) return;
    branches[i] = { versions: sibs.map((s) => (s.id === m.id ? visible.slice(i) : tail(s))), idx: sibs.findIndex((s) => s.id === m.id) };
  });
  return { visible, branches };
}
