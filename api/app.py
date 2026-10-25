"""FastAPI app: POST /api/chat (SSE), stop, downloads, health, conversations.

Run: ``uvicorn api.app:app --port 8787`` (demo mode) or ``CC_API_MODE=sim`` with a world
passed to ``create_app(auth=SimAuth(world.aws, world.dep))``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from analyzer.config import AnalyzerConfig
from analyzer.llm import PaidAPIDisabled
from analyzer.loop import Analyzer

from api.backends import AuthError, DemoAuth, make_llm
from api.ids import new_id
from api.mapping import answer_parts
from api.store import DownloadRegistry, InMemoryTranscriptStore, Principal

log = logging.getLogger("cc.api")
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


class ChatMessageIn(BaseModel):
    id: str | None = None
    role: str = "user"
    text: str = Field(default="", max_length=8000)


class ChatRequest(BaseModel):
    conversationId: str | None = None
    messages: list[ChatMessageIn] = Field(default_factory=list)
    options: dict = Field(default_factory=dict)


@dataclass
class Settings:
    llm: str = os.environ.get("CC_API_LLM", "scripted")
    heartbeat_seconds: float = float(os.environ.get("CC_API_HEARTBEAT_SECONDS", "15"))
    resume_seconds: float = 60.0
    stream_delay_seconds: float = 0.0
    analyzer_overrides: dict = field(default_factory=lambda: {
        "glossary": "basic", "data_dictionary": False, "data_profile": False, "row_limit": 5000,
        "candidates": 1, "cascade": "off"})


class AuditLog:
    """Structured audit events. Field allowlist: no SQL text, no row values, no question text."""

    ALLOWED = {"customer", "user", "file_id", "row_count", "conversation_id", "message_id", "parts",
               "queries", "elapsed_ms", "finish", "kind", "reason", "status"}

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
    store = store or InMemoryTranscriptStore(audit=audit)
    downloads = DownloadRegistry()
    runs: dict[str, _Run] = {}
    app = FastAPI(title="CC chat API", version="0.1.0", docs_url=None, redoc_url=None)
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
                return JSONResponse({"error": {"code": "validation", "message": "Nothing to resume."}}, 404,
                                    headers=NO_STORE)
            return _stream(run, after=int(seq) if seq.isdigit() else -1)
        users = [m for m in body.messages if m.role == "user" and m.text.strip()]
        if not users:
            return JSONResponse({"error": {"code": "validation", "message": "Send a question."}}, 422, headers=NO_STORE)
        message_id = new_id("m_")
        try:
            ctx = authed(request, message_id[-8:])
        except AuthError:
            return unauthorized()
        conversation_id = body.conversationId or new_id("c_")
        owner = getattr(store, "owner_of", lambda _cid: None)(conversation_id)
        if owner is not None and owner != ctx.principal:
            return JSONResponse({"error": {"code": "access_denied", "message": "Not found."}}, 404, headers=NO_STORE)
        question = users[-1].text.strip()
        user_msg = {"id": users[-1].id or new_id("m_"), "role": "user", "text": question,
                    "createdAt": _iso(time.time())}
        store.append_message(ctx.principal, conversation_id, user_msg)
        run = _Run(message_id, ctx.principal)
        runs[message_id] = run
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def emit(payload: dict):
            seq = len(run.events)
            run.events.append((seq, payload))
            loop.call_soon_threadsafe(queue.put_nowait, (seq, payload))

        def work():
            started = time.perf_counter()
            parts: list[dict] = []
            finish = "stop"
            queries = 0
            emit({"type": "message.start", "messageId": message_id, "conversationId": conversation_id,
                  "model": settings.llm, "strategy": body.options.get("strategy", "tool_loop")})
            emit({"type": "status", "label": "Analyzing"})
            try:
                config = AnalyzerConfig(**settings.analyzer_overrides)
                llm = llm_factory() if llm_factory else make_llm(settings.llm)
                analyzer = Analyzer(ctx.opener, llm, config)
                answer = analyzer.answer(question)
                queries = sum(1 for a in answer.audit if a.get("event") == "sql")
                elapsed = int((time.perf_counter() - started) * 1000)
                parts = answer_parts(answer, ctx.principal, downloads, elapsed_ms=elapsed)
            except PaidAPIDisabled:
                parts = [_error("internal", "Model unavailable", "Paid model APIs are disabled on this server.", False)]
                finish = "error"
            except Exception as exc:  # noqa: BLE001 - never echo internals or data to the client or log
                log.warning("chat failed: %s", type(exc).__name__)
                parts = [_error("internal", "Something went wrong", "The analysis failed. Try again.", True)]
                finish = "error"
            for part in parts:
                if run.cancelled.is_set():
                    finish = "aborted"
                    break
                if part["type"] == "text":
                    emit({"type": "text.start", "partId": part["id"]})
                    for chunk in _chunks(part["text"]):
                        emit({"type": "text.delta", "partId": part["id"], "delta": chunk})
                        if settings.stream_delay_seconds:
                            time.sleep(settings.stream_delay_seconds)
                    emit({"type": "text.end", "partId": part["id"]})
                else:
                    emit({"type": "part", "part": part})
            elapsed = int((time.perf_counter() - started) * 1000)
            emit({"type": "status", "label": None})
            emit({"type": "message.end", "messageId": message_id, "finishReason": finish,
                  "usage": {"queries": queries, "elapsedMs": elapsed}})
            kept = parts if finish != "aborted" else parts[:0]
            store.append_message(ctx.principal, conversation_id, {
                "id": message_id, "role": "assistant", "parts": kept, "createdAt": _iso(time.time()),
                "parentId": user_msg["id"]})
            audit("chat_message", customer=ctx.principal.customer, user=ctx.principal.user,
                  conversation_id=conversation_id, message_id=message_id, parts=len(parts), queries=queries,
                  elapsed_ms=elapsed, finish=finish)
            run.done = True
            run.finished_at = time.time()
            loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=work, name=f"chat-{message_id}", daemon=True).start()

        async def live():
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=settings.heartbeat_seconds)
                except asyncio.TimeoutError:
                    yield _sse(None, {"type": "heartbeat"})
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

    @app.get("/api/conversations")
    def conversations(request: Request, cursor: str | None = None):
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return unauthorized()
        return JSONResponse(store.list_conversations(ctx.principal, cursor), headers=NO_STORE)

    @app.get("/api/conversations/{conversation_id}")
    def conversation(conversation_id: str, request: Request):
        try:
            ctx = authed(request, new_id("r"))
        except AuthError:
            return unauthorized()
        conv = store.get_conversation(ctx.principal, conversation_id)
        if conv is None:
            return JSONResponse({"error": {"code": "access_denied", "message": "Not found."}}, 404, headers=NO_STORE)
        return JSONResponse(conv, headers=NO_STORE)

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
