"""Writes cases.json: small streams with the exact rule ids each must violate. Both runners must agree on every case.
Run: python -m api.contract.conformance_cases.make_cases"""
import json
from pathlib import Path

S = {"type": "message.start", "messageId": "m1", "conversationId": "c1", "contractVersion": "1.12.0"}
def E(fr="stop", mid="m1"): return {"type": "message.end", "messageId": mid, "finishReason": fr, "usage": {}}
def run(i, sql="SELECT 1"): return {"type": "step.sql_running", "stepId": i, "sql": sql, "purpose": "answer", "attempt": 1}
def res(i, ok=True): return {"type": "step.sql_result", "stepId": i, "ok": ok, "elapsedMs": 1}
def ts(p): return {"type": "text.start", "partId": p}
def td(p): return {"type": "text.delta", "partId": p, "delta": "x"}
def te(p): return {"type": "text.end", "partId": p}
def part(p, t="callout", **kw): return {"type": "part", "part": {"type": t, "id": p, **kw}}
HB = {"type": "heartbeat"}


def seq(evs, start=0):
    out, n = [], start
    for e in evs:
        if e.get("type") == "heartbeat":
            out.append(e)
        else:
            out.append({**e, "seq": n}); n += 1
    return out


CASES = [
    ("ok_minimal", seq([S, E()]), []),
    ("ok_full", seq([S, run("s1"), res("s1", False),
                     {"type": "step.repair_attempt", "stepId": "s2", "failedStepId": "s1", "attempt": 1, "error": "e", "revisedSql": "x"},
                     {**run("s3"), "purpose": "repair", "repairOf": "s2"}, res("s3"), HB,
                     {"type": "table.start", "partId": "p1", "columns": []},
                     {"type": "table.rows", "partId": "p1", "offset": 0, "rows": []}, part("p1", "table", sqlPartId=None),
                     ts("p2"), td("p2"), te("p2"),
                     {"type": "step.check", "stepId": "s4", "check": "c", "verdict": "pass", "message": "m", "targetStepId": "s3"},
                     {"type": "part.patch", "partId": "p1", "patch": {"title": "t"}}, E()]), []),
    ("ok_aborted_mid_text", seq([S, ts("p1"), td("p1"), E("aborted")]), []),
    ("ok_clarify", seq([S, part("p1", "clarification"), E("needs_input")]), []),
    ("empty", [], ["end.last", "end.once", "start.first", "start.once"]),
    ("not_a_list_item", [S | {"seq": 0}, "oops", E() | {"seq": 1}], ["event.shape"]),
    ("missing_end", seq([S]), ["end.last", "end.once"]),
    ("event_after_end", seq([S, E(), ts("p1")]), ["end.last", "text.span"]),
    ("two_starts", seq([S, S, E()]), ["start.once"]),
    ("seq_gap", [S | {"seq": 0}, E() | {"seq": 2}], ["seq.gapless"]),
    ("seq_string", [S | {"seq": "0"}, E() | {"seq": 1}], ["seq.gapless"]),
    ("message_id_mismatch", seq([S, E(mid="m2")]), ["message.same_id"]),
    ("unknown_finish", seq([S, E("done")]), ["finish.known"]),
    ("result_before_running", seq([S, res("s1"), run("s1"), E()]), ["sql.paired", "sql.paired"]),
    ("sql_never_finishes", seq([S, run("s1"), E()]), ["sql.paired"]),
    ("duplicate_step_id", seq([S, run("s1"), res("s1"), run("s1"), res("s1"), E()]), ["sql.paired", "sql.paired", "step.unique"]),
    ("repair_of_ok_step", seq([S, run("s1"), res("s1"),
                               {"type": "step.repair_attempt", "stepId": "s2", "failedStepId": "s1", "attempt": 1, "error": "e", "revisedSql": "x"}, E()]),
     ["repair.target"]),
    ("check_unknown_target", seq([S, {"type": "step.check", "stepId": "s1", "check": "c", "verdict": "pass", "message": "m", "targetStepId": "s9"}, E()]),
     ["check.target"]),
    ("delta_before_start", seq([S, td("p1"), ts("p1"), te("p1"), E()]), ["text.span"]),
    ("delta_after_end", seq([S, ts("p1"), te("p1"), td("p1"), E()]), ["text.span"]),
    ("text_never_closed", seq([S, ts("p1"), td("p1"), E()]), ["text.span"]),
    ("rows_without_table_start", seq([S, {"type": "table.rows", "partId": "p1", "offset": 0, "rows": []}, E()]), ["table.span"]),
    ("table_never_completed", seq([S, {"type": "table.start", "partId": "p1", "columns": []}, E()]), ["table.span"]),
    ("patch_unknown_part", seq([S, {"type": "part.patch", "partId": "p9", "patch": {}}, E()]), ["patch.target"]),
    ("step_after_answer", seq([S, ts("p1"), te("p1"), run("s1"), res("s1"), E()]), ["answer.after_steps", "answer.after_steps"]),
    ("escalation_after_answer", seq([S, part("p1"), {"type": "escalation", "from": {}, "to": {}, "reason": "r"}, E()]), ["answer.after_steps"]),
    ("sql_part_unknown", seq([S, part("p1", "table", sqlPartId="p9"), E()]), ["sql_part.target"]),
    ("repair_of_unknown", seq([S, {**run("s1"), "purpose": "repair", "repairOf": "s9"}, res("s1"), E()]), ["repair.of"]),
    ("needs_input_without_clarification", seq([S, E("needs_input")]), ["clarify.needs_input"]),
]

if __name__ == "__main__":
    out = [{"name": n, "events": evs, "expect": sorted(x)} for n, evs, x in CASES]
    Path(__file__).with_name("cases.json").write_text(json.dumps(out, indent=1) + "\n")
    print(f"wrote {len(out)} cases")
