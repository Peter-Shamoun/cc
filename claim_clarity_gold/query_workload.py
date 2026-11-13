"""The query workload gold is designed for, and what it reads.

The workload is the benchmark's 156 authored questions (bench/authored_questions.json): each has a query written
against the semantic views (sem.*), which are defined over silver. For every query this module finds the semantic
relations and columns it names; a gold schema covers a query when it holds all of them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
AUTHORED_QUESTIONS_PATH = REPOSITORY_ROOT / "bench" / "authored_questions.json"


CLAIM_PAYERS_REWRITES: dict[str, str] = json.loads((Path(__file__).parent / "claim_payers_rewrites.json").read_text())


@dataclass(frozen=True)
class WorkloadQuery:
    query_id: str
    category: str
    sql: str                                            # against sem.*
    columns_read: frozenset[tuple[str, str]]            # (semantic relation, column)

    @property
    def gold_sql(self) -> str:
        """The query as it is asked of gold: on claim_payers where that is simpler (each such rewrite returns the
        same rows), else the same query on the gold relation of the same name."""
        return CLAIM_PAYERS_REWRITES.get(self.query_id) or self.sql.replace("sem.", "gold.")

    def relations_read(self) -> frozenset[str]:
        return frozenset(relation for relation, _ in self.columns_read)


def semantic_relation_columns(silver: duckdb.DuckDBPyConnection) -> dict[str, list[str]]:
    """Every sem.* relation and its columns, in order. silver has the lossless tables and the semantic views."""
    columns: dict[str, list[str]] = {}
    for relation, column in silver.execute(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'sem' "
            "ORDER BY table_name, ordinal_position").fetchall():
        columns.setdefault(relation, []).append(column)
    return columns


def load_workload(silver: duckdb.DuckDBPyConnection) -> list[WorkloadQuery]:
    relation_columns = semantic_relation_columns(silver)
    schema = {"sem": {relation: dict.fromkeys(columns, "VARCHAR") for relation, columns in relation_columns.items()}}
    workload = []
    for question in json.loads(AUTHORED_QUESTIONS_PATH.read_text()):
        parsed = sqlglot.parse_one(question["gold_sql"], read="duckdb")
        try:                                             # a bare * reads every column of its relation
            tree = qualify(parsed.copy(), schema=schema, dialect="duckdb", validate_qualify_columns=False,
                           expand_stars=False)
        except AssertionError:
            tree = qualify(parsed.copy(), schema=schema, dialect="duckdb", validate_qualify_columns=False)
        relation_of_alias = {table.alias_or_name: table.name for table in tree.find_all(exp.Table) if table.db == "sem"}
        columns_read = {(relation_of_alias[column.table], column.name) for column in tree.find_all(exp.Column)
                        if column.table in relation_of_alias
                        and column.name in relation_columns[relation_of_alias[column.table]]}
        columns_read |= {(relation, relation_columns[relation][0]) for relation in relation_of_alias.values()
                         if relation not in {r for r, _ in columns_read}}      # e.g. SELECT count(*) FROM sem.x
        workload.append(WorkloadQuery(question["id"], question["category"], question["gold_sql"],
                                      frozenset(columns_read)))
    return workload


def column_usage(workload: list[WorkloadQuery]) -> dict[tuple[str, str], int]:
    """(relation, column) -> the number of workload queries that read it."""
    usage: dict[tuple[str, str], int] = {}
    for query in workload:
        for column in query.columns_read:
            usage[column] = usage.get(column, 0) + 1
    return usage
