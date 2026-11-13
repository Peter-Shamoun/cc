"""Gold v2 kept current a batch at a time equals gold built from scratch, on a small mock data set with 837, 835 and
277CA files. The benchmark (claim_clarity_gold/benchmark_gold.py) checks the same at scale."""
from __future__ import annotations

import subprocess
import sys

import pytest
from pyiceberg.catalog.sql import SqlCatalog

from claim_clarity_gold.canonical_questions import CANONICAL_QUESTIONS
from claim_clarity_gold.full_build import ALL_RELATIONS, build_gold_from_scratch, open_gold
from claim_clarity_gold.incremental_gold import IncrementalGold, open_incremental_gold
from claim_clarity_gold.silver_dataset import (batch_table_files, build_silver_database, export_silver_batches,
                                               open_silver)


@pytest.fixture(scope="module")
def gold_both_ways(tmp_path_factory):
    directory = tmp_path_factory.mktemp("gold")
    subprocess.run([sys.executable, "-m", "ccsim.edi_mock", str(directory / "mock"), "--days", "10", "--scale", ".3",
                    "--tenants", "acme_labs", "--seed", "7"], check=True, capture_output=True)
    build_silver_database(directory / "mock" / "acme_labs" / "raw", directory / "silver.duckdb")
    batch_ids = export_silver_batches(directory / "silver.duckdb", directory / "batches")

    (directory / "warehouse").mkdir()
    catalog = SqlCatalog("gold", uri=f"sqlite:///{directory}/catalog.sqlite",
                         warehouse=f"file://{directory}/warehouse")
    gold = IncrementalGold(directory / "incremental", catalog, "gold")
    for number, batch_id in enumerate(batch_ids, start=1):
        gold.apply_batch(batch_id, batch_table_files(directory / "batches", batch_id))
        if number == len(batch_ids) // 2:
            gold.compact()                                # compaction in the middle must not change anything
    gold.compact()
    gold.close()

    build_gold_from_scratch(open_silver(directory / "silver.duckdb"), directory / "full")
    return open_incremental_gold(catalog, "gold"), open_gold(directory / "full"), directory, len(batch_ids)


def test_the_data_set_has_several_batches_and_every_kind_of_file(gold_both_ways):
    incremental, _, _, batch_count = gold_both_ways
    assert batch_count >= 5
    for relation in ("claim_submissions", "claim_acknowledgments", "remit_claims", "superseded_remit_claims"):
        assert incremental.execute(f"SELECT count(*) FROM gold.{relation}").fetchall()[0][0] > 0, relation


@pytest.mark.parametrize("relation", ALL_RELATIONS)
def test_the_incremental_build_equals_the_full_build(gold_both_ways, relation):
    incremental, _, directory, _ = gold_both_ways
    full_file = directory / "full" / f"{relation}.parquet"
    columns = ", ".join(row[0] for row in incremental.execute(f"DESCRIBE gold.{relation}").fetchall())
    only_incremental, only_full = incremental.execute(
        f"SELECT (SELECT count(*) FROM (SELECT {columns} FROM gold.{relation} EXCEPT ALL "
        f"SELECT {columns} FROM '{full_file}')), (SELECT count(*) FROM (SELECT {columns} FROM '{full_file}' "
        f"EXCEPT ALL SELECT {columns} FROM gold.{relation}))").fetchall()[0]
    assert (only_incremental, only_full) == (0, 0)


def test_state_tables_have_one_row_per_key(gold_both_ways):
    incremental, _, _, _ = gold_both_ways
    assert incremental.execute("SELECT count(*) - count(DISTINCT claim_key) FROM gold.claims").fetchall()[0][0] == 0
    assert incremental.execute("SELECT count(*) - count(DISTINCT (claim_key, payer_key)) "
                               "FROM gold.claim_payers").fetchall()[0][0] == 0


def test_every_canonical_question_runs(gold_both_ways):
    incremental, _, _, _ = gold_both_ways
    as_of = incremental.execute("SELECT max(payment_date) FROM gold.payments").fetchall()[0][0]
    for name, sql in CANONICAL_QUESTIONS.items():
        incremental.execute(sql.format(as_of=as_of)).fetchall()
