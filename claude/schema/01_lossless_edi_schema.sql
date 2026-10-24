-- STATUS: CURRENT (design) - the lossless (silver) schema; replaces archive/v0-schema/cc-lossless-parquet-schema.md. Open: how superseded files are recorded on edi.source_file | source: project knowledge file (added 2026-10-22)
-- =============================================================================
-- CC — LOSSLESS EDI STORAGE SCHEMA (835 5010X221A1 + 837P 5010X222A1)
-- Target: DuckDB over Parquet (one Parquet dataset per table), one copy per
--         customer bucket/prefix (tenant isolation is physical, not a column).
-- =============================================================================
--
-- DESIGN RULES (read these before changing anything)
--
-- 1. Two levels of losslessness.
--    * BYTE-LEVEL: edi.segment stores every segment of every file, in order,
--      with the raw text and the parsed element list. Together with the
--      delimiters on edi.interchange, any source file can be re-emitted
--      byte-for-byte. This is the ground truth.
--    * ELEMENT-LEVEL: every data element of every segment also lands in a
--      typed column (or a typed child row) somewhere below. Nothing is dropped
--      by the typed tables; composites are kept raw AND split.
--    Every typed row carries (file_id, seg_ord) pointing back to the segment
--    that produced it, so any value can be traced to its exact byte location.
--
-- 2. Keys are deterministic surrogates, never business identifiers.
--    <thing>_uid = file_id || ':' || seg_ord_of_the_loop's_first_segment.
--    Re-ingesting the same file yields the same uids (idempotent loads), and
--    uniqueness is guaranteed. CLM01 / CLP01 / CLP07 are attributes, NOT keys:
--    one PCN legitimately appears many times (resubmissions, reversals,
--    corrections, secondary payers, duplicate files). Keying on them is what
--    produced the 8,294 duplicate keys in keys.remittances.
--
-- 3. Repeating "generic" segments (REF, DTP/DTM, AMT, QTY, NTE, PER, CAS, NM1
--    loops) are stored once, in shared child tables keyed by owner_uid +
--    owner_table, instead of exploding into dozens of near-identical tables.
--
-- 4. Codes are stored exactly as sent. Descriptions live in ref.* tables.
--    Amounts are DECIMAL(18,2) (X12 R type), quantities DECIMAL(18,4),
--    dates are DATE with the raw text kept where formats vary (DTP/DTM).
--
-- 5. Derived business logic (denials, current adjudication, 837<->835 match)
--    does NOT live here. It lives in the semantic layer (02_*.sql) so it can
--    change without re-ingesting.
--
-- Suggested Parquet layout (per tenant):
--   s3://<tenant-bucket>/cc/lossless/<schema>.<table>/
--        file_kind=<835|837P>/received_month=YYYY-MM/part-*.parquet
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS edi;   -- envelopes, provenance, shared children
CREATE SCHEMA IF NOT EXISTS c837;  -- 837P-specific loops
CREATE SCHEMA IF NOT EXISTS r835;  -- 835-specific loops
CREATE SCHEMA IF NOT EXISTS ref;   -- code sets & tenant configuration

-- =============================================================================
-- A. PROVENANCE: ingest runs and the S3 location of every source file
-- =============================================================================

CREATE TABLE edi.ingest_run (
    ingest_run_id        VARCHAR PRIMARY KEY,      -- ULID
    started_at           TIMESTAMP NOT NULL,
    finished_at          TIMESTAMP,
    status               VARCHAR NOT NULL,         -- RUNNING | SUCCEEDED | FAILED | PARTIAL
    parser_name          VARCHAR NOT NULL,
    parser_version       VARCHAR NOT NULL,         -- git sha / semver of the parser
    schema_version       VARCHAR NOT NULL,         -- version of THIS ddl
    files_seen           INTEGER,
    files_loaded         INTEGER,
    files_rejected       INTEGER,
    triggered_by         VARCHAR,                  -- user / scheduler / lambda
    notes                VARCHAR
);

-- The S3 location table. One row per physical object version ingested.
CREATE TABLE edi.source_file (
    file_id              VARCHAR PRIMARY KEY,      -- sha256 of raw bytes (hex). Content address => dedupe.
    tenant_code          VARCHAR NOT NULL,         -- e.g. 'LABCORP' (redundant w/ bucket, useful in exports)
    file_kind            VARCHAR NOT NULL,         -- '835' | '837P' | 'UNKNOWN'
    s3_uri               VARCHAR NOT NULL,         -- s3://bucket/key (canonical, for engineers)
    s3_bucket            VARCHAR NOT NULL,
    s3_key               VARCHAR NOT NULL,
    s3_version_id        VARCHAR,                  -- object version (bucket versioning on)
    s3_etag              VARCHAR,
    s3_last_modified     TIMESTAMP,
    s3_storage_class     VARCHAR,
    s3_region            VARCHAR,
    kms_key_arn          VARCHAR,                  -- SSE-KMS key used (PHI audit)
    original_filename    VARCHAR,                  -- name as delivered by SFTP / clearinghouse
    source_system        VARCHAR,                  -- e.g. 'SFTP', 'CLEARINGHOUSE_X', 'CONVERSION_DUMP'
    size_bytes           BIGINT NOT NULL,
    sha256               VARCHAR NOT NULL,         -- same as file_id, kept explicit
    compression          VARCHAR,                  -- none | gzip | zip (member name in original_filename)
    character_encoding   VARCHAR,                  -- detected (ASCII / UTF-8 / latin-1)
    received_at          TIMESTAMP,                -- when it landed in S3
    ingest_run_id        VARCHAR NOT NULL REFERENCES edi.ingest_run(ingest_run_id),
    ingested_at          TIMESTAMP NOT NULL,
    parse_status         VARCHAR NOT NULL,         -- PARSED | PARSED_WITH_WARNINGS | REJECTED
    parse_messages       VARCHAR[],                -- validation warnings/errors
    segment_count        INTEGER,
    interchange_count    INTEGER,
    transaction_count    INTEGER,
    duplicate_of_file_id VARCHAR,                  -- same bytes seen earlier under another key
    superseded_by_file_id VARCHAR                  -- if a later delivery replaces this file
);

-- =============================================================================
-- B. BYTE-LEVEL SPINE: every segment, in order
-- =============================================================================

CREATE TABLE edi.segment (
    file_id              VARCHAR NOT NULL REFERENCES edi.source_file(file_id),
    seg_ord              INTEGER NOT NULL,         -- 0-based position in file
    interchange_uid      VARCHAR,
    group_uid            VARCHAR,
    txn_uid              VARCHAR,                  -- NULL for ISA/GS/GE/IEA
    seg_ord_in_txn       INTEGER,                  -- ST = 1 (matches SE01 counting)
    segment_id           VARCHAR NOT NULL,         -- 'CLP', 'SV1', ...
    loop_id              VARCHAR,                  -- '2100', '2400', '2010BB', ...
    loop_path            VARCHAR,                  -- e.g. '2000A/2000B/2300/2400' (TR3 path)
    loop_instance_uid    VARCHAR,                  -- uid of the loop instance this segment belongs to
    elements             VARCHAR[] NOT NULL,       -- elements[1] = XX01 (raw, composites unsplit)
    raw_text             VARCHAR NOT NULL,         -- exact bytes incl. element separators, excl. terminator
    byte_offset          BIGINT,                   -- offset in decompressed file
    PRIMARY KEY (file_id, seg_ord)
);

-- =============================================================================
-- C. ENVELOPES
-- =============================================================================

CREATE TABLE edi.interchange (                      -- ISA / IEA
    interchange_uid      VARCHAR PRIMARY KEY,
    file_id              VARCHAR NOT NULL REFERENCES edi.source_file(file_id),
    seg_ord              INTEGER NOT NULL,
    element_separator    VARCHAR NOT NULL,         -- ISA position 4
    component_separator  VARCHAR NOT NULL,         -- ISA16
    repetition_separator VARCHAR,                  -- ISA11 (5010)
    segment_terminator   VARCHAR NOT NULL,         -- incl. trailing CR/LF if present
    isa01_auth_qual VARCHAR, isa02_auth_info VARCHAR,
    isa03_sec_qual  VARCHAR, isa04_sec_info  VARCHAR,
    isa05_sender_qual VARCHAR, isa06_sender_id VARCHAR,
    isa07_receiver_qual VARCHAR, isa08_receiver_id VARCHAR,
    isa09_date VARCHAR, isa10_time VARCHAR,
    isa12_version VARCHAR, isa13_control_number VARCHAR,
    isa14_ack_requested VARCHAR, isa15_usage_indicator VARCHAR,   -- P / T
    interchange_datetime TIMESTAMP,                -- parsed ISA09+ISA10
    iea01_group_count INTEGER, iea02_control_number VARCHAR,
    iea_seg_ord INTEGER
);

CREATE TABLE edi.functional_group (                 -- GS / GE
    group_uid            VARCHAR PRIMARY KEY,
    interchange_uid      VARCHAR NOT NULL REFERENCES edi.interchange(interchange_uid),
    file_id              VARCHAR NOT NULL,
    seg_ord              INTEGER NOT NULL,
    gs01_functional_id   VARCHAR,                  -- HP (835) / HC (837)
    gs02_sender_code VARCHAR, gs03_receiver_code VARCHAR,
    gs04_date VARCHAR, gs05_time VARCHAR,
    gs06_control_number VARCHAR, gs07_agency VARCHAR,
    gs08_version VARCHAR,                          -- 005010X221A1 / 005010X222A1
    ge01_txn_count INTEGER, ge02_control_number VARCHAR,
    ge_seg_ord INTEGER
);

CREATE TABLE edi.transaction_set (                  -- ST / SE (+ BHT for 837)
    txn_uid              VARCHAR PRIMARY KEY,
    group_uid            VARCHAR NOT NULL REFERENCES edi.functional_group(group_uid),
    file_id              VARCHAR NOT NULL,
    seg_ord              INTEGER NOT NULL,
    seg_ord_end          INTEGER NOT NULL,         -- SE position
    txn_type             VARCHAR NOT NULL,         -- '835' | '837'
    st01 VARCHAR, st02_control_number VARCHAR, st03_impl_convention VARCHAR,
    se01_segment_count INTEGER, se02_control_number VARCHAR,
    -- 837 only (BHT)
    bht01_structure_code VARCHAR, bht02_purpose_code VARCHAR,       -- 00 original / 18 reissue
    bht03_reference_id VARCHAR, bht04_date DATE, bht05_time VARCHAR,
    bht06_claim_or_encounter VARCHAR,                                -- CH chargeable / RP reporting
    count_check_ok       BOOLEAN                   -- SE01 matches actual segment count
);

-- =============================================================================
-- D. SHARED CHILD TABLES (used by both 835 and 837)
--    owner_table names the typed table the owner_uid belongs to.
-- =============================================================================

-- Every NM1/N1 "name loop" with its N2/N3/N4/PRV/DMG/RDM in one row.
-- 837: 1000A/B, 2010AA/AB/AC, 2010BA/BB, 2010CA, 2310A-F, 2330A-G, 2420A-H
-- 835: 1000A (payer), 1000B (payee), 2100 NM1 (QC/IL/74/82/TT/PR/GB)
CREATE TABLE edi.entity (
    entity_uid           VARCHAR PRIMARY KEY,
    txn_uid              VARCHAR NOT NULL,
    owner_uid            VARCHAR NOT NULL,         -- txn / hl / claim / line / other-payer uid
    owner_table          VARCHAR NOT NULL,
    file_id              VARCHAR NOT NULL,
    seg_ord              INTEGER NOT NULL,
    loop_id              VARCHAR NOT NULL,
    segment_id           VARCHAR NOT NULL,         -- NM1 | N1
    entity_id_code       VARCHAR NOT NULL,         -- NM101/N101: 85 billing, 87 pay-to, IL, QC, PR, 82, DN, DK, DQ, 77, PE, ...
    entity_type          VARCHAR,                  -- NM102: 1 person / 2 non-person
    name_last_or_org     VARCHAR,                  -- NM103 / N102
    name_first           VARCHAR,                  -- NM104
    name_middle          VARCHAR,                  -- NM105
    name_prefix          VARCHAR,                  -- NM106
    name_suffix          VARCHAR,                  -- NM107
    id_qualifier         VARCHAR,                  -- NM108 / N103: XX NPI, PI payer id, MI member id, FI TIN, ...
    id_value             VARCHAR,                  -- NM109 / N104
    nm110 VARCHAR, nm111 VARCHAR, nm112 VARCHAR,   -- rarely used; kept
    n2_additional_name   VARCHAR,
    addr_line1           VARCHAR,                  -- N301
    addr_line2           VARCHAR,                  -- N302
    city                 VARCHAR,                  -- N401
    state                VARCHAR,                  -- N402
    postal_code          VARCHAR,                  -- N403
    country              VARCHAR,                  -- N404
    country_subdivision  VARCHAR,                  -- N407
    location_qualifier   VARCHAR, location_id VARCHAR,   -- N405/N406
    prv_provider_code    VARCHAR,                  -- PRV01 (BI billing, PE performing, ...)
    prv_ref_qualifier    VARCHAR,                  -- PRV02 (PXC)
    prv_taxonomy         VARCHAR,                  -- PRV03
    dmg_format VARCHAR, dmg_birth_date DATE, dmg_gender VARCHAR,     -- DMG01-03
    rdm_elements         VARCHAR[],                -- 835 1000B RDM (remittance delivery method)
    segment_ords         INTEGER[]                 -- all seg_ords that fed this row
);

CREATE TABLE edi.reference (                        -- REF at any level
    ref_uid              VARCHAR PRIMARY KEY,      -- file_id:seg_ord
    txn_uid              VARCHAR NOT NULL,
    owner_uid            VARCHAR NOT NULL,
    owner_table          VARCHAR NOT NULL,
    file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL,
    loop_id              VARCHAR,
    qualifier            VARCHAR NOT NULL,         -- REF01: 6R line ctl, F8 original ref, G1 prior auth, 1K payer claim #, EA medical record, 2U payer id, EV, TJ, ...
    value                VARCHAR,                  -- REF02
    description          VARCHAR,                  -- REF03
    ref04_raw            VARCHAR                   -- REF04 composite, raw
);

CREATE TABLE edi.date_time (                        -- DTP (837) and DTM (835)
    dt_uid               VARCHAR PRIMARY KEY,
    txn_uid              VARCHAR NOT NULL,
    owner_uid            VARCHAR NOT NULL,
    owner_table          VARCHAR NOT NULL,
    file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL,
    loop_id              VARCHAR,
    segment_id           VARCHAR NOT NULL,         -- DTP | DTM
    qualifier            VARCHAR NOT NULL,         -- 472 service, 431 onset, 454 initial tx, 405 production, 232/233 claim period, 050 received, 036 expiration, 150/151 period
    format_qualifier     VARCHAR,                  -- D8 | RD8 | DT | TM ...
    raw_value            VARCHAR NOT NULL,
    date_start           DATE,                     -- parsed
    date_end             DATE,                     -- = date_start unless RD8
    time_value           VARCHAR                   -- DTM03 / DTM04 / DTM05 if present
);

CREATE TABLE edi.amount (                           -- AMT
    amt_uid              VARCHAR PRIMARY KEY,
    txn_uid VARCHAR NOT NULL, owner_uid VARCHAR NOT NULL, owner_table VARCHAR NOT NULL,
    file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL, loop_id VARCHAR,
    qualifier            VARCHAR NOT NULL,         -- 835: AU coverage, B6 allowed, KH late filing, T tax, T2, ZK.., I interest, D8 discount; 837: F5 patient paid, D, EAF, A8, ...
    amount               DECIMAL(18,2),
    raw_amount           VARCHAR,
    credit_debit_flag    VARCHAR                   -- AMT03
);

CREATE TABLE edi.quantity (                         -- QTY (835) and MEA-like counts
    qty_uid              VARCHAR PRIMARY KEY,
    txn_uid VARCHAR NOT NULL, owner_uid VARCHAR NOT NULL, owner_table VARCHAR NOT NULL,
    file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL, loop_id VARCHAR,
    qualifier            VARCHAR NOT NULL,         -- CA covered days, ZK, ...
    quantity             DECIMAL(18,4),
    raw_quantity         VARCHAR,
    qty03_raw            VARCHAR
);

CREATE TABLE edi.note (                             -- NTE (837 claim + line), K3
    note_uid             VARCHAR PRIMARY KEY,
    txn_uid VARCHAR NOT NULL, owner_uid VARCHAR NOT NULL, owner_table VARCHAR NOT NULL,
    file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL, loop_id VARCHAR,
    segment_id           VARCHAR NOT NULL,         -- NTE | K3
    note_ref_code        VARCHAR,                  -- NTE01 (ADD, CER, DCP, ...)
    note_text            VARCHAR                   -- NTE02 / K301
);

CREATE TABLE edi.contact (                          -- PER
    contact_uid          VARCHAR PRIMARY KEY,
    txn_uid VARCHAR NOT NULL, owner_uid VARCHAR NOT NULL, owner_table VARCHAR NOT NULL,
    file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL, loop_id VARCHAR,
    function_code        VARCHAR,                  -- PER01 IC / CX / BL
    contact_name         VARCHAR,
    comm1_qual VARCHAR, comm1_value VARCHAR,       -- TE / EM / FX / UR
    comm2_qual VARCHAR, comm2_value VARCHAR,
    comm3_qual VARCHAR, comm3_value VARCHAR
);

-- CAS exploded to ONE ROW PER (group, reason, amount, qty) TRIPLET.
-- 835: 2100 claim-level and 2110 line-level adjudication.
-- 837: 2320 (prior payer claim-level) and 2430 (prior payer line-level) COB.
CREATE TABLE edi.adjustment (
    adj_uid              VARCHAR PRIMARY KEY,      -- file_id:seg_ord:triplet_index
    txn_uid              VARCHAR NOT NULL,
    txn_type             VARCHAR NOT NULL,         -- '835' | '837'
    owner_uid            VARCHAR NOT NULL,         -- claim_payment / service_payment / other_payer / line_adjudication uid
    owner_table          VARCHAR NOT NULL,
    adj_level            VARCHAR NOT NULL,         -- 'CLAIM' | 'LINE'
    file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL, loop_id VARCHAR,
    cas_ordinal          INTEGER NOT NULL,         -- nth CAS under this owner
    triplet_index        SMALLINT NOT NULL,        -- 1..6 within the CAS
    group_code           VARCHAR NOT NULL,         -- CO | PR | OA | PI | CR
    reason_code          VARCHAR NOT NULL,         -- CARC
    amount               DECIMAL(18,2),            -- sign as sent (negative = money back to provider; reversals negate)
    quantity             DECIMAL(18,4),
    raw_amount           VARCHAR,
    raw_quantity         VARCHAR
);

-- Catch-all typed home for low-volume segments that have no dedicated table
-- (PWK, CR1-CR3, CRC, CN1, HCP, MEA, PS1, FRM, LQ-less K3, TS2, RDM, ...).
-- Element-level lossless; the agent never needs these directly.
CREATE TABLE edi.misc_segment (
    misc_uid             VARCHAR PRIMARY KEY,
    txn_uid VARCHAR NOT NULL, owner_uid VARCHAR NOT NULL, owner_table VARCHAR NOT NULL,
    file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL, loop_id VARCHAR,
    segment_id           VARCHAR NOT NULL,
    elements             VARCHAR[] NOT NULL
);

-- =============================================================================
-- E. 837P — Professional claim
-- =============================================================================

-- 2000A / 2000B / 2000C hierarchical levels (HL + SBR / PAT + CUR)
CREATE TABLE c837.hl (
    hl_uid               VARCHAR PRIMARY KEY,
    txn_uid              VARCHAR NOT NULL,
    file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL,
    loop_id              VARCHAR NOT NULL,         -- 2000A | 2000B | 2000C
    hl01_id              VARCHAR NOT NULL,
    hl02_parent_id       VARCHAR,
    hl03_level_code      VARCHAR NOT NULL,         -- 20 billing / 22 subscriber / 23 patient
    hl04_child_code      VARCHAR,
    parent_hl_uid        VARCHAR,
    -- SBR (2000B)
    sbr01_payer_resp_seq VARCHAR,                  -- P / S / T / A..H / U
    sbr02_relationship   VARCHAR,                  -- 18 self
    sbr03_group_number   VARCHAR,
    sbr04_group_name     VARCHAR,
    sbr05_insurance_type VARCHAR,
    sbr09_filing_indicator VARCHAR,                -- MB, MC, CI, BL, HM, 16, ...
    sbr06 VARCHAR, sbr07 VARCHAR, sbr08 VARCHAR,
    -- PAT (2000B / 2000C)
    pat01_relationship   VARCHAR,
    pat05_date_format VARCHAR, pat06_death_date VARCHAR,
    pat07_weight_unit VARCHAR, pat08_weight VARCHAR, pat09_pregnancy VARCHAR,
    pat02 VARCHAR, pat03 VARCHAR, pat04 VARCHAR,
    -- CUR (2000A)
    cur_entity_code VARCHAR, cur_currency VARCHAR
);

-- 2300 claim (CLM + claim-level single-occurrence segments)
CREATE TABLE c837.claim (
    claim_uid            VARCHAR PRIMARY KEY,
    txn_uid              VARCHAR NOT NULL,
    file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL, seg_ord_end INTEGER NOT NULL,
    claim_ord_in_txn     INTEGER NOT NULL,
    billing_hl_uid       VARCHAR NOT NULL,         -- 2000A
    subscriber_hl_uid    VARCHAR NOT NULL,         -- 2000B
    patient_hl_uid       VARCHAR,                  -- 2000C (NULL when patient = subscriber)
    clm01_patient_control_number VARCHAR NOT NULL, -- PCN; matches 835 CLP01
    clm02_total_charge   DECIMAL(18,2),
    clm02_raw            VARCHAR,
    clm03 VARCHAR, clm04 VARCHAR,
    clm05_raw            VARCHAR,                  -- composite
    clm05_1_place_of_service VARCHAR,
    clm05_2_facility_qualifier VARCHAR,            -- B
    clm05_3_frequency_code VARCHAR,                -- 1 original / 7 replacement / 8 void
    clm06_provider_signature VARCHAR,
    clm07_assignment_participation VARCHAR,
    clm08_benefits_assignment VARCHAR,
    clm09_release_of_info VARCHAR,
    clm10_patient_signature_source VARCHAR,
    clm11_raw            VARCHAR,                  -- related causes composite
    clm11_1_related_cause VARCHAR, clm11_2_related_cause VARCHAR, clm11_3_related_cause VARCHAR,
    clm11_4_auto_accident_state VARCHAR, clm11_5_country VARCHAR,
    clm12_special_program VARCHAR,
    clm13 VARCHAR, clm14 VARCHAR, clm15 VARCHAR, clm16 VARCHAR, clm17 VARCHAR, clm18 VARCHAR,
    clm19 VARCHAR,
    clm20_delay_reason   VARCHAR,
    -- CR1/CR2/CRC/PWK/CN1/HCP and other low-volume segments -> edi.misc_segment
    -- REF/DTP/AMT/NTE -> shared children; NM1 loops 2310x -> edi.entity
    -- HI -> c837.claim_code; 2320 -> c837.other_payer
    line_count           INTEGER
);

-- HI segments exploded: one row per code (diagnoses, and any other HI qualifier)
CREATE TABLE c837.claim_code (
    claim_code_uid       VARCHAR PRIMARY KEY,      -- file_id:seg_ord:element_position
    claim_uid            VARCHAR NOT NULL,
    txn_uid VARCHAR NOT NULL, file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL,
    hi_segment_ordinal   INTEGER NOT NULL,         -- 1st, 2nd HI segment ...
    element_position     SMALLINT NOT NULL,        -- 1..12 within the HI
    code_sequence        SMALLINT NOT NULL,        -- overall 1..N (dx pointer target = 1..12)
    code_list_qualifier  VARCHAR NOT NULL,         -- ABK principal dx, ABF other dx, BP/BQ, ...
    code                 VARCHAR NOT NULL,         -- as sent, no decimal
    hi_03 VARCHAR, hi_04 VARCHAR, hi_05 VARCHAR, hi_06 VARCHAR,
    hi_07 VARCHAR, hi_08 VARCHAR, hi_09 VARCHAR,
    raw_composite        VARCHAR NOT NULL
);

-- 2320 other subscriber / prior payer info (+ 2330A-G names -> edi.entity)
CREATE TABLE c837.other_payer (
    other_payer_uid      VARCHAR PRIMARY KEY,
    claim_uid            VARCHAR NOT NULL,
    txn_uid VARCHAR NOT NULL, file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL,
    ordinal              SMALLINT NOT NULL,
    sbr01_payer_resp_seq VARCHAR, sbr02_relationship VARCHAR,
    sbr03_group_number VARCHAR, sbr04_group_name VARCHAR,
    sbr05_insurance_type VARCHAR, sbr09_filing_indicator VARCHAR,
    sbr06 VARCHAR, sbr07 VARCHAR, sbr08 VARCHAR,
    oi03_benefits_assignment VARCHAR, oi04_patient_signature VARCHAR,
    oi06_release_of_info VARCHAR, oi_other_elements VARCHAR[],
    moa_elements         VARCHAR[]                 -- 2320 MOA if present
    -- AMT (D payer paid, EAF remaining liability, A8 noncovered) -> edi.amount
    -- CAS -> edi.adjustment (txn_type='837', adj_level='CLAIM')
);

-- 2400 service line (LX + SV1 + line single-occurrence segments)
CREATE TABLE c837.service_line (
    line_uid             VARCHAR PRIMARY KEY,
    claim_uid            VARCHAR NOT NULL,
    txn_uid VARCHAR NOT NULL, file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL, seg_ord_end INTEGER NOT NULL,
    lx01_line_number     INTEGER NOT NULL,
    sv101_raw            VARCHAR NOT NULL,
    sv101_1_qualifier    VARCHAR,                  -- HC (CPT/HCPCS), ER, IV, WK
    sv101_2_procedure_code VARCHAR,
    sv101_3_mod1 VARCHAR, sv101_4_mod2 VARCHAR, sv101_5_mod3 VARCHAR, sv101_6_mod4 VARCHAR,
    sv101_7_description  VARCHAR,
    sv101_8              VARCHAR,
    sv102_charge         DECIMAL(18,2),
    sv102_raw            VARCHAR,
    sv103_unit_basis     VARCHAR,                  -- UN / MJ
    sv104_units          DECIMAL(18,4),
    sv104_raw            VARCHAR,
    sv105_place_of_service VARCHAR,                -- overrides CLM05-1 when present
    sv106 VARCHAR,
    sv107_raw            VARCHAR,                  -- dx pointer composite
    sv107_dx_pointers    SMALLINT[],               -- e.g. [1,3]
    sv108 VARCHAR,
    sv109_emergency VARCHAR, sv110 VARCHAR, sv111_epsdt VARCHAR, sv112_family_planning VARCHAR,
    sv113 VARCHAR, sv114 VARCHAR,
    sv115_copay_exempt   VARCHAR,
    line_item_control_number VARCHAR               -- REF*6R copied here for joins (also in edi.reference)
    -- DTP*472 -> edi.date_time ; NTE/K3 -> edi.note ; 2420x -> edi.entity
    -- PWK/CR*/CRC/MEA/CN1/HCP/PS1/FRM -> edi.misc_segment
);

-- 2410 drug identification (LIN / CTP / REF*XZ)
CREATE TABLE c837.line_drug (
    line_drug_uid        VARCHAR PRIMARY KEY,
    line_uid             VARCHAR NOT NULL,
    txn_uid VARCHAR NOT NULL, file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL,
    lin02_qualifier      VARCHAR,                  -- N4
    lin03_ndc            VARCHAR,
    lin_other_elements   VARCHAR[],
    ctp04_quantity       DECIMAL(18,4),
    ctp05_raw            VARCHAR,
    ctp05_unit           VARCHAR,
    ctp_elements         VARCHAR[]
);

-- 2430 line adjudication by a prior payer (SVD + DTP*573 + CAS -> edi.adjustment)
CREATE TABLE c837.line_adjudication (
    line_adj_uid         VARCHAR PRIMARY KEY,
    line_uid             VARCHAR NOT NULL,
    txn_uid VARCHAR NOT NULL, file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL,
    svd01_other_payer_id VARCHAR,
    svd02_paid           DECIMAL(18,2),
    svd03_raw            VARCHAR,
    svd03_qualifier VARCHAR, svd03_procedure_code VARCHAR,
    svd03_mod1 VARCHAR, svd03_mod2 VARCHAR, svd03_mod3 VARCHAR, svd03_mod4 VARCHAR,
    svd03_description VARCHAR,
    svd04 VARCHAR,
    svd05_paid_units     DECIMAL(18,4),
    svd06_bundled_line_number VARCHAR
);

-- =============================================================================
-- F. 835 — Claim payment / advice
-- =============================================================================

-- Header: BPR + TRN + CUR (1:1 with an 835 transaction_set)
-- 1000A payer / 1000B payee -> edi.entity ; header REF/DTM*405 -> shared children
CREATE TABLE r835.payment (
    payment_uid          VARCHAR PRIMARY KEY,      -- = txn_uid of the 835 ST
    txn_uid              VARCHAR NOT NULL,
    file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL,
    bpr01_transaction_handling VARCHAR,            -- C payment+advice, I advice only, H notification, D, P, U, X
    bpr02_total_paid     DECIMAL(18,2),
    bpr02_raw            VARCHAR,
    bpr03_credit_debit   VARCHAR,                  -- C / D
    bpr04_payment_method VARCHAR,                  -- ACH / CHK / BOP / FWT / NON
    bpr05_payment_format VARCHAR,                  -- CCP / CTX
    bpr06_sender_dfi_qual VARCHAR, bpr07_sender_dfi_id VARCHAR,
    bpr08_sender_acct_qual VARCHAR, bpr09_sender_account VARCHAR,   -- treat as sensitive
    bpr10_originating_company_id VARCHAR,
    bpr11_originating_company_supplemental VARCHAR,
    bpr12_receiver_dfi_qual VARCHAR, bpr13_receiver_dfi_id VARCHAR,
    bpr14_receiver_acct_qual VARCHAR, bpr15_receiver_account VARCHAR, -- treat as sensitive
    bpr16_effective_date DATE,                     -- check issue / EFT effective date
    bpr17_business_function VARCHAR,
    bpr18 VARCHAR, bpr19 VARCHAR, bpr20 VARCHAR, bpr21 VARCHAR,
    trn01_trace_type     VARCHAR,
    trn02_check_eft_number VARCHAR,                -- reassociation key with bank deposit
    trn03_payer_id       VARCHAR,                  -- 1 + payer TIN
    trn04_originating_supplemental VARCHAR,
    cur_elements         VARCHAR[]
);

-- 2000 header number (LX) + TS3 / TS2 provider summary
CREATE TABLE r835.header_number (
    header_number_uid    VARCHAR PRIMARY KEY,
    payment_uid          VARCHAR NOT NULL,
    txn_uid VARCHAR NOT NULL, file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL,
    lx01_assigned_number VARCHAR,
    ts3_elements         VARCHAR[],                -- provider id, facility type, fiscal period end, counts, amounts
    ts2_elements         VARCHAR[]
);

-- 2100 claim payment (CLP). ONE ROW PER CLP — reversals, corrections and
-- secondary adjudications are separate rows with the same CLP01.
CREATE TABLE r835.claim_payment (
    claim_payment_uid    VARCHAR PRIMARY KEY,
    payment_uid          VARCHAR NOT NULL,
    header_number_uid    VARCHAR,
    txn_uid VARCHAR NOT NULL, file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL, seg_ord_end INTEGER NOT NULL,
    clp_ord_in_txn       INTEGER NOT NULL,         -- order matters: reversal precedes its correction
    clp01_patient_control_number VARCHAR NOT NULL, -- matches 837 CLM01
    clp02_claim_status   VARCHAR NOT NULL,         -- 1,2,3 processed as P/S/T; 4 denied; 19,20,21 forwarded; 22 reversal; 23 not our claim; 25 predetermination
    clp03_total_charge   DECIMAL(18,2),
    clp04_total_paid     DECIMAL(18,2),
    clp05_patient_responsibility DECIMAL(18,2),
    clp06_filing_indicator VARCHAR,
    clp07_payer_claim_control_number VARCHAR,      -- ICN / DCN
    clp08_facility_type  VARCHAR,
    clp09_frequency_code VARCHAR,
    clp10 VARCHAR,
    clp11_drg_code VARCHAR, clp12_drg_weight DECIMAL(18,4), clp13_discharge_fraction DECIMAL(18,4),
    clp14 VARCHAR, clp15 VARCHAR,
    raw_clp03 VARCHAR, raw_clp04 VARCHAR, raw_clp05 VARCHAR
    -- CAS -> edi.adjustment ; NM1 QC/IL/74/82/TT/PR/GB -> edi.entity
    -- REF (F8 original ref, 1K, EA, ...) / DTM (232, 233, 036, 050) / PER / AMT / QTY -> shared children
    -- MIA / MOA -> r835.medicare_adjudication ; remark codes -> r835.remark
);

-- MIA (inpatient) / MOA (outpatient) Medicare adjudication segments
CREATE TABLE r835.medicare_adjudication (
    medicare_adj_uid     VARCHAR PRIMARY KEY,
    claim_payment_uid    VARCHAR NOT NULL,
    txn_uid VARCHAR NOT NULL, file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL,
    segment_id           VARCHAR NOT NULL,         -- MIA | MOA
    elements             VARCHAR[] NOT NULL,
    reimbursement_rate   DECIMAL(18,4),            -- MOA01
    hcpcs_payable_amount DECIMAL(18,2),            -- MOA02
    esrd_amount          DECIMAL(18,2),            -- MOA08
    noncovered_professional DECIMAL(18,2)          -- MOA09
);

-- 2110 service payment (SVC)
CREATE TABLE r835.service_payment (
    service_payment_uid  VARCHAR PRIMARY KEY,
    claim_payment_uid    VARCHAR NOT NULL,
    txn_uid VARCHAR NOT NULL, file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL, seg_ord_end INTEGER NOT NULL,
    svc_ord_in_claim     INTEGER NOT NULL,
    svc01_raw            VARCHAR NOT NULL,         -- ADJUDICATED procedure composite
    svc01_1_qualifier    VARCHAR,                  -- HC / AD / ER / IV / N4 / NU / WK
    svc01_2_procedure_code VARCHAR,
    svc01_3_mod1 VARCHAR, svc01_4_mod2 VARCHAR, svc01_5_mod3 VARCHAR, svc01_6_mod4 VARCHAR,
    svc01_7_description  VARCHAR,
    svc01_8              VARCHAR,
    svc02_charge         DECIMAL(18,2),
    svc03_paid           DECIMAL(18,2),
    svc04_revenue_code   VARCHAR,
    svc05_units_paid     DECIMAL(18,4),
    svc06_raw            VARCHAR,                  -- SUBMITTED procedure, only when it differs
    svc06_1_qualifier VARCHAR, svc06_2_procedure_code VARCHAR,
    svc06_3_mod1 VARCHAR, svc06_4_mod2 VARCHAR, svc06_5_mod3 VARCHAR, svc06_6_mod4 VARCHAR,
    svc06_7_description VARCHAR,
    svc07_original_units DECIMAL(18,4),
    raw_svc02 VARCHAR, raw_svc03 VARCHAR,
    line_item_control_number VARCHAR               -- REF*6R copied here for joins (also in edi.reference)
    -- DTM*472/150/151 -> edi.date_time ; CAS -> edi.adjustment ; AMT (B6 allowed, KH, T, ...) -> edi.amount
    -- QTY -> edi.quantity ; LQ -> r835.remark ; REF -> edi.reference
);

-- Remark codes from every place they can appear (MOA03-07, MIA05/20-24, LQ)
CREATE TABLE r835.remark (
    remark_uid           VARCHAR PRIMARY KEY,
    owner_uid            VARCHAR NOT NULL,         -- claim_payment_uid or service_payment_uid
    owner_table          VARCHAR NOT NULL,
    remark_level         VARCHAR NOT NULL,         -- CLAIM | LINE
    txn_uid VARCHAR NOT NULL, file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL,
    source_segment       VARCHAR NOT NULL,         -- LQ | MOA | MIA
    source_element       VARCHAR NOT NULL,         -- LQ02 | MOA03 ...
    code_list_qualifier  VARCHAR,                  -- LQ01: HE (RARC) / RX (NCPDP)
    remark_code          VARCHAR NOT NULL
);

-- PLB provider-level adjustments: ONE ROW PER (reason:ref, amount) pair
CREATE TABLE r835.provider_adjustment (
    plb_uid              VARCHAR PRIMARY KEY,      -- file_id:seg_ord:pair_index
    payment_uid          VARCHAR NOT NULL,
    txn_uid VARCHAR NOT NULL, file_id VARCHAR NOT NULL, seg_ord INTEGER NOT NULL,
    plb01_provider_id    VARCHAR,
    plb02_fiscal_period_date DATE,
    pair_index           SMALLINT NOT NULL,        -- 1..6
    reason_raw           VARCHAR NOT NULL,         -- composite e.g. 'WO:ICN12345'
    reason_code          VARCHAR NOT NULL,         -- WO, FB, L6, 72, CS, IR, J1, ...
    reference_id         VARCHAR,                  -- often the PCN / ICN being offset
    amount               DECIMAL(18,2),            -- positive reduces the payment
    raw_amount           VARCHAR
);

-- =============================================================================
-- G. REFERENCE CODE SETS & TENANT CONFIG (small, loaded from published lists)
-- =============================================================================

CREATE TABLE ref.claim_status (              -- 835 CLP02
    code VARCHAR PRIMARY KEY, description VARCHAR NOT NULL,
    payer_sequence VARCHAR,                  -- primary/secondary/tertiary/other
    is_reversal BOOLEAN NOT NULL, is_denial BOOLEAN NOT NULL
);
CREATE TABLE ref.adjustment_group (          -- CAS01
    code VARCHAR PRIMARY KEY, description VARCHAR NOT NULL, responsible_party VARCHAR NOT NULL
);
CREATE TABLE ref.carc (                      -- Claim Adjustment Reason Codes (x12.org list)
    code VARCHAR PRIMARY KEY, description VARCHAR NOT NULL,
    start_date DATE, stop_date DATE, category VARCHAR   -- category: our own grouping (eligibility, auth, coding, medical necessity, timely filing, duplicate, contractual, patient resp, ...)
);
CREATE TABLE ref.rarc (                      -- Remittance Advice Remark Codes
    code VARCHAR PRIMARY KEY, description VARCHAR NOT NULL, start_date DATE, stop_date DATE
);
CREATE TABLE ref.plb_reason (code VARCHAR PRIMARY KEY, description VARCHAR NOT NULL);
CREATE TABLE ref.claim_frequency (code VARCHAR PRIMARY KEY, description VARCHAR NOT NULL);
CREATE TABLE ref.place_of_service (code VARCHAR PRIMARY KEY, description VARCHAR NOT NULL);
CREATE TABLE ref.filing_indicator (code VARCHAR PRIMARY KEY, description VARCHAR NOT NULL, payer_class VARCHAR);
-- CPT descriptors are AMA-licensed: load only if Xifin's license covers it.
CREATE TABLE ref.procedure_code (code VARCHAR PRIMARY KEY, code_system VARCHAR, short_description VARCHAR, category VARCHAR);

-- Business-rule config consumed by the semantic layer (versioned, per tenant)
CREATE TABLE ref.denial_carc (               -- the reviewed CARC list for "denial signal v3"
    reason_code VARCHAR PRIMARY KEY, rule_version VARCHAR NOT NULL, reviewed_by VARCHAR, reviewed_at DATE, note VARCHAR
);
CREATE TABLE ref.tenant_setting (key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL, note VARCHAR);
-- e.g. ('status4_is_recorded_denial','true','LABCORP profile') , ('min_group_size','20', 'CPT-grouped rates')

-- Payer resolution: maps every payer identity seen in 837 (2010BB) and 835 (1000A/TRN03/REF*2U)
-- to one canonical payer. Maintained by the payer resolver, reviewed by humans.
CREATE TABLE ref.payer_alias (
    alias_source VARCHAR NOT NULL,           -- '837_2010BB' | '835_1000A' | 'MANUAL'
    alias_id     VARCHAR,                    -- payer id as sent
    alias_name   VARCHAR,                    -- payer name as sent
    payer_key    VARCHAR NOT NULL,           -- canonical
    payer_name   VARCHAR NOT NULL,           -- canonical display name
    payer_class  VARCHAR,                    -- Medicare / Medicaid / Commercial / MA / Blues / Other
    match_method VARCHAR,                    -- exact_id / normalized_name / manual
    PRIMARY KEY (alias_source, alias_id, alias_name)
);

INSERT INTO ref.claim_status VALUES
 ('1','Processed as primary','primary',false,false),
 ('2','Processed as secondary','secondary',false,false),
 ('3','Processed as tertiary','tertiary',false,false),
 ('4','Denied',NULL,false,true),
 ('19','Processed as primary, forwarded to additional payer(s)','primary',false,false),
 ('20','Processed as secondary, forwarded to additional payer(s)','secondary',false,false),
 ('21','Processed as tertiary, forwarded to additional payer(s)','tertiary',false,false),
 ('22','Reversal of previous payment',NULL,true,false),
 ('23','Not our claim, forwarded to additional payer(s)',NULL,false,false),
 ('25','Predetermination pricing only - no payment',NULL,false,false);

INSERT INTO ref.adjustment_group VALUES
 ('CO','Contractual obligation','provider'),
 ('PR','Patient responsibility','patient'),
 ('OA','Other adjustment','other'),
 ('PI','Payer initiated reduction','provider'),
 ('CR','Correction and reversal (retired; legacy files)','other');

INSERT INTO ref.claim_frequency VALUES
 ('1','Original claim'),('7','Replacement of prior claim'),('8','Void/cancel of prior claim');
