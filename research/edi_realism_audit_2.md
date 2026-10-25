# EDI realism audit, round 2 (generator 3.1)

Independent auditor, sample acme_labs 2026-10-01 + 28 days at 5 MB/day (19 file days, 69,999 claims, 681 files),
own parser. Score **7/10** (audit 1: 5/10).

Audit-1 status: fixed - NCCI pairs, Medicare GA/GY/GZ/NCD, proprietary codes, cold start, banks (major payers),
PER*BL, envelopes, NPIs/CLIA; mostly fixed - demographics (street names still gridded), sex-specific (8 leaks +
new L68.0 on men), diagnosis fit (tail misfits); partly - tail payer names templated, PLB02 constant.

New findings (severity):
1. Major: Palmetto is the MolDX MAC, but molecular claims carry no DEX Z-code and are mostly paid (81479 82/92,
   81226, 81225, BRCA 81162); commercial pays germline genetics ~80%. Real: MolDX denies without Z-code
   (CO-16 + M20/N350-type RARC); commercial denies germline genetics 30-50% (CO-197/15, prior auth).
2. Major: identifier formats are uniform across payers - member ids 9 digits for UHC/Aetna/Cigna/Humana/UMR;
   every BCBSNC id prefix JMT; FEP FDO+9 (real R+8); NC Medicaid 10 digits (real 9 digits + letter); CLP07
   12-digit numeric for 20+ payers; Medicare ICN Julian date = 835 date, not receipt date.
3. Major: tail payer names geographically incoherent (Rio Grande Health Plan PO box in Asheville), repeated
   stems (Great Lakes x6), 6-digit PO boxes almost everywhere.
4. Major: tail clinical misfits - L68.0 (hirsutism) on 660 male claims; Z79.891 (opioid therapy) on 341 children
   under 12 (82570 billed alone); 86694 -> M19.90/A69.20, 80202/87070 -> A69.20, 84550 -> L68.0, 84153 -> Z79.01;
   confirmed Lyme A69.20 in 2.2% of dx slots (implausible for NC).
5. Major/minor: street names = common stem x random suffix (MAIN ST 779, MAIN DR 698, MAIN RD 529); 7,188 streets,
   1,000 surnames, 441 first names over 70k claims.
6. Minor: three COBA crossover partners in equal thirds, one generic STATE MEDICAID CROSSOVER (real: BCBSNC Medicare
   Supplement dominant + Zipf tail of Medigap, named Medicaid).
7. Minor: one ordering NPI on 4,833 claims (~250/day).
8. Minor: 2310C service facility sent with the billing provider's address (TR3: omit when equal).
9. Minor: PLB02 always 20261231; split reversal + correction reuse CLP07; CARC-18 duplicates are the top full
   denial even at Medicare (most would be 277CA front-end rejections).

Distributions: lines/claim 4.93 (22% single-line); top CPTs match CMS Part B ranking; days to pay medians
Medicare 16, MA 19-26, commercial 15-16, Medicaid FFS 19; paid/charge ~0.16 flat across classes (medium
confidence it should vary); 21-24% of commercial claims $0 paid all-PR (high for October); Medicare 90+ = 11%.

Convincing now: NC payer structure (Palmetto JM, NCTracks MCOs, real PO boxes), Census patients with consistent
city/ZIP/area code, valid NPIs/MBIs, high-volume dx-to-test fit, Medicare ABN/NCD handling, MA sequestration,
steady-state timing, reversals and split corrections, varied ABAs and envelopes.
