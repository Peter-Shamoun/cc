"""Run the whole design against the AWS Emulator, end to end, and write a report.

    python -m claim_clarity_pipeline.run_emulation --working-directory /tmp/claim-clarity-emulation

Needs a running emulator (aws-emulator --telemetry off). Steps: onboard the customer, generate mock 837P/835
files into the SFTP incoming directory, run the IT mover job (with duplicate deliveries and one corrupt file),
watch the alarms start the parser task, the queue drain into the Iceberg silver lake, the corrupt file reach the
dead-letter queue and the idle alarm stop the task; then fire candidate selection twice and move the CSV out.

The timings are shortened so a run takes minutes (alarm periods of 10 s, a 20 s visibility timeout, 8 MB slots);
CustomerPipelineSettings holds the production defaults.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote_plus

import boto3

from .customer_settings import CustomerPipelineSettings
from .emulator_connection import configure_process_for_emulator

# The pipeline modules import Arrow, which reads the trusted certificates when it loads. They are imported in run(),
# after configure_process_for_emulator.
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

log = logging.getLogger("claim_clarity_pipeline.run_emulation")
EMULATED_QUEUE_METRIC_NAMESPACE = "ClaimClarity/EmulatedSQS"       # PutMetricData refuses the AWS/ namespaces

EXAMPLE_CANDIDATE_SELECTION_PRESET = {
    "preset_name": "denied-claims-over-minimum-charge",
    "description": "Example only. Latest submission of each claim that its primary payer denied, at or above a "
                   "minimum charge.",
    "parameters": {"minimum_charge_amount": 100},
    "selection_sql": "SELECT claim_key, payer_name, payer_class, charge_amount, service_date_from, "
                     "submission_date, latest_claim_status, remit_paid_amount, billing_npi "
                     "FROM sem.claims "
                     "WHERE is_latest_submission AND is_denied AND charge_amount >= $minimum_charge_amount",
}


def emulation_settings(customer_code: str) -> CustomerPipelineSettings:
    return CustomerPipelineSettings(
        customer_code=customer_code, queue_metric_namespace=EMULATED_QUEUE_METRIC_NAMESPACE,
        queue_visibility_timeout_seconds=20, alarm_period_seconds=10, idle_alarm_periods_before_scale_to_zero=3,
        slot_size_limit_bytes=8 * 1024 ** 2, slot_idle_export_seconds=3.0, in_memory_queue_max_files=32)


class Observer:
    """Polls the AWS state the design is about and records each change with its time."""

    def __init__(self, settings: CustomerPipelineSettings, stand_ins):
        self.settings, self.stand_ins = settings, stand_ins
        session = boto3.Session(region_name=settings.aws_region)
        self.sqs, self.ecs, self.cloudwatch = session.client("sqs"), session.client("ecs"), session.client("cloudwatch")
        self.dead_letter_queue_url = self.sqs.get_queue_url(QueueName=settings.dead_letter_queue_name)["QueueUrl"]
        self.started_at = time.monotonic()
        self.timeline: list[dict] = []
        self.last_state: dict = {}

    def state(self) -> dict:
        alarms = self.cloudwatch.describe_alarms(AlarmNames=[self.settings.queue_has_files_alarm_name,
                                                             self.settings.queue_idle_alarm_name])["MetricAlarms"]
        service = self.ecs.describe_services(cluster=self.settings.ecs_cluster_name,
                                             services=[self.settings.parser_service_name])["services"][0]
        dead_letters = self.sqs.get_queue_attributes(QueueUrl=self.dead_letter_queue_url, AttributeNames=[
            "ApproximateNumberOfMessages"])["Attributes"]["ApproximateNumberOfMessages"]
        depth = self.stand_ins.queue_depth
        return {"queue_visible": depth.get("ApproximateNumberOfMessagesVisible"),
                "queue_in_flight": depth.get("ApproximateNumberOfMessagesNotVisible"),
                "dead_letter_queue": int(dead_letters),
                "queue_has_files_alarm": next(a["StateValue"] for a in alarms
                                              if a["AlarmName"] == self.settings.queue_has_files_alarm_name),
                "queue_idle_alarm": next(a["StateValue"] for a in alarms
                                         if a["AlarmName"] == self.settings.queue_idle_alarm_name),
                "service_desired_count": service["desiredCount"],
                "local_parser_tasks_running": sum(t.is_service_task for t in self.stand_ins.fargate.running.values())}

    def wait_until(self, description: str, condition, timeout_seconds: float) -> dict:
        deadline = time.monotonic() + timeout_seconds
        while True:
            state = self.state()
            if state != self.last_state:
                self.last_state = state
                self.timeline.append({"seconds": round(time.monotonic() - self.started_at), **state})
                log.info("t+%4ds %s", self.timeline[-1]["seconds"], json.dumps(state))
            if condition(state):
                log.info("reached: %s", description)
                return state
            if time.monotonic() > deadline:
                raise TimeoutError(f"not reached within {timeout_seconds}s: {description}; last state {state}")
            time.sleep(1)


def lake_summary(settings: CustomerPipelineSettings) -> dict:
    from .silver_lake import SILVER_TABLES, SilverLake
    lake = SilverLake(settings)
    tables = {}
    for name in SILVER_TABLES:
        table = lake.table(name)
        tables[name] = {"rows": sum(task.file.record_count for task in table.scan().plan_files()),
                        "parquet_files": len(list(table.scan().plan_files())), "snapshots": len(table.snapshots())}
    source_files = lake.read("edi.source_file", ("s3_key", "sha256", "file_kind", "segment_count"))
    return {"tables": tables, "files_in_lake": source_files.num_rows,
            "segments_counted_by_the_parser": sum(source_files["segment_count"].to_pylist()),
            "sha256_in_lake": sorted(source_files["sha256"].to_pylist()),
            "distinct_file_contents_in_lake": len(set(source_files["sha256"].to_pylist())),
            "file_kinds": {kind: source_files["file_kind"].to_pylist().count(kind)
                           for kind in sorted(set(source_files["file_kind"].to_pylist()))},
            "ingest_runs": lake.read("edi.ingest_run", ("ingest_run_id", "files_seen", "files_loaded", "notes"))
                               .sort_by("ingest_run_id").to_pylist()}


def check_report(report: dict):
    """The run did what the design says, or it fails here."""
    lake, mover = report["silver_lake"], report["mover_job"]
    problems = []
    # Files are compared by content: the mover job redelivers files, and the mock generator delivers some
    # byte-identical files under two names itself. Each distinct content belongs in the lake exactly once.
    dead_lettered = set(report["dead_letter_queue_keys"])
    sha256_of_good_deliveries = {sha256 for key, sha256 in report["delivered_sha256_by_s3_key"].items()
                                 if key not in dead_lettered}
    if set(lake["sha256_in_lake"]) != sha256_of_good_deliveries:
        problems.append("a delivered file is neither in the lake nor in the dead-letter queue")
    if len(set(lake["sha256_in_lake"])) != len(lake["sha256_in_lake"]):
        problems.append("the same file content is in the lake twice")
    if lake["tables"]["edi.segment"]["rows"] != lake["segments_counted_by_the_parser"]:
        problems.append("the lake's segment rows are not exactly those of its files")
    deliveries = len(report["delivered_sha256_by_s3_key"]) + mover["duplicate_deliveries"] - len(dead_lettered)
    if sum(run["files_seen"] - run["files_loaded"] for run in lake["ingest_runs"]) != deliveries - lake["files_in_lake"]:
        problems.append("the exports did not skip exactly the duplicate deliveries")
    if not any(key.endswith("corrupt_delivery.x12") for key in dead_lettered):
        problems.append(f"the corrupt file is not in the dead-letter queue: {sorted(dead_lettered)}")
    if report["ephemeral_storage_directories"]["after_task_stopped"]:
        problems.append("ephemeral storage outlived its task")
    first, second = report["candidate_selection_runs"]
    if not first["new_candidate_count"] or second["new_candidate_count"]:
        problems.append(f"candidate runs selected {first['new_candidate_count']} then {second['new_candidate_count']}")
    if len(report["sftp_outgoing"]) != 1 or report["sftp_outgoing"][0]["lines"] != first["new_candidate_count"] + 1:
        problems.append("the candidates CSV in SFTP outgoing does not hold the first run's candidates")
    if problems:
        raise AssertionError("; ".join(problems))


def run(working_directory: Path, customer_code: str, mock_days: int, emulator_port: int) -> dict:
    endpoint_url = configure_process_for_emulator(emulator_port)
    from .emulator_stand_ins import (EmulatorStandIns, fire_schedule_now, move_s3_output_to_sftp_outgoing,
                                     move_sftp_incoming_to_s3_input)
    from .provision_customer import provision_customer
    settings = emulation_settings(customer_code)
    sftp_incoming = working_directory / "sftp" / "incoming" / customer_code
    sftp_outgoing = working_directory / "sftp" / "outgoing" / customer_code
    report: dict = {"emulator_endpoint": endpoint_url, "customer_code": customer_code}

    log.info("onboarding %s", customer_code)
    provision_customer(settings, EXAMPLE_CANDIDATE_SELECTION_PRESET)

    log.info("generating %d days of mock 837P/835 into the SFTP incoming directory", mock_days)
    mock_directory = working_directory / "mock-edi"
    subprocess.run([sys.executable, "-m", "ccsim.edi_mock", str(mock_directory), "--days", str(mock_days),
                    "--tenants", "acme_labs", "--scale", "0.5"], cwd=REPOSITORY_ROOT, check=True,
                   stdout=subprocess.DEVNULL)
    sftp_incoming.parent.mkdir(parents=True)
    (mock_directory / "acme_labs" / "raw").rename(sftp_incoming)
    some_file = next(iter(sorted(sftp_incoming.rglob("*.x12"))))
    (sftp_incoming / "corrupt_delivery.x12").write_bytes(some_file.read_bytes()[:700])   # ends inside its envelope

    stand_ins = EmulatorStandIns(settings, working_directory)
    stand_ins.start()
    observer = Observer(settings, stand_ins)
    try:
        # Configuring the bucket notification makes S3 send s3:TestEvent messages, which start a task once.
        observer.wait_until("idle before any file: queue empty, no task", lambda s: s["queue_visible"] == 0
                            and s["service_desired_count"] == 0 and s["local_parser_tasks_running"] == 0, 300)
        report["mover_job"] = move_sftp_incoming_to_s3_input(settings, sftp_incoming, redeliver_every_nth_file=10)
        report["mover_job"]["corrupt_files"] = 1
        log.info("IT mover job: %s", report["mover_job"])
        observer.wait_until("alarm set the service's desired count to 1 and the task started",
                            lambda s: s["service_desired_count"] == 1 and s["local_parser_tasks_running"] == 1, 400)
        # Usually ~8 s after the alarm. Once, on an emulator already holding two other customers, App Auto Scaling
        # acted 130 s after the alarm changed state.
        observer.wait_until("the corrupt file reached the dead-letter queue", lambda s: s["dead_letter_queue"] >= 1,
                            300)
        observer.wait_until("queue empty and nothing in flight", lambda s: s["queue_visible"] == 0
                            and s["queue_in_flight"] == 0, 300)
        ephemeral_while_running = sorted(p.name for p in stand_ins.fargate.ephemeral_root.iterdir())
        observer.wait_until("idle alarm set the desired count to 0 and the task was torn down",
                            lambda s: s["service_desired_count"] == 0 and s["local_parser_tasks_running"] == 0, 300)
        report["ephemeral_storage_directories"] = {
            "while_task_ran": ephemeral_while_running,
            "after_task_stopped": sorted(p.name for p in stand_ins.fargate.ephemeral_root.iterdir())}
        dead_letter_bodies = []                           # the mock generator plants unreadable files of its own
        while messages := observer.sqs.receive_message(QueueUrl=observer.dead_letter_queue_url,
                                                       MaxNumberOfMessages=10).get("Messages", []):
            dead_letter_bodies += [json.loads(message["Body"]) for message in messages]
        report["dead_letter_queue_keys"] = sorted(unquote_plus(record["s3"]["object"]["key"])
                                                  for body in dead_letter_bodies for record in body["Records"])
        report["delivered_sha256_by_s3_key"] = {
            "input/" + path.relative_to(sftp_incoming).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(sftp_incoming.rglob("*.x12"))}
        report["silver_lake"] = lake_summary(settings)

        report["candidate_selection_runs"] = []
        for _ in range(2):                                # the second run must select nothing new
            task_arn = fire_schedule_now(settings)
            deadline = time.monotonic() + 180
            while task_arn not in stand_ins.fargate.exit_codes:
                if time.monotonic() > deadline:
                    raise TimeoutError("candidate selection task did not finish")
                time.sleep(1)
            task_log = next(stand_ins.fargate.log_directory.glob(f"*{task_arn.rsplit('/', 1)[-1]}.log")).read_text()
            if stand_ins.fargate.exit_codes[task_arn] != 0:
                raise RuntimeError(f"candidate selection task failed:\n{task_log}")
            summary = json.loads(task_log.rsplit("candidate selection done: ", 1)[1])
            report["candidate_selection_runs"].append(summary)
            log.info("candidate selection: %s", summary)
        moved = move_s3_output_to_sftp_outgoing(settings, sftp_outgoing)
        report["sftp_outgoing"] = [{"file": str(path), "lines": len(path.read_text().splitlines()),
                                    "first_lines": path.read_text().splitlines()[:3]} for path in moved]
        check_report(report)
    finally:
        stand_ins.stop()
        report["timeline"] = observer.timeline
        report["fargate_events"] = stand_ins.fargate.events
        (working_directory / "emulation_report.json").write_text(json.dumps(report, indent=2, default=str))
    return report


def main():
    arguments = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    arguments.add_argument("--working-directory", type=Path, required=True)
    arguments.add_argument("--customer-code", default="labcorp")
    arguments.add_argument("--mock-days", type=int, default=20)
    arguments.add_argument("--emulator-port", type=int, default=9297)
    options = arguments.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("pyiceberg").setLevel(logging.ERROR)
    options.working_directory.mkdir(parents=True, exist_ok=False)
    report = run(options.working_directory, options.customer_code, options.mock_days, options.emulator_port)
    print(json.dumps({k: v for k, v in report.items() if k not in ("timeline", "fargate_events")}, indent=2,
                     default=str))
    print(f"full report: {options.working_directory / 'emulation_report.json'}")


if __name__ == "__main__":
    main()
