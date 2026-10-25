## Per batch (median, month-close batches listed separately)

| design | business days | requests | MB read | MB written | client CPU s | month-close batch (req / MB read / MB written / CPU s) |
|---|---|---|---|---|---|---|
| current | 1-20 | 483 | 36 | 16 | 7.4 | 2,177 / 178 / 175 / 36 |
| current | 21-60 | 802 | 137 | 58 | 16.5 | 2,335 / 248 / 210 / 39 |
| current | 61-120 | 1,181 | 293 | 125 | 29.7 | 2,928 / 408 / 336 / 59 ; 3,148 / 544 / 435 / 78 ; 3,152 / 685 / 538 / 94 |
| current | total 120 batches | 121,383 | 24.9 GB | 11.4 GB | 2,874 | |
| ducklake | 1-20 | 575 | 51 (catalog file: 4 req, 35 MB read+written) | 26 | 7.5 | 2,143 / 224 / 237 / 41 |
| ducklake | 21-60 | 864 | 131 (catalog file: 4 req, 36 MB read+written) | 29 | 10.5 | 2,586 / 422 / 350 / 69 |
| ducklake | 61-120 | 1,190 | 240 (catalog file: 4 req, 36 MB read+written) | 29 | 13.0 | 998 / 458 / 348 / 72 ; 3,168 / 720 / 587 / 88 ; 3,274 / 868 / 689 / 98 |
| ducklake | total 120 batches | 124,036 | 22.4 GB | 5.6 GB | 1,683 | |
| iceberg | 1-20 | 1,423 | 53 | 23 | 16.1 | 8,145 / 385 / 244 / 64 |
| iceberg | 21-60 | 1,573 | 171 | 67 | 26.6 | 8,459 / 618 / 415 / 92 |
| iceberg | total 60 batches | 103,892 | 8.9 GB | 3.8 GB | 1,528 | |

## Last five ordinary batches

| design | batches | requests | MB read | MB written | client CPU s |
|---|---|---|---|---|---|
| current | 116-120 | 1,491 | 372 | 160 | 39.5 |
| ducklake | 116-120 | 1,551 | 299 | 31 | 13.9 |
| iceberg | 56-60 | 2,179 | 259 | 94 | 34.3 |

## Agent workload

| design | business days | state | session open: requests / MB | query requests median / max | query MB median / max | of which catalog file MB (open+query) | client CPU ms median (open+query) | results equal to current |
|---|---|---|---|---|---|---|---|---|
| current | 20 | as maintained | 22 / 0.06 | 7 / 17 | 0.32 / 3.37 | 0.00 | 195 | 29/29 |
| current | 60 | as maintained | 22 / 0.06 | 12 / 49 | 1.53 / 8.67 | 0.00 | 204 | 29/29 |
| current | 120 | as maintained | 22 / 0.06 | 14 / 43 | 3.40 / 15.77 | 0.00 | 189 | 29/29 |
| ducklake | 20 | as maintained | 11 / 1.85 | 10 / 17 | 2.27 / 5.49 | 3.68 | 413 | 29/29 |
| ducklake | 20 | after compacting now | 11 / 1.85 | 10 / 17 | 2.27 / 5.49 | 3.68 | 403 | 29/29 |
| ducklake | 60 | as maintained | 11 / 1.85 | 73 / 147 | 7.26 / 21.95 | 3.94 | 518 | 29/29 |
| ducklake | 60 | after compacting now | 11 / 1.85 | 12 / 25 | 2.93 / 8.15 | 3.94 | 438 | 29/29 |
| ducklake | 120 | as maintained | 11 / 1.85 | 59 / 130 | 6.71 / 22.81 | 3.94 | 554 | 29/29 |
| ducklake | 120 | after compacting now | 11 / 1.85 | 11 / 29 | 3.28 / 14.09 | 3.68 | 423 | 29/29 |
| iceberg | 20 | as maintained | 21 / 0.30 | 6 / 14 | 0.26 / 2.06 | 0.00 | 494 | 29/29 |
| iceberg | 20 | after compacting now | 21 / 0.30 | 6 / 14 | 0.26 / 2.06 | 0.00 | 496 | 29/29 |
| iceberg | 60 | as maintained | 21 / 0.88 | 29 / 60 | 4.47 / 18.53 | 0.00 | 574 | 29/29 |
| iceberg | 60 | after compacting now | 21 / 0.89 | 6 / 23 | 0.63 / 6.45 | 0.00 | 520 | 29/29 |

## Storage

| design | business days | silver live MB / files | gold live MB / files | all stored versions MB (what S3 bills before lifecycle) | table-format files (data / delete / snapshots) |
|---|---|---|---|---|---|
| current | 20 | 103 / 50 | 140 / 370 | 342 | - |
| current | 60 | 315 / 600 | 300 / 436 | 615 | - |
| current | 120 | 616 / 575 | 605 / 405 | 1221 | - |
| ducklake | 20 | 221 / 526 | 188 / 762 | 460 | silver 50 / 0 / 3; gold 26 / 0 / 4 |
| ducklake after compaction | 20 | 221 / 526 | 188 / 762 | 480 | silver 50 / 0 / 3; gold 26 / 0 / 4 |
| ducklake compaction on demand | 20 | 4 requests, 19 MB read, 19 MB written, 1 s CPU | | | |
| ducklake | 60 | 344 / 601 | 266 / 811 | 663 | silver 600 / 0 / 2; gold 530 / 139 / 2 |
| ducklake after compaction | 60 | 468 / 626 | 577 / 842 | 1117 | silver 75 / 0 / 3; gold 26 / 0 / 4 |
| ducklake compaction on demand | 60 | 2,011 requests, 498 MB read, 454 MB written, 73 s CPU | | | |
| ducklake | 120 | 670 / 576 | 500 / 649 | 1224 | silver 575 / 0 / 2; gold 434 / 108 / 2 |
| ducklake after compaction | 120 | 768 / 601 | 1131 / 680 | 1972 | silver 150 / 0 / 3; gold 26 / 0 / 4 |
| ducklake compaction on demand | 120 | 1,889 requests, 764 MB read, 747 MB written, 87 s CPU | | | |
| iceberg | 20 | 275 / 2201 | 443 / 3763 | 718 | silver 51 / - / 550; gold 25 / - / 689 |
| iceberg after compaction | 20 | 333 / 2208 | 443 / 3763 | 776 | silver 51 / - / 552; gold 25 / - / 689 |
| iceberg compaction on demand | 20 | 494 requests, 69 MB read, 58 MB written, 17 s CPU | | | |
| iceberg | 60 | 867 / 6367 | 3036 / 11560 | 3903 | silver 603 / - / 1604; gold 529 / - / 2057 |
| iceberg after compaction | 60 | 1122 / 6534 | 3272 / 11705 | 4393 | silver 80 / - / 1658; gold 26 / - / 2105 |
| iceberg compaction on demand | 60 | 7,180 requests, 593 MB read, 491 MB written, 81 s CPU | | | |

ducklake catalog file sizes, last batch: {'silver': 8925184, 'gold': 9449472}

catalog commits per batch (iceberg, median): 50.0
