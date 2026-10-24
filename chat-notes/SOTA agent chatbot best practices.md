## User messages (verbatim)

### Message 1
First view the memory. This project tackles a classic problem in the recent years of agents and AI

Essentially: 
A chatbot interface with an agent on the backend running some data analysis


This task is EXTREMLY well documented and one of the most common tasks for agentic engineering. Please do a deep dive on the best practices and SOTA techniques for this type of project

### Message 2
A little picky, but can you remove the non-sota counter examples from the doc, like things pre 2025 especially, maybe even pre 2026 if you think so too

### Message 3
You mention the removal of legacy non-SOTA. This is just one way the doc becomes bloated, prune from the doc anything unnecesary. Focus on 1 or 2 running examples, like the openai agent

### Message 4
Don't focus the doc on claim clarity, prune that space and use the space to expand on the gerneal concensus for SOTA

### Message 5
REMOVE JARGON, PARTICULARLY INSTANCES LIKE"COVERAGE CLIFF"

### Message 6
would it be good practice to have 2 sets of schmas:

1. that is lossless, all data
2. and 1 that is optimized for most queries that the agent can easily understand

### Message 7
good message? Hey guys, I had an idea about the claimclarity/nba/parquet/duckdb data backend I think would be helpful.
 
It seems like both problems involve agents converting natural text from a user into a SQL query. From what I've learned today, its common practice to give the agent access to a 'semantic layer' in projects like this. 
 
Essentially if we have all of our data in some schema, the 'semantic layer' is a subset/curated version of that schema that is easy for the agent to understand and interact with. 
 
In our case, maybe we have a parquet of all of our data in one, lossless schema, but then another parquet that is a curated subset that can answer 90% of user queries, for example.

### Message 8
only return this part fixed:  
In our case, maybe we have a parquet of all of our data in one, lossless schema, but then another parquet that is a curated subset that can answer 90% of user queries, for example.

### Message 9
revisiting, I need a refresher on SOTA, there was the openai methodology, which im decently verson on, the paper which is confusing, and then snowlfake? what else?

## Notes

### Decided
- The best-practice write-up should cover only current (2025–2026) work, center on two running examples (OpenAI's in-house data agent and the August 2026 bounded-planning paper), describe the general consensus rather than ClaimClarity specifically, and use plain language with no jargon.
- Keeping two schemas is good practice: a lossless "source of truth" layer, plus a curated agent layer (semantic layer) built from it as views, with business definitions such as "denied" computed once and keys carried through so every number can be traced back.
- Pitch the semantic-layer idea to the team on Slack, using the dbt Labs 2026 result (90% to 98% accuracy) as evidence.

### Built
- A deep-research report on conversational analytics agents with a ClaimClarity gap analysis (Markdown artifact).
- A Claude Docs document, revised four times. The final version is "Conversational Analytics Agents: SOTA (2025–2026)", a plain-language summary of the general consensus.
- A revised Slack message proposing the lossless + curated agent layer, and a one-sentence fix to the user's own draft.
- A plain-language refresher comparing the bounded-planning paper, Snowflake Cortex Analyst, Databricks Genie, Microsoft Fabric, dbt and NVIDIA's DABstep entry.

### Left open
- Which tables the curated agent layer should have (the user planned to propose a 15-minute team talk).
- Whether the Slack message was sent, and in which version.
- ClaimClarity follow-ups from the first report that were dropped from the final doc: eval harness, handling of questions it can't answer, ADR-009 free-form SQL lane, per-tenant DuckDB, small-cell suppression, prompt-injection hygiene, and PHI-safe tracing.
