"""Point this process, and every process it starts, at a running AWS Emulator.

The pipeline code is plain boto3 / pyiceberg and never mentions the emulator. This module only sets the standard
environment variables those libraries read, from the files the emulator writes at startup.

Call it before importing pyarrow: Arrow reads the trusted certificates (SSL_CERT_FILE) when it loads. Processes
started afterwards inherit the environment and need nothing.
"""
from __future__ import annotations

import configparser
import os
import tempfile
from pathlib import Path


def configure_process_for_emulator(emulator_port: int = 9297) -> str:
    """Returns the endpoint URL. Raises FileNotFoundError if no emulator is running on that port."""
    emulator_files = Path.home() / ".aws" / "emulator" / str(emulator_port)
    credentials = configparser.ConfigParser()
    if not credentials.read(emulator_files / "emulator_credentials"):
        raise FileNotFoundError(f"no AWS Emulator on port {emulator_port}: {emulator_files} has no credentials file")
    endpoint_url = f"https://127.0.0.1:{emulator_port}"
    # The emulator's certificate is self-signed. Arrow's S3 client reads SSL_CERT_FILE and boto3 reads
    # AWS_CA_BUNDLE; both get the certificates already trusted plus the emulator's.
    trusted_so_far = os.environ.get("CLAIM_CLARITY_ORIGINAL_CA_BUNDLE", os.environ.get("SSL_CERT_FILE", ""))
    bundle = (Path(trusted_so_far).read_text() if trusted_so_far else "") + (emulator_files / "ca.pem").read_text()
    bundle_path = Path(tempfile.gettempdir()) / f"claim-clarity-emulator-{emulator_port}-ca-bundle.pem"
    bundle_path.write_text(bundle)
    os.environ.update({
        "CLAIM_CLARITY_ORIGINAL_CA_BUNDLE": trusted_so_far,
        "AWS_ENDPOINT_URL": endpoint_url,
        "AWS_ACCESS_KEY_ID": credentials["default"]["aws_access_key_id"],
        "AWS_SECRET_ACCESS_KEY": credentials["default"]["aws_secret_access_key"],
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_CA_BUNDLE": str(bundle_path),
        "SSL_CERT_FILE": str(bundle_path),
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    })
    for name in ("AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
        os.environ.pop(name, None)
    return endpoint_url
