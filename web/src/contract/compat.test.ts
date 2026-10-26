/**
 * Drift gate between the hand-written client types (src/api/contract.ts, src/lib/contract.ts) and the types
 * generated from the schema (src/contract/v1.gen.ts, `python -m api.contract.gen_ts`).
 *
 * The type assertions below are checked by `tsc -b` (npm run build / typecheck): if the schema and the
 * hand-written types disagree in either direction, the build fails. The schema is the source of truth.
 */
import { describe, expect, it } from 'vitest';
import type * as G from './v1.gen';
import * as Gv from './v1.gen';
import type * as H from '../api/contract';
import type * as CA from '../adapters/conversationsApi';
import { CONTRACT_VERSION, EVENT_TYPES, PART_TYPES } from '../api/contract';

/** true iff A and B are mutually assignable (non-distributive). */
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
function same<A, B>(ok: Same<A, B>): Same<A, B> { return ok; }

// shared shapes
same<H.Usage, G.Usage>(true);
same<H.ColumnSummary, G.ColumnSummary>(true);
same<H.StepError, G.StepError>(true);
same<H.ModelRef, G.ModelRef>(true);
same<H.Column, G.Column>(true);
same<H.ColumnType, G.ColumnType>(true);
same<H.Sensitivity, G.Sensitivity>(true);
// parts
same<H.TextPart, G.TextPart>(true);
same<H.ReasoningPart, G.ReasoningPart>(true);
same<H.StatusPart, G.StatusPart>(true);
same<H.ToolCallPart, G.ToolCallPart>(true);
same<H.TablePart, G.TablePart>(true);
same<H.FilePart, G.FilePart>(true);
same<H.ImagePart, G.ImagePart>(true);
same<H.ChartPart, G.ChartPart>(true);
same<H.CodePart, G.CodePart>(true);
same<H.CalloutPart, G.CalloutPart>(true);
same<H.ArtifactPart, G.ArtifactPart>(true);
same<H.ErrorPart, G.ErrorPart>(true);
same<H.AbstainPart, G.AbstainPart>(true);
same<H.ClarificationPart, G.ClarificationPart>(true);
same<H.Part['type'], G.Part['type']>(true);
// events
same<H.MessageStartEvent, G.MessageStart>(true);
same<H.StatusEvent, G.Status>(true);
same<H.StepSqlRunningEvent, G.StepSqlRunning>(true);
same<H.StepSqlResultEvent, G.StepSqlResult>(true);
same<H.StepRepairAttemptEvent, G.StepRepairAttempt>(true);
same<H.StepCheckEvent, G.StepCheck>(true);
same<H.StepExploreEvent, G.StepExplore>(true);
same<H.StepCandidatesEvent, G.StepCandidates>(true);
same<H.EscalationEvent, G.Escalation>(true);
same<H.TextStartEvent, G.TextStart>(true);
same<H.TextDeltaEvent, G.TextDelta>(true);
same<H.TextEndEvent, G.TextEnd>(true);
same<H.ReasoningDeltaEvent, G.ReasoningDelta>(true);
same<H.ReasoningEndEvent, G.ReasoningEnd>(true);
same<H.CodeDeltaEvent, G.CodeDelta>(true);
same<H.PartEvent, G.PartEvent>(true);
same<H.PartPatchEvent, G.PartPatch>(true);
same<H.MessageEndEvent, G.MessageEnd>(true);
same<H.HeartbeatEvent, G.Heartbeat>(true);
same<H.StreamEvent['type'], G.StreamEvent['type']>(true);
// requests
same<H.ChatRequest, G.ChatRequest>(true);
same<H.HttpError, G.HttpError>(true);

// conversation REST (1.3.0): the history client's shapes match the schema
same<CA.ConversationSummary, G.ConversationSummary>(true);
same<CA.ConversationPage, G.ConversationPage>(true);
same<CA.TranscriptInfo, G.TranscriptsInfo>(true);
same<Awaited<ReturnType<CA.ConversationsApi['rename']>>, G.RenameResponse>(true);
same<CA.ConversationDetail['id'], G.Conversation['id']>(true);

describe('contract drift (schema -> TS)', () => {
  it('versions agree', () => {
    expect(CONTRACT_VERSION).toBe(Gv.CONTRACT_VERSION);
  });
  it('event and part type lists agree with the schema', () => {
    expect([...EVENT_TYPES].sort()).toEqual([...Gv.KNOWN_EVENT_TYPES].sort());
    expect([...PART_TYPES].sort()).toEqual([...Gv.KNOWN_PART_TYPES].sort());
  });
});
