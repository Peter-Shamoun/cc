/**
 * 1.23.0 multi-question clarification (CONTRACT.md §6.1), modelled on Claude's ask-the-user form: 1–4 questions, each
 * with a header of 12 characters or fewer, 2–4 described options, optional multiSelect, plus "Other" (free text) and
 * skip on every question. Mirrors `api/clarify.py`; both run `api/contract/clarify_cases.json`.
 *
 * - `validateClarifyAnswers` is the server's 422 rule: run it before sending so the form can explain the problem.
 * - `clarifySummary` is the exact line the analyzer gets after `Clarification:`. A client can use it as the visible
 *   text of the user's reply turn so the transcript reads the same as what was asked.
 * - `clarifyRequest` builds `ChatRequest.resume` for the answers.
 */
import type { ClarifyAnswer, ClarifyQuestion, ID } from '../api/contract';

export const CLARIFY_LIMITS = { questions: 4, minOptions: 2, maxOptions: 4, header: 12, other: 500 } as const;
export const CLARIFY_SKIPPED = 'no preference';

/** One line, trimmed, at most `limit` code points (like Python's `str[:limit]`). */
function clean(value: unknown, limit: number): string {
  const one = String(value ?? '').replace(/\s+/g, ' ').trim();
  return Array.from(one).slice(0, limit).join('').trimEnd();
}

/** null when valid, else the same user-safe message the server's 422 carries. */
export function validateClarifyAnswers(questions: readonly ClarifyQuestion[], answers: readonly ClarifyAnswer[]): string | null {
  if (answers.length > CLARIFY_LIMITS.questions) return 'Too many answers.';
  const byId = new Map(questions.map((q) => [q.id, q]));
  const seen = new Set<ID>();
  for (const a of answers) {
    const q = byId.get(a.questionId);
    if (!q) return "An answer refers to a question that wasn't asked.";
    if (seen.has(q.id)) return 'A question was answered twice.';
    seen.add(q.id);
    const choices = a.choiceIds ?? [];
    const other = clean(a.otherText, CLARIFY_LIMITS.other);
    if (a.skipped) {
      if (choices.length || other) return "A skipped question can't also have an answer.";
      continue;
    }
    if (!choices.length && !other) return 'Pick an option, type an answer, or skip the question.';
    const valid = new Set(q.options.map((o) => o.id));
    if (new Set(choices).size !== choices.length || !choices.every((c) => valid.has(c))) {
      return "An answer picks an option that wasn't offered.";
    }
    if (!q.multiSelect && choices.length + (other ? 1 : 0) > 1) return 'That question takes one answer.';
  }
  return null;
}

/** `Header: value; Header: value` in question order; unanswered or skipped reads `no preference`. */
export function clarifySummary(questions: readonly ClarifyQuestion[], answers: readonly ClarifyAnswer[]): string {
  const byQ = new Map(answers.map((a) => [a.questionId, a]));
  return questions.map((q) => {
    const a = byQ.get(q.id) ?? { questionId: q.id, skipped: true };
    const picked = new Set(a.choiceIds ?? []);
    const values = q.options.filter((o) => picked.has(o.id)).map((o) => o.label);
    const other = clean(a.otherText, CLARIFY_LIMITS.other);
    if (other) values.push(other);
    return `${q.header}: ${values.length && !a.skipped ? values.join(', ') : CLARIFY_SKIPPED}`;
  }).join('; ');
}

/** `ChatRequest.resume` for a multi-question part. An empty `answers` skips every question. */
export function clarifyRequest(clarificationId: ID, answers: readonly ClarifyAnswer[]): { clarificationId: ID; answers: ClarifyAnswer[] } {
  return { clarificationId, answers: answers.map((a) => ({ ...a })) };
}
