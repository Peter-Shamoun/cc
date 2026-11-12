"""Onboard one customer: clone the whole pipeline from the template.

    S3 bucket (input/, silver/, candidate-config/, output/) -> S3 event -> SQS queue (+ dead-letter queue)
    CloudWatch alarms on the queue -> App Auto Scaling -> ECS service desired count 0 or 1 (Fargate parser task)
    EventBridge Scheduler -> one-shot Fargate task (candidate selection)
    Glue database with the Iceberg silver tables

Idempotent. Encryption, access keys and IAM permissions are out of scope of this design: the roles exist only
because the APIs require one.
"""
from __future__ import annotations

import json

import boto3
from botocore.exceptions import ClientError

from .customer_settings import CANDIDATE_PRESET_KEY, INPUT_PREFIX, CustomerPipelineSettings
from .ingestion_task import EPHEMERAL_STORAGE_DIRECTORY_VARIABLE, TASK_VCPU_COUNT_VARIABLE
from .silver_lake import SilverLake

VISIBLE_METRIC = "ApproximateNumberOfMessagesVisible"
IN_FLIGHT_METRIC = "ApproximateNumberOfMessagesNotVisible"
CONTAINER_NAME = "task"
CONTAINER_IMAGE = "claimclarity/pipeline:emulated"       # the local Fargate stand-in runs the command, not an image


def provision_customer(settings: CustomerPipelineSettings, candidate_selection_preset: dict | None = None):
    session = boto3.Session(region_name=settings.aws_region)
    _bucket(session, settings)
    queue_arn = _queues_and_s3_events(session, settings)
    SilverLake(settings).create_tables()
    network = _ecs(session, settings)
    _alarms_and_scaling(session, settings)
    _candidate_selection_schedule(session, settings, network)
    if candidate_selection_preset is not None:
        session.client("s3").put_object(Bucket=settings.bucket_name, Key=CANDIDATE_PRESET_KEY,
                                        Body=json.dumps(candidate_selection_preset, indent=2).encode())
    return {"bucket": settings.bucket_name, "queue_arn": queue_arn}


def _bucket(session, settings):
    s3 = session.client("s3")
    try:
        s3.create_bucket(Bucket=settings.bucket_name)
    except ClientError as error:
        if error.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
            raise
    # versioned: the candidate preset is version-controlled config, and a run records the version it used
    s3.put_bucket_versioning(Bucket=settings.bucket_name, VersioningConfiguration={"Status": "Enabled"})


def _queues_and_s3_events(session, settings) -> str:
    sqs = session.client("sqs")
    sqs.create_queue(QueueName=settings.dead_letter_queue_name)
    dead_letter_queue_arn = settings.arn("sqs", settings.dead_letter_queue_name)
    queue_arn = settings.arn("sqs", settings.ingestion_queue_name)
    attributes = {
        "VisibilityTimeout": str(settings.queue_visibility_timeout_seconds),
        "RedrivePolicy": json.dumps({"deadLetterTargetArn": dead_letter_queue_arn,
                                     "maxReceiveCount": settings.failed_receives_before_dead_letter_queue}),
        "Policy": json.dumps({"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Principal": {"Service": "s3.amazonaws.com"}, "Action": "sqs:SendMessage",
            "Resource": queue_arn,
            "Condition": {"ArnLike": {"aws:SourceArn": f"arn:aws:s3:::{settings.bucket_name}"}}}]}),
    }
    queue_url = sqs.create_queue(QueueName=settings.ingestion_queue_name, Attributes=attributes)["QueueUrl"]
    sqs.set_queue_attributes(QueueUrl=queue_url, Attributes=attributes)
    session.client("s3").put_bucket_notification_configuration(
        Bucket=settings.bucket_name, NotificationConfiguration={"QueueConfigurations": [{
            "Id": "raw-edi-file-arrived", "QueueArn": queue_arn, "Events": ["s3:ObjectCreated:*"],
            "Filter": {"Key": {"FilterRules": [{"Name": "prefix", "Value": INPUT_PREFIX}]}}}]})
    return queue_arn


def _role(session, role_name: str, service_principal: str) -> str:
    iam = session.client("iam")
    try:
        return iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=json.dumps({
            "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": service_principal},
                                                    "Action": "sts:AssumeRole"}]}))["Role"]["Arn"]
    except ClientError as error:
        if error.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        return iam.get_role(RoleName=role_name)["Role"]["Arn"]


def _ecs(session, settings) -> dict:
    ecs = session.client("ecs")
    ecs.create_cluster(clusterName=settings.ecs_cluster_name)
    task_role_arn = _role(session, f"{settings.resource_name_prefix}-task", "ecs-tasks.amazonaws.com")

    def register(family: str, module: str, vcpu_count: int, memory_mib: int):
        environment = settings.to_environment() | {
            TASK_VCPU_COUNT_VARIABLE: str(vcpu_count),
            # Fargate mounts the task's ephemeral storage here; nothing else in the task writes to disk
            EPHEMERAL_STORAGE_DIRECTORY_VARIABLE: "/ephemeral"}
        ecs.register_task_definition(
            family=family, requiresCompatibilities=["FARGATE"], networkMode="awsvpc",
            cpu=str(vcpu_count * 1024), memory=str(memory_mib),
            ephemeralStorage={"sizeInGiB": settings.fargate_ephemeral_storage_gib},
            executionRoleArn=task_role_arn, taskRoleArn=task_role_arn,
            containerDefinitions=[{
                "name": CONTAINER_NAME, "image": CONTAINER_IMAGE, "essential": True,
                "command": ["python", "-m", f"claim_clarity_pipeline.{module}"],
                "environment": [{"name": k, "value": v} for k, v in sorted(environment.items())]}])

    register(settings.parser_task_family, "ingestion_task", settings.parser_task_vcpu_count,
             settings.parser_task_memory_mib)
    register(settings.candidate_task_family, "candidate_selection_task", settings.candidate_task_vcpu_count,
             settings.candidate_task_memory_mib)
    subnet_ids = [s["SubnetId"] for s in session.client("ec2").describe_subnets()["Subnets"]][:2]
    network = {"subnets": subnet_ids, "assignPublicIp": "DISABLED"}
    services = ecs.describe_services(cluster=settings.ecs_cluster_name, services=[settings.parser_service_name])
    if not [s for s in services["services"] if s["status"] == "ACTIVE"]:
        ecs.create_service(cluster=settings.ecs_cluster_name, serviceName=settings.parser_service_name,
                           taskDefinition=settings.parser_task_family, desiredCount=0, launchType="FARGATE",
                           networkConfiguration={"awsvpcConfiguration": network})
    return network


def _alarms_and_scaling(session, settings):
    scaling, cloudwatch = session.client("application-autoscaling"), session.client("cloudwatch")
    target = dict(ServiceNamespace="ecs", ScalableDimension="ecs:service:DesiredCount",
                  ResourceId=f"service/{settings.ecs_cluster_name}/{settings.parser_service_name}")
    scaling.register_scalable_target(MinCapacity=0, MaxCapacity=1, **target)

    def set_desired_count_policy(policy_name: str, desired_count: int, step: dict) -> str:
        return scaling.put_scaling_policy(
            PolicyName=policy_name, PolicyType="StepScaling", StepScalingPolicyConfiguration={
                "AdjustmentType": "ExactCapacity", "Cooldown": 0,
                "StepAdjustments": [step | {"ScalingAdjustment": desired_count}]}, **target)["PolicyARN"]

    start_task = set_desired_count_policy(f"{settings.resource_name_prefix}-start-parser-task", 1,
                                          {"MetricIntervalLowerBound": 0})
    stop_task = set_desired_count_policy(f"{settings.resource_name_prefix}-stop-parser-task", 0,
                                         {"MetricIntervalUpperBound": 0})

    def queue_metric(metric_id: str, metric_name: str) -> dict:
        return {"Id": metric_id, "ReturnData": False, "MetricStat": {
            "Period": settings.alarm_period_seconds, "Stat": "Maximum",
            "Metric": {"Namespace": settings.queue_metric_namespace, "MetricName": metric_name,
                       "Dimensions": [{"Name": "QueueName", "Value": settings.ingestion_queue_name}]}}}

    # queue > 0 -> 1 task
    cloudwatch.put_metric_alarm(
        AlarmName=settings.queue_has_files_alarm_name, AlarmActions=[start_task],
        Namespace=settings.queue_metric_namespace, MetricName=VISIBLE_METRIC, Statistic="Maximum",
        Dimensions=[{"Name": "QueueName", "Value": settings.ingestion_queue_name}],
        Period=settings.alarm_period_seconds, EvaluationPeriods=1, Threshold=0,
        ComparisonOperator="GreaterThanThreshold", TreatMissingData="notBreaching")
    # queue = 0 and in flight = 0 for X periods -> 0 tasks. Missing data must not stop a working task.
    cloudwatch.put_metric_alarm(
        AlarmName=settings.queue_idle_alarm_name, AlarmActions=[stop_task],
        Metrics=[{"Id": "waiting_or_in_flight", "Expression": "visible + in_flight", "ReturnData": True},
                 queue_metric("visible", VISIBLE_METRIC), queue_metric("in_flight", IN_FLIGHT_METRIC)],
        EvaluationPeriods=settings.idle_alarm_periods_before_scale_to_zero,
        DatapointsToAlarm=settings.idle_alarm_periods_before_scale_to_zero, Threshold=1,
        ComparisonOperator="LessThanThreshold", TreatMissingData="notBreaching")


def _candidate_selection_schedule(session, settings, network: dict):
    scheduler = session.client("scheduler")
    schedule = dict(
        Name=settings.candidate_schedule_name, FlexibleTimeWindow={"Mode": "OFF"},
        ScheduleExpression=settings.candidate_selection_schedule_expression,
        Target={"Arn": settings.arn("ecs", f"cluster/{settings.ecs_cluster_name}"),
                "RoleArn": _role(session, f"{settings.resource_name_prefix}-scheduler", "scheduler.amazonaws.com"),
                "EcsParameters": {
                    "TaskDefinitionArn": settings.arn("ecs", f"task-definition/{settings.candidate_task_family}"),
                    "LaunchType": "FARGATE", "TaskCount": 1,
                    "NetworkConfiguration": {"awsvpcConfiguration": {
                        "Subnets": network["subnets"], "AssignPublicIp": network["assignPublicIp"]}}}})
    try:
        scheduler.create_schedule(**schedule)
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConflictException":
            raise
        scheduler.update_schedule(**schedule)
