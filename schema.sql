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
  company         TEXT NOT NULL CHECK(company IN ('Microsoft','Google','Amazon','Meta','xAI','Oracle')),
  announced_date  TEXT,
  year            INTEGER NOT NULL,             -- ANNOUNCEMENT year
  cod_year        INTEGER,                      -- Commercial Operation Date (year). NULL if not disclosed / pending / range
  cod_note        TEXT,                         -- e.g. "range 2026–2030", "PSC approval pending", "existing plant, PPA starts 2027"
  generation_type TEXT NOT NULL CHECK(generation_type IN
                    ('Solar','Wind','Nuclear','Gas','Gas+CCS','Fuel Cell','Storage','Geothermal','Hydro',
                     'Solar+Storage','Renewable','Other')),
  capacity_mw     REAL CHECK(capacity_mw IS NULL OR capacity_mw >= 0),
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
       generation_type, capacity_mw, deal_name, counterparty, confidence, source_id
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
       company || ' ' || year || ' ' || generation_type || ' ' || capacity_mw || ' MW' AS fact,
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
UNION ALL SELECT 'turbine_supply', id,
       manufacturer || ' ' || as_of || ': ' || backlog_note, source_id
FROM turbine_supply;
