"""``python -m api [--host H] [--port P]``: run the chat API with production-safe uvicorn flags.

uvicorn adds ``server: uvicorn`` below the ASGI app, so SecurityHeadersMiddleware cannot strip it; this launcher turns it
off (``server_header=False``). Demo mode by default (CC_API_MODE / CC_API_LLM as usual); no paid APIs unless configured.
Equivalent CLI: ``uvicorn api.app:app --no-server-header``.
"""
from __future__ import annotations

import argparse
import os

import uvicorn


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m api")
    ap.add_argument("--host", default=os.environ.get("CC_API_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("CC_API_PORT", "8787")))
    args = ap.parse_args(argv)
    uvicorn.run("api.app:app", host=args.host, port=args.port, server_header=False, proxy_headers=False,
                log_level="info", access_log=False)  # access log would carry request paths; keep it off


if __name__ == "__main__":
    main()
