"""Zoom boards 04–07 in the original style, appended to the original canvas below boards 02 and 03."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from olib import Board, caps


def boards():
    cells = []
    # 04 · Analyzer: the excalidraw, plus the KPI gate and the presets ---------------------------------
    b = Board(cells, "s4", "04", "Analyzer", 40, 2250, 2520, 900)
    b.box("s4_chat", "Chat", "question · follow-up", 40, 380, 200, 90, res="users")
    b.decide("s4_gate", caps("KPI gate", 16) + "<br>does a preset<br>answer it?", 300, 370, 190, 110)
    b.box("s4_pre", "Preset KPI queries", "16 presets · fixed SQL · the gate fills payer, payer class, month, CPT, CARC", 600, 110, 560, 90, white=True)
    b.group("s4_ag", "Claim Query Agent · inside the run worker", 600, 250, 780, 600, res="fargate")
    b.text("s4_cache_t", caps("Cached · same for every question", 14), 630, 290, 500, 22)
    b.box("s4_r1", "Playbook", "past mistakes", 630, 318, 220, 80, white=True)
    b.box("s4_r2", "EDI glossary", "business terms", 870, 318, 220, 80, white=True)
    b.box("s4_r3", "Gold schema", "columns + allowed values", 1110, 318, 240, 80, white=True)
    b.box("s4_turn", "Each turn", "3 similar solved questions<br>today's date · data coverage<br>conversation history", 630, 470, 230, 200, white=True)
    b.agent("s4_llm", "Analyzer agent", "Claude Haiku on Amazon Bedrock", 930, 480, 320, 130)
    b.text("s4_dec", "<b>First decides:</b> answer, ask a clarifying question, or escalate", 630, 690, 270, 60, size=13, color="#1C2B57")
    b.note("s4_ll", "<b>Full lossless schema</b><br>only after a reasoned escalation", 930, 700, 320, 80)
    b.box("s4_cli", "cc CLI + SQL guard", "read-only · row, time and cost limits", 1460, 380, 280, 100)
    b.box("s4_duck", "DuckDB reader session", "one per run · the customer's role", 1800, 380, 270, 100)
    b.gate("s4_g6", 6, "IAM + KMS allow?", 2100, 413)
    b.group("s4_pfx", "customer_b/", 2170, 250, 320, 470, res="s3", phi=True)
    b.mono("s4_sem", "semantic_layer/ · sem.*", 2190, 390, 280, 80)
    b.note("s4_lls", "<b>lossless.*</b><br>PHI-stripped · after escalation", 2190, 560, 280, 80)
    b.edge("s4_e1", "s4_chat", "s4_gate", "ask")
    b.edge("s4_e2", "s4_gate", "s4_pre", "yes", exit_=(.5, 0), entry=(0, .5))
    b.edge("s4_e3", "s4_gate", "s4_ag", "no", exit_=(1, .5), entry=(0, .5))
    for i, r in enumerate(("s4_r1", "s4_r2", "s4_r3")):
        b.edge(f"s4_er{i}", r, "s4_llm", "", exit_=(.5, 1), entry=(.25 + .25 * i, 0))
    b.edge("s4_e4", "s4_turn", "s4_llm")
    b.edge("s4_e5", "s4_llm", "s4_ll", "", exit_=(.5, 1), entry=(.5, 0), dashed=True)
    b.edge("s4_e6", "s4_pre", "s4_cli", "preset SQL", exit_=(1, .5), entry=(.5, 0))
    b.edge("s4_e7", "s4_llm", "s4_cli", "agent SQL", exit_=(1, .5), entry=(0, .5))
    b.edge("s4_e8", "s4_cli", "s4_duck")
    b.edge("s4_e9", "s4_duck", "s4_g6")
    b.edge("s4_e10", "s4_g6", "s4_sem", "read", exit_=(1, .5), entry=(0, .5))
    b.edge("s4_e11", "s4_duck", "s4_lls", "escalation", exit_=(.5, 1), entry=(0, .5), dashed=True)
    b.edge("s4_e12", "s4_ag", "s4_chat", "answer + SQL + rows", exit_=(0, .9), entry=(.5, 1), red=True)

    # 05 · Chat backend --------------------------------------------------------------------------------
    b = Board(cells, "s5", "05", "Chat backend", 2680, 2250, 2400, 900)
    xs = [40, 330, 640, 950, 1260, 1570, 1880]
    items = [("users", "XiFin user", "browser"), ("cloudfront", "CloudFront + WAF", "the only public entry"),
             ("elastic_load_balancing", "Load balancer", "internal · private subnets"), ("fargate", "Chat API", "checks the token, queues"),
             ("sqs", "Run queue", "one run per question"), ("fargate", "Run worker", "runs the analyzer"), ("bedrock", "Amazon Bedrock", "Claude Haiku")]
    ids = []
    for x, (res, t, line) in zip(xs, items):
        ids.append(b.box(f"s5_{len(ids)}", t, line, x, 180, 260, 90, res=res))
    for a, c in zip(ids, ids[1:]):
        b.edge(f"s5_e{a}", a, c)
    b.group("s5_pfx", "customer_b/ · transcript key", 860, 400, 1310, 260, res="s3", phi=True)
    b.mono("s5_conv", "conversations/ · one object each, If-Match", 900, 470, 380, 80)
    b.mono("s5_ev", "run-events/ · numbered chunks", 1500, 470, 380, 80)
    b.edge("s5_ec", "s5_3", "s5_conv", "save", exit_=(.5, 1), entry=(.5, 0))
    b.edge("s5_ee", "s5_5", "s5_ev", "append", exit_=(.5, 1), entry=(.5, 0))
    b.note("s5_sse", "<b>Answer stream</b> · the chat API relays run events to the browser over SSE; a reconnect replays from run-events/", 40, 720, 800, 70)
    b.note("s5_lim", "<b>Limits</b> · 3 unfinished runs per user · run deadline 110 s · per-customer concurrency and token budgets", 900, 720, 1270, 70)

    # 06 · Network & security --------------------------------------------------------------------------
    b = Board(cells, "s6", "06", "Network & security", 40, 3250, 2520, 1050, tag="")
    b.box("s6_u", "XiFin users", "HTTPS", 40, 330, 220, 90, res="users")
    b.note("s6_labs", "<b>Customer labs</b><br>SFTP", 40, 560, 220, 80)
    b.group("s6_cloud", "AWS Cloud", 300, 90, 2180, 920)
    b.box("s6_cf", "CloudFront + WAF", "rate limits · managed rules", 340, 330, 260, 90, res="cloudfront")
    b.box("s6_tf", "SFTP endpoint", "Transfer Family · lab IPs only", 340, 555, 260, 90, res="transfer_family")
    b.group("s6_vpc", "VPC · two availability zones", 660, 140, 1080, 840, res="vpc")
    b.group("s6_sub", "Private subnets · no public IPs", 690, 200, 680, 560)
    b.box("s6_alb", "Load balancer", "CloudFront traffic only", 720, 330, 260, 90, res="elastic_load_balancing")
    b.box("s6_api", "Chat API", "", 1060, 260, 260, 80, res="fargate")
    b.box("s6_wrk", "Run workers", "", 1060, 400, 260, 80, res="fargate")
    b.box("s6_prs", "Parser task", "", 1060, 540, 260, 80, res="fargate")
    b.box("s6_vpe", "VPC endpoints", "S3 · STS · KMS · Bedrock<br>ECR · Logs · SQS · Secrets", 1410, 380, 300, 120, res="vpc")
    b.group("s6_eg", "Egress subnet", 690, 790, 680, 160)
    b.box("s6_nat", "NAT + Network Firewall", "Cognito hosts only", 720, 830, 320, 90, white=True)
    b.text("s6_keys_t", caps("Keys, roles & audit", 16), 1790, 150, 600, 26)
    b.box("s6_kms", "KMS", "per customer: lake key, transcript key", 1790, 200, 650, 90, res="key_management_service")
    b.box("s6_iam", "IAM roles", "per customer: SFTP copy, parser, reader, transcripts, audit", 1790, 320, 650, 90, res="identity_and_access_management")
    b.box("s6_ct", "CloudTrail", "every AWS call, incl. S3 and KMS data events", 1790, 440, 650, 90, res="cloudtrail")
    b.box("s6_aud", "Agent audit", "hash-chained · one record per SQL · write-once", 1790, 560, 650, 90, res="cloudwatch_2")
    b.box("s6_cw", "CloudWatch Logs", "PHI-redacted app logs · 1 year", 1790, 680, 650, 90, res="cloudwatch_2")
    b.edge("s6_e1", "s6_u", "s6_cf")
    b.edge("s6_e2", "s6_cf", "s6_alb", "VPC origin")
    b.edge("s6_e3", "s6_alb", "s6_api", "", exit_=(1, .5), entry=(0, .5))
    b.edge("s6_e4", "s6_wrk", "s6_vpe", "private")
    b.edge("s6_e5", "s6_labs", "s6_tf")

    # 07 · Operations ----------------------------------------------------------------------------------
    b = Board(cells, "s7", "07", "Operations", 2680, 3250, 2400, 720, tag="")
    b.text("s7_a", caps("Ship a change", 16), 40, 110, 400, 26)
    b.note("s7_git", "<b>Git</b>", 40, 150, 160, 80)
    b.box("s7_ci", "CI", "build + tests", 260, 150, 240, 80, white=True)
    b.box("s7_ecr", "Amazon ECR", "images", 560, 150, 260, 80, res="ecr")
    b.box("s7_ecs", "ECS services", "rolling, health-checked", 880, 150, 300, 80, res="ecs")
    b.note("s7_rb", "<b>Unhealthy?</b> roll back to the previous task definition", 1240, 150, 420, 80)
    b.edge("s7_e1", "s7_git", "s7_ci"); b.edge("s7_e2", "s7_ci", "s7_ecr"); b.edge("s7_e3", "s7_ecr", "s7_ecs"); b.edge("s7_e4", "s7_ecs", "s7_rb")
    b.text("s7_b", caps("Watch it", 16), 40, 300, 400, 26)
    b.box("s7_t", "ECS tasks", "API · workers · parser", 40, 340, 300, 80, res="fargate")
    b.box("s7_cw", "CloudWatch", "logs · metrics · alarms", 400, 340, 300, 80, res="cloudwatch_2")
    b.note("s7_al", "<b>Alarms</b> · queue depth · error rate · model latency and cost · parser failures · quarantined files", 760, 340, 900, 80)
    b.edge("s7_e5", "s7_t", "s7_cw"); b.edge("s7_e6", "s7_cw", "s7_al")
    b.text("s7_c", caps("Keep answers right", 16), 40, 490, 400, 26)
    b.note("s7_fb", "<b>Thumbs-down + sampled chats</b>", 40, 530, 300, 90)
    b.box("s7_rs", "Regression set", "answer keys · reworded questions", 400, 530, 320, 90, white=True)
    b.decide("s7_ok", caps("Accuracy holds?", 15), 790, 520, 200, 110)
    b.box("s7_ship", "Ship", "analyzer change goes live", 1060, 530, 300, 90, white=True)
    b.edge("s7_e7", "s7_fb", "s7_rs"); b.edge("s7_e8", "s7_rs", "s7_ok"); b.edge("s7_e9", "s7_ok", "s7_ship", "yes")
    return cells
