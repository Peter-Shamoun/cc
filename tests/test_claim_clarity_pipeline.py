"""The Claim Clarity pipeline against a running AWS Emulator (skipped when none is running).

The emulation runs in its own process: it points boto3 and Arrow at the emulator through environment variables,
which must not leak into the other tests. run_emulation.check_report holds the assertions: every delivered file
is in the lake exactly once, duplicate deliveries are skipped, the corrupt file ends in the dead-letter queue, the
task and its ephemeral storage are gone after the idle alarm, and a second candidate run selects nothing new.
"""
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from claim_clarity_pipeline.customer_settings import CustomerPipelineSettings

EMULATOR_PORT = 9297
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def test_settings_round_trip_through_a_task_environment(monkeypatch):
    settings = CustomerPipelineSettings("acme_labs", slot_size_limit_bytes=123, slot_idle_export_seconds=1.5)
    for name, value in settings.to_environment().items():
        monkeypatch.setenv(name, value)
    assert CustomerPipelineSettings.from_environment() == settings
    assert settings.bucket_name == "claimclarity-prod-acme_labs"


def test_whole_design_end_to_end_on_the_emulator(tmp_path):
    if not (Path.home() / ".aws" / "emulator" / str(EMULATOR_PORT) / "emulator_credentials").exists():
        pytest.skip(f"no AWS Emulator running on port {EMULATOR_PORT} (start one: aws-emulator --telemetry off)")
    working_directory = tmp_path / "emulation"
    customer_code = f"test{uuid.uuid4().hex[:8]}"         # the emulator keeps earlier runs' customers
    completed = subprocess.run(
        [sys.executable, "-m", "claim_clarity_pipeline.run_emulation", "--working-directory", str(working_directory),
         "--customer-code", customer_code, "--mock-days", "20", "--emulator-port", str(EMULATOR_PORT)],
        cwd=REPOSITORY_ROOT, capture_output=True, text=True, timeout=900)
    assert completed.returncode == 0, completed.stderr[-4000:]
    report = json.loads((working_directory / "emulation_report.json").read_text())
    assert report["silver_lake"]["files_in_lake"] > 100
    assert [event["event"] for event in report["fargate_events"]].count("task started") >= 3   # parser x2, candidates
