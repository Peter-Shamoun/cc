# cc agent-analyzer grid benchmark protocol (v1)

You're one of 100+ agents benchmarking agent-analyzer parameter combinations for the cc repo (https://github.com/Peter-Shamoun/cc). The agent analyzer is a text-to-SQL analyst over lab-billing EDI data in DuckDB `sem.*`, queried through `ccsim/query.py`. Every cell runs the SAME 20 questions so the effects of each factor can be pooled (fractional factorial design; see grid_design_v1.json). Your prompt gives your CELL id and CONFIG.

Time-box: 40 minutes. Branch off `claude/trusting-heisenberg-8xvazk`. Write ONLY `research/grid/<CELL>.json` and `research/grid/<CELL>.md`, then push and open a PR into `claude/trusting-heisenberg-8xvazk`. If you're short on time, score what you finished and report a partial n.

## Factors
- **generation**
  - `direct_sql`: write the SQL directly.
  - `metric_spec`: first write a structured spec (metric, population, filters, grain, time window, ties and rounding), then translate it to SQL.
  - `decompose_cte`: build CTE sub-steps and check each intermediate (row counts, key uniqueness, sums).
- **fewshot=1**: include the 3 most similar solved benchmark questions, with their reference SQL, using TF-IDF or BM25 similarity. Take them only from questions outside the dev set, and exclude the same parametric family or template as well as the held-out 27.
- **explore=1**: probe distinct values, ranges, null rates and join cardinalities before writing the final SQL.
- **candidates3=1**: write 3 diverse candidates, execute all of them, and pick by agreement between results plus sanity checks.
- **verifier=1**: run a fresh-context critic pass that checks the semantics, population, units and invariants, then revise once.
- **repair**
  - `repair=1`: up to 2 repairs on errors, empty results or suspicious results.
  - `repair=0`: the result of the first execution stands, and an error counts as a fail.
- **assumptions=1**: list the interpretations (population, date basis, units, ties), pick the standard revenue-cycle analyst one, and state it with the answer.
- **multiagent=1**: split into a planner and an SQL-writer working in separate contexts (subagents if available, otherwise strictly separated passes with no shared scratch beyond the plan).

## Setup
Build the frozen generator 3.3 dataset the way the benchmark and tests do (smallest benchmark config), and use a non-escalated reader session the way `bench/eval_subagents.py` does. YOU are the analyst model: run the protocol yourself, using real SQL executions.

## Dev set (identical for every cell)
Take all benchmark questions except the 27 held-out ones (`research/heldout_results_1.md`) and anything tagged `grokbot-r1`. Sort them by sha256(question_id) as hex and take the first 20. Put the 20 ids in your JSON.

## No contamination (strict)
Until all 20 answers are written to your JSON and committed, do NOT read:
- the pitfalls glossary
- the answer keys, reference SQL or gold answers for dev questions
- `screening_results` or `heldout` failure analyses
- other grid cells' results

Use only the basic glossary and the data dictionary or profile, plus whatever CONFIG adds. After committing your answers, score them with the repo's own judge or checker.

## Output JSON
```
{
  "cell": ..., "config": {...}, "model": "<your model name>", "dev_ids": [...],
  "results": [
    {"qid": ..., "answer": ..., "final_sql": ..., "sql_execs": n, "repairs": n, "seconds": s, "pass": true|false}
  ],
  "accuracy": k/n, "n": n, "mean_sql_execs": x, "notes": "..."
}
```

The .md file gives a short summary and the failure modes. Report the PR URL and the accuracy.
