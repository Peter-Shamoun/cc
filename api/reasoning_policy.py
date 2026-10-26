"""Reasoning PHI policy for the real-analyzer adapter path (adapter spec R2).

A real model's free text (``Completion.text``) can quote anything it saw: patient names, MRNs, dates of birth, member
IDs, NPIs. ``LiveTap`` streams that text as ``reasoning.delta`` (candidate 0 only). In the demo it is scripted and
synthetic. Once a real model is wired in it is not, so the adapter never forwards it as-is.

``ReasoningPolicy`` wraps the ``emit`` callable and decides what reaches the stream:

* ``"drop"`` (the **default**): every ``reasoning.delta`` and ``reasoning.end`` is swallowed. The answer still
  streams; the UI shows no "Thinking" disclosure, which the contract allows (reasoning parts are optional).
* ``"summary"``: model text is still swallowed. When the tap closes the reasoning part, one ``reasoning.delta`` is
  emitted, built **only from labels and counts** of the step events that already went out (probe kinds,
  query/failure/repair counts, row counts, check verdicts, candidate count, escalation count), then the
  ``reasoning.end``. No model-authored string, SQL, error text or table name is ever copied into it. As a last guard,
  the summary goes through ``phi_findings`` and is dropped (fail closed) if anything matches.

There is deliberately no "raw" mode. Forwarding model text needs a PHI review and a reviewed contract change (spec R2).
The demo path (``api.app`` + ``ScriptedLLM``) does not use this module, and its streams and golden examples are unchanged.
"""
from __future__ import annotations

import re
from typing import Callable

POLICIES = ("drop", "summary")
DEFAULT_POLICY = "drop"

# -- PHI detector (guard + test oracle). Conservative: false positives only cost a dropped summary. ------------------
_NAME_LEAD = r"(?:patient|pt|member|subscriber|insured|guarantor|beneficiary|name|mr|mrs|ms|miss|dr)\.?"
PHI_PATTERNS: dict[str, re.Pattern] = {
    # "Patient Jane Synthetic", "Mr. Doe", "name: Jane Q Synthetic", and "Synthetic, Jane" (last, first)
    "name": re.compile(rf"(?i:\b{_NAME_LEAD})\s*[:#=-]?\s*[A-Z][a-z'’-]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z'’-]+)?"
                       r"|\b[A-Z][a-z'’-]{1,}\s*,\s*[A-Z][a-z'’-]{1,}\b"),
    "mrn": re.compile(r"(?i)\b(?:mrn|medical\s+record(?:\s+(?:number|no\.?|#))?)\s*[:#=]?\s*[A-Z0-9-]{4,}"),
    "dob": re.compile(r"(?i)\b(?:dob|d\.o\.b\.|date\s+of\s+birth|born)\b\s*[:#=]?\s*\S+"
                      r"|\b(?:19|20)\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])\b"
                      r"|\b(?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])[-/.](?:19|20)?\d{2}\b"),
    "member_id": re.compile(r"(?i)\b(?:member|subscriber|policy|insurance|beneficiary|mbi)\s*(?:id|identifier|no\.?|"
                            r"number|#)\s*[:#=]?\s*[A-Z0-9-]{5,}"
                            r"|\b(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{9,15}\b"),
    "npi": re.compile(r"(?i)\bnpi\s*[:#=]?\s*\d{10}\b|\b[12]\d{9}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "phone": re.compile(r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "long_number": re.compile(r"\b\d{6,}\b"),
}


def phi_findings(text: str) -> list[str]:
    """The PHI pattern labels found in ``text`` (empty list = nothing matched). Labels only, never the match."""
    return [label for label, pattern in PHI_PATTERNS.items() if pattern.search(str(text or ""))]


# -- summary built from labels and counts ---------------------------------------------------------------------------
_PROBE_LABEL = {"list_views": "listed the views", "describe_view": "described a view",
                "sample_values": "sampled values", "profile": "profiled a view",
                "lookup_dictionary": "checked the data dictionary", "lookup_glossary": "checked the glossary",
                "search_examples": "looked at similar questions", "request_escalation": "asked for escalation"}


def _times(n: int, one: str, many: str) -> str:
    return one if n == 1 else f"{n} {many}"


class _Counts:
    def __init__(self) -> None:
        self.probes: dict[str, int] = {}
        self.queries = 0
        self.failed = 0
        self.repairs = 0
        self.rows: int | None = None
        self.checks: dict[str, int] = {}
        self.candidates: set[str] = set()
        self.escalations = 0

    def see(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "step.explore":
            probe = ev.get("probe")
            if isinstance(probe, str) and probe in _PROBE_LABEL:   # only known labels, never free text
                self.probes[probe] = self.probes.get(probe, 0) + 1
        elif t == "step.sql_running":
            self.queries += 1
            cid = ev.get("candidateId")
            if isinstance(cid, str) and re.fullmatch(r"k\d{1,2}", cid):
                self.candidates.add(cid)
        elif t == "step.sql_result":
            if ev.get("ok") is False:
                self.failed += 1
            elif isinstance(ev.get("rowCount"), int) and not isinstance(ev.get("rowCount"), bool):
                self.rows = ev["rowCount"]
        elif t == "step.repair_attempt":
            self.repairs += 1
        elif t == "step.check":
            verdict = ev.get("verdict")
            if verdict in ("pass", "warn", "fail"):
                self.checks[verdict] = self.checks.get(verdict, 0) + 1
        elif t == "escalation":
            self.escalations += 1

    def summary(self) -> str:
        parts: list[str] = []
        if self.probes:
            probes = [_PROBE_LABEL[p] + ("" if n == 1 else f" ({n}×)") for p, n in self.probes.items()]
            parts.append("Explored the data: " + ", ".join(probes) + ".")
        if self.queries:
            q = f"Ran {_times(self.queries, 'one query', 'queries')}"
            extra = []
            if self.failed:
                extra.append(f"{self.failed} failed")
            if self.repairs:
                extra.append(f"{self.repairs} repaired")
            parts.append(q + (f" ({', '.join(extra)})" if extra else "") + ".")
        if len(self.candidates) > 1:
            parts.append(f"Compared {len(self.candidates)} candidate queries.")
        if self.rows is not None:
            parts.append(f"The last successful query returned {_times(self.rows, 'one row', 'rows')}.")
        if self.checks:
            parts.append("Checks: " + ", ".join(f"{n} {v}" for v, n in sorted(self.checks.items())) + ".")
        if self.escalations:
            parts.append(f"Escalated to a larger model {_times(self.escalations, 'once', 'times')}.")
        return " ".join(parts)


class ReasoningPolicy:
    """``emit`` wrapper that applies the reasoning policy. Every non-reasoning event passes through unchanged."""

    def __init__(self, emit: Callable[[dict], None], policy: str = DEFAULT_POLICY):
        if policy not in POLICIES:
            raise ValueError(f"unknown reasoning policy {policy!r}; expected one of {POLICIES}")
        self._emit = emit
        self.policy = policy
        self._counts = _Counts()
        self.dropped_deltas = 0        # how many model-text deltas were withheld (for metrics; never the text)

    def __call__(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "reasoning.delta":
            self.dropped_deltas += 1
            return
        if t == "reasoning.end":
            if self.policy == "summary":
                text = self._counts.summary()
                if text and not phi_findings(text):
                    self._emit({"type": "reasoning.delta", "partId": ev.get("partId"), "delta": text})
                    self._emit(ev)
            return
        self._counts.see(ev)
        self._emit(ev)
