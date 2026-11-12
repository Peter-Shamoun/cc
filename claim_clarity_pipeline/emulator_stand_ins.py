"""Local stand-ins for what sits outside the pipeline or what the AWS Emulator does not do.

Outside the pipeline (IT's mover jobs, XiFin's on-prem SFTP server):
    move_sftp_incoming_to_s3_input      SFTP directory -> the customer's S3 input prefix
    move_s3_output_to_sftp_outgoing     the customer's S3 candidate CSVs -> SFTP outgoing directory

Emulator gaps (observed on AWS Emulator 1.0.322; each is done by AWS itself in a real account):
    publish_queue_depth_metrics         SQS reports its depth to CloudWatch; the emulator publishes no SQS metrics
    LocalFargate                        Fargate runs the task's container on fresh ephemeral storage; the emulator
                                        records ECS tasks but cannot run a container here
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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import boto3

from .customer_settings import CANDIDATE_OUTPUT_PREFIX, INPUT_PREFIX, CustomerPipelineSettings
from .ingestion_task import EPHEMERAL_STORAGE_DIRECTORY_VARIABLE
from .provision_customer import IN_FLIGHT_METRIC, VISIBLE_METRIC

log = logging.getLogger("claim_clarity_pipeline.emulator_stand_ins")
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


# --- IT's mover jobs -------------------------------------------------------------------------------------------
def move_sftp_incoming_to_s3_input(settings: CustomerPipelineSettings, sftp_incoming_directory: Path,
                                   redeliver_every_nth_file: int = 0) -> dict:
    """Upload every file under the SFTP directory to the input prefix. With redeliver_every_nth_file = n, every
    nth file is uploaded a second time under another name: the IT job is known to move duplicates."""
    s3 = boto3.client("s3", region_name=settings.aws_region)
    moved = redelivered = 0
    for number, path in enumerate(sorted(p for p in sftp_incoming_directory.rglob("*") if p.is_file()), start=1):
        key = INPUT_PREFIX + path.relative_to(sftp_incoming_directory).as_posix()
        s3.upload_file(str(path), settings.bucket_name, key)
        moved += 1
        if redeliver_every_nth_file and number % redeliver_every_nth_file == 0:
            s3.upload_file(str(path), settings.bucket_name, f"{key}.redelivered")
            redelivered += 1
    return {"files_moved": moved, "duplicate_deliveries": redelivered}


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
@dataclass
class _LocalTask:
    task_arn: str
    is_service_task: bool
    process: subprocess.Popen
    ephemeral_storage: Path
    log_path: Path


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
            is_service_task = task.get("group", "").startswith("service:")
            # The emulator stops a standalone task at once (it cannot pull an image), so a new standalone task is
            # run whatever its status; a service task is run while ECS wants it running.
            if not is_service_task or task["desiredStatus"] == "RUNNING":
                self._start(task, is_service_task)
        for task_arn, local in list(self.running.items()):
            exit_code = local.process.poll()
            stopped_by_ecs = local.is_service_task and (
                task_arn not in tasks or tasks[task_arn]["desiredStatus"] == "STOPPED")
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
                     stopped_by="ECS (service scaled in)" if stopped_by_ecs else "task exit")
        if local.is_service_task and not stopped_by_ecs:              # it crashed: tell ECS, which replaces it
            self.ecs.stop_task(cluster=self.settings.ecs_cluster_name, task=local.task_arn,
                               reason=f"container exited with code {exit_code}")

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
