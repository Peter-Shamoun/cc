<!-- STATUS: LEGACY - duplicate rebuilt from chat edits; the exact export is research/agent-chatbot-best-practices.md | source: chat "SOTA agent chatbot best practices" (2026-10-23) | FLAG: SUPERSEDED/cross-check: the doc rebuilt from the chat edits; the exact export is research/agent-chatbot-best-practices.md -->
# Conversational Analytics Agents: SOTA (2025–2026)

2026-10-22 · @me

The 2026 consensus for chatbots that analyze data: let the model decide *what* to calculate, let tested code decide *how*, and expect most accuracy gains to come from good background information and regular testing rather than from a better model.

## Two running examples

The two strongest 2026 designs sit at opposite ends: one gives the model freedom, the other keeps it tightly controlled. Each section below shows how both handle the topic.

**OpenAI's in-house data agent** (Jan 2026) is the flexible one. It serves 3.5k+ employees across 70k datasets and writes its own SQL, guided by a large body of background notes about the data. OpenAI's lessons: give it less but better information, describe the goal rather than the steps, and look in the code that builds a table to learn what it means.

**The bounded-planning system** (arXiv, Aug 2026) is the controlled one. The model only chooses from a short list of pre-defined options and never writes SQL; ordinary code builds the query. Asked 38 questions three times each, it was right every time on 97.4% of them, vs. 55.3% when the model wrote SQL directly. It never returned a wrong answer silently; the direct approach did so 29 times.

| | OpenAI data agent | Bounded-planning system |
| --- | --- | --- |
| Who writes the query | The model | Code |
| Best for | Open-ended exploration of large, varied data | Standard business numbers where a mistake is costly |
| Questions it wasn't set up for | Tries to answer them | Declines |
| Unclear questions | Asks, or makes a stated assumption | Declines or asks |
| How it improves | Saves corrections users confirm | Analysts add new definitions |
| Typical mistake | Query runs but joins or counts the data wrong | Can only answer what someone defined in advance |

## How the systems are structured

Products settled on several levels, from most controlled to most open. Each answer is labeled with the level it came from, and each level is tested separately.

1. **Pre-approved queries:** tested queries where the model only fills in values like a date range (Databricks Genie, Power BI).
2. **Defined metrics:** the model picks which measures, groupings and filters to use, and software writes the SQL (dbt, Looker, Snowflake). The bounded-planning system works here.
3. **Model-written SQL:** the model writes the query, which is checked and then run in an isolated environment. OpenAI's agent works here.
4. **Model-written code:** the model writes and reruns Python or SQL step by step, usually for analysts.

The evidence favors the controlled levels for standard numbers. In dbt Labs' April 2026 test, Claude Sonnet 4.6 was right 90.0% of the time writing SQL itself and 98.2% using defined metrics; GPT-5.3-Codex went from 84.1% to 100%. The cost is reach: defined metrics only answer what someone set up in advance.

Two habits connect the levels:

- **Move to a more open level only when needed, and say so.** A question goes to model-written SQL only when no approved query or metric fits, and the answer is labeled as less verified.
- **Turn repeat questions into approved queries.** NVIDIA's winning entry on the DABstep benchmark (89.95 on hard tasks, using Claude Haiku 4.5) built a library of reusable functions in advance, then answered quickly with them.

**Agents get hard limits** on how many steps, tool calls and seconds they may use. MCP, the common standard for connecting AI to tools, is now how most data tools are exposed (OpenAI, Snowflake, Databricks and DuckDB all offer it). Controlled setups expose specific actions through it, not open SQL access.

## Giving the model the right background

Every team that published 2026 results says the background information given to the model matters more than which model they use.

**OpenAI's agent draws on six sources:**

1. Which tables people actually use and combine.
2. Notes people have written about tables and columns.
3. The code that builds each table, which shows what its numbers mean.
4. Internal docs and chat, limited to what the user may see.
5. Corrections users have confirmed, saved for everyone or just that user.
6. A live look at the database at question time.

**The bounded-planning system** gets there differently: its list of defined options *is* the background, so the model only ever sees valid choices for the question at hand.

What the rest of the field adds:

- **Worked examples beat descriptions.** One Databricks team stalled at 54% accuracy after documenting every table and column; adding checked example questions with their correct queries closed the gap. Microsoft Fabric looks up the 3 most similar checked examples for each new question.
- **Match the user's words to real values first.** If a user types a nickname for a company, find the stored name before building the query; a wrong value often returns a wrong answer with no error.
- **Keep each agent's subject narrow.** Microsoft and Databricks both advise one focused topic per agent rather than one agent for everything.
- **Less is more.** OpenAI found that trimming the information and stating goals rather than steps improved results.

## Unclear questions and follow-ups

Back-and-forth conversation is where these agents struggle most. On BIRD-Interact (2026), a test built around unclear questions and follow-ups, GPT-5 finished 8.67% of tasks in chat mode and 17.00% when allowed to act on its own.

- **Ask only when the answer would change.** OpenAI's agent asks a clarifying question, but if no reply comes it picks a sensible default (such as the last 30 days) and says so.
- **"I can't answer that" is a valid answer.** The bounded-planning system declines rather than guesses. Good products suggest questions they can answer and record what was asked, so the gap can be filled later.
- **Follow-ups edit the last plan.** "Same thing, by quarter" should change the previous request's settings, not start over from the chat text.
- **Let users steer.** OpenAI's agent can be interrupted mid-answer and shows its working, so users can spot a wrong assumption.

## Running queries safely and reporting honestly

The real danger isn't a query that errors out; it's one that runs and returns a believable wrong number. The bounded-planning system cut those from 29 to 0, which is the clearest measure of what the controlled approach buys.

**Before running a model-written query:**

- Confirm it only reads data, and only from approved tables and columns.
- Check that every name exists and that the database accepts the query before running it for real.
- Limit how many rows, how much time and how much memory it can use.
- Run it somewhere it can't reach other files or the network. DuckDB, for example, has no built-in user permissions, so its docs say to switch off outside access and lock its settings, and to run it in its own container.

**After running:**

- **Question surprising results.** OpenAI's agent treats an empty result as a likely mistake in the query and retries before reporting "no data".
- **Don't let the model retype numbers.** Insert figures straight from the results into the reply, so the model can't misstate them.
- **Build charts from fixed templates**, not from code the model writes.
- **Show how the answer was built.** Display the steps or query used, and label checked vs. model-written answers.

## Testing and monitoring

Every system above relies on its own set of test questions, rerun after every change. Public benchmarks are no substitute.

**Why not public leaderboards:** top scores are close to the maximum (96.70 on Spider 2.0-Snow, 81.67% on BIRD), yet a January 2026 review found wrong answer keys in 52.8% of one BIRD set and 62.8% of Spider 2.0-Snow; rescoring moved agents up to 9 places. Meanwhile the best agent gets 11.4% right on BEAVER, which uses real company databases.

**What to track:**

1. **Correct answers** on a set of questions with known right results, judged by the result rather than whether the SQL matches word for word (OpenAI checks both).
2. **Silent wrong answers**, counted apart from refusals.
3. **How often it declines or asks** for clarification.
4. **Backsliding:** questions it used to get right but now gets wrong (Snowflake's 2026 testing tool reports this directly).
5. **User feedback**, used to improve the background information.

**How:** ask each question several times (3 for the bounded-planning study, 20 for dbt), since the same question can get different answers. Include test data deliberately changed so a wrong query can't match by luck. Don't release a change that breaks a previously passing question.

**Logging each step:** OpenTelemetry's standard for AI apps records every agent step, tool call and model call, and is supported by Langfuse, Arize Phoenix, Braintrust and LangSmith. The text of prompts and replies is kept separately so it can be dropped before logs leave your systems.

## Sensitive data

For health or financial data, the consensus is that privacy rules belong in the system's code and setup, never in instructions to the model.

- **Keep each customer's data physically separate.** If the database has no user permissions of its own (like DuckDB), give each customer separate storage and access keys, so a bad query can't reach anyone else's data.
- **Hide small groups, including ones that can be worked out.** Medicare's data rules bar showing any group of 10 or fewer, or any percentage or calculation that reveals one. If a total would let someone subtract their way to a hidden number, hide another number too.
- **Treat text stored in the data as untrusted.** The P2SQL study (2025) showed that instructions planted inside database records can take over the model when other users' queries return those records, and telling the model to ignore them didn't work. Remove or clearly mark free text, and check every model choice in code.
- **Send the model as little as possible.** Pass it table descriptions, plans and summaries rather than raw records wherever the task allows.
- **Know how the model provider handles data.** AWS says Bedrock can be used for health data under a signed HIPAA agreement and does not store or train on prompts, but its optional request logging creates a new copy of sensitive data if switched on. Strip message text from logs before they go to outside monitoring tools.

## Sources

- [OpenAI: Inside our in-house data agent (Jan 2026)](https://openai.com/index/inside-our-in-house-data-agent/)
- [Bounded Semantic Planning and Deterministic Compilation (arXiv, Aug 2026)](https://arxiv.org/pdf/2608.16663)
- [dbt Labs: Semantic Layer vs. text-to-SQL (2026)](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026)
- [Databricks: Production-ready Genie spaces](https://www.databricks.com/blog/how-build-production-ready-genie-spaces-and-build-trust-along-way)
- [Microsoft Fabric: Data agent example queries](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-example-queries)
- [Snowflake: Cortex Analyst Evaluations](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst-evaluations)
- [NVIDIA: DABstep first place](https://huggingface.co/blog/nvidia/nemo-agent-toolkit-data-explorer-dabstep-1st-place)
- [BIRD-Interact](https://www.alphaxiv.org/abs/2510.05318) · [BEAVER results (arXiv, 2026)](https://arxiv.org/pdf/2608.27796)
- [Spider 2.0 leaderboard audit](https://www.nl2sql.ai/news/spider2-live-leaderboard-public-repo-audit-gap) · [Genloop: Spider 2.0 (Mar 2026)](https://genloop.ai/blogs/genloop-is-1-on-spider-2.0)
- [OpenTelemetry GenAI conventions in 2026](https://dev.to/gabrielanhaia/opentelemetry-genai-semantic-conventions-your-llm-traces-should-look-like-this-in-2026-3ff6)
- [DuckDB: Securing DuckDB](https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview)
- [P2SQL: prompt-to-SQL injection (ICSE 2025)](https://arxiv.org/pdf/2308.01990v4)
- [ResDAC: CMS cell size suppression](https://www.resdac.org/articles/cms-cell-size-suppression-policy)
- [AWS: Bedrock data protection](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html)

Some figures come from the companies selling the product (dbt's test, Genloop's score). The bounded-planning authors note their system also had better background information, so the gap isn't due to code-built queries alone.
