"""Load YAML data → SQLite. Rebuilds data.db from scratch each run.

Every numeric fact row MUST have a source_id and that source_id MUST exist in sources.yaml.
The SQLite FK on source_id enforces this at load time (so a typo = failure, not silent).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB = ROOT / "data.db"
SCHEMA = ROOT / "schema.sql"


def read_yaml(p: Path):
    with p.open() as f:
        return yaml.safe_load(f)


def rebuild_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("PRAGMA foreign_keys = OFF;")
    # drop all tables + views
    cur = conn.execute(
        "SELECT type, name FROM sqlite_master "
        "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
    )
    for t, n in cur.fetchall():
        conn.execute(f"DROP {t} IF EXISTS {n}")
    conn.commit()
    conn.executescript(SCHEMA.read_text())
    conn.execute("PRAGMA foreign_keys = ON;")


def load_sources(conn: sqlite3.Connection) -> set[str]:
    rows = read_yaml(DATA / "sources.yaml")
    for r in rows:
        conn.execute(
            """INSERT INTO sources
               (id, publisher, title, url, pub_date, retrieved_on, kind, supports, notes)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (r["id"], r["publisher"], r["title"], r["url"],
             r.get("pub_date"), r.get("retrieved_on"),
             r.get("kind"), r.get("supports"), r.get("notes")),
        )
    conn.commit()
    return {r["id"] for r in rows}


def load_contracts(conn: sqlite3.Connection) -> int:
    n = 0
    for p in sorted((DATA / "contracts").glob("*.yaml")):
        doc = read_yaml(p)
        company = doc["company"]
        for r in doc["rows"]:
            conn.execute(
                """INSERT INTO hyperscaler_contracts
                   (company, announced_date, year, cod_year, cod_note,
                    generation_type, capacity_mw, confidence, deal_name,
                    counterparty, contract_years, geography, status,
                    connection_type, connection_reason, notes, source_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (company, r.get("announced_date"), r["year"],
                 r.get("cod_year"), r.get("cod_note"),
                 r["generation_type"], r["capacity_mw"],
                 r.get("confidence", "Estimated"), r["deal_name"],
                 r.get("counterparty"), r.get("contract_years"),
                 r.get("geography", "US"), r.get("status", "Announced"),
                 r.get("connection_type", "Unknown"),
                 r.get("connection_reason"),
                 r.get("notes"), r["source_id"]),
            )
            n += 1
    conn.commit()
    return n


def load_planned_generators(conn: sqlite3.Connection) -> int:
    """Load EIA-860M Table 6.05 (planned generators) as a federal supply-side cross-check.

    Status code → tier → delivery probability ladder. Probabilities are calibrated
    against historical hit rates (LBNL Queued Up reports historical interconnection
    completion rates of ~14% in PJM, ~50% in MISO/SPP; EIA-tracked plants already
    self-selected past the queue stage so probabilities here are higher).
    """
    try:
        import openpyxl
    except ImportError:
        print("[skip] openpyxl not installed; skipping planned_generators load")
        return 0

    p = ROOT / "data" / "external" / "eia860m_table_6_05.xlsx"
    if not p.exists():
        print(f"[skip] {p} not found; skipping planned_generators load")
        return 0

    EIA_TIERS = {
        '(TS)': ('ConstructionComplete', 0.95),
        '(V)':  ('MajorityComplete',     0.85),
        '(U)':  ('MinorityComplete',     0.70),
        '(T)':  ('ApprovalsReceived',    0.50),
        '(L)':  ('ApprovalsPending',     0.30),
        '(P)':  ('PlannedOnly',          0.15),
        '(OT)': ('Other',                0.20),
    }
    SOURCE_ID = 'S291'  # EIA-860M April 2026 snapshot
    VINTAGE = '2026-04'

    wb = openpyxl.load_workbook(p, data_only=True, read_only=True)
    ws = wb['Table_6_05']
    n = 0
    for row in ws.iter_rows(min_row=3, values_only=True):
        # Skip footer/note rows that lack a plant name or capacity
        if not row[5] or row[9] is None:
            continue
        year, month, ent_id, ent_name, prod_type, plant_name, state, plant_id, gen_id, \
            net_mw, tech, fuel, prime_mover, status, name_mw = row[:15]
        if not status:
            continue
        # Extract status code prefix like '(V)' or '(TS)'
        prefix = status[:status.index(')')+1] if ')' in status else '(OT)'
        tier, prob = EIA_TIERS.get(prefix, ('Other', 0.20))
        conn.execute(
            """INSERT INTO planned_generators
               (vintage, planned_year, planned_month, entity_id, entity_name,
                producer_type, plant_name, plant_state, plant_id, generator_id,
                net_summer_capacity_mw, nameplate_capacity_mw, technology,
                energy_source_code, prime_mover_code, status, status_tier,
                delivery_probability, source_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (VINTAGE, int(year), int(month) if month else None,
             int(ent_id) if ent_id else None,
             (ent_name or '').strip(), prod_type,
             (plant_name or '').strip(), state,
             int(plant_id) if plant_id else None, str(gen_id) if gen_id else None,
             float(net_mw) if net_mw is not None else None,
             float(name_mw) if name_mw is not None else None,
             tech, fuel, prime_mover, status, tier, prob, SOURCE_ID),
        )
        n += 1
    conn.commit()
    return n


def load_campuses(conn: sqlite3.Connection) -> int:
    p = DATA / "campuses.yaml"
    if not p.exists():
        return 0
    doc = read_yaml(p)
    n = 0
    for r in doc["rows"]:
        conn.execute(
            """INSERT INTO data_center_campuses
               (campus_id, campus_name, hyperscaler, primary_tenant,
                city, state_or_region, country, lat, lon,
                capacity_definition, it_load_mw_planned, it_load_mw_phase1,
                it_load_mw_energized, cod_phase1_year, cod_full_year,
                status, power_source_summary, primary_use, notes, source_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["campus_id"], r["campus_name"], r["hyperscaler"],
             r.get("primary_tenant"), r.get("city"), r.get("state_or_region"),
             r.get("country", "US"), r.get("lat"), r.get("lon"),
             r.get("capacity_definition", "Critical-IT"),
             r.get("it_load_mw_planned"), r.get("it_load_mw_phase1"),
             r.get("it_load_mw_energized", 0),
             r.get("cod_phase1_year"), r.get("cod_full_year"),
             r.get("status", "Announced"),
             r.get("power_source_summary"), r.get("primary_use"),
             r.get("notes"), r["source_id"]),
        )
        n += 1
    conn.commit()
    return n


def load_lcoe(conn: sqlite3.Connection) -> int:
    doc = read_yaml(DATA / "lcoe" / "lcoe.yaml")
    n = 0
    for r in doc["rows"]:
        conn.execute(
            """INSERT INTO lcoe_data
               (technology, year_vintage, report_name, report_date, geography,
                subsidized, lcoe_low, lcoe_mid, lcoe_high, currency_year, notes, source_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["technology"], r["year_vintage"], r["report_name"], r.get("report_date"),
             r.get("geography", "US"), int(r.get("subsidized", 0)),
             r.get("lcoe_low"), r.get("lcoe_mid"), r.get("lcoe_high"),
             r.get("currency_year"), r.get("notes"), r["source_id"]),
        )
        n += 1
    conn.commit()
    return n


def load_gas_capex(conn: sqlite3.Connection) -> int:
    doc = read_yaml(DATA / "capex" / "gas_capex.yaml")
    n = 0
    for r in doc["rows"]:
        conn.execute(
            """INSERT INTO gas_capex
               (year, plant_type, cost_low_kw, cost_mid_kw, cost_high_kw,
                data_type, label, notes, source_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (r["year"], r["plant_type"], r.get("cost_low_kw"), r.get("cost_mid_kw"),
             r.get("cost_high_kw"), r["data_type"], r["label"], r.get("notes"),
             r["source_id"]),
        )
        n += 1
    conn.commit()
    return n


def load_renewable_capex(conn: sqlite3.Connection) -> int:
    doc = read_yaml(DATA / "capex" / "renewable_capex.yaml")
    n = 0
    for r in doc["rows"]:
        conn.execute(
            """INSERT INTO renewable_capex
               (technology, year, cost_low_kw, cost_mid_kw, cost_high_kw,
                data_type, label, notes, source_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (r["technology"], r["year"], r.get("cost_low_kw"), r.get("cost_mid_kw"),
             r.get("cost_high_kw"), r["data_type"], r["label"], r.get("notes"),
             r["source_id"]),
        )
        n += 1
    conn.commit()
    return n


def load_turbine_supply(conn: sqlite3.Connection) -> int:
    doc = read_yaml(DATA / "capex" / "turbine_supply.yaml")
    n = 0
    for r in doc["rows"]:
        conn.execute(
            """INSERT INTO turbine_supply
               (manufacturer, as_of, backlog_note, notes, source_id)
               VALUES (?,?,?,?,?)""",
            (r["manufacturer"], r["as_of"], r["backlog_note"],
             r.get("notes"), r["source_id"]),
        )
        n += 1
    conn.commit()
    return n


def load_demand(conn: sqlite3.Connection) -> tuple[int, int, int]:
    doc = read_yaml(DATA / "demand" / "demand.yaml")
    n1 = n2 = n3 = 0
    for r in doc.get("demand_metrics", []):
        conn.execute(
            """INSERT INTO demand_metrics
               (metric, value_num, value_text, unit, year, geography, notes, source_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (r["metric"], r.get("value_num"), r.get("value_text"),
             r.get("unit"), r.get("year"), r.get("geography"),
             r.get("notes"), r["source_id"]),
        )
        n1 += 1
    for r in doc.get("grid_capacity_plan", []):
        conn.execute(
            """INSERT INTO grid_capacity_plan
               (technology, window_start, window_end, gw_planned, geography, notes, source_id)
               VALUES (?,?,?,?,?,?,?)""",
            (r["technology"], r["window_start"], r["window_end"], r["gw_planned"],
             r.get("geography", "US"), r.get("notes"), r["source_id"]),
        )
        n2 += 1
    for r in doc.get("hyperscaler_cumulative", []):
        conn.execute(
            """INSERT INTO hyperscaler_cumulative
               (company, as_of, metric, value_gw, notes, source_id)
               VALUES (?,?,?,?,?,?)""",
            (r["company"], r["as_of"], r["metric"], r["value_gw"],
             r.get("notes"), r["source_id"]),
        )
        n3 += 1
    conn.commit()
    return n1, n2, n3


def main() -> int:
    if DB.exists():
        DB.unlink()
    conn = sqlite3.connect(DB)
    try:
        rebuild_schema(conn)
        n_src = len(load_sources(conn))
        n_c = load_contracts(conn)
        n_cam = load_campuses(conn)
        n_pg = load_planned_generators(conn)
        n_l = load_lcoe(conn)
        n_g = load_gas_capex(conn)
        n_r = load_renewable_capex(conn)
        n_t = load_turbine_supply(conn)
        n_d, n_p, n_h = load_demand(conn)
    except sqlite3.IntegrityError as e:
        print(f"[LOAD FAILED] IntegrityError: {e}", file=sys.stderr)
        print("Most common cause: a row references a source_id not present in sources.yaml.")
        return 1
    finally:
        conn.close()

    print(f"loaded: {n_src} sources")
    print(f"        {n_c} contracts")
    print(f"        {n_cam} campuses")
    print(f"        {n_pg} planned_generators (EIA-860M)")
    print(f"        {n_l} lcoe")
    print(f"        {n_g} gas_capex")
    print(f"        {n_r} renewable_capex")
    print(f"        {n_t} turbine_supply")
    print(f"        {n_d} demand_metrics")
    print(f"        {n_p} grid_capacity_plan")
    print(f"        {n_h} hyperscaler_cumulative")
    print(f"db: {DB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
