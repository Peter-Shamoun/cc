"""Token cost times N, plus DuckDB-on-S3 cost of the 120-day lake.

Lake numbers are the published winner row, not a re-measurement. Each production request
(STS, CURRENT, manifest, view bind, query) at 120 business days:

- median 50 requests, median 3.34 MB fetched
- S3 model median 0.17 s, max 0.51 s

Source: ``docs/data-backend/lake-optimization.md`` section 3, agent-workload table, 120-day winner.
The S3 model elsewhere in that doc is ``requests × 25 ms / parallelism + bytes / 100 MB/s`` with
parallelism 8 for a DuckDB query. Combining the *median* request count with the *median* bytes
does not reproduce the reported median latency, because the median of the per-query model is not
the model of the medians. Latency below uses the reported 0.17 s / 0.51 s figures.

Prices, from ``research/technique_factors.yaml`` (T26) and the technique catalog (T19, T25):

- Sonnet 5.5 cache reads are $0.20 / MTok, which is 0.1× input, so input is $2.00 / MTok.
- Cache writes are 1.25× input (5-minute cache).
- Sonnet 5.5 output is $10 / MTok (catalog T19).
- Haiku 5.5 input is about 20× cheaper (T25). Haiku output is not priced in the repo; this module
  applies the same 20× ratio ($0.50 / MTok) and marks it as an assumption.
"""
from __future__ import annotations

from dataclasses import dataclass

LAKE_120D_MEDIAN_REQUESTS = 50
LAKE_120D_MEDIAN_MB = 3.34
LAKE_120D_S3_MEDIAN_S = 0.17
LAKE_120D_S3_MAX_S = 0.51
S3_MS_PER_REQUEST = 25
S3_QUERY_PARALLELISM = 8
S3_MB_PER_SECOND = 100

# USD per million tokens.
PRICES = {
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "output_assumed": False},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "output_assumed": True},
}
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_5M_MULTIPLIER = 1.25


@dataclass(frozen=True)
class TokenSketch:
    """One candidate's uncached tail, plus the shared prefix. Defaults are a short one-shot: the
    schema prefix is measured in the dry run and overridden there; these defaults are the sketch
    the unit test locks."""

    prefix_tokens: int = 4000
    uncached_input_tokens: int = 500
    output_tokens: int = 800
    selector_input_tokens: int = 1500
    selector_output_tokens: int = 200


def lake_query_latency(n_queries: int, *, parallel: bool) -> dict:
    """N production requests against the 120-day winner layout.

    ``parallel=False`` is one session, candidates one after another.
    ``parallel=True`` is one session per candidate at the same time: wall-clock stays at one
    query's S3-model time, and the request count still multiplies. That assumes S3 accepts the
    concurrent requests. The in-process runner is sequential because it holds one connection.
    """
    if n_queries < 0:
        raise ValueError(n_queries)
    return {
        "queries": n_queries,
        "requests": n_queries * LAKE_120D_MEDIAN_REQUESTS,
        "mb_fetched": round(n_queries * LAKE_120D_MEDIAN_MB, 2),
        "latency_s": LAKE_120D_S3_MEDIAN_S if parallel else round(n_queries * LAKE_120D_S3_MEDIAN_S, 4),
        "latency_max_s": LAKE_120D_S3_MAX_S if parallel else round(n_queries * LAKE_120D_S3_MAX_S, 4),
        "parallel": parallel,
    }


def formula_latency_s(requests: int, mb: float) -> float:
    """The doc's formula applied to a single (requests, MB) pair. Not the median of per-query models."""
    return requests * (S3_MS_PER_REQUEST / 1000) / S3_QUERY_PARALLELISM + mb / S3_MB_PER_SECOND


def _usd(tokens: int, price_per_million: float) -> float:
    return tokens * price_per_million / 1_000_000


def llm_cost_usd(
    n: int,
    model: str,
    sketch: TokenSketch | None = None,
    *,
    warm_cache: bool = True,
    selector: bool = False,
    selector_model: str = "claude-sonnet-5-5",
) -> dict:
    """Dollar cost of N candidates that share a prefix.

    ``warm_cache`` is a prefetch (or a staggered start) so the first request writes the prefix and
    the other N-1 read it. Firing all N at once on a cold cache misses: every candidate pays full
    input on the prefix. Parallelism and cache sharing are not the same thing.
    """
    if model not in PRICES:
        raise KeyError(model)
    sketch = sketch or TokenSketch()
    price = PRICES[model]
    if warm_cache:
        prefix_usd = _usd(sketch.prefix_tokens, price["input"] * CACHE_WRITE_5M_MULTIPLIER)
        if n > 1:
            prefix_usd += _usd(sketch.prefix_tokens * (n - 1), price["input"] * CACHE_READ_MULTIPLIER)
    else:
        prefix_usd = _usd(sketch.prefix_tokens * n, price["input"])
    uncached_usd = _usd(sketch.uncached_input_tokens * n, price["input"])
    output_usd = _usd(sketch.output_tokens * n, price["output"])
    selector_usd = 0.0
    if selector:
        selector_price = PRICES[selector_model]
        selector_usd = (
            _usd(sketch.selector_input_tokens, selector_price["input"])
            + _usd(sketch.selector_output_tokens, selector_price["output"])
        )
    total = prefix_usd + uncached_usd + output_usd + selector_usd
    return {
        "model": model,
        "n": n,
        "warm_cache": warm_cache,
        "selector": selector,
        "selector_model": selector_model if selector else None,
        "output_price_assumed": price["output_assumed"] or (selector and PRICES[selector_model]["output_assumed"]),
        "prefix_usd": round(prefix_usd, 6),
        "uncached_input_usd": round(uncached_usd, 6),
        "output_usd": round(output_usd, 6),
        "selector_usd": round(selector_usd, 6),
        "total_usd": round(total, 6),
        "input_tokens": sketch.prefix_tokens * n + sketch.uncached_input_tokens * n + (sketch.selector_input_tokens if selector else 0),
        "output_tokens": sketch.output_tokens * n + (sketch.selector_output_tokens if selector else 0),
    }


def estimate(
    n: int,
    model: str,
    *,
    queries_per_candidate: int = 1,
    parallel_queries: bool = True,
    warm_cache: bool = True,
    selector_on_disagreement: bool = True,
    sketch: TokenSketch | None = None,
    selector_model: str = "claude-sonnet-5-5",
) -> dict:
    """N candidates. Lake latency is per the 120-day winner. Selector cost is the disagreement case
    (one extra Sonnet call, no extra lake query: it reads the digests the candidates already produced)."""
    queries = n * queries_per_candidate
    lake = lake_query_latency(queries, parallel=parallel_queries)
    tokens = llm_cost_usd(
        n, model, sketch, warm_cache=warm_cache, selector=selector_on_disagreement, selector_model=selector_model,
    )
    single = llm_cost_usd(1, model, sketch, warm_cache=warm_cache, selector=False)
    return {
        "n": n,
        "model": model,
        "queries_per_candidate": queries_per_candidate,
        "lake": lake,
        "llm": tokens,
        "single_candidate_llm_usd": single["total_usd"],
        "llm_multiple_of_single": round(tokens["total_usd"] / single["total_usd"], 3) if single["total_usd"] else None,
    }


def main() -> None:
    sketch = TokenSketch()
    for model in ("claude-haiku-5-5", "claude-sonnet-5-5"):
        for n in (1, 3, 5):
            for parallel in (True, False):
                row = estimate(n, model, parallel_queries=parallel, selector_on_disagreement=(n > 1))
                lake = row["lake"]
                print(
                    f"{model} N={n} parallel={parallel}: "
                    f"lake {lake['latency_s']}s median ({lake['requests']} requests, {lake['mb_fetched']} MB), "
                    f"LLM ${row['llm']['total_usd']} ({row['llm_multiple_of_single']}× one candidate)"
                )


if __name__ == "__main__":
    main()
