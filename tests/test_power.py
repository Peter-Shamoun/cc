"""Power numbers recovered from the held-out writeup."""
from bench.power import REPORT_PATH, analyze, render_report


def test_heldout_variance_locks_the_sample_sizes():
    result = analyze()
    haiku_pitfalls = next(row for row in result["rows"] if row["model"] == "haiku" and row["contrast"] == "pitfalls")
    assert abs(haiku_pitfalls["sd_paired"] - 0.368509) < 1e-4
    conservative = result["planning"]["haiku"]["by_repeats"][2]["conservative"]
    assert conservative["n_80_for_5pp"] == 548
    assert conservative["n_80_for_10pp"] == 137
    assert conservative["n_ci_for_10pp"] == 67
    assert abs(conservative["sigma"] - 0.4176) < 1e-3
    sonnet = result["planning"]["sonnet"]["by_repeats"][2]["conservative"]
    assert sonnet["n_80_for_5pp"] == 154
    assert sonnet["n_80_for_10pp"] == 39
    assert sonnet["n_ci_for_10pp"] == 19
    # At two repeats the planning SD is the observed paired SD. Extra repeats barely move n.
    assert result["planning"]["haiku"]["by_repeats"][8]["conservative"]["n_80_for_10pp"] == 116
    assert render_report(result) == REPORT_PATH.read_text()
