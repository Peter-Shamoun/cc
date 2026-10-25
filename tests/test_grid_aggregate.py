"""Synthetic-data tests for research/grid/aggregate.py (no network, no gh)."""
import importlib.util
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("grid_aggregate", ROOT / "research/grid/aggregate.py")
agg = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agg)
DESIGN = json.loads((ROOT / "research/grid/design.json").read_text())

TRUE = {"gen:metric_spec": 0.15, "gen:decompose_cte": 0.05, "verifier": 0.20, "repair": 0.10,
        "fewshot": 0.0, "explore": 0.0, "candidates3": 0.0, "assumptions": 0.0, "multiagent": -0.10}


def _cell(cid, d, rng, qbase, alt_schema=False, notes="", dev_set="DEV_HARD_v1"):
    fr = agg.factor_row(d)
    eff = sum(TRUE[t] * x for t, x in zip(agg.TERMS, fr))
    rows = []
    for qi, b in enumerate(qbase):
        p = min(0.98, max(0.02, b + eff))
        ok = bool(rng.random() < p)
        execs = 1 + 2 * d["candidates3"] + d["explore"] + d["repair"] * 0.5
        if alt_schema:
            rows.append({"question_id": f"q{qi:02d}", "sql_executions": execs, "pass": ok})
        else:
            rows.append({"qid": f"q{qi:02d}", "sql_execs": execs, "pass": int(ok)})
    cfg = {"generation": d["generation"], **{f: int(d[f]) for f in agg.FLAGS}}
    key = "questions" if alt_schema else "results"
    return {"cell": cid, "config": cfg, "model": "synthetic", "dev_set": dev_set, "protocol": "v2",
            key: rows, "accuracy": f"{sum(r['pass'] for r in rows)}/{len(rows)}", "n": len(rows),
            "agent_note": notes}


def _write(tmp, cells):
    cdir = tmp / "cells"
    cdir.mkdir()
    for c in cells:
        (cdir / f"{c['cell']}.json").write_text(json.dumps(c))
    return cdir


def test_recovers_planted_effects(tmp_path):
    rng = np.random.default_rng(0)
    qbase = rng.uniform(0.2, 0.6, size=30)
    cells = [_cell("H" + d["id"][1:], d, rng, qbase, alt_schema=(i % 3 == 0)) for i, d in enumerate(DESIGN[:80])]
    ctx = agg.main(["--cells-dir", str(_write(tmp_path, cells)), "--out-dir", str(tmp_path / "out"),
                    "--design", str(ROOT / "research/grid/design.json"), "--no-prs", "--generated", "test"])
    fit = ctx["fits"]["H"]
    assert fit["n_cells"] == 80 and not ctx["invalid"]
    est = {e["term"]: e for e in fit["effects"]}
    for t, v in TRUE.items():
        assert est[t]["identified"]
        assert abs(est[t]["est"] - v) < 0.08, (t, est[t]["est"], v)
    assert est["verifier"]["lo"] > 0 and est["multiagent"]["hi"] < 0.05
    assert fit["interactions"], "80 cells should be enough to screen 2-way interactions"
    top = ctx["ranking"][0]
    assert top["generation"] == "metric_spec" and top["flags"][3] == "1"  # verifier on
    summary = (tmp_path / "out/SUMMARY.md").read_text()
    assert "Main effects: H-series" in summary and "Ranking of design combinations" in summary
    assert json.loads((tmp_path / "out/design.json").read_text()) == DESIGN
    json.loads((tmp_path / "out/aggregate.json").read_text())


def test_validation_contamination_and_ceiling(tmp_path):
    rng = np.random.default_rng(1)
    good = _cell("H003", DESIGN[3], rng, [0.5] * 10,
                 notes="While locating the dev-set rule, bench/question_families.py templates were visible.")
    fallback = _cell("H004", DESIGN[4], rng, [0.5] * 10, dev_set="v1_fallback")
    clean = _cell("H005", DESIGN[5], rng, [0.5] * 10,
                  notes="Scored with bench.compare.match against gold SQL. Did not read the answer key.")
    wrong = _cell("H006", DESIGN[6], rng, [0.5] * 10)
    wrong["config"]["generation"] = "direct_sql" if DESIGN[6]["generation"] != "direct_sql" else "metric_spec"
    ceiling = [_cell(f"C00{i}", DESIGN[i], rng, [1.0] * 5) for i in range(3)]
    for c in ceiling:
        for r in c["results"]:
            r["pass"] = 1
        c.pop("dev_set")
    ctx = agg.main(["--cells-dir", str(_write(tmp_path, [good, fallback, clean, wrong] + ceiling)),
                    "--out-dir", str(tmp_path / "out"), "--design", str(ROOT / "research/grid/design.json"),
                    "--no-prs", "--generated", "test"])
    assert [i[0] for i in ctx["invalid"]] == ["H006"]
    assert "H003" in ctx["flagged"] and "H004" in ctx["flagged"] and "H005" not in ctx["flagged"]
    assert ctx["fits"]["C"]["n_cells"] == 4  # 3 C cells + the v1_fallback H cell
    assert ctx["fits"]["C"]["n_obs"] <= 10
    assert any("ceiling" in h for h in ctx["headline"])
