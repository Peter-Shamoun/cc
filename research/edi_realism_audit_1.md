# EDI realism audit, round 1 (generator 3.0)

Independent auditor (did not see the calibration agent's reasoning), sample: acme_labs, 20 business days at 5 MB/day
(62,434 claims, 254 files). Score **5/10**: structurally strong, fails on clinical and identity realism.

Ranked findings (severity):
1. Blocker: placeholder demographics (20 surnames such as FAKERSON/LOREMSON, MOCK ST, 555-01xx phones).
2. Blocker: sex-specific tests/diagnoses ignore sex (PSA on F, HPV/pregnancy/N76.0/Z01.419 on M, all paid).
3. Blocker: NCCI component pairs billed and paid (80053+82947, 80053+80048, 81001+81003; <5% CO-97/236).
4. Major: 64 ICD-10 codes, flat top-5; line pointers unrelated to the test (85610 -> Z00.00, 81528 -> F32.9).
5. Major: Medicare pays GY/GZ lines and NCD-screening CBC/TSH on Z00/Z13 only; PR-50 almost absent.
6. Major: proprietary single-lab codes (81528 Cologuard, 0037U) billed by a regional lab.
7. Major: cold start (no 835s days 1-3, Medicare from day 8); PLB WO recoupments 2-7 days after payment.
8. Major: identical placeholder bank accounts (ABA 999999999) for every payer; payer addresses from ~8 cities.
9. Major: no PER*BL (required in 835 loop 1000A).
10. Major/minor: ISA10 always 1200; one global ISA13 counter across senders; *_redelivered.x12 file names.
11. Minor/major: billing NPI below the issued range, ordering NPIs share a prefix; CLIA appears to be a real lab's.
12. Minor: no CO-253 on MA; no PI group or CARCs 242/252/288/119; PLB03-2 format uniform; template tail payer
    names; collection-date weekday shape (holiday peak).

Convincing already: claim/line balancing, CO-45 + CO-253, AMT*B6, Medicare 14-day floor and CLFS limits,
crossovers (CLP02=19, NM1*TT, MA18), real payer ids, 837 loop structure (POS 81, REF*X4, NM1*DN), MBIs, COB,
timely-filing timing, delimiter/wrapping variety, multi-ISA, reversals, CPT volume mix, denial-reason shape.
