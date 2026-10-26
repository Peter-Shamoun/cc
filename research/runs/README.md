# Benchmark run records

Scored results of every subagent benchmark run, copied from `.ccsim/agent_sessions/<run>/` (which is gitignored and
lives only on the machine that ran it). `results.json` has one record per question × config × repeat (correct, why,
final SQL, answer text, tool calls, cost); `results.md` is the run's summary table. The per-session transcripts and
run manifests are not kept: the manifests are rebuilt by `bench/subagent_queue.py` from the code and the seeded data.

Which write-up each run belongs to:

| run | write-up |
|---|---|
| screen1-haiku, screen1-sonnet, pilot-1, smoke-allon, train_b0, heldout1 | earlier screening and pilots |
| s1, s1b, s1r2 | `research/stage1_results.md` |
| s2 | `research/stage2_3_results.md` |
| s4_test | `research/stage4_sealed_test.md` (the sealed TEST split, run once) |
| nm_val, nm_smoke_train | `research/named_metrics_results.md` |
| clar_val, clar_probe | `research/clarify_results.md` |
