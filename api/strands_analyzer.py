"""How the chat API runs the analyzer: bundle B1 on a Strands agent (``analyzer.strands_agent``), in this process.

There is one path. What varies is only the model behind the agent (``CC_API_ANALYZER_MODEL``):

- ``scripted`` (default): ``api.demo_model``, a scripted Strands model playing the demo scenarios offline. No network,
  no cost. With demo auth (``python -m api``) this is the offline demo and what the tests and the web e2e run.
- ``bedrock``: Claude on Amazon Bedrock through Strands' ``BedrockModel``, with a boto3 session for the AWS profile
  ``CC_AWS_PROFILE`` (default ``apiservice``) and region ``CC_AWS_REGION`` (default: the profile's region). Model id
  ``CC_STRANDS_MODEL_ID`` (default ``us.anthropic.claude-haiku-5-5``, a cross-region inference profile id: the
  ``us.`` prefix routes within US regions). Effort low: no extended thinking. Prompt caching is on (see the agent
  module). Bedrock is paid, so the paid switch ``CC_API_ALLOW_PAID_API`` must hold its exact phrase, and the run
  needs the bench snapshot ``CC_API_ANALYZER_SNAPSHOT`` (B1's TRAIN example library) and a signed-in customer's data
  (``SimAuth``: ``python -m api.sim_server``); demo auth's synthetic data does not fit B1's briefing.

Every problem with that configuration fails at startup (``AnalyzerSettings.check``), not on the first question.
The data scope is always the request's opener (``CancellableOpener`` + ``ReasonedEscalationOpener`` around the auth
backend's opener): the customer comes only from the verified token.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Mapping

from analyzer.answer import AnalyzerAnswer
from analyzer.strands_agent import answer_question

ANALYZER_MODEL_KINDS = ("scripted", "bedrock")
DEFAULT_STRANDS_MODEL_ID = "us.anthropic.claude-haiku-5-5"
DEFAULT_AWS_PROFILE = "apiservice"
PAID_SWITCH_ENV = "CC_API_ALLOW_PAID_API"
#: A deliberate phrase, not "1"/"true", so a copied .env or a typo can't turn it on.
PAID_SWITCH_VALUE = "i-understand-this-calls-a-paid-model-api"
#: B1 answers in a paragraph and short tool calls; effort low, so no thinking budget
BEDROCK_MAX_TOKENS = 4096
SCRIPTED_MODEL_LABEL = "scripted"
DEFAULT_TIME_ZONE = "America/Los_Angeles"


class AnalyzerConfigurationError(RuntimeError):
    """The analyzer cannot run as configured. Raised at startup."""


def paid_api_enabled(env: Mapping[str, str] | None = None) -> bool:
    """The paid-API switch. Off unless the env var holds the exact phrase."""
    return (os.environ if env is None else env).get(PAID_SWITCH_ENV) == PAID_SWITCH_VALUE


@dataclass
class AnalyzerSettings:
    model_kind: str = field(default_factory=lambda: os.environ.get("CC_API_ANALYZER_MODEL", "scripted"))
    strands_model_id: str = field(default_factory=lambda: os.environ.get("CC_STRANDS_MODEL_ID")
                                  or DEFAULT_STRANDS_MODEL_ID)
    aws_profile: str = field(default_factory=lambda: os.environ.get("CC_AWS_PROFILE") or DEFAULT_AWS_PROFILE)
    aws_region: str | None = field(default_factory=lambda: os.environ.get("CC_AWS_REGION") or None)
    #: bench snapshot directory: the TRAIN example library of the briefing (never the data a chat run queries)
    snapshot_path: str | None = field(default_factory=lambda: os.environ.get("CC_API_ANALYZER_SNAPSHOT") or None)
    #: the time zone "today" is taken in (the agent resolves "last month" against it)
    time_zone: str = field(default_factory=lambda: os.environ.get("CC_API_TIME_ZONE") or DEFAULT_TIME_ZONE)
    paid_api_allowed: bool = field(default_factory=paid_api_enabled)

    @property
    def paid(self) -> bool:
        return self.model_kind == "bedrock"

    def check(self, *, signed_in_customer_data: bool) -> None:
        """Fail fast on anything the configured model cannot run with."""
        if self.model_kind not in ANALYZER_MODEL_KINDS:
            raise AnalyzerConfigurationError(f"CC_API_ANALYZER_MODEL={self.model_kind!r}; expected one of "
                                             f"{', '.join(ANALYZER_MODEL_KINDS)}")
        if self.snapshot_path is not None and not (Path(self.snapshot_path) / "meta.json").is_file():
            raise AnalyzerConfigurationError(f"CC_API_ANALYZER_SNAPSHOT={self.snapshot_path!r} is not a bench snapshot "
                                             "directory (no meta.json)")
        try:
            ZoneInfo(self.time_zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise AnalyzerConfigurationError(f"CC_API_TIME_ZONE={self.time_zone!r} is not an IANA time zone "
                                             "(e.g. America/Los_Angeles)") from None
        if self.model_kind != "bedrock":
            return
        if not self.paid_api_allowed:
            raise AnalyzerConfigurationError(f"CC_API_ANALYZER_MODEL=bedrock calls a paid model API: set "
                                             f"{PAID_SWITCH_ENV}={PAID_SWITCH_VALUE}")
        if not self.strands_model_id.strip():
            raise AnalyzerConfigurationError("CC_STRANDS_MODEL_ID is empty; e.g. us.anthropic.claude-haiku-5-5")
        if self.snapshot_path is None:
            raise AnalyzerConfigurationError("CC_API_ANALYZER_MODEL=bedrock needs CC_API_ANALYZER_SNAPSHOT (the bench "
                                             "snapshot: B1's example library). Build it with: python -c \"from "
                                             "bench.dataset import snapshot; print(snapshot())\"")
        if not signed_in_customer_data:
            raise AnalyzerConfigurationError("CC_API_ANALYZER_MODEL=bedrock runs on a signed-in customer's lake: start "
                                             "python -m api.sim_server (SimAuth). Demo auth's synthetic data does not "
                                             "fit B1's briefing; python -m api.strands_smoke runs one question on "
                                             "the bench snapshot.")


def aws_session(profile: str, region: str | None, session_factory=None):
    """A boto3 session for ``profile``; the region is ``region``, else the profile's. Fails fast without either."""
    if session_factory is None:
        import boto3
        session_factory = boto3.Session
    try:
        session = session_factory(profile_name=profile, region_name=region) if region else \
            session_factory(profile_name=profile)
    except Exception as exc:  # botocore ProfileNotFound and friends: name the fix, not a traceback
        raise AnalyzerConfigurationError(f"AWS profile {profile!r} could not be loaded ({type(exc).__name__}: {exc}). "
                                         f"Configure it (aws configure sso --profile {profile}) or set "
                                         "CC_AWS_PROFILE.") from None
    if not session.region_name:
        raise AnalyzerConfigurationError(f"AWS profile {profile!r} has no region: set CC_AWS_REGION (e.g. us-east-1) "
                                         f"or `region` in the profile")
    return session


def bedrock_model_factory(settings: AnalyzerSettings, session_factory=None):
    """A function returning a fresh Strands ``BedrockModel`` per run over one boto3 session (built now, so a missing
    profile or region fails at startup)."""
    from strands.models import BedrockModel
    from strands.models.model import CacheConfig
    session = aws_session(settings.aws_profile, settings.aws_region, session_factory)

    def make_model(_scenario=None):
        return BedrockModel(boto_session=session, model_id=settings.strands_model_id, max_tokens=BEDROCK_MAX_TOKENS,
                            cache_config=CacheConfig(strategy="anthropic"))
    return make_model


class StrandsAnalyzer:
    """``answer(question, tap, cancelled, opener, scenario)``: one B1 run to its ``AnalyzerAnswer``."""

    def __init__(self, settings: AnalyzerSettings, make_model, model_label: str, clock=None):
        self.settings = settings
        self.make_model = make_model
        self.model_label = model_label
        self.clock = clock or (lambda: datetime.now(ZoneInfo(settings.time_zone)))

    def today(self) -> date:
        return self.clock().date()

    def answer(self, question: str, tap, cancelled: threading.Event, opener, scenario: str | None = None
               ) -> AnalyzerAnswer:
        snapshot = Path(self.settings.snapshot_path) if self.settings.snapshot_path else None
        answer = answer_question(question, opener=opener, model=self.make_model(scenario), model_label=self.model_label,
                                 observer=tap, cancelled=cancelled, today=self.today(), snapshot_path=snapshot)
        tap.record_model_usage(self.model_label, answer.usage)
        return answer


def build_analyzer(settings: AnalyzerSettings, *, signed_in_customer_data: bool, demo_pace_seconds: float = 0.0,
                   session_factory=None) -> StrandsAnalyzer:
    settings.check(signed_in_customer_data=signed_in_customer_data)
    if settings.model_kind == "bedrock":
        return StrandsAnalyzer(settings, bedrock_model_factory(settings, session_factory), settings.strands_model_id)
    from api.demo_model import demo_model
    return StrandsAnalyzer(settings, lambda scenario=None: demo_model(scenario, demo_pace_seconds),
                           SCRIPTED_MODEL_LABEL)
