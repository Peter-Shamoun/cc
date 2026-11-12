"""One customer's pipeline: every resource name and every tunable.

The whole pipeline is cloned per customer from this one template, so every name is derived from customer_code.
A task reads its settings from its task definition's container environment (to_environment / from_environment).
"""
from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass

ENVIRONMENT_PREFIX = "CLAIM_CLARITY_"
INPUT_PREFIX = "input/"                              # raw .edi files, as the IT mover job delivers them
SILVER_PREFIX = "silver"                             # the Iceberg warehouse
CANDIDATE_PRESET_KEY = "candidate-config/candidate_selection_preset.json"
CANDIDATE_OUTPUT_PREFIX = "output/candidates/"


@dataclass(frozen=True)
class CustomerPipelineSettings:
    customer_code: str
    aws_region: str = "us-east-1"
    aws_account_id: str = "000000000000"
    # --- SQS -----------------------------------------------------------------------------------------------------
    queue_visibility_timeout_seconds: int = 300      # a received file stays hidden this long unless extended
    failed_receives_before_dead_letter_queue: int = 3
    # --- CloudWatch alarms -> App Auto Scaling -------------------------------------------------------------------
    # SQS publishes its depth under AWS/SQS. The emulator does not, so there a stand-in publishes the same metric
    # names under another namespace (emulator_stand_ins.publish_queue_depth_metrics).
    queue_metric_namespace: str = "AWS/SQS"
    alarm_period_seconds: int = 60
    idle_alarm_periods_before_scale_to_zero: int = 15         # X minutes of an empty queue with nothing in flight
    # --- the ECS parser task -------------------------------------------------------------------------------------
    parser_task_vcpu_count: int = 4                  # parser processes = vCPUs - 1
    parser_task_memory_mib: int = 16384
    fargate_ephemeral_storage_gib: int = 200
    downloader_thread_count: int = 4
    in_memory_queue_max_files: int = 64              # bound of the URI, raw file and parsed file queues
    ephemeral_disk_cap_bytes: int = 150 * 1024 ** 3  # downloaders wait while raw + parsed + slots use more
    slot_size_limit_bytes: int = 512 * 1024 ** 2     # a slot is exported when its staged rows reach this size
    slot_idle_export_seconds: float = 5.0            # ... or when no file has arrived for this long
    # --- candidate selection -------------------------------------------------------------------------------------
    candidate_selection_schedule_expression: str = "cron(0 9 * * ? *)"    # nightly, 09:00 UTC
    candidate_task_vcpu_count: int = 2
    candidate_task_memory_mib: int = 8192

    # --- names ---------------------------------------------------------------------------------------------------
    @property
    def resource_name_prefix(self) -> str:
        return f"claimclarity-{self.customer_code}"

    @property
    def bucket_name(self) -> str:
        return f"claimclarity-prod-{self.customer_code}"

    @property
    def ingestion_queue_name(self) -> str:
        return f"{self.resource_name_prefix}-ingestion"

    @property
    def dead_letter_queue_name(self) -> str:
        return f"{self.resource_name_prefix}-ingestion-dlq"

    @property
    def ecs_cluster_name(self) -> str:
        return self.resource_name_prefix

    @property
    def parser_service_name(self) -> str:
        return f"{self.resource_name_prefix}-parser"

    @property
    def parser_task_family(self) -> str:
        return f"{self.resource_name_prefix}-parser"

    @property
    def candidate_task_family(self) -> str:
        return f"{self.resource_name_prefix}-candidate-selection"

    @property
    def candidate_schedule_name(self) -> str:
        return f"{self.resource_name_prefix}-candidate-selection"

    @property
    def queue_has_files_alarm_name(self) -> str:
        return f"{self.resource_name_prefix}-queue-has-files"

    @property
    def queue_idle_alarm_name(self) -> str:
        return f"{self.resource_name_prefix}-queue-idle"

    @property
    def glue_database_name(self) -> str:
        return f"claimclarity_{self.customer_code}_silver"

    @property
    def silver_warehouse_uri(self) -> str:
        return f"s3://{self.bucket_name}/{SILVER_PREFIX}"

    def arn(self, service: str, resource: str) -> str:
        return f"arn:aws:{service}:{self.aws_region}:{self.aws_account_id}:{resource}"

    # --- task environment ----------------------------------------------------------------------------------------
    def to_environment(self) -> dict[str, str]:
        return {ENVIRONMENT_PREFIX + f.name.upper(): str(getattr(self, f.name)) for f in dataclasses.fields(self)}

    @classmethod
    def from_environment(cls) -> CustomerPipelineSettings:
        values = {}
        for f in dataclasses.fields(cls):
            raw = os.environ.get(ENVIRONMENT_PREFIX + f.name.upper())
            if raw is not None:
                values[f.name] = {"int": int, "float": float, "str": str}[f.type](raw)
        return cls(**values)                         # a missing CLAIM_CLARITY_CUSTOMER_CODE is a TypeError
