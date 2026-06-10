# T005 Grid, Regulator, And Planning-Body Commentary Anchors

## Receipt Summary

Gathered high-signal grid-operator, regulator, and planning-body anchors. Strongest forecast/queue evidence is ERCOT, PJM, MISO, WECC, NYISO, NERC, EIA, FERC, CAISO/CEC, Georgia PSC, and Virginia SCC.

Actual realized-load evidence is thinner: WECC gives broad 2024 annual/peak demand records and early large-load effects, FERC/EIA discuss operating data-center capacity/demand growth, while most RTO/ISO materials remain forecast, queue, planning, or regulatory-constraint evidence. These should not map to campus-level energized MW except as regional narrative/context.

## Candidate Timeline Rows

| statement_id | timeline_bucket | date | precision | body | taxonomy | load_stage | polarity | source |
|---|---:|---:|---|---|---|---|---|---|
| qual_grid_ercot_2025_cdr_001 | 2025Q1 | 2025-02-13 | day | ERCOT | grid_or_regulatory_constraint | bottleneck_constraint | mixed_or_uncertain | https://www.ercot.com/news/release/02132025-ercot-releases-capacity |
| qual_grid_ferc_2025_colocation_001 | 2025Q1 | 2025-02-20 | day | FERC | grid_or_regulatory_constraint | bottleneck_constraint | mixed_or_uncertain | https://www.ferc.gov/news-events/news/ferc-orders-action-co-location-issues-related-data-centers-running-ai |
| qual_grid_ercot_2025_ltdef_001 | 2025Q2 | 2025-04-08 | day | ERCOT | demand_future | announced_pipeline | positive_acceleration | https://www.ercot.com/files/docs/2025/04/08/ERCOT-2025-Long-Term-Load-Forecast-Report.pdf |
| qual_grid_nyiso_2025_power_trends_001 | 2025Q2 | 2025-06 | month | NYISO | demand_future | announced_pipeline | mixed_or_uncertain | https://www.nyiso.com/-/press-release-nyiso-releases-power-trends-2025 |
| qual_grid_gaps_2025_irp_001 | 2025Q3 | 2025-07-15 | day | Georgia PSC | contracted_or_committed_demand | contracted_service | mixed_or_uncertain | https://psc.ga.gov/site/assets/files/8932/media_advisory_2025_irp_vote.pdf |
| qual_grid_pjm_2025_ferc_001 | 2025Q4 | 2025-10-23 | day | PJM | interconnection_or_service_queue | interconnection_queue | mixed_or_uncertain | https://insidelines.pjm.com/pjm-discusses-large-load-planning-at-ferc-annual-reliability-technical-conference/ |
| qual_grid_va_scc_2025_high_load_001 | 2025Q4 | 2025-11-25 | day | Virginia SCC | grid_or_regulatory_constraint | contracted_service | mixed_or_uncertain | https://www.scc.virginia.gov/about-the-scc/newsreleases/release/scc-issues-order-on-dev-biennial-review-2025/scc-rules-in-dev-biennial-review-case.html |
| qual_grid_wecc_soti_2025_001 | 2025 | 2025 | year | WECC | observed_or_metered_load | energized_or_metered | positive_acceleration | https://feature.wecc.org/soti2025/soti2025/load/ |
| qual_grid_spp_2025_itp_001 | 2025 | 2025 | year | SPP | demand_future | announced_pipeline | neutral_context | https://spp.org/documents/75192/2025%20integrated%20transmission%20plan%20report.pdf |
| qual_grid_caiso_2026_large_load_001 | 2026Q1 | 2026-01-20 | day | CAISO/CEC | demand_future | announced_pipeline | neutral_context | https://www.caiso.com/documents/issue-paper-large-load-consideration-jan-20-2026.pdf |
| qual_grid_wecc_wara_2026_001 | 2026Q1 | 2026-01-22 | day | WECC | uncertainty_or_pullback | bottleneck_constraint | mixed_or_uncertain | https://feature.wecc.org/2025wara/index.html |
| qual_grid_nerc_ltra_2026_001 | 2026Q1 | 2026-01-29 | day | NERC | grid_or_regulatory_constraint | bottleneck_constraint | mixed_or_uncertain | https://www.nerc.com/globalassets/our-work/assessments/nerc_ltra_2025.pdf |
| qual_grid_pjm_2026_ltf_001 | 2026Q1 | 2026-02-06 | day | PJM | demand_future | announced_pipeline | positive_acceleration | https://www.pjm.com/planning/resource-adequacy-planning/load-forecast-dev-process |
| qual_grid_eia_2026_steo_001 | 2026Q1 | 2026-03-12 | day | EIA | observed_or_metered_load | energized_or_metered | positive_acceleration | https://www.eia.gov/todayinenergy/detail.php?id=67344 |
| qual_grid_miso_2026_ltf_001 | 2026Q2 | 2026-04 | month | MISO | demand_future | announced_pipeline | positive_acceleration | https://www.utilitydive.com/news/miso-long-range-forecast-data-center/817917/ |

## Notes For Judge

- Forecasted large-load/data-center requests are expanding faster than observable metered-load evidence.
- WECC provides both bullish realized-load context and cautionary adequacy/forecast uncertainty.
- EIA national data-center demand should probably be treated as aggregate demand context, not named utility-metered proof.
- State regulators in Georgia and Virginia focus heavily on cost allocation, contract risk, and customer protection.
- Prefer official PDFs/decks over secondary articles where Worker ingestion is possible.
