"""Prefetch schema, values, and train-only examples, then ask a pluggable model for SQL.

Retrieval runs in code. The model sees the result and returns one SELECT, which is
executed through the guarded tool (``tools.sql``). The value index is built on the
session connection, which is the connection ``ccsim.query.open_session`` or
``bench.dataset.open_snapshot`` already scoped to one tenant.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bench.agents import Answer

from analyst.variants.context_retrieval.fewshot import load_train_library, retrieve_examples
from analyst.variants.context_retrieval.llm import LLMRequest, ScriptedLLM
from analyst.variants.context_retrieval.prompts import (
    TOOL_SPEC,
    render_subagent_briefing,
    render_system,
    render_user,
)
from analyst.variants.context_retrieval.schema_link import link_schema
from analyst.variants.context_retrieval.screen import RetrievalConfig
from analyst.variants.context_retrieval.value_index import ValueIndex, resolve_question

_OTHER_CUSTOMER = re.compile(
    r"\bacme diagnostics\b|\bother customer\b|\banother (customer|tenant|lab)\b",
    re.IGNORECASE,
)
_PHI = re.compile(
    r"\b(dates? of birth|member ids?|social security|patient names?)\b",
    re.IGNORECASE,
)
_WRITE = re.compile(r"\b(delete|drop table|update|insert|truncate)\b", re.IGNORECASE)

_KIND_VIEWS = {
    "payer_name": {"sem.payers", "sem.remit_claims", "sem.claims"},
    "procedure_code": {"sem.claim_lines", "sem.remit_lines"},
    "carc": {"sem.adjustments"},
    "rarc": {"sem.remarks"},
    "diagnosis_code": {"sem.diagnoses", "sem.claims"},
}


def request_refusal(question: str) -> str | None:
    """Deterministic tenant / PHI / write refusal. Independent of the model."""
    if _PHI.search(question):
        return "that asks for patient identifiers, which this session cannot return"
    if _OTHER_CUSTOMER.search(question):
        return "that asks about a different customer than the logged-in tenant"
    if _WRITE.search(question):
        return "the data is read-only; this session cannot change it"
    return None


@dataclass
class RetrievalPacket:
    question: str
    question_id: str | None
    config: RetrievalConfig
    system: str
    user: str
    briefing: str
    examples: list[dict]
    resolved: dict | None
    dropped_views: list[str]
    fallback_full: bool
    link: object
    refusal: str | None = None
    approx_system_tokens: int = 0
    approx_user_tokens: int = 0
    trace: list = field(default_factory=list)


def _mask_literals(question: str, resolved: dict | None) -> list[str]:
    literals = []
    if not resolved:
        return literals
    for item in resolved["bound"] + resolved["ambiguous"] + resolved["unmatched"]:
        literals.append(str(item.get("query") or ""))
        for match in item.get("matches") or []:
            literals.append(str(match["value"]))
    return [literal for literal in literals if literal]


def prepare_context(connection, question: str, *, config: RetrievalConfig, tenant_id: str,
                    library: list[dict] | None = None, question_id: str | None = None,
                    family: str | None = None, value_index: ValueIndex | None = None) -> RetrievalPacket:
    refusal = request_refusal(question) if config.hard_refuse else None
    index = value_index
    resolved = None
    if config.value_index and refusal is None:
        index = index or ValueIndex.build(connection, tenant_id)
        resolved = resolve_question(index, tenant_id, question)
    force_views: set[str] = set()
    if resolved:
        for item in resolved["bound"]:
            force_views |= _KIND_VIEWS.get(item["kind"], set())
    link = link_schema(
        connection, question, mode=config.schema_mode, recall_floor_views=config.recall_floor_views,
        force_views=force_views,
    )
    examples = []
    if refusal is None and config.fewshot_k:
        pool = library if library is not None else load_train_library()
        examples = retrieve_examples(
            pool, question, question_id=question_id, family=family,
            k=min(config.fewshot_k, config.max_examples_in_prompt),
            selector=config.fewshot_selector,
            near_duplicate_jaccard=config.near_duplicate_jaccard,
            exclude_same_family=config.exclude_same_family,
            mask_literals=_mask_literals(question, resolved),
        )
    schema_in_system = config.schema_mode == "full"
    system = render_system(link, glossary=config.glossary, context_style=config.context_style,
                           schema_in_system=schema_in_system)
    user = render_user(question, link, resolved, examples, value_index=config.value_index,
                       schema_in_system=schema_in_system)
    return RetrievalPacket(
        question=question, question_id=question_id, config=config, system=system, user=user,
        briefing=render_subagent_briefing(system=system, user=user), examples=examples, resolved=resolved,
        dropped_views=list(link.dropped_views), fallback_full=link.fallback_full, link=link, refusal=refusal,
        approx_system_tokens=len(system) // 4, approx_user_tokens=len(user) // 4,
        trace=[{"dropped_views": list(link.dropped_views), "fallback_full": link.fallback_full,
                "example_ids": [example["id"] for example in examples]}],
    )


def dispatch_tool(connection, tenant_id: str, library: list[dict], name: str, arguments: dict,
                  value_index: ValueIndex | None = None, config: RetrievalConfig | None = None) -> dict:
    """The tool surface named by ``TOOL_SPEC``. Tenant id comes from the session, not the model."""
    chosen = config or RetrievalConfig()
    if name == "link_schema":
        link = link_schema(
            connection, arguments["question"], mode=arguments.get("mode", chosen.schema_mode),
            recall_floor_views=chosen.recall_floor_views,
        )
        return {"views": [view.name for view in link.views], "dropped_views": link.dropped_views,
                "fallback_full": link.fallback_full}
    if name == "lookup_values":
        index = value_index or ValueIndex.build(connection, tenant_id)
        return index.lookup(tenant_id, arguments["kind"], arguments["text"])
    if name == "similar_examples":
        return {"examples": retrieve_examples(
            library, arguments["question"], k=int(arguments.get("k", chosen.fewshot_k or 3)),
            selector=arguments.get("selector", chosen.fewshot_selector),
            near_duplicate_jaccard=chosen.near_duplicate_jaccard,
            exclude_same_family=chosen.exclude_same_family,
        )}
    raise ValueError(f"unknown tool {name!r}; known retrieval tools are link_schema, lookup_values, similar_examples")


class ContextRetrievalAgent:
    """``bench.eval`` agent. Defaults to a scripted model so an unconfigured run cannot call a paid API."""

    def __init__(self, config: RetrievalConfig | None = None, llm=None, tenant_id: str = "acme_labs",
                 library: list[dict] | None = None, value_index: ValueIndex | None = None):
        self.config = config or RetrievalConfig(value_index=True, fewshot_k=3, fewshot_selector="skeleton",
                                                hard_refuse=True)
        self.llm = llm if llm is not None else ScriptedLLM({})
        self.tenant_id = tenant_id
        self.library = library
        self.value_index = value_index

    def answer(self, question, tools) -> Answer:
        packet = prepare_context(
            tools._con, question, config=self.config, tenant_id=self.tenant_id, library=self.library,
            value_index=self.value_index,
        )
        if packet.refusal:
            return Answer(refused=True, text=packet.refusal, trace=packet.trace)
        response = self.llm.complete(LLMRequest(
            question_id=packet.question_id, question=question, system=packet.system, user=packet.user,
            tools=TOOL_SPEC,
        ))
        if response.refused or not response.sql:
            return Answer(refused=response.refused, text=response.text, usage=response.usage, trace=packet.trace)
        _columns, rows = tools.sql(response.sql, _final=True)
        return Answer(sql=response.sql, rows=rows, text=response.text, usage=response.usage, trace=packet.trace)
