"""CC analyzer: bundle B1 run by a Strands agent (``analyzer.strands_agent``).

The analyzer answers a billing question by querying one customer's lake through the request's opener
(``ccsim.query.open_session`` in the simulated backend). Model SQL always goes through ``bench.guard``. Escalation to
``lossless.*`` is a separate audited request that opens a new session; it is not a flag on the guard.
"""
