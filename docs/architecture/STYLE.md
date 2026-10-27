# Architecture docs: conventions (read before drawing or writing)

Audience: the company's IT and security reviewers. They should be able to build, secure and operate the system
from these pages. Describe the architecture as designed, in the present tense ("the parser task reads..."), as
one system.

## Never write

- "simulated", "simulator", "ccsim", "mock", "prototype", "not built", "planned" or any other build-status words.
  Exception: the overview page has one sentence about the demo data set.
- Internal names: "B1", bundle names, benchmark run names, "claude -p", "grok". Call the agent "the analyzer"
  and its two paths "the standard KPI path" and "the custom analysis path" (the free-SQL agent).
- Deployment details: regions, account ids, profile names, ARNs with ids, CIDRs, bucket or role names with
  environment suffixes. Use logical names instead: "the ClaimClarity bucket", "Customer B's role", "Customer B's key".
- Model ids. Say "Claude Haiku on Amazon Bedrock".

## Facts fixed for all pages

- Compute: Amazon ECS on AWS Fargate for the chat API, the run workers and the parser. Not EC2.
- One S3 bucket. Each customer has its own prefix, its own KMS key and its own IAM role.
- Sign-in: Microsoft Entra ID (company login) federated into Amazon Cognito. The Cognito id token names the
  customer. It is exchanged at STS (AssumeRoleWithWebIdentity) for short-lived credentials of that customer's role.
- Analyzer: a Strands agent running inside the run workers, calling Claude Haiku on Amazon Bedrock. Every turn
  passes a KPI gate first. The gate sends the question to a standard KPI query (fixed SQL) or to the custom
  analysis agent (free SQL over gold behind a guard). The agent escalates to the lossless silver tables only
  with a stated, audited reason.
- Data zones: raw (bronze), lossless (silver), semantic (gold, `sem.*`). The chat reads gold by default.
- AWS gates are numbered 1–6 everywhere, with the same meaning:
  1. EventBridge rule matches Customer B's prefix
  2. iam:PassRole allowed for the launcher
  3. IAM + KMS allow the parser (write path)
  4. Token valid (Cognito signature, audience, expiry, customer claim)
  5. Role trust policy allows this token (STS)
  6. IAM + KMS allow the reader (read path)

## Pages

Ids are `NN-name`. One `.drawio` file, one `.md` file and one rendered PNG and SVG per page.

```
00-overview                      L0
10-ingestion                     L1  ingestion & lake
  11-sftp-intake, 12-s3-layout, 13-parse-trigger, 14-parser-task, 15-gold-publish
20-identity                      L1  identity & access
  21-sign-in, 22-token-to-credentials, 23-customer-isolation
30-chat-backend                  L1  chat backend
  31-edge, 32-chat-turn, 33-state, 34-limits
40-analyzer                      L1  agent analyzer
  41-kpi-gate, 42-standard-kpi-path, 43-custom-analysis-agent, 44-sql-guard-escalation, 45-model-calls
50-network-security              L1  network & security
  51-network, 52-encryption-keys, 53-audit-logging
60-operations                    L1  operations
  61-deploy, 62-monitoring, 63-eval-regression
90-service-catalog, 91-iam-kms-matrix, 92-glossary   appendix (markdown only)
```

## Files

- `src/<id>.drawio`: one page, `<diagram id="<id>" name="<short title>">`.
- `png/<id>.png` and `svg/<id>.svg`: renders.
- `pages/<id>.md`: the page text.
- `cards/<service>.md`: an AWS service spec card for the appendix (template below).

Render with:

```sh
S=/tmp/claude-0/-home-user-cc/0e6bb8ac-ae6e-51d1-adaa-a5d39fba27c7/scratchpad
export HOME=/tmp
xvfb-run -a $S/squashfs-root/drawio -x -f png --width 2000 -o png/<id>.png src/<id>.drawio --no-sandbox --disable-gpu
xvfb-run -a $S/squashfs-root/drawio -x -f svg -o svg/<id>.svg src/<id>.drawio --no-sandbox --disable-gpu
python3 -I $S/drawio-skill/skills/drawio-skill/scripts/validate.py src/<id>.drawio
```

Run the renders one at a time: concurrent drawio processes can collide. If a render fails, retry once.

The validator has a known false positive: waypoints inside containers are not offset, so crossing warnings on
waypointed edges inside containers can be ignored. Open every PNG with Read and check it for overlaps, clipped
text and edges through boxes. Do at most two repair rounds.

## Clarity rules (these override everything else on this page)

The user found the first diagrams confusing. Every diagram must pass these rules:
1. One story per diagram, read left to right. Put parallel flows in separate horizontal lanes, each lane
   labelled at its left ("DATA IN", "QUESTIONS", "SIGN-IN"). No arrows that go back up across a lane, unless
   it is a single short vertical arrow to shared storage.
2. L1 diagrams: at most 8 boxes. L2 diagrams: at most 10 boxes. Anything more goes into the page text or another page.
3. A box has a name and at most ONE short line under it (about 50 characters). No pipelines written inside a
   box ("A → B → C"): those are boxes and arrows, or text on the page.
4. At most 3 edges per box. Edge labels are 1–2 words, or none.
5. No crossing edges, and no edge running through a box.
6. AWS gate diamonds only on the pages whose subject they are: 13 and 14 (gates 1–3), 22 (gates 4–6) and
   23. Each diamond gets a plain question as its label ("Is the token valid?"). Elsewhere, mention gates in the
   text only.
7. Leave white space. Do not fill the canvas, and size the canvas to the content.
8. Before you finish, look at the PNG and ask: "Could an IT reader say what happens, in order, in 10 seconds?"
   If not, simplify. Remove boxes and move detail to the text.

## Shapes: use real icons, not plain boxes (overrides the "Service box" rule below)

The user rejected plain boxes. Every AWS service is drawn as its official AWS architecture icon, and every
other thing is drawn as what it is. Compare /home/user/cc/diagrams/CC_Data_Backend.png, which puts an icon on
each item.

- **AWS service** = a 48x48 icon with its label below:
  `sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=<COLOR>;strokeColor=#ffffff;dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;fontSize=11;fontFamily=Helvetica;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.<NAME>;`
  - Label: `<b>N · Service name</b><br>one short line`.
  - Do NOT set whiteSpace=wrap on icons, or the label wraps to the icon's width.
  - Leave at least 170 px between icon centres, so the labels don't collide.
  - Names and colours (verified to render; other names may draw a blank square, so check your PNG):

  | Service | NAME | COLOR |
  |---|---|---|
  | S3 | s3 | #7AA116 |
  | Transfer Family | transfer_family | #7AA116 |
  | KMS | key_management_service | #DD344C |
  | IAM | identity_and_access_management | #DD344C |
  | STS | sts | #DD344C |
  | Cognito | cognito | #DD344C |
  | WAF | waf | #DD344C |
  | Secrets Manager | secrets_manager | #DD344C |
  | EventBridge | eventbridge | #E7157B |
  | SQS | sqs | #E7157B |
  | CloudWatch | cloudwatch_2 | #E7157B |
  | CloudTrail | cloudtrail | #E7157B |
  | Fargate | fargate | #ED7100 |
  | ECS | ecs | #ED7100 |
  | ECR | ecr | #ED7100 |
  | CloudFront | cloudfront | #8C4FFF |
  | Elastic Load Balancing | elastic_load_balancing | #8C4FFF |
  | VPC | vpc | #8C4FFF |
  | Amazon Bedrock | bedrock | #01A88D |

  NOT working: elastic_container_service, elastic_container_registry, simple_storage_service.

- **AWS resource icons** (no tile), same label rule:
  `sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor=<COLOR>;strokeColor=none;dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;fontSize=11;fontFamily=Helvetica;aspect=fixed;shape=mxgraph.aws4.<NAME>;`
  - application_load_balancer (#8C4FFF)
  - endpoints, i.e. VPC endpoint (#8C4FFF)
  - role, i.e. an IAM role (#DD344C)
  - data_encryption_key, i.e. a KMS key (#DD344C)
  - bucket (#7AA116)
  - container_1, i.e. a task or container (#ED7100)
  - users (#232F3E)
  - client, i.e. a browser (#232F3E)
  - corporate_data_center, i.e. a customer lab (#232F3E)
  - traditional_server (#232F3E)
  - database (#232F3E)
- **Database or query engine** (DuckDB reader session, answer key):
  `shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=10;fillColor=#EAF0F9;strokeColor=#4A6BB0;fontSize=11;fontFamily=Helvetica;`
- **Table** (sem.* tables, silver tables, conversation object fields): an ERD-style list, with the title row and
  up to 5 field rows (then "…"):
  `swimlane;fontStyle=1;childLayout=stackLayout;horizontal=1;startSize=26;fillColor=#EAF0F9;strokeColor=#4A6BB0;horizontalStack=0;resizeParent=1;resizeLast=0;collapsible=0;marginBottom=0;html=1;fontSize=12;fontFamily=Helvetica;`
  - Child rows: `text;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;spacingLeft=8;html=1;fontSize=11;fontFamily=Helvetica;`
    with parent = the table and geometry y = 26 + 26·k, height 26.
- **File or object** (raw X12 file, manifest, CURRENT pointer, conversation JSON):
  `shape=note;size=12;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#4A6BB0;fontSize=11;fontFamily=Helvetica;`
- **S3 prefix or folder**: `shape=mxgraph.aws4.folder;html=1;fillColor=#4A6BB0;strokeColor=none;aspect=fixed;`
  with the label below.
- **Agent or model-driven step**: keep the navy hexagon from the "Agent" style below.
- **Our own code that isn't an AWS service** (the chat API process, the SQL guard, the KPI gate check): the
  plain service box is still allowed. Put the icon of whatever it runs on (fargate or ecs) inside it at the left,
  24x24.
- **External, non-AWS** (Microsoft Entra ID, GitHub): a dashed box (External style), or an AWS resource icon such
  as users or client when it is a person or a device.
- **Groups**: AWS standard groups instead of plain containers, wherever the grouping is an AWS boundary.
  - AWS Cloud: `points=[[0,0]];outlineConnect=0;html=1;whiteSpace=wrap;fontSize=12;container=1;pointerEvents=0;collapsible=0;recursiveResize=0;shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_aws_cloud_alt;strokeColor=#232F3E;fillColor=none;verticalAlign=top;align=left;spacingLeft=30;fontColor=#232F3E;dashed=0;`
  - VPC: the same with `grIcon=mxgraph.aws4.group_vpc2;strokeColor=#8C4FFF;fontColor=#8C4FFF;`
  - Private subnet: the same with `grIcon=mxgraph.aws4.group_security_group;grStroke=0;strokeColor=#00A4A6;fillColor=#E6F6F7;fontColor=#147EBA;`
  - Keep the navy dashed PHI boundary for a customer's data.
- **Legend**: show the shape kinds the page uses (AWS service icon, table, file, database, agent, external,
  PHI boundary, AWS gate).

## Diagram style

Copy these styles exactly. They come from `diagrams/CC_Backend.drawio`.

- Product name in diagrams: **ClaimClarity** (titles in caps: `CLAIMCLARITY · <AREA>`), never "CC". Logical names follow it: "the ClaimClarity bucket".
- Canvas: about 1600 x 1000. Title at the top left: `CLAIMCLARITY · <AREA>` in bold 20px navy #1C2B57, plus a 12px
  subtitle.
- L2 pages: put a breadcrumb under the title, e.g. `Overview › Chat backend › Chat turn`, 11px #3A5A9E.
- Service box:
  `rounded=0;whiteSpace=wrap;html=1;fillColor=#EAF0F9;strokeColor=#4A6BB0;fontColor=#1A1D23;align=left;spacingLeft=14;spacingRight=10;verticalAlign=middle;fontSize=12;fontFamily=Helvetica;`
  - Label: `<b>N · NAME IN CAPS</b><br>one or two short lines`. N is the box number used in the page's
    "In this diagram" table.
  - Numbering: number the boxes the reader acts on or links from. Not every box needs one.
- External actor or system (customer, user, Entra, GitHub): white fill, `strokeColor=#8A8E95;dashed=1`, same
  text style.
- Group container:
  `rounded=0;whiteSpace=wrap;html=1;fillColor=none;strokeColor=#4A6BB0;strokeWidth=1.5;container=1;pointerEvents=0;collapsible=0;`
  with a bold 11px caps title text cell inside at the top left.
- PHI boundary container: `strokeColor=#1C2B57;dashed=1;dashPattern=6 4;strokeWidth=1.5`, no fill.
- Agent (model-driven step):
  `shape=hexagon;perimeter=hexagonPerimeter2;size=0.12;whiteSpace=wrap;html=1;fillColor=#1C2B57;strokeColor=#1C2B57;fontColor=#FFFFFF;fontFamily=Helvetica;fontSize=11;`
- AWS gate: a navy rhombus 30x30
  `rhombus;html=1;fillColor=#1C2B57;strokeColor=#1C2B57;fontColor=#FFFFFF;fontStyle=1;fontSize=13;fontFamily=Helvetica;`
  with its number, and a text label beside it ("Token valid?").
- Edge:
  `edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#4A6BB0;strokeWidth=1.5;endArrow=block;endFill=1;endSize=6;fontSize=11;fontColor=#3A5A9E;labelBackgroundColor=#FFFFFF;fontFamily=Helvetica;`
  - Pin exitX/exitY/entryX/entryY on every edge, and bind every edge to source and target.
  - Keep labels to two or three words.
- Sequence diagrams (one chat turn, sign-in): lifelines as service boxes along the top, dashed vertical
  lines, numbered horizontal messages, top to bottom.
- Legend at the bottom right: AGENT · SERVICE · EXTERNAL · PHI BOUNDARY · AWS GATE, the same as the existing
  diagram. Show only the symbols that page uses.
- At most about 12 boxes per diagram, not counting the legend. Split the rest into another L2 page.
- Ids: stable and descriptive (`svc-sqs`, `edge-api-sqs`). Never reuse `0` or `1` except for the root cells.

## Page text (`pages/<id>.md`)

```
# <Title>

<Breadcrumb: Overview › Area › Page as links: [Overview](00-overview.md) › ...>

<2–3 sentences: what this part does and why it is built this way.>

![<title>](../png/<id>.png)

## In this diagram
| # | Box | What it does | Details |
|---|---|---|---|
| 1 | Chat API | ... | [Chat turn](32-chat-turn.md) |

## How it works
1. Numbered steps that match the numbers on the diagram.

## Specification
Tables for what IT needs: permissions (actions on which resources, in words), encryption, data held and
whether it is PHI, limits and timeouts, failure behaviour and retries, logging. Concrete values where the code
has them (e.g. "credentials live 15 minutes", "60 s total query time per question").

## Related
Up: [<L1 page>](...) · See also: ...
```

L1 pages also have a "Pages in this area" list. Keep the language plain and the sentences short.

## Spec card (`cards/<service>.md`)

```
### <AWS service>
| | |
|---|---|
| Purpose in CC | |
| Talks to | |
| Permissions | who can do what, in words |
| Encryption | |
| Data held / PHI | |
| Scaling and limits | |
| Failure behaviour | |
| Logging and audit | |
| Used on pages | links |
```
