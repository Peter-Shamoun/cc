"""Parser benchmark: CPU, memory and Parquet bytes of bronze -> silver, plus an equivalence oracle.

The measured code is the pipeline's own silver path (ccsim.pipeline.SilverBatch + silver_part_files), run on local
files instead of S3, one batch per received day (as the Fargate task runs), and one batch per synthetic large file.

    python -m bench.parser_bench corpus                  # build the cached corpus (~1 GB disk at most)
    python -m bench.parser_bench freeze                  # run the current parser once: the reference output
    python -m bench.parser_bench run --label v1          # measure + prove identical to the reference
    python -m bench.parser_bench profile --batch large_835_35mb [--cprofile]

Corpus (.ccsim/bench/parser/corpus/): generator output at production volume (Scenario(target_mb_per_day=5), one
tenant, ~10 business days), a stress run for the generator's delivery noise (BOM, NUL padding, 80-column wrapping,
several ISAs per file, corrupt files), derived variants of every noise kind, and large files of 20-50 MB made by
concatenating whole interchanges.

Metrics: CPU seconds (time.process_time, all threads of the batch's process; wall-clock is not trusted on a shared
machine) per raw MB, split parse / stage / write; peak RSS of the batch's own fresh process (VmHWM) and its
growth over the process after imports; Parquet bytes written.

Oracle: the reference is the output of the parser as it was before optimisation. A candidate is identical when,
for every batch, the same tables are written, each with the same Arrow schema (names, types, nullability), the
same row count and the same multiset of rows (DuckDB EXCEPT ALL both ways), and every file opens in DuckDB and in
pyarrow. Keys are columns, so key values are compared too. Quarantine decisions and count_check_ok flags are part
of edi.source_file / edi.transaction_set and are also reported on their own.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BENCH_DIR = ROOT / ".ccsim" / "bench" / "parser"
CORPUS_DIR = BENCH_DIR / "corpus"
REFERENCE_DIR = BENCH_DIR / "reference"
RESULTS_DIR = BENCH_DIR / "results"
CUSTOMER = "acme_labs"
VOLUME_SCENARIO = dict(target_mb_per_day=5, days=15)          # 15 calendar days from 2026-07-01 = 10 business days
STRESS_SCENARIO = dict(days=60, volume_scale=.3, seed=11)
LARGE_FILES = {"large_837_20mb": ("837", 20), "large_835_35mb": ("835", 35), "large_835_50mb": ("835", 50)}
MB = 1e6


# --- corpus ------------------------------------------------------------------------------------------------------
def _generated_files(out: Path) -> list[dict]:
    files = json.loads((out / "truth.json").read_text())["tenants"][CUSTOMER]["files"]
    return [dict(path=str((out / CUSTOMER / "raw" / f"received_date={f['day']}" / f["kind"] / f["name"])
                          .relative_to(CORPUS_DIR)), day=f["day"], kind=f["kind"], expect=f["expect"]) for f in files]


def _generate(name: str, scenario) -> list[dict]:
    from ccsim import edi_mock
    out = CORPUS_DIR / name
    if not (out / "truth.json").exists():
        shutil.rmtree(out, ignore_errors=True)
        edi_mock.generate(out, scenario, [CUSTOMER], return_claims=False)
        shutil.rmtree(out / "truth", ignore_errors=True)          # the claim ledger is not needed here
    return _generated_files(out)


def _is_plain_interchange(body: bytes) -> bool:
    return body[:3] == b"ISA" and b"\n" not in body[:106] and not body.endswith((b"\x00", b" "))


def _noise_variants(sample_837: bytes, sample_835: bytes) -> dict[str, bytes]:
    """Every delivery-noise kind the parser must survive, and every corruption it must quarantine, derived from a
    plain file (so each kind is present whatever the generator's random draws were)."""
    s, r = sample_837, sample_835
    flat = s.replace(b"\r", b"").replace(b"\n", b"")
    el = s[3:4]
    return {
        "bom.x12": b"\xef\xbb\xbf" + s,
        "leading_newline.x12": b"\r\n" + s,
        "leading_spaces.x12": b"  \t" + s,
        "nul_padding.x12": s + b"\x00" * 200,
        "space_padding.x12": s + b" " * 80,
        "wrapped_80_lf.x12": b"\n".join(flat[i:i + 80] for i in range(0, len(flat), 80)) + b"\n",
        "wrapped_80_crlf.x12": b"\r\n".join(flat[i:i + 80] for i in range(0, len(flat), 80)) + b"\r\n",
        "two_interchanges.x12": s + s,
        "mixed_837_835.x12": s + r if s[3:4] == r[3:4] and s[104:107] == r[104:107] else s + s,
        "crlf_after_terminator.x12": flat.replace(s[105:106], s[105:106] + b"\r\n"),
        "corrupt_truncated.x12": s[: len(s) // 2],
        "corrupt_not_x12.x12": b"%PDF-1.4\n% scanned remittance image\n",
        "corrupt_no_iea.x12": s.replace(b"IEA" + el, b"IEX" + el, 1),
        "corrupt_se_control.x12": s.replace(b"SE" + el, b"SE" + el + b"9", 1),
        "corrupt_non_ascii.x12": s.replace(b"NM1" + el, "NM1é".encode() + el, 1),
        "corrupt_empty.x12": b"",
        "corrupt_segment_outside_txn.x12": s.replace(b"GS" + el, b"REF" + el + b"X" + s[105:106] + b"GS" + el, 1),
        "bad_se_count.x12": _bad_se_count(s),
    }


def _bad_se_count(body: bytes) -> bytes:
    """SE01 off by one: kept and flagged (count_check_ok = false), not rejected."""
    el, term = re.escape(body[3:4]), re.escape(body[105:106])
    m = re.search(term + rb"[\r\n]*SE" + el + rb"(\d+)", body)
    return body[:m.start(1)] + str(int(m.group(1)) + 1).encode() + body[m.end(1):]


def build_corpus() -> dict:
    from ccsim.edi_mock import Scenario
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = CORPUS_DIR / "corpus.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text())
    volume = _generate("volume", Scenario(**VOLUME_SCENARIO))
    stress = _generate("stress", Scenario.stress(**STRESS_SCENARIO))
    batches = {}
    for prefix, files in (("day", volume), ("stress", stress)):
        for f in files:
            batches.setdefault(f"{prefix}_{f['day']}", []).append(dict(path=f["path"], day=f["day"]))

    plain = {k: [f for f in volume if f["kind"] == k and f["expect"] == "parsed"
                 and _is_plain_interchange((CORPUS_DIR / f["path"]).read_bytes())] for k in ("837", "835")}
    noise_dir = CORPUS_DIR / "noise"
    noise_dir.mkdir(exist_ok=True)
    sample = {k: (CORPUS_DIR / min(plain[k], key=lambda f: (CORPUS_DIR / f["path"]).stat().st_size)["path"]).read_bytes()
              for k in plain}
    for name, body in _noise_variants(sample["837"], sample["835"]).items():
        (noise_dir / name).write_bytes(body)
        batches.setdefault("noise_variants", []).append(dict(path=f"noise/{name}", day="2026-07-01"))

    large_dir = CORPUS_DIR / "large"
    large_dir.mkdir(exist_ok=True)
    for name, (kind, mb) in LARGE_FILES.items():
        bodies = [(CORPUS_DIR / f["path"]).read_bytes() for f in plain[kind]]
        style = max({b[3:4] + b[104:107] for b in bodies}, key=lambda st: sum(b[3:4] + b[104:107] == st for b in bodies))
        bodies = [b for b in bodies if b[3:4] + b[104:107] == style]       # one delimiter set per file
        with open(large_dir / f"{name}.x12", "wb") as fh:
            written, i = 0, 0
            while written < mb * MB:
                fh.write(bodies[i % len(bodies)])
                written += len(bodies[i % len(bodies)])
                i += 1
        batches[name] = [dict(path=f"large/{name}.x12", day="2026-07-15")]
    manifest = dict(batches=batches, raw_bytes={b: sum((CORPUS_DIR / f["path"]).stat().st_size for f in fs)
                                                 for b, fs in batches.items()})
    manifest_path.write_text(json.dumps(manifest, indent=1))
    return manifest


# --- one batch, in its own process ----------------------------------------------------------------------------------
def _meta(path: str, body: bytes, day: str) -> dict:
    return dict(s3_uri=f"s3://bench/{path}", s3_bucket="bench", s3_key=path, s3_version_id="v1",
                s3_etag=hashlib.md5(body).hexdigest(), s3_last_modified=datetime.fromisoformat(day) + timedelta(hours=6),
                s3_region="us-west-2", kms_key_arn="arn:aws:kms:us-west-2:000000000000:key/bench",
                original_filename=path.rsplit("/", 1)[-1], source_system="sftp", size_bytes=len(body),
                sha256=hashlib.sha256(body).hexdigest(), received_at=datetime.fromisoformat(day),
                character_encoding="ascii")


def peak_rss_mb() -> float:
    """This process's own RSS high-water mark (VmHWM). Not ru_maxrss: Linux carries that across fork + exec, so a
    batch process would report its parent's peak."""
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmHWM:"):
            return int(line.split()[1]) / 1024
    raise RuntimeError("no VmHWM in /proc/self/status")


def run_batch(batch_name: str, out_dir: Path, use_tracemalloc: bool = False) -> dict:
    """Parse and write one batch exactly as the pipeline does; returns its metrics. Call in a fresh process."""
    from ccsim.pipeline import SilverBatch, silver_part_files
    files = build_corpus()["batches"][batch_name]
    rss_after_imports = peak_rss_mb()
    if use_tracemalloc:
        import tracemalloc
        tracemalloc.start()
    out_dir.mkdir(parents=True, exist_ok=True)
    started = datetime(2026, 10, 1, 12) + timedelta(days=sorted(build_corpus()["batches"]).index(batch_name))
    known_sha, raw_bytes, rejected = {}, 0, []
    cpu0 = time.process_time()
    batch = SilverBatch(f"run_bench_{batch_name}", started, triggered_by="bench", notes=None)
    for f in files:
        body = (CORPUS_DIR / f["path"]).read_bytes()
        raw_bytes += len(body)
        meta = _meta(f["path"], body, f["day"])
        file_id = hashlib.sha1(f"{f['path']}#v1".encode()).hexdigest()[:16]
        if batch.add_raw_file(body, file_id, CUSTOMER, meta, duplicate_of_file_id=known_sha.get(meta["sha256"])):
            known_sha.setdefault(meta["sha256"], file_id)
        else:
            rejected.append(f["path"])
        del body
    cpu1 = time.process_time()
    con = batch.finish()
    con.execute("UPDATE edi.ingest_run SET finished_at = ?, files_seen = ?, files_loaded = ?, files_rejected = ?",
                [started + timedelta(minutes=5), len(files), len(files) - len(rejected), len(rejected)])
    cpu2 = time.process_time()
    parquet_bytes = {t: p.stat().st_size for t, p in silver_part_files(con, out_dir) if p is not None}
    cpu3 = time.process_time()
    for p in out_dir.glob("*.parquet"):                            # empty tables are not published
        if p.stem not in parquet_bytes:
            p.unlink()
    traced_peak = None
    if use_tracemalloc:
        import tracemalloc
        traced_peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    return dict(batch=batch_name, files=len(files), rejected=rejected, raw_bytes=raw_bytes,
                cpu_parse_s=cpu1 - cpu0, cpu_stage_s=cpu2 - cpu1, cpu_write_s=cpu3 - cpu2, cpu_s=cpu3 - cpu0,
                peak_rss_mb=peak_rss_mb(), rss_after_imports_mb=rss_after_imports,
                traced_peak_mb=traced_peak and traced_peak / 2**20, parquet_bytes=parquet_bytes)


def _run_batch_in_subprocess(batch_name: str, out_dir: Path, use_tracemalloc: bool) -> dict:
    cmd = [sys.executable, "-m", "bench.parser_bench", "_batch", batch_name, str(out_dir)]
    if use_tracemalloc:
        cmd.append("--tracemalloc")
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"batch {batch_name} failed:\n{done.stderr[-4000:]}")
    return json.loads(done.stdout.strip().splitlines()[-1])


# --- oracle ------------------------------------------------------------------------------------------------------
def _schema_of(path: Path) -> list:
    import pyarrow.parquet as pq
    return [(f.name, str(f.type), f.nullable) for f in pq.read_schema(path)]


def compare_batch(reference_dir: Path, candidate_dir: Path, slim_segment: bool = False) -> list[str]:
    """Every difference between the reference's and the candidate's Parquet for one batch (empty = identical).
    slim_segment: edi.segment is compared on the columns the slimmed table keeps (keys, segment_id) only."""
    import duckdb
    import pyarrow.parquet as pq
    problems = []
    ref = {p.stem: p for p in reference_dir.glob("*.parquet")}
    cand = {p.stem: p for p in candidate_dir.glob("*.parquet")}
    if set(ref) != set(cand):
        problems.append(f"tables differ: only in reference {sorted(set(ref) - set(cand))}, "
                        f"only in candidate {sorted(set(cand) - set(ref))}")
    con = duckdb.connect()
    for t in sorted(set(ref) & set(cand)):
        try:
            pq.read_table(cand[t])
        except Exception as err:                                          # noqa: BLE001 - report any reader failure
            problems.append(f"{t}: pyarrow cannot read the candidate: {err}")
            continue
        columns = "*"
        if slim_segment and t == "edi.segment":
            kept = ["file_id", "seg_ord", "interchange_uid", "group_uid", "txn_uid", "seg_ord_in_txn", "segment_id",
                    "loop_id", "loop_path", "loop_instance_uid"]
            columns = ", ".join(kept)
            ref_schema = [c for c in _schema_of(ref[t]) if c[0] in kept]
            cand_schema = [c for c in _schema_of(cand[t]) if c[0] in kept]
        else:
            ref_schema, cand_schema = _schema_of(ref[t]), _schema_of(cand[t])
        if ref_schema != cand_schema:
            problems.append(f"{t}: schema differs\n  reference {ref_schema}\n  candidate {cand_schema}")
            continue
        r, c = f"read_parquet('{ref[t]}')", f"read_parquet('{cand[t]}')"
        n_ref, n_cand = (con.execute(f"SELECT count(*) FROM {x}").fetchone()[0] for x in (r, c))
        only_ref = con.execute(f"SELECT count(*) FROM (SELECT {columns} FROM {r} EXCEPT ALL SELECT {columns} FROM {c})").fetchone()[0]
        only_cand = con.execute(f"SELECT count(*) FROM (SELECT {columns} FROM {c} EXCEPT ALL SELECT {columns} FROM {r})").fetchone()[0]
        if (n_ref, only_ref, only_cand) != (n_cand, 0, 0):
            problems.append(f"{t}: rows reference {n_ref} candidate {n_cand}; {only_ref} only in reference, "
                            f"{only_cand} only in candidate")
    return problems


def decisions(directory: Path) -> dict:
    """Quarantine decisions and SE01 count checks of one batch's output."""
    import duckdb
    con = duckdb.connect()
    out = dict(rejected=[], count_check_failed=0)
    if (directory / "edi.source_file.parquet").exists():
        out["rejected"] = sorted(con.execute(
            f"SELECT s3_key, parse_messages FROM read_parquet('{directory}/edi.source_file.parquet') "
            "WHERE parse_status = 'REJECTED'").fetchall())
    if (directory / "edi.transaction_set.parquet").exists():
        out["count_check_failed"] = con.execute(
            f"SELECT count(*) FILTER (WHERE NOT count_check_ok) FROM read_parquet('{directory}/edi.transaction_set.parquet')"
        ).fetchone()[0]
    return out


# --- commands ------------------------------------------------------------------------------------------------------
def _summary(results: list[dict]) -> dict:
    raw = sum(r["raw_bytes"] for r in results)
    volume = [r for r in results if r["batch"].startswith("day_")]
    large = [r for r in results if r["batch"].startswith("large_")]
    return dict(raw_mb=round(raw / MB, 1), cpu_s=round(sum(r["cpu_s"] for r in results), 1),
                cpu_s_per_mb=round(sum(r["cpu_s"] for r in results) / (raw / MB), 4),
                volume_cpu_s_per_mb=round(sum(r["cpu_s"] for r in volume) / (sum(r["raw_bytes"] for r in volume) / MB), 4),
                parse_share=round(sum(r["cpu_parse_s"] for r in results) / sum(r["cpu_s"] for r in results), 3),
                peak_rss_mb_volume_batch=round(max(r["peak_rss_mb"] for r in volume), 0),
                peak_rss_mb_by_large_file={r["batch"]: round(r["peak_rss_mb"], 0) for r in large},
                parquet_mb=round(sum(sum(r["parquet_bytes"].values()) for r in results) / MB, 1),
                segment_parquet_mb=round(sum(r["parquet_bytes"].get("edi.segment", 0) for r in results) / MB, 1))


def cmd_run(label: str, freeze: bool, only: list[str] | None, use_tracemalloc: bool, slim_segment: bool,
            keep_output: bool) -> dict:
    manifest = build_corpus()
    names = only or sorted(manifest["batches"])
    if freeze:
        shutil.rmtree(REFERENCE_DIR, ignore_errors=True)
    out_root = REFERENCE_DIR if freeze else BENCH_DIR / "candidate" / label
    shutil.rmtree(out_root, ignore_errors=True)
    results, problems = [], {}
    for name in names:
        r = _run_batch_in_subprocess(name, out_root / name, use_tracemalloc)
        if not freeze:
            found = compare_batch(REFERENCE_DIR / name, out_root / name, slim_segment)
            if decisions(REFERENCE_DIR / name) != decisions(out_root / name):
                found.append("quarantine decisions or count_check_ok flags differ")
            if found:
                problems[name] = found
        r["decisions"] = decisions(out_root / name)
        results.append(r)
        print(f"{name:28s} {r['raw_bytes'] / MB:7.2f} MB  cpu {r['cpu_s']:7.2f}s = {r['cpu_s'] / max(r['raw_bytes'] / MB, 1e-9):6.3f} s/MB"
              f" (parse {r['cpu_parse_s']:.2f} stage {r['cpu_stage_s']:.2f} write {r['cpu_write_s']:.2f})"
              f"  rss {r['peak_rss_mb']:6.0f} MB  {'' if freeze else ('IDENTICAL' if name not in problems else 'DIFFERENT')}",
              flush=True)
    if freeze:
        (REFERENCE_DIR / "schemas.json").write_text(json.dumps(
            {name: {p.stem: _schema_of(p) for p in sorted((REFERENCE_DIR / name).glob("*.parquet"))} for name in names},
            indent=1))
    elif not keep_output:
        shutil.rmtree(out_root)
    report = dict(label=label, frozen_reference=freeze, identical=None if freeze else not problems,
                  problems=problems, summary=_summary(results), batches=results)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / f"{label}.json").write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(dict(label=label, identical=report["identical"], **report["summary"]), indent=1))
    for name, found in problems.items():
        print(f"DIFFERENT {name}:", *found[:10], sep="\n  ")
    return report


def cmd_profile(batch_name: str, use_cprofile: bool):
    out = BENCH_DIR / "profile" / batch_name
    shutil.rmtree(out, ignore_errors=True)
    if use_cprofile:
        import cProfile
        import pstats
        prof = cProfile.Profile()
        prof.enable()
        r = run_batch(batch_name, out)
        prof.disable()
        pstats.Stats(prof).sort_stats("tottime").print_stats(35)
    else:
        r = run_batch(batch_name, out, use_tracemalloc=True)
    shutil.rmtree(out, ignore_errors=True)
    print(json.dumps(r, indent=1, default=str))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("corpus")
    sub.add_parser("freeze")
    run = sub.add_parser("run")
    run.add_argument("--label", required=True)
    run.add_argument("--only", nargs="*", help="batch names (default: all)")
    run.add_argument("--tracemalloc", action="store_true", help="also record the Python heap peak (slower)")
    run.add_argument("--slim-segment", action="store_true", help="edi.segment without raw_text/elements (step 5)")
    run.add_argument("--keep-output", action="store_true")
    prof = sub.add_parser("profile")
    prof.add_argument("--batch", required=True)
    prof.add_argument("--cprofile", action="store_true")
    one = sub.add_parser("_batch")
    one.add_argument("batch")
    one.add_argument("out_dir", type=Path)
    one.add_argument("--tracemalloc", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "corpus":
        m = build_corpus()
        print(f"{len(m['batches'])} batches, {sum(m['raw_bytes'].values()) / MB:.1f} MB raw in {CORPUS_DIR}")
    elif a.cmd == "freeze":
        cmd_run("reference", True, None, False, False, True)
    elif a.cmd == "run":
        cmd_run(a.label, False, a.only, a.tracemalloc, a.slim_segment, a.keep_output)
    elif a.cmd == "profile":
        cmd_profile(a.batch, a.cprofile)
    elif a.cmd == "_batch":
        print(json.dumps(run_batch(a.batch, a.out_dir, a.tracemalloc), default=str))


if __name__ == "__main__":
    main()
