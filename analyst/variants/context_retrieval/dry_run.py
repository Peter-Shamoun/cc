"""Qualitative dry run: build a retrieval packet and, optionally, score schema recall.

The packet printer does not include the current question's gold SQL. Recall is a
separate audit so a missed view is visible even when the person writing SQL has
seen the answer key.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from analyst.variants.context_retrieval.fewshot import load_train_library
from analyst.variants.context_retrieval.schema_link import link_schema, recall_against_sql
from analyst.variants.context_retrieval.screen import RetrievalConfig, SCREEN_ARMS, config_from_flags
from analyst.variants.context_retrieval.session import prepare_context
from analyst.variants.context_retrieval.value_index import ValueIndex
from bench.dataset import open_snapshot


def _handwritten() -> list[dict]:
    return yaml.safe_load((Path(__file__).resolve().parents[3] / "bench" / "questions.yaml").read_text())


def audit(connection, questions: list[dict], tenant_id: str, library: list[dict],
          value_index: ValueIndex | None = None) -> list[dict]:
    rows = []
    index = value_index
    catalog_columns = {
        column.name
        for view in link_schema(connection, "catalog", mode="full").views
        for column in view.columns
    }
    for question in questions:
        if question.get("expect_refusal") or not question.get("gold_sql"):
            packet = prepare_context(
                connection, question["question"], config=config_from_flags(SCREEN_ARMS["cr_value"]),
                tenant_id=tenant_id, library=library, question_id=question["id"], value_index=index,
            )
            rows.append({"id": question["id"], "refusal": packet.refusal, "examples": [e["id"] for e in packet.examples]})
            continue
        per_mode = {}
        configs = {
            "cr_value": config_from_flags(SCREEN_ARMS["cr_value"]),
            "cr_schema_recall": config_from_flags(SCREEN_ARMS["cr_schema_recall"]),
            "cr_schema_columns": RetrievalConfig(schema_mode="linked_columns"),
            "cr_fewshot_skeleton": config_from_flags(SCREEN_ARMS["cr_fewshot_skeleton"]),
        }
        for arm_name, config in configs.items():
            packet = prepare_context(
                connection, question["question"], config=config, tenant_id=tenant_id, library=library,
                question_id=question["id"], family=question.get("family"), value_index=index,
            )
            if index is None and packet.resolved is not None:
                index = value_index
            per_mode[arm_name] = {
                "recall": recall_against_sql(packet.link, question["gold_sql"], catalog_columns),
                "example_ids": [example["id"] for example in packet.examples],
                "bound": [
                    {"kind": item["kind"], "query": item["query"], "value": item["matches"][0]["value"]}
                    for item in (packet.resolved or {}).get("bound", [])
                ],
                "ambiguous": [item["query"] for item in (packet.resolved or {}).get("ambiguous", [])],
                "system_tokens": packet.approx_system_tokens,
                "user_tokens": packet.approx_user_tokens,
                "fallback_full": packet.fallback_full,
            }
        rows.append({"id": question["id"], "modes": per_mode})
    return rows


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Context-retrieval dry run against a snapshot")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--tenant", default="acme_labs")
    parser.add_argument("--question-id", action="append", dest="question_ids")
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args(argv)
    connection = open_snapshot(Path(args.snapshot))
    library = load_train_library()
    index = ValueIndex.build(connection, args.tenant)
    wanted = set(args.question_ids or [])
    questions = [question for question in _handwritten() if not wanted or question["id"] in wanted]
    if args.audit:
        print(json.dumps(audit(connection, questions, args.tenant, library, index), indent=1))
        return
    config = config_from_flags(SCREEN_ARMS["cr_all"])
    for question in questions:
        packet = prepare_context(
            connection, question["question"], config=config, tenant_id=args.tenant, library=library,
            question_id=question["id"], value_index=index,
        )
        print(json.dumps({
            "id": question["id"],
            "refusal": packet.refusal,
            "dropped_views": packet.dropped_views,
            "fallback_full": packet.fallback_full,
            "examples": [{"id": example["id"], "score": example["score"]} for example in packet.examples],
            "bound": [
                {"kind": item["kind"], "value": item["matches"][0]["value"]}
                for item in (packet.resolved or {}).get("bound", [])
            ],
            "system_tokens": packet.approx_system_tokens,
            "user_tokens": packet.approx_user_tokens,
        }, indent=1))


if __name__ == "__main__":
    main()
