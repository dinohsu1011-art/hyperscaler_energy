# hyperscaler_energy

Source-tracked database of hyperscaler energy contracts and new-build generation costs.

**Core rule:** every numeric fact row must reference a `source_id` defined in `data/sources.yaml`.
The SQLite schema enforces this with `NOT NULL` + foreign key. A typo in a `source_id` causes `load.py`
to fail — numbers cannot exist in `data.db` without a traceable URL.

## Layout

```
hyperscaler_energy/
├── schema.sql                    # SQLite schema (source_id FK enforced)
├── data.db                       # built by scripts/load.py (gitignored)
├── data/
│   ├── sources.yaml              # one entry per citation; stable IDs (S1, S2, ...)
│   ├── contracts/                # energy deals by operator group
│   │   ├── microsoft.yaml / google.yaml / amazon.yaml / meta.yaml
│   │   ├── oracle.yaml / xai.yaml
│   │   └── colocation.yaml / neoclouds.yaml / sovereign.yaml
│   ├── operator_disclosures.yaml # operator self-disclosed capacity by stage (term registry + quarterly rows)
│   ├── campuses.yaml             # data-center campus registry
│   ├── campus_evidence.yaml      # energization evidence per campus
│   ├── primary_buildout_signals.yaml      # buildout signals from primary sources
│   ├── proxy_signal_definitions.yaml      # SEC proxy signal definitions
│   ├── sec_proxy_metrics.yaml             # XBRL facts backing market proxies
│   ├── sec_filing_text_signals.yaml       # qualitative snippets from filings
│   ├── qualitative_load_commentary.yaml   # utility/ISO load commentary
│   ├── lcoe/lcoe.yaml            # LCOE observations by (technology, vintage, report)
│   ├── capex/                    # gas_capex / renewable_capex / turbine_supply
│   ├── demand/demand.yaml        # power demand, grid plans, cumulative totals
│   └── external/                 # EIA-860M generator workbooks
├── scripts/
│   ├── load.py                   # YAML → SQLite (rebuilds data.db)
│   ├── validate.py               # integrity checks beyond the schema (exit 2 = warnings-only)
│   ├── dashboard.py              # builds output/dashboard.html (GitHub Pages)
│   ├── analyze.py / report.py    # charts + CSVs under output/
│   ├── capacity_reality_report.py / evidence_coverage.py
│   ├── primary_buildout_signals.py / primary_buildout_report.py
│   └── sec_proxy_signals.py / sec_proxy_report.py / sec_filing_text_signals.py
└── output/                       # dashboard.html (tracked); other artifacts gitignored
```

> **Note:** run the pipeline with the project venv (`.venv/bin/python`) — the system
> `python3` lacks PyYAML. CI (`.github/workflows/build-dashboard.yml`) rebuilds and
> commits `output/dashboard.html` on every push to `main`; warnings (exit 2) do not
> fail the build, errors (exit 1) do.

## Usage

```bash
cd ~/projects/hyperscaler_energy
.venv/bin/python scripts/load.py          # rebuild data.db
.venv/bin/python scripts/validate.py      # check integrity
.venv/bin/python scripts/analyze.py       # write charts + tables to output/
.venv/bin/python scripts/analyze.py provenance --limit 100   # audit every number
```

## Adding new data

1. **New source:** append to `data/sources.yaml` with next ID (`S44`, `S45`, ...). Never renumber.
2. **New fact:** add a row in the appropriate YAML file with `source_id` pointing to step 1.
3. Re-run `load.py` then `validate.py`.

**Estimates:** set `confidence: Estimated` and explain the derivation in `notes`. When the primary
source becomes available, update the existing row — do not add a new one in parallel (the uniqueness
constraints on `(company, year, generation_type, deal_name)` will prevent accidental duplicates,
but you should still edit in place to preserve provenance history).

**Parent-child deals:** when splitting a parent agreement (e.g. Brookfield 10.5 GW → solar + wind
rows), keep both child rows on the same `source_id` and use `validate.py` aggregate checks to
ensure children sum to the parent.

## Confidence tiers

| Tier        | Meaning                                                   |
|-------------|-----------------------------------------------------------|
| `Sourced`   | The exact number is published in the source cited.        |
| `Estimated` | Derived (split, residual, interpolated). `notes` explains.|

Every `Estimated` row is a candidate for replacement as primary data becomes available.

## Update cadence

| Source                  | Typical release | Tables updated                  |
|-------------------------|-----------------|---------------------------------|
| Lazard LCOE+            | June            | `lcoe_data`                     |
| BNEF LCOE               | February        | `lcoe_data`                     |
| NREL ATB                | November        | `renewable_capex`               |
| EIA AEO                 | March           | `lcoe_data`, `gas_capex`        |
| IRENA RPGC              | July            | `lcoe_data`                     |
| GE Vernova earnings     | quarterly       | `turbine_supply`                |
| Hyperscaler deals       | as announced    | `hyperscaler_contracts`         |
| Operator earnings disclosures | quarterly (run Stage E within 2 weeks of each earnings cluster) | `operator_capacity_disclosures` |

**Capacity-by-stage authority:** `operator_capacity_disclosures` is the authoritative table for operator self-reported capacity by stage; `primary_buildout_signals` remains the raw claim ledger and receives no new capacity rows.
