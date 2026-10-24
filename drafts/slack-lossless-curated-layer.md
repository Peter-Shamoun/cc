<!-- STATUS: REFERENCE - Slack draft; whether/which version was sent is unknown | source: chat "SOTA agent chatbot best practices" (2026-10-23) | FLAG: assembled: your draft with its last paragraph replaced by the rewrite; posted version unknown -->
Hey guys, I had an idea about the cc/nba/parquet/duckdb data backend I think would be helpful.

It seems like both problems involve agents converting natural text from a user into a SQL query. From what I've learned today, its common practice to give the agent access to a 'semantic layer' in projects like this.

Essentially if we have all of our data in some schema, the 'semantic layer' is a subset/curated version of that schema that is easy for the agent to understand and interact with.

In our case, we could keep all our data in one lossless Parquet schema and build a curated layer from it, a few simple tables or views generated automatically, that can answer about 90% of user queries.
