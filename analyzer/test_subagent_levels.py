"""Subagent-route parameter levels: briefing variants, CLI gates, repair, lint, trigger, examples, playbook."""

import contextlib
import io
import json
from pathlib import Path

import pytest

from analyzer import cli
from analyzer.config import AnalyzerConfig
from analyzer.harness import build_subagent_prompt
from analyzer.trigger import LINT_REPAIR_CONFIDENCE_TRIGGER, evaluate_trigger

PAID_BY_PAYER = "SELECT payer_name, sum(paid_amount) AS paid FROM sem.remit_claims GROUP BY 1 ORDER BY 1"
PAID_BY_PAYER_CURRENT = ("SELECT payer_name, sum(paid_amount) AS paid FROM sem.remit_claims "
                         "WHERE is_current_adjudication GROUP BY 1 ORDER BY 1")
QUESTION = {"id": "q_levels", "question": "How much has each payer paid us on current adjudications?"}


def _session(snapshot, technique, question=QUESTION, model="haiku"):
    from analyzer.harness import stamp_session
    from bench.agent_session import create_session
    session = create_session(snapshot, question, technique, model, "pytest-levels")
    stamp_session(session)
    return session


def _run(session, *args):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(["--session", str(session), *args])
    out = buffer.getvalue()
    return code, (json.loads(out) if code == 0 and out.strip().startswith("{") else out)


def _train_case(snapshot):
    from analyzer.smoke_bundles import smoke_questions
    return smoke_questions(snapshot, 1)[0]


# ---- briefing variants -------------------------------------------------------------------------------------

def test_b0_briefing_is_the_calibrated_text(bench_snapshot, sessions_root):
    """Every new block is empty at its off level: the B0 briefing is the pre-bundle format, byte for byte."""
    session = _session(bench_snapshot, {"effort": "low", "self_correct": "none", "max_repairs": 0})
    prompt = build_subagent_prompt(session)
    from analyzer.prompts import procedure_text, refusal_text
    from analyzer.tools import _glossary_body
    config = AnalyzerConfig.from_technique({"effort": "low", "self_correct": "none", "max_repairs": 0})
    cli_prefix = f"python3 -m analyzer.cli --session {session}"
    expected = (
        "You are CC, an analytics assistant for one clinical lab's insurance claims (837P) and remittances (835).\n"
        "Answer the user's question by running only the commands below with the Bash tool. Do not read, list, or open any file, "
        "and do not run any other program. The answer is scored by re-running the SQL you submit, so that SQL must compute the "
        "answer from the data.\n\n" + refusal_text(config) + "\n" + procedure_text(config) + "\n"
        + "Business definitions:\n" + _glossary_body(config).strip() + "\n\n"
        + "Commands:\n" + "\n".join([f"{cli_prefix} list", f"{cli_prefix} describe sem.claims",
                                     f"{cli_prefix} sample sem.claims", f'{cli_prefix} glossary "denial"',
                                     f'{cli_prefix} sql "SELECT ..."',
                                     f'{cli_prefix} escalate "<why sem.* cannot answer>"'])
        + "\n\nFinish with exactly one of:\n" + f'{cli_prefix} answer --sql "SELECT ..." --text "<one-paragraph answer>"\n'
        + f'{cli_prefix} refuse "<reason>"' + "\n\nYou may make at most 12 commands besides the final one. "
        + f"Then reply with the single word DONE.\n\nUser question: {QUESTION['question']}\n")
    assert prompt == expected


def test_minimal_style_and_rewritten_descriptions(bench_snapshot, sessions_root):
    session = _session(bench_snapshot, {"context_style": "minimal", "rewritten_tool_descriptions": True,
                                        "max_tool_calls": 6})
    prompt = build_subagent_prompt(session)
    assert f"{session} list" not in prompt and "glossary \"denial\"" not in prompt
    assert "Submit one SELECT that computes the answer." in prompt and "Work in these steps" not in prompt
    assert "# Before you write SQL on a view" in prompt and "at most 6 commands" in prompt


def test_invariants_glossary_has_invariants_without_pitfalls(bench_snapshot, sessions_root):
    prompt = build_subagent_prompt(_session(bench_snapshot, {"glossary": "invariants"}))
    assert "Invariants:" in prompt and "is_current_adjudication" in prompt
    assert "Rates: use 100.0" not in prompt and "Ranked questions" not in prompt


def test_protocol_blocks_reuse_the_approaches(bench_snapshot, sessions_root):
    prompt = build_subagent_prompt(_session(bench_snapshot, {
        "state_assumptions": True, "restate_question": True, "plan_mode": "plan_and_replan",
        "generation_mode": "decompose_cte", "explore_probes": True, "candidate_shape": "query_plan",
        "report_confidence": True}))
    assert "Axes and the readings that have to be listed before any SQL" in prompt          # A09 checklist
    assert "restatement: restate the question as grain, population, time_basis" in prompt    # A05 plan fields
    assert "Explore-first protocol (A06)" in prompt and " probe \"SELECT ...\"" in prompt     # A06 probes
    assert "Checks on each CTE" in prompt and "population, join, metric" in prompt           # A07
    assert "Strategy: query plan." in prompt                                                 # A04 shape
    assert "--confidence <high|medium|low>" in prompt
    assert "Probe and interpret commands do not count." in prompt


def test_compiler_block_names_the_spec_commands(bench_snapshot, sessions_root):
    prompt = build_subagent_prompt(_session(bench_snapshot, {"generation_mode": "compiler_sql_fallback"}))
    assert " spec '<json>'" in prompt and "answer --spec" in prompt and "cannot_express" in prompt


# ---- tool design and budgets (#19, #20) --------------------------------------------------------------------

def test_digest_results_and_rows_shown(bench_snapshot, sessions_root):
    sql = "SELECT claim_key, charge_amount FROM sem.claims WHERE is_latest_submission ORDER BY claim_key LIMIT 60"
    code, digest = _run(_session(bench_snapshot, {"digest_results": True}), "sql", sql)
    assert code == 0 and len(digest["rows"]) == 5 and digest["row_count"] == 60 and digest["truncated"]
    assert digest["column_summary"]["claim_key"]["distinct"] == 60
    assert {"min", "max", "sum", "nulls"} <= set(digest["column_summary"]["charge_amount"])
    code, raw = _run(_session(bench_snapshot, {"rows_shown_to_model": 50}), "sql", sql)
    assert len(raw["rows"]) == 50 and "column_summary" not in raw


def test_actionable_errors_name_the_columns(bench_snapshot, sessions_root):
    sql = "SELECT payer_nam FROM sem.claims"
    code, raw = _run(_session(bench_snapshot, {}), "sql", sql)
    assert code == 1 and "Next step" not in raw
    code, actionable = _run(_session(bench_snapshot, {"actionable_errors": True}), "sql", sql)
    assert code == 1 and "Next step" in actionable and "closest to payer_nam: payer_name" in actionable
    code, missing_table = _run(_session(bench_snapshot, {"actionable_errors": True}), "sql", "SELECT * FROM sem.nope")
    assert code == 1 and "sem.remit_claims" in missing_table


def test_dictionary_derivations_and_enum_profile(bench_snapshot, sessions_root):
    code, dictionary = _run(_session(bench_snapshot, {"data_dictionary": True, "dictionary_detail": "derivations"}),
                            "dictionary", "sem.remit_claims")
    derivations = {item["metric"]: item for item in dictionary["relations"][0]["derivations"]}
    assert derivations["denied_claims"]["expression"].startswith("COUNT(DISTINCT claim_payer_key)")
    assert derivations["denial_rate"]["expression"] == "denied_claims / remitted_claims"
    code, profile = _run(_session(bench_snapshot, {"data_profile": True, "profile_detail": "enums"}),
                         "profile", "sem.remit_claims")
    columns = {column["name"]: column for column in profile["profiles"][0]["columns"]}
    assert "values" in columns["payer_class"] and "paid_amount" not in columns


# ---- lint, reconciliation, repair, trigger (#13, #22, #23, #26) ---------------------------------------------

def test_lint_shows_after_sql(bench_snapshot, sessions_root):
    code, result = _run(_session(bench_snapshot, {"domain_lint": True}), "sql", PAID_BY_PAYER)
    assert code == 0 and result["lint"][0].startswith("missing_current_adjudication")
    code, clean = _run(_session(bench_snapshot, {"domain_lint": True}), "sql", PAID_BY_PAYER_CURRENT)
    assert code == 0 and "lint" not in clean


def test_lint_feeds_repair_until_the_budget_is_spent_then_triggers(bench_snapshot, sessions_root):
    session = _session(bench_snapshot, {"domain_lint": True, "self_correct": "errors_only", "max_repairs": 2,
                                        "report_confidence": True})
    assert _run(session, "answer", "--sql", PAID_BY_PAYER)[0] == 1                     # --confidence missing
    for attempt in (1, 2):
        code, out = _run(session, "answer", "--sql", PAID_BY_PAYER, "--confidence", "high")
        assert code == 1 and f"repair {attempt} of 2" in out and "is_current_adjudication" in out
    code, recorded = _run(session, "answer", "--sql", PAID_BY_PAYER, "--confidence", "high")
    assert code == 0 and recorded["unresolved_findings"]
    final = json.loads((session / "final.json").read_text())
    assert final["repairs_used"] == 2 and final["confidence"] == "high"
    fired = evaluate_trigger(session, LINT_REPAIR_CONFIDENCE_TRIGGER)
    assert fired["escalate"] and set(fired["fired"]) == {"lint_after_repair", "repairs_exhausted"}


def test_clean_answer_does_not_trigger_unless_confidence_is_low(bench_snapshot, sessions_root):
    session = _session(bench_snapshot, {"domain_lint": True, "self_correct": "errors_only", "max_repairs": 2,
                                        "report_confidence": True})
    assert _run(session, "answer", "--sql", PAID_BY_PAYER_CURRENT, "--confidence", "low")[0] == 0
    assert evaluate_trigger(session, LINT_REPAIR_CONFIDENCE_TRIGGER)["fired"] == ["low_confidence"]
    unanswered = _session(bench_snapshot, {})
    assert evaluate_trigger(unanswered, LINT_REPAIR_CONFIDENCE_TRIGGER)["fired"] == ["no_answer"]
    refused = _session(bench_snapshot, {})
    assert _run(refused, "refuse", "patient names")[0] == 0
    assert not evaluate_trigger(refused, LINT_REPAIR_CONFIDENCE_TRIGGER)["escalate"]


def test_b0_never_bounces_an_answer(bench_snapshot, sessions_root):
    session = _session(bench_snapshot, {"effort": "low", "self_correct": "none", "max_repairs": 0,
                                        "domain_lint": True})
    assert _run(session, "answer", "--sql", PAID_BY_PAYER)[0] == 0


def test_result_signals_bounce_an_empty_result(bench_snapshot, sessions_root):
    session = _session(bench_snapshot, {"result_checks": True, "verify": True, "self_correct": "errors+result_signals",
                                        "max_repairs": 1})
    empty = "SELECT count(*) AS n FROM sem.claims WHERE false GROUP BY claim_key"
    code, out = _run(session, "answer", "--sql", empty)
    assert code == 1 and "no rows" in out
    assert _run(session, "answer", "--sql", empty)[0] == 0
    assert evaluate_trigger(session, ("blocking_warning",))["escalate"]


def test_reconciliation_feeds_repair(bench_snapshot, sessions_root):
    session = _session(bench_snapshot, {"reconciliation_checks": True, "self_correct": "errors_only", "max_repairs": 1},
                       {"id": "q_check", "question": "What were our check totals by payer?"})
    code, out = _run(session, "answer", "--sql", PAID_BY_PAYER_CURRENT)
    assert code == 1 and "payment_balancing" in out


# ---- gates (#5, #8, #9, #10, #11, #25) ----------------------------------------------------------------------

def test_interpret_gate_validation_and_replan(bench_snapshot, sessions_root):
    session = _session(bench_snapshot, {"restate_question": True, "state_assumptions": True,
                                        "plan_mode": "plan_and_replan", "domain_lint": True,
                                        "self_correct": "errors_only", "max_repairs": 2})
    assert _run(session, "sql", PAID_BY_PAYER_CURRENT)[0] == 1
    code, out = _run(session, "interpret", json.dumps({"restatement": {"grain": "payer"}}))
    assert code == 1 and "missing assumptions" in out
    record = {"restatement": {"grain": "one row per payer", "population": "current adjudications",
                              "time_basis": "none", "measure": "paid", "units": "dollars"},
              "assumptions": [], "steps": ["sum paid_amount by payer on current adjudications"]}
    assert _run(session, "interpret", json.dumps(record))[0] == 0
    assert _run(session, "sql", PAID_BY_PAYER_CURRENT)[0] == 0
    assert _run(session, "answer", "--sql", PAID_BY_PAYER)[0] == 1                      # lint repair
    code, out = _run(session, "sql", PAID_BY_PAYER_CURRENT)
    assert code == 1 and "replan" in out
    assert _run(session, "interpret", json.dumps(record))[0] == 0
    assert _run(session, "answer", "--sql", PAID_BY_PAYER_CURRENT)[0] == 0
    assert cli.latest_interpretation(session) == record


def test_compiler_gate_and_spec_answer(bench_snapshot, sessions_root):
    session = _session(bench_snapshot, {"generation_mode": "compiler_sql_fallback"})
    assert "cannot_express" in _run(session, "sql", PAID_BY_PAYER_CURRENT)[1]
    spec = {"population": "current_adjudications", "metrics": [{"alias": "n", "agg": "count"}]}
    code, result = _run(session, "spec", json.dumps(spec))
    assert code == 0 and result["row_count"] == 1 and "is_current_adjudication" in result["compiled_sql"]
    code, _ = _run(session, "answer", "--spec", json.dumps(spec))
    assert code == 0 and json.loads((session / "final.json").read_text())["via"] == "compiler"
    fallback = _session(bench_snapshot, {"generation_mode": "compiler_sql_fallback"})
    code, out = _run(fallback, "spec", json.dumps({"status": "cannot_express", "because": "needs a window"}))
    assert code == 1 and out.startswith("ERROR: cannot_express")
    assert _run(fallback, "answer", "--sql", PAID_BY_PAYER_CURRENT)[0] == 0


def test_mandatory_lookup_gate(bench_snapshot, sessions_root):
    session = _session(bench_snapshot, {"enable_lookup": True, "lookup_mandatory_before_literals": True})
    sql = "SELECT count(*) FROM sem.remit_claims WHERE is_current_adjudication AND payer_name = 'Aetna'"
    code, out = _run(session, "sql", sql)
    assert code == 1 and "lookup payer_name" in out
    assert _run(session, "lookup", "payer_name", "aetna")[0] == 0
    assert _run(session, "sql", sql)[0] == 0
    assert "lookup payer_name" in _run(session, "sql", sql.replace("'Aetna'", "'AETNA'"))[1]


def test_probes_are_uncharged_and_capped(bench_snapshot, sessions_root, monkeypatch):
    session = _session(bench_snapshot, {"explore_probes": True, "max_tool_calls": 1})
    monkeypatch.setattr(cli, "MAXIMUM_PROBES", 2)
    probe = "SELECT count(*) FROM sem.claims"
    assert _run(session, "probe", probe)[0] == 0 and _run(session, "probe", probe)[0] == 0
    assert "probes are used up" in _run(session, "probe", probe)[1]
    assert _run(session, "sql", probe)[0] == 0                                           # budget still has 1
    assert "budget" in _run(session, "sql", probe)[1]
    assert "not enabled" in _run(_session(bench_snapshot, {}), "probe", probe)[1]


# ---- examples (#6) and playbook (#2) ------------------------------------------------------------------------

def test_examples_come_from_the_train_split_only(bench_snapshot, sessions_root):
    from bench import eval_subagents
    from bench.eval import load_cases
    from bench.splits import select_cases
    cases = load_cases(snapshot_path=bench_snapshot)
    train_ids = {case["id"] for case in select_cases(cases, "train")}
    target = sorted(case["id"] for case in select_cases(cases, "validation") if not case.get("expect_refusal"))[0]
    manifest = eval_subagents.prepare("pytest-examples", {"k5": {"enable_examples": True, "examples_k": 5}},
                                      ["haiku"], 1, only=target, snapshot_path=bench_snapshot, agent="analyzer",
                                      question_split="validation")
    entry = json.loads(Path(manifest).read_text())["entries"][0]
    meta = json.loads((Path(entry["session"]) / "session.json").read_text())
    assert meta["example_library_path"] is None                    # never the snapshot's generated_questions.json
    from analyzer.examples import train_examples_for
    shown = train_examples_for(bench_snapshot, target, 5)
    assert len(shown) == 5 and {example.question_id for example in shown} <= train_ids
    assert all(f"Example {i} ({example.question_id})" in entry["prompt"] for i, example in enumerate(shown, start=1))
    assert "already in your briefing" in _run(Path(entry["session"]), "examples", "x")[1]


def test_playbook_slot_is_empty_hashed_and_frozen(bench_snapshot, sessions_root, tmp_path, monkeypatch):
    from analyzer import playbook
    from bench import eval_subagents
    assert playbook.PLAYBOOK_PATH.read_text() == ""                # waits on the TRAIN distillation and review
    with pytest.raises(playbook.PlaybookNotReady):
        eval_subagents.prepare("pytest-playbook-empty", {"pb": {"playbook": True}}, ["haiku"], 1,
                               only="q01_claims_submitted", snapshot_path=bench_snapshot, agent="analyzer")
    frozen = tmp_path / "playbook.md"
    frozen.write_text("- reviewed bullet\n")
    monkeypatch.setattr(playbook, "PLAYBOOK_PATH", frozen)
    manifest = eval_subagents.prepare("pytest-playbook", {"pb": {"playbook": True}}, ["haiku"], 1,
                                      only="q01_claims_submitted", snapshot_path=bench_snapshot, agent="analyzer")
    entry = json.loads(Path(manifest).read_text())["entries"][0]
    meta = json.loads((Path(entry["session"]) / "session.json").read_text())
    assert meta["playbook_sha256"] == playbook.playbook_sha256("- reviewed bullet\n")
    assert "- reviewed bullet" in entry["prompt"]
    frozen.write_text("- edited after prepare\n")
    code, out = _run(Path(entry["session"]), "briefing")
    assert code == 1 and "changed after this session was prepared" in out


def test_in_process_loop_refuses_subagent_only_levels():
    from analyzer.loop import Analyzer
    with pytest.raises(NotImplementedError, match="domain_lint"):
        Analyzer(opener=None, llm=None, config=AnalyzerConfig(domain_lint=True))
