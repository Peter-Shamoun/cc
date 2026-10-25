# EDI realism notes: what a reference lab's 837P/835 traffic looks like

Companion to `research/edi_realism_calibration.yaml`, which holds every number with a source and a confidence level. This file gives the reasoning, says where the evidence is strong or weak, and ends with the 15 largest gaps between `ccsim/edi_mock.py` v2.0 and real traffic, in priority order.

Researched 2026-10-25. Primary data pulled and computed directly:
- **CMS CY2026 CLFS** file (26CLABQ4, 2,243 codes)
- **CMS Medicare Physician & Other Practitioners by Geography and Service, CY2024**, all national rows (13,463 HCPCS x place-of-service rows; services, bene-days, average submitted charge, average allowed)
- **Quest FY2025 10-K** (payer mix by volume and revenue, requisitions, DSO)
- **XiFin 2026 Payor Denial Impact Report** (PDF text tables)
- **CMS 835 companion guides**
- **UHC PLB guide**

Everything else comes from payer and MAC pages, X12 RFIs, KFF, and Optum.

---

## 1. Volume and the 5 MB/day target

**Bytes per claim (computed).** I built segment lists for a realistic lab claim. Field widths are typical: a 14-character PCN, 40-character addresses, a CLIA `REF*X4`, a clearinghouse `REF*D9`, `REF*6R` on every line, and no line breaks. Bytes grow linearly with the number of lines:

- **837P claim:** about **398 + 80 x lines** bytes. A dependent (2000C) adds 142 B; COB (2320/2330B plus `SVD`/`CAS`/`DTP` per line) adds about 300 B + 52 B per line.
- **835 CLP:** about **213 + 109 x lines** bytes (Medicare). Commercial is about 10% more because of PR `CAS`, `LQ` and `AMT*AU`.
- **Current generator:** 467 B per 837 claim and 403 B per CLP, because it averages about 2.1 lines per claim.

**Lines per claim.** In Medicare 2024 there are 318M CLFS-code lines (distinct beneficiary, day and code) against 40.3M venipuncture days. That is **7.9 codes per blood-draw day**. Claims with no draw (urine, swab) and younger commercial patients pull the mean down. A blended mean of about **4.9 lines** (spread 3.5–6.5) is defensible. Units are almost always 1. The exceptions are **87798 (mean 10.3 units per line)** and 87150 (8.4), which is exactly where Medicare's MUE edit (CO-151/N362) fires.

**Mapping 5 MB to volume.** All-in, one claim costs about 835 B of 837 plus 1.10 CLPs x about 770 B of 835, roughly **1.68 KB**:

| What "5 MB/day" counts | Claims per business day |
|---|---|
| 837 and 835 together (bytes split about 50/50) | ~3,000 (2,700–3,300) |
| 837 only | ~6,000 |
| 7-day calendar average | multiply either figure by 1.4 |

**Sanity check against Quest.** Quest processed about 244M requisitions in 2025. About 60% are insurance-billed (insurers 43% + government 17%); 37% are client-billed and never become an 837. So 3,000 claims per day is about 5,000 requisitions per day, roughly 1/190 of Quest: a mid-size or large regional reference lab. Quest's net revenue per requisition is about **$44**, which is a useful check on mean paid per claim.

**835 shape.** I found no public measurement of 835 file counts or sizes, so this is derived (confidence: low):
- The MAC issues one 835 per payee NPI per business day. For this tenant that is about 600 CLPs and about 420 KB, the largest file of the day.
- State Medicaid runs a weekly checkwrite (NC, NV and OH are weekly; AL is biweekly; NC skips the last cycle of June and December).
- Commercial payers range from daily to twice a week, and small payers from weekly to monthly.
- With about 300 payer IDs a month and a Zipf-shaped tail, expect **30–60 835 files a day**: median about 6 claims (about 5 KB), p90 about 120 claims.
- There is no ERA or EFT on weekends or federal holidays. Under CORE 370, the 835 may arrive no more than 3 days before the EFT.

**Patient reuse.** Medicare patients who are tested have **2.7 blood-draw days per year**. The generator's patient pool (0.35 x claims) gives about 2.9 claims per patient within 30 days. In reality a 30-day window holds about 1.1–1.3 claims per distinct patient.

## 2. Payer mix and identifiers

Quest by volume: government is 28% of insurance-billed claims. That covers traditional Medicare and Medicaid FFS; MA and Medicaid MCOs are counted under "healthcare insurers".
- MA is now 54% of eligible beneficiaries (KFF 2025), so MA volume is about the same as Medicare FFS volume.
- 78% of Medicaid enrollees are in MCOs.

Recommended share of insurance-billed claims:

| Payer type | Share |
|---|---|
| Medicare FFS (MAC) | ~20% |
| Medicare Advantage | ~16% |
| Medicaid MCO | ~11% |
| Medicaid FFS | ~3% |
| BCBS plans | ~20% |
| National commercial (UHC, Aetna, Cigna, Humana) | ~21% |
| Regional plans and TPAs | ~7% |
| Tricare and other | ~2% |

The generator has **no Medicare FFS payer at all**, and that is the single most visible gap.

Identifier conventions:
- **Medicare.** Payer IDs are the MAC's 5-digit contract number for the jurisdiction where the lab sits. A lab in Burlington, NC bills Palmetto JM; the "11502" pattern is right, but verify the exact numbers.
- **National commercial.** UHC 87726, Aetna 60054, Cigna 62308 and Humana 61101 are fairly universal across clearinghouses. Blues and Medicaid MCO IDs vary by clearinghouse.
- **835 payer identity.**
  - `N1*PR` carries the payer's processing name, which often differs from the 837 name ("MEDICARE PART B" on the 837 becomes "PALMETTO GBA" on the 835).
  - `TRN03` is usually "1" + the payer's EIN.
  - `REF*2U` is optional.
  - The 835's `ISA06` is the clearinghouse's ID, not the payer's.
  - The generator models only the name difference (`name_835`). It puts the payer ID in `ISA06` and `TRN03`, which real feeds rarely do.

## 3. CPT mix, charges, and allowed amounts

**Volume.** The Medicare 2024 line shares are dominated by **36415 (12.7%)**, then 80053 (8.6%), 85025 (8.2%), 80061 (5.5%), 83036 (4.6%) and 84443 (4.4%). After those come urinalysis (81001/81003), urine creatinine and microalbumin, free T4, BMP, B12, ferritin, iron, PSA, PT and culture work-ups (87086, 87088, 87077, 87186).

**Dollars.** The top codes by allowed dollars are **87798 (6.1%)**, 36415, 81528, 80053, 84443, 80061, 85025, 82306, 83036, G0483 and 80307. Genetic tests are 5% of Part B lab tests but 43% of Part B lab dollars (OIG 2024).

**Charges (chargemaster proxy).** Average submitted charges run **4–11x the CLFS rate for routine chemistry and hematology** (80053: $61 vs $10.56), about 2.5x for NAATs, about 2.6x for definitive drug testing, and 1.2–2.5x for molecular. Venipuncture is $21.81 vs $9.34.

**Allowed amounts.** These should be computed from the **CLFS**, not as a share of charge:
- **Medicare FFS:** 1.0x CLFS, then minus 2% sequestration (CO-253) on every paid line.
- **MA:** about 1.0x.
- **Medicaid:** capped at CLFS by SSA 1903(i)(7); typically 0.6–0.9x.
- **Contracted commercial:** about 0.8–2.0x, typical about 1.15x. PAMA built the CLFS from the median of private-payer rates, so contracted commercial rates cluster near it. Evidence here is weak: Avalere's 2013 CBC figure of $20.26 vs $11.02 predates PAMA.
- **Out-of-network commercial:** a percentage of billed charges.

The generator's 80053 allowed amount is about $35, three times the CLFS rate.

**Code hygiene.**
- **0240U** is not on the CY2026 CLFS.
- **81479** has no national rate (the MAC prices it) and needs a description in `SV101-7` or `NTE`.
- XiFin reports that payers now treat **87801** as a bundling ceiling for STI NAATs billed together.

## 4. Denials

**Rates (XiFin, the only lab-specific source at scale).**

| Year (DOS) | All lab segments | Clinical | Molecular | Pathology |
|---|---|---|---|---|
| 2021 | n/a | 13.6% | 27.2% | 19.8% |
| 2023 | 9.9% | n/a | n/a | n/a |
| 2024 | 8.0% | 6.5% (with hospital outreach) | 32.9% | n/a |
| 2025 | 11.2% | n/a | n/a | n/a |

Across industries for comparison: Optum's hospital index puts all claims at 12% in 2023, and KFF puts ACA in-network denials at 19%. In 2021, contracted payers denied 21.2% of claims and non-contracted payers 32.7%, a ratio of about 1.5. XiFin's payer-group charts are images, so the multipliers by payer class in the YAML (Medicare FFS 0.6x, MA 1.3x, Medicaid MCO 1.5x) are estimates, marked low confidence.

**Reasons: the biggest correction.** For 2025 clinical lab denials, XiFin reports:
- **59.4% "procedure not paid separately"** (bundling: CARC 97/234/236, plus NCCI)
- **15.2% duplicates** (CARC 18)
- 6.6% non-covered (96/204)
- 3.5% prior authorization (197)
- 3.2% modifier (4)
- 2.1% COB (22)
- 1.8% experimental (55)
- 0.5% HSA
- 0.4% wrong payer (109)

Molecular is led by prior authorization (27.6%), then duplicates (16.3%), experimental (14.5%) and bundling (14.2%). Pathology is led by duplicates (35.5%), then bundling (23%) and COB (15%).

The generator is weighted toward 50, 197 and 16 and almost never emits 97, 18, 151 or 236. A dashboard built on its output would rank denial causes in nearly the reverse of the real order.

**Medicare-specific edits worth modeling:**

| Edit | Group / CARC | RARCs |
|---|---|---|
| MUE units | CO-151 (whole line denied) | N362 |
| NCCI procedure-to-procedure | CO-236 | n/a |
| PECOS ordering-provider edit | CO-183 or CO-16 | N574, N264, N265, N575, MA13 |
| CLIA certificate scope | CO-B7 | N95 / MA120 |
| LCD/NCD medical necessity | CO-50 (PR-50 when modifier GA) | N386 / N115 |

Two further Medicare facts:
- Medicare Part B reports adjustments **only at line level**; claim-level `CAS` does not apply to Part B carriers.
- MACs periodically run **mass adjustments**: a wave of reversals and corrections after an erroneous edit, as with Noridian's B7 incidents in 2024 and 2025.

**Partial vs full denials.** Most lab "denied claims" are paid claims with one or two lines denied (bundled venipuncture, MUE, medical necessity on one code). I estimate about 60% of denied claims are partial (low confidence). The generator has 6% partial versus about 8% full.

**Rework.**
- Corrected claims are 2–5% of 837 volume.
- Medicare often wants a *new* claim rather than a frequency-7 replacement for PECOS and CO-16 fixes.
- 2025 appeal success, first attempt (clinical): additional information 36%, medical necessity 10%.
- Appeals return about 0.35% of insurance payments for clinical labs but 9.3% for molecular.

## 5. Timing, interest, PLB, reversals

**Days from receipt to payment.**
- **Medicare:** an electronic clean claim cannot be paid before **day 14** (a 13-day floor). Interest is owed after day 30, at 4.125% for 2026H1 and 4.75% for 2026H2, and is not paid on full denials. It is reported as CLP-level `AMT*I` and summed in `PLB L6`.
- **Medicaid FFS:** follows the weekly checkwrite.
- **Commercial and MA:** no good public lab number. Hospital data (Crowe, Florida Hospital Association) shows long tails. The YAML uses a typical 12–16 days with a 5% tail beyond 60 days (low confidence).
- **Reference point:** Quest's DSO is 48 days.

**PLB semantics (UHC guide).**
- **WO:** a positive amount recoups an earlier overpayment. When a reversal and its correction land in *different* 835s, the reversal's 835 carries a negative WO and the correction's 835 a positive WO.
- **72:** a refund check, offset by WO.
- **FB:** negative when a balance moves forward to a later 835, positive when it comes back; the reference is the originating `TRN02`.
- **J1, IR, CS:** J1 is a suppressed payment, IR is IRS withholding, and CS (mostly FEP/Blues) is an adjustment overpayment.

The generator always emits a correction in the same 835 as its reversal, and uses its own WO references.

**Reversals.** I estimate `CLP02=22` at about 1–2% of CLPs (low confidence). The generator's combined rate (1.5% + 2.5% + 0.4% of claims) is probably about 2–3x too high.

## 6. Patient responsibility and COB

- **Medicare Part B CLFS tests have no deductible and no coinsurance** (Quest 10-K). So Medicare-primary lab claims produce almost no PR and almost no Medigap or Medicaid secondary payments. The exception is pathology (PFS), which carries the deductible plus 20%.
- **Medicare crossovers** happen through COBA. The lab does not send a secondary 837. The Medicare 835 shows `CLP02=19`, an `NM1*TT` crossover carrier and an `MOA` with MA18 (or N89 when the claim crosses to more than one payer).
- **Commercial (KFF 2025 EHBS):** 88% of covered workers have a deductible, averaging **$1,886**; 34% have deductibles of $2,000 or more. The generator's PPO deductible of $500 is far too low.
- **How much PR shows up:** XiFin reports **30% of lab claims carried patient responsibility in 2025** (26% in 2023), while patient payments fell to 9%. PR should spike in January–March as deductibles reset.
- **COB billed through an 837:** probably only 2–8% of claims. The generator's 12% secondary coverage, with a secondary 837 for every claim that has PR, overstates it, especially since it never applies the Medicare zero-PR rule.

## 7. X12 conventions (837P and 835)

**837P.**
- **Delimiters:** `*`, then `:` (about 75%) or `>` as the component separator, `^` for repetition, `~` as terminator. Most clearinghouse traffic has no line breaks.
- **Envelope:** usually `ZZ` qualifiers. `GS01=HC`, version 005010X222A1, `BHT*0019*00*...*CH`.
- **Size limits:** at most 5,000 `CLM` per ST (a TR3 recommendation that companion guides repeat), usually 1–10 files per day.
- **Billing provider:** `PRV*BI*PXC*291U00000X`, a 9-digit ZIP, `REF*EI`.
- **CLM05:** `81:B:1`, about 92% POS 81.
- **CLIA:** `REF*X4` is **required** at claim level. Anthem rejects claims without it. A line-level `REF*X4` is allowed when lines are performed by different CLIA sites (X12 RFI 1742).
- **Prior auth:** `REF*G1` on molecular claims.
- **Original claim:** `REF*F8` on frequency 7/8.
- **Notes:** `NTE` for unlisted-code descriptions.
- **Ordering provider:** 2310A `NM1*DN` with `NM102=1` (an organization fails the PECOS edit), or a line-level 2420E `NM1*DK`.
- **Line pointers:** up to 4 diagnosis pointers in `SV107`.
- **Lab modifiers:** 91, 90, QW, 59/XU, GA/GZ/GY, 26/TC.

**835.**
- **Medicare:** `CLP06=MB`; only CO, OA and PR groups (never PI); no `NM1*IL`; the patient is identified by MBI; `MOA` carries claim remarks (MA01, MA18, N89); `LX*000000`/`000001` style; CO-253 on every paid line; LCD/NCD reference on medical-necessity denials.
- **Commercial:** `SVC06` when a code is recoded (panel unbundling, 87801), `SVC07` when units are cut, `AMT*B6`, `REF*6R` echoed only when it was sent, `LQ*HE`, line splitting and bundling.

## 8. Quirks the parser must survive

The generator covers: delimiter variants, truncation, non-X12 files, a damaged IEA, byte-identical redelivery, multiple STs per file, a missing `REF*6R`, and 835 names that differ from the 837.

It is missing:
- **Envelope and file problems:** 80-character wrapped lines, several ISAs in one file, a BOM or leading whitespace, trailing NULs or Ctrl-Z, mixed line endings, ISA IDs not padded to 15 characters, `SE01` counts off by one, `ISA13` control numbers reused across days.
- **835 content changes:** payers altering `CLP01` (uppercased, truncated to 17 or 20 characters, special characters stripped), and an ICN reused on an adjustment.
- **835 structure:** SVCs with no `DTM*472`, one CARC under two groups on one line, PLB segments with several adjustment pairs and composite references.
- **Identity drift:** member IDs with alpha prefixes, payer names that change mid-month, and occasional payer-side unbalanced 835s.

---

## 9. The 15 most important gaps, prioritized

Ranked by how much each one distorts metrics a user would see (denial rates, top CARCs, payer and CPT rankings, dollars), then by effort.

1. **No traditional Medicare.** Add a MAC payer (CLFS-based, 14-day floor, daily 835, `CLP06=MB`, line-level CAS only, CO-253 2% sequestration on every paid line, `MOA` remarks, no PR on CLFS codes) at about 20% of claims. Also add MA ≈ Medicare FFS volume, and split Medicaid into MCO and FFS.
2. **Allowed amounts are about 3x too high.** Replace `charge x fee_ratio` with `CLFS_2026[code] x payer multiplier` (Medicare 1.0, MA about 1.0, Medicaid at most 1.0 and typically 0.8, commercial about 1.15). Keep the chargemaster at 4–11x CLFS for routine tests, from the Medicare average-submitted-charge column.
3. **The denial-reason mix is inverted.** Clinical denials should be about 59% bundling (CO-97/234/236), 15% duplicates (18), 7% non-covered, 3.5% prior auth, 3% modifier (4), 2% COB (22). Molecular is led by prior auth (28%) and experimental (55, 15%). Add the Medicare edits CO-151/N362, CO-183/N574, CO-B7, CO-50/N386.
4. **Too few lines per claim and the wrong CPT mix.** The mean is about 2.1 lines; it should be about 4.9. Put 36415 on about 75% of blood claims. Add urinalysis, urine chemistry, free T4, B12, ferritin, iron, PSA, PT, and culture reflexes (87088/87077/87186). 87798 needs about 10 units. Replace 0240U with 87637/0241U. Use order profiles (YAML `cpt_mix.order_profiles`).
5. **Volume is about 25x too low for 5 MB/day.** It needs about 3,000 claims per business day per tenant (or about 6,000 if 5 MB means 837 only). Split 837s into several files per day with at most 5,000 CLM per ST.
6. **Most denials should be partial.** About 60% of denied claims should be paid claims with one or two denied lines. Today `p_partial_line_denial` is 0.06 while full-claim denials are about 8%.
7. **835 cadence and file population.** Real traffic has 30–60 files a day: one large Medicare file, weekly Medicaid checkwrite files, commercial files daily to twice weekly, and a long tail of tiny files. Add federal holidays (no ERA or EFT), state fiscal-year skips, and BPR16 at least as late as `DTM*405`, within 3 days. Today there is one 835 per payer per day whenever events exist.
8. **Payer long tail and identifiers.** Grow from 6 payers to about 150–600 payer IDs per month, Zipf-distributed. 835 `TRN03` should be "1" + EIN, `ISA06` should be the clearinghouse, `REF*2U` optional, and the 837 and 835 names should differ about 60% of the time. Currently the 835 uses the payer ID as sender and as `TRN03`.
9. **Patient responsibility is unrealistic.** Use deductibles averaging $1,886 (34% at $2,000 or more), about 30% of claims with any PR, a January–March spike, $0 for preventive screening, and $0 for Medicare CLFS tests. Today the PPO deductible is $500, HDHP $2,500, and patients repeat too often (2.9 claims per 30 days vs about 1.15).
10. **COB and crossover.** Medicare crossovers should be COBA-style (`CLP02=19` + `NM1*TT` + MA18, no secondary 837). Secondary 837s should drop to about 4% of claims, almost all with a commercial primary. Today 12% of patients have secondary coverage and an 837 goes to every one with PR.
11. **CLIA and lab-specific 837 segments.** Add `REF*X4` (required; its absence should produce a front-end rejection), `REF*G1` when prior auth was obtained, `REF*D9`, `NTE` for 81479, `SV107` pointers to the relevant diagnosis, the lab modifiers (91, 90, QW, 59/XU, GA/GZ/GY, 26/TC), 2420E ordering or 2310C facility on some claims, and a 9-digit billing ZIP.
12. **Reversal and correction behavior.** Cut the rates (`CLP02=22` about 1–2% of CLPs). In about 30% of cases, split the reversal and its correction across 835s with offsetting PLB WO (negative, then positive). Add occasional MAC mass-adjustment waves over a whole edit and date range. Use realistic WO references (DOS + patient account, or FCN + MBI) and PLB 72, J1, CS and IR.
13. **Seasonality and calendar.** Respiratory panels should run about 3x in December–February, with routine volume dipping in summer and around holidays. Add holiday volume dips and the post-holiday catch-up, plus Monday catch-up of Saturday DOS (today only Monday 1.3 / Friday 0.9 plus noise). DOS-to-submission lag should be longer for molecular (about 14 days).
14. **835 line fidelity.** Add `SVC07` (original units) with unit reductions, line splits and bundles (payer folds lines and denies the rest with CO-97 at $0), `SVC06` on 87801 ceiling recodes, `LQ` on only about 40% of adjusted lines, `REF*6R` echoed only when sent, `AMT*AU`/`REF*1L`/`DTM*050` at payer-specific rates, and Medicare REF LCD/NCD references.
15. **Parser-hardening quirks.** Add 80-character wrapping, several ISAs per file, a BOM, NUL or Ctrl-Z padding, mixed line endings, short ISA IDs, `SE01` off by one, `ISA13` reuse, payer-altered `CLP01` (truncated or uppercased), member-ID prefixes, mid-month payer renames, and rare unbalanced payer 835s. Lower the current corrupt-file rate (3%) and duplicate-835 rate (4%) to about 1–2% each so they stay edge cases.

## Confidence summary

- **High:** CLFS rates; Medicare volumes, units, charges and allowed amounts; Quest payer mix and requisitions; the Medicare payment floor, interest, sequestration and MUE/PECOS/B7 codes; CLIA `REF*X4`; the 5,000-CLM limit; Medicare 835 conventions; the Medicaid cap at CLFS; KFF deductibles.
- **Medium:** XiFin denial rates and reason mixes (vendor data, but large); PLB semantics (UHC and BCBSTX, generalized to other payers); payer-ID conventions.
- **Low (tunable defaults, no public measurement):**
  - denial multipliers by payer class
  - the partial vs full denial split
  - commercial days to pay
  - 835 file counts and sizes per day
  - reversal and corrected-claim rates
  - weekday and seasonal indices
  - commercial line shares
  - quirk rates

---

## Generator 3.0 status (implemented 2026-10-25)

`ccsim/edi_mock.py` 3.0 implements the gaps above; `tests/test_edi_calibration.py` asserts the distributions.
Measured on a one-year, 5 MB/business-day run (one tenant, ~3,100 claims per business day) and a 150-day test run:

| Gap | Status | Measured (spec target) |
|---|---|---|
| 1 Medicare / payer mix | Done: MAC by tenant state (Palmetto JM 11502, Novitas JH 04412), MA, Medicaid FFS + MCOs, Blues + FEP, national ids, 320-payer Zipf tail | segment shares within 1 pt of the spec; top 10 ids 72% (~70%); ~325 payer ids/month (150-600) |
| 2 Allowed amounts | Done: CY2026 CLFS (shipped as `ccsim/data/clfs_2026q4.csv`) x payer multiplier x stable contract noise; Medicaid capped; OON = share of billed | allowed/CLFS: Medicare 1.00, MA 1.00, Medicaid 0.84, commercial 1.12; charge/CLFS 6.8 |
| 3 Denial mix | Done (XiFin clinical/molecular/pathology mixes, Medicare edits 151/236/183/B7/50) | 11% of claims; bundling ~55-60% of non-duplicate denials; duplicates ~14% |
| 4 Lines / CPT mix | Done: order profiles + long CLFS tail; 36415 on 75% of draws; 87798 multiples; 0240U/0241U dropped | 4.9 lines (Medicare 5.9, commercial 4.4); ~600 distinct codes/month at 60 claims/day |
| 5 Volume | Done: 837s split at 2,500 CLM per file | 5 MB/day = ~3,130 claims per business day; 57/43 837/835 bytes |
| 6 Partial denials | Done (emerges from the reason mix) | 60-70% of denied claims are line-level |
| 7 835 cadence | Done: per-payer weekdays / every N weeks, NC half-year skip, no weekends/holidays, BPR16 0-2 business days after DTM*405 | ~32 835 files/day (25-80); Medicare daily file ~480 KB (420) |
| 8 Identifiers | Done: ISA06 = clearinghouse, TRN03 = 1 + EIN, REF*2U on ~half, 835 names differ on ~60% | |
| 9 Patient share | Done: KFF deductibles met through the year, coinsurance/copay, ACA preventive $0, Medicare CLFS $0 | 30% of claims with PR; deductible avg ~$1,900 |
| 10 COB / crossover | Done: COBA CLP02=19 + NM1*TT + MA18 on ~20% of Medicare; secondary 837s | ~4% of claims billed to a secondary |
| 11 837 lab segments | Done: REF*X4, G1, D9, NTE + SV101-7 for 81479, SV107, modifiers 91/90/QW/59/XU/GA/GZ/GY/26/TC, 2420E, 2310C, ZIP+4, POS mix | |
| 12 Reversals | Done: rates cut, 30% split across 835s with -WO/+WO, MAC mass-adjustment waves, 72/J1/CS/IR, UHC-style WO refs | CLP22 ~1.4% of CLPs (0.5-4%) |
| 13 Calendar | Done: weekday index, day-after-holiday 1.5x, holiday weeks 0.75x, monthly + respiratory index, DOS weekday mix, molecular DOS lag | respiratory Jan ~5x Jun |
| 14 835 line fidelity | Mostly: SVC07 unit cuts, 87801 ceiling recode (SVC06), 6R echoed only when sent, AMT*AU, REF*1L, DTM*050/232, REF*0K NCD/LCD on Medicare med-nec | skipped: split lines (one billed line as two SVCs) |
| 15 Quirks | Partly: 80-col wrap, BOM, NUL padding, several ISAs per file, ISA13 reuse; corrupt 1%, re-delivery 2% | skipped: payer-altered CLP01, SE01 off by one, unpadded ISA ids, lowercase content, mid-month payer renames, unbalanced payer 835s (each breaks a business-rule join or a fixed-width ISA read; decide in the lake phase) |

Low-confidence items are typical values and stay tunable (`Scenario`, module constants): denial multipliers by payer
class, the partial share, commercial lags, cadence, reversal/rework rates, quirk rates, plan designs.

---

## Generator 3.1: fixes for realism audit 1 (2026-10-25)

`research/edi_realism_audit_1.md` scored 3.0 at 5/10. What changed, by finding:

| # | Finding | 3.1 |
|---|---|---|
| 1 | Placeholder demographics | `ccsim/edi_reference.py`: the 1,000 most common Census 2010 surnames (Hispanic surnames weighted by the state's share), SSA first names by sex and birth cohort, middle initials, house number + common street + suffix (plus NC/TX highway routes, apartments, PO boxes), real city/ZIP pairs in each lab's referral area, local area codes (lab PER: 336) |
| 2 | Sex/age-blind tests | the patient is drawn first (clinical sex, age, known chronic conditions); profiles and every code are filtered by sex and age (PSA men 40+, HPV/hCG/obstetric panel women, no lipids or drug screens on toddlers); diagnoses carry sex/age limits. Residue: patients whose insurance gender marker differs (0.4%): KX on Medicare and on half of commercial lines, else CO-16 MA39 / CO-11 M76 |
| 3 | NCCI pairs paid | the lab's scrubber strips panel components (CMP/BMP/renal/hepatic/lipid/CBC/obstetric/hepatitis panels, urinalysis exclusives); ~0.5% of claims keep one, always denied CO-97 (M80/N20) or CO-236 N20. Remaining bundling is payer policy (36415, reflex cultures, LDL with lipid panel, specimen-validity creatinine with drug tests, 87801 ceiling) |
| 4 | 64 ICD codes, unrelated pointers | `ccsim/data/icd10cm_lab_dx_2026.csv`: 2,340 FY2026 billable ICD-10-CM codes (every one checked against CMS `icd10cm_codes_2026.txt`) in 50 clinical domains, Zipf within each; tests map to the domains that justify them; each line points at a fitting diagnosis (the patient's known condition when one fits); ~1,500 distinct codes a month at 5 MB/day |
| 5 | Medicare pays screening | Medicare screening-only orders: GA -> PR-50 M38, GZ -> CO-50 N386, GY -> PR-96/204 N425, no modifier -> CO-50 N386 + REF*0K NCD; covered screening benefits (Z13.6 lipids, Z13.1 glucose, G0103 PSA, G0328 FIT, STI/HIV/HCV) pay; MA applies the NCDs less strictly |
| 6 | Single-lab codes | 81528, 0037U and all PLA/MAAA codes removed; FIT (82274/G0328), CFTR, F2, F5, MTHFR, JAK2 added |
| 7 | Cold start | `Scenario.burn_in_days = 90` simulated and not emitted: day 1 has ~33 835 files and Medicare; recoupments target payments 30-365 days old (median ~3 months), 60% partial, sometimes several claims at once (FB carry-forwards follow) |
| 8 | Placeholder banks / cities | valid-checksum ABA + account per paying company, the lab's receiving account fixed, real claims addresses for national/regional payers (plausible ones for the tail) |
| 9 | No PER*BL | PER*CX (most payers), PER*BL always, PER*IC with the payer web site |
| 10 | Envelopes | ISA13 per sender/receiver pair with its own start, ISA10/GS05 send times (lab batch runs, clearinghouse overnight ERAs), 1-5 uneven 837 batches a day, timestamped file names; a re-delivery gets a new name, same bytes |
| 11 | NPIs / CLIA | NPIs hashed over the issued range (no shared prefixes), billing NPIs checked absent from NPPES; CLIA 34D2187436 checked absent from the CMS CLIA file (34D0655059 was LabCorp Burlington's) |
| 12 | Cheap wins | CO-253 on MA plans paying Medicare rates, PI-45 on some non-contracted payers, CARCs 119/242/252/288, per-payer PLB03-2 styles, invented tail names from varied patterns, holiday-light and flatter Mon-Thu collection dates |

Measured, one tenant at 5 MB/business day, 30 days from 2026-07-01 (3.0 numbers in brackets; 3.0's 30-day run had a
cold start, its one-year figures are in the table above): lines/claim 5.1 [5.0]; Medicare 6.3, commercial 4.6;
initial denial rate 10.0% [10.2%], 72% line-level [72%], bundling 50% of non-duplicate denials; allowed/CLFS Medicare
1.00, MA 1.00, Medicaid 0.83-0.85, commercial 1.14; 34% of claims with patient share; 4.9 MB a day = ~2,980 claims
per business day [3.2 MB on a cold start; ~3,130 in a year] (bytes per claim rose: middle names, apartments, more
diagnoses, PER segments); 33 835 files a day from day 1 [0 on days 1-3]. The 150-day calibration tests
(`tests/test_edi_calibration.py`) pass unchanged except that they count claims billed inside the window.
One year at 5 MB/business day (plus the 90-day burn-in): 1,306 MB raw over 250 file days, 3,031 claims per business
day (base), 9.5 minutes, peak RSS 972 MB (the burn-in starts the window with a full set of claims in flight).

Ledger: a claim billed during the burn-in appears only with what the window delivered (its 835 events and any 837
resubmission). `bench/truth_db.py` `ledger.claims` holds claims with an 837 in the window (like `sem.claims`), with
`submitted` = first submission in the window; events hold every 835 event in the window (like `sem.remit_claims`).

Not done: NCD frequency edits (e.g. A1c more often than every 3 months) need per-patient history; the 2-4 month
tail of WO recoupments means a 45-day benchmark window sees few recoupments of its own payments.

## Generator 3.2: fixes for realism audit 2 (2026-10-25)

`research/edi_realism_audit_2.md` scored 3.1 at 7/10.

| # | Finding | 3.2 |
|---|---|---|
| 1 | MolDX / germline genetics paid | Palmetto (JM) is flagged MolDX: molecular lines carry the lab's DEX Z-code in SV101-7 (8% dropped -> CO-16 M20, N350 on 81479); technical-assessment non-coverage (MTHFR always, CFTR carrier, BRCA without a cancer diagnosis, part of PGx/81479) -> CO-50 N386 or PR-50 M38 with GA. Commercial, Medicaid and MA plans require prior auth for germline tests: CO-197/15 without, CO-198/50/96 now and then with. Measured: 41% of commercial germline claims denied, 37% of Medicare's (plus GA patient-liable lines) |
| 2 | Uniform identifiers | member id and claim-number templates per payer (UHC 9 digits, Aetna W+9, Cigna U+8+2, Humana H+8, FEP R+8, NC Medicaid and its MCOs 9 digits + letter, TX Medicaid 9 digits, Blues prefixes per group); CLP07 formats differ by payer; Medicare ICN Julian date = receipt date (DTM*050); corrections get their own ICN |
| 3 | Tail payer geography | regional stems map to the states their claims office can be in; at most 3 payers per stem; PO boxes of 2-6 digits and some street-and-suite addresses |
| 4 | Clinical misfits | sex/age limits on every code (L68.0 female, Z79.891 14+), code limits merged across domains; 82570 only with microalbumin or a drug test; uric acid -> gout/CKD; serology defaults to infection codes, Lyme demoted (A69.20 rare); targeted tests (PSA, hormones, coagulation) never point at general work-up codes; Z79.01 only with anticoagulation |
| 5 | Streets and names | 10,000 Census 2010 surnames with counts and Hispanic share (`census2010_surnames.csv`), SSA top-400 first names per sex and birth decade 1920-2017 (`ssa_first_names.csv`): ~8,500 distinct surnames in a month at 5 MB/day; streets named after stems and surnames, each stem with its own suffix mix (MAIN ST, LAKE RD, CREEKSIDE DR), NC "<NAME> MILL/CHAPEL/STORE RD" |
| 6 | COBA partners | BCBSNC (BCBSTX) Medicare Supplement leads, then AARP, the state Medicaid, Mutual of Omaha and a tail |
| 7 | One ordering NPI dominant | 2 x daily volume ordering clinicians, flatter Zipf (top one <3% of claims) |
| 8 | 2310C = billing address | 12 patient service centers per lab with their own NPI and address |
| 9 | PLB02 constant; duplicates at Medicare | PLB02 per payer (fiscal year end, month end or remittance date); 70% of Medicare exact duplicates are front-end rejected (277CA) instead of CO-18 on an 835 |
| - | Medicare 90+ = 11% | ~7% |

Measured at 5 MB/day for 30 days from 2026-07-01: lines/claim 5.13; denial rate 10.4%, 70% line-level; allowed/CLFS
unchanged; ~1,560 distinct diagnoses; 2,990 claims per business day; 32 835 files a day from day 1.
One year at 5 MB/business day plus the burn-in: 1,330 MB raw, 3,088 claims per business day, 622 s CPU, peak RSS
978 MB.
