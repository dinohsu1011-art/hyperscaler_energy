# hyperscaler_energy

Source-tracked database of hyperscaler energy contracts and new-build generation costs.

**Core rule:** every numeric fact row must reference a `source_id` defined in `data/sources.yaml`.
The SQLite schema enforces this with `NOT NULL` + foreign key. A typo in a `source_id` causes `load.py`
to fail — numbers cannot exist in `data.db` without a traceable URL.

## Layout

```
hyperscaler_energy/
├── schema.sql                    # SQLite schema (source_id FK enforced)
├── data.db                       # built by scripts/load.py
├── data/
│   ├── sources.yaml              # one entry per citation; stable IDs (S1, S2, ...)
│   ├── contracts/                # one file per hyperscaler
│   │   ├── microsoft.yaml
│   │   ├── google.yaml
│   │   ├── amazon.yaml
│   │   └── meta.yaml
│   ├── lcoe/lcoe.yaml            # LCOE observations by (technology, vintage, report)
│   ├── capex/
│   │   ├── gas_capex.yaml
│   │   ├── renewable_capex.yaml
│   │   └── turbine_supply.yaml
│   └── demand/demand.yaml        # power demand, grid plans, hyperscaler cumulative totals
├── scripts/
│   ├── load.py                   # YAML → SQLite (rebuilds data.db)
│   ├── validate.py               # integrity checks beyond the schema
│   └── analyze.py                # charts + CSVs under output/
└── output/                       # charts, CSVs, provenance audit
```

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
