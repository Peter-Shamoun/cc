In the analyzer (the `cc-strands` core branch), the SQL guard's row cap currently applies to the final answer too,
so list answers are silently cut off. Change it so that:

- the 1,000-row cap applies only to the agent's exploratory queries;
- the final answer returns the full result;
- the model is told how the two modes work, so its responses account for them.

## The bug, with evidence

- `bench/guard.py` `guard()` wraps every query as `SELECT * FROM (<sql>) AS agent_query LIMIT {row_limit}`. An inner
  `LIMIT 5000` written by the agent is overridden by the outer limit.
- `analyzer/config.py`: `row_limit = 1000`, `rows_shown_to_model = 20`, `time_limit_seconds = 25.0`.
- `analyzer/tools.py` `_execute_guarded()` runs `guard(sql, self.config.row_limit, self.escalated)` for every query.
  The digest's `truncated` flag means "more than `rows_shown_to_model`", not "hit the row cap". Nothing tells the
  model or the user that a result stopped at the cap.
- `analyzer/b1_commands.py:135`: the final re-run, `self.tools.run_sql(final.sql, charge=False, include_all_rows=True)`,
  goes through the same guard and cap. `api/mapping.py` `answer_parts()` then builds the table and the CSV download from
  those rows, and `rowCount` reports 1,000.
- So "list the top 5,000 payers" returns 1,000 rows in the table and CSV, and presents them as complete.

## What to change

1. **Flag capped exploratory results.** Keep `row_limit = 1000` for exploratory queries (`sql`, `lookup`, profiles).
   When a query returns exactly `row_limit` rows, add `"hit_row_limit": true` to the digest the model sees. Keep the
   existing `truncated` (more than 20 rows shown) as it is.
2. **Give the final answer its own, much higher limit.** Add `final_row_limit` to `analyzer/config.py`, default
   100_000, as a safety ceiling for memory and export size rather than a sampling cap. Use it only for the final
   re-run in `b1_commands.py`. `guard()` already takes `row_limit`, but it currently rejects values above
   `MAX_ROW_LIMIT = 10_000`, so allow `final_row_limit` through for the final re-run only. Keep everything else
   identical for the final re-run: the guard's other checks, `check_plan_cost`, and the time limit.
3. **Never present a ceiling-capped final result as complete.** If the final result still hits `final_row_limit`, the
   answer must say so: run a `COUNT(*)` of the final query, and have the table show "first N of M", or follow the
   contract's existing way of marking a partial result. Check `api/CONTRACT.md` before adding a field.
4. **Tell the model.** In the briefing (`analyzer/briefing.py`, and the B1 knowledge or CLI reference if the limits
   are described there), state:
   - Exploratory query results are capped at 1,000 rows and marked `hit_row_limit` when capped. Never state a count,
     total or "all" from a capped result; use `COUNT(*)`, `SUM` or another aggregate.
   - The final answer's SQL is re-run with every row, up to 100,000, into the user's table and CSV. Write it to produce
     the complete answer the question asks for (all 5,000 payers), not a sample.
   - You only see the first 20 rows of any result. Summarise in the text (totals, the top few, the pattern) and refer
     to the table, rather than listing rows from memory.
5. **Leave alone:** the join-cost check (`check_plan_cost`, it refuses rather than truncates), the 25 s per-query and
   60 s per-question time limits, and the other guard checks.

## Tests

- A final answer whose SQL returns 5,000 rows produces a table and CSV with all 5,000 rows, and `rowCount` is 5,000.
- An exploratory query returning more than 1,000 rows returns 1,000 with `hit_row_limit: true`. One returning 999 rows
  has no flag.
- A final result above `final_row_limit` is labelled as partial with the true total, not presented as complete.
- `guard()` still rejects a `row_limit` above 10,000 unless it is the configured final-answer limit.
- Existing guard, join-cost and time-limit tests still pass.

Run the analyzer and API test suites and report the results. Don't change the scored benchmark sets.
