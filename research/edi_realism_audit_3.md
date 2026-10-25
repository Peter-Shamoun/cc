# EDI realism audit, round 3 (generator 3.2)

Independent auditor, own parser. Sample: acme_labs (NC) 2026-10-01 + 28 days (67,771 claims, 642 835 transactions)
and acme_diagnostics (TX) 5 file days (18,402 claims). Score **7.5/10** (audit 1: 5, audit 2: 7).

Audit-2 status: fixed - MolDX Z-codes/germline, COBA partners, ordering-NPI concentration, PLB02/ICN reuse;
mostly fixed - ID formats (BCBSNC prefixes evenly used, no BlueCard prefixes), clinical misfits; partly - tail
payer geography (worse at file-set level), street names (stem x suffix grid, TX reuses NC stems, surname streets).

New findings (claim-level realism is now hard to fault; the giveaways are at file-set level):
1. Blocker: one shared national payer tail for both labs, head out of state (NC tail by office state: KY 990,
   NC 611, MS 445, UT 345; KY "Bluegrass Regional Health Plan" is NC's #17 payer; TX tail identical; NC-themed
   funds in TX; NC "workers comp state fund" does not exist). Fix: per-tenant tail weighted to home + neighbours;
   out-of-state members mostly arrive via BlueCard.
2. Major: the two labs are statistically identical (CPT shares, lines/claim, deductible share, segment shares);
   TX Medicaid FFS 24% (real STAR managed care leaves <10%). Fix: per-tenant Dirichlet perturbation of profile,
   CPT and segment weights; state managed-care penetration.
3. Major: COB pairings violate rules (FEP secondary behind BCBSNC = 17% of FEP; TRICARE primary ahead of
   commercial/Medicaid 43x; out-of-state fund as a local patient's primary). Fix: birthday/employer rules,
   TRICARE and Medicaid always last, local secondaries, damp Blues-behind-Blues.
4. Major: dx-to-test misfits at volume (E22.1 1.77% of dx slots justifying fertility/hormone panels; Z01.84 2.47%
   on CRP/HSV/ESR/CBC/cultures; B18.2 1.64% - real <0.5%; E87.1 on 266 children).
5. Major: modifiers at random (90 on venipuncture and in-house tests; QW on ~0.9% of lines incl. 85025/87491 for
   an accredited high-complexity lab). Fix: send-out list for 90; no QW for this lab type.
6. Major: commercial/Blues patients 65+ exactly 0% (real ~4-8%, MSP working aged); October $0-to-deductible
   ~20% of commercial claims (estimate 8-12% for Q4).
7. Minor-major: Medicare corrections re-price CLFS (contract noise applied to MAC corrections).
8. Minor: 12 patient service centers with equal volume, own NPIs, residential addresses.
9. Minor: invented MA payer ids (87727, 61102, 60055, 62309); real routing uses parent ids (Cigna-HealthSpring
   63092).
10. Minor: random within one billing system: 2420E repeats claim-level ordering provider on 25%; REF*D9
    clearinghouse trace on 50%.
11. Minor: CARC 18 split CO/OA 50/50 per payer; N522 on non-crossovers; N130 with CARC 18.
12. Minor: freq-8 voids sent to Medicare Part B (MACs reject 7/8 on 837P); no 837s on Columbus Day; generic
    "GENE PANEL" SV101-7 description.

Convincing: envelopes, COB loops (2320/2330/2430, SVD/CAS, DTP*573), ICNs, MolDX, ABN modifiers + PR-50 M38,
CLFS pricing + sequestration, reversals vs corrections, PLB WO pairs netting to zero across files, ACH traces,
Census-grade names; volume and top-CPT ranking; plausible denial rates and days to pay.
