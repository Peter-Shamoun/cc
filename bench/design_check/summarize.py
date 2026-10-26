"""Tables for docs/data-backend/lake-design-check.md from the run.py outputs.

    python -m bench.design_check.summarize current.json ducklake.json iceberg.json
CPU of the current pipeline includes parsing (it parses inside the task); the parse CPU measured once per day
(gen_days, same parser, same files) is subtracted so every row is lake maintenance only.
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

WINDOWS = ((1, 20), (21, 60), (61, 120))


def lake_cost(r: dict, b: dict) -> dict:
    out = dict(requests=b["requests"], read=b["bytes_read"], written=b["bytes_written"],
               cpu=b["cpu_client_s"] + b["cpu_endpoint_s"] * 0)            # client CPU only
    if r["system"] == "current":
        out["cpu"] = max(0.0, b["cpu_client_s"] - b["parse_cpu_s"])
    if r["system"] == "ducklake":                    # catalog file GET at open + PUT at close (Postgres would not)
        ph = b["phases"].values()
        out["catalog_xfer"] = sum(p.get("catalog_bytes_read", 0) + p.get("catalog_bytes_written", 0) for p in ph)
        out["catalog_requests"] = sum(p.get("catalog_requests", 0) for p in ph)
    return out


def med(xs):
    return st.median(xs) if xs else float("nan")


def batch_table(runs) -> str:
    lines = ["| design | business days | requests | MB read | MB written | client CPU s | month-close batch (req / MB read / MB written / CPU s) |",
             "|---|---|---|---|---|---|---|"]
    for r in runs:
        for lo, hi in WINDOWS:
            bs = [b for b in r["batches"] if lo <= b["n"] <= hi and not b["month_close"]]
            if not bs:
                continue
            cs = [lake_cost(r, b) for b in bs]
            mc = [lake_cost(r, b) for b in r["batches"] if lo <= b["n"] <= hi and b["month_close"]]
            mcs = " ; ".join(f"{c['requests']:,} / {c['read'] / 1e6:.0f} / {c['written'] / 1e6:.0f} / {c['cpu']:.0f}" for c in mc)
            extra = ""
            if "catalog_xfer" in cs[0]:
                extra = (f" (catalog file: {med([c['catalog_requests'] for c in cs]):.0f} req, "
                         f"{med([c['catalog_xfer'] for c in cs]) / 1e6:.0f} MB read+written)")
            lines.append(f"| {r['system']} | {lo}-{hi} | {med([c['requests'] for c in cs]):,.0f} | "
                         f"{med([c['read'] for c in cs]) / 1e6:.0f}{extra} | {med([c['written'] for c in cs]) / 1e6:.0f} | "
                         f"{med([c['cpu'] for c in cs]):.1f} | {mcs or '-'} |")
        tot = [lake_cost(r, b) for b in r["batches"]]
        lines.append(f"| {r['system']} | total {len(tot)} batches | {sum(c['requests'] for c in tot):,} | "
                     f"{sum(c['read'] for c in tot) / 1e9:.1f} GB | {sum(c['written'] for c in tot) / 1e9:.1f} GB | "
                     f"{sum(c['cpu'] for c in tot):,.0f} | |")
    return "\n".join(lines)


def last_batches(runs, n=5) -> str:
    lines = ["| design | batches | requests | MB read | MB written | client CPU s |", "|---|---|---|---|---|---|"]
    for r in runs:
        bs = [b for b in r["batches"] if not b["month_close"]][-n:]
        cs = [lake_cost(r, b) for b in bs]
        lines.append(f"| {r['system']} | {bs[0]['n']}-{bs[-1]['n']} | {med([c['requests'] for c in cs]):,.0f} | "
                     f"{med([c['read'] for c in cs]) / 1e6:.0f} | {med([c['written'] for c in cs]) / 1e6:.0f} | "
                     f"{med([c['cpu'] for c in cs]):.1f} |")
    return "\n".join(lines)


def q_summary(qs) -> str:
    o_req = med([q["open"]["requests"] for q in qs])
    o_mb = med([q["open"]["bytes_read"] for q in qs]) / 1e6
    q_req = [q["query"]["requests"] for q in qs]
    q_mb = [q["query"]["bytes_read"] / 1e6 for q in qs]
    cpu = med([q["open"]["cpu_client_s"] + q["query"]["cpu_client_s"] for q in qs])
    cat = med([q["open"].get("catalog_bytes_read", 0) + q["query"].get("catalog_bytes_read", 0) for q in qs]) / 1e6
    return (f"{o_req:.0f} / {o_mb:.2f} | {med(q_req):.0f} / {max(q_req)} | {med(q_mb):.2f} / {max(q_mb):.2f} | "
            f"{cat:.2f} | {cpu * 1000:.0f}")


def query_table(runs) -> str:
    ref = {cp["n"]: {q["id"]: q["fp"] for q in cp["queries"]} for cp in runs[0]["checkpoints"]}
    lines = ["| design | business days | state | session open: requests / MB | query requests median / max | "
             "query MB median / max | of which catalog file MB (open+query) | client CPU ms median (open+query) | "
             "results equal to current |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        for cp in r["checkpoints"]:
            for state, key in (("as maintained", "queries"), ("after compacting now", "queries_after_compact")):
                if key not in cp:
                    continue
                qs = cp[key]
                same = sum(q["fp"] == ref.get(cp["n"], {}).get(q["id"]) for q in qs)
                lines.append(f"| {r['system']} | {cp['n']} | {state} | {q_summary(qs)} | {same}/{len(qs)} |")
    return "\n".join(lines)


def storage_table(runs) -> str:
    lines = ["| design | business days | silver live MB / files | gold live MB / files | all stored versions MB "
             "(what S3 bills before lifecycle) | table-format files (data / delete / snapshots) |",
             "|---|---|---|---|---|---|"]
    for r in runs:
        for cp in r["checkpoints"]:
            for state, sk, fk in (("", "storage", "files"), (" after compaction", "storage_after_compact", "files_after_compact")):
                if sk not in cp:
                    continue
                s = cp[sk]
                f = cp.get(fk) or {}
                fs = "; ".join(f"{layer} {v.get('data_files')} / {v.get('delete_files', '-')} / {v.get('snapshots')}"
                               for layer, v in f.items()) or "-"
                lines.append(f"| {r['system']}{state} | {cp['n']} | {s['silver']['live_bytes'] / 1e6:.0f} / "
                             f"{s['silver']['live_files']} | {s['gold']['live_bytes'] / 1e6:.0f} / {s['gold']['live_files']} | "
                             f"{(s['silver']['stored_bytes'] + s['gold']['stored_bytes']) / 1e6:.0f} | {fs} |")
            if "compact_now" in cp:
                c = cp["compact_now"]
                lines.append(f"| {r['system']} compaction on demand | {cp['n']} | {c['requests']:,} requests, "
                             f"{c['bytes_read'] / 1e6:.0f} MB read, {c['bytes_written'] / 1e6:.0f} MB written, "
                             f"{c['cpu_client_s']:.0f} s CPU | | | |")
    return "\n".join(lines)


def main(paths):
    runs = [json.loads(Path(p).read_text()) for p in paths]
    print("## Per batch (median, month-close batches listed separately)\n")
    print(batch_table(runs))
    print("\n## Last five ordinary batches\n")
    print(last_batches(runs))
    print("\n## Agent workload\n")
    print(query_table(runs))
    print("\n## Storage\n")
    print(storage_table(runs))
    for r in runs:
        if r["system"] == "iceberg":
            print("\ncatalog commits per batch (iceberg, median):",
                  med([b.get("catalog_commits", 0) for b in r["batches"]]))
        if r["system"] == "ducklake":
            print("\nducklake catalog file sizes, last batch:", r["batches"][-1].get("catalog_bytes"))


if __name__ == "__main__":
    main(sys.argv[1:])
