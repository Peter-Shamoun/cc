<!-- STATUS: REFERENCE - Slack draft; whether/which version was sent is unknown | source: chat "SOTA agent chatbot best practices" (2026-10-23) | FLAG: blockquote markers stripped -->
Hey all, I had an idea for the CC / NBA data backend (Parquet + DuckDB).

Both projects have agents turning a user's question into a SQL query. From what I've been reading, the common practice for this is to give the agent a "semantic layer" instead of the raw tables. That's a small, curated set of tables with clear names and business definitions (e.g., "denied" computed once in one place) that's easy for the agent to understand.

For us, that could look like:
• **Full schema:** all our data, lossless, traceable back to the S3 files. This is the source of truth for engineers.
• **Agent layer:** a few simple tables or views built automatically from the full schema, designed to answer most user questions (say ~90%).

Because the agent layer is generated from the full data, the two can't drift apart, and every number can still be traced back. One recent benchmark (dbt Labs, 2026) found the same model went from 90% to 98% accuracy when it queried a semantic layer instead of writing SQL against the raw tables.

Would it be worth 15 minutes to talk through what tables the agent layer should have?
