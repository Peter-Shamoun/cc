#!/usr/bin/env python3
"""Pool agent-analyzer grid cells into factor effects (stdlib + numpy).

Usage (from the repo root):
    python research/grid/aggregate.py              # branch cells + open-PR cells
    python research/grid/aggregate.py --no-prs     # only research/grid/*.json on disk
    python research/grid/aggregate.py --cells-dir DIR --out-dir DIR --no-prs  # (tests)

Reads every research/grid/[CH]###.json (C = v1 dev set, at ceiling; H = DEV_HARD_v1,
protocol v2), normalises the two result schemas cell agents have produced
(`results[{qid,sql_execs,pass}]` and `questions[{question_id,sql_executions,pass}]`),
validates each cell against design.json, flags contamination, and fits

    pass_{cell,q} ~ generation + 7 flags + question fixed effects

as a linear probability model (cluster-robust SEs by cell) plus a ridge-penalised
logistic model as a robustness check. Writes SUMMARY.md and aggregate.json.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

REPO = "Peter-Shamoun/cc"
BASE = "claude/trusting-heisenberg-8xvazk"
FLAGS = ["fewshot", "explore", "candidates3", "verifier", "repair", "assumptions", "multiagent"]
GENS = ["direct_sql", "metric_spec", "decompose_cte"]  # direct_sql is the reference level
TERMS = ["gen:metric_spec", "gen:decompose_cte"] + FLAGS
CELL_RE = re.compile(r"^[CH]\d{3}$")
CELL_FILE_RE = re.compile(r"(?:^|/)research/grid/([CH]\d{3})\.json$")
HERE = Path(__file__).resolve().parent

# Phrases cell agents use when they admit seeing something the protocol forbids.
CONTAM_PATTERNS = [
    (r"reference sql", "reference SQL"),
    (r"gold (sql|answer|result)", "gold SQL/answers"),
    (r"answer key", "answer key"),
    (r"question_families\.py|sql templates?|templates? (were|was) visible", "family SQL templates"),
    (r"pitfalls? glossary", "pitfalls glossary"),
    (r"screening_results|heldout_results|failure analys", "screen/held-out failure analyses"),
    (r"other (grid )?cells?'? results", "other cells' results"),
    (r"contaminat", "self-reported contamination"),
    (r"\bvisible\b|\bopened\b|\bsaw\b|\bpeeked?\b", "saw restricted material"),
]
SCORING = re.compile(r"scor|compare\.match|checker|judge|re-?executed")
ADMISSION = re.compile(r"\b(seen|saw|visible|opened|read|peek|looked)\b")
NEGATION = re.compile(r"\b(no|not|never|without|didn'?t|did not)\b[^.;]{0,60}$")


# --------------------------------------------------------------------------- loading
def _run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True).stdout


def load_dir(d: Path, source: str):
    out = {}
    for p in sorted(Path(d).glob("*.json")):
        if CELL_RE.match(p.stem):
            try:
                out[p.stem] = (json.loads(p.read_text()), source)
            except Exception as e:  # noqa: BLE001
                out[p.stem] = ({"_load_error": str(e)}, source)
    return out


def load_ref(repo_dir: Path, ref: str, source: str):
    """Cell JSONs at a git ref (e.g. origin/<base>), read with git show; no checkout."""
    out = {}
    try:
        _run(["git", "fetch", "-q", "origin", BASE], cwd=repo_dir)
        names = _run(["git", "ls-tree", "-r", "--name-only", ref, "research/grid/"], cwd=repo_dir).split()
    except Exception as e:  # noqa: BLE001
        print(f"warn: could not read {ref}: {e}", file=sys.stderr)
        return out
    for name in names:
        m = CELL_FILE_RE.search(name)
        if m:
            try:
                out[m.group(1)] = (json.loads(_run(["git", "show", f"{ref}:{name}"], cwd=repo_dir)), source)
            except Exception as e:  # noqa: BLE001
                out[m.group(1)] = ({"_load_error": str(e)}, source)
    return out


def load_prs(repo_dir: Path):
    """Cell JSONs from open PR heads (fetched as refs; nothing is checked out)."""
    out, prs = {}, []
    try:
        prs = json.loads(_run(["gh", "pr", "list", "-R", REPO, "--state", "open", "--limit", "300",
                               "--json", "number,headRefName,baseRefName,title"]))
    except Exception as e:  # noqa: BLE001
        print(f"warn: gh pr list failed: {e}", file=sys.stderr)
        return out, []
    for pr in sorted(prs, key=lambda p: p["number"]):
        n = pr["number"]
        ref = f"refs/grid-agg/pr{n}"
        try:
            _run(["git", "fetch", "-q", "origin", f"+pull/{n}/head:{ref}"], cwd=repo_dir)
            names = _run(["git", "ls-tree", "-r", "--name-only", ref, "research/grid/"], cwd=repo_dir).split()
        except Exception as e:  # noqa: BLE001
            print(f"warn: PR #{n} fetch failed: {e}", file=sys.stderr)
            continue
        for name in names:
            m = CELL_FILE_RE.search(name)
            if not m:
                continue
            try:
                d = json.loads(_run(["git", "show", f"{ref}:{name}"], cwd=repo_dir))
            except Exception as e:  # noqa: BLE001
                d = {"_load_error": str(e)}
            out.setdefault(m.group(1), []).append((d, f"PR #{n}"))
    return out, prs


# --------------------------------------------------------------------------- normalise / validate
def _b(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "pass", "on")
    return None


def _text(d):
    parts = []
    for k in ("agent_note", "agent_notes", "notes", "protocol_notes", "contamination", "caveats"):
        v = d.get(k)
        if v:
            parts.append(v if isinstance(v, str) else json.dumps(v))
    return "\n".join(parts)


def contamination(d, series):
    reasons = []
    text = _text(d)
    low = text.lower()
    for pat, label in CONTAM_PATTERNS:
        for m in re.finditer(pat, low):
            before = low[max(0, m.start() - 80):m.start()]
            sentence_start = max(before.rfind(". "), before.rfind(";"))
            if NEGATION.search(before[sentence_start + 1:]):
                continue  # "did not read the answer key" is not an admission
            end = min([i for i in (low.find(". ", m.end()), low.find(";", m.end())) if i >= 0] or [len(low)])
            sentence = low[max(0, m.start() - 80) + sentence_start + 1:end]
            if SCORING.search(sentence) and not ADMISSION.search(sentence):
                continue  # "scored against gold SQL" describes the checker, not a leak
            reasons.append(label)
            break
    ds = dev_set_of(d, series)
    if series == "H" and ds != "DEV_HARD_v1":
        reasons.append(f"dev_set={ds} (not DEV_HARD_v1)")
    if isinstance(d.get("contaminated"), bool) and d["contaminated"]:
        reasons.append("contaminated=true")
    return sorted(set(reasons))


def dev_set_of(d, series):
    ds = d.get("dev_set")
    if isinstance(ds, str):
        return ds
    if isinstance(ds, dict):
        name = ds.get("name") or ds.get("id")
        if name:
            return str(name)
        return "DEV_HARD_v1" if "dev_hard" in json.dumps(ds).lower() else "v1"
    return "v1" if series == "C" else "unknown"


def dataset_of(d):
    ds = d.get("dataset")
    if isinstance(ds, dict) and ds.get("dataset_id"):
        return str(ds["dataset_id"])
    m = re.search(r"(?:dataset|snapshot|\bid)\b[^0-9a-f]{0,12}([0-9a-f]{12})\b", _text(d), re.I)
    return m.group(1) if m else "unknown"


def normalise(cid, d, design):
    """Return (cell_dict or None, errors, warnings)."""
    errs, warns = [], []
    if "_load_error" in d:
        return None, [f"unreadable JSON: {d['_load_error']}"], []
    if d.get("cell") not in (None, cid):
        warns.append(f"cell field {d.get('cell')!r} != filename {cid}")
    cfg = d.get("config") or {}
    want = design.get("C" + cid[1:])
    config = {}
    gen = cfg.get("generation")
    if gen not in GENS:
        errs.append(f"bad/missing generation {gen!r}")
    config["generation"] = gen
    for f in FLAGS:
        v = _b(cfg.get(f))
        if v is None:
            errs.append(f"missing flag {f}")
        config[f] = v
    if want is None:
        errs.append("cell id not in design")
    elif not errs:
        diff = [k for k in ["generation"] + FLAGS if want[k] != config[k]]
        if diff:
            errs.append(f"config disagrees with design on {diff}")
    rows_in = d.get("results") or d.get("questions") or []
    rows = []
    for i, r in enumerate(rows_in):
        qid = r.get("qid") or r.get("question_id")
        p = _b(r.get("pass"))
        if not qid or p is None:
            warns.append(f"row {i} missing qid/pass; dropped")
            continue
        ex = r.get("sql_execs", r.get("sql_executions"))
        rows.append({"qid": str(qid), "pass": int(p),
                     "sql_execs": float(ex) if isinstance(ex, (int, float)) else math.nan,
                     "repairs": r.get("repairs")})
    if not rows:
        errs.append("no scored results")
    if len({r["qid"] for r in rows}) != len(rows):
        warns.append("duplicate qids; kept first")
        seen, ded = set(), []
        for r in rows:
            if r["qid"] not in seen:
                seen.add(r["qid"]); ded.append(r)
        rows = ded
    acc = d.get("accuracy")
    if isinstance(acc, str) and "/" in acc and rows:
        k, n = acc.split("/")[:2]
        try:
            if int(k) != sum(r["pass"] for r in rows) or int(n) != len(rows):
                warns.append(f"reported accuracy {acc} != recomputed {sum(r['pass'] for r in rows)}/{len(rows)}")
        except ValueError:
            pass
    if errs:
        return None, errs, warns
    series = cid[0]
    execs = [r["sql_execs"] for r in rows if not math.isnan(r["sql_execs"])]
    mse = d.get("mean_sql_execs", d.get("mean_sql_calls"))
    cell = {
        "cell": cid, "series": series, "config": config, "rows": rows,
        "n": len(rows), "k": sum(r["pass"] for r in rows),
        "mean_sql_execs": float(np.mean(execs)) if execs else (float(mse) if mse is not None else math.nan),
        "model": d.get("model", "?"), "dev_set": dev_set_of(d, series), "dataset_id": dataset_of(d),
        "contamination": contamination(d, series), "partial": len(rows) < 20,
    }
    return cell, errs, warns


# --------------------------------------------------------------------------- models
def factor_row(config):
    x = [1.0 if config["generation"] == "metric_spec" else 0.0,
         1.0 if config["generation"] == "decompose_cte" else 0.0]
    x += [1.0 if config[f] else 0.0 for f in FLAGS]
    return x


def build_long(cells):
    ys, F, cl, qs = [], [], [], []
    for ci, c in enumerate(cells):
        fr = factor_row(c["config"])
        for r in c["rows"]:
            ys.append(r["pass"]); F.append(fr); cl.append(ci); qs.append(r["qid"])
    return np.array(ys, float), np.array(F, float).reshape(-1, len(TERMS)), np.array(cl), qs


def design_matrix(F, qs, extra=None):
    """Factor columns (+ optional interaction columns) + one dummy per question (no intercept)."""
    qlist = sorted(set(qs))
    qi = {q: i for i, q in enumerate(qlist)}
    Q = np.zeros((len(qs), len(qlist)))
    for i, q in enumerate(qs):
        Q[i, qi[q]] = 1.0
    cols = [F] + ([extra] if extra is not None else []) + [Q]
    return np.hstack(cols), qlist


def estimable(X, j, tol=1e-8):
    e = np.zeros(X.shape[1]); e[j] = 1.0
    sol, *_ = np.linalg.lstsq(X.T, e, rcond=None)
    return np.linalg.norm(X.T @ sol - e) < 1e-6 + tol


def ols_cluster(y, X, cl):
    XtX_pinv = np.linalg.pinv(X.T @ X)
    b = XtX_pinv @ X.T @ y
    e = y - X @ b
    G = len(set(cl.tolist()))
    n, k = X.shape
    rank = np.linalg.matrix_rank(X)
    meat = np.zeros((k, k))
    for g in set(cl.tolist()):
        m = cl == g
        s = X[m].T @ e[m]
        meat += np.outer(s, s)
    if G > 1 and n > rank:
        c = (G / (G - 1)) * ((n - 1) / max(n - rank, 1))
    else:
        c = math.nan
    V = c * XtX_pinv @ meat @ XtX_pinv
    return b, V, G, rank


def ridge_logit(y, X, lam=1.0, iters=100):
    """L2-penalised logistic (penalty on all coefs) via Newton; returns b, approx cov."""
    k = X.shape[1]
    b = np.zeros(k)
    P = lam * np.eye(k)
    for _ in range(iters):
        eta = np.clip(X @ b, -30, 30)
        p = 1 / (1 + np.exp(-eta))
        W = p * (1 - p)
        H = X.T @ (X * W[:, None]) + P
        g = X.T @ (y - p) - P @ b
        step = np.linalg.solve(H, g)
        b += step
        if np.max(np.abs(step)) < 1e-8:
            break
    eta = np.clip(X @ b, -30, 30)
    p = 1 / (1 + np.exp(-eta))
    H = X.T @ (X * (p * (1 - p))[:, None]) + P
    return b, np.linalg.inv(H)


def tcrit(df):
    # Student-t 97.5% quantile, small table + normal tail (no scipy needed).
    table = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
             9: 2.262, 10: 2.228, 12: 2.179, 15: 2.131, 20: 2.086, 25: 2.060, 30: 2.042, 40: 2.021,
             60: 2.000, 120: 1.980}
    if df is None or df <= 0 or math.isnan(df):
        return math.nan
    keys = sorted(table)
    for kk in keys:
        if df <= kk:
            return table[kk]
    return 1.96


def fit_pool(cells, interactions=True):
    res = {"n_cells": len(cells), "n_obs": 0, "effects": [], "logit": [], "interactions": [],
           "dropped_constant_q": 0, "questions": 0, "note": ""}
    if not cells:
        res["note"] = "no cells"
        return res
    y, F, cl, qs = build_long(cells)
    # questions with no variance across cells carry no information under question FE
    byq = {}
    for yi, q in zip(y, qs):
        byq.setdefault(q, []).append(yi)
    informative = {q for q, v in byq.items() if 0 < np.mean(v) < 1}
    res["questions"] = len(byq)
    res["dropped_constant_q"] = len(byq) - len(informative)
    res["q_pass_rate"] = {q: float(np.mean(v)) for q, v in byq.items()}
    m = np.array([q in informative for q in qs])
    res["n_obs"] = int(m.sum())
    if m.sum() == 0:
        res["note"] = ("every question has the same outcome in every cell (all pass or all fail) - "
                       "no factor is identified")
        return res
    y, F, cl, qs = y[m], F[m], cl[m], [q for q, keep in zip(qs, m) if keep]
    X, qlist = design_matrix(F, qs)
    b, V, G, rank = ols_cluster(y, X, cl)
    df = G - 1
    tc = tcrit(df)
    res["clusters"] = G
    res["fe"] = {q: float(b[len(TERMS) + i]) for i, q in enumerate(qlist)}
    res["b"] = [float(v) for v in b[:len(TERMS)]]
    for j, t in enumerate(TERMS):
        ok = estimable(X, j)
        se = math.sqrt(V[j, j]) if ok and V[j, j] > 0 and not math.isnan(V[j, j]) else math.nan
        res["effects"].append({
            "term": t, "identified": bool(ok), "est": float(b[j]) if ok else math.nan, "se": se,
            "lo": float(b[j] - tc * se) if ok and not math.isnan(se) else math.nan,
            "hi": float(b[j] + tc * se) if ok and not math.isnan(se) else math.nan,
            "cells_on": len({c for c, f in zip(cl.tolist(), F[:, j]) if f}),
        })
    try:
        lb, lV = ridge_logit(y, X, lam=1.0)
        for j, t in enumerate(TERMS):
            se = math.sqrt(lV[j, j])
            res["logit"].append({"term": t, "logodds": float(lb[j]), "lo": float(lb[j] - 1.96 * se),
                                 "hi": float(lb[j] + 1.96 * se)})
    except np.linalg.LinAlgError:
        pass
    # two-way interactions, one at a time, only if well over-determined at cell level
    if interactions and G >= 24:
        found = []
        for a in range(len(TERMS)):
            for c2 in range(a + 1, len(TERMS)):
                if a == 0 and c2 == 1:
                    continue  # generation levels are mutually exclusive
                col = (F[:, a] * F[:, c2])[:, None]
                cells_on = len({c for c, v in zip(cl, col[:, 0]) if v})
                if cells_on < 4 or cells_on > G - 4:
                    continue
                X2 = np.hstack([F, col, X[:, len(TERMS):]])
                if not estimable(X2, len(TERMS)):
                    continue
                b2, V2, _, _ = ols_cluster(y, X2, cl)
                j = len(TERMS)
                se = math.sqrt(V2[j, j]) if V2[j, j] > 0 else math.nan
                if math.isnan(se) or se == 0:
                    continue
                found.append({"terms": f"{TERMS[a]} x {TERMS[c2]}", "est": float(b2[j]), "se": se,
                              "t": float(b2[j] / se), "lo": float(b2[j] - tc * se), "hi": float(b2[j] + tc * se),
                              "cells_on": cells_on})
        res["interactions"] = sorted(found, key=lambda r: -abs(r["t"]))[:5]
    elif interactions:
        res["note"] += f"2-way interactions skipped (need >= 24 cells in pool, have {G}). "
    return res


def cost_model(cells):
    """Cell-level OLS of mean_sql_execs on factors (intercept + main effects)."""
    pts = [(factor_row(c["config"]), c["mean_sql_execs"]) for c in cells if not math.isnan(c["mean_sql_execs"])]
    if len(pts) < len(TERMS) + 3:
        return None
    X = np.array([[1.0] + p for p, _ in pts])
    y = np.array([v for _, v in pts])
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    return b


def rank_combos(design_list, fit, cost_b, observed):
    out = []
    has_fit = bool(fit.get("b"))
    base = float(np.mean([fit["q_pass_rate"][q] for q in fit["q_pass_rate"]])) if fit.get("q_pass_rate") else math.nan
    fe_mean = float(np.mean(list(fit["fe"].values()))) if fit.get("fe") else math.nan
    ident = [e["identified"] for e in fit.get("effects", [])]
    for d in design_list:
        cid = d["id"]
        fr = factor_row(d)
        pred = math.nan
        if has_fit and all(ident):
            # mean over informative questions of FE + x'b; constant questions contribute their fixed rate
            qr = fit["q_pass_rate"]
            const = [v for q, v in qr.items() if q not in fit["fe"]]
            xb = float(np.dot(fr, fit["b"]))
            inf = [min(1.0, max(0.0, fe + xb)) for fe in fit["fe"].values()]
            pred = (sum(inf) + sum(const)) / (len(inf) + len(const))
        cost = float(cost_b[0] + np.dot(fr, cost_b[1:])) if cost_b is not None else math.nan
        obs = observed.get(cid[1:])
        out.append({"id": cid[1:], "generation": d["generation"],
                    "flags": "".join("1" if d[f] else "0" for f in FLAGS),
                    "pred_acc": pred, "pred_execs": cost,
                    "obs": f"{obs['k']}/{obs['n']}" if obs else "", "obs_execs": obs["mean_sql_execs"] if obs else math.nan})
    _ = (base, fe_mean)
    key = (lambda r: (-(r["pred_acc"] if not math.isnan(r["pred_acc"]) else -1),
                      r["pred_execs"] if not math.isnan(r["pred_execs"]) else 1e9))
    out.sort(key=key)
    # Pareto frontier: no other combo has >= accuracy and <= cost with one strict
    for r in out:
        r["pareto"] = not any(
            (o["pred_acc"] >= r["pred_acc"] and o["pred_execs"] <= r["pred_execs"] and
             (o["pred_acc"] > r["pred_acc"] or o["pred_execs"] < r["pred_execs"]))
            for o in out if o is not r) if not (math.isnan(r["pred_acc"]) or math.isnan(r["pred_execs"])) else False
    return out


# --------------------------------------------------------------------------- report
def pp(x, digits=1):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{100 * x:+.{digits}f}"


def num(x, digits=2):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}f}"


def effects_table(fit):
    lines = ["| term | effect (pp) | 95% CI (pp) | identified | ridge-logit log-odds [95% CI] |",
             "|---|---|---|---|---|"]
    lg = {r["term"]: r for r in fit.get("logit", [])}
    for e in fit.get("effects", []):
        l = lg.get(e["term"])
        lt = f"{l['logodds']:+.2f} [{l['lo']:+.2f}, {l['hi']:+.2f}]" if l else "n/a"
        ci = f"[{pp(e['lo'])}, {pp(e['hi'])}]" if not math.isnan(e["lo"]) else "n/a"
        lines.append(f"| {e['term']} | {pp(e['est'])} | {ci} | {'yes' if e['identified'] else 'NO (aliased)'} | {lt} |")
    return "\n".join(lines)


def write_summary(path, ctx):
    L = []
    w = L.append
    w("# Agent-analyzer grid: pooled results")
    w("")
    w(f"_Generated by `research/grid/aggregate.py` at {ctx['generated']}. Re-run it to refresh; do not hand-edit._")
    w("")
    w("## Status")
    w("")
    w(f"- Cells found: **{ctx['n_found']}** ({ctx['n_valid']} valid, {len(ctx['invalid'])} rejected by schema/design checks).")
    for s in ("H", "C"):
        cs = [c for c in ctx["cells"] if c["series"] == s]
        if cs:
            w(f"- {s}-series ({'DEV_HARD_v1, protocol v2 - the series that matters' if s == 'H' else 'v1 dev set - at ceiling'}): "
              f"{len(cs)} cells, {sum(c['k'] for c in cs)}/{sum(c['n'] for c in cs)} question-passes "
              f"({100 * sum(c['k'] for c in cs) / max(1, sum(c['n'] for c in cs)):.1f}%).")
    w(f"- Sources: {', '.join(sorted(ctx['sources']))}.")
    w(f"- Contamination-flagged cells: {len(ctx['flagged'])} ({', '.join(ctx['flagged']) or 'none'}).")
    w("")
    w("## Headline")
    w("")
    for h in ctx["headline"]:
        w(f"- {h}")
    w("")
    for label, key in (("H-series (primary)", "H"), ("H-series, contamination-flagged cells excluded", "H_clean"),
                       ("C-series (v1 dev set, ceiling - for reference only)", "C")):
        fit = ctx["fits"].get(key)
        if not fit:
            continue
        w(f"## Main effects: {label}")
        w("")
        w(f"Linear probability model, pass ~ generation (ref = direct_sql) + 7 flags + question fixed effects; "
          f"cluster-robust SEs by cell, t({max(0, fit.get('clusters', 1) - 1)}) CIs. "
          f"{fit['n_cells']} cells, {fit['questions']} questions, {fit['dropped_constant_q']} questions dropped as "
          f"constant across cells (all-pass/all-fail carry no information under question FE), {fit['n_obs']} informative observations.")
        if fit.get("note"):
            w("")
            w(f"> {fit['note'].strip()}")
        w("")
        if fit.get("effects"):
            w(effects_table(fit))
            w("")
        if fit.get("interactions"):
            w("Top two-way interactions (each added alone to the main-effects model):")
            w("")
            w("| interaction | est (pp) | 95% CI (pp) | t | cells with both on |")
            w("|---|---|---|---|---|")
            for r in fit["interactions"]:
                w(f"| {r['terms']} | {pp(r['est'])} | [{pp(r['lo'])}, {pp(r['hi'])}] | {r['t']:+.2f} | {r['cells_on']} |")
            w("")
    w("## Ranking of design combinations (H-series model)")
    w("")
    rk = ctx["ranking"]
    if rk and not math.isnan(rk[0]["pred_acc"]):
        w("Predicted accuracy = mean over DEV_HARD_v1 questions of the fitted main-effects LPM (clipped to [0,1]); "
          "cost = predicted mean SQL executions per question from a cell-level OLS. * = on the accuracy/cost Pareto frontier.")
        w("")
        w("| rank | cell | generation | flags fe/ex/c3/ve/re/as/ma | pred acc | pred SQL execs | observed | obs execs |")
        w("|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(rk[:15], 1):
            w(f"| {i} | {r['id']}{'*' if r['pareto'] else ''} | {r['generation']} | {r['flags']} | "
              f"{100 * r['pred_acc']:.1f}% | {num(r['pred_execs'], 1)} | {r['obs']} | {num(r['obs_execs'], 1)} |")
    else:
        w("Not enough identified H-series effects to predict untested combinations yet. Observed cells, best first:")
        w("")
        w("| cell | generation | flags fe/ex/c3/ve/re/as/ma | pass | mean SQL execs | flags |")
        w("|---|---|---|---|---|---|")
        obs = sorted(ctx["cells"], key=lambda c: (c["series"] != "H", -c["k"] / c["n"], c["mean_sql_execs"]))
        for c in obs:
            w(f"| {c['cell']} | {c['config']['generation']} | {''.join('1' if c['config'][f] else '0' for f in FLAGS)} | "
              f"{c['k']}/{c['n']} | {num(c['mean_sql_execs'], 1)} | {'; '.join(c['contamination']) or ''} |")
    w("")
    w("## Cells")
    w("")
    w("| cell | source | dev set | dataset | model | pass | SQL execs | contamination flags |")
    w("|---|---|---|---|---|---|---|---|")
    for c in sorted(ctx["cells"], key=lambda c: c["cell"]):
        w(f"| {c['cell']} | {c['source']} | {c['dev_set']} | {c['dataset_id']} | {c['model']} | {c['k']}/{c['n']}"
          f"{' (partial)' if c['partial'] else ''} | {num(c['mean_sql_execs'], 1)} | {'; '.join(c['contamination'])} |")
    if ctx["invalid"]:
        w("")
        w("Rejected:")
        w("")
        for cid, src, errs in ctx["invalid"]:
            w(f"- {cid} ({src}): {'; '.join(errs)}")
    if ctx["warnings"]:
        w("")
        w("Warnings:")
        w("")
        for cid, src, ws in ctx["warnings"]:
            w(f"- {cid} ({src}): {'; '.join(ws)}")
    w("")
    w("## Caveats")
    w("")
    for cv in ctx["caveats"]:
        w(f"- {cv}")
    w("")
    Path(path).write_text("\n".join(L))


def headline(ctx):
    hs = []
    H = ctx["fits"].get("H")
    C = ctx["fits"].get("C")
    if not H or not H.get("effects"):
        nh = len([c for c in ctx["cells"] if c["series"] == "H"])
        hs.append(f"No identified H-series effects yet ({nh} H cells pooled). "
                  + (H.get("note", "").strip() if H else ""))
    else:
        sig = [e for e in H["effects"] if e["identified"] and not math.isnan(e["lo"]) and (e["lo"] > 0 or e["hi"] < 0)]
        best = sorted([e for e in H["effects"] if e["identified"]], key=lambda e: -abs(e["est"]))[:3]
        if not best:
            hs.append(f"{H['n_cells']} H cells pooled, but too few distinct configurations to identify any factor yet.")
        else:
            hs.append("Largest H-series effects: " + "; ".join(
                f"{e['term']} {pp(e['est'])} pp [{pp(e['lo'])}, {pp(e['hi'])}]" for e in best) + ".")
        if best:
            hs.append("CIs excluding zero: " + (", ".join(f"{e['term']} ({pp(e['est'])} pp)" for e in sig) or "none yet") + ".")
        al = [e["term"] for e in H["effects"] if not e["identified"]]
        if al:
            hs.append(f"Not yet identifiable (aliased with too few cells): {', '.join(al)}.")
        if ctx["ranking"] and not math.isnan(ctx["ranking"][0]["pred_acc"]):
            r = ctx["ranking"][0]
            hs.append(f"Best predicted combination: {r['id']} ({r['generation']}, flags {r['flags']}) at "
                      f"{100 * r['pred_acc']:.1f}% with ~{num(r['pred_execs'], 1)} SQL execs/question.")
    if C:
        cs = [c for c in ctx["cells"] if c["series"] == "C"]
        if cs and all(c["k"] == c["n"] for c in cs):
            hs.append(f"C-series (v1 dev set): all {len(cs)} cells scored 100% - ceiling, no factor information.")
    return hs


# --------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default=str(HERE.parent.parent))
    ap.add_argument("--cells-dir", default=None, help="default: <repo>/research/grid")
    ap.add_argument("--out-dir", default=None, help="default: <repo>/research/grid")
    ap.add_argument("--design", default=None, help="design.json (default: next to this script, else ../cc_army/combos.json)")
    ap.add_argument("--no-prs", action="store_true")
    ap.add_argument("--generated", default=None, help="timestamp string for SUMMARY (tests)")
    a = ap.parse_args(argv)
    repo = Path(a.repo_dir)
    cells_dir = Path(a.cells_dir) if a.cells_dir else repo / "research/grid"
    out_dir = Path(a.out_dir) if a.out_dir else repo / "research/grid"
    out_dir.mkdir(parents=True, exist_ok=True)
    dpath = Path(a.design) if a.design else (HERE / "design.json")
    design_list = json.loads(dpath.read_text())
    design = {d["id"]: d for d in design_list}
    if dpath.resolve() != (out_dir / "design.json").resolve():
        (out_dir / "design.json").write_text(json.dumps(design_list, indent=1) + "\n")

    raw = {k: [v] for k, v in load_dir(cells_dir, "branch").items()}
    prs = []
    if not a.no_prs and not a.cells_dir:
        for k, v in load_ref(repo, f"origin/{BASE}", "branch").items():
            raw[k] = [v]  # the pushed working branch beats a stale local checkout
    if not a.no_prs:
        pr_cells, prs = load_prs(repo)
        for k, lst in pr_cells.items():
            if k in raw:
                continue  # merged copy on the branch wins
            raw.setdefault(k, []).extend(lst)

    cells, invalid, warnings, sources = [], [], [], set()
    for cid in sorted(raw):
        chosen = None
        for d, src in raw[cid]:
            c, errs, ws = normalise(cid, d, design)
            if ws:
                warnings.append((cid, src, ws))
            if c is None:
                invalid.append((cid, src, errs))
                continue
            c["source"] = src
            chosen = c  # last valid copy (highest PR number) wins
        if chosen:
            cells.append(chosen)
            sources.add("working branch" if chosen["source"] == "branch" else "open PRs")

    flagged = [c["cell"] for c in cells if c["contamination"]]
    H = [c for c in cells if c["series"] == "H" and c["dev_set"] != "v1_fallback"]
    Hfb = [c for c in cells if c["series"] == "H" and c["dev_set"] == "v1_fallback"]
    C = [c for c in cells if c["series"] == "C"] + Hfb
    fits = {"H": fit_pool(H), "C": fit_pool(C, interactions=False)}
    Hclean = [c for c in H if not c["contamination"]]
    if Hclean and len(Hclean) != len(H):
        fits["H_clean"] = fit_pool(Hclean)
    cost_b = cost_model(H) if len(H) >= len(TERMS) + 3 else cost_model(cells)
    observed = {c["cell"][1:]: c for c in H}
    ranking = rank_combos(design_list, fits["H"], cost_b, observed)

    datasets = sorted({c["dataset_id"] for c in H})
    caveats = [
        "Every cell's analyst is the cloud/box agent's own model (see the model column), not Haiku or Sonnet; "
        "effects describe that model and may not transfer.",
        "Contamination is flagged from free-text notes (agent_note/notes/protocol_notes) by keyword, so it is a "
        "screen, not a verdict. Read the flagged cells' notes. Most admissions so far are about seeing family SQL "
        "templates in bench/question_families.py while locating the dev-set rule.",
        "H cells with dev_set=v1_fallback are pooled with the C-series, not the H-series.",
        "Pass/fail is the repo's deterministic matcher (bench.compare.match) unless the cell says otherwise.",
    ]
    if len(datasets) > 1:
        caveats.append(f"H cells ran on {len(datasets)} different snapshots ({', '.join(datasets)}); question fixed "
                       "effects absorb per-question difficulty but not snapshot x factor interactions.")
    if any(c["partial"] for c in cells):
        caveats.append("Some cells are partial (n < 20); they are pooled at their real n.")
    from datetime import datetime
    ctx = {"generated": a.generated or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"),
           "n_found": len(raw), "n_valid": len(cells), "invalid": invalid, "warnings": warnings,
           "cells": cells, "sources": sources or {"none"}, "flagged": flagged, "fits": fits,
           "ranking": ranking, "caveats": caveats}
    ctx["headline"] = headline(ctx)
    write_summary(out_dir / "SUMMARY.md", ctx)
    slim = {k: v for k, v in ctx.items() if k not in ("cells", "sources")}
    slim["cells"] = [{k: v for k, v in c.items() if k != "rows"} for c in cells]
    for f in slim["fits"].values():
        f.pop("q_pass_rate", None); f.pop("fe", None)
    slim["ranking"] = ranking[:30]
    (out_dir / "aggregate.json").write_text(json.dumps(slim, indent=1, default=str,
                                                       allow_nan=True).replace("NaN", "null") + "\n")
    print(f"cells pooled: {len(cells)} (H={len(H)}, C={len(C)}), rejected={len(invalid)}, flagged={len(flagged)}")
    for h in ctx["headline"]:
        print(" -", h)
    return ctx


if __name__ == "__main__":
    main()
