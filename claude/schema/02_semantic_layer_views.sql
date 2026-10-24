-- STATUS: CURRENT (design) - the semantic layer (gold); replaces the 7-table analytics layer in archive/v0-schema. Partly outdated: header says only remit_event + line_match get materialized; superseded by the data-backend plan (every sem.* view materialized to semantic_layer/ each batch) | source: project knowledge file (added 2026-10-22)
-- =============================================================================
-- ClaimClarity — SEMANTIC LAYER (views over the lossless schema)
-- =============================================================================
-- The agent sees ONLY schema `sem` (10 views, one documented grain each).
-- `sem_int` holds plumbing (payer resolution, reversal ordering, 837<->835 line
-- matching) and is never exposed to the model.
--
-- Business rules encoded here (change them here, not in prompts):
--   * remitted claim      = one PCN as adjudicated by one payer, CURRENT state
--                           (latest event per PCN+payer; reversals net out).
--   * recorded denial     = CLP02 = '4' (gated by ref.tenant_setting
--                           'status4_is_recorded_denial', default true).
--   * denial signal v3    = non-reversal CLP, CLP04 = 0, and a CO-group CAS
--                           (claim or line level) whose CARC is in ref.denial_carc.
--   * denied              = recorded denial OR denial signal v3.
--   * submitted procedure = SVC06 when present, else SVC01 (TR3: SVC06 carries
--                           the submitted code only when the payer changed it).
--
-- Performance: every view is plain SQL so it stays correct as rules change.
-- In production, materialize sem_int.remit_event and sem_int.line_match to
-- Parquet at load time (they are the only expensive steps) and point the views
-- at the materialized copies.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS sem_int;
CREATE SCHEMA IF NOT EXISTS sem;

-- ---------------------------------------------------------------------------
-- sem_int: payer identity on both sides, resolved to a canonical payer_key
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW sem_int.payer_835 AS
SELECT
    p.payment_uid,
    e.name_last_or_org                                   AS payer_name_as_sent,
    COALESCE(r2u.value, e.id_value, p.trn03_payer_id)    AS payer_id_as_sent,
    COALESCE(pa.payer_key,
             upper(regexp_replace(COALESCE(e.name_last_or_org, p.trn03_payer_id, 'UNKNOWN'), '[^A-Za-z0-9]', '', 'g')))
                                                         AS payer_key,
    COALESCE(pa.payer_name, e.name_last_or_org, 'Unknown payer') AS payer_name,
    pa.payer_class
FROM r835.payment p
LEFT JOIN edi.entity e
       ON e.owner_uid = p.payment_uid AND e.loop_id = '1000A' AND e.entity_id_code = 'PR'
LEFT JOIN edi.reference r2u
       ON r2u.owner_uid = e.entity_uid AND r2u.qualifier = '2U'
LEFT JOIN ref.payer_alias pa
       ON pa.alias_source = '835_1000A'
      AND pa.alias_id IS NOT DISTINCT FROM COALESCE(r2u.value, e.id_value, p.trn03_payer_id)
      AND pa.alias_name IS NOT DISTINCT FROM e.name_last_or_org;

CREATE OR REPLACE VIEW sem_int.payer_837 AS
SELECT
    c.claim_uid,
    e.name_last_or_org AS payer_name_as_sent,
    e.id_value         AS payer_id_as_sent,
    COALESCE(pa.payer_key,
             upper(regexp_replace(COALESCE(e.name_last_or_org, e.id_value, 'UNKNOWN'), '[^A-Za-z0-9]', '', 'g')))
                       AS payer_key,
    COALESCE(pa.payer_name, e.name_last_or_org, 'Unknown payer') AS payer_name,
    pa.payer_class
FROM c837.claim c
LEFT JOIN edi.entity e
       ON e.owner_uid = c.subscriber_hl_uid AND e.loop_id = '2010BB' AND e.entity_id_code = 'PR'
LEFT JOIN ref.payer_alias pa
       ON pa.alias_source = '837_2010BB'
      AND pa.alias_id IS NOT DISTINCT FROM e.id_value
      AND pa.alias_name IS NOT DISTINCT FROM e.name_last_or_org;

-- ---------------------------------------------------------------------------
-- sem_int: every 835 claim event, with CAS rollups and reversal ordering
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW sem_int.claim_cas AS          -- claim + line CAS rolled to the CLP
WITH all_cas AS (
    SELECT a.owner_uid AS claim_payment_uid, a.adj_level, a.group_code, a.reason_code, a.amount
    FROM edi.adjustment a
    WHERE a.txn_type = '835' AND a.adj_level = 'CLAIM'
    UNION ALL
    SELECT sp.claim_payment_uid, a.adj_level, a.group_code, a.reason_code, a.amount
    FROM edi.adjustment a
    JOIN r835.service_payment sp ON sp.service_payment_uid = a.owner_uid
    WHERE a.txn_type = '835' AND a.adj_level = 'LINE'
)
SELECT
    claim_payment_uid,
    SUM(amount) FILTER (WHERE group_code = 'CO')        AS co_amount,
    SUM(amount) FILTER (WHERE group_code = 'PR')        AS pr_amount,
    SUM(amount) FILTER (WHERE group_code = 'OA')        AS oa_amount,
    SUM(amount) FILTER (WHERE group_code = 'PI')        AS pi_amount,
    SUM(amount)                                         AS total_adjustment_amount,
    bool_or(group_code = 'CO' AND reason_code IN (SELECT reason_code FROM ref.denial_carc))
                                                        AS has_co_denial_carc,
    list_sort(list_distinct(list(group_code || '-' || reason_code))) AS adjustment_codes
FROM all_cas
GROUP BY claim_payment_uid;

CREATE OR REPLACE VIEW sem_int.remit_event AS
WITH dates AS (
    SELECT owner_uid,
           MIN(date_start) FILTER (WHERE qualifier IN ('232','472','150')) AS svc_from,
           MAX(date_end)   FILTER (WHERE qualifier IN ('233','472','151')) AS svc_to,
           MIN(date_start) FILTER (WHERE qualifier = '050')               AS payer_received_date
    FROM edi.date_time
    WHERE owner_table IN ('r835.claim_payment', 'r835.service_payment')
    GROUP BY owner_uid
),
line_dates AS (
    SELECT sp.claim_payment_uid, MIN(d.svc_from) AS svc_from, MAX(d.svc_to) AS svc_to
    FROM r835.service_payment sp JOIN dates d ON d.owner_uid = sp.service_payment_uid
    GROUP BY sp.claim_payment_uid
),
prod AS (
    SELECT owner_uid AS payment_uid, MIN(date_start) AS production_date
    FROM edi.date_time WHERE owner_table = 'r835.payment' AND qualifier = '405'
    GROUP BY owner_uid
),
s4 AS (
    SELECT COALESCE(MAX(value) = 'true', true) AS status4_is_denial
    FROM ref.tenant_setting WHERE key = 'status4_is_recorded_denial'
),
ev AS (
    SELECT
        cp.claim_payment_uid,
        cp.payment_uid,
        cp.file_id,
        cp.clp01_patient_control_number                          AS claim_key,
        py.payer_key, py.payer_name, py.payer_class,
        cp.clp02_claim_status                                    AS claim_status_code,
        cs.description                                           AS claim_status,
        COALESCE(cs.payer_sequence, 'unknown')                   AS payer_sequence,
        COALESCE(cs.is_reversal, false)                          AS is_reversal,
        cp.clp07_payer_claim_control_number                      AS payer_claim_control_number,
        cp.clp06_filing_indicator                                AS filing_indicator,
        cp.clp09_frequency_code                                  AS frequency_code,
        cp.clp03_total_charge                                    AS charge_amount,
        cp.clp04_total_paid                                      AS paid_amount,
        cp.clp05_patient_responsibility                          AS patient_responsibility_amount,
        COALESCE(cc.co_amount, 0)  AS co_amount,
        COALESCE(cc.pr_amount, 0)  AS pr_amount,
        COALESCE(cc.oa_amount, 0)  AS oa_amount,
        COALESCE(cc.pi_amount, 0)  AS pi_amount,
        COALESCE(cc.adjustment_codes, [])                        AS adjustment_codes,
        p.bpr16_effective_date                                   AS payment_date,
        pr.production_date,
        p.trn02_check_eft_number                                 AS check_or_eft_number,
        p.bpr04_payment_method                                   AS payment_method,
        COALESCE(d.svc_from, ld.svc_from)                        AS service_date_from,
        COALESCE(d.svc_to,   ld.svc_to,  d.svc_from, ld.svc_from) AS service_date_to,
        d.payer_received_date,
        sf.received_at                                           AS file_received_at,
        (cp.clp02_claim_status = '4' AND s4.status4_is_denial)   AS is_denied_recorded,
        (NOT COALESCE(cs.is_reversal, false)
            AND COALESCE(cp.clp04_total_paid, 0) = 0
            AND COALESCE(cc.has_co_denial_carc, false))          AS is_denied_signal_v3,
        ROW_NUMBER() OVER (
            PARTITION BY cp.clp01_patient_control_number, py.payer_key
            ORDER BY p.bpr16_effective_date DESC NULLS LAST,
                     sf.received_at DESC NULLS LAST,
                     cp.file_id DESC,
                     cp.seg_ord DESC)                             AS event_rank_desc,
        COUNT(*) OVER (PARTITION BY cp.clp01_patient_control_number, py.payer_key) AS events_for_claim_payer
    FROM r835.claim_payment cp
    JOIN r835.payment p          ON p.payment_uid = cp.payment_uid
    JOIN edi.source_file sf      ON sf.file_id = cp.file_id
    JOIN sem_int.payer_835 py    ON py.payment_uid = cp.payment_uid
    LEFT JOIN ref.claim_status cs ON cs.code = cp.clp02_claim_status
    LEFT JOIN sem_int.claim_cas cc ON cc.claim_payment_uid = cp.claim_payment_uid
    LEFT JOIN dates d             ON d.owner_uid = cp.claim_payment_uid
    LEFT JOIN line_dates ld       ON ld.claim_payment_uid = cp.claim_payment_uid
    LEFT JOIN prod pr             ON pr.payment_uid = cp.payment_uid
    CROSS JOIN s4
    WHERE sf.superseded_by_file_id IS NULL
      AND sf.duplicate_of_file_id IS NULL                       -- identical re-deliveries counted once
)
SELECT ev.*,
       (event_rank_desc = 1 AND NOT is_reversal)                AS is_current_adjudication,
       CASE WHEN is_reversal                              THEN 'reversal'
            WHEN event_rank_desc = 1                      THEN 'current'
            ELSE 'superseded' END                              AS adjudication_state,
       (is_denied_recorded OR is_denied_signal_v3)             AS is_denied
FROM ev;

-- ---------------------------------------------------------------------------
-- sem_int: 837 submissions with "latest submission per PCN"
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW sem_int.submission AS
SELECT c.*,
       t.bht04_date                         AS submission_date,
       sf.received_at                       AS file_received_at,
       ROW_NUMBER() OVER (PARTITION BY c.clm01_patient_control_number
                          ORDER BY t.bht04_date DESC NULLS LAST, sf.received_at DESC NULLS LAST,
                                   c.file_id DESC, c.seg_ord DESC) AS submission_rank_desc,
       COUNT(*) OVER (PARTITION BY c.clm01_patient_control_number) AS submissions_for_claim
FROM c837.claim c
JOIN edi.transaction_set t ON t.txn_uid = c.txn_uid
JOIN edi.source_file sf    ON sf.file_id = c.file_id
WHERE sf.superseded_by_file_id IS NULL AND sf.duplicate_of_file_id IS NULL;

-- ---------------------------------------------------------------------------
-- sem_int: 1:1 match of 837 lines (latest submission) to 835 lines
--          (current, non-secondary adjudication). Priority:
--          (1) REF*6R line item control number, (2) procedure + mod1 + charge,
--          (3) procedure only; ties broken by line order.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW sem_int.line_match AS
WITH l837 AS (
    SELECT sl.line_uid, s.clm01_patient_control_number AS claim_key, sl.lx01_line_number AS ord,
           sl.line_item_control_number AS lic, sl.sv101_2_procedure_code AS proc,
           sl.sv101_3_mod1 AS mod1, sl.sv102_charge AS charge
    FROM c837.service_line sl
    JOIN sem_int.submission s ON s.claim_uid = sl.claim_uid AND s.submission_rank_desc = 1
),
l835 AS (
    SELECT sp.service_payment_uid, e.claim_key, sp.svc_ord_in_claim AS ord,
           sp.line_item_control_number AS lic,
           COALESCE(sp.svc06_2_procedure_code, sp.svc01_2_procedure_code) AS proc,
           CASE WHEN sp.svc06_2_procedure_code IS NOT NULL THEN sp.svc06_3_mod1 ELSE sp.svc01_3_mod1 END AS mod1,
           sp.svc02_charge AS charge
    FROM r835.service_payment sp
    JOIN sem_int.remit_event e ON e.claim_payment_uid = sp.claim_payment_uid
    WHERE e.is_current_adjudication AND e.payer_sequence IN ('primary', 'unknown')
),
cand AS (
    SELECT a.line_uid, b.service_payment_uid,
           CASE WHEN a.lic IS NOT NULL AND a.lic = b.lic                          THEN 1
                WHEN a.proc = b.proc AND a.mod1 IS NOT DISTINCT FROM b.mod1
                     AND a.charge = b.charge                                       THEN 2
                WHEN a.proc = b.proc                                               THEN 3 END AS match_tier,
           abs(a.ord - b.ord) AS ord_gap
    FROM l837 a JOIN l835 b ON a.claim_key = b.claim_key
),
ranked AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY line_uid            ORDER BY match_tier, ord_gap, service_payment_uid) AS r1,
        ROW_NUMBER() OVER (PARTITION BY service_payment_uid ORDER BY match_tier, ord_gap, line_uid)            AS r2
    FROM cand WHERE match_tier IS NOT NULL
)
SELECT line_uid, service_payment_uid,
       CASE match_tier WHEN 1 THEN 'line_control_number' WHEN 2 THEN 'procedure_modifier_charge'
                       ELSE 'procedure_only' END AS match_method
FROM ranked WHERE r1 = 1 AND r2 = 1;

-- =============================================================================
-- PUBLIC SEMANTIC VIEWS (what the agent is allowed to see)
-- =============================================================================

-- 1) sem.remit_claims — GRAIN: one 835 claim adjudication event (one CLP).
--    Use is_current_adjudication = true for "how did claims end up".
CREATE OR REPLACE VIEW sem.remit_claims AS
SELECT
    claim_payment_uid            AS remit_claim_id,
    claim_key,                                   -- patient control number (CLM01 = CLP01)
    claim_key || '|' || payer_key AS claim_payer_key,
    payer_key, payer_name, payer_class,
    payer_sequence, claim_status_code, claim_status,
    adjudication_state, is_current_adjudication, is_reversal,
    is_denied, is_denied_recorded, is_denied_signal_v3,
    payer_claim_control_number, filing_indicator, frequency_code,
    charge_amount, paid_amount, patient_responsibility_amount,
    co_amount, pr_amount, oa_amount, pi_amount, adjustment_codes,
    payment_date, date_trunc('month', payment_date)::DATE AS payment_month,
    production_date, check_or_eft_number, payment_method,
    service_date_from, service_date_to,
    date_trunc('month', service_date_from)::DATE AS service_month,
    payer_received_date,
    file_id                      AS source_file_id
FROM sem_int.remit_event;

-- 2) sem.remit_lines — GRAIN: one 835 service line (one SVC), with its claim's context.
CREATE OR REPLACE VIEW sem.remit_lines AS
WITH line_cas AS (
    SELECT owner_uid AS service_payment_uid,
           SUM(amount) FILTER (WHERE group_code = 'CO') AS co_amount,
           SUM(amount) FILTER (WHERE group_code = 'PR') AS pr_amount,
           SUM(amount) FILTER (WHERE group_code = 'OA') AS oa_amount,
           SUM(amount) FILTER (WHERE group_code = 'PI') AS pi_amount,
           bool_or(group_code = 'CO' AND reason_code IN (SELECT reason_code FROM ref.denial_carc)) AS has_co_denial_carc,
           list_sort(list_distinct(list(group_code || '-' || reason_code))) AS adjustment_codes
    FROM edi.adjustment WHERE txn_type = '835' AND adj_level = 'LINE'
    GROUP BY owner_uid
),
allowed AS (
    SELECT owner_uid AS service_payment_uid, SUM(amount) AS allowed_amount
    FROM edi.amount WHERE owner_table = 'r835.service_payment' AND qualifier = 'B6'
    GROUP BY owner_uid
),
ldate AS (
    SELECT owner_uid AS service_payment_uid, MIN(date_start) AS service_date
    FROM edi.date_time WHERE owner_table = 'r835.service_payment' AND qualifier IN ('472', '150')
    GROUP BY owner_uid
),
rmk AS (
    SELECT owner_uid AS service_payment_uid, list_sort(list_distinct(list(remark_code))) AS remark_codes
    FROM r835.remark WHERE remark_level = 'LINE' GROUP BY owner_uid
)
SELECT
    sp.service_payment_uid                                     AS remit_line_id,
    e.claim_payment_uid                                        AS remit_claim_id,
    e.claim_key, e.claim_key || '|' || e.payer_key             AS claim_payer_key,
    e.payer_key, e.payer_name, e.payer_class, e.payer_sequence,
    e.claim_status_code, e.claim_status, e.adjudication_state,
    e.is_current_adjudication, e.is_reversal,
    e.is_denied                                                AS claim_is_denied,
    COALESCE(sp.svc06_2_procedure_code, sp.svc01_2_procedure_code) AS procedure_code,
    CASE WHEN sp.svc06_2_procedure_code IS NOT NULL THEN sp.svc06_3_mod1 ELSE sp.svc01_3_mod1 END AS modifier_1,
    CASE WHEN sp.svc06_2_procedure_code IS NOT NULL THEN sp.svc06_4_mod2 ELSE sp.svc01_4_mod2 END AS modifier_2,
    sp.svc01_2_procedure_code                                  AS adjudicated_procedure_code,
    (sp.svc06_2_procedure_code IS NOT NULL)                    AS procedure_changed_by_payer,
    sp.svc01_1_qualifier                                       AS procedure_code_type,
    sp.svc04_revenue_code                                      AS revenue_code,
    sp.svc02_charge                                            AS line_charge_amount,
    sp.svc03_paid                                              AS line_paid_amount,
    a.allowed_amount                                           AS line_allowed_amount,
    sp.svc05_units_paid                                        AS units_paid,
    sp.svc07_original_units                                    AS units_billed,
    COALESCE(lc.co_amount, 0) AS line_co_amount, COALESCE(lc.pr_amount, 0) AS line_pr_amount,
    COALESCE(lc.oa_amount, 0) AS line_oa_amount, COALESCE(lc.pi_amount, 0) AS line_pi_amount,
    COALESCE(lc.adjustment_codes, [])                          AS line_adjustment_codes,
    COALESCE(r.remark_codes, [])                               AS line_remark_codes,
    (NOT e.is_reversal AND COALESCE(sp.svc03_paid, 0) = 0
        AND (COALESCE(lc.has_co_denial_carc, false) OR e.is_denied)) AS line_is_denied,
    COALESCE(ld.service_date, e.service_date_from)             AS service_date,
    date_trunc('month', COALESCE(ld.service_date, e.service_date_from))::DATE AS service_month,
    e.payment_date, date_trunc('month', e.payment_date)::DATE  AS payment_month,
    sp.line_item_control_number,
    e.file_id                                                  AS source_file_id
FROM r835.service_payment sp
JOIN sem_int.remit_event e   ON e.claim_payment_uid = sp.claim_payment_uid
LEFT JOIN line_cas lc        ON lc.service_payment_uid = sp.service_payment_uid
LEFT JOIN allowed a          ON a.service_payment_uid  = sp.service_payment_uid
LEFT JOIN ldate ld           ON ld.service_payment_uid = sp.service_payment_uid
LEFT JOIN rmk r              ON r.service_payment_uid  = sp.service_payment_uid;

-- 3) sem.claims — GRAIN: one 837P claim submission (one CLM).
--    is_latest_submission = true gives one row per claim_key. Outcome columns
--    roll up the CURRENT 835 adjudications for that claim_key (all payers).
CREATE OR REPLACE VIEW sem.claims AS
WITH outcome AS (
    SELECT claim_key,
           COUNT(*)                                                    AS current_adjudication_count,
           bool_or(is_denied) FILTER (WHERE payer_sequence IN ('primary','unknown')) AS primary_is_denied,
           bool_or(is_denied)                                          AS any_payer_denied,
           SUM(paid_amount)                                            AS remit_paid_amount,
           SUM(patient_responsibility_amount)                          AS remit_patient_responsibility_amount,
           SUM(co_amount)                                              AS remit_co_amount,
           MIN(payment_date)                                           AS first_payment_date,
           MAX(payment_date)                                           AS last_payment_date,
           arg_max(claim_status, payment_date)                         AS latest_claim_status
    FROM sem_int.remit_event WHERE is_current_adjudication
    GROUP BY claim_key
),
any_remit AS (
    SELECT claim_key, MIN(payment_date) AS first_remit_date, bool_or(is_reversal) AS ever_reversed
    FROM sem_int.remit_event GROUP BY claim_key
),
dx AS (
    SELECT claim_uid,
           MIN(code) FILTER (WHERE code_list_qualifier IN ('ABK','BK')) AS principal_diagnosis_code,
           list(code ORDER BY code_sequence) FILTER (WHERE code_list_qualifier IN ('ABK','ABF','BK','BF')) AS diagnosis_codes
    FROM c837.claim_code GROUP BY claim_uid
),
dos AS (
    SELECT sl.claim_uid, MIN(d.date_start) AS svc_from, MAX(d.date_end) AS svc_to
    FROM c837.service_line sl
    JOIN edi.date_time d ON d.owner_uid = sl.line_uid AND d.qualifier = '472'
    GROUP BY sl.claim_uid
),
bill AS (
    SELECT owner_uid AS billing_hl_uid, arg_min(id_value, seg_ord) AS billing_npi,
           arg_min(name_last_or_org, seg_ord) AS billing_provider_name,
           arg_min(state, seg_ord) AS billing_provider_state
    FROM edi.entity WHERE loop_id = '2010AA' AND entity_id_code = '85'
    GROUP BY owner_uid
),
rend AS (
    SELECT owner_uid AS claim_uid, arg_min(id_value, seg_ord) AS rendering_npi
    FROM edi.entity WHERE loop_id = '2310B' AND entity_id_code = '82'
    GROUP BY owner_uid
),
reff AS (
    SELECT owner_uid AS claim_uid, arg_min(id_value, seg_ord) AS referring_npi
    FROM edi.entity WHERE loop_id = '2310A' AND entity_id_code = 'DN'
    GROUP BY owner_uid
)
SELECT
    s.claim_uid                                   AS claim_id,
    s.clm01_patient_control_number                AS claim_key,
    s.submission_rank_desc = 1                    AS is_latest_submission,
    s.submissions_for_claim                       AS submission_count,
    s.clm05_3_frequency_code                      AS frequency_code,
    fq.description                                AS frequency,
    py.payer_key, py.payer_name, py.payer_class,
    hl.sbr01_payer_resp_seq                       AS payer_responsibility,
    hl.sbr09_filing_indicator                     AS filing_indicator,
    s.clm02_total_charge                          AS charge_amount,
    s.clm05_1_place_of_service                    AS place_of_service,
    s.line_count,
    dx.principal_diagnosis_code, dx.diagnosis_codes,
    dos.svc_from                                  AS service_date_from,
    dos.svc_to                                    AS service_date_to,
    date_trunc('month', dos.svc_from)::DATE       AS service_month,
    s.submission_date,
    date_trunc('month', s.submission_date)::DATE  AS submission_month,
    bill.billing_npi, bill.billing_provider_name, bill.billing_provider_state,
    rend.rendering_npi, reff.referring_npi,
    -- outcome (from 835)
    (ar.claim_key IS NOT NULL)                    AS has_remit,
    COALESCE(ar.ever_reversed, false)             AS ever_reversed,
    o.current_adjudication_count,
    o.latest_claim_status,
    COALESCE(o.primary_is_denied, false)          AS is_denied,
    COALESCE(o.any_payer_denied, false)           AS any_payer_denied,
    o.remit_paid_amount, o.remit_patient_responsibility_amount, o.remit_co_amount,
    o.first_payment_date, o.last_payment_date,
    date_diff('day', s.submission_date, ar.first_remit_date) AS days_submission_to_first_remit,
    s.file_id                                     AS source_file_id
FROM sem_int.submission s
JOIN c837.hl hl                ON hl.hl_uid = s.subscriber_hl_uid
JOIN sem_int.payer_837 py      ON py.claim_uid = s.claim_uid
LEFT JOIN ref.claim_frequency fq ON fq.code = s.clm05_3_frequency_code
LEFT JOIN dx                   ON dx.claim_uid = s.claim_uid
LEFT JOIN dos                  ON dos.claim_uid = s.claim_uid
LEFT JOIN bill                 ON bill.billing_hl_uid = s.billing_hl_uid
LEFT JOIN rend                 ON rend.claim_uid = s.claim_uid
LEFT JOIN reff                 ON reff.claim_uid = s.claim_uid
LEFT JOIN outcome o            ON o.claim_key = s.clm01_patient_control_number
LEFT JOIN any_remit ar         ON ar.claim_key = s.clm01_patient_control_number;

-- 4) sem.claim_lines — GRAIN: one 837P service line (one LX/SV1) with its
--    matched primary 835 outcome (latest submission lines only get matches).
CREATE OR REPLACE VIEW sem.claim_lines AS
WITH ldate AS (
    SELECT owner_uid AS line_uid, MIN(date_start) AS service_date
    FROM edi.date_time WHERE owner_table = 'c837.service_line' AND qualifier = '472'
    GROUP BY owner_uid
)
SELECT
    sl.line_uid                                   AS claim_line_id,
    c.claim_id, c.claim_key, c.is_latest_submission,
    c.payer_key, c.payer_name, c.payer_class, c.filing_indicator,
    sl.lx01_line_number                           AS line_number,
    sl.sv101_2_procedure_code                     AS procedure_code,
    sl.sv101_3_mod1 AS modifier_1, sl.sv101_4_mod2 AS modifier_2,
    sl.sv101_5_mod3 AS modifier_3, sl.sv101_6_mod4 AS modifier_4,
    sl.sv101_1_qualifier                          AS procedure_code_type,
    sl.sv102_charge                               AS line_charge_amount,
    sl.sv104_units                                AS units_billed,
    COALESCE(sl.sv105_place_of_service, c.place_of_service) AS place_of_service,
    sl.sv107_dx_pointers                          AS diagnosis_pointers,
    ld.service_date, date_trunc('month', ld.service_date)::DATE AS service_month,
    c.submission_date, c.billing_npi, c.rendering_npi,
    -- matched remit outcome
    (m.service_payment_uid IS NOT NULL)           AS has_line_remit,
    m.match_method                                AS remit_match_method,
    rl.line_paid_amount, rl.line_allowed_amount, rl.line_co_amount, rl.line_pr_amount,
    rl.line_is_denied, rl.line_adjustment_codes, rl.line_remark_codes,
    rl.adjudicated_procedure_code, rl.procedure_changed_by_payer,
    rl.payment_date,
    sl.line_item_control_number,
    c.source_file_id
FROM c837.service_line sl
JOIN sem.claims c              ON c.claim_id = sl.claim_uid
LEFT JOIN ldate ld             ON ld.line_uid = sl.line_uid
LEFT JOIN sem_int.line_match m ON m.line_uid = sl.line_uid
LEFT JOIN sem.remit_lines rl   ON rl.remit_line_id = m.service_payment_uid;

-- 5) sem.adjustments — GRAIN: one CAS reason/amount pair on an 835 (claim or line level).
CREATE OR REPLACE VIEW sem.adjustments AS
SELECT
    a.adj_uid                                     AS adjustment_id,
    e.claim_payment_uid                           AS remit_claim_id,
    CASE WHEN a.adj_level = 'LINE' THEN a.owner_uid END AS remit_line_id,
    e.claim_key, e.payer_key, e.payer_name, e.payer_class,
    e.is_current_adjudication, e.is_reversal, e.adjudication_state,
    a.adj_level                                   AS adjustment_level,
    a.group_code, g.description                   AS group_description, g.responsible_party,
    a.reason_code                                 AS carc_code,
    carc.description                              AS carc_description,
    carc.category                                 AS carc_category,
    (a.reason_code IN (SELECT reason_code FROM ref.denial_carc)) AS is_denial_carc,
    a.amount                                      AS adjustment_amount,
    a.quantity                                    AS adjustment_quantity,
    COALESCE(sp.svc06_2_procedure_code, sp.svc01_2_procedure_code) AS procedure_code,
    e.payment_date, date_trunc('month', e.payment_date)::DATE AS payment_month,
    e.service_date_from                           AS service_date,
    e.file_id                                     AS source_file_id
FROM edi.adjustment a
LEFT JOIN r835.service_payment sp
       ON a.adj_level = 'LINE' AND sp.service_payment_uid = a.owner_uid
JOIN sem_int.remit_event e
       ON e.claim_payment_uid = CASE WHEN a.adj_level = 'LINE' THEN sp.claim_payment_uid ELSE a.owner_uid END
LEFT JOIN ref.adjustment_group g ON g.code = a.group_code
LEFT JOIN ref.carc carc          ON carc.code = a.reason_code
WHERE a.txn_type = '835';

-- 6) sem.remarks — GRAIN: one RARC on an 835 claim (MOA/MIA) or line (LQ).
CREATE OR REPLACE VIEW sem.remarks AS
SELECT
    r.remark_uid AS remark_id,
    e.claim_payment_uid AS remit_claim_id,
    CASE WHEN r.remark_level = 'LINE' THEN r.owner_uid END AS remit_line_id,
    e.claim_key, e.payer_key, e.payer_name, e.is_current_adjudication,
    r.remark_level, r.remark_code, rc.description AS remark_description,
    COALESCE(sp.svc06_2_procedure_code, sp.svc01_2_procedure_code) AS procedure_code,
    e.payment_date, date_trunc('month', e.payment_date)::DATE AS payment_month
FROM r835.remark r
LEFT JOIN r835.service_payment sp ON r.remark_level = 'LINE' AND sp.service_payment_uid = r.owner_uid
JOIN sem_int.remit_event e
  ON e.claim_payment_uid = CASE WHEN r.remark_level = 'LINE' THEN sp.claim_payment_uid ELSE r.owner_uid END
LEFT JOIN ref.rarc rc ON rc.code = r.remark_code;

-- 7) sem.payments — GRAIN: one 835 payment (one check/EFT/advice = one ST/BPR).
CREATE OR REPLACE VIEW sem.payments AS
WITH clp AS (
    SELECT payment_uid, COUNT(*) AS claim_count,
           COUNT(*) FILTER (WHERE is_reversal) AS reversal_count,
           SUM(paid_amount) AS claims_paid_amount
    FROM sem_int.remit_event GROUP BY payment_uid
),
plb AS (
    SELECT payment_uid, SUM(amount) AS provider_adjustment_amount
    FROM r835.provider_adjustment GROUP BY payment_uid
)
SELECT
    p.payment_uid                     AS payment_id,
    py.payer_key, py.payer_name, py.payer_class,
    p.trn02_check_eft_number          AS check_or_eft_number,
    p.bpr04_payment_method            AS payment_method,
    p.bpr01_transaction_handling      AS transaction_handling,
    p.bpr02_total_paid                AS total_payment_amount,
    p.bpr16_effective_date            AS payment_date,
    date_trunc('month', p.bpr16_effective_date)::DATE AS payment_month,
    COALESCE(clp.claim_count, 0)      AS claim_count,
    COALESCE(clp.reversal_count, 0)   AS reversal_count,
    clp.claims_paid_amount,
    COALESCE(plb.provider_adjustment_amount, 0) AS provider_adjustment_amount,
    -- 835 balancing: BPR02 = sum(CLP04) - sum(PLB amounts)
    (p.bpr02_total_paid = COALESCE(clp.claims_paid_amount, 0) - COALESCE(plb.provider_adjustment_amount, 0))
                                      AS is_balanced,
    p.file_id                         AS source_file_id
FROM r835.payment p
JOIN sem_int.payer_835 py ON py.payment_uid = p.payment_uid
LEFT JOIN clp ON clp.payment_uid = p.payment_uid
LEFT JOIN plb ON plb.payment_uid = p.payment_uid;

-- 8) sem.provider_adjustments — GRAIN: one PLB reason/amount pair.
CREATE OR REPLACE VIEW sem.provider_adjustments AS
SELECT
    pa.plb_uid AS provider_adjustment_id, pa.payment_uid AS payment_id,
    py.payer_key, py.payer_name,
    pa.reason_code AS plb_reason_code, r.description AS plb_reason_description,
    pa.reference_id, pa.amount AS provider_adjustment_amount,
    pa.plb02_fiscal_period_date AS fiscal_period_date,
    p.bpr16_effective_date AS payment_date, date_trunc('month', p.bpr16_effective_date)::DATE AS payment_month
FROM r835.provider_adjustment pa
JOIN r835.payment p ON p.payment_uid = pa.payment_uid
JOIN sem_int.payer_835 py ON py.payment_uid = pa.payment_uid
LEFT JOIN ref.plb_reason r ON r.code = pa.reason_code;

-- 9) sem.diagnoses — GRAIN: one diagnosis code on one 837P claim submission.
CREATE OR REPLACE VIEW sem.diagnoses AS
SELECT cc.claim_code_uid AS diagnosis_id, c.claim_id, c.claim_key, c.is_latest_submission,
       c.payer_key, c.payer_name,
       cc.code AS diagnosis_code, cc.code_sequence AS diagnosis_sequence,
       cc.code_list_qualifier IN ('ABK','BK') AS is_principal,
       c.service_month, c.is_denied AS claim_is_denied
FROM c837.claim_code cc
JOIN sem.claims c ON c.claim_id = cc.claim_uid
WHERE cc.code_list_qualifier IN ('ABK','ABF','BK','BF');

-- 10) sem.payers — GRAIN: one canonical payer (dimension, for name lookup).
CREATE OR REPLACE VIEW sem.payers AS
WITH s AS (
    SELECT payer_key, payer_name, payer_class, payer_name_as_sent, payer_id_as_sent, '837' AS side FROM sem_int.payer_837
    UNION ALL
    SELECT payer_key, payer_name, payer_class, payer_name_as_sent, payer_id_as_sent, '835' FROM sem_int.payer_835
)
SELECT payer_key,
       any_value(payer_name)  AS payer_name,
       any_value(payer_class) AS payer_class,
       list_sort(list_distinct(list(payer_name_as_sent) FILTER (WHERE payer_name_as_sent IS NOT NULL))) AS names_as_sent,
       list_sort(list_distinct(list(payer_id_as_sent)   FILTER (WHERE payer_id_as_sent IS NOT NULL)))   AS ids_as_sent,
       COUNT(*) FILTER (WHERE side = '837') AS claim_submissions,
       COUNT(*) FILTER (WHERE side = '835') AS payments
FROM s GROUP BY payer_key;

-- Engineers only (not in the agent catalog): file inventory with S3 location.
CREATE OR REPLACE VIEW sem_int.source_files AS
SELECT file_id, file_kind, s3_uri, s3_version_id, original_filename, size_bytes,
       received_at, ingested_at, parse_status, transaction_count, segment_count,
       duplicate_of_file_id, superseded_by_file_id, ingest_run_id
FROM edi.source_file;
