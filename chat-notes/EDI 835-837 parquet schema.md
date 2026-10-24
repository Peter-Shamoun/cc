<!-- STATUS: REFERENCE - your messages + what was decided/built/left open | source: chat "EDI 835/837 parquet schema" (2026-10-22) -->
## User messages (verbatim)

### Message 1
Given the Other conversations in the project, regarding the best practices for Agent Analyst Chatbots, it seems that data schema and context is paramount. 

In our case, our data is simple, and inherently carries context. It is 835 and 837p data. This data's labels are (semi) universal and generally mean the same thing everywhere. It will be important however, for the agent to query the data efficiently and accurately. 

 Our task is to make a parquet db schema of tables. This schema must be optimized for agentic use

This likely means you want to follow best practice standardization and atomic techniques. 

We also likely want to prioritize simplicity and inuitive-ness for the agent, so it doesnt need to heavily rely on docs, or send malformed calls to the db. 

the schema should most likely be lossless to the full edi data, unless you strongly disagree, and I think maybe should even carry some s3 data with it for engineers to examine

I dont believe we should limit our schema per agent interaction, we can have hooks and blocks that stop that later on 

Research, but importantly, do not come back with a research report. Come back with a db schema. I should be able to understand it easily, and understand why you made the decisions easily. Again, this is a chatbot to query all claim and remitance data from one customer, labcorps, 837 and 837 files. This schema would be duplicated in different bucvkets/folders for multitenancy across customers

### Message 2
html artifact of the schema

### Message 3
I don't really understnad a lot of the engineer-facing tables, add a way to filter for only agent facing

### Message 4
It is rediculous that there are 40 agent facing tables for 2 file types

I was expecting claim, claimline. remit, remitline. I know there are likely more intracies, example: batch and payer, but this is absolutley ridiculous

example, not saying to copy this or mimic it: AI_APPEAL_SFTP_FILE

Tracks every file received from S3.

Column	Type	Constraints	Description
PK_SEQ_ID	NUMBER(28)	PK, sequence	Auto-generated primary key
S3_URI	VARCHAR2(1000)	UNIQUE, NOT NULL	Full S3 path; doubles as idempotency key
FILE_TYPE	VARCHAR2(3)	NOT NULL	'835' or '837'
PARSED_S3_URI	VARCHAR2(1000)	UNIQUE, NOT NULL	The S3 path of the parsed EDI file
UPLOAD_DT	DATE	NOT NULL	When the file appeared in S3
PARSED_DT	DATE	 	When the file was parsed
B_PARSED	NUMBER(1)	NOT NULL, CHECK (0/1)	1 = parsed successfully
ERR_MSG	VARCHAR2(4000)	 	Null on success; error detail on failure
AUD_USER	VARCHAR2(50)	 	 
AUD_DT	DATE	DEFAULT SYSDATE	 
AUD_REC_ID	NUMBER(10)	 	 
 

AI_APPEAL_SFTP_CLAIM_BATCH

One row per batch within a 837

Column	Type	Constraints	Description
PK_SEQ_ID	NUMBER(28)	PK, sequence	Auto-generated primary key
FK_AI_APPEAL_SFTP_FILE_ID	NUMBER(28)	FK → FILE, NOT NULL	Source file
PYR_NM	VARCHAR2(100)	NOT NULL	Payor name/identifier — join key; NM1*PR segment, NM1-03
PYR_EAR_ID	VARCHAR2(100)	 	REF*2U segment, REF-02
AUD_USER	VARCHAR2(50)	 	 
AUD_DT	DATE	DEFAULT SYSDATE	 
AUD_REC_ID	NUMBER(10)	 	 
AI_APPEAL_SFTP_CLAIM

Individual claims parsed from 837 files. One row per CLM01 within a batch.

Column	Type	Constraints	Description
PK_SEQ_ID	NUMBER(28)	PK, sequence	Auto-generated primary key
FK_AI_APPEAL_SFTP_CLAIM_BATCH_ID	NUMBER(28)	FK → FILE, NOT NULL	Source file batch
PTNT_CTRL_NO	VARCHAR2(50)	NOT NULL	CLM01 from the 837 — join key
START_INDEX	VARCHAR2(100)	NOT NULL	The start position of the claim within the parsed SFTP file
END_INDEX	VARCHAR2(100)	NOT NULL	The end position of the claim within the parsed SFTP file
AUD_USER	VARCHAR2(50)	 	 
AUD_DT	DATE	DEFAULT SYSDATE	 
AUD_REC_ID	NUMBER(10)	 	 
 

AI_APPEAL_SFTP_REMIT_BATCH

One row per batch within an 835

Column	Type	Constraints	Description
PK_SEQ_ID	NUMBER(28)	PK, sequence	Auto-generated primary key
FK_AI_APPEAL_SFTP_FILE_ID	NUMBER(28)	FK → FILE, NOT NULL	Source file
PYR_NM	VARCHAR2(100)	NOT NULL	Payor name/identifier — join key
PYR_EAR_ID	VARCHAR2(100)	 	REF*2U segment, REF-02
PYR_TAX_ID	VARCHAR2(100)	 	TRN-03
AUD_USER	VARCHAR2(50)	 	 
AUD_DT	DATE	DEFAULT SYSDATE	 
AUD_REC_ID	NUMBER(10)	 	 
AI_APPEAL_SFTP_REMIT

Individual remittances parsed from 835 files. One row per ICN within a batch.

Column	Type	Constraints	Description
PK_SEQ_ID	NUMBER(28)	PK, sequence	Auto-generated primary key
FK_AI_APPEAL_SFTP_REMIT_BATCH_ID	NUMBER(28)	FK → FILE, NOT NULL	Source file batch
ICN	VARCHAR2(50)	 	Internal Control Number
STATUS_CODE	NUMBER(10)	 	Claim status code; 1=initial, 22=reversal
PTNT_CTRL_NO	VARCHAR2(50)	NOT NULL	Patient Control Number — join key
START_INDEX	VARCHAR2(100)	NOT NULL	The start index of the remit within the parsed SFTP file 
END_INDEX	VARCHAR2(100)	NOT NULL	The end index of the remit within the parsed SFTP file
AUD_USER	VARCHAR2(50)	 	 
AUD_DT	DATE	DEFAULT SYSDATE	 
AUD_REC_ID	NUMBER(10)	 	 
 

AI_APPEAL_SFTP_CANDIDATE

Identified appeal candidates. Each row is a claim+remit pair that passed criteria filtering.

Column	Type	Constraints	Description
PK_SEQ_ID	NUMBER(28)	PK, sequence	Auto-generated primary key
FK_AI_APPEAL_SFTP_CLAIM_ID	NUMBER(28)	FK → CLAIM, NOT NULL	The matched claim
FK_AI_APPEAL_SFTP_REMIT_ID	NUMBER(28)	FK → REMIT, NOT NULL, UNIQUE	The matched remit (unique — each remit used at most once)
CANDIDATE_BATCH_ID	VARCHAR2(50)	NOT NULL	Groups candidates from the same identification run
IDENTIFIED_DT	DATE	NOT NULL	When this candidate was identified
B_EXPORTED	NUMBER(1)	NOT NULL, CHECK (0/1), DEFAULT 0	1 after CSV is generated and uploaded
AUD_USER	VARCHAR2(50)	 	 
AUD_DT	DATE	DEFAULT SYSDATE	 
AUD_REC_ID	NUMBER(10)

### Message 5
to be honest im starting to think this is just the schema we should choose, with the rest of the details as columns instead of the start and stop byte index:
Requested Tables
AI_APPEAL_SFTP_FILE

Tracks every file received from S3.

Column	Type	Constraints	Description
PK_SEQ_ID	NUMBER(28)	PK, sequence	Auto-generated primary key
S3_URI	VARCHAR2(1000)	UNIQUE, NOT NULL	Full S3 path; doubles as idempotency key
FILE_TYPE	VARCHAR2(3)	NOT NULL	'835' or '837'
PARSED_S3_URI	VARCHAR2(1000)	UNIQUE, NOT NULL	The S3 path of the parsed EDI file
UPLOAD_DT	DATE	NOT NULL	When the file appeared in S3
PARSED_DT	DATE	 	When the file was parsed
B_PARSED	NUMBER(1)	NOT NULL, CHECK (0/1)	1 = parsed successfully
ERR_MSG	VARCHAR2(4000)	 	Null on success; error detail on failure
AUD_USER	VARCHAR2(50)	 	 
AUD_DT	DATE	DEFAULT SYSDATE	 
AUD_REC_ID	NUMBER(10)	 	 
 

AI_APPEAL_SFTP_CLAIM_BATCH

One row per batch within a 837

Column	Type	Constraints	Description
PK_SEQ_ID	NUMBER(28)	PK, sequence	Auto-generated primary key
FK_AI_APPEAL_SFTP_FILE_ID	NUMBER(28)	FK → FILE, NOT NULL	Source file
PYR_NM	VARCHAR2(100)	NOT NULL	Payor name/identifier — join key; NM1*PR segment, NM1-03
PYR_EAR_ID	VARCHAR2(100)	 	REF*2U segment, REF-02
AUD_USER	VARCHAR2(50)	 	 
AUD_DT	DATE	DEFAULT SYSDATE	 
AUD_REC_ID	NUMBER(10)	 	 
AI_APPEAL_SFTP_CLAIM

Individual claims parsed from 837 files. One row per CLM01 within a batch.

Column	Type	Constraints	Description
PK_SEQ_ID	NUMBER(28)	PK, sequence	Auto-generated primary key
FK_AI_APPEAL_SFTP_CLAIM_BATCH_ID	NUMBER(28)	FK → FILE, NOT NULL	Source file batch
PTNT_CTRL_NO	VARCHAR2(50)	NOT NULL	CLM01 from the 837 — join key
START_INDEX	VARCHAR2(100)	NOT NULL	The start position of the claim within the parsed SFTP file
END_INDEX	VARCHAR2(100)	NOT NULL	The end position of the claim within the parsed SFTP file
AUD_USER	VARCHAR2(50)	 	 
AUD_DT	DATE	DEFAULT SYSDATE	 
AUD_REC_ID	NUMBER(10)	 	 
 

AI_APPEAL_SFTP_REMIT_BATCH

One row per batch within an 835

Column	Type	Constraints	Description
PK_SEQ_ID	NUMBER(28)	PK, sequence	Auto-generated primary key
FK_AI_APPEAL_SFTP_FILE_ID	NUMBER(28)	FK → FILE, NOT NULL	Source file
PYR_NM	VARCHAR2(100)	NOT NULL	Payor name/identifier — join key
PYR_EAR_ID	VARCHAR2(100)	 	REF*2U segment, REF-02
PYR_TAX_ID	VARCHAR2(100)	 	TRN-03
AUD_USER	VARCHAR2(50)	 	 
AUD_DT	DATE	DEFAULT SYSDATE	 
AUD_REC_ID	NUMBER(10)	 	 
AI_APPEAL_SFTP_REMIT

Individual remittances parsed from 835 files. One row per ICN within a batch.

Column	Type	Constraints	Description
PK_SEQ_ID	NUMBER(28)	PK, sequence	Auto-generated primary key
FK_AI_APPEAL_SFTP_REMIT_BATCH_ID	NUMBER(28)	FK → FILE, NOT NULL	Source file batch
ICN	VARCHAR2(50)	 	Internal Control Number
STATUS_CODE	NUMBER(10)	 	Claim status code; 1=initial, 22=reversal
PTNT_CTRL_NO	VARCHAR2(50)	NOT NULL	Patient Control Number — join key
START_INDEX	VARCHAR2(100)	NOT NULL	The start index of the remit within the parsed SFTP file 
END_INDEX	VARCHAR2(100)	NOT NULL	The end index of the remit within the parsed SFTP file
AUD_USER	VARCHAR2(50)	 	 
AUD_DT	DATE	DEFAULT SYSDATE	 
AUD_REC_ID	NUMBER(10)	 	 
 

AI_APPEAL_SFTP_CANDIDATE

Identified appeal candidates. Each row is a claim+remit pair that passed criteria filtering.

Column	Type	Constraints	Description
PK_SEQ_ID	NUMBER(28)	PK, sequence	Auto-generated primary key
FK_AI_APPEAL_SFTP_CLAIM_ID	NUMBER(28)	FK → CLAIM, NOT NULL	The matched claim
FK_AI_APPEAL_SFTP_REMIT_ID	NUMBER(28)	FK → REMIT, NOT NULL, UNIQUE	The matched remit (unique — each remit used at most once)
CANDIDATE_BATCH_ID	VARCHAR2(50)	NOT NULL	Groups candidates from the same identification run
IDENTIFIED_DT	DATE	NOT NULL	When this candidate was identified
B_EXPORTED	NUMBER(1)	NOT NULL, CHECK (0/1), DEFAULT 0	1 after CSV is generated and uploaded
AUD_USER	VARCHAR2(50)	 	 
AUD_DT	DATE	DEFAULT SYSDATE	 
AUD_REC_ID	NUMBER(10)

### Message 6
Okay so why would this be fine:

file table

claim batch table
claim table
claim service line table

Remit batch table
Remit table
Remit service line table

### Message 7
Okay, so it looks like Adjustment reasons is another table (or 2?)

from that, can you make the 8-9 table schema?

### Message 8
can you draw this as an entity relationship html? I believe the only maps are claim to remit on PCN, and to respective batches, adn to file? maybe you can connect mroe like SVC, correct me if im wrong

### Message 9
Few things, One, I dont understand why the batch tables dont match the batch tables I pasted exactly. Im not saying youre wrong, but why did you make the change? 

What is remit_batch_adjustment? I thought an adjustment was a remitance. 

What is remit_adjustment

And why are you adding fallbacks to the matches for the joins? Are you saying that there are cases where the match fails on its designated fields? These fallbacks

### Message 10
PYR_EAR_ID is completely missing from Claim_batch in the entity relationship wdym??

### Message 11
Okay correction. Here is what has happened

You proposed some schema, which was lossless but verbose, I wanted it limited to be easy to understand and interact with

However, limiting it makes it lossy

here is the real solution: We need 2 schemas. 1 Db schema, and 1 schema which is a view, or a curated subset, built from the original lossless  schema, common practice for agent interaction

The one crumble: I still feel that your semantic layer, the subset, is not correct. it is very unconvincing

I feel that the original lossless schema as likely fine, but the semantic layer likely needs revisting.

### Message 12
Well the agent can access lossless if it determines that it can not answer the question with the semantic layer

### Message 13
Let me see the semantic layer

## Notes

**Decided**
- Use two layers. `edi` is the lossless storage schema: the original roughly 37-40 table normalized X12 design, which the user said is "likely fine". `analytics` is a semantic layer built from it, and the agent queries it by default.
- The agent may drop down to `edi` when `analytics` can't answer. It enters through the source ID columns, never recomputes money metrics in `edi`, says when it escalated, and logs each escalation. Every `edi` column needs a COMMENT ON.
- Rejected along the way: the 40-table agent surface ("ridiculous" for two file types), then the file-shaped 8- and 9-table subsets modeled on the AI_APPEAL_SFTP tables. The user found them unconvincing as a semantic layer.
- 837 and 835 are matched at ingest and the result is stored. The agent joins on IDs, not on PCN directly. Claim matching uses PCN plus payer and charge. Line matching uses REF*6R, falling back to CPT, date and charge.
- The denial rule is reused from existing work: CLP02 = 4, or denial signal v3 over the 23 reviewed CARCs.

**Built**
- A research-task design doc (markdown): the lossless Parquet schema v1, with roughly 40 tables, ref tables, views, S3 layout and example SQL.
- The "CC Schema v1" HTML artifact (https://claude.ai/artifact/CrmSTvUNfZRJpjXjkHZvzB), overwritten in place several times: a 48-table catalog with an agent/engineer filter, then 8 tables, then 9 tables, and finally two layers. The final `analytics` layer has 7 tables (claims, claim_lines, claim_events, adjustments, deposits, deposit_adjustments, payers) plus metric definitions, the rules it settles, and the edi escalation guidance.
- The "CC ER Diagram" HTML artifact for the 9-table design, with payer_secondary_id (PYR_EAR_ID) added.
- Updated project memory (implementations-neo4j-chatbot.md) with the two-layer decision and the escalation rule.

**Left open**
- Claim grain: is a claim one PCN per payer, so primary and secondary count as two claims?
- The denial definition and the list of contractual CO reasons (45, 253) need an owner.
- Someone has to curate `payer_alias` per tenant, and `carc_category` has to be built.
- The ER diagram was never redrawn for the two-layer design.
- The user asked to see the semantic layer at the end, and Claude reopened the page. The chat ends there, with no verdict on whether the revised semantic layer is convincing.
- X12 qualifier names should be checked against the licensed TR3, and the match rules tuned on real Labcorp data.
