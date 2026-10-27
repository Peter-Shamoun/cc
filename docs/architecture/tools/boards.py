"""Zoom boards 00–07 in the original board style (tools/olib.py), each a zoom-in of the main data-backend board.

Gates are drawn on the arrow they guard (olib.big_gate). The analyzer is the Claim Query Agent running inside the
run worker; DuckDB runs as its own query service (DuckDB Quack server) that holds the customer's credentials.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from olib import Board, big_gate, caps

# board id -> (number, title, x, y, w, h); the main board sits at (1900, 160, 2460, 1700)
LAYOUT = {
    "s0": ("00", "Storage & isolation", -900, 160, 2700, 1400),
    "s1": ("01", "Ingestion & parse", 4500, 160, 2600, 1250),
    "s2": ("02", "Login & customer mapping", -900, 1700, 2700, 760),
    "s3": ("03", "Query path", 4500, 1550, 2600, 1150),
    "s4": ("04", "Analyzer", -900, 2800, 3000, 1050),
    "s5": ("05", "Chat backend", 2300, 2800, 2600, 900),
}


def board(cells, key, tag="Shown for Customer B"):
    number, title, x, y, w, h = LAYOUT[key]
    return Board(cells, key, number, title, x, y, w, h, tag=tag)


def storage(cells):
    b = board(cells, "s0")
    b.group("s0_pfx", "Prod S3 bucket | claimclarity/ · customer_b/ · PHI boundary", 1000, 110, 1060, 1160, res="s3", phi=True)
    rows = [("landing", "landing/ · files from SFTP, deleted after copy"), ("raw", "raw/received_date=YYYY-MM-DD/ · write-once, Object Lock 7 years"),
            ("parquet", "parquet/ · silver · lossless tables"), ("sem", "semantic_layer/ · gold · sem.* · CURRENT version"),
            ("staging", "staging/ · batch lease"), ("quar", "quarantine/ · files that failed to parse · 1 year"),
            ("conv", "conversations/ · run-events/ · transcript key"), ("audit", "audit/ · one record per SQL · write-once")]
    ry = {}
    for i, (key, text) in enumerate(rows):
        ry[key] = 170 + i * 135
        b.mono(f"s0_{key}", text, 1040, ry[key], 980, 70)
    roles = [("copy", "raw", "SFTP copy role", "writes landing/ → raw/", 3, "IAM + KMS allow?", "lake key"),
             ("parser", "parquet", "Parser role", "raw/ → parquet/ + semantic_layer/", 3, "IAM + KMS allow?", "lake key"),
             ("reader", "sem", "Reader role", "used by the DuckDB query service", 6, "IAM + KMS allow?", "CURRENT gold only"),
             ("trans", "conv", "Transcript role", "chat API + run workers", 6, "IAM + KMS allow?", "transcript key")]
    for key, target, title, line, n, q, detail in roles:
        y = ry[target]
        b.box(f"s0_r_{key}", f"Customer B · {title}", line, 40, y - 10, 400, 90, res="identity_and_access_management")
        big_gate(b, f"s0_g_{key}", n, q, detail, 650, y + 3)
        b.edge(f"s0_e1_{key}", f"s0_r_{key}", f"s0_g_{key}")
        b.edge(f"s0_e2_{key}", f"s0_g_{key}", f"s0_{target}")
    b.box("s0_r_a", "Customer A · any role", "reader, parser, copy", 40, 1180, 400, 90, res="identity_and_access_management")
    big_gate(b, "s0_g_a", "", "Customer A's role or key", "bucket policy + key policy refuse it", 650, 1193, deny=True)
    b.edge("s0_e_a1", "s0_r_a", "s0_g_a")
    b.edge("s0_e_a2", "s0_g_a", "s0_pfx", "", entry=(0, .93))
    b.text("s0_k_t", caps("Access controls", 16), 2120, 120, 500, 26)
    for i, (res, title, line) in enumerate([("key_management_service", "KMS · lake key", "Customer B only · raw, silver, gold"),
                                            ("key_management_service", "KMS · transcript key", "Customer B only · chat state"),
                                            ("key_management_service", "KMS · query audit key", "platform key · one statement per customer"),
                                            ("s3", "Bucket policy", "TLS only · each role inside its own prefix"),
                                            ("cloudtrail", "CloudTrail", "data events for every S3 and KMS call")]):
        b.box(f"s0_k{i}", title, line, 2120, 170 + i * 160, 540, 100, res=res)


def ingestion(cells):
    b = board(cells, "s1")
    b.text("s1_a", caps("Files arrive", 16), 40, 110, 400, 26)
    b.note("s1_lab", "<b>Customer B's lab</b><br>daily 837 · 835 · 277", 40, 160, 220, 90)
    b.box("s1_sftp", "SFTP endpoint", "Transfer Family · lab IPs only", 320, 160, 300, 90, res="transfer_family")
    b.mono("s1_land", "landing/", 690, 170, 200, 70)
    b.box("s1_copy", "Copy step", "as B's SFTP copy role", 960, 160, 280, 90, white=True)
    big_gate(b, "s1_g3a", 3, "IAM + KMS allow?", "create-only · lake key", 1380, 173)
    b.mono("s1_raw", "raw/received_date=…/ · _SUCCESS last", 1600, 170, 560, 70)
    for e, (s, t) in enumerate([("s1_lab", "s1_sftp"), ("s1_sftp", "s1_land"), ("s1_land", "s1_copy"), ("s1_copy", "s1_g3a"), ("s1_g3a", "s1_raw")]):
        b.edge(f"s1_ea{e}", s, t)
    b.text("s1_b", caps("A batch starts one task", 16), 40, 420, 600, 26)
    b.mono("s1_succ", "_SUCCESS", 40, 480, 220, 70)
    big_gate(b, "s1_g1", 1, "Rule matches B?", "EventBridge rule per customer", 400, 483)
    b.box("s1_eb", "EventBridge", "on _SUCCESS", 620, 470, 280, 90, res="eventbridge")
    b.box("s1_run", "Launcher · ECS RunTask", "3 attempts, then a dead-letter queue", 960, 470, 380, 90, res="ecs")
    big_gate(b, "s1_g2", 2, "PassRole allowed?", "only B's parser role", 1480, 483)
    b.box("s1_task", "Parser task", "Fargate · one per batch", 1700, 470, 300, 90, res="fargate")
    for e, (s, t) in enumerate([("s1_succ", "s1_g1"), ("s1_g1", "s1_eb"), ("s1_eb", "s1_run"), ("s1_run", "s1_g2"), ("s1_g2", "s1_task")]):
        b.edge(f"s1_eb{e}", s, t)
    b.group("s1_ft", "Fargate parser task · Customer B's parser role · lives for one batch", 40, 720, 1640, 420, res="fargate", phi=True)
    steps = [("Start", "takes the batch lease"), ("Parse", "each raw file"), ("Silver", "part files, commit record last"),
             ("Gold", "rebuild touched claims"), ("Exit", "lease released, creds expire")]
    for i, (t, line) in enumerate(steps):
        b.box(f"s1_s{i}", f"{i + 1} · {t}", line, 70 + i * 320, 800, 280, 90, white=(i != 4))
        if i:
            b.edge(f"s1_es{i}", f"s1_s{i - 1}", f"s1_s{i}")
    b.note("s1_bad", "<b>A file that fails to parse</b> goes to quarantine/; the rest of the batch still loads. A failed batch commits nothing and is re-run.", 70, 960, 1580, 80)
    big_gate(b, "s1_g3b", 3, "IAM + KMS allow?", "every read and write · lake key", 1820, 973)
    b.mono("s1_gold", "parquet/ + semantic_layer/ · new CURRENT", 2040, 980, 520, 70)
    b.edge("s1_eg1", "s1_s3", "s1_g3b", "", exit_=(.5, 1), entry=(0, .5))
    b.edge("s1_eg2", "s1_g3b", "s1_gold")


def login(cells):
    b = board(cells, "s2")
    b.box("s2_user", "XiFin user", "opens ClaimClarity", 40, 230, 260, 90, res="users")
    b.box("s2_entra", "Microsoft Entra ID", "company login", 380, 230, 300, 90, white=True)
    b.decide("s2_grp", caps("Assigned group?", 15), 760, 220, 200, 110)
    b.box("s2_cog", "Cognito", "maps the group to one customer", 1040, 230, 320, 90, res="cognito")
    b.mono("s2_tok", "ID token · customer = B · 60 min", 1440, 240, 380, 70)
    big_gate(b, "s2_g4", 4, "Token valid?", "signature · audience · expiry · customer", 1960, 243)
    b.box("s2_api", "Chat API", "customer from the token only", 2180, 230, 300, 90, res="fargate")
    for e, (s, t) in enumerate([("s2_user", "s2_entra"), ("s2_entra", "s2_grp"), ("s2_grp", "s2_cog"), ("s2_cog", "s2_tok"), ("s2_tok", "s2_g4"), ("s2_g4", "s2_api")]):
        b.edge(f"s2_e{e}", s, t, "yes" if s == "s2_grp" else "")
    b.mono("s2_map", "ClaimClarity-LABCORP → labcorp<br>ClaimClarity-CUSTB → customer_b", 1040, 450, 520, 90)
    b.text("s2_map_t", caps("Group → customer map", 14), 1040, 415, 400, 24)
    b.edge("s2_em", "s2_map", "s2_cog", "", exit_=(.5, 0), entry=(.5, 1))
    b.note("s2_cookie", "<b>Session</b> · the token sits in an httpOnly, Secure, SameSite=Strict cookie; the refresh token never reaches the browser", 1440, 450, 1040, 90)
    b.note("s2_no", "<b>No group, no access</b> · Entra refuses users outside the assigned groups", 380, 600, 580, 80)
    b.edge("s2_eno", "s2_grp", "s2_no", "no", exit_=(.5, 1), entry=(.5, 0))


def query_path(cells):
    b = board(cells, "s3")
    b.group("s3_rw", "Run worker · ECS on Fargate · Claim Query Agent", 40, 120, 760, 620, res="fargate")
    b.agent("s3_ag", "Claim Query Agent", "Claude Haiku on Amazon Bedrock", 120, 200, 300, 120)
    b.box("s3_cli", "ClaimClarity CLI", "cli.py · one command per call", 120, 400, 300, 90, white=True)
    b.box("s3_guard", "SQL guard", "read-only · fails bad queries early", 120, 580, 300, 90, white=True)
    b.edge("s3_e0", "s3_ag", "s3_cli", "SQL", exit_=(.5, 1), entry=(.5, 0))
    b.edge("s3_e1", "s3_cli", "s3_guard", "", exit_=(.5, 1), entry=(.5, 0))
    b.note("s3_nocred", "<b>The agent never holds AWS credentials.</b> It sends SQL over a per-run channel.", 460, 400, 300, 120)
    b.box("s3_q", "DuckDB query service", "Quack server · own Fargate service", 960, 570, 340, 110, res="fargate")
    b.edge("s3_e2", "s3_guard", "s3_q", "Quack · per-run token")
    big_gate(b, "s3_g5", 5, "Trust policy allows B?", "AssumeRoleWithWebIdentity", 1098, 830, label_below=True)
    b.box("s3_sts", "STS", "15-minute credentials · B's reader role", 1440, 817, 360, 90, res="sts")
    b.edge("s3_e3", "s3_q", "s3_g5", "", exit_=(.5, 1), entry=(.5, 0))
    b.edge("s3_e4", "s3_g5", "s3_sts")
    b.group("s3_pfx", "Prod S3 bucket | claimclarity/", 1760, 120, 800, 640, res="s3")
    b.mono("s3_a", "customer_a/ · no access", 1800, 180, 720, 70)
    b.group("s3_b", "customer_b/ · PHI boundary", 1800, 300, 720, 420, phi=True)
    b.mono("s3_sem", "semantic_layer/ · sem.* · current version", 1840, 360, 640, 80)
    b.note("s3_ll", "<b>lossless.*</b> · PHI-stripped, every EDI field · only after a reasoned escalation", 1840, 560, 640, 100)
    big_gate(b, "s3_g6a", 6, "IAM + KMS allow?", "this customer only", 1500, 368)
    big_gate(b, "s3_g6b", 6, "IAM + KMS allow?", "escalation is logged", 1500, 578, label_below=True)
    b.edge("s3_e5", "s3_q", "s3_g6a", "read", exit_=(1, .2), entry=(0, .5))
    b.edge("s3_e6", "s3_g6a", "s3_sem")
    b.edge("s3_e7", "s3_q", "s3_g6b", "", exit_=(1, .7), entry=(0, .5), dashed=True)
    b.edge("s3_e8", "s3_g6b", "s3_ll", "", dashed=True)
    b.note("s3_rej", "<b>Rejected by the API</b> · GET /claims?customer=customer_a — the customer comes only from the verified token", 40, 960, 1000, 90)
    b.note("s3_den", "<b>Denied by IAM</b> · SQL that points at customer_a/ — B's credentials can't read it; enforcement is in AWS, not app code", 1100, 960, 1460, 90)


def analyzer(cells):
    b = board(cells, "s4")
    b.text("s4_fe_t", "Frontend user interface", 40, 160, 400, 24, size=15)
    b.box("s4_fe", "ClaimClarity chat", "", 40, 190, 360, 640, white=True)
    b.decide("s4_gate", caps("KPI gate", 16) + "<br>does a preset<br>answer it?", 470, 330, 200, 120)
    b.box("s4_pre", "Preset KPI queries", "16 presets · fixed SQL · the gate fills payer, payer class, month, CPT, CARC", 760, 110, 640, 80, white=True)
    b.edge("s4_eq", "s4_fe", "s4_gate", "user query", exit_=(1, .22), entry=(0, .5))
    b.edge("s4_eyes", "s4_gate", "s4_pre", "yes", exit_=(.5, 0), entry=(0, .5))
    b.group("s4_ag", "Claim Query Agent · the run worker · backend for ClaimClarity and Agentic Next Best Actions", 760, 230, 1500, 760, res="fargate")
    b.edge("s4_eno", "s4_gate", "s4_ag", "no", exit_=(1, .5), entry=(0, .23))
    b.edge("s4_ef", "s4_fe", "s4_ag", "follow-up or clarify answers", exit_=(1, .72), entry=(0, .62))
    b.edge("s4_ea", "s4_ag", "s4_fe", "answer + formatting + live steps, or clarifying questions", exit_=(0, .9), entry=(1, .9))
    b.box("s4_bank", "Solved example question bank", "training set", 790, 290, 240, 440, white=True)
    b.box("s4_tool", "Tool & CLI reference", "prompt cached", 790, 760, 240, 190, white=True)
    b.group("s4_refs", "References · prompt cached", 1080, 280, 760, 190)
    b.box("s4_r1", "Rules", "from past mistakes", 1100, 330, 230, 110, white=True)
    b.box("s4_r2", "EDI glossary", "business terms", 1350, 330, 230, 110, white=True)
    b.box("s4_r3", "Semantic layer", "schema + allowed values", 1600, 330, 220, 110, white=True)
    b.agent("s4_llm", "Claude Haiku", "one agent · Strands on Amazon Bedrock", 1160, 560, 340, 130)
    b.note("s4_ll", "<b>Full lossless EDI schema</b><br>only after the agent escalates with a reason (logged)", 1180, 790, 300, 110)
    b.edge("s4_ek", "s4_bank", "s4_llm", "K = 3 most similar question + SQL + answer", exit_=(1, .65), entry=(0, .5))
    for i, r in enumerate(("s4_r1", "s4_r2", "s4_r3")):
        b.edge(f"s4_er{i}", r, "s4_llm", "", exit_=(.5, 1), entry=(.25 + .25 * i, 0))
    b.edge("s4_et", "s4_tool", "s4_llm", "", exit_=(1, .5), entry=(.2, 1))
    b.edge("s4_ell", "s4_llm", "s4_ll", "", exit_=(.5, 1), entry=(.5, 0), dashed=True)
    b.box("s4_sys", "System prompt + conversation history", "prompt cached", 1880, 520, 340, 120, white=True)
    b.box("s4_new", "User's newest prompt", "today's date · data coverage", 1880, 700, 340, 100, white=True)
    b.edge("s4_esys", "s4_sys", "s4_llm", "", exit_=(0, .5), entry=(1, .3))
    b.edge("s4_enew", "s4_new", "s4_llm", "", exit_=(0, .5), entry=(1, .8))
    b.text("s4_dec", "<b>First, the agent decides:</b> answer, ask a clarifying question, or escalate", 1530, 860, 330, 60, size=13, color="#1C2B57")
    b.group("s4_cg", "Query tools", 2330, 230, 630, 760)
    b.box("s4_cli", "cli.py", "ClaimClarity CLI", 2360, 500, 220, 90, white=True)
    b.box("s4_guard", "SQL guard", "blocks non-read queries and queries that would fail against the schema", 2620, 470, 310, 150, white=True)
    b.edge("s4_e1", "s4_llm", "s4_cli", "agent writes CLI calls with SQL", exit_=(1, .5), entry=(0, .5))
    b.edge("s4_e2", "s4_cli", "s4_guard")
    b.edge("s4_e3", "s4_pre", "s4_guard", "preset SQL", exit_=(1, .5), entry=(.5, 0))
    b.note("s4_out", "To the <b>DuckDB query service</b> (board 03) · rows stream back · the server re-runs the final query", 2360, 700, 570, 100)
    b.edge("s4_e4", "s4_guard", "s4_out", "", exit_=(.5, 1), entry=(.5, 0))


def chat_backend(cells):
    b = board(cells, "s5")
    b.box("s5_u", "XiFin user", "browser", 40, 180, 240, 90, res="users")
    b.box("s5_cf", "CloudFront + WAF", "the only public entry", 340, 180, 280, 90, res="cloudfront")
    b.box("s5_alb", "Load balancer", "internal", 680, 180, 260, 90, res="elastic_load_balancing")
    big_gate(b, "s5_g4", 4, "Token valid?", "every request", 1080, 193)
    b.box("s5_api", "Chat API", "ECS on Fargate · queues the run", 1300, 180, 320, 90, res="fargate")
    b.box("s5_sqs", "Run queue", "SQS · one run per question", 1700, 180, 320, 90, res="sqs")
    for e, (s, t) in enumerate([("s5_u", "s5_cf"), ("s5_cf", "s5_alb"), ("s5_alb", "s5_g4"), ("s5_g4", "s5_api"), ("s5_api", "s5_sqs")]):
        b.edge(f"s5_e{e}", s, t)
    b.box("s5_rw", "Run worker", "ECS on Fargate · the Claim Query Agent", 1700, 420, 320, 100, res="fargate")
    b.box("s5_bed", "Amazon Bedrock", "Claude Haiku", 2140, 420, 300, 100, res="bedrock")
    b.box("s5_q", "DuckDB query service", "Quack server · own Fargate service", 1700, 650, 320, 100, res="fargate")
    b.edge("s5_e5", "s5_sqs", "s5_rw", "", exit_=(.5, 1), entry=(.5, 0))
    b.edge("s5_e6", "s5_rw", "s5_bed")
    b.edge("s5_e7", "s5_rw", "s5_q", "Quack", exit_=(.5, 1), entry=(.5, 0))
    b.group("s5_pfx", "customer_b/ · chat state · transcript key", 300, 420, 1100, 330, res="s3", phi=True)
    b.mono("s5_conv", "conversations/ · one object each, If-Match writes", 340, 500, 1020, 70)
    b.mono("s5_ev", "run-events/ · numbered chunks · replayed on reconnect", 340, 610, 1020, 70)
    b.edge("s5_e8", "s5_api", "s5_conv", "save", exit_=(.5, 1), entry=(.85, 0))
    b.edge("s5_e9", "s5_rw", "s5_ev", "append", exit_=(0, .5), entry=(1, .5))
    b.note("s5_lim", "<b>Limits</b> · 3 unfinished runs per user · 110 s per run · per-customer concurrency and token budgets · answer streams back over SSE", 40, 790, 2400, 70)


def network(cells):
    b = board(cells, "s6", tag="")
    b.box("s6_u", "XiFin users", "HTTPS", 40, 330, 240, 90, res="users")
    b.note("s6_labs", "<b>Customer labs</b><br>SFTP · port 22", 40, 600, 240, 90)
    b.group("s6_cloud", "AWS Cloud", 330, 90, 2630, 1020)
    b.box("s6_cf", "CloudFront + WAF", "rate limits · managed rules", 380, 330, 300, 90, res="cloudfront")
    b.box("s6_tf", "SFTP endpoint", "Transfer Family · lab IPs only", 380, 600, 300, 90, res="transfer_family")
    b.group("s6_vpc", "VPC · two availability zones", 760, 140, 1440, 940, res="vpc")
    b.group("s6_sub", "Private subnets · no public IPs", 790, 200, 980, 660)
    b.box("s6_alb", "Load balancer", "CloudFront traffic only", 830, 330, 280, 90, res="elastic_load_balancing")
    b.box("s6_api", "Chat API", "", 1180, 250, 280, 80, res="fargate")
    b.box("s6_wrk", "Run workers", "Claim Query Agent", 1180, 390, 280, 80, res="fargate")
    b.box("s6_q", "DuckDB query service", "Quack server", 1180, 530, 280, 80, res="fargate")
    b.box("s6_prs", "Parser task", "", 1180, 670, 280, 80, res="fargate")
    b.box("s6_vpe", "VPC endpoints", "S3 · STS · KMS · Bedrock<br>ECR · Logs · SQS · Secrets", 1820, 430, 340, 130, res="vpc")
    b.group("s6_eg", "Egress subnet", 790, 890, 980, 160)
    b.box("s6_nat", "NAT + Network Firewall", "Cognito hosts only", 830, 930, 360, 90, white=True)
    b.text("s6_keys_t", caps("Keys, roles & audit", 16), 2260, 150, 600, 26)
    for i, (res, title, line) in enumerate([("key_management_service", "KMS", "per customer: lake key, transcript key"),
                                            ("identity_and_access_management", "IAM roles", "per customer: SFTP copy, parser, reader, transcripts, audit"),
                                            ("cloudtrail", "CloudTrail", "every AWS call · 6 years"),
                                            ("cloudwatch_2", "Agent audit", "hash-chained · one record per SQL · write-once"),
                                            ("cloudwatch_2", "CloudWatch Logs", "PHI-redacted app logs · 1 year")]):
        b.box(f"s6_k{i}", title, line, 2260, 200 + i * 140, 660, 100, res=res)
    b.edge("s6_e1", "s6_u", "s6_cf")
    b.edge("s6_e2", "s6_cf", "s6_alb", "VPC origin")
    b.edge("s6_e3", "s6_alb", "s6_api")
    b.edge("s6_e4", "s6_q", "s6_vpe", "private", exit_=(1, .5), entry=(0, .7))
    b.edge("s6_e5", "s6_labs", "s6_tf")


def operations(cells):
    b = board(cells, "s7", tag="")
    b.text("s7_a", caps("Ship a change", 16), 40, 110, 400, 26)
    b.note("s7_git", "<b>Git</b>", 40, 150, 160, 80)
    b.box("s7_ci", "CI", "build + tests", 260, 150, 240, 80, white=True)
    b.box("s7_ecr", "Amazon ECR", "images", 560, 150, 260, 80, res="ecr")
    b.box("s7_ecs", "ECS services", "rolling, health-checked", 880, 150, 300, 80, res="ecs")
    b.note("s7_rb", "<b>Unhealthy?</b> roll back to the previous task definition", 1240, 150, 460, 80)
    for e, (s, t) in enumerate([("s7_git", "s7_ci"), ("s7_ci", "s7_ecr"), ("s7_ecr", "s7_ecs"), ("s7_ecs", "s7_rb")]):
        b.edge(f"s7_ea{e}", s, t)
    b.text("s7_b", caps("Watch it", 16), 40, 300, 400, 26)
    b.box("s7_t", "ECS tasks", "API · workers · query service · parser", 40, 340, 380, 80, res="fargate")
    b.box("s7_cw", "CloudWatch", "logs · metrics · alarms", 480, 340, 300, 80, res="cloudwatch_2")
    b.note("s7_al", "<b>Alarms</b> · queue depth · error rate · model latency and cost · parser failures · quarantined files", 840, 340, 1000, 80)
    b.edge("s7_eb0", "s7_t", "s7_cw"); b.edge("s7_eb1", "s7_cw", "s7_al")
    b.text("s7_c", caps("Keep answers right", 16), 40, 490, 400, 26)
    b.note("s7_fb", "<b>Thumbs-down + sampled chats</b>", 40, 530, 300, 90)
    b.box("s7_rs", "Regression set", "answer keys · reworded questions", 400, 530, 340, 90, white=True)
    b.decide("s7_ok", caps("Accuracy holds?", 15), 810, 520, 210, 110)
    b.box("s7_ship", "Ship", "the analyzer change goes live", 1090, 530, 320, 90, white=True)
    b.edge("s7_ec0", "s7_fb", "s7_rs"); b.edge("s7_ec1", "s7_rs", "s7_ok"); b.edge("s7_ec2", "s7_ok", "s7_ship", "yes")


def boards():
    cells = []
    for build in (storage, ingestion, login, query_path, analyzer, chat_backend):
        build(cells)
    return cells
