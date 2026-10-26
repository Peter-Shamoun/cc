"""Stream conformance runner (contract 1.12.0). It interprets ``stream_rules.json``, the single source of the §2
ordering rules shared with ``web/src/contract/conformance.ts``.

    from api.contract.conformance import check
    violations = check(events)    # [] when the stream conforms

It is pure and total: malformed input (non-dicts, missing fields, wrong types) is reported as violations and never
raises.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

RULES_PATH = Path(__file__).with_name("stream_rules.json")
RULES: dict = json.loads(RULES_PATH.read_text())


@dataclass(frozen=True)
class Violation:
    rule: str
    index: int  # event index (-1 when the rule concerns the whole stream)
    message: str


def get(ev, path: str):
    cur = ev
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def matches(t, pattern: str) -> bool:
    return isinstance(t, str) and (t.startswith(pattern[:-1]) if pattern.endswith(".*") else t == pattern)


def _has(container, k) -> bool:
    """``k in container`` that never raises: an unhashable key (a JSON object or array) is never present, the way an
    object key never matches in the TS runner's Map/Set."""
    try:
        return k in container
    except TypeError:
        return False


def _any(t, patterns) -> bool:
    return any(matches(t, p) for p in patterns)


def check(events, rules: dict | None = None) -> list[Violation]:
    rules = rules or RULES
    if not isinstance(events, list):
        return [Violation("stream.list", -1, "the stream is not a list of events")]
    out: list[Violation] = []
    evs: list[tuple[int, dict]] = []
    for i, e in enumerate(events):
        if not isinstance(e, dict) or not isinstance(e.get("type"), str):
            out.append(Violation("event.shape", i, "an event must be an object with a string `type`"))
        elif e["type"] != "heartbeat":
            evs.append((i, e))
    end = next((e for _, e in reversed(evs) if e["type"] == "message.end"), None)
    finish = get(end, "finishReason") if end else None
    for r in rules["rules"]:
        out.extend(_RUNNERS[r["kind"]](r, evs, finish))
    return sorted(out, key=lambda v: (v.index, v.rule))


def _first(r, evs, _):
    if not evs or evs[0][1]["type"] != r["type"]:
        return [Violation(r["id"], evs[0][0] if evs else -1, f"first event must be {r['type']}")]
    return []


def _last(r, evs, _):
    if not evs or evs[-1][1]["type"] != r["type"]:
        return [Violation(r["id"], evs[-1][0] if evs else -1, f"last event must be {r['type']}")]
    return []


def _count(r, evs, _):
    hits = [i for i, e in evs if matches(e["type"], r["type"])]
    if not (r.get("min", 0) <= len(hits) <= r.get("max", len(hits))):
        return [Violation(r["id"], hits[r["max"]] if len(hits) > r.get("max", 10**9) else -1,
                          f"{r['type']} appears {len(hits)} times")]
    return []


def _seq(r, evs, _):
    want = r.get("start", 0)
    for i, e in evs:
        s = e.get("seq")
        if not (_int(s) or (isinstance(s, int) and not isinstance(s, bool))) or s != want:
            return [Violation(r["id"], i, f"seq {s!r}, expected {want}")]
        want += 1
    return []


def _same(r, evs, _):
    vals = [(i, get(e, r["field"])) for i, e in evs if _any(e["type"], r["types"])]
    if not vals:
        return []
    first = vals[0][1]
    return [Violation(r["id"], i, f"{r['field']} differs") for i, v in vals[1:] if v != first][:1]


def _enum(r, evs, _):
    return [Violation(r["id"], i, f"{r['field']} {get(e, r['field'])!r} is not one of {r['values']}")
            for i, e in evs if matches(e["type"], r["type"]) and get(e, r["field"]) not in r["values"]]


def _unique(r, evs, _):
    seen, out = set(), []
    for i, e in evs:
        if _any(e["type"], r["types"]):
            v = get(e, r["field"])
            if not isinstance(v, str):
                out.append(Violation(r["id"], i, f"{r['field']} missing"))
            elif v in seen:
                out.append(Violation(r["id"], i, f"{r['field']} {v} repeats"))
            seen.add(v)
    return out


def _ref(r, evs, _):
    known, out = set(), []
    for i, e in evs:
        if matches(e["type"], r["type"]):
            v = get(e, r["field"])
            if v is not None and not _has(known, v):
                out.append(Violation(r["id"], i, f"{r['field']} {v!r} does not name an earlier {r['target']}"))
        if matches(e["type"], r["target"]) and all(get(e, k) == want for k, want in r.get("where", {}).items()):
            t = get(e, r["targetField"])
            if isinstance(t, str):
                known.add(t)
    return out


def _span(r, evs, finish):
    state: dict[str, str] = {}  # key -> "open" | "closed"
    out = []
    for i, e in evs:
        t = e["type"]
        if t == r["open"]:
            k = get(e, r["key"])
            if not isinstance(k, str) or _has(state, k):
                out.append(Violation(r["id"], i, f"{t} {k!r} opened twice or without a key"))
            else:
                state[k] = "open"
        elif t in r["inner"]:
            k = get(e, r["key"])
            if not _has(state, k) or state[k] != "open":
                out.append(Violation(r["id"], i, f"{t} {k!r} outside its {r['open']} … {r['close'][0]}"))
        elif t in r["close"]:
            k = get(e, r["closeKey"])
            if _has(state, k):
                if state[k] != "open":
                    out.append(Violation(r["id"], i, f"{t} {k!r} closed twice"))
                state[k] = "closed"
            elif not r.get("closeWithoutOpenOk"):  # e.g. a complete `part` needs no table.start
                out.append(Violation(r["id"], i, f"{t} {k!r} without {r['open']}"))
    if finish not in r.get("unclosedOk", []):
        out += [Violation(r["id"], -1, f"{r['open']} {k} never closed") for k, s in state.items() if s == "open"]
    return out


def _none_after(r, evs, _):
    exc = r.get("anchorExcept", {})
    started = False
    out = []
    for i, e in evs:
        if not started and e["type"] in r["anchor"] and not any(get(e, p) in vs for p, vs in exc.items()):
            started = True
            continue
        if started and _any(e["type"], r["forbidden"]) and not _any(e["type"], r.get("allowed", [])):
            out.append(Violation(r["id"], i, f"{e['type']} after the answer began"))
    return out


def _finish_requires(r, evs, finish):
    if finish != r["finishReason"]:
        return []
    if any(e["type"] == "part" and get(e, "part.type") == r["partType"] for _, e in evs):
        return []
    return [Violation(r["id"], -1, f"finishReason {finish} without a {r['partType']} part")]


def _rows_seen(r, evs):
    """Per key: the rows streamed so far, by offset (a replay overwrites)."""
    acc: dict = {}
    for i, e in evs:
        if e["type"] == r["inner"]:
            k, off, rows = get(e, r["key"]), get(e, "offset"), get(e, r["rowsField"])
            if isinstance(k, str) and _int(off) and isinstance(rows, list):
                buf = acc.setdefault(k, [])
                for j, row in enumerate(rows):
                    if off + j < len(buf):
                        buf[off + j] = row
                    elif off + j == len(buf):
                        buf.append(row)
        yield i, e, acc


def _int(v) -> bool:
    """A non-negative integer the way JSON (and the TS runner's ``Number.isInteger``) sees it: ``3.0`` counts."""
    return (isinstance(v, int) and not isinstance(v, bool) and v >= 0) or (
        isinstance(v, float) and v.is_integer() and v >= 0)


def _contiguous(r, evs, _):
    count: dict = {}
    out = []
    for i, e in evs:
        if e["type"] not in r["types"]:
            continue
        k, off, rows = get(e, r["key"]), get(e, r["offsetField"]), get(e, r["rowsField"])
        if not _int(off) or not isinstance(rows, list):
            out.append(Violation(r["id"], i, f"{e['type']} needs an integer {r['offsetField']} ≥ 0 and a list"))
            continue
        key = (e["type"], json.dumps(k, ensure_ascii=False, default=str))  # TS: JSON.stringify
        have = count.get(key, 0)
        if off > have:
            out.append(Violation(r["id"], i, f"{e['type']} {k!r} offset {off} skips rows (have {have})"))
            continue
        count[key] = max(have, off + len(rows))
    return out


def _prefix(r, evs, _):
    out = []
    for i, e, acc in _rows_seen(r, evs):
        if e["type"] == r["close"]:
            k = get(e, r["closeKey"])
            if isinstance(k, str) and acc.get(k):
                final = get(e, r["closeRowsField"])
                got = acc[k]
                if not isinstance(final, list) or final[: len(got)] != got:
                    out.append(Violation(r["id"], i, f"streamed {r['inner']} for {k!r} differ from the final part"))
    return out


def _matches(r, evs, _):
    opened: dict = {}
    out = []
    for i, e in evs:
        if e["type"] == r["open"]:
            if isinstance(get(e, r["key"]), str):
                opened[get(e, r["key"])] = get(e, r["field"])
        elif e["type"] == r["close"]:
            k = get(e, r["closeKey"])
            if isinstance(k, str) and k in opened:
                final = get(e, r["closeField"])
                cut = {a: b for a, b in final.items() if a not in r.get("omit", [])} if isinstance(final, dict) else final
                if cut != opened[k]:
                    out.append(Violation(r["id"], i, f"{r['closeField']} of {k!r} differs from its {r['open']}"))
    return out


def _plot(r, evs, _):
    from api.plot import check_plot
    out = []
    for i, e in evs:
        if not matches(e["type"], r["type"]) or not all(get(e, p) == v for p, v in r.get("where", {}).items()):
            continue
        cur = e
        present = True
        for k in r["field"].split("."):
            if not isinstance(cur, dict) or k not in cur:
                present = False
                break
            cur = cur[k]
        if not present:
            if not r.get("optional"):
                out.append(Violation(r["id"], i, f"{r['field']} missing"))
            continue
        bad = check_plot(cur, header=bool(r.get("header")))
        if bad:
            out.append(Violation(r["id"], i, f"{r['field']} invalid: {', '.join(bad)}"))
    return out


_RUNNERS = {"first": _first, "last": _last, "count": _count, "seq": _seq, "same": _same, "enum": _enum,
            "unique": _unique, "ref": _ref, "span": _span, "none_after": _none_after,
            "finish_requires": _finish_requires, "contiguous": _contiguous, "prefix": _prefix, "matches": _matches,
            "plot": _plot}
