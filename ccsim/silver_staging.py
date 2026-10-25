"""A parser batch's silver rows, staged in DuckDB under the lossless DDL before they are written as Parquet.

SilverBatch parses raw files, in this process or in a pool of worker processes (PARSE_WORKERS), and stages their
rows in DuckDB tables with the lossless DDL's column types and NOT NULLs. A raw file that fails to parse is
recorded as a REJECTED edi.source_file row instead; the caller quarantines it. Measurements and the reasoning
behind each choice: docs/data-backend/parser-optimization.md.

Memory stays flat with the size of the batch and of its files: rows go to DuckDB every FLUSH_SEGMENTS segments,
also in the middle of a file (the parser's latest row of each table stays buffered, since it may still change),
and the staged tables spill to disk above STAGING_MEMORY_LIMIT. A file rejected after a flush has its flushed rows
deleted again.
"""
from __future__ import annotations

import gc
import hashlib
import multiprocessing
import re
import tempfile
from collections import deque
from collections.abc import Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from typing import NamedTuple

import duckdb
import pyarrow as pa

from .parser import DDL_LOSSLESS, SEGMENT_FIELDS, TABLES, ParseError, TableRows, parse_file

FLUSH_SEGMENTS = 50_000          # buffered segments that trigger a flush into DuckDB (or, in a worker, into Arrow)
FLUSH_MIN_ROWS = 2_000           # a flush leaves smaller tables buffered: one insert per batch is cheaper
STAGING_MEMORY_LIMIT = "256MB"
POOL_MAX_FILE_BYTES = 16_000_000     # bigger files are parsed in the task process, which streams them into DuckDB
# What a file that cannot be loaded raises. Anything else is a bug and fails the batch.
REJECTED_FILE_ERRORS = (ParseError, UnicodeDecodeError, IndexError, ValueError, KeyError, TypeError)


class RawFile(NamedTuple):
    key: str
    body: bytes
    file_id: str
    meta: dict                   # the edi.source_file provenance columns (S3 location, sha256, size, ...)


# --- rows -> Arrow -> DuckDB ---------------------------------------------------------------------------------------
def arrow_table(table: str, rows: list) -> pa.Table:
    """Rows of one table as Arrow: row dicts that all have the same keys in the same order (the parser's do), or
    edi.segment's SEGMENT_FIELDS tuples. Columns are lists: pyarrow reads tuples ten times slower."""
    if table == "edi.segment":
        names, values = SEGMENT_FIELDS, zip(*rows, strict=True)
    else:
        names, values = rows[0], zip(*map(dict.values, rows), strict=True)   # strict: a row with other keys fails
    return pa.table(dict(zip(names, map(list, values), strict=True)))


def insert_arrow(con, table: str, data: pa.Table):
    con.register("_rows", data)
    if table == "edi.segment":                    # elements is split from raw_text here (see parser.SEGMENT_FIELDS)
        con.execute("INSERT INTO edi.segment BY NAME SELECT * EXCLUDE (element_separator, elements_json), "
                    "CASE WHEN elements_json IS NULL THEN string_split(raw_text, element_separator)[2:] "
                    "ELSE elements_json::VARCHAR[] END AS elements FROM _rows")
    else:
        con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _rows")
    con.unregister("_rows")


def insert_rows(con, table: str, rows: list):
    if rows:
        insert_arrow(con, table, arrow_table(table, rows))


def _drain(rows_by_table: dict[str, list], keep_latest_rows: bool) -> Iterator[tuple[str, pa.Table]]:
    """Take buffered rows out as Arrow tables. keep_latest_rows (in the middle of a file): leave each table's
    latest row, the only one the parser may still change, and leave small tables for later."""
    for t, rows in rows_by_table.items():
        n = len(rows) - 1 if keep_latest_rows and t != "edi.segment" else len(rows)
        if n > (FLUSH_MIN_ROWS if keep_latest_rows else 0):
            data = arrow_table(t, rows[:n])
            del rows[:n]                                      # in place: the parser's adders append to these lists
            yield t, data


def _staging_ddl() -> tuple[str, list[tuple[str, str, str, str]]]:
    """The lossless DDL for a batch's staging tables: its types and NOT NULLs, without keys. Returns it and the
    foreign keys as (table, column, parent table, parent column).

    Rows of a file are inserted while the file is still being parsed, before its edi.source_file row exists, so the
    foreign keys are checked once per batch instead (SilverBatch.finish). Primary keys are unique by construction
    (<file_id>:<seg_ord>; tests/test_parser.py checks it) and maintaining their indexes was half the insert cost.
    Neither changes the Parquet written: its schema comes from the column types and the data."""
    con = duckdb.connect()
    con.execute(DDL_LOSSLESS)
    keys = [(f"{schema}.{table}", columns[0], f"{schema}.{parent}", parent_columns[0])
            for schema, table, columns, parent, parent_columns in con.execute(
                "SELECT schema_name, table_name, constraint_column_names, referenced_table, referenced_column_names "
                "FROM duckdb_constraints() WHERE constraint_type = 'FOREIGN KEY'").fetchall()]
    n_primary_keys = con.execute("SELECT count(*) FROM duckdb_constraints() "
                                 "WHERE constraint_type = 'PRIMARY KEY'").fetchone()[0]
    ddl, n_references = re.subn(r"\s+REFERENCES\s+[\w.]+\(\w+\)", "", DDL_LOSSLESS)
    ddl, n_table_keys = re.subn(r",([ \t]*--[^\n]*)?\n\s*PRIMARY KEY\s*\([^)]*\)", r"\1", ddl)
    ddl, n_column_keys = re.subn(r"\s+PRIMARY KEY\b", "", ddl)
    assert n_references == len(keys) and n_table_keys + n_column_keys == n_primary_keys, (keys, n_primary_keys)
    con = duckdb.connect()
    con.execute(ddl)
    assert not con.execute("SELECT count(*) FROM duckdb_constraints() WHERE constraint_type IN "
                           "('PRIMARY KEY', 'FOREIGN KEY', 'UNIQUE')").fetchone()[0]
    return ddl, keys


STAGING_DDL, STAGING_FOREIGN_KEYS = _staging_ddl()


# --- a pool worker: one file -> Arrow ------------------------------------------------------------------------------
class _FileToArrow(TableRows):
    def __init__(self):
        super().__init__()
        self.tables: dict[str, list[pa.Table]] = {t: [] for t in TABLES}

    def after_chunk(self):
        if len(self.rows["edi.segment"]) >= FLUSH_SEGMENTS:
            for t, data in _drain(self.rows, keep_latest_rows=True):
                self.tables[t].append(data)


def _parse_in_worker(body: bytes, file_id: str, customer: str, meta: dict, run_id: str, started: datetime):
    """(Arrow tables per table, the edi.source_file row) for a file that parsed, or the rejection message."""
    gc.disable()                                               # as in SilverBatch.add_raw_files; a worker only parses
    sink = _FileToArrow()
    try:
        parse_file(body, file_id, customer, meta, run_id, started, sink=sink)
    except REJECTED_FILE_ERRORS as err:
        return f"{type(err).__name__}: {err}"
    (source_file_row,) = sink.rows.pop("edi.source_file")
    for t, data in _drain(sink.rows, keep_latest_rows=False):
        sink.tables[t].append(data)
    return {t: v for t, v in sink.tables.items() if v}, source_file_row


# --- the batch -----------------------------------------------------------------------------------------------------
class SilverBatch(TableRows):
    """See the module doc. add_raw_files, then finish() returns the DuckDB connection holding the staged tables."""

    def __init__(self, run_id: str, started: datetime, triggered_by: str, notes: str | None, parser_version: str,
                 workers: int = 1):
        super().__init__()
        self.run_id, self.started, self.workers = run_id, started, workers
        # One DuckDB thread: more only spend CPU at this size. Above the memory limit staged tables spill to disk.
        self.spill_dir = tempfile.TemporaryDirectory(prefix="silver-staging-")    # removed with the batch
        self.con = duckdb.connect(config={"threads": 1, "memory_limit": STAGING_MEMORY_LIMIT,
                                          "temp_directory": self.spill_dir.name})
        self.con.execute(STAGING_DDL)
        self.flushed_during_file = False
        self.arrow_buffer: dict[str, list[pa.Table]] = {t: [] for t in TABLES}    # pool results not yet inserted
        insert_rows(self.con, "edi.ingest_run", [dict(
            ingest_run_id=run_id, started_at=started, status="SUCCEEDED", parser_name="ccsim.parser",
            parser_version=parser_version, schema_version="1", triggered_by=triggered_by, notes=notes)])

    def add_raw_files(self, files: Iterable[RawFile], customer: str,
                      known_sha: dict[str, str]) -> Iterator[tuple[RawFile, bool]]:
        """Parse files into the batch in order; yields each with whether it was loaded (False: rejected, recorded).
        known_sha (sha256 -> file_id of loaded files) marks byte-identical re-deliveries and is updated.

        The cyclic garbage collector is paused meanwhile: parsing allocates millions of short-lived, acyclic rows,
        and its scans of them cost ~7% of the batch's CPU."""
        collecting = gc.isenabled()
        gc.disable()
        try:
            yield from self._add_raw_files(files, customer, known_sha)
        finally:
            if collecting:
                gc.enable()

    def _add_raw_files(self, files: Iterable[RawFile], customer: str, known_sha: dict[str, str]):
        for f, outcome in self._parse_all(files, customer):
            sha = f.meta["sha256"]
            duplicate_of = known_sha.get(sha)
            if isinstance(outcome, str):
                self._reject(f, customer, outcome)
                yield f, False
                continue
            if outcome is not None:                           # parsed by a worker: tables + edi.source_file row
                tables, source_file_row = outcome
                for t, parts in tables.items():
                    self.arrow_buffer[t] += parts
                    if sum(p.num_rows for p in self.arrow_buffer[t]) >= FLUSH_SEGMENTS:
                        self._insert_arrow_buffer(t)
                self.add["edi.source_file"](**source_file_row)
            if duplicate_of is not None:
                self.rows["edi.source_file"][-1]["duplicate_of_file_id"] = duplicate_of
            known_sha.setdefault(sha, f.file_id)
            yield f, True

    def _parse_all(self, files: Iterable[RawFile], customer: str):
        """(file, None if parsed into self / (tables, source_file_row) from a worker / rejection message), in order."""
        if self.workers <= 1:
            for f in files:
                yield f, self._parse_here(f, customer)
            return
        # spawn: workers are plain children of the task (no fork of a process running DuckDB threads)
        with ProcessPoolExecutor(self.workers, mp_context=multiprocessing.get_context("spawn")) as pool:
            pending = deque()                            # in file order; at most 2 per worker in flight (memory)
            for f in files:
                if len(f.body) > POOL_MAX_FILE_BYTES:    # stream a big file here, after everything before it
                    while pending:
                        yield pending[0][0], pending.popleft()[1].result()
                    self._insert_arrow_buffers()
                    yield f, self._parse_here(f, customer)
                    continue
                pending.append((f, pool.submit(_parse_in_worker, f.body, f.file_id, customer, f.meta, self.run_id,
                                               self.started)))
                while len(pending) > 2 * self.workers or (pending and pending[0][1].done()):
                    yield pending[0][0], pending.popleft()[1].result()
            while pending:
                yield pending[0][0], pending.popleft()[1].result()

    def _parse_here(self, f: RawFile, customer: str) -> str | None:
        self.flushed_during_file = False
        try:
            parse_file(f.body, f.file_id, customer, f.meta, self.run_id, self.started, sink=self)
        except REJECTED_FILE_ERRORS as err:
            self._discard_file(f.file_id)
            return f"{type(err).__name__}: {err}"
        return None

    def _reject(self, f: RawFile, customer: str, message: str):
        self.add["edi.source_file"](file_id=f.file_id, tenant_code=customer, file_kind="UNKNOWN",
                                    ingest_run_id=self.run_id, ingested_at=self.started, parse_status="REJECTED",
                                    parse_messages=[message[:500]], **f.meta)

    def after_chunk(self):
        if len(self.rows["edi.segment"]) >= FLUSH_SEGMENTS:
            for t, data in _drain(self.rows, keep_latest_rows=True):
                insert_arrow(self.con, t, data)
            self.flushed_during_file = True

    def _insert_arrow_buffer(self, table: str):
        if self.arrow_buffer[table]:                     # promote: a column all NULL in one file is typed in another
            insert_arrow(self.con, table, pa.concat_tables(self.arrow_buffer[table], promote_options="default"))
            self.arrow_buffer[table] = []

    def _insert_arrow_buffers(self):
        for t in TABLES:
            self._insert_arrow_buffer(t)

    def _discard_file(self, file_id: str):
        for t, rows in self.rows.items():
            rows[:] = [r for r in rows if (r[0] if t == "edi.segment" else r["file_id"]) != file_id]
            if self.flushed_during_file:
                self.con.execute(f"DELETE FROM {t} WHERE file_id = ?", [file_id])

    def finish(self) -> duckdb.DuckDBPyConnection:
        """Insert what is still buffered and check the foreign keys; returns the connection with the batch."""
        self._insert_arrow_buffers()
        for t, data in _drain(self.rows, keep_latest_rows=False):
            insert_arrow(self.con, t, data)
        for table, column, parent, parent_column in STAGING_FOREIGN_KEYS:
            orphans = self.con.execute(f"SELECT count(*) FROM {table} WHERE {column} NOT IN "
                                       f"(SELECT {parent_column} FROM {parent})").fetchone()[0]
            if orphans:
                raise RuntimeError(f"{orphans} rows of {table} reference no {parent}.{parent_column}")
        return self.con


def file_id_of(key: str, version_id: str | None) -> str:
    return hashlib.sha1(f"{key}#{version_id}".encode()).hexdigest()[:16]
