"""Attach A10 examples to an eval_subagents manifest.

    python -m analyst.approaches.a10_dynamic_fewshot MANIFEST \\
        --questions pool.json --splits splits.json --target-splits validation
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from analyst.approaches.a10_dynamic_fewshot.adapter import CONFIG_NAME, attach_to_manifest
from analyst.approaches.a10_dynamic_fewshot.leakage import SplitAssignment
from analyst.approaches.a10_dynamic_fewshot.retriever import DEFAULT_K


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Attach A10 few-shot examples to an eval_subagents manifest.")
    parser.add_argument("manifest", help="path written by bench.eval_subagents prepare")
    parser.add_argument("--questions", required=True, help="JSON list of question objects, every split")
    parser.add_argument("--splits", required=True, help="JSON split assignment from the split manager")
    parser.add_argument("--method", choices=("tfidf", "bm25"), default="tfidf")
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--config-name", default=CONFIG_NAME)
    parser.add_argument(
        "--target-splits",
        default="train,validation,test",
        help="comma-separated splits that may be targets. Use 'validation' for a screen.",
    )
    args = parser.parse_args(argv)
    questions = json.loads(Path(args.questions).read_text())
    if not isinstance(questions, list):
        raise SystemExit("--questions must be a JSON list of question objects")
    report = attach_to_manifest(
        args.manifest,
        questions,
        SplitAssignment.from_json(args.splits),
        k=args.k,
        method=args.method,
        config_name=args.config_name,
        target_splits=tuple(part for part in args.target_splits.split(",") if part),
    )
    json.dump(report, sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
