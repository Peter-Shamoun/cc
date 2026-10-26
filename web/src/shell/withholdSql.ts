import type { SharedConversation } from '../contract/v1.gen';

/**
 * QA-S18: a share made "without result data" also keeps the SQL to the owner. Queries can carry literal values
 * (member ids, NPIs, dates) in WHERE clauses, so they count as data. Contract 1.11 §17 still sends SQL on data-off
 * shares; until the server withholds it (requested from the Contract team, see NOTES), the shared page drops it
 * here, before anything renders: SQL code parts go, and every SQL string on steps / legacy tool calls is emptied
 * (the fields are required by the schema, so they stay, empty). With no SQL left, "View SQL", the SQL inspector,
 * "Copy SQL" and the artifact's SQL tab have nothing to show and stay hidden.
 */
export function withholdSql(shared: SharedConversation): SharedConversation {
  if (shared.includeData) return shared;
  return { ...shared, messages: shared.messages.map((m) => scrubMessage(m as unknown as Record<string, unknown>)) as unknown as SharedConversation['messages'] };
}

const isSqlCode = (p: Record<string, unknown>) => p.type === 'code' && String(p.language ?? '').toLowerCase() === 'sql';

function scrubMessage(m: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...m };
  if (Array.isArray(m.parts)) {
    out.parts = (m.parts as Record<string, unknown>[]).filter((p) => !isSqlCode(p)).map((p) => {
      if (p.type === 'table' && 'sqlPartId' in p) { const { sqlPartId: _drop, ...rest } = p; return rest; }
      if (p.type === 'tool_call' && p.input && typeof p.input === 'object' && 'sql' in (p.input as object)) return { ...p, input: { ...(p.input as object), sql: '' } };
      return p;
    });
  }
  if (Array.isArray(m.steps)) out.steps = (m.steps as Record<string, unknown>[]).map(scrubStep);
  return out;
}

function scrubStep(s: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...s };
  if (typeof s.sql === 'string') out.sql = '';
  if (typeof s.revisedSql === 'string') out.revisedSql = '';
  if (Array.isArray(s.candidates)) out.candidates = (s.candidates as Record<string, unknown>[]).map((c) => (typeof c.sql === 'string' ? { ...c, sql: '' } : c));
  return out;
}
