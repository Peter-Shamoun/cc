"""Evaluate the pipeline design on the AWS Emulator: gates (must hold) and scores (to minimise or maximise).

    python -m claim_clarity_pipeline.evaluation.run_evaluation --working-directory /tmp/evaluation-1
        [--scenarios control_plane,export_failure,task_crash,throughput] [--throughput-days 7]
        [--set slot_size_limit_bytes=268435456 --set downloader_thread_count=8 ...]

Writes scorecard.json and exits non-zero if a gate fails. A design change is a code change or a --set of any
CustomerPipelineSettings field; compare scorecards.

Scenarios
    control_plane    the whole design at small scale with the alarms in charge (run_emulation): scale from zero,
                     duplicates, dead-letter queue, scale to zero, ephemeral storage, candidate selection twice
    export_failure   lake appends fail part-way through the first export, twice: no file may be dead-lettered
    task_crash       the task dies part-way through an export: its replacement must leave each file in once
    throughput       N days at ~5 MB per business day through the data plane, then candidate selection: the scores

The data-plane scenarios start and stop the parser service themselves instead of waiting for the alarms, so their
time is the pipeline's, not CloudWatch's. Mock data is cached per (days, MB/day) under --mock-cache-directory.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote_plus

from ..customer_settings import SILVER_PREFIX, CustomerPipelineSettings
from ..emulator_connection import configure_process_for_emulator
from ..run_emulation import (EMULATED_QUEUE_METRIC_NAMESPACE, EXAMPLE_CANDIDATE_SELECTION_PRESET, REPOSITORY_ROOT,
                             Observer, lake_summary)

log = logging.getLogger("claim_clarity_pipeline.evaluation")
SCENARIOS = ("control_plane", "export_failure", "task_crash", "throughput")
SILVER_TABLE_COUNT = 25


def evaluation_settings(customer_code: str, overrides: dict) -> CustomerPipelineSettings:
    """Production defaults, except what only shortens waiting: alarm periods, the visibility timeout (a failed
    file is retried after it) and the idle export delay."""
    base = dict(customer_code=customer_code, queue_metric_namespace=EMULATED_QUEUE_METRIC_NAMESPACE,
                queue_visibility_timeout_seconds=30, alarm_period_seconds=10,
                idle_alarm_periods_before_scale_to_zero=3, slot_idle_export_seconds=3.0)
    return CustomerPipelineSettings(**(base | overrides))


def parse_setting_overrides(assignments: list[str]) -> dict:
    types = {f.name: f.type for f in dataclasses.fields(CustomerPipelineSettings)}
    overrides = {}
    for assignment in assignments:
        name, value = assignment.split("=", 1)
        overrides[name] = {"int": int, "float": float, "str": str}[types[name]](value)   # KeyError: no such setting
    return overrides


def mock_edi_directory(cache_directory: Path, days: int, megabytes_per_day: float | None) -> Path:
    """<cache>/<name>/raw with the generated files; generated once."""
    name = f"acme_labs-{days}d-" + (f"{megabytes_per_day:g}mb" if megabytes_per_day else "scale0.5")
    directory = cache_directory / name
    if not (directory / "raw").exists():
        log.info("generating mock EDI: %s", name)
        scratch = cache_directory / f"{name}.generating"
        shutil.rmtree(scratch, ignore_errors=True)
        volume = ["--mb-per-day", str(megabytes_per_day)] if megabytes_per_day else ["--scale", "0.5"]
        subprocess.run([sys.executable, "-m", "ccsim.edi_mock", str(scratch), "--days", str(days), "--tenants",
                        "acme_labs", *volume], cwd=REPOSITORY_ROOT, check=True, stdout=subprocess.DEVNULL)
        directory.mkdir(parents=True)
        (scratch / "acme_labs" / "raw").rename(directory / "raw")
        shutil.rmtree(scratch)
    return directory / "raw"


def merged_files_directory(raw_directory: Path, files_per_merged_file: int) -> Path:
    """The same data as fewer, larger files: each day's files of one kind concatenated in groups (an X12 file may
    hold several interchanges). Built once, next to raw_directory."""
    merged = raw_directory.parent / f"raw-merged-{files_per_merged_file}"
    if not merged.exists():
        scratch = raw_directory.parent / f"raw-merged-{files_per_merged_file}.building"
        shutil.rmtree(scratch, ignore_errors=True)
        for kind_directory in sorted(d for d in raw_directory.glob("*/*") if d.is_dir()):
            files = sorted(f for f in kind_directory.iterdir() if f.is_file())
            target = scratch / kind_directory.relative_to(raw_directory)
            target.mkdir(parents=True)
            # Only files that start at ISA with the same delimiters can share a file; the rest stay as they are.
            by_delimiters: dict[bytes, list[Path]] = {}
            for file in files:
                head = file.read_bytes()[:106]
                mergeable = head.startswith(b"ISA") and len(head) == 106 and head.isascii()
                by_delimiters.setdefault(head[3:4] + head[104:106] if mergeable else file.name.encode(), []).append(file)
            group_number = 0
            for group in by_delimiters.values():
                for start in range(0, len(group), files_per_merged_file):
                    group_number += 1
                    with open(target / f"merged_{group_number:04d}.x12", "wb") as out:
                        for file in group[start:start + files_per_merged_file]:
                            out.write(file.read_bytes().rstrip(b" \r\n\x00") + b"\n")
        scratch.rename(merged)
    return merged


def committed_slots(task_log_directory: Path) -> list[dict]:
    """Every slot export the parser tasks logged, with the time of its commit."""
    exports = []
    for task_log in sorted(task_log_directory.glob("*-parser-*.log")):
        for line in task_log.read_text().splitlines():
            if "committed slot: " in line:
                export = json.loads(line.split("committed slot: ", 1)[1])
                export["committed_at"] = datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S,%f").timestamp()
                exports.append(export)
    return sorted(exports, key=lambda export: export["committed_at"])


def sqs_manager_totals(task_log_directory: Path) -> dict | None:
    """What the last parser task's SQS manager logged when it stopped."""
    totals = None
    for task_log in sorted(task_log_directory.glob("*-parser-*.log")):
        for line in task_log.read_text().splitlines():
            if "sqs manager totals: " in line:
                totals = json.loads(line.split("sqs manager totals: ", 1)[1])
    return totals


def run_data_plane_scenario(scenario: str, working_directory: Path, settings: CustomerPipelineSettings,
                            raw_directory: Path, parser_task_extra_environment: dict[str, str],
                            run_candidate_selection: bool, timeout_seconds: float) -> dict:
    """Onboard, deliver every file under raw_directory (every 10th twice), run the parser service until the queue
    is empty, stop it. Returns {"gates": ..., "scores": ..., "details": ...}."""
    import boto3

    from ..emulator_stand_ins import EmulatorStandIns, fire_schedule_now, move_sftp_incoming_to_s3_input
    from ..provision_customer import provision_customer
    provision_customer(settings, EXAMPLE_CANDIDATE_SELECTION_PRESET,
                       parser_task_module="evaluation.fault_injecting_ingestion_task",
                       parser_task_extra_environment=parser_task_extra_environment)
    session = boto3.Session(region_name=settings.aws_region)
    sqs, ecs, s3 = session.client("sqs"), session.client("ecs"), session.client("s3")
    queue_url = sqs.get_queue_url(QueueName=settings.ingestion_queue_name)["QueueUrl"]
    sqs.purge_queue(QueueUrl=queue_url)                   # the s3:TestEvent messages of onboarding
    service = dict(cluster=settings.ecs_cluster_name, service=settings.parser_service_name)

    stand_ins = EmulatorStandIns(settings, working_directory)
    stand_ins.start()
    observer = Observer(settings, stand_ins)
    try:
        delivery_started = time.time()
        mover = move_sftp_incoming_to_s3_input(settings, raw_directory, redeliver_every_nth_file=10)
        delivery_finished = time.time()
        ecs.update_service(desiredCount=1, **service)     # what the queue alarm does, without its delay
        max_in_flight = 0

        def drained(state: dict) -> bool:
            nonlocal max_in_flight
            max_in_flight = max(max_in_flight, state["queue_in_flight"] or 0)
            return (state["queue_visible"] == 0 and state["queue_in_flight"] == 0
                    and state["local_parser_tasks_running"] == 1)
        observer.wait_until("delivered files are in the queue", lambda s: (s["queue_visible"] or 0) > 0
                            or (s["queue_in_flight"] or 0) > 0, 120)
        observer.wait_until("queue empty and nothing in flight", drained, timeout_seconds)
        ecs.update_service(desiredCount=0, **service)     # what the idle alarm does
        observer.wait_until("task stopped", lambda s: s["local_parser_tasks_running"] == 0, 300)

        dead_lettered = []
        while messages := sqs.receive_message(QueueUrl=observer.dead_letter_queue_url,
                                              MaxNumberOfMessages=10).get("Messages", []):
            dead_lettered += [unquote_plus(record["s3"]["object"]["key"]) for message in messages
                              for record in json.loads(message["Body"])["Records"]]
        lake = lake_summary(settings)
        candidate_seconds = None
        if run_candidate_selection:
            started = time.time()
            task_arn = fire_schedule_now(settings)
            while task_arn not in stand_ins.fargate.exit_codes:
                if time.time() - started > 900:
                    raise TimeoutError("candidate selection task did not finish")
                time.sleep(1)
            if stand_ins.fargate.exit_codes[task_arn] != 0:
                raise RuntimeError("candidate selection task failed; see task-logs")
            candidate_seconds = round(time.time() - started, 1)
    finally:
        stand_ins.stop()
        (working_directory / "timeline.json").write_text(json.dumps(
            {"timeline": observer.timeline, "fargate_events": stand_ins.fargate.events}, indent=1))

    delivered_sha256 = {"input/" + path.relative_to(raw_directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in raw_directory.rglob("*") if path.is_file()}
    good_contents = {sha256 for key, sha256 in delivered_sha256.items()
                     if key not in dead_lettered and f"{key}.redelivered" not in dead_lettered}
    exports = committed_slots(working_directory / "task-logs")
    silver_objects = [o for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=settings.bucket_name, Prefix=f"{SILVER_PREFIX}/") for o in page.get("Contents", [])]
    data_files = [o for o in silver_objects if o["Key"].endswith(".parquet")]
    parser_tasks = [e for e in stand_ins.fargate.events if e["event"].startswith("task stopped")
                    and "peak_resident_memory_bytes" in e]
    raw_megabytes = sum(path.stat().st_size for path in raw_directory.rglob("*") if path.is_file()) / 1e6
    last_commit = exports[-1]["committed_at"] if exports else None
    task_started = min(datetime.fromisoformat(e["at"]).timestamp() for e in stand_ins.fargate.events
                       if e["event"] == "task started" and e["family"] == settings.parser_task_family)
    ingest_seconds = last_commit - task_started if last_commit else None
    megabytes = 1 / 1e6
    return {
        "gates": {
            "every_good_file_content_is_in_the_lake_exactly_once":
                sorted(lake["sha256_in_lake"]) == sorted(good_contents),
            "only_unreadable_files_are_dead_lettered":      # a good file's content is in the lake
                not ({delivered_sha256.get(key.removesuffix(".redelivered")) for key in dead_lettered}
                     & set(lake["sha256_in_lake"])),
            "no_export_journal_left_behind": not any("/_export_journal/" in o["Key"] for o in silver_objects),
            "ephemeral_storage_removed_with_the_task": not any(stand_ins.fargate.ephemeral_root.iterdir()),
        },
        "scores": {
            "raw_megabytes": round(raw_megabytes, 1),
            "files_delivered": mover["files_moved"] + mover["duplicate_deliveries"],
            "ingest_seconds_task_start_to_last_commit": round(ingest_seconds, 1) if ingest_seconds else None,
            "raw_megabytes_per_second": round(raw_megabytes / ingest_seconds, 2) if ingest_seconds else None,
            "seconds_to_first_commit": round(exports[0]["committed_at"] - task_started, 1) if exports else None,
            "slot_exports": len(exports),
            "export_seconds_mean": round(sum(e["export_seconds"] for e in exports) / len(exports), 1) if exports else None,
            "export_seconds_max": max((e["export_seconds"] for e in exports), default=None),
            "failed_export_attempts": sum(e["failed_attempts"] for e in exports),
            # where the task's time goes: worker-seconds per stage (summed over the stage's workers) and the
            # exporter's steps
            "stage_worker_seconds": {stage: round(sum(e.get("stage_seconds", {}).get(stage, 0) for e in exports), 1)
                                     for stage in ("wait_for_downloader", "download", "wait_for_parser", "parse",
                                                   "wait_for_stager", "stage")},
            "sqs_manager": sqs_manager_totals(working_directory / "task-logs"),
            "export_step_seconds": {step: round(sum(e.get("export_step_seconds", {}).get(step, 0) for e in exports), 1)
                                    for step in sorted({k for e in exports for k in e.get("export_step_seconds", {})})},
            "silver_megabytes": round(sum(o["Size"] for o in silver_objects) * megabytes, 1),
            "silver_s3_objects": len(silver_objects),
            "silver_parquet_files": len(data_files),
            "silver_parquet_megabytes_mean": round(sum(o["Size"] for o in data_files) * megabytes / len(data_files), 3)
            if data_files else None,
            "iceberg_snapshots_per_table_max": max(t["snapshots"] for t in lake["tables"].values()),
            "peak_ephemeral_storage_megabytes": round(max((t["peak_ephemeral_storage_bytes"] for t in parser_tasks),
                                                          default=0) * megabytes, 1),
            "peak_task_memory_megabytes": round(max((t["peak_resident_memory_bytes"] for t in parser_tasks),
                                                    default=0) * megabytes, 1),
            "max_messages_in_flight": max_in_flight,
            "dead_lettered_files": len(dead_lettered),
            "candidate_selection_seconds": candidate_seconds,
            "delivery_seconds": round(delivery_finished - delivery_started, 1),
        },
        "details": {"scenario": scenario, "customer_code": settings.customer_code, "dead_lettered": sorted(dead_lettered),
                    "exports": exports, "parser_tasks_run": len(parser_tasks)},
    }


def run_control_plane_scenario(working_directory: Path, customer_code: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "claim_clarity_pipeline.run_emulation", "--working-directory", str(working_directory),
         "--customer-code", customer_code], cwd=REPOSITORY_ROOT, capture_output=True, text=True)
    (working_directory.parent / "control_plane.log").write_text(completed.stdout + completed.stderr)
    report_path = working_directory / "emulation_report.json"
    timeline = json.loads(report_path.read_text())["timeline"] if report_path.exists() else []

    def seconds_between(earlier, later) -> float | None:
        delivered = next((t["seconds"] for t in timeline if (t["queue_visible"] or 0) > 10), None)
        first = next((t["seconds"] for t in timeline if delivered is not None and t["seconds"] >= delivered
                      and earlier(t)), None)
        second = next((t["seconds"] for t in timeline if first is not None and t["seconds"] >= first and later(t)), None)
        return None if second is None else second - first
    return {
        "gates": {"whole_design_run_passes_its_checks": completed.returncode == 0},
        "scores": {
            "seconds_from_files_queued_to_task_running": seconds_between(
                lambda t: (t["queue_visible"] or 0) > 10, lambda t: t["local_parser_tasks_running"] == 1),
            "seconds_from_queue_drained_to_task_stopped": seconds_between(
                lambda t: t["queue_visible"] == 0 and t["queue_in_flight"] == 0 and t["dead_letter_queue"] >= 1,
                lambda t: t["local_parser_tasks_running"] == 0)},
        "details": {"customer_code": customer_code},
    }


def main():
    arguments = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    arguments.add_argument("--working-directory", type=Path, required=True)
    arguments.add_argument("--scenarios", default=",".join(SCENARIOS))
    arguments.add_argument("--throughput-days", type=int, default=7)
    arguments.add_argument("--throughput-megabytes-per-day", type=float, default=5.0)
    arguments.add_argument("--throughput-raw-directory", type=Path,
                           help="deliver the files under this directory instead of generating mock data")
    arguments.add_argument("--throughput-files-per-merged-file", type=int, default=1,
                           help="deliver the same data as larger files: this many files of a day concatenated")
    arguments.add_argument("--throughput-customers", type=int, default=1,
                           help="run this many customers' pipelines at once, each with the same data")
    arguments.add_argument("--throughput-timeout-seconds", type=float, default=3600)
    arguments.add_argument("--mock-cache-directory", type=Path, default=Path.home() / ".cache" / "claim-clarity-mock-edi")
    arguments.add_argument("--set", action="append", default=[], metavar="SETTING=VALUE",
                           help="override a CustomerPipelineSettings field in the data-plane scenarios")
    arguments.add_argument("--emulator-port", type=int, default=9297)
    options = arguments.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("pyiceberg").setLevel(logging.ERROR)
    overrides = parse_setting_overrides(options.set)
    options.working_directory.mkdir(parents=True, exist_ok=False)
    configure_process_for_emulator(options.emulator_port)
    run_tag = f"{int(time.time()) % 100000:05d}"          # the emulator keeps earlier runs' customers
    scorecard = {"setting_overrides": overrides, "scenarios": {}}
    for scenario in options.scenarios.split(","):
        directory = options.working_directory / scenario
        customer_code = f"eval{run_tag}{scenario.replace('_', '')[:10]}"
        log.info("=== scenario %s (customer %s)", scenario, customer_code)
        started = time.time()
        try:
            if scenario == "control_plane":
                result = run_control_plane_scenario(directory, customer_code)
            else:
                directory.mkdir()
                settings = evaluation_settings(customer_code, overrides)
                small = lambda: mock_edi_directory(options.mock_cache_directory, 5, None)   # noqa: E731
                if scenario == "export_failure":          # the first export fails at its 5th table append
                    result = run_data_plane_scenario(scenario, directory, settings, small(), {
                        "CLAIM_CLARITY_EVALUATION_FAIL_LAKE_APPENDS": "5-5"}, False, 600)
                    result["gates"]["the_failed_export_was_retried_in_the_task"] = (
                        result["scores"]["failed_export_attempts"] >= 1 and result["details"]["parser_tasks_run"] == 1)
                    result["gates"]["no_file_dead_lettered"] = result["scores"]["dead_lettered_files"] == 0
                elif scenario == "task_crash":
                    result = run_data_plane_scenario(scenario, directory, settings, small(), {
                        "CLAIM_CLARITY_EVALUATION_CRASH_AT_LAKE_APPEND": "12"}, False, 900)
                    result["gates"]["a_replacement_task_finished_the_work"] = result["details"]["parser_tasks_run"] >= 2
                    result["gates"]["no_file_dead_lettered"] = result["scores"]["dead_lettered_files"] == 0
                elif scenario == "throughput":
                    raw_directory = options.throughput_raw_directory or mock_edi_directory(
                        options.mock_cache_directory, options.throughput_days, options.throughput_megabytes_per_day)
                    if options.throughput_files_per_merged_file > 1:
                        raw_directory = merged_files_directory(raw_directory, options.throughput_files_per_merged_file)
                    if options.throughput_customers == 1:
                        result = run_data_plane_scenario(scenario, directory, settings, raw_directory, {}, True,
                                                         options.throughput_timeout_seconds)
                    else:                                 # every customer gets the same data, at the same time
                        def one_customer(number: int) -> dict:
                            customer_directory = directory / f"customer{number}"
                            customer_directory.mkdir()
                            return run_data_plane_scenario(
                                scenario, customer_directory, evaluation_settings(f"{customer_code}{number}", overrides),
                                raw_directory, {}, True, options.throughput_timeout_seconds)
                        with ThreadPoolExecutor(options.throughput_customers) as pool:
                            per_customer = list(pool.map(one_customer, range(1, options.throughput_customers + 1)))
                        result = {
                            "gates": {gate: all(r["gates"][gate] for r in per_customer) for gate in per_customer[0]["gates"]},
                            "scores": {"customers": len(per_customer)} | {
                                f"{name}_{statistic.__name__}": statistic(r["scores"][name] for r in per_customer)
                                for name in ("ingest_seconds_task_start_to_last_commit", "raw_megabytes_per_second",
                                             "peak_task_memory_megabytes", "peak_ephemeral_storage_megabytes",
                                             "candidate_selection_seconds") for statistic in (min, max)},
                            "details": {"per_customer": per_customer}}
                else:
                    raise ValueError(f"unknown scenario {scenario!r}; choose from {SCENARIOS}")
        except Exception as error:                        # noqa: BLE001 - a scenario that cannot finish fails its gate
            log.exception("scenario %s did not finish", scenario)
            result = {"gates": {"scenario_finished": False}, "scores": {}, "details": {"error": f"{type(error).__name__}: {error}"}}
        result["scenario_seconds"] = round(time.time() - started, 1)
        scorecard["scenarios"][scenario] = result
        log.info("scenario %s: gates %s", scenario, result["gates"])
    scorecard["all_gates_pass"] = all(all(result["gates"].values()) for result in scorecard["scenarios"].values())
    (options.working_directory / "scorecard.json").write_text(json.dumps(scorecard, indent=2, default=str))
    print(json.dumps({name: {"gates": result["gates"], "scores": result["scores"],
                             "scenario_seconds": result["scenario_seconds"]}
                      for name, result in scorecard["scenarios"].items()}, indent=2))
    print(f"all gates pass: {scorecard['all_gates_pass']}; scorecard: {options.working_directory / 'scorecard.json'}")
    sys.exit(0 if scorecard["all_gates_pass"] else 1)


if __name__ == "__main__":
    main()
