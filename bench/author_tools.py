"""Tools for the (trusted) Opus agents that author and verify benchmark questions. Not for evaluated agents.

    python3 bench/author_tools.py --snapshot DIR schema            # sem.* views + ledger tables + glossary
    python3 bench/author_tools.py --snapshot DIR sem "<SELECT>"     # run over the semantic layer (read-only)
    python3 bench/author_tools.py --snapshot DIR ledger "<SELECT>"  # run over the generator's answer key (read-only)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench.agents import GLOSSARY  # noqa: E402
from bench.dataset import open_snapshot  # noqa: E402

ROWS_SHOWN = 30


def _describe(con, schemas) -> str:
    out = []
    for schema, name in con.execute("SELECT schema_name, table_name FROM duckdb_tables() WHERE schema_name IN "
                                    f"({', '.join(repr(s) for s in schemas)}) UNION ALL SELECT schema_name, view_name "
                                    f"FROM duckdb_views() WHERE NOT internal AND schema_name IN "
                                    f"({', '.join(repr(s) for s in schemas)}) ORDER BY 1, 2").fetchall():
        cols = con.execute(f"DESCRIBE {schema}.{name}").fetchall()
        out.append(f"{schema}.{name}(" + ", ".join(f"{c[0]} {c[1]}" for c in cols) + ")")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("command", choices=["schema", "sem", "ledger"])
    ap.add_argument("sql", nargs="?")
    a = ap.parse_args()
    snap = Path(a.snapshot)
    sem = open_snapshot(snap)
    ledger = duckdb.connect(str(snap / "answer_key.duckdb"), read_only=True)
    if a.command == "schema":
        print("# Semantic layer (what the analytics agent sees)\n" + _describe(sem, ["sem"]) + "\n\n" + GLOSSARY +
              "\n\n# Answer key (the generator's ledger; independent of the parser and the views)\n" +
              _describe(ledger, ["ledger"]) +
              "\n\nLedger notes: payer names are upper case as sent (the semantic layer shows Title Case); "
              "payer_sequence 0 = primary, 1 = secondary; events.is_current marks the current adjudication; "
              "dates are ISO strings (cast with ::DATE).")
        return
    con = sem if a.command == "sem" else ledger
    try:
        cur = con.execute(a.sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        print(json.dumps({"columns": cols, "row_count": len(rows), "rows": rows[:ROWS_SHOWN]}, default=str))
    except Exception as e:                                             # noqa: BLE001
        print(f"ERROR: {str(e).splitlines()[0]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
