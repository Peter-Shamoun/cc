# Clarification (`ask`, multi-question): B1 vs B1 + ask on VAL and the clarify probe set. Adopted

Runs `clar_val` (validation split, 129 questions) and `clar_probe` (`bench/clarify_probe_questions.yaml`, 16 ambiguous +
8 unanswerable questions outside the frozen splits), 2026-10-26, snapshot `4ae0001be5a8`, Haiku, effort low, two
repeats per arm, both arms interleaved in one manifest per run, `claude -p` workers (`bench/claude_print_runner.py
--workers 16`). Configs: `research/clarify_configs.json` (B1 as in `analyzer/bundles.py`; B1_ask adds
`enable_ask`). 612 runs. Analysis: `python research/clarify_paired_analysis.py` (paired by question, 10,000
bootstrap resamples). The sealed TEST split was not run.

## What was added

* `ask` takes one plain question (unchanged) or `--questions`: 1-4 questions, each a header (<= 12 chars), a question
  and 2-4 options with label + description, optional `multiSelect` (`analyzer/tools.py` `clarification_request`,
  limits mirror `api/clarify.py`). Ids `q1..`/`o1..`, so `api.mapping.outcome_parts` shows contract 1.23.0's
  clarification form exactly as asked. The user's form reply comes back as the next turn's question plus one line
  `Clarification: Header: choice; ...` (`api/app.py` resume, `api/clarify.py` `summary`).
* When ask is on, the briefing gets a when-to-ask block (`analyzer/protocols.py` `clarify_block`): answer clear
  questions; ask only when two readings give materially different numbers and neither definitions nor playbook settle
  it, or a required scope (measure, comparison) is missing; if a default exists and readings barely differ, answer and
  state the assumption; if the data cannot answer it, refuse; ask everything at once; apply a returned
  `Clarification:` line and do not ask again. Written from TRAIN-style questions and general principles; no VAL/TEST
  question text or keys were read while designing it.

## Result

| group (paired by question) | metric | B1 | B1 + ask | diff, 95% CI |
|---|---|---|---|---|
| clear VAL (128 q) | accuracy | 85.2% | 84.4% | -0.8 pp [-5.1, +3.1] |
| clear VAL | answered and wrong | 13.3% | 14.1% | +0.8 pp [-2.7, +4.7] |
| clear VAL | asked | 0.0% | 1.2% | +1.2 pp [0.0, +3.1] |
| VAL PHI refusal (1 q) | refused | 100% | 100% | 0 |
| ambiguous probe (16 q) | asked (correct) | 0.0% | 90.6% | +90.6 pp [+75.0, +100] |
| ambiguous probe | answered (with an assumption) | 96.9% | 9.4% | -87.5 pp [-100, -71.9] |
| unanswerable probe (8 q) | refused or asked (correct) | 50.0% | 100% | +50.0 pp [+18.8, +81.2] |
| unanswerable probe | answered and wrong | 25.0% | 0.0% | -25.0 pp [-62.5, 0.0] |

| arm | VAL $/question | VAL $/correct | probe $/correct |
|---|---|---|---|
| B1 | 0.0070 | 0.0082 | 0.0404 |
| B1 + ask | 0.0071 | 0.0084 | 0.0065 |

Clear VAL: better on 10 questions, worse on 8. B1 + ask asked on 3 of 256 clear runs (2 questions) and refused one;
B1 here (85.3% all VAL) matches the earlier B1 runs (85.7%, `research/named_metrics_results.md`). Asks used the form:
35 asks, all with `questions`, 1.7 questions per ask on average. The 3 ambiguous runs that answered stated their
reading.

**Decision: adopted.** Clear-question accuracy holds (-0.8 pp, CI includes zero, cost per correct +2%) and
ambiguous/unanswerable handling improves by a wide margin. `enable_ask` is on in B1 (`analyzer/bundles.py`, parameter
#21), so the shipped `claude -p` route (`api/claude_code_cli_driver.py`, bundle B1) can end a turn in the
multi-question clarification form.

Caveats: the probe set and the when-to-ask instruction were written by the same author, so the ambiguous result is
optimistic; B1 answering an ambiguous question with a stated assumption is not always wrong for a user, and the probe
scoring counts it wrong. The clear-accuracy CI still admits a drop of up to ~5 pp; two repeats on 128 questions cannot
resolve 1-2 pp. VAL was already used to tune B1. The second turn (answer after a `Clarification:` line) is tested with
a scripted model only, not measured live.
