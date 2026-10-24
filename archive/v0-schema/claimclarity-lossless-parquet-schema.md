# ClaimClarity: Parquet Schema for Labcorp 837P and 835 Data (v1)

**Design summary.** Store Labcorp's 837P and 835 data as a normalized, lossless set of Parquet tables. The grain follows the X12 hierarchy: file → transaction → claim → service line → adjustment/remark. Every row carries lineage back to its S3 object and segment span, and a byte-exact `raw_segment` table makes the whole set lossless. Code meanings live in `ref_*` lookup tables. On top of this sits a small layer of logical DuckDB views with descriptions already decoded, which is the default surface the agent queries. Repeating X12 constructs are unpivoted into long tables, one row per occurrence, instead of wide or nested columns: CAS trios, PLB pairs, HI diagnoses, REF/DTP/AMT/QTY and NM1 parties. The most-used qualifiers are also promoted to self-describing typed columns. Matching 837 claims to 835 remittances is done once at ingest into explicit match tables, so the agent never has to work out join logic.

## TL;DR
- **Shape:** 4 provenance tables, 11 837P tables (`claim*`), 12 835 tables (`remit*`) and 2 derived match tables, all keyed by deterministic `<entity>_id` columns. There are also about 10 `ref_*` code tables and 6 logical `v_*` views. The agent's default surface is the views, the core claim/line/remit tables and the ref tables; `raw_segment` and the `*_misc_segment` tables are for engineers.
- **Lossless: yes, with two layers.** (1) `raw_segment` holds every segment byte-for-byte, with its delimiters, position, loop and parsed element list. (2) The typed tables cover every element of every commonly used segment. Rare segments are kept in generic `*_misc_segment` tables rather than given dozens of near-empty columns.
- **Layout:** `s3://<bucket>/tenants/labcorp/warehouse/v1/<table>/<partition>=…/`. 837 tables are partitioned by `submission_month`, 835 tables by `payment_month`, and child tables carry the parent's partition column. Files are sorted by date and business key so DuckDB can skip row groups. Each customer gets an identical tree under its own prefix and IAM boundary.

---

## 1. Research findings that drove the design (brief)
- **Normalized vs. wide.** The only controlled study found (Kohita 2025, arXiv 2510.01989) reports that denormalized schemas score higher on simple retrieval. Normalized schemas score higher on aggregations, "mainly due to their robustness against the data duplication and NULL value issues" of wide tables, and few-shot examples largely fix their join errors. An analyst bot mostly aggregates money, so the core is normalized and views give retrieval convenience.
- **Names and descriptions matter.** Wretblad et al. (arXiv 2408.04691) report that "incorporating such generated column descriptions consistently enhances text-to-SQL model performance, particularly for larger models like GPT-4o, Qwen2 72B and Mixtral 22Bx8." Databricks' Genie guidance says to "resolve column ambiguities and pre-join or de-normalize tables using views", and caps a space at 30 tables/views. In MotherDuck's Feb 2026 BIRD Mini-Dev run (Jacob Matson), Claude Opus 4.5, GPT-5.2 and Gemini 3 Flash scored 94.9% (train) and 94.4% (test) under LLM-judge scoring from the schema alone ("No semantic layer. No query history. No special context. Just the schema."), versus 64.0%/58.2% under strict BIRD scoring, on simple models (average 7 tables, none over 13). Conclusion: self-describing names first, comments second, and a small curated surface.
- **DuckDB/Parquet.** The DuckDB docs say "DuckDB works best on Parquet files with row groups of 100K-1M rows each… DuckDB can only parallelize over row groups", and its performance checklist targets "file sizes between 100 MB to 10 GB". Hive-style `key=value` folders let it prune whole partitions. `COMMENT ON TABLE/COLUMN/VIEW` is stored in the catalog and readable through `duckdb_columns()`. DuckLake currently rejects column comments on views (issue #1239), so a `data_dictionary` table is kept as well.
- **X12 facts the schema must respect:**
  - A CAS segment carries up to six reason/amount/quantity trios under one group code, and there can be up to 99 CAS per claim (2100) and per line (2110).
  - The 2110 loop repeats up to 999 times per claim. 837P allows up to 50 lines (2400), 12 diagnoses (HI), 10 other payers (2320) and 15 line adjudications (2430).
  - PLB holds six reason/amount pairs. A positive PLB or CAS amount *decreases* payment.
  - Balancing rules: `SVC02 − ΣCAS = SVC03`, `CLP03 − ΣCAS = CLP04` and `ΣCLP04 − ΣPLB = BPR02`.
  - `CLP02 = 22` is a reversal and usually carries negative amounts.
  - Replacements and voids use `CLM05-3 = 7/8` with the payer's original claim number in `REF*F8`.
  - The X12 interpretation in RFI 1684 says an 835 returns `REF*6R` only when it was sent on the 837, or when lines were split. The TR3 requires 6R to be unique within a claim.
  - Per CMS MLN Matters MM12676, CARC and RARC updates "occur in publication 3 times per year – around March 1, July 1, and November 1"; RARC is maintained by CMS.
- **Lesson from existing parsers.** The Databricks x12-edi-parser (EDI Ember) writes claim header and line tables. Its open issue #63 asks for "full HL hierarchy and dynamic segment extraction to avoid data loss", which shows that typed-only flattening drops data. That is the reason for `raw_segment`.

## 2. Naming and type conventions
- **Names:** lowercase `snake_case`, full English words, no X12 codes as column names; the X12 reference goes in the column comment. Singular table names. Table prefixes: `claim_` (837P), `remit_` (835), `ref_` (lookups), `v_` (views), and `source_`/`edi_`/`raw_` (lineage).
- **Suffixes:**
  - `_id`: surrogate key, VARCHAR, deterministic `md5(source_file_id || ':' || source_segment_start)`, so re-ingesting a file is idempotent.
  - `_number`: an identifier from the business or payer.
  - `_code`: a coded value, always VARCHAR so leading zeros and alphanumeric codes survive (POS `"01"`, CARC `"B7"`).
  - `_description`: decoded text (in views and ref tables only).
  - `_amount`: DECIMAL(18,2), stored as sent, sign included.
  - `_count` / `_quantity`: DECIMAL(15,3).
  - `_date`: DATE.
  - `_at`: TIMESTAMP in UTC.
  - `is_`: BOOLEAN.
  - `_sequence` / `_position`: INTEGER ordinal, 1-based.
- **One concept, one name everywhere.** For example, `patient_control_number` names both CLM01 and CLP01, and `payer_claim_control_number` names both CLP07 and 837 `REF*F8`. Join keys share names across tables, so `USING (...)` works.
- **Date ranges:** RD8 values split into `_start_date` and `_end_date`. D8 values set both columns to the same date.
- **No nested types** in agent-facing tables. The only `LIST(VARCHAR)` is `raw_segment.elements`, which is for engineers.
- **Column order:** keys, then business identifiers, then codes, then amounts, then dates, then partition column, then the lineage block.
- **Standard lineage block on every core row (written "+L" below):** `source_file_id VARCHAR`, `transaction_id VARCHAR`, `source_segment_start INTEGER` and `source_segment_end INTEGER` (the 1-based segment positions in the file spanned by this row's loop).

## 3. Entity-relationship overview

```
source_file 1─* edi_transaction 1─* (837) claim 1─* claim_line
     │                │                    │  1─* claim_diagnosis
     │                │                    │  1─* claim_party ─ (opt) claim_line_id
     │                │                    │  1─* claim_reference / claim_date / claim_amount (opt claim_line_id)
     │                │                    │  1─* claim_other_payer 1─* claim_cob_adjustment (opt claim_line_id)
     │                │                    │  claim_line 1─* claim_line_adjudication (2430)
     │                │                    └─ 1─* claim_misc_segment
     │                └─1:1 (835) remit_payment 1─* remit_claim 1─* remit_service_line
     │                                   │             │ 1─* remit_adjustment (claim- or line-level)
     │                                   │             │ 1─* remit_remark     (claim- or line-level)
     │                                   │             └ 1─* remit_party / remit_reference / remit_date / remit_amount / remit_quantity
     │                                   ├ 1─* remit_provider_adjustment (PLB)
     │                                   └ 1─* remit_misc_segment
     └─1─* raw_segment  (joins to any row on source_file_id + segment_position BETWEEN source_segment_start AND source_segment_end)

claim ─*  claim_remit_match  *─ remit_claim            (derived at ingest)
claim_line ─* claim_line_remit_match *─ remit_service_line
ref_* tables join on code columns (e.g., remit_adjustment.adjustment_reason_code = ref_carc.carc_code)
```

## 4. Provenance and lineage tables

**`source_file`**: one row per S3 object ingested. PK `source_file_id` (sha256 of the file bytes). Partitioned by `ingest_month`.

| column | type | description |
|---|---|---|
| source_file_id | VARCHAR | sha256 of the file bytes; deduplicates re-deliveries |
| tenant_id | VARCHAR | `labcorp`; constant per tenant tree, kept as a guard against cross-tenant mixing |
| s3_bucket, s3_key, s3_version_id, s3_etag | VARCHAR | exact landing object |
| file_name | VARCHAR | base name of the S3 key |
| file_size_bytes | BIGINT | |
| transaction_type | VARCHAR | `837P` or `835` (from ST01 + GS08) |
| element_separator, component_separator, repetition_separator, segment_terminator | VARCHAR | ISA delimiters (ISA position 4, ISA16, ISA11, terminator), needed to rebuild the file byte-for-byte |
| segment_count | INTEGER | segments parsed |
| received_at | TIMESTAMP | S3 LastModified time |
| ingested_at | TIMESTAMP | parse time |
| ingest_run_id, parser_name, parser_version | VARCHAR | pipeline lineage |
| parse_status | VARCHAR | `parsed`, `parsed_with_warnings` or `rejected` |
| ingest_month | VARCHAR | partition, `YYYY-MM` |

**`edi_transaction`**: one row per ST/SE transaction set, with its ISA and GS envelope values copied onto it (they are small, and this avoids two join hops). PK `transaction_id`; FK `source_file_id`.

| column | type | description |
|---|---|---|
| transaction_id | VARCHAR | surrogate key |
| source_file_id | VARCHAR | FK |
| interchange_sender_qualifier, interchange_sender_id, interchange_receiver_qualifier, interchange_receiver_id | VARCHAR | ISA05–ISA08 |
| interchange_date, interchange_time | VARCHAR | ISA09–ISA10, as sent |
| interchange_control_number | VARCHAR | ISA13 |
| interchange_usage_indicator | VARCHAR | ISA15: `P` production / `T` test |
| isa_authorization_and_security_raw | VARCHAR | ISA01–ISA04, as sent |
| functional_id_code | VARCHAR | GS01: `HC` (837) / `HP` (835) |
| group_sender_code, group_receiver_code | VARCHAR | GS02–GS03 |
| group_date, group_time | VARCHAR | GS04–GS05 |
| group_control_number | VARCHAR | GS06 |
| implementation_version | VARCHAR | GS08 / ST03, e.g., `005010X222A1` or `005010X221A1`; A2 errata versions are accepted and recorded |
| transaction_set_code | VARCHAR | ST01 `837`/`835` |
| transaction_control_number | VARCHAR | ST02 (= SE02) |
| transaction_segment_count | INTEGER | SE01 |
| bht_purpose_code, bht_reference_number, bht_creation_date, bht_creation_time, bht_claim_type_code | VARCHAR/DATE | 837 BHT02–BHT06 (null for 835) |
| submitter_name, submitter_id, receiver_name, receiver_id | VARCHAR | 837 loops 1000A/1000B NM1 |
| ingest_month | VARCHAR | partition |

**`raw_segment`**: one row per segment of every file. This is what makes the dataset lossless. PK (`source_file_id`, `segment_position`). Partitioned by `transaction_type`, `ingest_month`.

| column | type | description |
|---|---|---|
| source_file_id | VARCHAR | FK |
| segment_position | INTEGER | 1-based ordinal in the file |
| transaction_id | VARCHAR | null for ISA/GS/GE/IEA |
| segment_id | VARCHAR | e.g., `CLP` |
| loop_id | VARCHAR | e.g., `2110`, `2010BB`, `HEADER`, `SUMMARY` |
| hl_id, hl_parent_id | VARCHAR | the enclosing 837 HL01/HL02 |
| segment_text | VARCHAR | the exact segment without its terminator |
| elements | LIST(VARCHAR) | elements split on the file's separator (engineer use) |
| anchor_table, anchor_id | VARCHAR | the typed row this segment belongs to, e.g., `remit_service_line` |
| transaction_type, ingest_month | VARCHAR | partitions |

**`ingest_issue`**: one row per parse, validation or balancing issue. Columns: `ingest_issue_id`, `source_file_id`, `segment_position`, `issue_type` (`parse_error`, `unknown_segment`, `balance_line`, `balance_claim`, `balance_transaction`, `se_count_mismatch`), `severity`, `message`, `expected_amount`, `actual_amount`, `ingest_month`.

## 5. Core lossless tables: 837P (005010X222A1)

All 837 tables are partitioned by `submission_month`, taken from BHT04, the transaction creation date, and carry +L.

**`claim`**: one row per CLM occurrence (2300). A claim resubmitted as a replacement is a new row. PK `claim_id`; FK `transaction_id`. Fields from the HL hierarchy above the claim (2000A/B/C) are copied onto it, so the agent never walks HL trees.

| column | type | X12 | description |
|---|---|---|---|
| claim_id | VARCHAR | – | surrogate key |
| patient_control_number | VARCHAR | 2300 CLM01 | provider claim ID; returned in 835 CLP01 |
| total_charge_amount | DECIMAL(18,2) | CLM02 | billed amount |
| place_of_service_code | VARCHAR | CLM05-1 | → `ref_place_of_service` |
| facility_code_qualifier | VARCHAR | CLM05-2 | `B` |
| claim_frequency_code | VARCHAR | CLM05-3 | 1 original, 7 replacement, 8 void → `ref_claim_frequency` |
| provider_signature_indicator | VARCHAR | CLM06 | |
| assignment_participation_code | VARCHAR | CLM07 | |
| benefits_assignment_indicator | VARCHAR | CLM08 | |
| release_of_information_code | VARCHAR | CLM09 | |
| patient_signature_source_code | VARCHAR | CLM10 | |
| related_cause_code_1, _2, _3 | VARCHAR | CLM11-1..3 | accident/employment |
| related_cause_state_code, related_cause_country_code | VARCHAR | CLM11-4, -5 | |
| special_program_code | VARCHAR | CLM12 | |
| delay_reason_code | VARCHAR | CLM20 | |
| original_payer_claim_control_number | VARCHAR | 2300 REF*F8 | payer claim number being replaced or voided |
| prior_authorization_number | VARCHAR | REF*G1 | |
| referral_number | VARCHAR | REF*9F | |
| clia_number | VARCHAR | REF*X4 | lab CLIA certificate (key for Labcorp) |
| medical_record_number | VARCHAR | REF*EA | |
| clearinghouse_trace_number | VARCHAR | REF*D9 | |
| patient_paid_amount | DECIMAL(18,2) | AMT*F5 | |
| claim_note_text | VARCHAR | NTE02 | |
| principal_diagnosis_code | VARCHAR | HI01-2 (ABK) | copy of the first row in `claim_diagnosis` |
| diagnosis_count, service_line_count | INTEGER | derived | |
| billing_provider_npi | VARCHAR | 2010AA NM109 | |
| billing_provider_name | VARCHAR | 2010AA NM103 | |
| billing_provider_tax_id | VARCHAR | 2010AA REF*EI/SY | |
| billing_provider_taxonomy_code | VARCHAR | 2000A PRV03 | |
| payer_name, payer_id | VARCHAR | 2010BB NM103/NM109 | destination payer |
| payer_responsibility_code | VARCHAR | 2000B SBR01 | P/S/T… |
| claim_filing_indicator_code | VARCHAR | SBR09 | → `ref_claim_filing_indicator` |
| subscriber_group_number, subscriber_group_name | VARCHAR | SBR03/SBR04 | |
| insurance_type_code | VARCHAR | SBR05 | |
| subscriber_member_id | VARCHAR | 2010BA NM109 | |
| subscriber_last_name, subscriber_first_name | VARCHAR | 2010BA NM103/NM104 | PHI |
| subscriber_birth_date | DATE | 2010BA DMG02 | PHI |
| subscriber_gender_code | VARCHAR | DMG03 | |
| is_patient_the_subscriber | BOOLEAN | 2000C absent | |
| patient_relationship_code | VARCHAR | SBR02 / PAT01 | |
| patient_last_name, patient_first_name | VARCHAR | 2010CA or 2010BA | PHI; resolved to whoever the patient is |
| patient_birth_date | DATE | DMG02 | PHI |
| patient_gender_code | VARCHAR | DMG03 | |
| rendering_provider_npi | VARCHAR | 2310B NM109 | |
| referring_provider_npi | VARCHAR | 2310A NM109 (first) | |
| service_facility_npi | VARCHAR | 2310C NM109 | |
| billing_provider_hl_id, subscriber_hl_id, patient_hl_id | VARCHAR | HL01 | hierarchy trace |
| submission_date | DATE | BHT04 | |
| submission_month | VARCHAR | – | partition |

**`claim_line`**: one row per 2400 service line (up to 50 per claim). PK `claim_line_id`; FK `claim_id`.

| column | type | X12 | description |
|---|---|---|---|
| claim_line_id, claim_id | VARCHAR | – | keys |
| line_number | INTEGER | LX01 | |
| procedure_code_qualifier | VARCHAR | SV101-1 | `HC` = CPT/HCPCS |
| procedure_code | VARCHAR | SV101-2 | |
| procedure_modifier_1 … _4 | VARCHAR | SV101-3..6 | |
| procedure_description | VARCHAR | SV101-7 | |
| line_charge_amount | DECIMAL(18,2) | SV102 | |
| unit_basis_code | VARCHAR | SV103 | UN/MJ |
| service_unit_count | DECIMAL(15,3) | SV104 | |
| place_of_service_code | VARCHAR | SV105 | overrides the claim-level POS |
| diagnosis_pointer_1 … _4 | INTEGER | SV107-1..4 | point to `claim_diagnosis.diagnosis_sequence` |
| emergency_indicator, epsdt_indicator, family_planning_indicator | VARCHAR | SV109/SV111/SV112 | |
| copay_status_code | VARCHAR | SV115 | |
| service_start_date, service_end_date | DATE | 2400 DTP*472 | |
| line_item_control_number | VARCHAR | 2400 REF*6R | line match key to 835 |
| line_prior_authorization_number | VARCHAR | REF*G1 | |
| line_clia_number, referring_clia_number | VARCHAR | REF*X4 / REF*F4 | |
| ndc_code | VARCHAR | 2410 LIN03 | |
| drug_unit_count, drug_unit_code | DECIMAL/VARCHAR | CTP04 / CTP05-1 | |
| line_note_text | VARCHAR | NTE02 | |
| rendering_provider_npi, ordering_provider_npi, referring_provider_npi | VARCHAR | 2420A / 2420E / 2420F NM109 | null means the claim-level provider applies |
| submission_month | VARCHAR | – | partition |

**`claim_diagnosis`**: one row per diagnosis in HI (up to 12). PK (`claim_id`, `diagnosis_sequence`). Columns: `diagnosis_sequence` (1–12; the order SV107 points into), `diagnosis_code_qualifier` (`ABK` principal / `ABF` other), `diagnosis_code` (ICD-10-CM as sent, no decimal), `is_principal`, `submission_month`, +L.

**`claim_party`**: one row per NM1 entity loop. These are 2010AA/AB/AC, 2010BA/BB, 2010CA, 2310A–F, 2330A–G and 2420A–H. Parties from the hierarchy above the claim are repeated for every claim beneath them, so `claim_id` is never null. PK `claim_party_id`; FKs `claim_id`, optional `claim_line_id` (2420 loops), optional `other_payer_sequence` (2330 loops). Columns:
- `loop_id`
- `entity_role`: readable, e.g., `billing_provider`, `pay_to_address`, `subscriber`, `payer`, `patient`, `referring_provider`, `rendering_provider`, `service_facility`, `supervising_provider`, `ordering_provider`, `purchased_service_provider`, `other_subscriber`, `other_payer`, `ambulance_pickup`, `ambulance_dropoff`
- `entity_identifier_code` (NM101), `entity_type_qualifier` (NM102)
- `last_or_organization_name`, `first_name`, `middle_name`, `name_prefix`, `name_suffix` (NM103–NM107)
- `id_code_qualifier`, `id_code` (NM108/NM109)
- `address_line_1`, `address_line_2` (N3), `city`, `state_code`, `postal_code`, `country_code` (N4)
- `provider_role_code`, `taxonomy_code` (PRV01/PRV03)
- `birth_date`, `gender_code` (DMG)
- `contact_name`, `contact_phone`, `contact_email` (first PER)
- `is_inherited_from_hierarchy`
- `submission_month`, +L

**`claim_reference`**, **`claim_date`**, **`claim_amount`**: one row per REF, DTP or AMT occurrence anywhere in an 837 claim. They hold every occurrence, including the ones also promoted to typed columns. Shared keys: `claim_id`, `claim_line_id` (null at claim level), `claim_party_id` (for REFs under NM1 loops), `other_payer_sequence`, `loop_id`.
- `claim_reference`: `reference_qualifier` (REF01), `reference_value` (REF02), `reference_description` (REF03), `reference_composite_raw` (REF04).
- `claim_date`: `date_qualifier` (DTP01, e.g., 431, 454, 304, 439, 472, 573), `date_format_qualifier` (D8/RD8), `start_date`, `end_date`, `date_raw`.
- `claim_amount`: `amount_qualifier` (AMT01, e.g., F5, D, A8, EAF, T), `amount` (AMT02).

Qualifier meanings come from `ref_x12_qualifier`.

**`claim_other_payer`**: one row per 2320 other-subscriber loop (up to 10), with its 2330A/B payer and subscriber summarized. PK (`claim_id`, `other_payer_sequence`). Columns: `payer_responsibility_code` (SBR01), `individual_relationship_code` (SBR02), `group_number` (SBR03), `claim_filing_indicator_code` (SBR09), `other_payer_paid_amount` (AMT*D), `non_covered_amount` (AMT*A8), `remaining_patient_liability_amount` (AMT*EAF), `benefits_assignment_indicator` (OI03), `release_of_information_code` (OI06), `other_payer_name`/`other_payer_id` (2330B NM103/NM109), `other_payer_adjudication_date` (2330B DTP*573), `other_payer_claim_control_number` (2330B REF*F8), `other_subscriber_member_id` (2330A NM109), `submission_month`, +L.

**`claim_line_adjudication`**: one row per 2430 prior-payer line adjudication (up to 15 per line). PK `claim_line_adjudication_id`; FK `claim_line_id`. Columns: `other_payer_primary_id` (SVD01), `paid_amount` (SVD02), `procedure_code_qualifier`/`procedure_code`/`procedure_modifier_1..4` (SVD03), `paid_unit_count` (SVD05), `bundled_line_number` (SVD06), `adjudication_date` (DTP*573), `remaining_patient_liability_amount` (AMT*EAF), `submission_month`, +L.

**`claim_cob_adjustment`**: one row per CAS trio in 2320 (claim level, max 5 CAS) or 2430 (line level, max 5 CAS). The structure matches `remit_adjustment`. Columns: `claim_id`, `claim_line_id` (null at claim level), `other_payer_sequence`, `claim_line_adjudication_id`, `adjustment_level` (`claim`/`service_line`), `cas_segment_sequence`, `trio_position` (1–6), `adjustment_group_code`, `adjustment_reason_code`, `adjustment_amount`, `adjustment_quantity`, `submission_month`, +L.

**`claim_misc_segment`**: one row per rarely used 837P segment, kept losslessly in generic form. These are PWK, CN1, K3, CR1, CR3, CRC, HCP, MEA, PS1, QTY, CUR, PER extras, HI condition/anesthesia codes, and LQ/FRM in 2440. Columns: `claim_id`, `claim_line_id`, `loop_id`, `segment_id`, `qualifier` (first element), `element_01` … `element_15` VARCHAR, `segment_text`, `submission_month`, +L. Promote a segment to typed columns if Labcorp data shows it is used heavily.

## 6. Core lossless tables: 835 (005010X221A1)

All 835 tables are partitioned by `payment_month`, taken from BPR16, the check/EFT effective date (falling back to DTM*405), and carry +L.

**`remit_payment`**: one row per 835 transaction set (one payment or advice). PK `remit_payment_id`; FK `transaction_id` (1:1).

| column | type | X12 | description |
|---|---|---|---|
| remit_payment_id, transaction_id | VARCHAR | – | keys |
| transaction_handling_code | VARCHAR | BPR01 | C/D/H/I/P/U/X |
| total_payment_amount | DECIMAL(18,2) | BPR02 | money actually paid |
| credit_debit_flag_code | VARCHAR | BPR03 | |
| payment_method_code | VARCHAR | BPR04 | ACH/CHK/BOP/FWT/NON |
| payment_format_code | VARCHAR | BPR05 | CCP/CTX |
| sender_dfi_number, sender_account_number_last4 | VARCHAR | BPR07/BPR09 | masked; full value only in `raw_segment` |
| receiver_dfi_number, receiver_account_number_last4 | VARCHAR | BPR13/BPR15 | masked |
| payer_identifier | VARCHAR | BPR10 | |
| originating_company_supplemental_code | VARCHAR | BPR11 | |
| payment_date | DATE | BPR16 | check issue / EFT effective date |
| trace_type_code | VARCHAR | TRN01 | |
| trace_number | VARCHAR | TRN02 | **check or EFT trace number**; ties to the bank deposit |
| trace_originator_id | VARCHAR | TRN03 | |
| currency_code | VARCHAR | CUR02 | null means USD |
| receiver_identifier | VARCHAR | REF*EV | |
| production_date | DATE | DTM*405 | |
| payer_name | VARCHAR | 1000A N102 | |
| payer_id_qualifier, payer_id | VARCHAR | 1000A N103/N104 | |
| payee_name | VARCHAR | 1000B N102 | |
| payee_id_qualifier, payee_id | VARCHAR | 1000B N103/N104 | XX = NPI, FI = TIN |
| payee_npi, payee_tax_id | VARCHAR | N104 / REF*TJ | resolved |
| remittance_delivery_method_code | VARCHAR | RDM01 | |
| remit_claim_count | INTEGER | derived | |
| sum_claim_payment_amount | DECIMAL(18,2) | derived ΣCLP04 | |
| sum_provider_adjustment_amount | DECIMAL(18,2) | derived ΣPLB | |
| is_balanced | BOOLEAN | derived | ΣCLP04 − ΣPLB = BPR02 |
| payment_month | VARCHAR | – | partition |

**`remit_claim`**: one row per 2100 CLP loop. A reversal and its correction are two rows. PK `remit_claim_id`; FKs `remit_payment_id`, `transaction_id`. `payment_date`, `trace_number`, `payer_name` and `payer_id` are copied from `remit_payment` so most questions need no join.

| column | type | X12 | description |
|---|---|---|---|
| remit_claim_id, remit_payment_id | VARCHAR | – | keys |
| header_number | INTEGER | 2000 LX01 | |
| claim_sequence | INTEGER | – | order within the transaction |
| patient_control_number | VARCHAR | CLP01 | = 837 CLM01 |
| claim_status_code | VARCHAR | CLP02 | → `ref_claim_status` |
| is_reversal | BOOLEAN | CLP02 = 22 | |
| is_denied | BOOLEAN | CLP02 = 4 | the payer's own "denied" status only |
| total_charge_amount | DECIMAL(18,2) | CLP03 | negative on reversals |
| claim_payment_amount | DECIMAL(18,2) | CLP04 | |
| patient_responsibility_amount | DECIMAL(18,2) | CLP05 | |
| claim_filing_indicator_code | VARCHAR | CLP06 | |
| payer_claim_control_number | VARCHAR | CLP07 | the payer's ICN/DCN; needed for appeals and corrections |
| facility_type_code | VARCHAR | CLP08 | |
| claim_frequency_code | VARCHAR | CLP09 | |
| drg_code, drg_weight, discharge_fraction, yes_no_condition_code | VARCHAR/DECIMAL | CLP11–CLP14 | lossless; rarely used for professional claims |
| patient_last_name, patient_first_name, patient_member_id | VARCHAR | NM1*QC | PHI |
| insured_member_id | VARCHAR | NM1*IL NM109 | |
| rendering_provider_npi | VARCHAR | NM1*82 NM109 | |
| crossover_carrier_name, crossover_carrier_id | VARCHAR | NM1*TT | claim forwarded to |
| corrected_priority_payer_name, corrected_priority_payer_id | VARCHAR | NM1*PR | |
| outpatient_reimbursement_rate | DECIMAL(9,4) | MOA01 | |
| hcpcs_payable_amount | DECIMAL(18,2) | MOA02 | |
| esrd_payment_amount, nonpayable_professional_component_amount | DECIMAL(18,2) | MOA08/MOA09 | |
| statement_start_date, statement_end_date | DATE | DTM*232/233 | |
| coverage_expiration_date | DATE | DTM*036 | |
| payer_received_date | DATE | DTM*050 | |
| coverage_amount, interest_amount, patient_amount_paid | DECIMAL(18,2) | AMT*AU / AMT*I / AMT*F5 | |
| group_or_policy_number | VARCHAR | REF*1L | |
| original_reference_number | VARCHAR | REF*F8 | |
| prior_authorization_number | VARCHAR | REF*G1 | |
| payment_date, trace_number, payer_name, payer_id | – | copied | |
| payment_month | VARCHAR | – | partition |

**`remit_service_line`**: one row per 2110 SVC loop (up to 999 per claim). PK `remit_service_line_id`; FKs `remit_claim_id`, `remit_payment_id`.

| column | type | X12 | description |
|---|---|---|---|
| remit_service_line_id, remit_claim_id, remit_payment_id | VARCHAR | – | keys |
| service_line_sequence | INTEGER | – | order within the claim |
| procedure_code_qualifier, procedure_code | VARCHAR | SVC01-1/-2 | the code **as adjudicated** |
| procedure_modifier_1 … _4 | VARCHAR | SVC01-3..6 | |
| procedure_description | VARCHAR | SVC01-7 | |
| line_charge_amount | DECIMAL(18,2) | SVC02 | |
| line_payment_amount | DECIMAL(18,2) | SVC03 | |
| revenue_code | VARCHAR | SVC04 | |
| paid_unit_count | DECIMAL(15,3) | SVC05 | |
| submitted_procedure_code_qualifier, submitted_procedure_code, submitted_procedure_modifier_1..4, submitted_procedure_description | VARCHAR | SVC06 | the code **as billed**, when the payer changed it |
| submitted_unit_count | DECIMAL(15,3) | SVC07 | |
| service_start_date, service_end_date | DATE | DTM*472 or 150/151 | |
| line_item_control_number | VARCHAR | REF*6R | = 837 2400 REF*6R |
| allowed_amount | DECIMAL(18,2) | AMT*B6 | |
| late_filing_reduction_amount | DECIMAL(18,2) | AMT*KH | |
| healthcare_policy_id | VARCHAR | REF*0K (first) | |
| rendering_provider_id | VARCHAR | 2110 REF (HPI etc.) | |
| payment_date | DATE | copied | |
| payment_month | VARCHAR | – | partition |

**`remit_adjustment`**: one row per CAS reason/amount/quantity trio, at claim level (2100) or line level (2110). PK `remit_adjustment_id`.

| column | type | X12 | description |
|---|---|---|---|
| remit_adjustment_id | VARCHAR | – | |
| remit_payment_id, remit_claim_id | VARCHAR | – | always populated |
| remit_service_line_id | VARCHAR | – | null for claim-level CAS |
| adjustment_level | VARCHAR | – | `claim` or `service_line` |
| cas_segment_sequence | INTEGER | – | which CAS segment within the loop (up to 99) |
| trio_position | INTEGER | – | 1–6 within the segment |
| adjustment_group_code | VARCHAR | CAS01 | CO/PR/OA/PI (CR is legacy) → `ref_cas_group` |
| adjustment_reason_code | VARCHAR | CAS02/05/08/11/14/17 | CARC → `ref_carc` |
| adjustment_amount | DECIMAL(18,2) | CAS03/06/… | positive **reduces** payment |
| adjustment_quantity | DECIMAL(15,3) | CAS04/07/… | |
| payment_date, payer_id | – | copied | |
| payment_month | VARCHAR | – | partition |

**`remit_remark`**: one row per remark code. PK `remit_remark_id`. Columns: `remit_payment_id`, `remit_claim_id`, `remit_service_line_id` (null at claim level), `remark_level`, `source_segment` (`MOA`/`MIA`/`LQ`), `source_element` (e.g., `MOA03`, `LQ02`), `code_list_qualifier` (LQ01 `HE` = RARC, `RX` = NCPDP; `HE` implied for MOA/MIA), `remark_code` (→ `ref_rarc`), `payment_month`, +L.

**`remit_provider_adjustment`**: one row per PLB reason/amount pair (six per PLB segment). PK `remit_provider_adjustment_id`; FK `remit_payment_id`.

| column | type | X12 | description |
|---|---|---|---|
| plb_segment_sequence, pair_position | INTEGER | – | |
| provider_identifier | VARCHAR | PLB01 | |
| fiscal_period_date | DATE | PLB02 | |
| adjustment_reason_code | VARCHAR | PLB03-1 (05-1…) | e.g., WO overpayment recovery, FB forwarding balance, L6 interest, 72 authorized return, CS → `ref_provider_adjustment_reason` |
| adjustment_reference_identifier | VARCHAR | PLB03-2 | as sent; often the PCN and/or payer ICN, or the TRN02 for FB |
| referenced_patient_control_number, referenced_payer_claim_control_number | VARCHAR | parsed | best-effort parse of the reference; null if the format is unknown |
| adjustment_amount | DECIMAL(18,2) | PLB04 (06…) | positive **reduces** payment; negative increases it |
| payment_date, trace_number | – | copied | |
| payment_month | VARCHAR | – | partition |

**`remit_party`**: one row per N1 (1000A/1000B) or 2100 NM1 entity (QC, IL, 74, 82, TT, PR, GB). It uses the same columns as `claim_party`, with keys `remit_payment_id` and `remit_claim_id` (null for 1000A/B).

**`remit_reference`**, **`remit_date`**, **`remit_amount`**, **`remit_quantity`**: one row per REF/DTM/AMT/QTY occurrence at the header, 1000A/B, 2100 or 2110 level. They are complete; the promoted typed columns above are copies. The per-claim limits that make wide columns impractical: REF Other Claim ID up to 5 and Rendering Provider up to 10; AMT up to 13; QTY up to 14. Keys: `remit_payment_id`, `remit_claim_id`, `remit_service_line_id`, `loop_id`. Value columns follow the 837 pattern (`reference_qualifier`/`reference_value`, `date_qualifier`/`date_value`, `amount_qualifier`/`amount`, `quantity_qualifier`/`quantity`).

**`remit_misc_segment`**: TS3, TS2 (provider summary in 2000; mostly Medicare Part A), MIA (non-remark elements; MIA and MOA never appear together), PER contacts and other long-tail segments. Generic `element_01…element_24` columns plus `segment_text`, as in `claim_misc_segment`.

## 7. Derived linkage tables (physical; rebuilt at ingest)

**`claim_remit_match`**: one row per candidate (claim, remit claim) pair. PK (`claim_id`, `remit_claim_id`).

| column | type | description |
|---|---|---|
| claim_id, remit_claim_id | VARCHAR | keys |
| patient_control_number | VARCHAR | shared key (CLM01 = CLP01) |
| payer_claim_control_number | VARCHAR | CLP07 from the remit |
| match_method | VARCHAR | `pcn_payer_charge`, `pcn_payer`, `pcn_only` or `payer_claim_control_number` (from a later 837 REF*F8) |
| match_rank | INTEGER | 1 = best candidate for this remit claim |
| is_best_match | BOOLEAN | the agent filters `is_best_match` |
| remit_sequence_for_claim | INTEGER | order of all remits for the claim by payment date: original, reversal, correction, secondary… |
| days_submission_to_payment | INTEGER | |

Matching rules, applied in order:
1. Same `patient_control_number`, a compatible payer (837 `payer_id` against 835 `payer_id`/TRN03), and `CLP03` equal to `CLM02` (or its negative on a reversal). Payment must fall on or after submission.
2. The same, without the charge test.
3. PCN only.

Every claim submission row (original, replacement 7, void 8) can match. The "current" claim is the latest submission sharing the PCN or the F8 chain.

**`claim_line_remit_match`**: one row per (`claim_line_id`, `remit_service_line_id`), with `match_method`:
1. `line_item_control_number`, when 6R was sent and returned.
2. Otherwise `procedure_date_charge`: submitted procedure (SVC06, else SVC01), modifiers, service date and charge, within an already-matched claim pair.
3. Otherwise `unmatched_bundled`.

## 8. Reference and code lookup tables (`ref_*`)
Every ref table has `code`, `description` and `source`. Code lists maintained externally also have `start_date`, `stop_date`, `last_modified_date`, `notes` and `as_of_date`. They are reloaded on X12's update cycle (around March 1, July 1 and November 1, per CMS MLN Matters MM12676). Keeping retired codes with their stop dates lets old remits still decode.

| table | key | content |
|---|---|---|
| ref_carc | carc_code | Claim Adjustment Reason Codes (X12) |
| ref_rarc | rarc_code | Remittance Advice Remark Codes (CMS-maintained) |
| ref_cas_group | group_code | CO, PR, OA, PI (+ legacy CR), with a plain-language `who_is_responsible` |
| ref_claim_status | claim_status_code | CLP02: 1, 2, 3, 4, 19, 20, 21, 22, 23, 25 |
| ref_claim_frequency | claim_frequency_code | 1 original, 7 replacement, 8 void… |
| ref_place_of_service | place_of_service_code | CMS POS list |
| ref_claim_filing_indicator | claim_filing_indicator_code | SBR09/CLP06 (MB, MC, CI, BL…) |
| ref_provider_adjustment_reason | provider_adjustment_reason_code | PLB03-1 (WO, FB, L6, 72, CS, 50, 90…) |
| ref_x12_qualifier | (transaction_type, element_reference, code) | all qualifier lists: REF01, DTP01/DTM01, AMT01, QTY01, NM101, N101, BPR01, BPR04, LQ01, etc. |
| ref_icd10cm, ref_hcpcs | code | free CMS lists; CPT descriptions need an AMA license, so load them only if Xifin holds one |
| data_dictionary | (table_name, column_name) | description, x12_reference, example_values, synonyms, `audience` (`agent`/`engineer`); mirrors `COMMENT ON` |

## 9. Agent-friendly convenience views (logical DuckDB views, not materialized)
Keep these as views over the Parquet files. They are cheap in DuckDB, cannot go stale, and carry decoded `_description` columns. Materialize one into Parquet only if measured latency calls for it, starting with `v_claim`.

| view | grain | what it adds |
|---|---|---|
| v_claim | one row per 837 claim submission | billing provider, payer and patient fields; `remit_count`; `latest_claim_status_code/_description`; `net_paid_amount` (Σ CLP04 across all matched remits, so reversals net out); `net_patient_responsibility_amount`; `first_payment_date`, `last_payment_date`; `has_remit` |
| v_claim_line | one row per 837 line | best-matched 835 line: `allowed_amount`, `line_payment_amount`, `adjustment_total_amount`, `primary_carc_code/_description` |
| v_remit_claim | one row per CLP | payment and payer fields, decoded status, matched `claim_id` |
| v_adjustment_detail | one row per CAS trio | group and CARC descriptions, procedure code, payer, `payment_date`, patient_control_number, matched claim_id |
| v_remark_detail | one row per remark | RARC description plus context |
| v_payment_reconciliation | one row per 835 | BPR02, ΣCLP04, ΣPLB, PLB breakdown, `is_balanced`, trace number |

## 10. S3 layout, multitenancy and partitioning

```
s3://xifin-claimclarity-<env>/tenants/labcorp/
  landing/837p/YYYY/MM/DD/<original_file_name>        # immutable raw EDI (Object Lock)
  landing/835/YYYY/MM/DD/<original_file_name>
  warehouse/v1/
    source_file/ingest_month=2026-10/part-000.parquet
    edi_transaction/ingest_month=2026-10/…
    raw_segment/transaction_type=835/ingest_month=2026-10/…
    claim/submission_month=2026-10/…        (claim_line, claim_diagnosis, claim_party, … same key)
    remit_payment/payment_month=2026-10/…   (remit_claim, remit_service_line, remit_adjustment, … same key)
    claim_remit_match/  claim_line_remit_match/   (unpartitioned, or by payment_month if large)
    ref/ref_carc/as_of=2026-11-01/ref_carc.parquet
  catalog/claimclarity_v1.duckdb   # views + COMMENT ON + data_dictionary, bound to this tenant's prefix only
```

- **Tenant isolation lives in the path and IAM, not in a column filter.** Each customer gets the identical tree under `tenants/<customer>/`. The agent's DuckDB catalog only references its own tenant's prefix, and S3 credentials are scoped to that prefix. `tenant_id` sits in `source_file` only as a guard.
- **Partition keys:** 837 tables use `submission_month`, 835 tables use `payment_month`, and both are copied onto every child table so a pushed-down filter prunes joins consistently. Month is coarse on purpose: it avoids the "tiny files" problem. If a month of a table compresses to under about 100 MB, switch that table to `year`.
- **Within a partition,** sort by date, then `patient_control_number`, with ZSTD compression and 100K–1M row groups. That way a filter on `payment_date` or a PCN lookup skips row groups through Parquet min/max statistics, even when the agent doesn't filter the partition column.
- **Writes are idempotent:** the IDs are deterministic hashes, and a re-parse rewrites the affected partitions. `v1` in the path allows breaking schema changes alongside the old version.

## 11. Key decisions and why
- **Lossless: yes.** Labcorp disputes, appeals and audits sometimes depend on an obscure element, and engineers need exact traceability. Lossless is achieved through `raw_segment`, a byte-exact copy that can rebuild every file using the delimiters stored in `source_file`, plus typed tables for meaning. I partly disagree with a typed-columns-for-everything approach. Turning every TS2/MIA/CR1/CRC element into its own column would add a lot of near-empty, confusing columns for the agent. Those segments stay in the generic `*_misc_segment` tables and in `raw_segment`.
- **Normalized core at the true X12 grain.** CAS, PLB, remarks and diagnoses are true one-to-many relations with high maximum counts (99 CAS, 999 lines). Wide `adjustment_1..n` columns would make "sum CO-45 dollars" error-prone. One row per trio makes it a single `SUM … GROUP BY`.
- **A single `remit_adjustment` table for both levels**, with `remit_claim_id` always filled. "All adjustments on this claim" then never misses the line-level ones, which is the most common 835 analysis bug.
- **Promote the frequently used, keep the long tail.** Typed columns exist for the qualifiers analysts actually ask about (6R, F8, G1, X4, B6, 472, 050). Long REF/DTP/AMT/QTY tables hold all occurrences. Column comments say which column is the promoted copy, so the agent has an obvious first choice.
- **Hierarchy flattened onto the claim.** The HL tree (billing provider → subscriber → patient) is copied onto `claim` and `claim_party`. LLMs handle self-joins on HL parents poorly, and the hierarchy is fixed per claim.
- **Matching is precomputed, not left to the agent.** CLM01↔CLP01 is not unique over time: resubmissions, secondary payers and reversals all share it. 6R comes back only when sent, per X12 RFI 1684. Getting this right once in `claim_remit_match` is far safer than letting each prompt reinvent it.
- **Reversals are kept as rows, never overwritten.** `CLP02 = 22` rows stay, with `is_reversal`. Views compute net paid as Σ CLP04 across all matched remits, which is what actually settled.
- **Codes in core tables, descriptions in ref tables and views.** CARC/RARC text changes on X12's three-times-a-year cycle. Decoding in views keeps core files immutable while the agent still sees plain English.
- **Sign conventions are stored as sent and documented.** CAS and PLB positive values reduce payment, and reversals are negative. Comments and `data_dictionary` state this explicitly, because it is the most likely source of wrong answers about money.
- **Sensitive financial fields are masked in typed tables.** Bank account numbers keep only the last 4 digits; the full value stays in `raw_segment` under engineer-only access. PHI columns are tagged in `data_dictionary` so the planned guardrails can target them.
- **Physical vs. view:** match tables are physical, because the logic is complex and stable. Agent convenience tables are views, because they are cheap, never stale, and easy to change.

## 12. Example questions and the SQL an agent would write

**Q1. "Top 10 denial/adjustment reasons by dollars for UnitedHealthcare in Q3 2026 (excluding patient responsibility)."**
```sql
SELECT adjustment_group_code, adjustment_reason_code, carc_description,
       SUM(adjustment_amount) AS adjusted_dollars, COUNT(DISTINCT remit_claim_id) AS claims
FROM v_adjustment_detail
WHERE payer_name ILIKE '%UNITED%'
  AND payment_date BETWEEN DATE '2026-07-01' AND DATE '2026-09-30'
  AND adjustment_group_code <> 'PR'
GROUP BY ALL ORDER BY adjusted_dollars DESC LIMIT 10;
```

**Q2. "What happened to claim LC12345678?"**
```sql
SELECT c.submission_date, c.claim_frequency_code, c.total_charge_amount,
       r.payment_date, r.claim_status_code, s.description AS status_description,
       r.claim_payment_amount, r.patient_responsibility_amount, r.payer_claim_control_number, r.trace_number
FROM claim c
LEFT JOIN claim_remit_match m ON m.claim_id = c.claim_id AND m.is_best_match
LEFT JOIN remit_claim r       ON r.remit_claim_id = m.remit_claim_id
LEFT JOIN ref_claim_status s  ON s.claim_status_code = r.claim_status_code
WHERE c.patient_control_number = 'LC12345678'
ORDER BY c.submission_date, r.payment_date;
```

**Q3. "Does EFT trace 1234567890 tie out to the deposit, and if not, why?"**
```sql
SELECT trace_number, total_payment_amount, sum_claim_payment_amount,
       sum_provider_adjustment_amount, is_balanced
FROM v_payment_reconciliation WHERE trace_number = '1234567890';

SELECT adjustment_reason_code, adjustment_reference_identifier, adjustment_amount
FROM remit_provider_adjustment WHERE trace_number = '1234567890';
```

**Q4. "Average allowed vs. paid for CPT 80053 by payer in 2026."**
```sql
SELECT r.payer_name, COUNT(*) AS lines,
       AVG(l.allowed_amount) AS avg_allowed, AVG(l.line_payment_amount) AS avg_paid
FROM remit_service_line l JOIN remit_claim r USING (remit_claim_id)
WHERE l.procedure_code = '80053' AND l.payment_month BETWEEN '2026-01' AND '2026-12'
  AND NOT r.is_reversal
GROUP BY ALL ORDER BY lines DESC;
```

**Q5. "Claims submitted more than 45 days ago with no remittance yet."**
```sql
SELECT patient_control_number, payer_name, submission_date, total_charge_amount
FROM v_claim
WHERE NOT has_remit AND claim_frequency_code <> '8'
  AND submission_date < current_date - INTERVAL 45 DAY
ORDER BY total_charge_amount DESC;
```

**Q6 (engineer). "Show me the raw EDI behind this remit claim."**
```sql
SELECT f.s3_bucket, f.s3_key, t.interchange_control_number, t.group_control_number,
       t.transaction_control_number, s.segment_position, s.segment_text
FROM remit_claim r
JOIN source_file f     USING (source_file_id)
JOIN edi_transaction t USING (transaction_id)
JOIN raw_segment s ON s.source_file_id = r.source_file_id
                  AND s.segment_position BETWEEN r.source_segment_start AND r.source_segment_end
WHERE r.remit_claim_id = :id ORDER BY s.segment_position;
```

## 13. Caveats
- Element and qualifier lists were built from TR3-derived public references and payer companion guides, not the licensed X12 TR3 PDFs. A few qualifier names (some AMT/QTY/REF codes) should be checked against the TR3 before DDL is finalized. One public 837P reference page is labeled X222A2, a later errata, so `implementation_version` is recorded and both versions are accepted.
- The match rules are a starting heuristic. Calibrate them on a sample of real Labcorp data, especially PCN reuse across payers and secondary/crossover remits (CLP02 19/20, NM1*TT).
- `is_denied` reflects only CLP02 = 4. Business definitions of "denial" (for example, a zero-paid line with CO adjustments) belong in views or few-shot examples, not in core columns.
- File-size targets come from DuckDB guidance; partition granularity should be confirmed against actual Labcorp volumes once the first months are loaded.
