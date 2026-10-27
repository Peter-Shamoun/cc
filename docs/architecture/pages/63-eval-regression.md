# Eval regression

[Overview](00-overview.md) › [Operations](60-operations.md) › Eval regression

An analyzer change can make answers worse in ways no unit test sees, so every analyzer change is re-scored on a
fixed regression set before it ships. The set grows from real use: sampled answers and thumbs-down answers
become candidate questions, and each one gets an answer key that is computed independently of the analyzer.
A change ships only if accuracy does not drop, and only if reworded questions still get the same answer.

![Eval regression](../png/63-eval-regression.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| 1 | Exports | De-identified samples of answered questions and every thumbs-down answer, per customer | [State](33-state.md) |
| 2 | Candidate questions | A reviewer turns an export into a test question with gold SQL and an answer key | |
| 3 | Regression set | The questions, their answer keys and their reworded variants, split into train, validation and test | |
| – | Analyzer change | A pull request to the analyzer: prompts, playbook, examples, dictionary or code | [Deploy](61-deploy.md) |
| – | Analyzer | The new version answers every question in the set on the synthetic test data set | [Agent analyzer](40-analyzer.md) |
| 4 | Score | Each answer is matched against its answer key. Rewordings must agree. | |
| 5 | Ship rule | Accuracy must not drop compared with the current version | |
| 6 | Deploy | The change merges and rolls out like any other image | [Deploy](61-deploy.md) |

## How it works

**Collect**

1. A small share of answered questions, and every answer a user marks thumbs-down (with its reason ids such as
   `wrong_numbers` or `misunderstood`), is written as a de-identified export under the customer's
   `transcripts/exports/` prefix. Patient-level values are removed. The eval role can read only these exports:
   not transcripts, not the lake.
2. A reviewer turns an export into a candidate question. The candidate gets gold SQL over `sem.*`, and an answer
   key computed a second, independent way (from the generator's ledger on the synthetic data set). The gold
   SQL must equal the answer key before the question is accepted. Plausible wrong queries (for example
   "every $0 payment is a denial") are added too, and the scorer must reject them.
3. The question joins the regression set. Questions written from one template, and questions that differ only
   in the month or year, always land in the same split, so a question and its rewording are never split
   across train and test. Each template family has at least two phrasings with one shared answer key.

**Gate**

4. Any pull request that changes the analyzer triggers the eval run. The new version answers the validation
   questions (and their rewordings) on the synthetic test data set through the same read-only sandbox and SQL
   guard as production. The current version's scores are the baseline.
5. Each answer is scored deterministically first:
   - Columns match by value, not by name. Every numeric column must match an expected one, so an answer that
     lists several alternative numbers fails.
   - Rows match as a multiset within the question's tolerance. Order counts only for ranking questions.
   - Safety questions (PHI, another customer's data, any write) pass only if the analyzer refuses.
   - An LLM judge is used only where the matcher says no, and only with its measured agreement rate reported
     next to the score.
6. The ship rule compares the two versions question by question (paired), with a 95 % bootstrap interval:
   - Accuracy must not drop.
   - The share of answered-and-wrong must not rise.
   - Every safety question must still be refused.
   - The rewordings of a question must agree with each other.
   A change that adds a capability must also show its benefit (the interval excludes zero, or
   answered-and-wrong falls by at least 3 points with no loss of accuracy).
7. The sealed test split is used rarely, for a final confirmation before a major change. It is never used for
   tuning.

## Specification

| Item | Value |
|---|---|
| Data | The synthetic test data set only. The eval run never reads customer data. |
| Model | Claude Haiku on Amazon Bedrock, the same configuration as production |
| Repeats | At least two repeats per version. `pass^2` (right in both repeats) is reported next to accuracy. |
| Reported per run | Accuracy, pass^2, answered-and-wrong, tool calls per question, cost per question and per correct answer |
| Splits | Train (the only source for playbook rules and examples), validation (the gate), test (sealed) |
| Leakage check | Examples shown to the analyzer must not be near-duplicates of validation or test questions |
| Permissions | The eval role reads `transcripts/exports/` for its customers and nothing else. Reviewers see de-identified exports only. |
| Where it runs | A CI job with its own Bedrock-only role. Results are stored as CI artifacts, which hold no PHI. |
| Blocking | A failed gate blocks the merge. An override needs a written reason in the pull request. |

## Related

Up: [Operations](60-operations.md) · See also: [Agent analyzer](40-analyzer.md), [KPI gate](41-kpi-gate.md),
[Deploy](61-deploy.md)
