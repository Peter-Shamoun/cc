"""FastAPI app: POST /api/chat (SSE), stop, downloads, health, conversations.

Run: ``python -m api --port 8787`` (demo mode; or ``uvicorn api.app:app --no-server-header``) or ``CC_API_MODE=sim`` with a world
passed to ``create_app(auth=SimAuth(world.aws, world.dep))``.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field

from analyzer.config import AnalyzerConfig
from analyzer.llm import PaidAPIDisabled
from analyzer.loop import Analyzer

from api.backends import SCENARIO_CONFIG, AuthError, DemoAuth, detect_scenario, make_llm
from api.contract import CONTRACT_VERSION, schema as contract_schema
from api.ids import new_id
from api.live import Cancelled, LiveTap
from api.mapping import outcome_parts, report_artifacts
from api.redact import install_access_log_redaction, install_log_redaction
from api.security import (MAX_BODY_BYTES, SESSION_COOKIE, BodyLimitMiddleware, CsrfMiddleware, ErrorShieldMiddleware,
                          SecurityHeadersMiddleware)
from api.store import DownloadRegistry, Principal
from api.feedback import MAX_COMMENT, check as check_feedback, record as feedback_record
from api.capabilities import DEFAULT_STRATEGY, capabilities as capabilities_body, check_options
from api.export import FORMATS as EXPORT_FORMATS, as_json as export_json, as_markdown as export_markdown, filename as export_filename
from api.share import ShareRegistry, link as share_link, shared_view
from api.sources import sources_for
from api.stream_parts import emit_parts
from api.styles import apply_style
from api.titles import title_from_parts
from api.transcripts import CustomerKeyDisabled, make_store

log = logging.getLogger("cc.api")
_SAFE_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,40}")
#: rows per ``table.rows`` event (contract 1.9.0)
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


_ID = r"^[A-Za-z0-9_:.-]{1,128}$"
_SAFE_ID = re.compile(_ID)


class RenameIn(BaseModel):
    title: str = Field(max_length=500)


class FeedbackIn(BaseModel):
    rating: Literal["up", "down"]
    reasons: list[str] = Field(default_factory=list, max_length=9)
    comment: str | None = Field(default=None, max_length=MAX_COMMENT)


class ShareIn(BaseModel):
    includeData: bool = False


class SearchIn(BaseModel):
    q: str = Field(default="", max_length=200)
    cursor: str | None = Field(default=None, max_length=16)


class RetentionIn(BaseModel):
    days: int = Field(ge=0, le=3650)


class ChatMessageIn(BaseModel):
    id: str | None = Field(default=None, pattern=_ID)
    role: str = "user"
    text: str = Field(default="", max_length=8000)
    parentId: str | None = Field(default=None, pattern=_ID)
    #: id of an earlier user message this one replaces (an edit); the new message becomes its sibling (1.2.0)
    editOf: str | None = Field(default=None, pattern=_ID)


class ResumeIn(BaseModel):
    clarificationId: str = Field(pattern=_ID)
    choiceIds: list[str] = Field(default_factory=list, max_length=8)
    text: str | None = Field(default=None, max_length=2000)


class RegenerateIn(BaseModel):
    messageId: str = Field(pattern=_ID)
    style: Literal["default", "concise", "detailed"] = "default"


class ChatRequest(BaseModel):
    conversationId: str | None = Field(default=None, pattern=_ID)
    messages: list[ChatMessageIn] = Field(default_factory=list)
    options: dict = Field(default_factory=dict)
    resume: ResumeIn | None = None
    regenerate: RegenerateIn | None = None
    scenario: str | None = Field(default=None, max_length=32)


#: Demo pacing (QA-P1/P4). With the scripted LLM, answers are paced like a real model so the streaming UI
#: is actually exercised: a pause per model call and per text chunk. CC_API_DEMO_PACE=0 turns it off (tests),
#: and explicit CC_API_MODEL_DELAY_SECONDS / CC_API_STREAM_DELAY_SECONDS always win.
DEMO_PACE_MODEL_SECONDS = 0.25
DEMO_PACE_CHUNK_SECONDS = 0.02
#: The "slow" scenario must stay stoppable: at least this long per model call while pacing is on.
SLOW_FLOOR_SECONDS = 0.8


def demo_pace_enabled() -> bool:
    return os.environ.get("CC_API_DEMO_PACE", "1") != "0" and os.environ.get("CC_API_LLM", "scripted") == "scripted"


def _paced(name: str, paced_default: float) -> float:
    value = os.environ.get(name)
    if value is not None:
        return float(value)
    return paced_default if demo_pace_enabled() else 0.0


def _safe_filename(name: str) -> str:
    """Header-safe download name: [A-Za-z0-9._-] only (no quotes, CR/LF or path parts), always .csv."""
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "").strip("._") or "download"
    return base if base.endswith(".csv") else base + ".csv"


@dataclass
class Settings:
    llm: str = os.environ.get("CC_API_LLM", "scripted")
    heartbeat_seconds: float = float(os.environ.get("CC_API_HEARTBEAT_SECONDS", "15"))
    resume_seconds: float = 60.0
    #: a run whose last client disconnected is cancelled after this grace period unless a client re-attaches
    #: (``GET /api/chat/{id}/stream`` or ``POST /api/chat`` + ``Last-Event-ID``). CONTRACT.md §8.
    disconnect_grace_seconds: float = float(os.environ.get("CC_API_DISCONNECT_GRACE_SECONDS", "10"))
    stream_delay_seconds: float = field(default_factory=lambda: _paced("CC_API_STREAM_DELAY_SECONDS", DEMO_PACE_CHUNK_SECONDS))
    #: simulated model latency per analyzer turn in demo mode (realistic streaming); tests use 0
    model_delay_seconds: float = field(default_factory=lambda: _paced("CC_API_MODEL_DELAY_SECONDS", DEMO_PACE_MODEL_SECONDS))
    demo_pace: bool = field(default_factory=demo_pace_enabled)
    max_body_bytes: int = int(os.environ.get("CC_API_MAX_BODY_BYTES", str(MAX_BODY_BYTES)))
    #: unfinished /api/chat runs allowed per user at once (429 rate_limited beyond it)
    max_concurrent_runs: int = int(os.environ.get("CC_API_MAX_CONCURRENT_RUNS", "3"))
    clarification_ttl_seconds: float = 1800.0
    #: background retention sweep (store.purge_expired) interval while the app is running; 0 = off
    transcript_sweep_seconds: float = field(default_factory=lambda: float(os.environ.get("CC_TRANSCRIPT_SWEEP_SECONDS",
                                                                                         "3600")))
    #: "demo" (default) lets create_app fall back to DemoAuth; anything else requires an explicit auth backend
    auth_mode: str = field(default_factory=lambda: os.environ.get("CC_API_AUTH", "demo"))
    analyzer_overrides: dict = field(default_factory=lambda: {
        "glossary": "basic", "data_dictionary": False, "data_profile": False, "row_limit": 5000,
        "candidates": 1, "cascade": "off"})


class AuditLog:
    """Structured audit events. Field allowlist: no SQL text, no row values, no question text."""

    ALLOWED = {"customer", "user", "file_id", "row_count", "conversation_id", "message_id", "parts",
               "queries", "elapsed_ms", "finish", "kind", "reason", "status", "scenario", "steps"}

    def __init__(self):
        self.events: list[dict] = []
        self._lock = threading.Lock()

    def __call__(self, event: str, **detail):
        record = {"event": event, "at": time.time(), **{k: v for k, v in detail.items() if k in self.ALLOWED}}
        with self._lock:
            self.events.append(record)
        log.info("audit %s", json.dumps(record, default=str))


@dataclass
class _Run:
    message_id: str
    principal: Principal
    events: list = field(default_factory=list)  # (seq, payload)
    done: bool = False
    cancelled: threading.Event = field(default_factory=threading.Event)
    finished_at: float | None = None
    subscribers: set = field(default_factory=set)  # (event loop, asyncio.Event) per attached client
    attached: int = 0
    grace: threading.Timer | None = None
    conversation_id: str | None = None
    #: set when the conversation is deleted mid-run: the worker must not write it back (no resurrection)
    discarded: threading.Event = field(default_factory=threading.Event)

    def notify(self):
        """Wake every attached client, each on its own event loop. Thread-safe; never raises."""
        for loop, ev in list(self.subscribers):
            try:
                loop.call_soon_threadsafe(ev.set)
            except RuntimeError:  # that client's loop is gone
                self.subscribers.discard((loop, ev))


def _token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(SESSION_COOKIE, "")  # never a query parameter: URLs end up in logs


def create_app(auth=None, settings: Settings | None = None, store=None, llm_factory=None) -> FastAPI:
    """``llm_factory`` (no args -> LLM) overrides ``settings.llm``; tests use it to replay a script."""
    settings = settings or Settings()
    if auth is None:
        if settings.auth_mode != "demo":
            raise RuntimeError(f"CC_API_AUTH={settings.auth_mode!r} needs an explicit auth backend "
                               "(create_app(auth=...)); refusing to fall back to demo tokens")
        auth = DemoAuth()
    audit = AuditLog()
    store = store or make_store(audit=audit)
    downloads = DownloadRegistry()
    shares = ShareRegistry()
    runs: dict[str, _Run] = {}
    purge_lock = threading.Lock()  # a delete and a run's final transcript write never interleave

    @asynccontextmanager
    async def lifespan(_app):
        async def sweep():
            while True:
                await asyncio.sleep(settings.transcript_sweep_seconds)
                try:
                    removed = await asyncio.to_thread(store.purge_expired)
                    if removed:
                        log.info("retention sweep removed %d conversations", removed)
                except Exception as exc:  # noqa: BLE001 - keep sweeping
                    log.warning("retention sweep failed: %s", type(exc).__name__)

        task = None
        if settings.transcript_sweep_seconds > 0 and hasattr(store, "purge_expired"):
            await asyncio.to_thread(store.purge_expired)  # once at startup: catch up on what expired while down
            task = asyncio.create_task(sweep())
        try:
            yield
        finally:
            if task:
                task.cancel()

    app = FastAPI(title="CC chat API", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    install_log_redaction()
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(ErrorShieldMiddleware)  # inside SecurityHeaders: 500s get the headers too
    app.add_middleware(SecurityHeadersMiddleware)  # outermost, so the 413 carries the security headers too
    app.state.audit, app.state.store, app.state.downloads, app.state.runs = audit, store, downloads, runs
    app.state.settings, app.state.auth = settings, auth

    def authed(request: Request, request_id: str):
        return auth.authenticate(_token(request), request_id)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        # 1.12.0: unmatched routes (404) and wrong methods (405) use the standard envelope too, not {"detail"}.
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, f"http_{exc.status_code}")
        message = {404: "Not found.", 405: "Method not allowed."}.get(exc.status_code, "Request failed.")
        return JSONResponse({"error": {"code": code, "message": message}}, exc.status_code,
                            headers={**NO_STORE, **(exc.headers or {})})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        # FastAPI's default 422 echoes the offending ``input`` (it can be PHI) and isn't the contract envelope.
        # Report field paths only, with user-controlled dict keys masked.
        fields = sorted({".".join(str(p) if isinstance(p, int) or _SAFE_KEY.fullmatch(str(p)) else "*"
                                  for p in e.get("loc", ())[1:]) for e in exc.errors()})[:10]
        msg = "Invalid request" + (f": {', '.join(f for f in fields if f)}." if any(fields) else ".")
        return error_json("validation", msg, 422)

    def forget(principal, conversation_id: str | None) -> None:
        """Everything outside the store that still holds this conversation (None = all of the principal's): stop
        and discard in-flight runs, so their final write can't resurrect it, and their resume buffers are dropped at
        the next gc. Also drops pending clarifications (they hold the question text) and revokes CSV downloads."""
        for run in list(runs.values()):
            if run.principal == principal and (conversation_id is None or run.conversation_id == conversation_id):
                run.discarded.set()
                run.cancelled.set()
        for key, pending in list(clarifications.items()):
            if pending["principal"] == principal and (conversation_id is None
                                                      or pending["conversation_id"] == conversation_id):
                clarifications.pop(key, None)
        downloads.revoke(principal, conversation_id)

    def unauthorized():
        return JSONResponse({"error": {"code": "auth_expired", "message": "Sign in again."}}, 401, headers=NO_STORE)

    def gc_runs():
        now = time.time()
        for key in [k for k, r in runs.items() if r.finished_at and (
                r.discarded.is_set() or now - r.finished_at > settings.resume_seconds)]:
            del runs[key]

    @app.get("/api/health")
    def health():
        return JSONResponse({"status": "ok", "mode": getattr(auth, "mode", "custom"), "llm": settings.llm,
                             "paidApis": False}, headers=NO_STORE)

    @app.get("/api/capabilities")
    def capabilities(request: Request):
        """Strategies and models for a picker (contract 1.4.0). Signed in (1.5.0): per-organization ``available``.
        Signed out it still answers (no user data), without org-specific flags."""
        try:
            customer = authed(request, new_id("r")).principal.customer if _token(request) else None
        except AuthError:
            customer = None
        return JSONResponse(capabilities_body(CONTRACT_VERSION, demo=settings.llm == "scripted", customer=customer),
                            headers=NO_STORE)

    @app.post("/api/auth/sign-out")
    def sign_out(request: Request):
        """Ends the session everywhere it can. Server side: ``auth.sign_out(token)`` (best effort, any token, even
        expired). Browser side, always: ``Clear-Site-Data: "cache", "storage"`` wipes the HTTP cache, localStorage,
        sessionStorage, IndexedDB, Cache Storage and service workers for this origin, and ``cc_session`` is expired
        explicitly (not ``"cookies"``, which would clear every cookie on the registrable domain). CSRF-protected like
        every cookie-borne write, so a third-party page can't sign the user out. 204, no body."""
        token = _token(request)
        principal = None
        if token:
            try:
                principal = authed(request, new_id("r")).principal
            except AuthError:
                pass
            try:
                revoke = getattr(auth, "sign_out", None)  # backends written before 1.x may lack it
                if revoke:
                    revoke(token)
            except Exception as exc:  # noqa: BLE001 - sign-out must always clear the browser
                log.warning("sign_out revoke failed: %s", type(exc).__name__)
        if principal is not None:
            audit("signed_out", customer=principal.customer, user=principal.user)
        resp = Response(status_code=204, headers={**NO_STORE, "Clear-Site-Data": '"cache", "storage"'})
        for path in ("/api", "/"):  # a cookie is only removed by a matching Path; the spec'd one is /api
            resp.delete_cookie(SESSION_COOKIE, path=path, secure=True, httponly=True, samesite="strict")
        return resp

    @app.get("/api/contract")
    def contract():
        return JSONResponse({"version": CONTRACT_VERSION, "schema": contract_schema()}, headers=NO_STORE)

    clarifications: dict[str, dict] = {}
    app.state.clarifications = clarifications

    def error_json(code: str, message: str, status: int, legacy_code: str | None = None):
        error = {"code": code, "message": message, **({"legacyCode": legacy_code} if legacy_code else {})}
        return JSONResponse({"error": error}, status, headers=NO_STORE)

    @app.post("/api/chat")
    async def chat(body: ChatRequest, request: Request):
        gc_runs()
        last_event = request.headers.get("last-event-id")
        if last_event and ":" in last_event:
            mid, _, seq = last_event.rpartition(":")
            run = runs.get(mid)
            try:
                ctx = authed(request, new_id("r"))
            except AuthError:
                return unauthorized()
            if run is None or run.principal != ctx.principal:
                return error_json("stream_gone", "Nothing to resume.", 404)
            return _stream(run, int(seq) if seq.isascii() and seq.isdigit() else -1, request)
        users = [m for m in body.messages if m.role == "user" and m.text.strip()]
        if not users and not body.regenerate:
            return error_json("validation", "Send a question.", 422)
        message_id = new_id("m_")
        try:
            ctx = authed(request, message_id[-8:])
        except AuthError:
            return unauthorized()
        bad_option = check_options(body.options, ctx.principal.customer)
        if bad_option:
            return error_json("validation", bad_option, 422)
        conversation_id = body.conversationId or new_id("c_")
        if not _SAFE_ID.match(conversation_id) or store.owned_by_other(ctx.principal, conversation_id):
            return error_json("access_denied", "Not found.", 404)
        demo = getattr(auth, "mode", "") == "demo"
        # Demo-only fault injection for the web client's 429 auto-retry (web ?fault=429, dev builds only).
        # Ignored outside demo mode; nothing is stored or run.
        if demo and request.headers.get("x-demo-fault") == "rate_limited":
            return JSONResponse(
                {"error": {"code": "rate_limited", "message": "Too many requests (demo fault)."}},
                429,
                headers={**NO_STORE, "Retry-After": "3"},
            )
        # One user can't pin the analyzer threads: at most max_concurrent_runs unfinished runs per principal.
        active = sum(1 for r in list(runs.values()) if r.principal == ctx.principal and not r.done)
        if active >= settings.max_concurrent_runs:
            audit("chat_rate_limited", customer=ctx.principal.customer, user=ctx.principal.user, reason="concurrent_runs")
            return JSONResponse(
                {"error": {"code": "rate_limited", "message": "Too many answers in progress. Wait for one to finish."}},
                429,
                headers={**NO_STORE, "Retry-After": "3"},
            )
        start_extra: dict = {}
        scenario_hint = body.scenario if demo else None
        if body.regenerate:
            try:
                conv = store.get_conversation(ctx.principal, conversation_id) if body.conversationId else None
            except CustomerKeyDisabled:
                return shredded()
            msgs = (conv or {}).get("messages", [])
            target = next((m for m in msgs if m.get("id") == body.regenerate.messageId and m.get("role") == "assistant"), None)
            parent = next((m for m in msgs if target and m.get("id") == target.get("parentId")), None)
            if parent is None:
                return error_json("not_found", "Nothing to regenerate.", 404, legacy_code="validation")
            question = parent.get("analyzerQuestion") or parent["text"]
            user_msg = parent
            start_extra["regenerateOf"] = body.regenerate.messageId
            if body.regenerate.style != "default":
                start_extra["regenerateStyle"] = body.regenerate.style
            scenario_hint = scenario_hint or parent.get("scenario")
        else:
            reply = users[-1].text.strip()
            question = reply
            edited = None
            if users[-1].editOf:
                conv = store.get_conversation(ctx.principal, conversation_id) if body.conversationId else None
                edited = next((m for m in (conv or {}).get("messages", [])
                               if m.get("id") == users[-1].editOf and m.get("role") == "user"), None)
                if edited is None:
                    return error_json("not_found", "Nothing to edit.", 404, legacy_code="validation")
                start_extra["editOf"] = edited["id"]
            if body.resume:
                now = time.time()
                for key in [k for k, v in clarifications.items() if v["expires"] < now]:
                    del clarifications[key]
                pending = clarifications.get(body.resume.clarificationId)
                if pending is None or pending["principal"] != ctx.principal or pending["conversation_id"] != conversation_id:
                    return error_json("clarification_expired", "That question has expired; send it as a new question.", 409)
                del clarifications[body.resume.clarificationId]
                labels = [c["label"] for c in pending["choices"] if c["id"] in set(body.resume.choiceIds)]
                answer_text = "; ".join(labels) or (body.resume.text or reply)
                question = f"{pending['question']}\nClarification: {answer_text}"
                start_extra["resumeOf"] = {"clarificationId": body.resume.clarificationId,
                                           "messageId": pending["message_id"]}
                scenario_hint = scenario_hint or pending.get("scenario")
            user_msg = {"id": users[-1].id or new_id("m_"), "role": "user", "text": reply,
                        "createdAt": _iso(time.time())}
            if users[-1].parentId:
                user_msg["parentId"] = users[-1].parentId
            if edited is not None:
                # an edit is a sibling of the message it replaces: same parent (none = a new root), CONTRACT.md §8
                user_msg["editOf"] = edited["id"]
                user_msg.pop("parentId", None)
                if edited.get("parentId"):
                    user_msg["parentId"] = edited["parentId"]
            if question != reply:
                user_msg["analyzerQuestion"] = question
            try:
                store.append_message(ctx.principal, conversation_id, user_msg)
            except CustomerKeyDisabled:
                return shredded()
        scenario = detect_scenario(question, scenario_hint) if demo else None
        if scenario and "scenario" not in user_msg and isinstance(user_msg, dict):
            user_msg["scenario"] = scenario
        legacy_tools = bool(body.options.get("legacyToolParts"))
        run = _Run(message_id, ctx.principal, conversation_id=conversation_id)
        runs[message_id] = run
        started = time.perf_counter()
        marks: dict = {}
        persisted_steps: list = []
        reasoning: dict = {}

        def emit(payload: dict):
            seq = len(run.events)
            event = {**payload, "seq": seq}
            if "first_event" not in marks:
                marks["first_event"] = int((time.perf_counter() - started) * 1000)
            t = event["type"]
            if t.startswith("step.") or t == "escalation":
                persisted_steps.append(event)
            elif t == "reasoning.delta":
                reasoning.setdefault(event["partId"], {"type": "reasoning", "id": event["partId"], "summary": ""})
                reasoning[event["partId"]]["summary"] += event["delta"]
            elif t == "reasoning.end" and event["partId"] in reasoning:
                reasoning[event["partId"]]["durationMs"] = event["durationMs"]
            elif t == "text.delta" and "first_token" not in marks:
                marks["first_token"] = int((time.perf_counter() - started) * 1000)
            run.events.append((seq, event))
            run.notify()

        def work():
            parts: list[dict] = []
            finish = "stop"
            overrides = {**settings.analyzer_overrides, **SCENARIO_CONFIG.get(scenario or "", {})}
            delay = settings.model_delay_seconds * (4 if scenario == "slow" else 1)
            if scenario == "slow" and settings.demo_pace:
                delay = max(delay, SLOW_FLOOR_SECONDS)
            emit({"type": "message.start", "messageId": message_id, "conversationId": conversation_id,
                  "contractVersion": CONTRACT_VERSION, "model": settings.llm, "parentId": user_msg["id"],
                  "strategy": body.options.get("strategy", DEFAULT_STRATEGY), **start_extra})
            emit({"type": "status", "label": "Thinking"})
            tap = None
            answer = None
            try:
                config = AnalyzerConfig(**overrides)
                inner = llm_factory() if llm_factory else make_llm(settings.llm, scenario)
                tap = LiveTap(inner, emit, cancelled=run.cancelled, model_delay=delay, started=started,
                              multi_candidate=config.candidates > 1)
                analyzer = Analyzer(ctx.opener, tap, config)
                answer = analyzer.answer(question)
                tap.finish(answer)
                elapsed = int((time.perf_counter() - started) * 1000)
                parts, finish = outcome_parts(answer, ctx.principal, downloads.scoped(conversation_id), elapsed_ms=elapsed, question=question,
                                              ask_args=tap.ask_args, refuse_args=tap.refuse_args,
                                              legacy_tools=legacy_tools)
                if scenario == "report" and finish == "stop":
                    parts = report_artifacts(parts)
                if finish == "stop" and start_extra.get("regenerateStyle"):
                    parts = apply_style(parts, start_extra["regenerateStyle"])
            except Cancelled:
                finish = "aborted"
                if tap is not None:
                    tap.abort()
            except PaidAPIDisabled:
                parts = [_error("internal", "Model unavailable", "Paid model APIs are disabled on this server.", False)]
                finish = "error"
            except Exception as exc:  # noqa: BLE001 - never echo internals or data to the client or log
                log.warning("chat failed: %s", type(exc).__name__)
                if tap is not None:
                    tap.finish(None)
                parts = [_error("internal", "Something went wrong", "The analysis failed. Try again.", True)]
                finish = "error"
            if run.cancelled.is_set():
                finish = "aborted"
            if tap is not None:
                tap.enter("render")
            emit({"type": "status", "label": "Writing answer" if finish == "stop" else None})
            def remember_clarification(part: dict):
                if part["type"] == "clarification" and not run.discarded.is_set():
                    clarifications[part["clarificationId"]] = {
                        "principal": ctx.principal, "conversation_id": conversation_id, "question": question,
                        "message_id": message_id, "choices": part.get("choices", []), "scenario": scenario,
                        "expires": time.time() + settings.clarification_ttl_seconds}

            if not emit_parts(parts, emit, cancelled=run.cancelled, delay=settings.stream_delay_seconds,
                              on_part=remember_clarification):
                finish = "aborted"
            elapsed = int((time.perf_counter() - started) * 1000)
            emit({"type": "status", "label": None})
            usage = (tap.usage(elapsed, marks.get("first_event"), marks.get("first_token")) if tap is not None
                     else {"queries": 0, "elapsedMs": elapsed})
            end = {"type": "message.end", "messageId": message_id, "finishReason": finish, "usage": usage}
            sources = sources_for(parts, persisted_steps) if finish != "aborted" else []
            if sources:
                end["sources"] = sources
            title = title_from_parts(parts) if finish == "stop" else None
            if title and conversation_id:
                try:
                    if store.auto_title(ctx.principal, conversation_id, title):
                        end["conversationTitle"] = title
                except Exception as exc:  # noqa: BLE001 - a title must never break the stream
                    log.warning("auto title failed: %s", type(exc).__name__)
            emit(end)
            kept = list(reasoning.values()) + parts if finish != "aborted" else []
            record = {"id": message_id, "role": "assistant", "parts": kept, "createdAt": _iso(time.time()),
                      "parentId": user_msg["id"], "finishReason": finish, "usage": usage,
                      "steps": persisted_steps if finish != "aborted" else []}
            if sources:
                record["sources"] = sources
            if start_extra.get("regenerateOf"):
                record["regenerateOf"] = start_extra["regenerateOf"]
            if start_extra.get("regenerateStyle"):
                record["regenerateStyle"] = start_extra["regenerateStyle"]
            try:
                with purge_lock:
                    if not run.discarded.is_set():
                        store.append_message(ctx.principal, conversation_id, record)
            except Exception as exc:  # noqa: BLE001 - a history write must not hang the stream
                log.warning("transcript append failed: %s", type(exc).__name__)
            audit("chat_message", customer=ctx.principal.customer, user=ctx.principal.user,
                  conversation_id=conversation_id, message_id=message_id, parts=len(parts), queries=usage["queries"],
                  elapsed_ms=elapsed, finish=finish, scenario=scenario, steps=len(persisted_steps))
            run.finished_at = time.time()
            run.done = True
            run.notify()

        threading.Thread(target=work, name=f"chat-{message_id}", daemon=True).start()

        return _stream(run, -1, request)

    def _detach(run: _Run):
        run.attached -= 1
        if run.attached > 0 or run.done:
            return
        if settings.disconnect_grace_seconds <= 0:
            run.cancelled.set()
            return

        def expire():
            run.grace = None
            if run.attached == 0 and not run.done:
                run.cancelled.set()
        if run.grace is None:  # a thread timer: independent of whichever event loop served the dropped client
            run.grace = threading.Timer(settings.disconnect_grace_seconds, expire)
            run.grace.daemon = True
            run.grace.start()

    def _stream(run: _Run, after: int, request: Request):
        """Replay events with seq > ``after``, then follow the run live. Heartbeat on silence (no id).

        Several clients may be attached to one run; the run is only cancelled when none is attached for
        ``disconnect_grace_seconds`` (CONTRACT.md §8)."""
        async def gen():
            wake = asyncio.Event()
            sub = (asyncio.get_running_loop(), wake)
            run.subscribers.add(sub)
            run.attached += 1
            if run.grace is not None:
                run.grace.cancel()
                run.grace = None
            sent = after
            try:
                while True:
                    wake.clear()
                    for seq, payload in run.events[sent + 1:]:
                        yield _sse(f"{run.message_id}:{seq}", payload)
                        sent = seq
                    if run.done and sent >= len(run.events) - 1:
                        return
                    try:
                        await asyncio.wait_for(wake.wait(), timeout=settings.heartbeat_seconds)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            return
                        yield _sse(None, {"type": "heartbeat", "at": _iso(time.time())})
            finally:
                run.subscribers.discard(sub)
                _detach(run)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={**NO_STORE, "X-Accel-Buffering": "no"})

    @app.get("/api/chat/{message_id}/stream")
    def resume_stream(message_id: str, request: Request):
        """EventSource-friendly resume: replays events after ``Last-Event-ID`` (or all), then follows live."""
        gc_runs()
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return unauthorized()
        run = runs.get(message_id)
        if run is None or run.principal != ctx.principal:
            return error_json("stream_gone", "Nothing to resume.", 404)
        last_event = request.headers.get("last-event-id") or ""
        mid, _, seq = last_event.rpartition(":")
        after = int(seq) if mid == message_id and seq.isascii() and seq.isdigit() else -1
        return _stream(run, after, request)

    @app.post("/api/chat/{message_id}/stop")
    def stop(message_id: str, request: Request):
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return unauthorized()
        run = runs.get(message_id)
        if run is None or run.principal != ctx.principal:
            # 1.13.0: the error envelope rides along, so every 4xx body has `error.code`.
            return JSONResponse({"stopped": False, "error": {"code": "not_found", "message": "Nothing to stop."}}, 404,
                                headers=NO_STORE)
        run.cancelled.set()
        return JSONResponse({"stopped": True}, headers=NO_STORE)

    @app.get("/api/download/{download_id}")
    def download(download_id: str, request: Request):
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return unauthorized()
        item = downloads.get(ctx.principal, download_id)
        if item is None:
            audit("download_denied", customer=ctx.principal.customer, user=ctx.principal.user, file_id=download_id)
            return JSONResponse({"error": {"code": "access_denied", "message": "Not found or expired."}}, 404,
                                headers=NO_STORE)
        data = item.csv_bytes()
        audit("download", customer=ctx.principal.customer, user=ctx.principal.user, file_id=item.id,
              row_count=len(item.rows))
        return Response(data, media_type="text/csv; charset=utf-8", headers={
            **NO_STORE, "Content-Disposition": f'attachment; filename="{_safe_filename(item.name)}"'})

    def not_found():
        return JSONResponse({"error": {"code": "not_found", "message": "Not found."}}, 404, headers=NO_STORE)

    def shredded():
        return JSONResponse({"error": {"code": "transcripts_unavailable",
                                       "message": "Chat history is unavailable for this organization."}},
                            410, headers=NO_STORE)

    @app.get("/api/transcripts")
    def transcripts_info(request: Request):
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return unauthorized()
        return JSONResponse({"mode": store.mode, "persisted": store.mode == "persisted",
                             "retentionDays": store.retention_days(ctx.principal)}, headers=NO_STORE)

    @app.put("/api/transcripts/retention")
    def set_retention(body: RetentionIn, request: Request):
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return unauthorized()
        store.set_retention(ctx.principal, body.days)
        audit("retention_set", customer=ctx.principal.customer, user=ctx.principal.user, status=body.days)
        return JSONResponse({"mode": store.mode, "retentionDays": store.retention_days(ctx.principal)},
                            headers=NO_STORE)

    @app.get("/api/conversations")
    def conversations(request: Request, cursor: str | None = None):
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return unauthorized()
        try:
            return JSONResponse(store.list_conversations(ctx.principal, cursor), headers=NO_STORE)
        except CustomerKeyDisabled:
            return shredded()

    @app.post("/api/conversations/search")
    def search_conversations(body: SearchIn, request: Request):
        """POST, not GET: the search text can be PHI and must never sit in a URL (contract section 10)."""
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return unauthorized()
        query = body.q.strip()
        if not query:
            return JSONResponse({"items": []}, headers=NO_STORE)
        try:
            return JSONResponse(store.list_conversations(ctx.principal, body.cursor, query), headers=NO_STORE)
        except CustomerKeyDisabled:
            return shredded()

    @app.delete("/api/conversations")
    def delete_all_conversations(request: Request):
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return unauthorized()
        with purge_lock:
            count = store.delete_all(ctx.principal)
            forget(ctx.principal, None)
        return JSONResponse({"deleted": count}, headers=NO_STORE)

    @app.get("/api/conversations/{conversation_id}")
    def conversation(conversation_id: str, request: Request):
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return unauthorized()
        try:
            conv = store.get_conversation(ctx.principal, conversation_id)
        except CustomerKeyDisabled:
            return shredded()
        if conv is None:
            return not_found()
        return JSONResponse(conv, headers=NO_STORE)

    @app.patch("/api/conversations/{conversation_id}")
    def rename_conversation(conversation_id: str, body: RenameIn, request: Request):
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return unauthorized()
        try:
            out = store.rename_conversation(ctx.principal, conversation_id, body.title)
        except ValueError:
            return JSONResponse({"error": {"code": "invalid_title", "message": "Use a single line of text."}}, 422,
                                headers=NO_STORE)
        except CustomerKeyDisabled:
            return shredded()
        if out is None:
            return not_found()
        audit("conversation_renamed", customer=ctx.principal.customer, user=ctx.principal.user,
              conversation_id=conversation_id)
        return JSONResponse(out, headers=NO_STORE)

    @app.get("/api/conversations/{conversation_id}/export")
    def export_conversation(conversation_id: str, request: Request, format: str = "markdown"):
        """Download the conversation as Markdown or JSON (contract 1.8.0, §15). Your own transcript only."""
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return unauthorized()
        if format not in EXPORT_FORMATS:
            return error_json("validation", "Use format=markdown or format=json.", 422)
        try:
            conv = store.get_conversation(ctx.principal, conversation_id) if _SAFE_ID.match(conversation_id) else None
        except CustomerKeyDisabled:
            return shredded()
        if conv is None:
            return not_found()
        now = _iso(time.time())
        if format == "json":
            body = json.dumps(export_json(conv, CONTRACT_VERSION, now), ensure_ascii=False, default=str)
        else:
            body = export_markdown(conv, now)
        audit("conversation_exported", customer=ctx.principal.customer, user=ctx.principal.user,
              conversation_id=conversation_id, kind=format)
        mime = EXPORT_FORMATS[format][0]
        return Response(body.encode(), media_type=mime, headers={
            **NO_STORE, "Content-Disposition": f'attachment; filename="{export_filename(format)}"'})

    @app.put("/api/conversations/{conversation_id}/messages/{message_id}/feedback")
    def put_feedback(conversation_id: str, message_id: str, body: FeedbackIn, request: Request):
        """Thumbs up/down on an assistant answer (contract 1.7.0, §13). Replaces any earlier feedback."""
        return _feedback(conversation_id, message_id, body, request)

    @app.delete("/api/conversations/{conversation_id}/messages/{message_id}/feedback")
    def delete_feedback(conversation_id: str, message_id: str, request: Request):
        return _feedback(conversation_id, message_id, None, request)

    def _feedback(conversation_id: str, message_id: str, body: FeedbackIn | None, request: Request):
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return unauthorized()
        if not (_SAFE_ID.match(conversation_id) and _SAFE_ID.match(message_id)):
            return not_found()
        record = None
        if body is not None:
            problem = check_feedback(body.rating, body.reasons)
            if problem:
                return error_json("validation", problem, 422)
            record = feedback_record(body.rating, body.reasons, body.comment)
        try:
            result = store.set_feedback(ctx.principal, conversation_id, message_id, record)
        except CustomerKeyDisabled:
            return shredded()
        if result == "missing":
            return not_found()
        if result == "not_assistant":
            return error_json("validation", "Feedback is for answers, not questions.", 422)
        # rating + reason ids only: the comment is user text, like a message, and never leaves the transcript
        audit("answer_feedback", customer=ctx.principal.customer, user=ctx.principal.user,
              conversation_id=conversation_id, message_id=message_id,
              kind=record["rating"] if record else "cleared", reason=",".join(record["reasons"]) if record else "")
        if record is None:
            return JSONResponse({"cleared": True}, headers=NO_STORE)
        return JSONResponse({"messageId": message_id, **record}, headers=NO_STORE)

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str, request: Request):
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return unauthorized()
        with purge_lock:
            ok = store.delete_conversation(ctx.principal, conversation_id)
            if ok:
                forget(ctx.principal, conversation_id)
                shares.revoke(ctx.principal.customer, ctx.principal.user, conversation_id)
        if not ok:  # 1.13.0: keep `deleted: false` and add the standard envelope
            return JSONResponse({"deleted": False, "error": {"code": "not_found", "message": "Not found."}}, 404,
                                headers=NO_STORE)
        return JSONResponse({"deleted": ok}, headers=NO_STORE)

    # ---------------------------------------------------------------- share (contract 1.11.0, §17)

    def _own_conversation(request: Request, conversation_id: str):
        """(ctx, None) when the caller owns the conversation, else (None, error response)."""
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return None, unauthorized()
        try:
            conv = store.get_conversation(ctx.principal, conversation_id) if _SAFE_ID.match(conversation_id) else None
        except CustomerKeyDisabled:
            return None, shredded()
        return (ctx, None) if conv is not None else (None, not_found())

    @app.get("/api/conversations/{conversation_id}/share")
    def get_share(conversation_id: str, request: Request):
        ctx, err = _own_conversation(request, conversation_id)
        if err is not None:
            return err
        share = shares.for_conversation(ctx.principal.customer, ctx.principal.user, conversation_id)
        return JSONResponse(share_link(share), headers=NO_STORE) if share else not_found()

    @app.put("/api/conversations/{conversation_id}/share")
    def put_share(conversation_id: str, body: ShareIn, request: Request):
        """Create (or update) the org-scoped read-only link. ``includeData`` defaults to false."""
        ctx, err = _own_conversation(request, conversation_id)
        if err is not None:
            return err
        share = shares.put(ctx.principal.customer, ctx.principal.user, conversation_id, body.includeData, _iso(time.time()))
        audit("conversation_shared", customer=ctx.principal.customer, user=ctx.principal.user,
              conversation_id=conversation_id, kind="with_data" if body.includeData else "no_data")
        return JSONResponse(share_link(share), headers=NO_STORE)

    @app.delete("/api/conversations/{conversation_id}/share")
    def delete_share(conversation_id: str, request: Request):
        ctx, err = _own_conversation(request, conversation_id)
        if err is not None:
            return err
        if not shares.revoke(ctx.principal.customer, ctx.principal.user, conversation_id):
            return not_found()
        audit("conversation_share_revoked", customer=ctx.principal.customer, user=ctx.principal.user,
              conversation_id=conversation_id)
        return JSONResponse({"revoked": True}, headers=NO_STORE)

    @app.get("/api/shared/{share_id}")
    def get_shared(share_id: str, request: Request):
        """Read-only view for any signed-in user of the same organization. 404 for unknown, revoked, deleted or
        other-organization shares alike, so a link reveals nothing outside the org."""
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return unauthorized()
        share = shares.get(share_id, ctx.principal.customer) if _SAFE_ID.match(share_id) else None
        if share is None:
            return not_found()
        try:
            conv = store.get_conversation(Principal(share.customer, share.user), share.conversation_id)
        except CustomerKeyDisabled:
            return shredded()
        if conv is None:
            return not_found()
        audit("shared_conversation_viewed", customer=ctx.principal.customer, user=ctx.principal.user,
              conversation_id=share.conversation_id, kind="with_data" if share.include_data else "no_data")
        return JSONResponse(shared_view(share, conv), headers={**NO_STORE, "Referrer-Policy": "no-referrer"})

    install_access_log_redaction(app.routes)  # after every route is registered
    return app


def _error(code: str, title: str, message: str, retryable: bool) -> dict:
    return {"type": "error", "id": new_id("p_"), "code": code, "title": title, "message": message,
            "retryable": retryable}


def _sse(event_id: str | None, payload: dict) -> str:
    head = f"id: {event_id}\n" if event_id else ""
    return f"{head}data: {json.dumps(payload, default=str, separators=(',', ':'))}\n\n"


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


app = create_app()
