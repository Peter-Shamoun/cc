"""The parser task with faults injected into its lake writes. Used only by the evaluation suite.

    CLAIM_CLARITY_EVALUATION_FAIL_LAKE_APPENDS=first-last   table commits number first..last (1-based) raise
    CLAIM_CLARITY_EVALUATION_CRASH_AT_LAKE_APPEND=n         the task process dies at table commit number n, once
                                                            per customer (a marker object in S3 records that it did)

A slot export makes one commit per silver table, so a range inside one export fails it part-way.
"""
import logging
import os
import threading

import boto3
from botocore.exceptions import ClientError

from .. import ingestion_task
from ..customer_settings import SILVER_PREFIX, CustomerPipelineSettings
from ..silver_lake import SilverLake

CRASH_MARKER_KEY = f"{SILVER_PREFIX}/_evaluation/crashed_once"


def main():
    settings = CustomerPipelineSettings.from_environment()
    first_failing, last_failing = (int(n) for n in os.environ.get("CLAIM_CLARITY_EVALUATION_FAIL_LAKE_APPENDS",
                                                                  "0-0").split("-"))
    crash_at = int(os.environ.get("CLAIM_CLARITY_EVALUATION_CRASH_AT_LAKE_APPEND", "0"))
    s3 = boto3.client("s3", region_name=settings.aws_region)
    commit_count = 0
    commit_count_lock = threading.Lock()

    def already_crashed() -> bool:
        try:
            s3.head_object(Bucket=settings.bucket_name, Key=CRASH_MARKER_KEY)
            return True
        except ClientError:
            return False

    def with_faults(commit):
        def faulty_commit(lake, name, *arguments, **keywords):
            nonlocal commit_count
            with commit_count_lock:
                commit_count += 1
                number = commit_count
            if first_failing <= number <= last_failing:
                raise OSError(f"injected fault: lake commit number {number} ({name}) refused")
            if number == crash_at and not already_crashed():
                s3.put_object(Bucket=settings.bucket_name, Key=CRASH_MARKER_KEY, Body=b"")
                logging.getLogger(__name__).error("injected fault: task dies at lake commit number %d", number)
                os._exit(137)
            return commit(lake, name, *arguments, **keywords)
        return faulty_commit

    SilverLake.append = with_faults(SilverLake.append)                # the two ways a table gets a commit
    SilverLake.add_data_file = with_faults(SilverLake.add_data_file)
    ingestion_task.main()


if __name__ == "__main__":
    main()
