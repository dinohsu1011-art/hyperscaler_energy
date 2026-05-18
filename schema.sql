-- hyperscaler_energy: source-of-truth schema
-- Every numeric fact row REQUIRES a source_id FK to sources(id).
-- Uniqueness constraints prevent the same fact being counted twice.

PRAGMA foreign_keys = ON;

CREATE TABLE sources (
  id            TEXT PRIMARY KEY,               -- e.g. 'S1', 'S2' (stable keys)
  publisher     TEXT NOT NULL,
  title         TEXT NOT NULL,
  url           TEXT NOT NULL,
  pub_date      TEXT,                           -- YYYY-MM or YYYY-MM-DD
  retrieved_on  TEXT,                           -- YYYY-MM-DD
  kind          TEXT CHECK(kind IN ('Primary','Secondary','Aggregator')),
  supports      TEXT,                           -- free-text: which figures it supports
  notes         TEXT,
  created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE hyperscaler_contracts (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  company         TEXT NOT NULL,                 -- relaxed: any DC operator name (was: 6 hyperscalers only)
  operator_type   TEXT NOT NULL DEFAULT 'Hyperscaler' CHECK(operator_type IN
                    ('Hyperscaler','AI-Cloud','Colocation','Sovereign','Other')),
  announced_date  TEXT,
  year            INTEGER NOT NULL,             -- ANNOUNCEMENT year
  cod_year        INTEGER,                      -- Commercial Operation Date (year). NULL if not disclosed / pending / range
  cod_note        TEXT,                         -- e.g. "range 2026–2030", "PSC approval pending", "existing plant, PPA starts 2027"
  generation_type TEXT NOT NULL CHECK(generation_type IN
                    ('Solar','Wind','Nuclear','Gas','Gas+CCS','Fuel Cell','Storage','Geothermal','Hydro',
                     'Solar+Storage','Renewable','Other')),
  capacity_mw     REAL CHECK(capacity_mw IS NULL OR capacity_mw >= 0),
  storage_power_mw REAL CHECK(storage_power_mw IS NULL OR storage_power_mw >= 0),
  storage_energy_mwh REAL CHECK(storage_energy_mwh IS NULL OR storage_energy_mwh >= 0),
  confidence      TEXT NOT NULL DEFAULT 'Estimated' CHECK(confidence IN ('Sourced','Estimated')),
  deal_name       TEXT NOT NULL,
  counterparty    TEXT,
  contract_years  INTEGER,
  geography       TEXT DEFAULT 'US',
  status          TEXT DEFAULT 'Announced' CHECK(status IN
                    ('Announced','Approved','UnderConstruction','Operational','Cancelled','MOU','PPA','Framework')),
  connection_type   TEXT NOT NULL DEFAULT 'Unknown' CHECK(connection_type IN
                    ('BTM','Grid','Unknown')),
  connection_reason TEXT,
  notes           TEXT,
  source_id       TEXT NOT NULL REFERENCES sources(id),
  created_at      TEXT DEFAULT (datetime('now')),
  UNIQUE(company, year, generation_type, deal_name)
);
CREATE INDEX idx_contracts_op_type ON hyperscaler_contracts(operator_type);

CREATE TABLE lcoe_data (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  technology      TEXT NOT NULL,                -- 'Wind-Onshore', 'Solar-Utility', 'Gas-CC', ...
  year_vintage    INTEGER NOT NULL,
  report_name     TEXT NOT NULL,                -- 'Lazard v18', 'BNEF LCOE 2026', ...
  report_date     TEXT,
  geography       TEXT DEFAULT 'US',
  subsidized      INTEGER NOT NULL DEFAULT 0 CHECK(subsidized IN (0,1)),
  lcoe_low        REAL,
  lcoe_mid        REAL,
  lcoe_high       REAL,
  currency_year   TEXT,
  notes           TEXT,
  source_id       TEXT NOT NULL REFERENCES sources(id),
  created_at      TEXT DEFAULT (datetime('now')),
  -- prevent the same (tech, vintage, report, subsidy flag, geo) being loaded twice
  UNIQUE(technology, year_vintage, report_name, subsidized, geography),
  CHECK (lcoe_low IS NOT NULL OR lcoe_mid IS NOT NULL OR lcoe_high IS NOT NULL)
);

CREATE TABLE gas_capex (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  year          INTEGER NOT NULL,
  plant_type    TEXT NOT NULL CHECK(plant_type IN ('CCGT','CT','Simple Cycle')),
  cost_low_kw   REAL,
  cost_mid_kw   REAL,
  cost_high_kw  REAL,
  data_type     TEXT NOT NULL CHECK(data_type IN ('Actual','Benchmark','Procurement','Forecast')),
  label         TEXT NOT NULL,                  -- short identifier, e.g. 'NextEra-2022-actual'
  notes         TEXT,
  source_id     TEXT NOT NULL REFERENCES sources(id),
  created_at    TEXT DEFAULT (datetime('now')),
  UNIQUE(year, plant_type, label, source_id),
  CHECK (cost_low_kw IS NOT NULL OR cost_mid_kw IS NOT NULL OR cost_high_kw IS NOT NULL)
);

CREATE TABLE renewable_capex (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  technology    TEXT NOT NULL,                  -- 'Solar-Utility','Wind-Onshore','Battery-4hr','Solar+Battery','Nuclear-New','Nuclear-Vogtle'
  year          INTEGER NOT NULL,
  cost_low_kw   REAL,
  cost_mid_kw   REAL,
  cost_high_kw  REAL,
  data_type     TEXT NOT NULL CHECK(data_type IN ('Actual','Benchmark','Forecast','Derived','Estimate')),
  label         TEXT NOT NULL,
  notes         TEXT,
  source_id     TEXT NOT NULL REFERENCES sources(id),
  created_at    TEXT DEFAULT (datetime('now')),
  UNIQUE(technology, year, label, source_id),
  CHECK (cost_low_kw IS NOT NULL OR cost_mid_kw IS NOT NULL OR cost_high_kw IS NOT NULL)
);

CREATE TABLE turbine_supply (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  manufacturer  TEXT NOT NULL,
  as_of         TEXT NOT NULL,                  -- YYYY-MM
  backlog_note  TEXT NOT NULL,
  notes         TEXT,
  source_id     TEXT NOT NULL REFERENCES sources(id),
  UNIQUE(manufacturer, as_of, source_id)
);

CREATE TABLE demand_metrics (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  metric        TEXT NOT NULL,
  value_num     REAL,                           -- numeric value (for quotes, leave null + put in value_text)
  value_text    TEXT,                           -- quotes / non-numeric
  unit          TEXT,                           -- 'GW','TWh/yr', ...
  year          INTEGER,
  geography     TEXT,
  notes         TEXT,
  source_id     TEXT NOT NULL REFERENCES sources(id),
  UNIQUE(metric, year, geography, source_id),
  CHECK (value_num IS NOT NULL OR value_text IS NOT NULL)
);

CREATE TABLE grid_capacity_plan (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  technology    TEXT NOT NULL,
  window_start  INTEGER NOT NULL,               -- start year (e.g. 2026)
  window_end    INTEGER NOT NULL,               -- end year (e.g. 2030)
  gw_planned    REAL NOT NULL,
  geography     TEXT DEFAULT 'US',
  notes         TEXT,
  source_id     TEXT NOT NULL REFERENCES sources(id),
  UNIQUE(technology, window_start, window_end, geography, source_id)
);

CREATE TABLE hyperscaler_cumulative (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  company       TEXT NOT NULL,
  as_of         TEXT NOT NULL,                  -- YYYY-MM
  metric        TEXT NOT NULL,                  -- 'Clean-Total-GW','Nuclear-GW', ...
  value_gw      REAL NOT NULL,
  notes         TEXT,
  source_id     TEXT NOT NULL REFERENCES sources(id),
  UNIQUE(company, as_of, metric, source_id)
);

-- Data-center campus pipeline. Captures the IT-load (compute side) view that complements
-- the hyperscaler_contracts table (energy side). Same physical buildout viewed from a
-- different angle — and the place where "announced vs actually-energized" gets answered.
--
-- Convention: capacity_definition='Critical-IT' is the primary unit. Hyperscalers usually
-- disclose this. For sites that only disclose facility power, store as 'Facility-power' and
-- note the convention. PUE conversion stays in the dashboard layer, not here.
CREATE TABLE data_center_campuses (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  campus_id             TEXT NOT NULL UNIQUE,        -- stable key for cross-references, e.g. 'C001'
  campus_name           TEXT NOT NULL,
  hyperscaler           TEXT NOT NULL CHECK(hyperscaler IN
                          ('Microsoft','Google','Amazon','Meta','xAI','Oracle',
                           'OpenAI','Anthropic','CoreWeave','Nebius','Multi')),
  primary_tenant        TEXT,                        -- e.g. 'OpenAI' for Stargate Abilene; can differ from operator
  city                  TEXT,
  state_or_region       TEXT,
  country               TEXT DEFAULT 'US',
  lat                   REAL,
  lon                   REAL,
  capacity_definition   TEXT NOT NULL DEFAULT 'Critical-IT' CHECK(capacity_definition IN
                          ('Critical-IT','IT-load','Facility-power','Mixed')),
  it_load_mw_planned    REAL,                        -- target at full build
  it_load_mw_phase1     REAL,                        -- first phase at energization
  it_load_mw_energized  REAL DEFAULT 0,              -- as of latest observation
  cod_phase1_year       INTEGER,                     -- year first phase reaches initial energization
  cod_full_year         INTEGER,                     -- year full planned capacity reached
  status                TEXT NOT NULL DEFAULT 'Announced' CHECK(status IN
                          ('Announced','SiteWork','UnderConstruction','PartiallyEnergized',
                           'Operational','Cancelled','Paused')),
  power_source_summary  TEXT,                        -- free-text: 'PJM grid + 366 MW BTM gas (S83)'
  primary_use           TEXT,                        -- 'AI-training','AI-inference','General-cloud','Mixed'
  notes                 TEXT,
  source_id             TEXT NOT NULL REFERENCES sources(id),
  created_at            TEXT DEFAULT (datetime('now')),
  UNIQUE(campus_name, hyperscaler)
);

-- Source-backed evidence records for campus-level capacity claims.
-- This table is intentionally narrower than data_center_campuses: it captures
-- why a campus status/MW claim is believed, without overwriting the current
-- rollup fields until the evidence has been reviewed.
CREATE TABLE campus_evidence (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  evidence_id           TEXT NOT NULL UNIQUE,         -- stable key, e.g. 'CE001'
  campus_id             TEXT NOT NULL REFERENCES data_center_campuses(campus_id),
  evidence_date         TEXT NOT NULL,                -- YYYY-MM-DD or YYYY-MM when only month is public
  evidence_type         TEXT NOT NULL CHECK(evidence_type IN
                          ('utility_service_agreement','commission_order','local_site_plan',
                           'building_permit','electrical_permit','certificate_of_occupancy',
                           'air_permit_backup_generation','onsite_generation_permit',
                           'substation_transmission','eia_generator_crosscheck',
                           'satellite_construction','corporate_activation','water_wastewater',
                           'tax_abatement','media_report','other')),
  independence_group    TEXT NOT NULL CHECK(independence_group IN
                          ('operator','utility','regulator','local_government',
                           'environmental_agency','grid_operator','federal_dataset',
                           'satellite','financial_filing','media','paid_dataset','other')),
  claim_status          TEXT NOT NULL CHECK(claim_status IN
                          ('announced','approved','site_work','under_construction',
                           'energized_partial','energized_full','paused','cancelled',
                           'not_publicly_knowable','unknown')),
  capacity_definition   TEXT NOT NULL DEFAULT 'Unknown' CHECK(capacity_definition IN
                          ('Critical-IT','IT-load','Facility-power','Mixed',
                           'Contracted-load','Generation-nameplate','Backup-generation',
                           'Substation-capacity','Building-count','Unknown')),
  capacity_mw           REAL CHECK(capacity_mw IS NULL OR capacity_mw >= 0),
  phase                 TEXT,                         -- optional phase/building/substation label
  collectability        TEXT NOT NULL DEFAULT 'Manual' CHECK(collectability IN
                          ('Programmatic','Portal','Manual','FOIA','Paid','Unavailable')),
  evidence_strength     TEXT NOT NULL CHECK(evidence_strength IN ('High','Medium','Low')),
  quote                 TEXT,
  notes                 TEXT,
  source_id             TEXT NOT NULL REFERENCES sources(id),
  created_at            TEXT DEFAULT (datetime('now')),
  CHECK (capacity_mw IS NOT NULL OR quote IS NOT NULL OR notes IS NOT NULL)
);
CREATE INDEX idx_ce_campus ON campus_evidence(campus_id);
CREATE INDEX idx_ce_type ON campus_evidence(evidence_type);
CREATE INDEX idx_ce_group ON campus_evidence(independence_group);
CREATE INDEX idx_ce_status ON campus_evidence(claim_status);

-- Market-proxy signal definitions. These describe what a proxy can and cannot
-- prove so accounting/hardware/utilization signals do not get confused with
-- direct campus energization evidence.
CREATE TABLE proxy_signal_definitions (
  signal_id              TEXT PRIMARY KEY,
  signal_name            TEXT NOT NULL,
  signal_group           TEXT NOT NULL CHECK(signal_group IN
                            ('hyperscaler_accounting','server_supply_chain',
                             'chip_vendor_demand','gpu_cloud_marketplace',
                             'networking_inference','foundry_memory_supply',
                             'rumor_overlay','other')),
  metric_keys            TEXT NOT NULL,          -- comma-separated metric keys used by collectors
  source_route           TEXT NOT NULL,
  update_frequency       TEXT NOT NULL,
  confidence             TEXT NOT NULL CHECK(confidence IN ('High','Medium','Low')),
  validates              TEXT NOT NULL,
  cannot_validate        TEXT NOT NULL,
  notes                  TEXT,
  created_at             TEXT DEFAULT (datetime('now'))
);

-- Official SEC/XBRL market-proxy facts. Values are raw facts from companyfacts,
-- not analyst-derived estimates. Duration and instant facts are kept apart, and
-- metric_key + xbrl_tag stay explicit to avoid mixing accounting meanings.
CREATE TABLE sec_proxy_metrics (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker                TEXT NOT NULL,
  cik                   INTEGER NOT NULL,
  company_name          TEXT NOT NULL,
  company_group         TEXT NOT NULL CHECK(company_group IN
                          ('hyperscaler','server_vendor','chip_vendor',
                           'networking_vendor','memory_vendor','neocloud','other')),
  metric_key            TEXT NOT NULL,
  metric_label          TEXT NOT NULL,
  taxonomy              TEXT NOT NULL,
  xbrl_tag              TEXT NOT NULL,
  unit                  TEXT NOT NULL,
  period_type           TEXT NOT NULL CHECK(period_type IN ('instant','quarter','annual')),
  period_year           INTEGER NOT NULL,        -- year of the fact end_date; safer for comparative facts
  sec_fiscal_year       INTEGER,                 -- fy value reported by SEC companyfacts
  fiscal_period         TEXT NOT NULL,
  form                  TEXT NOT NULL,
  filed_date            TEXT,
  start_date            TEXT,
  end_date              TEXT NOT NULL,
  frame                 TEXT,
  accession             TEXT NOT NULL,
  value                 REAL NOT NULL,
  source_url            TEXT NOT NULL,
  source_id             TEXT NOT NULL REFERENCES sources(id),
  retrieved_on          TEXT NOT NULL,
  created_at            TEXT DEFAULT (datetime('now')),
  UNIQUE(ticker, metric_key, xbrl_tag, unit, period_type, period_year,
         fiscal_period, form, accession, end_date, value)
);
CREATE INDEX idx_spm_ticker ON sec_proxy_metrics(ticker);
CREATE INDEX idx_spm_group ON sec_proxy_metrics(company_group);
CREATE INDEX idx_spm_metric ON sec_proxy_metrics(metric_key);
CREATE INDEX idx_spm_period ON sec_proxy_metrics(period_year, fiscal_period);

-- Short official SEC filing-text snippets for non-XBRL market proxy signals:
-- backlog, customer concentration, AI infrastructure commentary, capex guidance,
-- purchase commitments, supply constraints, and revenue-mix language.
CREATE TABLE sec_filing_text_signals (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker                TEXT NOT NULL,
  cik                   INTEGER NOT NULL,
  company_name          TEXT NOT NULL,
  company_group         TEXT NOT NULL CHECK(company_group IN
                          ('hyperscaler','server_vendor','chip_vendor',
                           'networking_vendor','memory_vendor','neocloud','other')),
  form                  TEXT NOT NULL,
  filed_date            TEXT NOT NULL,
  report_date           TEXT,
  accession             TEXT NOT NULL,
  document_url          TEXT NOT NULL,
  signal_type           TEXT NOT NULL CHECK(signal_type IN
                          ('ai_infrastructure','backlog_rpo','customer_concentration',
                           'capex_guidance','purchase_commitments','inventory_supply',
                           'revenue_mix')),
  matched_term          TEXT NOT NULL,
  snippet               TEXT NOT NULL,
  source_id             TEXT NOT NULL REFERENCES sources(id),
  retrieved_on          TEXT NOT NULL,
  created_at            TEXT DEFAULT (datetime('now')),
  UNIQUE(ticker, accession, signal_type, matched_term, snippet)
);
CREATE INDEX idx_sfts_ticker ON sec_filing_text_signals(ticker);
CREATE INDEX idx_sfts_signal ON sec_filing_text_signals(signal_type);
CREATE INDEX idx_sfts_filed ON sec_filing_text_signals(filed_date);

-- Primary-source company/operator buildout disclosures. These rows capture
-- management or company-published claims about capacity delivery, power status,
-- customer commitments, chip counts, and ramp timing. They intentionally do not
-- overwrite campus-level energized MW; instead they provide an operator/company
-- evidence layer that can be reconciled against campus_evidence and SEC proxies.
CREATE TABLE primary_buildout_signals (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id             TEXT NOT NULL UNIQUE,
  campus_id             TEXT REFERENCES data_center_campuses(campus_id),
  reporting_company     TEXT NOT NULL,
  company_bucket        TEXT NOT NULL CHECK(company_bucket IN
                          ('hyperscaler_ai_lab','neocloud_operator',
                           'colocation_operator','supply_chain')),
  ticker                TEXT,
  counterparty          TEXT,
  project_name          TEXT,
  geography             TEXT,
  claim_type            TEXT NOT NULL CHECK(claim_type IN
                          ('current_active_capacity','delivered_capacity',
                           'under_construction_capacity','announced_future_capacity',
                           'secured_capacity','connected_capacity','contracted_capacity',
                           'billable_capacity','rpo_or_revenue_backlog',
                           'contract_value','gpu_or_chip_count','lease_term',
                           'ramp_or_rfs_date','utilization_or_sold_out',
                           'buildout_constraints','site_count')),
  status_stage          TEXT NOT NULL CHECK(status_stage IN
                          ('active','delivered','ready_for_service','billable',
                           'connected','contracted','secured','under_construction',
                           'announced','planned','future_target','backlog',
                           'constraint','unknown')),
  capacity_basis        TEXT NOT NULL DEFAULT 'Unknown' CHECK(capacity_basis IN
                          ('Active-power','Connected-power','Contracted-power',
                           'Critical-IT','Gross-power','Utility-capacity',
                           'Billable-critical-IT','Energized-power',
                           'Data-center-count','Chip-count','Revenue-backlog',
                           'Contract-value','Lease-term','Date','Not-capacity',
                           'Unknown')),
  metric_value          REAL CHECK(metric_value IS NULL OR metric_value >= 0),
  metric_unit           TEXT CHECK(metric_unit IS NULL OR metric_unit IN
                          ('MW','GW','USD_b','chips','GPUs','sites','data_halls',
                           'years','date','cluster','text')),
  as_of_date            TEXT,
  expected_online_date  TEXT,
  source_quote          TEXT,
  notes                 TEXT,
  source_id             TEXT NOT NULL REFERENCES sources(id),
  created_at            TEXT DEFAULT (datetime('now')),
  CHECK (metric_value IS NOT NULL OR source_quote IS NOT NULL OR notes IS NOT NULL)
);
CREATE INDEX idx_pbs_company ON primary_buildout_signals(reporting_company);
CREATE INDEX idx_pbs_bucket ON primary_buildout_signals(company_bucket);
CREATE INDEX idx_pbs_claim ON primary_buildout_signals(claim_type);
CREATE INDEX idx_pbs_status ON primary_buildout_signals(status_stage);

-- Qualitative narrative anchors for management, utility, grid-operator,
-- regulator, and expert commentary. These rows timestamp public statements
-- about demand, contracting, construction pace, and bottlenecks. They are
-- intentionally separate from campus_evidence and primary_buildout_signals:
-- a statement can frame reality, but it does not update energized MW.
CREATE TABLE qualitative_load_commentary (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  statement_id          TEXT NOT NULL UNIQUE,
  source_id             TEXT NOT NULL REFERENCES sources(id),
  statement_date        TEXT NOT NULL,          -- format depends on date_precision
  date_precision        TEXT NOT NULL CHECK(date_precision IN
                          ('day','month','quarter','year','inferred')),
  event_name            TEXT NOT NULL,
  timeline_bucket       TEXT NOT NULL,          -- deterministic bucket derived by loader
  speaker_name          TEXT NOT NULL,
  speaker_title         TEXT,
  organization          TEXT NOT NULL,
  organization_bucket   TEXT NOT NULL CHECK(organization_bucket IN
                          ('hyperscaler_ai_lab','utility_ipp_generation_owner',
                           'neocloud_colocation_contractor',
                           'grid_regulator_planning_body',
                           'supply_chain_infrastructure_expert','other')),
  source_route          TEXT NOT NULL CHECK(source_route IN
                          ('official_company','earnings_call','investor_presentation',
                           'filing','regulator','grid_operator','federal_dataset',
                           'institutional_research','local_government','other')),
  source_type           TEXT NOT NULL CHECK(source_type IN
                          ('earnings_call','earnings_release','shareholder_letter',
                           'official_blog','press_release','investor_presentation',
                           'regulatory_order','load_forecast','technical_report',
                           'sec_filing','institutional_report','other')),
  statement_taxonomy    TEXT NOT NULL CHECK(statement_taxonomy IN
                          ('demand_future','contracted_or_committed_demand',
                           'interconnection_or_service_queue','observed_or_metered_load',
                           'construction_delivery_pace','energization_or_ready_for_service',
                           'capacity_utilization_or_sold_out','capex_or_power_procurement',
                           'supply_chain_bottleneck','grid_or_regulatory_constraint',
                           'negative_not_material_yet','uncertainty_or_pullback')),
  polarity              TEXT NOT NULL CHECK(polarity IN
                          ('positive_acceleration','neutral_context','negative_delay',
                           'negative_not_observed','mixed_or_uncertain')),
  load_stage            TEXT NOT NULL CHECK(load_stage IN
                          ('narrative_demand','announced_pipeline','interconnection_queue',
                           'contracted_service','under_construction','ready_for_service',
                           'energized_or_metered','bottleneck_constraint')),
  geography             TEXT,
  related_company       TEXT,
  short_quote           TEXT,
  paraphrase            TEXT NOT NULL,
  numeric_value         REAL CHECK(numeric_value IS NULL OR numeric_value >= 0),
  numeric_unit          TEXT,
  capacity_basis        TEXT NOT NULL CHECK(capacity_basis IN
                          ('announced','contracted','interconnection','ready_for_service',
                           'live_cluster','metered_load','aggregate_demand_context',
                           'backlog','capex','supplier_proxy','forecast',
                           'regulatory_constraint','generation_supply',
                           'construction_status','not_capacity_evidence')),
  time_horizon_start    TEXT,
  time_horizon_end      TEXT,
  confidence            TEXT NOT NULL CHECK(confidence IN ('High','Medium','Low')),
  independence_group    TEXT NOT NULL CHECK(independence_group IN
                          ('operator','utility','regulator','grid_operator',
                           'federal_dataset','financial_filing','supply_chain',
                           'industry_expert','institutional_expert',
                           'local_government','other')),
  notes                 TEXT,
  created_at            TEXT DEFAULT (datetime('now')),
  CHECK (numeric_value IS NULL OR numeric_unit IS NOT NULL)
);
CREATE INDEX idx_qlc_bucket ON qualitative_load_commentary(timeline_bucket);
CREATE INDEX idx_qlc_org ON qualitative_load_commentary(organization);
CREATE INDEX idx_qlc_taxonomy ON qualitative_load_commentary(statement_taxonomy);
CREATE INDEX idx_qlc_load_stage ON qualitative_load_commentary(load_stage);

-- Federal cross-check: EIA Form 860M planned generators (the supply side).
-- Loaded from data/external/eia860m_table_6_05.xlsx as a single snapshot.
-- Used to verify whether announced PPAs have a matching plant under construction.
CREATE TABLE planned_generators (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  vintage               TEXT NOT NULL,               -- snapshot month, e.g. '2026-03'
  planned_year          INTEGER NOT NULL,
  planned_month         INTEGER,
  entity_id             INTEGER,
  entity_name           TEXT NOT NULL,
  producer_type         TEXT,                        -- 'IPP Non-CHP', 'Electric Utility', etc.
  plant_name            TEXT NOT NULL,
  plant_state           TEXT,
  county                TEXT,
  balancing_authority   TEXT,                        -- e.g. 'PJM', 'ERCO', 'CISO', 'MISO'
  lat                   REAL,
  lon                   REAL,
  plant_id              INTEGER,
  generator_id          TEXT,
  net_summer_capacity_mw REAL,
  nameplate_capacity_mw  REAL,
  technology            TEXT,                        -- 'Solar Photovoltaic', 'Batteries', 'Natural Gas Fired Combined Cycle', etc.
  energy_source_code    TEXT,
  prime_mover_code      TEXT,
  status                TEXT NOT NULL,               -- raw EIA status string
  status_tier           TEXT NOT NULL CHECK(status_tier IN
                          ('ConstructionComplete','MajorityComplete','MinorityComplete',
                           'ApprovalsReceived','ApprovalsPending','PlannedOnly','Other')),
  delivery_probability  REAL NOT NULL,               -- 0.0–1.0 derived from status_tier
  source_id             TEXT NOT NULL REFERENCES sources(id),
  created_at            TEXT DEFAULT (datetime('now')),
  UNIQUE(vintage, plant_id, generator_id)            -- one row per generator per snapshot
);
CREATE INDEX idx_pg_vintage ON planned_generators(vintage);
CREATE INDEX idx_pg_year ON planned_generators(planned_year);
CREATE INDEX idx_pg_state ON planned_generators(plant_state);
CREATE INDEX idx_pg_tech ON planned_generators(technology);
CREATE INDEX idx_pg_entity ON planned_generators(entity_name);
CREATE INDEX idx_pg_genkey ON planned_generators(plant_id, generator_id);

-- Operating generators (every US plant currently in commercial operation).
-- Loaded once from the latest EIA-860M vintage. The Operating Year/Month is
-- the directly-reported commercial-operation date — used to verify our
-- "operated" inference against ground truth.
CREATE TABLE operating_generators (
  id                       INTEGER PRIMARY KEY AUTOINCREMENT,
  plant_id                 INTEGER NOT NULL,
  generator_id             TEXT NOT NULL,
  entity_name              TEXT,
  plant_name               TEXT NOT NULL,
  plant_state              TEXT,
  county                   TEXT,
  balancing_authority      TEXT,
  sector                   TEXT,
  technology               TEXT,
  energy_source_code       TEXT,
  net_summer_capacity_mw   REAL,
  nameplate_capacity_mw    REAL,
  operating_year           INTEGER,
  operating_month          INTEGER,
  status                   TEXT,
  source_id                TEXT NOT NULL REFERENCES sources(id),
  UNIQUE(plant_id, generator_id)
);
CREATE INDEX idx_op_year ON operating_generators(operating_year, operating_month);
CREATE INDEX idx_op_state ON operating_generators(plant_state);
CREATE INDEX idx_op_tech ON operating_generators(technology);

-- Time-series view: status distribution by vintage (the funnel evolution chart)
CREATE VIEW v_eia_status_by_vintage AS
SELECT vintage,
       status_tier,
       COUNT(*) AS gen_count,
       ROUND(SUM(nameplate_capacity_mw), 0) AS total_mw
FROM planned_generators
GROUP BY vintage, status_tier
ORDER BY vintage, status_tier;

-- Tech distribution by vintage (separate cut)
CREATE VIEW v_eia_tech_by_vintage AS
SELECT vintage,
       CASE
         WHEN technology LIKE '%Solar%'    THEN 'Solar'
         WHEN technology LIKE '%Wind%'     THEN 'Wind'
         WHEN technology = 'Batteries' OR technology LIKE '%Storage%' THEN 'Storage'
         WHEN technology LIKE '%Nuclear%'  THEN 'Nuclear'
         WHEN technology LIKE '%Natural Gas%' THEN 'Gas'
         WHEN technology LIKE '%Geothermal%' THEN 'Geothermal'
         WHEN technology LIKE '%Hydro%'    THEN 'Hydro'
         ELSE 'Other'
       END AS tech_group,
       COUNT(*) AS gen_count,
       ROUND(SUM(nameplate_capacity_mw), 0) AS total_mw
FROM planned_generators
GROUP BY vintage, tech_group;

-- Aggregate view: federal pipeline by year × tier (the haircut ladder)
CREATE VIEW v_eia_pipeline_by_tier AS
SELECT planned_year,
       status_tier,
       COUNT(*)                                  AS gen_count,
       ROUND(SUM(nameplate_capacity_mw), 0)     AS announced_mw,
       ROUND(SUM(nameplate_capacity_mw * delivery_probability), 0) AS expected_mw
FROM planned_generators
GROUP BY planned_year, status_tier
ORDER BY planned_year, status_tier;

-- Aggregate view: latest-vintage federal pipeline by year × technology
CREATE VIEW v_eia_pipeline_by_tech AS
SELECT planned_year,
       CASE
         WHEN technology LIKE '%Solar%' THEN 'Solar'
         WHEN technology LIKE '%Wind%'  THEN 'Wind'
         WHEN technology = 'Batteries' OR technology LIKE '%Storage%' THEN 'Storage'
         WHEN technology LIKE '%Nuclear%' THEN 'Nuclear'
         WHEN technology LIKE '%Natural Gas%' THEN 'Gas'
         WHEN technology LIKE '%Geothermal%' THEN 'Geothermal'
         WHEN technology LIKE '%Hydro%' THEN 'Hydro'
         ELSE 'Other'
       END AS tech_group,
       COUNT(*)                                  AS gen_count,
       ROUND(SUM(nameplate_capacity_mw), 0)     AS announced_mw,
       ROUND(SUM(nameplate_capacity_mw * delivery_probability), 0) AS expected_mw
FROM planned_generators
WHERE vintage = (SELECT MAX(vintage) FROM planned_generators)
GROUP BY planned_year, tech_group
ORDER BY planned_year, tech_group;

-- Aggregate view: hyperscaler-level IT-load pipeline, current vs full
CREATE VIEW v_campus_pipeline_by_hyperscaler AS
SELECT hyperscaler,
       COUNT(*) AS campus_count,
       ROUND(SUM(it_load_mw_energized), 0) AS energized_mw,
       ROUND(SUM(it_load_mw_phase1), 0) AS phase1_mw,
       ROUND(SUM(it_load_mw_planned), 0) AS planned_mw
FROM data_center_campuses
GROUP BY hyperscaler
ORDER BY planned_mw DESC NULLS LAST;

-- Views

-- View by ANNOUNCEMENT year (who committed, when)
CREATE VIEW v_energy_mix_by_company_year AS
SELECT company, year AS announce_year, generation_type,
       ROUND(SUM(capacity_mw), 1) AS total_mw,
       COUNT(*) AS deal_count
FROM hyperscaler_contracts
GROUP BY company, year, generation_type;

CREATE VIEW v_gas_vs_clean_by_year AS
SELECT year AS announce_year,
       ROUND(SUM(CASE WHEN generation_type IN ('Gas','Gas+CCS') THEN capacity_mw ELSE 0 END), 0) AS gas_mw,
       ROUND(SUM(CASE WHEN generation_type NOT IN ('Gas','Gas+CCS') THEN capacity_mw ELSE 0 END), 0) AS clean_mw,
       ROUND(100.0 * SUM(CASE WHEN generation_type IN ('Gas','Gas+CCS') THEN capacity_mw ELSE 0 END)
             / NULLIF(SUM(capacity_mw), 0), 1) AS gas_pct
FROM hyperscaler_contracts
GROUP BY year;

-- View by COMMERCIAL OPERATION year (when electrons actually flow)
CREATE VIEW v_operational_by_cod_year AS
SELECT cod_year,
       ROUND(SUM(CASE WHEN generation_type IN ('Gas','Gas+CCS') THEN capacity_mw ELSE 0 END), 0) AS gas_mw,
       ROUND(SUM(CASE WHEN generation_type NOT IN ('Gas','Gas+CCS') THEN capacity_mw ELSE 0 END), 0) AS clean_mw,
       COUNT(*) AS deal_count
FROM hyperscaler_contracts
WHERE cod_year IS NOT NULL
GROUP BY cod_year
ORDER BY cod_year;

-- Forward pipeline: announced but not yet operational (relative to a passed-in year via ? param in code)
CREATE VIEW v_forward_pipeline AS
SELECT company, year AS announce_year, cod_year, cod_note, status,
       generation_type, capacity_mw, storage_power_mw, storage_energy_mwh,
       deal_name, counterparty, confidence, source_id
FROM hyperscaler_contracts
WHERE status <> 'Operational';

CREATE VIEW v_lcoe_lazard_trend AS
SELECT technology,
       MAX(CASE WHEN year_vintage=2024 THEN lcoe_mid END) AS mid_2024,
       MAX(CASE WHEN year_vintage=2025 THEN lcoe_mid END) AS mid_2025
FROM lcoe_data
WHERE geography='US' AND subsidized=0 AND report_name LIKE 'Lazard%'
GROUP BY technology;

-- Audit view: every numeric fact with its source URL (proves provenance end-to-end)
CREATE VIEW v_fact_provenance AS
SELECT 'hyperscaler_contracts' AS tbl, id AS row_id,
       company || ' ' || year || ' ' || generation_type || ' ' || capacity_mw || ' MW' ||
       CASE WHEN storage_energy_mwh IS NOT NULL THEN ' + ' || storage_energy_mwh || ' MWh storage' ELSE '' END AS fact,
       source_id
FROM hyperscaler_contracts
UNION ALL SELECT 'lcoe_data', id,
       technology || ' ' || year_vintage || ' ' || report_name ||
       ' [' || COALESCE(lcoe_low,'-') || '/' || COALESCE(lcoe_mid,'-') || '/' || COALESCE(lcoe_high,'-') || ']', source_id
FROM lcoe_data
UNION ALL SELECT 'gas_capex', id,
       plant_type || ' ' || year || ' $' || COALESCE(cost_mid_kw, cost_low_kw, cost_high_kw) || '/kW (' || label || ')', source_id
FROM gas_capex
UNION ALL SELECT 'renewable_capex', id,
       technology || ' ' || year || ' $' || COALESCE(cost_mid_kw, cost_low_kw, cost_high_kw) || '/kW (' || label || ')', source_id
FROM renewable_capex
UNION ALL SELECT 'demand_metrics', id,
       metric || ' ' || COALESCE(value_num || ' ' || COALESCE(unit,''), value_text), source_id
FROM demand_metrics
UNION ALL SELECT 'grid_capacity_plan', id,
       technology || ' ' || window_start || '-' || window_end || ' ' || gw_planned || ' GW', source_id
FROM grid_capacity_plan
UNION ALL SELECT 'hyperscaler_cumulative', id,
       company || ' ' || metric || ' ' || value_gw || ' GW as of ' || as_of, source_id
FROM hyperscaler_cumulative
UNION ALL SELECT 'data_center_campuses', id,
       hyperscaler || ' ' || campus_name || ' ' ||
       COALESCE(it_load_mw_planned, it_load_mw_phase1, it_load_mw_energized, 0) ||
       ' MW (' || capacity_definition || ', ' || status || ')', source_id
FROM data_center_campuses
UNION ALL SELECT 'campus_evidence', id,
       campus_id || ' ' || evidence_type || ' ' ||
       COALESCE(capacity_mw || ' MW ', '') ||
       '(' || capacity_definition || ', ' || claim_status || ', ' || evidence_strength || ')',
       source_id
FROM campus_evidence
UNION ALL SELECT 'sec_proxy_metrics', id,
       ticker || ' ' || metric_key || ' ' || period_year || ' ' ||
       COALESCE(fiscal_period, '') || ' ' || ROUND(value, 0) || ' ' || unit ||
       ' (' || xbrl_tag || ', ' || period_type || ')',
       source_id
FROM sec_proxy_metrics
UNION ALL SELECT 'sec_filing_text_signals', id,
       ticker || ' ' || form || ' ' || filed_date || ' ' ||
       signal_type || ' (' || matched_term || ')',
       source_id
FROM sec_filing_text_signals
UNION ALL SELECT 'primary_buildout_signals', id,
       COALESCE(campus_id || ' ', '') || reporting_company || ' ' || claim_type || ' ' ||
       COALESCE(metric_value || ' ' || COALESCE(metric_unit, ''), COALESCE(source_quote, '')) ||
       ' (' || capacity_basis || ', ' || status_stage || ')',
       source_id
FROM primary_buildout_signals
UNION ALL SELECT 'qualitative_load_commentary', id,
       organization || ' ' || event_name || ' ' || statement_taxonomy ||
       ' (' || timeline_bucket || ', ' || load_stage || ', ' || capacity_basis || ')',
       source_id
FROM qualitative_load_commentary
UNION ALL SELECT 'planned_generators', id,
       entity_name || ' ' || plant_name || ' ' ||
       ROUND(COALESCE(net_summer_capacity_mw, 0), 0) || ' MW ' ||
       technology || ' (' || status_tier || ', ' || planned_year || ')', source_id
FROM planned_generators
UNION ALL SELECT 'turbine_supply', id,
       manufacturer || ' ' || as_of || ': ' || backlog_note, source_id
FROM turbine_supply;
