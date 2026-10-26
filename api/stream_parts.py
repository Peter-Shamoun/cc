"""Turn finished message parts into contract stream events (CONTRACT.md §4, §5.1).

The live server (``api.app``) and the analyzer adapter (``api.analyzer_adapter``) share this code, so a real
analyzer run and the demo stream the same way: text as ``text.start``/``text.delta``/``text.end``; a table with
rows as ``table.start`` + ``table.rows`` chunks, then the complete ``part``; a chart with a ``plot`` (1.18.0) as
``chart.start`` (the PlotHeader) + ``chart.points`` chunks, then the complete ``part``; and every other part as one
``part``. Charts reach here already validated (``api.plot.gate_parts``).
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Iterable

TABLE_CHUNK_ROWS = 10
#: points per ``chart.points`` event (contract 1.18.0, PlotSpec ``x-caps.chunkPoints``)
CHART_CHUNK_POINTS = 50


def text_chunks(text: str, size: int = 24) -> Iterable[str]:
    """Word-boundary chunks of about ``size`` characters; joining them gives back ``text`` exactly."""
    buf = ""
    for word in text.split(" "):
        piece = word if not buf else " " + word
        if len(buf) + len(piece) > size and buf:
            yield buf
            buf = " " + word
        else:
            buf += piece
    if buf:
        yield buf


def emit_parts(parts: list[dict], emit: Callable[[dict], None], *, cancelled: threading.Event | None = None,
               delay: float = 0.0, on_part: Callable[[dict], None] | None = None) -> bool:
    """Stream ``parts`` through ``emit``. Returns False if ``cancelled`` was set part-way (finish is "aborted").

    ``on_part`` is called after each non-text ``part`` event (the app registers clarifications there)."""
    stop = cancelled or threading.Event()
    for part in parts:
        if stop.is_set():
            return False
        if part["type"] == "text":
            emit({"type": "text.start", "partId": part["id"]})
            for chunk in text_chunks(part["text"]):
                if stop.is_set():
                    break
                emit({"type": "text.delta", "partId": part["id"], "delta": chunk})
                if delay:
                    time.sleep(delay)
            emit({"type": "text.end", "partId": part["id"]})
            continue
        if part["type"] == "table" and part.get("rows"):
            start = {"type": "table.start", "partId": part["id"], "columns": part["columns"]}
            for key in ("title", "sensitivity", "stepId", "sqlPartId"):
                if part.get(key) is not None:
                    start[key] = part[key]
            emit(start)
            rows = part["rows"]
            for offset in range(0, len(rows), TABLE_CHUNK_ROWS):
                if stop.is_set():
                    break
                emit({"type": "table.rows", "partId": part["id"], "offset": offset,
                      "rows": rows[offset:offset + TABLE_CHUNK_ROWS]})
                if delay:
                    time.sleep(delay)
        if part["type"] == "chart" and isinstance(part.get("plot"), dict) and part["plot"].get("data"):
            plot = part["plot"]
            start = {"type": "chart.start", "partId": part["id"], "plot": {k: v for k, v in plot.items() if k != "data"}}
            for key in ("alt", "tablePartId", "sensitivity"):
                if part.get(key) is not None:
                    start[key] = part[key]
            emit(start)
            points = plot["data"]
            for offset in range(0, len(points), CHART_CHUNK_POINTS):
                if stop.is_set():
                    break
                emit({"type": "chart.points", "partId": part["id"], "offset": offset,
                      "rows": points[offset:offset + CHART_CHUNK_POINTS]})
                if delay:
                    time.sleep(delay)
        emit({"type": "part", "part": part})
        if on_part is not None:
            on_part(part)
    return not stop.is_set()
