"""Generate the design-check workload once: N business days of one customer at 5 MB/day (raw EDI kept on local
disk for the current pipeline), and every day parsed with the unchanged ccsim.parser into per-table Parquet (one
directory per day) for the table-format prototypes. Every system under test replays the same days.

    python -m bench.design_check.gen_days DATA_DIR [BUSINESS_DAYS]
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
from datetime import date, datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ccsim import edi_mock
from ccsim.parser import TABLES, ParseError, parse_file
from ccsim.pipeline import DDL_LOSSLESS, _insert

CUSTOMER = "acme_labs"
BUCKET = "claimclarity-prod"


def parse_day(day_dir: Path, day: str, out: Path, run_id: str, known_sha: dict) -> dict:
    """The parser task's per-file loop (pipeline._parse_batch_locked) without S3: same file ids, duplicate and
    quarantine handling. Writes one Parquet per non-empty table."""
    started = datetime.fromisoformat(day)
    con = duckdb.connect()
    con.execute(DDL_LOSSLESS)
    rows_by_table = {t: [] for t in TABLES}
    stats = dict(loaded=0, rejected=0)
    _insert(con, "edi.ingest_run", [dict(ingest_run_id=run_id, started_at=started, status="SUCCEEDED",
                                          parser_name="ccsim.parser", parser_version="ccsim-0.2", schema_version="1")])
    for f in sorted(p for p in day_dir.rglob("*") if p.is_file()):
        key = f"{CUSTOMER}/raw/received_date={day}/{f.relative_to(day_dir).as_posix()}"
        body = f.read_bytes()
        sha = hashlib.sha256(body).hexdigest()
        file_id = hashlib.sha1(f"{key}#v1".encode()).hexdigest()[:16]
        meta = dict(s3_uri=f"s3://{BUCKET}/{key}", s3_bucket=BUCKET, s3_key=key, s3_version_id="v1", s3_etag=sha[:32],
                    s3_last_modified=started, s3_region="us-west-2", kms_key_arn=None, original_filename=f.name,
                    source_system="sftp", size_bytes=len(body), sha256=sha, received_at=started, character_encoding="ascii")
        try:
            text = body[3:].decode("ascii") if body.startswith(b"\xef\xbb\xbf") else body.decode("ascii")
            rows = parse_file(text, file_id, CUSTOMER, meta, run_id, started)
        except (ParseError, UnicodeDecodeError, IndexError, ValueError, KeyError, TypeError) as err:
            stats["rejected"] += 1
            rows_by_table["edi.source_file"].append(dict(file_id=file_id, tenant_code=CUSTOMER, file_kind="UNKNOWN",
                                                         ingest_run_id=run_id, ingested_at=started, parse_status="REJECTED",
                                                         parse_messages=[f"{type(err).__name__}: {err}"[:500]], **meta))
            continue
        if sha in known_sha:
            rows["edi.source_file"][0]["duplicate_of_file_id"] = known_sha[sha]
        else:
            known_sha[sha] = file_id
        for t in TABLES:
            rows_by_table[t] += rows.get(t, [])
        stats["loaded"] += 1
    for t in TABLES:
        _insert(con, t, rows_by_table[t])
    out.mkdir(parents=True, exist_ok=True)
    for t in ["edi.ingest_run"] + TABLES:
        if con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]:
            con.execute(f"COPY {t} TO '{out / (t + '.parquet')}' (FORMAT parquet, COMPRESSION zstd)")
    return stats


def main(data: Path, business_days: int = 120, start: date = date(2026, 1, 5)):
    raw_root, parsed_root = data / "raw", data / "parsed"
    sc = edi_mock.Scenario(start=start, days=business_days * 7 // 5 + 7, target_mb_per_day=5.0)
    index, known_sha = [], {}
    for dd in edi_mock.generate_days(data / "gen", sc, [CUSTOMER]):
        day = dd.day.isoformat()
        dest = raw_root / day
        shutil.rmtree(dest, ignore_errors=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dd.path), dest)
        run_id = f"run_{day.replace('-', '')}T000000000000_{day.replace('-', '')}"
        c0 = time.process_time()
        st = parse_day(dest, day, parsed_root / day, run_id, known_sha)
        index.append(dict(day=day, run_id=run_id, raw_bytes=dd.bytes, parse_cpu_s=round(time.process_time() - c0, 2), **st))
        print(index[-1], flush=True)
        (data / "days.json").write_text(json.dumps(index, indent=0))
        if len(index) == business_days:
            break
    shutil.rmtree(data / "gen", ignore_errors=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 120)
