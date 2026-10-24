"""LLM judge, used only when the deterministic matcher says "no" (as in OpenAI's in-house data-agent eval):
it sees the question, the business glossary, the reference SQL and result, and the candidate SQL and result.

Its verdicts are calibrated with `python -m bench.eval calibrate`: the judge is run on every alt_sql (must
accept) and wrong_sql (must reject) in questions.yaml, and the agreement rate is reported next to every score.
"""
from __future__ import annotations

import json
import os

from .agents import GLOSSARY

MODEL = os.environ.get("CC_JUDGE_MODEL", "claude-opus-5-5")
PROMPT = """You grade answers from a data-analysis agent. Decide whether the candidate result answers the
question as correctly as the reference result does.

Accept: extra columns, different column names or order, different row order (unless the question asks for a
ranking), rates as percentages, rounding differences within the stated precision, and a positive amount where a
credit was asked for as positive.
Reject: different numbers beyond rounding, a different population (wrong filter, wrong grain, wrong time window),
a different definition of a business term than the glossary's, missing or extra rows.

{glossary}
Question: {question}

Reference SQL:
{gold_sql}
Reference result ({gold_n} rows, first 25): {gold_rows}

Candidate SQL:
{cand_sql}
Candidate result ({cand_n} rows, first 25): {cand_rows}

Reply with JSON only: {{"correct": true or false, "error_type": "none|filter|grain|definition|join|aggregation|rounding|other", "reason": "one sentence"}}"""


def judge(question, gold_sql, gold_rows, cand_sql, cand_rows, model: str | None = None) -> dict:
    import anthropic
    msg = anthropic.Anthropic().messages.create(
        model=model or MODEL, max_tokens=300,
        messages=[{"role": "user", "content": PROMPT.format(
            glossary=GLOSSARY, question=question, gold_sql=gold_sql, gold_n=len(gold_rows), gold_rows=gold_rows[:25],
            cand_sql=cand_sql, cand_n=len(cand_rows), cand_rows=cand_rows[:25])}])
    text = "".join(b.text for b in msg.content if b.type == "text")
    try:
        out = json.loads(text[text.index("{"): text.rindex("}") + 1])
        out["correct"] = bool(out.get("correct"))
        return out
    except ValueError:
        return {"correct": False, "error_type": "other", "reason": "unparseable judge reply: " + text[:200]}
