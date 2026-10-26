"""Security headers on every api/ response (CONTRACT.md §10).

Pure ASGI middleware (not BaseHTTPMiddleware) so SSE streams are never buffered. Headers a route
sets itself win, so the CSV download keeps its Content-Disposition and route-specific values.
The API only serves JSON, SSE and CSV, so the CSP denies everything: a response opened directly
in a browser can't run script, load anything, or be framed.
"""
from __future__ import annotations

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": API_CSP,
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}
_ENCODED = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in SECURITY_HEADERS.items()]


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                headers.extend((k, v) for k, v in _ENCODED if k not in present)
                headers = [(k, v) for k, v in headers if k.lower() != b"server"]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
