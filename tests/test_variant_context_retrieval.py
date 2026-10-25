"""Context-retrieval variant: value index, tenant scope, and the train-only example guard."""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from analyst.variants.context_retrieval.fewshot import load_train_library, retrieve_examples
from analyst.variants.context_retrieval.llm import LLMNotScripted, LLMResponse, ScriptedLLM
from analyst.variants.context_retrieval.phi import PhiError
from analyst.variants.context_retrieval.schema_link import link_schema, recall_against_sql
from analyst.variants.context_retrieval.screen import SCREEN_ARMS, RetrievalConfig, config_from_flags
from analyst.variants.context_retrieval.session import ContextRetrievalAgent, dispatch_tool, prepare_context
from analyst.variants.context_retrieval.splits import SplitLabelError, SplitLeakError, assign_split
from analyst.variants.context_retrieval.value_index import BUILD_QUERIES, TenantScopeError, ValueIndex, resolve_question
from bench.agents import Tools


def _database() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute("CREATE SCHEMA sem")
    connection.execute(
        """
        CREATE TABLE sem.payers (
            payer_name VARCHAR, payer_class VARCHAR, names_as_sent VARCHAR[],
            claim_submissions BIGINT, payments BIGINT
        )
        """
    )
    connection.execute(
        """
        INSERT INTO sem.payers VALUES
            ('AETNA', 'commercial', ['AETNA', 'AETNA INC'], 10, 4),
            ('BLUE CROSS BLUE SHIELD OF NC', 'blues', ['BCBS NC'], 8, 3),
            ('BLUE CROSS BLUE SHIELD OF TX', 'blues', ['BCBS TX'], 2, 1),
            ('HOLLISTER CLAIMS ADMINISTRATION', 'commercial', ['HOLLISTER CLAIMS'], 1, 1),
            ('UNITEDHEALTHCARE', 'commercial', ['UNITEDHEALTHCARE INSURANCE COMPANY'], 9, 4),
            ('UNITEDHEALTHCARE MEDICARE ADVANTAGE', 'medicare_advantage', [], 3, 2)
        """
    )
    connection.execute("CREATE TABLE sem.claim_lines (procedure_code VARCHAR)")
    connection.execute("INSERT INTO sem.claim_lines VALUES ('87798'), ('87798'), ('80053')")
    connection.execute("CREATE TABLE sem.remit_lines (procedure_code VARCHAR)")
    connection.execute("INSERT INTO sem.remit_lines VALUES ('87798'), ('36415')")
    connection.execute(
        """
        CREATE TABLE sem.adjustments (
            carc_code VARCHAR, carc_description VARCHAR, carc_category VARCHAR,
            remit_claim_id VARCHAR
        )
        """
    )
    connection.execute(
        """
        INSERT INTO sem.adjustments VALUES
            ('29', 'The time limit for filing has expired', 'timely_filing', 'r1'),
            ('29', 'The time limit for filing has expired', 'timely_filing', 'r2'),
            ('1', 'Deductible amount', 'patient responsibility', 'r3'),
            ('45', 'Charges exceed fee schedule', 'contractual', 'r4'),
            ('23', 'Impact of prior payer adjudication including payments and adjustments', 'prior payer', 'r6'),
            ('11', 'Diagnosis inconsistent with the procedure', 'medical necessity', 'r7'),
            ('99', 'patient SSN 123-45-6789 listed', 'other', 'r5')
        """
    )
    connection.execute("CREATE TABLE sem.remarks (remark_code VARCHAR, remark_description VARCHAR)")
    connection.execute("INSERT INTO sem.remarks VALUES ('N290', 'Missing documentation')")
    connection.execute("CREATE TABLE sem.diagnoses (diagnosis_code VARCHAR)")
    connection.execute("INSERT INTO sem.diagnoses VALUES ('E11.9'), ('E11.9'), ('Z00.00')")
    connection.execute(
        """
        CREATE TABLE sem.remit_claims (
            claim_key VARCHAR, payer_name VARCHAR, is_denied BOOLEAN,
            is_current_adjudication BOOLEAN, paid_amount DOUBLE, check_or_eft_number VARCHAR
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE sem.claims (
            claim_key VARCHAR, is_latest_submission BOOLEAN, charge_amount DOUBLE,
            payer_responsibility VARCHAR, principal_diagnosis_code VARCHAR
        )
        """
    )
    connection.execute(
        "CREATE TABLE sem.payments (payment_id VARCHAR, total_payment_amount DOUBLE, is_balanced BOOLEAN)"
    )
    connection.execute(
        """
        CREATE TABLE sem.provider_adjustments (
            plb_reason_code VARCHAR, provider_adjustment_amount DOUBLE, payer_name VARCHAR
        )
        """
    )
    return connection


def _other_tenant() -> duckdb.DuckDBPyConnection:
    connection = _database()
    connection.execute("DELETE FROM sem.payers")
    connection.execute(
        """
        INSERT INTO sem.payers VALUES
            ('TEXAS MEDICAID', 'medicaid', ['TEXAS MEDICAID'], 3, 1)
        """
    )
    return connection


def _train_pool() -> list[dict]:
    return [
        {
            "id": "rate_by_payer", "split": "train", "question_set": None, "family": None,
            "question": "What is the denial rate for each payer?",
            "sql": "SELECT payer_name, avg(is_denied::INT) FROM sem.remit_claims",
        },
        {
            "id": "entity_paid", "split": "train", "question_set": None, "family": "paid",
            "question": "Aetna CPT 87798 total paid",
            "sql": "SELECT sum(paid_amount) FROM sem.remit_claims WHERE payer_name = 'AETNA'",
        },
        {
            "id": "same_family", "split": "train", "question_set": None, "family": "paid",
            "question": "How much was paid in total by each payer?",
            "sql": "SELECT payer_name, sum(paid_amount) FROM sem.remit_claims",
        },
    ]


def test_value_index_resolves_codes_and_scrubs_phi_text():
    index = ValueIndex.build(_database(), "acme_labs")
    serialized = index.dumps()
    assert "123-45-6789" not in serialized
    assert "claim_key" not in serialized
    carc_99 = next(entry for entry in index.entries if entry.kind == "carc" and entry.value == "99")
    assert carc_99.description is None
    timely = index.lookup("acme_labs", "carc", "29")
    assert timely["bound"] is True
    assert timely["matches"][0]["value"] == "29"
    assert timely["matches"][0]["description"] == "The time limit for filing has expired"
    procedure = index.lookup("acme_labs", "procedure_code", "87798")
    assert procedure["bound"] is True
    assert procedure["matches"][0]["row_count"] == 3
    assert set(procedure["matches"][0]["source_views"]) == {"sem.claim_lines", "sem.remit_lines"}
    diagnosis = index.lookup("acme_labs", "diagnosis_code", "E11.9")
    assert diagnosis["bound"] is True
    assert diagnosis["matches"][0]["value"] == "E119"
    deductible = index.lookup("acme_labs", "carc", "deductible")
    assert deductible["bound"] is True
    assert deductible["matches"][0]["value"] == "1"
    roundtrip = ValueIndex.from_json(json.loads(serialized))
    assert roundtrip.lookup("acme_labs", "rarc", "N290")["bound"] is True


def test_value_index_is_tenant_scoped_and_refuses_identifier_columns():
    labs = ValueIndex.build(_database(), "acme_labs")
    diagnostics = ValueIndex.build(_other_tenant(), "acme_diagnostics")
    with pytest.raises(TenantScopeError):
        labs.lookup("acme_diagnostics", "payer_name", "AETNA")
    assert labs.lookup("acme_labs", "payer_name", "Aetna")["matches"][0]["value"] == "AETNA"
    assert diagnostics.lookup("acme_diagnostics", "payer_name", "Aetna")["matches"] == []
    assert diagnostics.lookup("acme_diagnostics", "payer_name", "Texas Medicaid")["bound"] is True
    with pytest.raises(PhiError):
        ValueIndex.build(_database(), "acme_labs", queries={**BUILD_QUERIES, "payers": "SELECT claim_key FROM sem.payers"})
    with pytest.raises(PhiError):
        ValueIndex.build(_database(), "acme_labs", queries={**BUILD_QUERIES, "carc": "SELECT * FROM sem.adjustments"})
    resolved = resolve_question(labs, "acme_labs", "How many claims did Aetna deny?")
    assert [item["matches"][0]["value"] for item in resolved["bound"]] == ["AETNA"]
    assert resolved["ambiguous"] == []
    timely = resolve_question(labs, "acme_labs", "How many denials were for timely filing?")
    assert [item["matches"][0]["value"] for item in timely["bound"]] == ["29"]
    deductible = resolve_question(labs, "acme_labs", "What is the total deductible assigned by primary payers?")
    assert [item["matches"][0]["value"] for item in deductible["bound"]] == ["1"]
    united = resolve_question(labs, "acme_labs", "What is United's denial rate?")
    assert united["bound"] == []
    united_values = {match["value"] for item in united["ambiguous"] for match in item["matches"]}
    assert united_values >= {"UNITEDHEALTHCARE", "UNITEDHEALTHCARE MEDICARE ADVANTAGE"}
    adjustments = resolve_question(labs, "acme_labs", "How much was taken back through provider-level adjustments?")
    assert all(item["matches"][0]["value"] != "23" for item in adjustments["bound"])
    diagnosis = resolve_question(labs, "acme_labs", "What is the denial rate by principal diagnosis code?")
    assert all(item["matches"][0]["value"] != "11" for item in diagnosis["bound"])
    blues = labs.lookup("acme_labs", "payer_name", "Blue Cross")
    assert blues["bound"] is False
    assert {match["value"] for match in blues["matches"]} == {
        "BLUE CROSS BLUE SHIELD OF NC", "BLUE CROSS BLUE SHIELD OF TX",
    }


def test_schema_link_keeps_recall_and_column_mode_can_drop():
    connection = _database()
    full = link_schema(connection, "What is the denial rate?", mode="full")
    assert full.dropped_views == []
    assert "sem.payments" in full.kept_relations()
    recall = link_schema(connection, "What is the denial rate?", mode="linked_recall")
    assert "sem.remit_claims" in recall.kept_relations()
    assert "check_or_eft_number" in recall.kept_columns()
    columns = link_schema(connection, "What is the denial rate?", mode="linked_columns")
    assert "is_denied" in columns.kept_columns()
    assert "check_or_eft_number" not in columns.kept_columns()
    missed = recall_against_sql(columns, "SELECT check_or_eft_number FROM sem.remit_claims")
    assert missed["missing_columns"] == ["check_or_eft_number"]
    short = link_schema(connection, "Which denial reason codes appear most often?", mode="linked_columns")
    short_recall = recall_against_sql(
        short,
        "SELECT carc_code, count(DISTINCT remit_claim_id) FROM sem.adjustments",
        {column.name for view in link_schema(connection, "catalog", mode="full").views for column in view.columns},
    )
    assert short_recall["missing_columns"] == ["remit_claim_id"]
    fallback = link_schema(connection, "zzzz", mode="linked_recall", recall_floor_views=2)
    assert fallback.fallback_full is True
    assert "sem.payments" in fallback.kept_relations()


def test_fewshot_selector_and_train_guard():
    pool = _train_pool()
    question = "Aetna CPT 87798 denial rate"
    lexical = retrieve_examples(pool, question, k=1, selector="lexical", mask_literals=["aetna", "87798"])
    skeleton = retrieve_examples(pool, question, k=1, selector="skeleton", mask_literals=["aetna", "87798"])
    assert lexical[0]["id"] == "entity_paid"
    assert skeleton[0]["id"] == "rate_by_payer"
    family_blocked = retrieve_examples(
        pool, "How much was paid last month?", question_id="other", family="paid", k=3, selector="lexical",
    )
    assert "same_family" not in {example["id"] for example in family_blocked}
    assert "entity_paid" not in {example["id"] for example in family_blocked}
    poisoned = pool + [{
        "id": "heldout_item", "split": "validation", "question_set": "heldout_hard_1", "family": None,
        "question": "validation only question", "sql": "SELECT 1",
    }]
    assert retrieve_examples(poisoned, question, k=0) == []
    with pytest.raises(SplitLeakError):
        retrieve_examples(poisoned, question, k=3)
    near = retrieve_examples(
        pool + [{
            "id": "copy", "split": "train", "question_set": None, "family": None,
            "question": "What is the denial rate for each payer?",
            "sql": "SELECT 1 AS leaked",
        }],
        "What is the denial rate for each payer?",
        k=3,
    )
    assert "copy" not in {example["id"] for example in near}
    assert "leaked" not in json.dumps(near)


def test_real_library_excludes_heldout_and_rejects_unknown_sets():
    library = load_train_library()
    authored = json.loads(Path("bench/authored_questions.json").read_text())
    heldout_ids = {question["id"] for question in authored if question.get("question_set") == "heldout_hard_1"}
    assert heldout_ids
    assert heldout_ids.isdisjoint({example["id"] for example in library})
    assert all(example["split"] == "train" for example in library)
    with pytest.raises(SplitLabelError):
        assign_split({"id": "future", "question_set": "round3"})
    with pytest.raises(SplitLeakError):
        load_train_library(extra_questions=[{
            "id": "nope", "question_set": "heldout_hard_1", "question": "x", "gold_sql": "SELECT 1",
        }])


def test_prepare_context_puts_bound_values_in_the_prompt_and_refuses_without_the_model():
    connection = _database()
    library = _train_pool()
    packet = prepare_context(
        connection, "What is Aetna's denial rate?",
        config=RetrievalConfig(value_index=True, fewshot_k=1, fewshot_selector="skeleton"),
        tenant_id="acme_labs", library=library, question_id="current",
    )
    assert "AETNA" in packet.user
    assert "bound" in packet.user
    assert packet.examples
    assert "validation only" not in packet.briefing
    ambiguous = prepare_context(
        connection, "How much did Blue Cross pay?",
        config=RetrievalConfig(value_index=True, fewshot_k=0),
        tenant_id="acme_labs", library=library,
    )
    assert "ambiguous" in ambiguous.user
    assert "BLUE CROSS BLUE SHIELD OF NC" in ambiguous.user
    assert "BLUE CROSS BLUE SHIELD OF TX" in ambiguous.user

    class Boom:
        def complete(self, request):
            raise AssertionError("the model must not be called after a hard refusal")

    agent = ContextRetrievalAgent(
        config=RetrievalConfig(hard_refuse=True, value_index=False, fewshot_k=0),
        llm=Boom(), tenant_id="acme_labs", library=library,
    )
    tools = Tools(connection)
    for question in (
        "List the names and dates of birth of patients whose claims were denied last month.",
        "Give me the insurance member IDs for patients with denied molecular tests.",
        "What is Acme Diagnostics' denial rate?",
        "Delete the re-delivered duplicate 835 files from the data.",
    ):
        answer = agent.answer(question, tools)
        assert answer.refused, question


def test_scripted_agent_executes_sql_and_screen_flags_reject_pitfalls():
    connection = _database()
    connection.execute("INSERT INTO sem.claims VALUES ('c1', true, 10, 'P', 'E119'), ('c2', true, 12, 'P', 'E119')")
    scripted = ScriptedLLM({"How many claims?": LLMResponse(sql="SELECT count(*) AS n FROM sem.claims", text="two")})
    agent = ContextRetrievalAgent(
        config=RetrievalConfig(value_index=False, fewshot_k=0, hard_refuse=False),
        llm=scripted, library=[],
    )
    answer = agent.answer("How many claims?", Tools(connection))
    assert answer.rows == [(2,)]
    assert "pitfalls" not in scripted.seen[0].system
    with pytest.raises(LLMNotScripted):
        ContextRetrievalAgent(config=RetrievalConfig(value_index=False, fewshot_k=0), library=[]).answer(
            "How many claims?", Tools(connection),
        )
    with pytest.raises(ValueError):
        RetrievalConfig(glossary="pitfalls")
    with pytest.raises(ValueError):
        config_from_flags({"not_a_flag": True})
    assert set(SCREEN_ARMS) >= {"cr0_base", "cr_value", "cr_fewshot_skeleton", "cr_schema_recall"}
    assert SCREEN_ARMS["cr_value"]["value_index"] is True
    assert SCREEN_ARMS["cr_value"]["fewshot_k"] == 0
    assert SCREEN_ARMS["cr_value"]["schema_mode"] == "full"
    assert SCREEN_ARMS["cr0_base"]["value_index"] is False
    poisoned = _train_pool() + [{
        "id": "heldout_item", "split": "validation", "question_set": "heldout_hard_1", "family": None,
        "question": "validation only question", "sql": "SELECT 1",
    }]
    with pytest.raises(SplitLeakError):
        dispatch_tool(connection, "acme_labs", poisoned, "similar_examples", {"question": "denial rate", "k": 2})
    labs = ValueIndex.build(connection, "acme_labs")
    with pytest.raises(TenantScopeError):
        dispatch_tool(connection, "acme_diagnostics", [], "lookup_values",
                      {"kind": "payer_name", "text": "Aetna"}, value_index=labs)
