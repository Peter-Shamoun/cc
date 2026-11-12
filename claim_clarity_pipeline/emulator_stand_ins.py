"""Local stand-ins for what sits outside the pipeline or what the AWS Emulator does not do.

Outside the pipeline (IT's mover jobs, XiFin's on-prem SFTP server):
    move_sftp_incoming_to_s3_input      SFTP directory -> the customer's S3 input prefix
    move_s3_output_to_sftp_outgoing     the customer's S3 candidate CSVs -> SFTP outgoing directory

Emulator gaps (observed on AWS Emulator 1.0.322; each is done by AWS itself in a real account):
    publish_queue_depth_metrics         SQS reports its depth to CloudWatch; the emulator publishes no SQS metrics
    LocalFargate                        Fargate runs the task's container on fresh ephemeral storage; the emulator
                                        records ECS tasks but cannot run a container here, and does not replace a
                                        service task whose container exited
    fire_schedule_now                   EventBridge Scheduler launches the ECS target; the emulator stores the
                                        schedule but never launches it

Everything else (S3 events to SQS, redrive to the dead-letter queue, alarm evaluation, App Auto Scaling setting the
ECS service's desired count, ECS starting and stopping service tasks, Glue, S3) is the emulator's own behaviour.
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import boto3

from .customer_settings import CANDIDATE_OUTPUT_PREFIX, INPUT_PREFIX, CustomerPipelineSettings
from .ingestion_task import EPHEMERAL_STORAGE_DIRECTORY_VARIABLE, directory_size_bytes
from .provision_customer import IN_FLIGHT_METRIC, VISIBLE_METRIC

log = logging.getLogger("claim_clarity_pipeline.emulator_stand_ins")
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


# --- IT's mover jobs -------------------------------------------------------------------------------------------
def move_sftp_incoming_to_s3_input(settings: CustomerPipelineSettings, sftp_incoming_directory: Path,
                                   redeliver_every_nth_file: int = 0, upload_thread_count: int = 8) -> dict:
    """Upload every file under the SFTP directory to the input prefix. With redeliver_every_nth_file = n, every
    nth file is uploaded a second time under another name: the IT job is known to move duplicates."""
    s3 = boto3.client("s3", region_name=settings.aws_region)
    uploads = []
    for number, path in enumerate(sorted(p for p in sftp_incoming_directory.rglob("*") if p.is_file()), start=1):
        key = INPUT_PREFIX + path.relative_to(sftp_incoming_directory).as_posix()
        uploads.append((path, key))
        if redeliver_every_nth_file and number % redeliver_every_nth_file == 0:
            uploads.append((path, f"{key}.redelivered"))
    with ThreadPoolExecutor(upload_thread_count) as pool:
        list(pool.map(lambda upload: s3.upload_file(str(upload[0]), settings.bucket_name, upload[1]), uploads))
    duplicate_deliveries = sum(key.endswith(".redelivered") for _, key in uploads)
    return {"files_moved": len(uploads) - duplicate_deliveries, "duplicate_deliveries": duplicate_deliveries,
            "bytes_moved": sum(path.stat().st_size for path, _ in uploads)}


def move_s3_output_to_sftp_outgoing(settings: CustomerPipelineSettings, sftp_outgoing_directory: Path) -> list[Path]:
    s3 = boto3.client("s3", region_name=settings.aws_region)
    sftp_outgoing_directory.mkdir(parents=True, exist_ok=True)
    moved = []
    listed = s3.list_objects_v2(Bucket=settings.bucket_name, Prefix=CANDIDATE_OUTPUT_PREFIX)
    for candidate_csv in listed.get("Contents", []):
        destination = sftp_outgoing_directory / candidate_csv["Key"].rsplit("/", 1)[-1]
        if not destination.exists():
            s3.download_file(settings.bucket_name, candidate_csv["Key"], str(destination))
            moved.append(destination)
    return moved


# --- SQS -> CloudWatch -----------------------------------------------------------------------------------------
def publish_queue_depth_metrics(settings: CustomerPipelineSettings, sqs, cloudwatch) -> dict[str, int]:
    queue_url = sqs.get_queue_url(QueueName=settings.ingestion_queue_name)["QueueUrl"]
    attributes = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=[
        "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
    depth = {VISIBLE_METRIC: int(attributes["ApproximateNumberOfMessages"]),
             IN_FLIGHT_METRIC: int(attributes["ApproximateNumberOfMessagesNotVisible"])}
    cloudwatch.put_metric_data(Namespace=settings.queue_metric_namespace, MetricData=[
        {"MetricName": name, "Value": value, "Unit": "Count",
         # CloudWatch keeps a metric per minute unless it is published as high resolution; alarm periods under a
         # minute (the shortened emulation timings) need that. AWS/SQS itself reports once a minute.
         "StorageResolution": 1 if settings.alarm_period_seconds < 60 else 60,
         "Dimensions": [{"Name": "QueueName", "Value": settings.ingestion_queue_name}]}
        for name, value in depth.items()])
    return depth


# --- EventBridge Scheduler -------------------------------------------------------------------------------------
def fire_schedule_now(settings: CustomerPipelineSettings) -> str:
    """Do what the schedule does when its time comes: ECS RunTask with the schedule's own target. Returns the
    task ARN."""
    session = boto3.Session(region_name=settings.aws_region)
    target = session.client("scheduler").get_schedule(Name=settings.candidate_schedule_name)["Target"]
    parameters = target["EcsParameters"]
    network = parameters["NetworkConfiguration"]["awsvpcConfiguration"]
    task = session.client("ecs").run_task(
        cluster=target["Arn"], taskDefinition=parameters["TaskDefinitionArn"], launchType=parameters["LaunchType"],
        count=parameters.get("TaskCount", 1), startedBy=f"scheduler/{settings.candidate_schedule_name}"[:36],
        networkConfiguration={"awsvpcConfiguration": {"subnets": network["Subnets"],
                                                      "assignPublicIp": network["AssignPublicIp"]}})["tasks"][0]
    return task["taskArn"]


# --- Fargate ---------------------------------------------------------------------------------------------------
def process_tree_memory_bytes(process_id: int) -> tuple[int, int, int]:
    """(resident, anonymous, anonymous of the process itself) for a process and its descendants (the parser task's
    parser processes), from /proc. Resident includes the pages of memory-mapped files, which the kernel can drop;
    anonymous is the heap."""
    children_by_parent: dict[int, list[int]] = {}
    memory: dict[int, tuple[int, int]] = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                parent = int((entry / "stat").read_text().rsplit(")", 1)[1].split()[1])
                status = dict(line.split(":", 1) for line in (entry / "status").read_text().splitlines() if ":" in line)
            except (FileNotFoundError, ProcessLookupError):
                continue
            children_by_parent.setdefault(parent, []).append(int(entry.name))
            memory[int(entry.name)] = (int(status.get("VmRSS", "0 kB").split()[0]) * 1024,
                                       int(status.get("RssAnon", "0 kB").split()[0]) * 1024)
    resident = anonymous = 0
    stack = [process_id]
    while stack:
        current = stack.pop()
        resident += memory.get(current, (0, 0))[0]
        anonymous += memory.get(current, (0, 0))[1]
        stack += children_by_parent.get(current, [])
    return resident, anonymous, memory.get(process_id, (0, 0))[1]


@dataclass
class _LocalTask:
    task_arn: str
    is_service_task: bool
    process: subprocess.Popen
    ephemeral_storage: Path
    log_path: Path
    peak_ephemeral_storage_bytes: int = 0
    peak_resident_memory_bytes: int = 0
    peak_anonymous_memory_bytes: int = 0
    peak_main_process_anonymous_memory_bytes: int = 0      # without the parser processes


class LocalFargate:
    """Runs each ECS task of the cluster as a local process with its own ephemeral storage directory, and tears
    both down when ECS stops the task (or a one-shot task exits). events records what happened, for the report."""

    def __init__(self, settings: CustomerPipelineSettings, working_directory: Path):
        self.settings = settings
        self.ecs = boto3.client("ecs", region_name=settings.aws_region)
        self.ephemeral_root = working_directory / "fargate-ephemeral-storage"
        self.log_directory = working_directory / "task-logs"          # stands in for CloudWatch Logs
        self.log_directory.mkdir(parents=True, exist_ok=True)
        self.running: dict[str, _LocalTask] = {}
        self.exit_codes: dict[str, int] = {}
        self.events: list[dict] = []
        self.handled_task_arns = {task["taskArn"] for task in self._describe_tasks()}   # from before this run

    def _describe_tasks(self) -> list[dict]:
        task_arns = []
        for desired_status in ("RUNNING", "STOPPED"):
            task_arns += self.ecs.list_tasks(cluster=self.settings.ecs_cluster_name,
                                             desiredStatus=desired_status).get("taskArns", [])
        if not task_arns:
            return []
        return self.ecs.describe_tasks(cluster=self.settings.ecs_cluster_name, tasks=task_arns)["tasks"]

    def _record(self, event: str, task: _LocalTask, **details):
        self.events.append({"at": datetime.now(UTC).isoformat(timespec="seconds"), "event": event,
                            "task": task.task_arn.rsplit("/", 1)[-1], **details})
        log.info("%s %s %s", event, task.task_arn.rsplit("/", 1)[-1], details or "")

    def poll(self):
        tasks = {task["taskArn"]: task for task in self._describe_tasks()}
        for task_arn, task in tasks.items():
            if task_arn in self.handled_task_arns:
                continue
            self.handled_task_arns.add(task_arn)
            if task["desiredStatus"] == "RUNNING":
                self._start(task, task.get("group", "").startswith("service:"))
        for task_arn, local in list(self.running.items()):
            local.peak_ephemeral_storage_bytes = max(local.peak_ephemeral_storage_bytes,
                                                     directory_size_bytes(local.ephemeral_storage))
            resident, anonymous, main_anonymous = process_tree_memory_bytes(local.process.pid)
            local.peak_resident_memory_bytes = max(local.peak_resident_memory_bytes, resident)
            local.peak_anonymous_memory_bytes = max(local.peak_anonymous_memory_bytes, anonymous)
            local.peak_main_process_anonymous_memory_bytes = max(local.peak_main_process_anonymous_memory_bytes,
                                                                 main_anonymous)
            exit_code = local.process.poll()
            stopped_by_ecs = task_arn not in tasks or tasks[task_arn]["desiredStatus"] == "STOPPED"
            if exit_code is None and stopped_by_ecs:
                local.process.send_signal(signal.SIGTERM)             # ECS sends SIGTERM, then SIGKILL
                try:
                    exit_code = local.process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    local.process.kill()
                    exit_code = local.process.wait()
            if exit_code is not None:
                self._tear_down(local, exit_code, stopped_by_ecs)

    def _start(self, task: dict, is_service_task: bool):
        definition = self.ecs.describe_task_definition(taskDefinition=task["taskDefinitionArn"])["taskDefinition"]
        container = definition["containerDefinitions"][0]
        task_id = task["taskArn"].rsplit("/", 1)[-1]
        ephemeral_storage = self.ephemeral_root / task_id
        ephemeral_storage.mkdir(parents=True)
        environment = os.environ | {e["name"]: e["value"] for e in container.get("environment", [])}
        environment[EPHEMERAL_STORAGE_DIRECTORY_VARIABLE] = str(ephemeral_storage)
        command = [sys.executable if part == "python" else part for part in container["command"]]
        log_path = self.log_directory / f"{definition['family']}-{task_id}.log"
        process = subprocess.Popen(command, cwd=REPOSITORY_ROOT, env=environment, stdout=open(log_path, "wb"),
                                   stderr=subprocess.STDOUT)
        local = _LocalTask(task["taskArn"], is_service_task, process, ephemeral_storage, log_path)
        self.running[task["taskArn"]] = local
        self._record("task started", local, family=definition["family"],
                     ephemeral_storage_gib=definition.get("ephemeralStorage", {}).get("sizeInGiB"))

    def _tear_down(self, local: _LocalTask, exit_code: int, stopped_by_ecs: bool):
        del self.running[local.task_arn]
        self.exit_codes[local.task_arn] = exit_code
        shutil.rmtree(local.ephemeral_storage)
        self._record("task stopped, ephemeral storage torn down", local, exit_code=exit_code,
                     stopped_by="ECS" if stopped_by_ecs else "container exit",
                     peak_ephemeral_storage_bytes=local.peak_ephemeral_storage_bytes,
                     peak_resident_memory_bytes=local.peak_resident_memory_bytes,
                     peak_anonymous_memory_bytes=local.peak_anonymous_memory_bytes,
                     peak_main_process_anonymous_memory_bytes=local.peak_main_process_anonymous_memory_bytes)
        if not stopped_by_ecs:            # the container exited: ECS stops the task, and replaces a service's task
            self.ecs.stop_task(cluster=self.settings.ecs_cluster_name, task=local.task_arn,
                               reason=f"Essential container in task exited (code {exit_code})")
            if local.is_service_task:     # the emulator's service scheduler does not start the replacement itself
                self.ecs.update_service(cluster=self.settings.ecs_cluster_name,
                                        service=self.settings.parser_service_name, forceNewDeployment=True)

    def stop_all(self):
        for local in list(self.running.values()):
            local.process.kill()
            self._tear_down(local, local.process.wait(), stopped_by_ecs=True)


class EmulatorStandIns(threading.Thread):
    """Publishes the queue depth and runs ECS tasks locally, every poll_seconds, until stop()."""

    def __init__(self, settings: CustomerPipelineSettings, working_directory: Path, poll_seconds: float = 2.0):
        super().__init__(name="emulator-stand-ins", daemon=True)
        self.settings, self.poll_seconds = settings, poll_seconds
        self.fargate = LocalFargate(settings, working_directory)
        self.sqs = boto3.client("sqs", region_name=settings.aws_region)
        self.cloudwatch = boto3.client("cloudwatch", region_name=settings.aws_region)
        self.queue_depth: dict[str, int] = {}
        self._stop_requested = threading.Event()

    def run(self):
        while not self._stop_requested.is_set():
            try:
                self.queue_depth = publish_queue_depth_metrics(self.settings, self.sqs, self.cloudwatch)
                self.fargate.poll()
            except Exception:                                         # noqa: BLE001 - keep standing in
                log.exception("stand-in poll failed")
            self._stop_requested.wait(self.poll_seconds)

    def stop(self):
        self._stop_requested.set()
        self.join()
        self.fargate.stop_all()
