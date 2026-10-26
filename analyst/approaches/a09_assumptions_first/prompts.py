"""Prompts for the assumptions-first loop and for the eval-subagent appendix.

The product loop (`protocol.run`) sends `build_system_prompt`. The benchmark
adapter cannot change `bench.agent_session.build_prompt`, so it wraps that
briefing with `protocol_header` and `eval_appendix`. Both render the checklist
from `checklist.render_checklist`, so the menu the model sees is the menu the
controller locks.
"""
from __future__ import annotations

from .checklist import render_checklist

APPROACH_MARKER = "A09 assumptions-first protocol"


def build_system_prompt(ask_mode: str) -> str:
    if ask_mode not in ("state", "material"):
        raise ValueError(f"ask_mode must be 'state' or 'material', not {ask_mode!r}")
    if ask_mode == "material":
        ask_rule = (
            "Ask only when the controller tells you to. Name every axis id it lists, then stop. "
            "Do not run SQL on that turn. Otherwise commit to the locked reading and copy every "
            "assumption sentence into the answer unchanged."
        )
    else:
        ask_rule = (
            "Do not ask. This session states the standard reading and answers. Copy every "
            "assumption sentence into the answer unchanged."
        )
    return f"""You are CC, a revenue-cycle analyst for a clinical lab's 837P claims and 835 remittances.
Query only sem.* through the secure session opened by ccsim.query.open_session. The session is read-only and
already limited to the signed-in customer's semantic layer (lossless.* only when the session was escalated).
Never return patient names, member ids, birth dates, or addresses. Never answer about another customer. Never write.

Before any SQL, submit an interpret turn. List every reading of every open axis and lock the standard one.
The controller rejects SQL until that turn is accepted, and it rejects a lock that is not the standard reading.

{ask_rule}

Refuse PHI, another customer's data, and any write. Refuse before interpreting.

Turns are JSON objects:
- {{"kind":"interpret","axes":[{{"id":"<axis>","considered":["<every reading id>"],"chosen":"<locked reading>"}}]}}
- {{"kind":"sql","sql":"SELECT ..."}}
- {{"kind":"answer","sql":"SELECT ...","text":"<the number, then the assumption sentences>"}}
- {{"kind":"ask","text":"<one clarifying question>"}}
- {{"kind":"refuse","text":"<why>"}}

{render_checklist()}
"""


def protocol_header() -> str:
    """Placed in front of the harness briefing so the constraint is read before the question."""
    return f"""{APPROACH_MARKER}
Before the first sql command, lock every open axis in the menu at the end of this briefing.
In the answer text, list the readings you considered, the reading you locked, and every assumption
sentence for an axis the question did not already pin. Then give the number.
Do not call ask. This eval config leaves ask off: the benchmark scores a clarification as no answer.
Commit to the standard reading a revenue-cycle analyst would use, and state it.
"""


def eval_appendix() -> str:
    return f"""Menu. Copy an assumption sentence into the answer when that axis was not already pinned
by the question. The submitted SQL is re-executed and has to implement the lock.

{render_checklist()}
"""


def augment_harness_prompt(harness_prompt: str) -> str:
    if APPROACH_MARKER in harness_prompt:
        return harness_prompt
    return f"{protocol_header()}\n{harness_prompt.rstrip()}\n\n{eval_appendix()}"
