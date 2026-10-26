"""How many questions and repeats a paired glossary contrast needs.

The held-out run (research/heldout_results_1.md) kept the configuration totals, pass^2, and the question-level
bootstrap intervals. It did not keep the per-question rows. With two repeats, accuracy and pass^2 identify the
distribution of correct repeats on a question, and the bootstrap interval identifies the standard deviation of
the paired per-question difference (normal approximation to the percentile interval). Those are the observed
variance and the within-question repeat correlation. The correlation between the baseline and the alternative,
on the same question, follows from the two variances and the variance of the difference.

Planning uses that structure. Extra repeats only shrink the within-question noise. They do not shrink the
between-question part, which is most of the variance when a question is usually either solved or not.

    python -m bench.power
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "research" / "screen2_power.md"

# Two-sided 5% test, 80% power. Normal quantiles (no scipy dependency).
Z_ALPHA = 1.959963984540054
Z_POWER = 0.8416212335729143
N_QUESTIONS = 27
REPEATS_OBSERVED = 2

# Published percentages converted to proportions. pass_all is pass^2. The paired effect and CI are the
# question-level bootstrap from `python -m bench.eval_subagents contrast`, in proportion (the writeup multiplies
# by 100). Source table: research/heldout_results_1.md.
OBSERVED = [
    dict(model="haiku", contrast="pitfalls", acc_base=0.741, acc_alt=0.722, pass_base=0.704, pass_alt=0.667,
         effect=-0.019, ci=(-0.148, 0.130)),
    dict(model="haiku", contrast="pitfalls_procedural", acc_base=0.741, acc_alt=0.759, pass_base=0.704, pass_alt=0.630,
         effect=0.019, ci=(-0.130, 0.185)),
    dict(model="haiku", contrast="screen1_winner", acc_base=0.741, acc_alt=0.852, pass_base=0.704, pass_alt=0.815,
         effect=0.111, ci=(-0.037, 0.278)),
    dict(model="sonnet", contrast="pitfalls", acc_base=0.833, acc_alt=0.907, pass_base=0.815, pass_alt=0.889,
         effect=0.074, ci=(0.000, 0.167)),
    dict(model="sonnet", contrast="pitfalls_procedural", acc_base=0.833, acc_alt=0.833, pass_base=0.815, pass_alt=0.815,
         effect=0.000, ci=(-0.056, 0.056)),
    dict(model="sonnet", contrast="screen1_winner", acc_base=0.833, acc_alt=0.870, pass_base=0.815, pass_alt=0.852,
         effect=0.037, ci=(0.000, 0.111)),
]


def repeat_distribution(accuracy: float, pass_all: float, repeats: int = REPEATS_OBSERVED) -> dict:
    """Moments of the question-level mean of `repeats` Bernoulli trials, from accuracy and P(all correct).

    For two repeats, P(both correct) and the mean identify P(exactly one) and P(neither). A negative cell smaller
    than rounding error (the published figures are one decimal in percent) is clipped.
    """
    if repeats != 2:
        raise ValueError("the closed form uses the two-repeat pass^2 reported for the held-out run")
    p2 = pass_all
    mean_count = accuracy * repeats
    p1 = mean_count - 2 * p2
    p0 = 1 - p1 - p2
    p0, p1, p2 = _clip_distribution(p0, p1, p2)
    mean = (p1 + 2 * p2) / 2
    second = (p1 + 4 * p2) / 4
    variance = max(0.0, second - mean ** 2)
    trial_variance = accuracy * (1 - accuracy)
    if trial_variance <= 1e-12:
        correlation = 1.0
    else:
        # Var(mean of r exchangeable trials) = trial_var * (1 + (r-1)*rho) / r
        correlation = (variance * repeats / trial_variance - 1) / (repeats - 1)
    return dict(p0=p0, p1=p1, p2=p2, mean=mean, variance=variance, trial_variance=trial_variance,
                repeat_correlation=correlation)


def _clip_distribution(p0: float, p1: float, p2: float) -> tuple[float, float, float]:
    raw = [p0, p1, p2]
    if min(raw) >= -1e-9 and abs(sum(raw) - 1) < 1e-6:
        return tuple(max(0.0, x) for x in raw)  # type: ignore[return-value]
    clipped = [max(0.0, x) for x in raw]
    total = sum(clipped)
    if total <= 0:
        raise ValueError(f"repeat distribution is empty: {raw}")
    return tuple(x / total for x in clipped)  # type: ignore[return-value]


def difference_sd(ci: tuple[float, float], n_questions: int = N_QUESTIONS, z: float = Z_ALPHA) -> float:
    """SD of the per-question paired difference, from a normal approximation to the bootstrap interval."""
    half = (ci[1] - ci[0]) / 2
    standard_error = half / z
    return standard_error * math.sqrt(n_questions)


def variance_of_mean(accuracy: float, repeat_correlation: float, repeats: int) -> float:
    rho = min(1.0, max(-1.0 / max(repeats - 1, 1), repeat_correlation))
    return accuracy * (1 - accuracy) * (1 + (repeats - 1) * rho) / repeats


def questions_for_power(sigma: float, effect: float, z_alpha: float = Z_ALPHA, z_power: float = Z_POWER) -> int:
    """Smallest n with 80% power for a two-sided normal test of a paired mean, or 1 if sigma is 0."""
    if effect <= 0:
        raise ValueError("effect must be positive")
    if sigma <= 0:
        return 1
    return max(2, math.ceil((z_alpha + z_power) ** 2 * sigma ** 2 / effect ** 2 - 1e-9))


def questions_for_ci(sigma: float, effect: float, z: float = Z_ALPHA) -> int:
    """Smallest n whose expected 95% interval excludes 0 when the true paired effect is `effect`."""
    if effect <= 0:
        raise ValueError("effect must be positive")
    if sigma <= 0:
        return 1
    return max(2, math.ceil((z * sigma / effect) ** 2 - 1e-9))


def _analyze_row(row: dict) -> dict:
    base = repeat_distribution(row["acc_base"], row["pass_base"])
    alt = repeat_distribution(row["acc_alt"], row["pass_alt"])
    sd = difference_sd(row["ci"])
    var_d = sd ** 2
    cov = (base["variance"] + alt["variance"] - var_d) / 2
    denom = math.sqrt(base["variance"] * alt["variance"])
    correlation = cov / denom if denom > 0 else 0.0
    # Cov of the two question-level means is taken as constant in the number of repeats: repeat noise is
    # independent across conditions, so averaging more repeats does not add cross-condition covariance.
    cov_used = cov
    note = ""
    if correlation > 1:
        cov_used = denom
        correlation_used = 1.0
        note = "cross-condition correlation clipped to 1 (published figures are rounded)"
    elif correlation < -1:
        cov_used = -denom
        correlation_used = -1.0
        note = "cross-condition correlation clipped to -1 (published figures are rounded)"
    else:
        correlation_used = correlation
    by_repeats = {}
    for repeats in (1, 2, 3, 5, 8):
        var = (variance_of_mean(row["acc_base"], base["repeat_correlation"], repeats)
               + variance_of_mean(row["acc_alt"], alt["repeat_correlation"], repeats)
               - 2 * cov_used)
        # At r=2 the identity var_base + var_alt - 2 cov = var_d holds before clipping. After clipping, floor at 0.
        var = max(0.0, var)
        sigma = math.sqrt(var)
        by_repeats[repeats] = dict(
            sigma=sigma,
            n_80_for_5pp=questions_for_power(sigma, 0.05),
            n_80_for_10pp=questions_for_power(sigma, 0.10),
            n_ci_for_5pp=questions_for_ci(sigma, 0.05),
            n_ci_for_10pp=questions_for_ci(sigma, 0.10),
        )
    return dict(
        model=row["model"], contrast=row["contrast"], effect=row["effect"], ci=row["ci"],
        sd_paired=sd, var_base=base["variance"], var_alt=alt["variance"],
        rho_base=base["repeat_correlation"], rho_alt=alt["repeat_correlation"],
        corr_conditions=correlation_used, corr_conditions_raw=correlation, note=note,
        by_repeats=by_repeats,
    )


def analyze() -> dict:
    rows = [_analyze_row(row) for row in OBSERVED]
    planning = {}
    for model in ("haiku", "sonnet"):
        model_rows = [row for row in rows if row["model"] == model]
        # Conservative: the noisiest contrast at each repeat count. Also report the pitfalls contrast alone,
        # which is the glossary-versus-basic comparison.
        planning[model] = dict(contrasts=model_rows, by_repeats={})
        for repeats in (1, 2, 3, 5, 8):
            cells = [row["by_repeats"][repeats] for row in model_rows]
            noisiest = max(cells, key=lambda cell: cell["sigma"])
            pitfalls = next(row["by_repeats"][repeats] for row in model_rows if row["contrast"] == "pitfalls")
            planning[model]["by_repeats"][repeats] = dict(conservative=noisiest, pitfalls=pitfalls)
    return dict(n_questions=N_QUESTIONS, repeats_observed=REPEATS_OBSERVED, rows=rows, planning=planning)


def _pp(value: float) -> str:
    return f"{value * 100:.1f}"


def render_report(result: dict | None = None) -> str:
    result = result or analyze()
    lines = [
        "# Screen 2 power: questions and repeats for a paired effect",
        "",
        "Source: `research/heldout_results_1.md` (27 questions, 2 repeats, 4 configs, Haiku and Sonnet).",
        "The per-question rows were not kept. Everything below is recovered from the published accuracy, pass^2,",
        "and the question-level bootstrap intervals. Regenerate with `python -m bench.power`.",
        "",
        "## What was observed",
        "",
        "For two repeats, pass^2 is P(both correct) and accuracy is the mean of the question-level average.",
        "That pair identifies how often a question was right once, twice, or never, and so the variance of the",
        "question-level score. The repeat correlation is the usual exchangeable-trials correlation implied by that",
        "variance. The SD of the paired difference is the bootstrap half-width times sqrt(27) / 1.96 (a normal",
        "approximation to a percentile interval, which is slightly asymmetric in the writeup).",
        "",
        "| model | contrast | effect (pp) | 95% CI (pp) | SD of paired difference (pp) | repeat corr. base | repeat corr. alt | corr. of the two conditions |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in result["rows"]:
        lines.append(
            f"| {row['model']} | {row['contrast']} | {_pp(row['effect'])} | "
            f"[{_pp(row['ci'][0])}, {_pp(row['ci'][1])}] | {_pp(row['sd_paired'])} | "
            f"{row['rho_base']:.2f} | {row['rho_alt']:.2f} | {row['corr_conditions']:.2f} |")
        if row["note"]:
            lines.append(f"| | | | | | | | {row['note']} |")
    lines += [
        "",
        "Haiku's paired differences have an SD of about 35–42 points. Sonnet's are about 15–22 points.",
        "Repeat correlation is high on both models (a question that is right on one repeat is usually right on the",
        "other). The two conditions are positively correlated as well: many questions are solved, or missed, under",
        "both configs. The interval is wide because the questions that do flip dominate a set of 27.",
        "",
        "One published subtraction does not match to the last tenth of a point (Haiku pitfalls+procedural accuracy",
        "75.9 − 74.1 = 1.8, while the paired effect is printed as +1.9). That is rounding in the one-decimal table.",
        "The SD uses the printed interval, not the subtracted accuracies.",
        "",
        "## Planning model",
        "",
        "A new repeat count changes the within-question noise and not the covariance between conditions.",
        "Var(question mean) = p(1−p) · (1 + (r−1)ρ) / r, with ρ the repeat correlation above.",
        "Var(paired difference) = Var(base) + Var(alt) − 2 Cov, and Cov is held at the r=2 value.",
        "At r=2 this returns the observed SD. At r=1 it is wider. As r grows it floors at the between-question variance.",
        "",
        "Two sample sizes, both two-sided:",
        "",
        "- **80% power** at α=0.05 for a normal test of the paired mean. This is \"detect\".",
        "- **Expected CI excludes 0**: the n at which a true effect of that size has a 95% interval that just clears 0",
        "  in expectation. That is a lower bar (about 50% power). It is the bar in the held-out note's \"~80+ questions\",",
        "  and it is also the CI half of the learner's gate.",
        "",
        "The learner accepts a candidate when the effect is at least 5 points **or** the CI lower bound clears 0.",
        "The effect rule will accept a noisy +5 or +6 that the CI still covers. Until validation is large enough for",
        "the CI column below, treat an accept-on-effect-only as provisional.",
        "",
        "Numbers are the **conservative** SD for that model (the noisiest of the three contrasts) and, in the second",
        "table, the pitfalls-versus-base contrast alone.",
        "",
        "### Conservative (noisiest contrast)",
        "",
        "| model | repeats | SD (pp) | n for 5 pp, 80% | n for 10 pp, 80% | n for 5 pp, CI clears 0 | n for 10 pp, CI clears 0 |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += _planning_rows(result, "conservative")
    lines += [
        "",
        "### Pitfalls versus basic glossary",
        "",
        "| model | repeats | SD (pp) | n for 5 pp, 80% | n for 10 pp, 80% | n for 5 pp, CI clears 0 | n for 10 pp, CI clears 0 |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += _planning_rows(result, "pitfalls")
    haiku_r2 = result["planning"]["haiku"]["by_repeats"][2]["conservative"]
    haiku_r8 = result["planning"]["haiku"]["by_repeats"][8]["conservative"]
    sonnet_r2 = result["planning"]["sonnet"]["by_repeats"][2]["conservative"]
    lines += [
        "",
        "## What to run",
        "",
        f"- **10 points on Haiku, 80% power, 2 repeats:** about **{haiku_r2['n_80_for_10pp']}** questions.",
        f"  The expected interval clears 0 at about **{haiku_r2['n_ci_for_10pp']}**. The held-out note's \"~80+\"",
        "  sits on that second bar, not on 80% power.",
        f"- **5 points on Haiku, 80% power, 2 repeats:** about **{haiku_r2['n_80_for_5pp']}** questions.",
        "  A 5-point Haiku effect is not a realistic target for the next screen.",
        f"- **10 points on Sonnet, 80% power, 2 repeats:** about **{sonnet_r2['n_80_for_10pp']}** questions.",
        f"  **5 points:** about **{sonnet_r2['n_80_for_5pp']}**.",
        f"- **Repeats are a weak substitute for questions.** Going from 2 to 8 repeats moves Haiku's 10-point",
        f"  80% sample size from {haiku_r2['n_80_for_10pp']} to {haiku_r8['n_80_for_10pp']}. The repeat correlation",
        "  is high, so the second repeat already captured most of what repeats can capture. Use 2 repeats",
        "  (pass^2, as in the held-out run). A third repeat is optional; it is not the way to make a 5-point",
        "  Haiku effect detectable.",
        "- **Validation after round 3.** The frozen split puts 25 of the current 99 concrete questions in",
        "  validation. Round 3 is about 56 questions and the split manager sends a new, unlinked question to",
        "  validation. 25 + 56 = 81, which is the held-out note's bar for *seeing* a 10-point Haiku effect",
        f"  (CI clears 0 around {haiku_r2['n_ci_for_10pp']}) and is enough for a 10-point Sonnet effect at 80% power.",
        f"  It is not enough for 80% power on a 10-point Haiku effect ({haiku_r2['n_80_for_10pp']}).",
        "- **Do not unseal the test split to buy power.** Those 25 questions are the one-shot end run.",
        "",
        "## What this does not say",
        "",
        "- The normal approximation is not the bootstrap. The published intervals are percentile intervals and",
        "  some are asymmetric; the SD uses their width.",
        "- Binary questions are not normal at n=27. The n above is a planning number, not a promise that the",
        "  next bootstrap interval will have that width.",
        "- Power is for a paired difference of question-level means. It is not power for pass^k, and it is not",
        "  power for an effect that itself varies by question family.",
        "",
    ]
    return "\n".join(lines)


def _planning_rows(result: dict, which: str) -> list[str]:
    lines = []
    for model in ("haiku", "sonnet"):
        for repeats in (1, 2, 3, 5, 8):
            cell = result["planning"][model]["by_repeats"][repeats][which]
            lines.append(
                f"| {model} | {repeats} | {_pp(cell['sigma'])} | {cell['n_80_for_5pp']} | {cell['n_80_for_10pp']} | "
                f"{cell['n_ci_for_5pp']} | {cell['n_ci_for_10pp']} |")
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    text = render_report()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    print(args.out)


if __name__ == "__main__":
    main()
