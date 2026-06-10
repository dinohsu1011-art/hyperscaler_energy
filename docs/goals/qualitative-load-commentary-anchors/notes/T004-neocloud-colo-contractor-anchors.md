# T004 Neocloud, Colo, And Contractor Commentary Anchors

## Receipt Summary

Gathered high-signal neocloud, colocation, miner-to-HPC, and contractor/public-infrastructure anchors. Strongest ingestion candidates are CoreWeave, Applied Digital, Core Scientific, TeraWulf, IREN, Crusoe, Galaxy, Digital Realty, Equinix, DataBank, Vantage, and Quanta.

Most evidence is contracted, RFS, pipeline, or billable/active-power language, not utility-metered load. Private operators such as QTS, Switch, and Vantage have useful releases but limited speaker-level utilization detail.

## Candidate Timeline Rows

| statement_id | timeline_bucket | date | precision | organization | speaker | taxonomy | load_stage | polarity | source |
|---|---:|---:|---|---|---|---|---|---|---|
| qual_neocloud_eqix_2024_xscale_001 | 2024Q1 | 2024-02 | month | Equinix | Company release | capacity_utilization_or_sold_out | contracted_service | positive_acceleration | https://investor.equinix.com/sec-filings/current-reports/content/0001628280-24-004799/0001628280-24-004799.pdf |
| qual_neocloud_galaxy_2025_helios_001 | 2025Q2 | 2025-04-23 | day | Galaxy | Company release | contracted_or_committed_demand | contracted_service | positive_acceleration | https://investor.galaxy.com/news-releases/news-release-details/galaxy-announces-commitment-coreweave-host-additional-artificial |
| qual_neocloud_apld_2025_coreweave_001 | 2025Q2 | 2025-06-02 | day | Applied Digital | Wes Cummins / company release | energization_or_ready_for_service | under_construction | positive_acceleration | https://www.datacenterdynamics.com/en/news/applied-digital-signs-250mw-agreement-with-coreweave-for-capacity-at-ellendale-campus-north-dakota/ |
| qual_neocloud_iren_2025_fy_001 | 2025Q3 | 2025-08-28 | day | IREN | Company release | capacity_utilization_or_sold_out | ready_for_service | positive_acceleration | https://irisenergy.gcs-web.com/news-releases/news-release-details/iren-reports-full-year-fy25-results |
| qual_neocloud_crusoe_2025_abilene_live_001 | 2025Q3 | 2025-09-30 | day | Crusoe | Company release | energization_or_ready_for_service | ready_for_service | positive_acceleration | https://www.crusoe.ai/resources/newsroom/crusoe-announces-flagship-abilene-data-center-is-live |
| qual_neocloud_terawulf_2025q3_001 | 2025Q4 | 2025-11-10 | day | TeraWulf | Company release | contracted_or_committed_demand | contracted_service | positive_acceleration | https://investors.terawulf.com/news-events/press-releases/detail/126/terawulf-reports-third-quarter-2025-results |
| qual_neocloud_dlr_2025q4_001 | 2026Q1 | 2026-02-05 | day | Digital Realty | Andy Power / management | capacity_utilization_or_sold_out | contracted_service | positive_acceleration | https://investor.digitalrealty.com/static-files/241d2e64-633e-44fc-9297-013076675afb |
| qual_contractor_quanta_2025q4_001 | 2026Q1 | 2026-02-20 | day | Quanta Services | Company release / management | supply_chain_bottleneck | bottleneck_constraint | mixed_or_uncertain | https://investors.quantaservices.com/_assets/_e85e2a5e6fe60c69e848b2fc49d6dcf2/quantaservices/news/2025-02-20_QUANTA_SERVICES_REPORTS_FOURTH_QUARTER_AND_FULL_371.pdf |
| qual_neocloud_coreweave_2025q4_001 | 2026Q1 | 2026-02-27 | day | CoreWeave | Michael Intrator / management | energization_or_ready_for_service | ready_for_service | positive_acceleration | https://finance.yahoo.com/news/coreweave-crwv-q4-2025-earnings-165353271.html |
| qual_neocloud_coresci_2025q4_001 | 2026Q1 | 2026-03-02 | day | Core Scientific | Adam Sullivan / company deck | construction_delivery_pace | energized_or_metered | positive_acceleration | https://investors.corescientific.com/sec-filings/all-sec-filings/content/0001628280-26-013214/q4fy25earningsdeckvfinal.htm |
| qual_neocloud_nebius_2025_20f_001 | 2026-04 | 2026-04 | month | Nebius | Company filing | contracted_or_committed_demand | contracted_service | positive_acceleration | https://www.sec.gov/Archives/edgar/data/1513845/000110465926052948/nbis-20251231x20f.htm |

## Notes For Judge

- CoreWeave/Core Scientific separate active power, energized power, and billable capacity; Worker must preserve `capacity_basis`.
- Crusoe Abilene has official live/under-construction statements, but secondary reporting on reliability or pullback should be tagged `mixed_or_uncertain` unless primary sources support it.
- Digital Realty, Equinix, DataBank, Vantage, QTS, and Switch often disclose financing, development pipeline, or leasing momentum, not actual energized customer load.
- Third-party transcript hosts may be needed for CoreWeave and Digital Realty; Judge should decide whether to accept them or require official docs only.
