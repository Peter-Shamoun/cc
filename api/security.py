"""Security headers on every API response (JSON, SSE, CSV, errors, 404s).

Pure ASGI middleware, so streaming (SSE) responses are not buffered. The API never serves HTML, so its
CSP is deny-all. These values always win over a route's own value for the same
header (so no route can weaken no-store); the CSV route's Content-Disposition is untouched. Keep PERMISSIONS_POLICY in step with web/src/security/headers.ts.
"""
from __future__ import annotations

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
