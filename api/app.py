"""FastAPI app: POST /api/chat (SSE), stop, downloads, health, conversations.

Run: ``uvicorn api.app:app --port 8787`` (demo mode) or ``CC_API_MODE=sim`` with a world
passed to ``create_app(auth=SimAuth(world.aws, world.dep))``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from analyzer.config import AnalyzerConfig
from analyzer.llm import PaidAPIDisabled
from analyzer.loop import Analyzer

from api.backends import SCENARIO_CONFIG, AuthError, DemoAuth, detect_scenario, make_llm
from api.contract import CONTRACT_VERSION, schema as contract_schema
from api.ids import new_id
from api.live import Cancelled, LiveTap
from api.mapping import outcome_parts
from api.security import SecurityHeadersMiddleware
from api.store import DownloadRegistry, Principal
from api.transcripts import CustomerKeyDisabled, make_store

log = logging.getLogger("cc.api")
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


_ID = r"^[A-Za-z0-9_:.-]{1,128}$"
_SAFE_ID = re.compile(_ID)


class RenameIn(BaseModel):
    title: str = Field(max_length=500)


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


class ResumeIn(BaseModel):
    clarificationId: str = Field(pattern=_ID)
    choiceIds: list[str] = Field(default_factory=list, max_length=8)
    text: str | None = Field(default=None, max_length=2000)


class RegenerateIn(BaseModel):
    messageId: str = Field(pattern=_ID)


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


@dataclass
class Settings:
    llm: str = os.environ.get("CC_API_LLM", "scripted")
    heartbeat_seconds: float = float(os.environ.get("CC_API_HEARTBEAT_SECONDS", "15"))
    resume_seconds: float = 60.0
    stream_delay_seconds: float = field(default_factory=lambda: _paced("CC_API_STREAM_DELAY_SECONDS", DEMO_PACE_CHUNK_SECONDS))
    #: simulated model latency per analyzer turn in demo mode (realistic streaming); tests use 0
    model_delay_seconds: float = field(default_factory=lambda: _paced("CC_API_MODEL_DELAY_SECONDS", DEMO_PACE_MODEL_SECONDS))
    demo_pace: bool = field(default_factory=demo_pace_enabled)
    clarification_ttl_seconds: float = 1800.0
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


def _token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get("cc_session", "")


def create_app(auth=None, settings: Settings | None = None, store=None, llm_factory=None) -> FastAPI:
    """``llm_factory`` (no args -> LLM) overrides ``settings.llm``; tests use it to replay a script."""
    settings = settings or Settings()
    auth = auth or DemoAuth()
    audit = AuditLog()
    store = store or make_store(audit=audit)
    downloads = DownloadRegistry()
    runs: dict[str, _Run] = {}
    app = FastAPI(title="CC chat API", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityHeadersMiddleware)
    app.state.audit, app.state.store, app.state.downloads, app.state.runs = audit, store, downloads, runs
    app.state.settings, app.state.auth = settings, auth

    def authed(request: Request, request_id: str):
        return auth.authenticate(_token(request), request_id)

    def unauthorized():
        return JSONResponse({"error": {"code": "auth_expired", "message": "Sign in again."}}, 401, headers=NO_STORE)

    def gc_runs():
        now = time.time()
        for key in [k for k, r in runs.items() if r.finished_at and now - r.finished_at > settings.resume_seconds]:
            del runs[key]

    @app.get("/api/health")
    def health():
        return JSONResponse({"status": "ok", "mode": getattr(auth, "mode", "custom"), "llm": settings.llm,
                             "paidApis": False}, headers=NO_STORE)

    @app.get("/api/contract")
    def contract():
        return JSONResponse({"version": CONTRACT_VERSION, "schema": contract_schema()}, headers=NO_STORE)

    clarifications: dict[str, dict] = {}
    app.state.clarifications = clarifications

    def error_json(code: str, message: str, status: int):
        return JSONResponse({"error": {"code": code, "message": message}}, status, headers=NO_STORE)

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
                return error_json("validation", "Nothing to resume.", 404)
            return _stream(run, after=int(seq) if seq.isdigit() else -1)
        users = [m for m in body.messages if m.role == "user" and m.text.strip()]
        if not users and not body.regenerate:
            return error_json("validation", "Send a question.", 422)
        message_id = new_id("m_")
        try:
            ctx = authed(request, message_id[-8:])
        except AuthError:
            return unauthorized()
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
                return error_json("validation", "Nothing to regenerate.", 404)
            question = parent.get("analyzerQuestion") or parent["text"]
            user_msg = parent
            start_extra["regenerateOf"] = body.regenerate.messageId
            scenario_hint = scenario_hint or parent.get("scenario")
        else:
            reply = users[-1].text.strip()
            question = reply
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
        run = _Run(message_id, ctx.principal)
        runs[message_id] = run
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
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
            loop.call_soon_threadsafe(queue.put_nowait, (seq, event))

        def work():
            parts: list[dict] = []
            finish = "stop"
            overrides = {**settings.analyzer_overrides, **SCENARIO_CONFIG.get(scenario or "", {})}
            delay = settings.model_delay_seconds * (4 if scenario == "slow" else 1)
            if scenario == "slow" and settings.demo_pace:
                delay = max(delay, SLOW_FLOOR_SECONDS)
            emit({"type": "message.start", "messageId": message_id, "conversationId": conversation_id,
                  "contractVersion": CONTRACT_VERSION, "model": settings.llm, "parentId": user_msg["id"],
                  "strategy": body.options.get("strategy", "tool_loop"), **start_extra})
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
                parts, finish = outcome_parts(answer, ctx.principal, downloads, elapsed_ms=elapsed, question=question,
                                              ask_args=tap.ask_args, refuse_args=tap.refuse_args,
                                              legacy_tools=legacy_tools)
            except Cancelled:
                finish = "aborted"
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
            emit({"type": "status", "label": "Writing answer" if finish == "stop" else None})
            for part in parts:
                if run.cancelled.is_set():
                    finish = "aborted"
                    break
                if part["type"] == "text":
                    emit({"type": "text.start", "partId": part["id"]})
                    for chunk in _chunks(part["text"]):
                        if run.cancelled.is_set():
                            break
                        emit({"type": "text.delta", "partId": part["id"], "delta": chunk})
                        if settings.stream_delay_seconds:
                            time.sleep(settings.stream_delay_seconds)
                    emit({"type": "text.end", "partId": part["id"]})
                else:
                    emit({"type": "part", "part": part})
                    if part["type"] == "clarification":
                        clarifications[part["clarificationId"]] = {
                            "principal": ctx.principal, "conversation_id": conversation_id, "question": question,
                            "message_id": message_id, "choices": part.get("choices", []), "scenario": scenario,
                            "expires": time.time() + settings.clarification_ttl_seconds}
            elapsed = int((time.perf_counter() - started) * 1000)
            emit({"type": "status", "label": None})
            usage = (tap.usage(elapsed, marks.get("first_event"), marks.get("first_token")) if tap is not None
                     else {"queries": 0, "elapsedMs": elapsed})
            emit({"type": "message.end", "messageId": message_id, "finishReason": finish, "usage": usage})
            kept = list(reasoning.values()) + parts if finish != "aborted" else []
            record = {"id": message_id, "role": "assistant", "parts": kept, "createdAt": _iso(time.time()),
                      "parentId": user_msg["id"], "finishReason": finish, "usage": usage,
                      "steps": persisted_steps if finish != "aborted" else []}
            if start_extra.get("regenerateOf"):
                record["regenerateOf"] = start_extra["regenerateOf"]
            try:
                store.append_message(ctx.principal, conversation_id, record)
            except Exception as exc:  # noqa: BLE001 - a history write must not hang the stream
                log.warning("transcript append failed: %s", type(exc).__name__)
            audit("chat_message", customer=ctx.principal.customer, user=ctx.principal.user,
                  conversation_id=conversation_id, message_id=message_id, parts=len(parts), queries=usage["queries"],
                  elapsed_ms=elapsed, finish=finish, scenario=scenario, steps=len(persisted_steps))
            run.done = True
            run.finished_at = time.time()
            loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=work, name=f"chat-{message_id}", daemon=True).start()

        async def live():
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=settings.heartbeat_seconds)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        run.cancelled.set()
                        return
                    yield _sse(None, {"type": "heartbeat", "at": _iso(time.time())})
                    continue
                if item is None:
                    return
                if await request.is_disconnected():
                    run.cancelled.set()
                    return
                seq, payload = item
                yield _sse(f"{message_id}:{seq}", payload)

        return StreamingResponse(live(), media_type="text/event-stream",
                                 headers={**NO_STORE, "X-Accel-Buffering": "no"})

    def _stream(run: _Run, after: int):
        async def replay():
            sent = after
            while True:
                pending = [(s, p) for s, p in list(run.events) if s > sent]
                for seq, payload in pending:
                    yield _sse(f"{run.message_id}:{seq}", payload)
                    sent = seq
                if run.done and sent >= len(run.events) - 1:
                    return
                await asyncio.sleep(0.05)
        return StreamingResponse(replay(), media_type="text/event-stream", headers=NO_STORE)

    @app.post("/api/chat/{message_id}/stop")
    def stop(message_id: str, request: Request):
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return unauthorized()
        run = runs.get(message_id)
        if run is None or run.principal != ctx.principal:
            return JSONResponse({"stopped": False}, 404, headers=NO_STORE)
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
            **NO_STORE, "Content-Disposition": f'attachment; filename="{item.name}"'})

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
        return JSONResponse({"deleted": store.delete_all(ctx.principal)}, headers=NO_STORE)

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

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str, request: Request):
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return unauthorized()
        ok = store.delete_conversation(ctx.principal, conversation_id)
        return JSONResponse({"deleted": ok}, 200 if ok else 404, headers=NO_STORE)

    return app


def _error(code: str, title: str, message: str, retryable: bool) -> dict:
    return {"type": "error", "id": new_id("p_"), "code": code, "title": title, "message": message,
            "retryable": retryable}


def _chunks(text: str, size: int = 24):
    words = text.split(" ")
    buf = ""
    for word in words:
        piece = word if not buf else " " + word
        if len(buf) + len(piece) > size and buf:
            yield buf
            buf = " " + word
        else:
            buf += piece
    if buf:
        yield buf


def _sse(event_id: str | None, payload: dict) -> str:
    head = f"id: {event_id}\n" if event_id else ""
    return f"{head}data: {json.dumps(payload, default=str, separators=(',', ':'))}\n\n"


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


app = create_app()
