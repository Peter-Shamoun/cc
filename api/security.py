"""Security headers on every API response (JSON, SSE, CSV, errors, 404s).

Pure ASGI middleware, so streaming (SSE) responses are not buffered. The API never serves HTML, so its
CSP is deny-all. These values always win over a route's own value for the same
header (so no route can weaken no-store); the CSV route's Content-Disposition is untouched. Keep PERMISSIONS_POLICY in step with web/src/security/headers.ts.
"""
from __future__ import annotations

import logging

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; sandbox"

PERMISSIONS_POLICY = ", ".join([
    "accelerometer=()", "autoplay=()", "camera=()", "display-capture=()", "encrypted-media=()",
    "geolocation=()", "gyroscope=()", "magnetometer=()", "microphone=()", "midi=()", "payment=()",
    "publickey-credentials-get=()", "screen-wake-lock=()", "usb=()", "xr-spatial-tracking=()",
    "clipboard-write=(self)", "fullscreen=(self)",
])

SECURITY_HEADERS: dict[str, str] = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Content-Security-Policy": API_CSP,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": PERMISSIONS_POLICY,
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}

_ENCODED = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in SECURITY_HEADERS.items()]
_NAMES = {k for k, _ in _ENCODED}


class SecurityHeadersMiddleware:
    """Sets SECURITY_HEADERS on every HTTP response, replacing any value a route set for the same name."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() not in _NAMES]
                headers = [(k, v) for k, v in headers if k.lower() != b"server"]
                message = {**message, "headers": headers + _ENCODED}
            await send(message)

        await self.app(scope, receive, send_with_headers)


#: Request bodies are small JSON (chat turns carry history as text, <= 8000 chars per message). 1 MiB is far above any
#: legitimate body and stops a client from making the API buffer and parse megabytes of JSON.
MAX_BODY_BYTES = 1 << 20
_TOO_LARGE = b'{"error":{"code":"validation","message":"Request body is too large."}}'


class BodyLimitMiddleware:
    """Rejects request bodies over ``max_bytes`` with 413 before the route parses them (pure ASGI).

    A declared Content-Length over the limit is rejected without reading. Otherwise (chunked bodies) the body is read
    up to the limit and replayed to the app. Bodies here are small JSON, so buffering is cheap. GET/HEAD/OPTIONS (incl.
    SSE) pass straight through.
    """

    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def _reject(self, send):
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(_TOO_LARGE)).encode())]})
        await send({"type": "http.response.body", "body": _TOO_LARGE})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = self.max_bytes + 1
                if declared > self.max_bytes:
                    await self._reject(send)
                    return
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break  # client went away; let the app see the disconnect
            body = message.get("body", b"")
            size += len(body)
            if size > self.max_bytes:
                await self._reject(send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        if message["type"] != "http.request":
            await self.app(scope, receive, send)
            return
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class ErrorShieldMiddleware:
    """Unhandled exceptions become the contract's 500 envelope, ``{"error": {"code": "internal", ...}}``. Only the
    exception type and code location are logged, never ``str(exc)``, which can carry user input. The exception is
    not re-raised, so Starlette/uvicorn never print a traceback with the message. If the response has already
    started (a stream), the connection is just closed."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def send_wrapper(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # noqa: BLE001 - this is the last-resort boundary
            from api.redact import safe_exc
            route = scope.get("route")
            logging.getLogger("cc.api").error("unhandled %s on %s %s", safe_exc(exc), scope.get("method"),
                                              getattr(route, "path", "?"))
            if started:
                return
            body = b'{"error":{"code":"internal","message":"Something went wrong. Try again in a moment."}}'
            await send({"type": "http.response.start", "status": 500, "headers": [
                (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": body})
