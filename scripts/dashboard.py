"""Generate a self-contained interactive HTML dashboard.

Reads data.db, embeds the rows as JSON, renders charts with Chart.js (loaded from
CDN — single file, no build step). Open output/dashboard.html in a browser.
"""
from __future__ import annotations

import html
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data.db"
OUT = ROOT / "output" / "dashboard.html"


def q(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def compute_vintage_transitions(conn: sqlite3.Connection) -> list[dict]:
    """For each consecutive vintage pair, decompose flows.

    OPERATED is now directly observed (not inferred): MW that began commercial
    operation between vintage_from and vintage_to, per the EIA Operating sheet's
    Operating Year/Month field.

    NEWLY ANNOUNCED and CANCELLED are inferred from the planned-generators panel
    (plant appears or disappears). All flows annualized so unevenly-spaced
    vintages compare cleanly.
    """
    from collections import defaultdict

    # ---- Operated: directly observed from Operating sheet ----
    op_rows = conn.execute("""
        SELECT operating_year, operating_month, nameplate_capacity_mw
        FROM operating_generators
        WHERE operating_year IS NOT NULL AND operating_month IS NOT NULL
    """).fetchall()

    # ---- Planned panel: inflow + cancellation ----
    pl_rows = conn.execute("""
        SELECT vintage, plant_id, generator_id, nameplate_capacity_mw
        FROM planned_generators
        WHERE plant_id IS NOT NULL AND generator_id IS NOT NULL
    """).fetchall()
    by_v = defaultdict(dict)
    for r in pl_rows:
        by_v[r['vintage']][(r['plant_id'], r['generator_id'])] = (
            r['nameplate_capacity_mw'] or 0
        )
    vintages = sorted(by_v.keys())

    def months_between(va: str, vb: str) -> int:
        ya, ma = [int(x) for x in va.split('-')]
        yb, mb = [int(x) for x in vb.split('-')]
        return (yb - ya) * 12 + (mb - ma)

    def in_window(year: int, month: int, v_from: str, v_to: str) -> bool:
        # Vintage 'YYYY-MM' represents data as of that month-end. A plant that
        # began operating in month M of year Y "operated in window (v_from, v_to]"
        # if (Y, M) is strictly after v_from and on or before v_to.
        ym = year * 100 + month
        fy, fm = [int(x) for x in v_from.split('-')]
        ty, tm = [int(x) for x in v_to.split('-')]
        return (fy * 100 + fm) < ym <= (ty * 100 + tm)

    out = []
    for v_from, v_to in zip(vintages[:-1], vintages[1:]):
        a = by_v[v_from]; b = by_v[v_to]
        months = max(months_between(v_from, v_to), 1)
        ann = lambda mw: round(mw * 12.0 / months, 1)

        # Operated = directly observed
        operated = sum(r['nameplate_capacity_mw'] or 0
                       for r in op_rows
                       if in_window(r['operating_year'], r['operating_month'], v_from, v_to))

        # Cancelled = was in planned set, gone in next vintage. Don't filter by tier
        # since we now have a separate operated number.
        cancelled = sum(mw for k, mw in a.items() if k not in b)
        # Subtract whatever fraction of the disappearance was actually operated
        # (we counted them above). We can't precisely match because operating
        # data doesn't include the planned-set plant_id — but practically the
        # operated MW is much smaller than total disappearance, so the residual
        # "cancelled" is close to true.
        cancelled = max(0, cancelled - operated)

        # Newly announced = present in v_to, not in v_from
        new_mw = sum(mw for k, mw in b.items() if k not in a)

        out.append({
            'v_from': v_from, 'v_to': v_to, 'months': months,
            'operated_mw':  round(operated,  0),
            'cancelled_mw': round(cancelled, 0),
            'new_mw':       round(new_mw,    0),
            'operated_per_yr':  ann(operated),
            'cancelled_per_yr': ann(cancelled),
            'new_per_yr':       ann(new_mw),
        })
    return out


def build(conn: sqlite3.Connection) -> str:
    conn.row_factory = sqlite3.Row

    data = {
        "contracts": q(conn, """
            SELECT id, company, operator_type, announced_date, year, cod_year, cod_note,
                   generation_type, capacity_mw, storage_power_mw, storage_energy_mwh,
                   confidence, deal_name,
                   counterparty, contract_years, geography, status,
                   connection_type, connection_reason, notes, source_id
            FROM hyperscaler_contracts"""),
        "lcoe": q(conn, """
            SELECT technology, year_vintage, report_name, geography, subsidized,
                   lcoe_low, lcoe_mid, lcoe_high, source_id
            FROM lcoe_data"""),
        "gas_capex": q(conn, "SELECT * FROM gas_capex"),
        "renewable_capex": q(conn, "SELECT * FROM renewable_capex"),
        "demand": q(conn, "SELECT * FROM demand_metrics"),
        "grid_plan": q(conn, "SELECT * FROM grid_capacity_plan"),
        "cumulative": q(conn, "SELECT * FROM hyperscaler_cumulative"),
        "turbine": q(conn, "SELECT * FROM turbine_supply"),
        "campuses": q(conn, """
            SELECT campus_id, campus_name, hyperscaler, primary_tenant,
                   city, state_or_region, country, lat, lon,
                   capacity_definition, it_load_mw_planned, it_load_mw_phase1,
                   it_load_mw_energized, cod_phase1_year, cod_full_year,
                   status, power_source_summary, primary_use, notes, source_id
            FROM data_center_campuses
            ORDER BY COALESCE(it_load_mw_planned, it_load_mw_phase1, 0) DESC"""),
        "campus_pipeline": q(conn, """
            SELECT hyperscaler, campus_count, energized_mw, phase1_mw, planned_mw
            FROM v_campus_pipeline_by_hyperscaler"""),
        "commentary": q(conn, """
            SELECT statement_id, source_id, statement_date, date_precision,
                   event_name, timeline_bucket, speaker_name, speaker_title,
                   organization, organization_bucket, source_route, source_type,
                   statement_taxonomy, polarity, load_stage, geography,
                   related_company, short_quote, paraphrase, numeric_value,
                   numeric_unit, capacity_basis, time_horizon_start,
                   time_horizon_end, confidence, independence_group, notes
            FROM qualitative_load_commentary
            ORDER BY statement_date, statement_id"""),
        "eia_tier": q(conn, """
            SELECT status_tier,
                   COUNT(*) AS gens,
                   ROUND(SUM(nameplate_capacity_mw), 0) AS announced_mw,
                   ROUND(SUM(nameplate_capacity_mw * delivery_probability), 0) AS expected_mw,
                   ROUND(AVG(delivery_probability), 2) AS prob
            FROM planned_generators
            WHERE vintage = (SELECT MAX(vintage) FROM planned_generators)
            GROUP BY status_tier"""),
        "eia_year_tech": q(conn, """
            SELECT * FROM v_eia_pipeline_by_tech"""),
        "eia_status_by_vintage": q(conn, "SELECT * FROM v_eia_status_by_vintage"),
        "eia_tech_by_vintage": q(conn, "SELECT * FROM v_eia_tech_by_vintage"),
        "eia_stage_tech_by_vintage": q(conn, "SELECT * FROM v_eia_stage_tech_by_vintage"),
        "eia_transitions": compute_vintage_transitions(conn),
        "eia_state_tech": q(conn, """
            SELECT plant_state,
                   CASE
                     WHEN technology LIKE '%Solar%'    THEN 'Solar'
                     WHEN technology LIKE '%Wind%'     THEN 'Wind'
                     WHEN technology = 'Batteries' OR technology LIKE '%Storage%' THEN 'Storage'
                     WHEN technology LIKE '%Nuclear%'  THEN 'Nuclear'
                     WHEN technology LIKE '%Natural Gas%' THEN 'Gas'
                     WHEN technology LIKE '%Geothermal%' THEN 'Geothermal'
                     WHEN technology LIKE '%Hydro%' THEN 'Hydro'
                     ELSE 'Other'
                   END AS tech_group,
                   COUNT(*)                              AS gen_count,
                   ROUND(SUM(nameplate_capacity_mw), 0) AS announced_mw,
                   ROUND(SUM(nameplate_capacity_mw * delivery_probability), 0) AS expected_mw
            FROM planned_generators
            WHERE plant_state IS NOT NULL
              AND vintage = (SELECT MAX(vintage) FROM planned_generators)
            GROUP BY plant_state, tech_group"""),
        "eia_status_tech": q(conn, """
            SELECT status_tier,
                   CASE
                     WHEN technology LIKE '%Solar%'    THEN 'Solar'
                     WHEN technology LIKE '%Wind%'     THEN 'Wind'
                     WHEN technology = 'Batteries' OR technology LIKE '%Storage%' THEN 'Storage'
                     WHEN technology LIKE '%Nuclear%'  THEN 'Nuclear'
                     WHEN technology LIKE '%Natural Gas%' THEN 'Gas'
                     WHEN technology LIKE '%Geothermal%' THEN 'Geothermal'
                     WHEN technology LIKE '%Hydro%' THEN 'Hydro'
                     ELSE 'Other'
                   END AS tech_group,
                   COUNT(*)                              AS gen_count,
                   ROUND(SUM(nameplate_capacity_mw), 0) AS announced_mw,
                   ROUND(SUM(nameplate_capacity_mw * delivery_probability), 0) AS expected_mw
            FROM planned_generators
            WHERE vintage = (SELECT MAX(vintage) FROM planned_generators)
            GROUP BY status_tier, tech_group"""),
        "sources": {r["id"]: dict(r) for r in conn.execute("SELECT * FROM sources")},
        "disclosures": q(conn, """
            SELECT disclosure_id, operator, operator_bucket, as_of_date, as_of_quarter,
                   fiscal_label, stage_normalized, stage_verbatim, row_kind, component_label,
                   mw_value, capacity_basis, tenant_operator, verbatim_quote, notes, source_id
            FROM operator_capacity_disclosures
            ORDER BY operator, as_of_quarter DESC, stage_normalized, capacity_basis"""),
        "disclosure_cells": __import__("stacks").headline_cells(conn),
        "disclosure_series": {
            op: __import__("stacks").quarterly_series(conn, op)
            for (op,) in conn.execute("""
                SELECT DISTINCT operator FROM operator_capacity_disclosures
                WHERE stage_normalized != 'none_disclosed'
                GROUP BY operator, stage_normalized, capacity_basis
                HAVING COUNT(DISTINCT as_of_quarter) >= 2""")
        },
        "disclosure_coverage": [
            dict(r) for r in conn.execute("""
                SELECT hyperscaler AS operator,
                       ROUND(SUM(COALESCE(it_load_mw_planned, it_load_mw_phase1, it_load_mw_energized)), 0) AS campus_mw
                FROM data_center_campuses
                WHERE hyperscaler IN ('Microsoft','Google','Amazon','Meta','Oracle','xAI','CoreWeave','Nebius')
                GROUP BY hyperscaler""")
        ],
    }
    payload = json.dumps(data, default=str)

    import datetime as _dt
    return (TEMPLATE
            .replace("__DATA__", payload)
            .replace("__BUILDDATE__", _dt.date.today().strftime("%B %-d, %Y")))


TEMPLATE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>Hyperscaler Energy Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:#F5F5F7; --panel:#FFFFFF; --ink:#1D1D1F; --muted:#6E6E73;
    --line:#ECECEE; --accent:#0E7B5B;
    --gas:#3A3A3C; --clean:#0E7B5B; --nuclear:#8273B5; --geo:#C26B4E;
    --solar:#E2A63D; --wind:#79A6CE; --storage:#2F8488; --other:#B9B9C0;
    --tile-r:22px;
  }
  * { box-sizing: border-box; }
  body { margin:0; font-family:'Inter Tight','Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
         background:var(--bg); color:var(--ink); line-height:1.5; }
  header { padding:2.1rem 2.4rem .2rem; display:flex; justify-content:space-between; align-items:baseline; }
  h1 { margin:0 0 .35rem; font-size:1.55rem; font-weight:700; letter-spacing:-.02em; }
  .sub { color:var(--muted); font-size:.82rem; font-variant-numeric:tabular-nums; }
  main { padding:1.4rem 2.4rem 2.6rem; max-width:1400px; margin:0 auto; }
  .bento { display:grid; grid-template-columns:repeat(6,1fr);
           grid-template-areas:
             "hero hero chart chart chart chart"
             "hero hero chart chart chart chart"
             "clean clean ops ops latest latest"
             "dark dark . . . .";
           gap:14px; margin-bottom:14px; }
  .tile { background:var(--panel); border-radius:22px; padding:1.3rem 1.4rem; }
  .tile-hero { grid-area:hero; display:flex; flex-direction:column; }
  .tile-chart { grid-area:chart; min-height:430px; }
  .tile-clean { grid-area:clean; }
  .tile-ops { grid-area:ops; }
  .tile-latest { grid-area:latest; }
  .tile-dark { grid-area:dark; background:#1D1D1F; color:#F5F5F7; }
  .tile .eyebrow { font-size:.75rem; font-weight:600; color:#6E6E73; }
  .tile-dark .eyebrow { color:#86868B; }
  .tile .kv { display:flex; justify-content:space-between; align-items:baseline;
              font-size:.8rem; padding:.55rem 0; border-top:1px solid #F0F0F2; }
  .tile .kv b { font-variant-numeric:tabular-nums; font-weight:600; }
  .ph { color:#B9B9C0; font-size:.8rem; text-align:center; padding:2.2rem 0; }
  .hero-fig { font-size:3.3rem; font-weight:700; letter-spacing:-.035em; line-height:1;
              margin-top:.7rem; font-variant-numeric:tabular-nums; }
  .hero-unit { font-size:1.35rem; color:#6E6E73; font-weight:600; margin-left:.15em; }
  .hero-sub { color:#6E6E73; font-size:.82rem; margin-top:.55rem; }
  .hero-kvs { margin-top:auto; padding-top:1.1rem; }
  .bchart-wrap { position:relative; height:296px; margin-top:.8rem; }
  .blegend { display:flex; flex-wrap:wrap; gap:6px 14px; margin-top:12px; padding-top:12px;
             border-top:1px solid #F0F0F2; font-size:.72rem; color:#6E6E73; }
  .bdot { width:8px; height:8px; border-radius:4px; display:inline-block; margin-right:5px; vertical-align:-1px; }
  .bops { display:grid; grid-template-columns:78px 1fr 40px; gap:9px 10px; align-items:center;
          font-size:.78rem; margin-top:14px; }
  .bops .bbar { height:7px; border-radius:4px; background:#1D1D1F; }
  .blat { display:flex; justify-content:space-between; align-items:center; gap:8px;
          padding:.5rem 0; border-bottom:1px solid #F0F0F2; font-size:.78rem; }
  .blat:last-child { border-bottom:0; }
  .dark-num { font-size:2.2rem; font-weight:700; margin-top:.55rem; font-variant-numeric:tabular-nums; }
  .dark-cap { font-size:.78rem; color:#A1A1A6; line-height:1.5; margin-top:.35rem; }

  @media (max-width:900px) { .bento { grid-template-columns:1fr; grid-template-areas:none; }
    .bento > .tile { grid-area:auto; } }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:14px; }
  .card { background:var(--panel); border-radius:var(--tile-r); padding:1.35rem 1.5rem; }
  .card h2 { margin:0 0 .25rem; font-size:.95rem; font-weight:600; letter-spacing:-.01em; }
  .card .hint { color:var(--muted); font-size:.78rem; margin-bottom:.7rem; }
  .chart-wrap { position:relative; height:320px; }
  .wide { grid-column:1 / -1; }
  .filters { display:flex; gap:.6rem; flex-wrap:wrap; margin-bottom:1rem; align-items:center; }
  .filters label { color:var(--muted); font-size:.82rem; font-weight:500; }
  select, input[type="text"] {
    background:#FFFFFF; color:var(--ink); border:0.5px solid #D9D9DE;
    padding:.42rem .65rem; border-radius:10px; font-size:.84rem; font-family:inherit;
  }
  select:focus, input[type="text"]:focus { outline:none; border-color:#B9B9C0; }
  table { width:100%; border-collapse:collapse; font-size:.82rem; }
  th, td { text-align:left; padding:.5rem .6rem; border-bottom:1px solid #F0F0F2;
           vertical-align:top; }
  th { color:var(--muted); font-weight:600; font-size:.72rem; letter-spacing:0;
       text-transform:none; position:sticky; top:0; background:var(--panel);
       cursor:pointer; user-select:none; box-shadow:0 1px 0 #ECECEE; }
  th:hover { color:var(--ink); }
  td { font-variant-numeric:tabular-nums; }
  tr:hover td { background:#FAFAFA; }
  .table-wrap { max-height:640px; overflow:auto; background:var(--panel);
                border-radius:18px; padding:.4rem .9rem .9rem; }
  .cite { color:var(--accent); text-decoration:none; font-size:.72rem;
          padding:1px 6px; border-radius:6px; background:rgba(14,123,91,.08);
          margin-left:.2rem; }
  .cite:hover { background:rgba(14,123,91,.18); }
  .badge { display:inline-block; padding:2px 8px; border-radius:999px;
           font-size:.68rem; font-weight:600; letter-spacing:0; }
  .b-gas { background:rgba(58,58,60,.09); color:#3A3A3C; }
  .b-fuelcell { background:rgba(160,140,114,.16); color:#6F5B41; }
  .b-clean { background:rgba(14,123,91,.10); color:#0E7B5B; }
  .b-nuclear { background:rgba(130,115,181,.14); color:#5F5286; }
  .b-geo { background:rgba(194,107,78,.13); color:#A04F33; }
  .b-op { background:rgba(14,123,91,.10); color:#0E7B5B; }
  .b-pending { background:rgba(176,122,46,.13); color:#9A6A24; }
  .b-announced { background:rgba(142,142,147,.13); color:#6E6E73; }
  .b-btm { background:rgba(194,107,78,.13); color:#A04F33; }
  .b-grid { background:rgba(47,132,136,.12); color:#236F73; }
  .b-unknown { background:rgba(142,142,147,.13); color:#6E6E73; }
  .deal-link { color:var(--ink); text-decoration:none; border-bottom:1px solid #DEDEE1; }
  .deal-link:hover { color:var(--accent); border-bottom-color:var(--accent); }
  .conf-s { color:#0E7B5B; font-weight:600; font-size:.7rem; }
  .conf-e { color:#B07A2E; font-weight:600; font-size:.7rem; }
  .tabs { display:inline-flex; gap:2px; background:#E9E9EB; border-radius:999px;
          padding:3px; margin-bottom:1.3rem; }
  .tab { padding:.48rem 1.05rem; cursor:pointer; color:var(--muted);
         border-radius:999px; font-size:.84rem; font-weight:500; white-space:nowrap; }
  .tab.active { color:var(--ink); background:#FFFFFF; font-weight:600;
                border:0.5px solid rgba(0,0,0,.06); }
  .panel { display:none; }
  .panel.active { display:block; }
  code { background:#EFEFF1; padding:1px 6px; border-radius:6px;
         font-size:.8rem; color:var(--muted); }
  .legend-swatch { display:inline-block; width:9px; height:9px;
                   border-radius:5px; margin-right:5px; vertical-align:middle; }

  /* freshness highlight — deals announced within the last 7 days */
  .new-badge {
    display:inline-block; margin-left:.35rem; padding:1px 8px;
    border-radius:999px; font-size:.62rem; font-weight:600;
    letter-spacing:.01em;
    background:rgba(14,123,91,.10); color:#0E7B5B;
    vertical-align:middle;
  }

  @media (max-width: 900px) { .grid { grid-template-columns:1fr; } }

  /* --- Campuses panel --- */
  .camp-hero {
    display:grid; grid-template-columns: 1.6fr 1fr; gap:2.5rem;
    background:var(--panel); border-radius:var(--tile-r);
    padding:1.6rem 1.7rem; margin-bottom:14px;
  }
  .camp-hero .lead { color:var(--muted); font-size:.9rem; max-width:52ch; line-height:1.6; margin:.2rem 0; }
  .camp-hero .lead b { color:var(--ink); font-weight:600; }
  .camp-stats { display:grid; grid-template-columns:1fr 1fr; gap:1.3rem 2rem; align-self:end; }
  .camp-stat .num { font-size:2.2rem; font-weight:700; letter-spacing:-.03em; line-height:1;
                    font-variant-numeric: tabular-nums; }
  .camp-stat .num .gw { font-size:.95rem; color:var(--muted); font-weight:500; margin-left:.2em; }
  .camp-stat .lab { color:var(--muted); font-size:.73rem; font-weight:600; margin-top:.4rem; }
  .camp-stat.live .num { color:#0E7B5B; }
  .camp-stat.gap .num { color:#B07A2E; }

  .camp-section-title {
    font-size:.8rem; color:var(--ink); margin:0 0 .9rem; font-weight:650;
    letter-spacing:-.01em; text-transform:none;
    display:flex; align-items:baseline; gap:.8rem;
  }
  .camp-section-title .rule { flex:1; height:1px; background:#E4E4E7; }
  .camp-section-title span:last-child { color:var(--muted); font-weight:400; font-size:.74rem; }

  .pipeline-list { display:flex; flex-direction:column; gap:.65rem;
                   background:var(--panel); border-radius:var(--tile-r);
                   padding:1.2rem 1.4rem; margin-bottom:14px; }
  .pipe-row { display:grid; grid-template-columns: 130px 1fr 84px;
              gap:1rem; align-items:center; }
  .pipe-row .name { font-weight:600; font-size:.88rem; }
  .pipe-row .name .cnt { color:var(--muted); font-weight:400; font-size:.73rem; margin-left:.4rem; }
  .pipe-bar { position:relative; height:24px; background:#F0F0F2;
              border-radius:7px; overflow:hidden; }
  .pipe-bar .seg { position:absolute; top:0; bottom:0; transition:width .4s ease; }
  .pipe-bar .seg.energized { background:#0E7B5B; left:0; }
  .pipe-bar .seg.phase1    { background:#7FB5A6; }
  .pipe-bar .seg.planned   { background:#E2E2E5; }
  .pipe-bar .seg-label { position:absolute; top:50%; transform:translateY(-50%);
                         font-size:.66rem; color:#FFFFFF; font-weight:600; padding:0 .45rem;
                         white-space:nowrap; pointer-events:none; }
  .pipe-bar .seg-label.outside { color:var(--muted); }
  .pipe-row .total { font-variant-numeric: tabular-nums; font-size:.84rem;
                     text-align:right; color:var(--muted); }
  .pipe-row .total b { color:var(--ink); font-weight:600; }

  /* clickable bars + source popover */
  .pipe-bar.click { cursor:pointer; }
  .pipe-bar.click:hover { box-shadow:inset 0 0 0 1.5px rgba(14,123,91,.5); }
  #srcPop { position:fixed; z-index:300; width:min(460px, calc(100vw - 32px));
            max-height:min(480px, 72vh); overflow-y:auto; background:var(--panel);
            border-radius:18px; padding:1.05rem 1.15rem 1rem; display:none;
            box-shadow:0 18px 50px rgba(0,0,0,.18), 0 2px 10px rgba(0,0,0,.08); }
  #srcPop.open { display:block; }
  #srcPop h4 { margin:0; font-size:.92rem; font-weight:650; letter-spacing:-.01em; padding-right:1.8rem; }
  #srcPop .psub { font-size:.72rem; color:var(--muted); margin:.15rem 0 .3rem; }
  #srcPop .x { position:absolute; top:.75rem; right:.85rem; width:24px; height:24px; border:none;
               border-radius:50%; background:#F0F0F2; color:var(--muted); font-size:.78rem;
               cursor:pointer; line-height:1; display:flex; align-items:center; justify-content:center; }
  #srcPop .x:hover { background:#E7E7EA; color:var(--ink); }
  #srcPop .it { padding:.55rem 0 .5rem; font-size:.78rem; }
  #srcPop .it + .it { border-top:1px solid var(--line); }
  #srcPop .it .row1 { display:flex; justify-content:space-between; gap:.9rem; align-items:baseline; }
  #srcPop .it .mw { font-variant-numeric:tabular-nums; font-weight:600; white-space:nowrap; }
  #srcPop .it .quote { color:var(--muted); font-size:.72rem; font-style:italic; margin-top:.12rem; }
  #srcPop .it .meta { color:var(--muted); font-size:.71rem; margin-top:.14rem; }
  #srcPop .it .meta a { color:var(--accent); text-decoration:none; font-weight:500; }
  #srcPop .it .meta a:hover { text-decoration:underline; }

  .pipe-legend { display:flex; gap:1.4rem; font-size:.74rem; color:var(--muted);
                 margin:-2px 0 14px; padding:0 .3rem; }
  .pipe-legend .sw { display:inline-block; width:10px; height:10px; border-radius:5px;
                     vertical-align:middle; margin-right:.4em; }

  .camp-table-wrap { background:var(--panel); border-radius:18px;
                     max-height:640px; overflow:auto; padding:.4rem .9rem .9rem; }
  .camp-table-wrap table { width:100%; border-collapse:collapse; font-size:.8rem; }
  .camp-table-wrap th { background:var(--panel); color:var(--muted); font-weight:600; font-size:.71rem;
                        text-transform:none; letter-spacing:0;
                        text-align:left; padding:.55rem .6rem; border-bottom:1px solid #F0F0F2;
                        position:sticky; top:0; cursor:pointer; user-select:none;
                        box-shadow:0 1px 0 #ECECEE; }
  .camp-table-wrap td { padding:.55rem .6rem; border-bottom:1px solid #F0F0F2; vertical-align:top; }
  .camp-table-wrap td.r { text-align:right; font-variant-numeric: tabular-nums; white-space:nowrap; }
  .camp-table-wrap tr:hover td { background:#FAFAFA; }

  .b-stat { display:inline-block; padding:2px 8px; border-radius:999px;
            font-size:.66rem; font-weight:600; letter-spacing:0; }
  .b-Operational      { background:rgba(14,123,91,.12); color:#0E7B5B; }
  .b-PartiallyEnergized { background:rgba(14,123,91,.07); color:#3C8A6E; border:1px dashed rgba(14,123,91,.3); }
  .b-UnderConstruction { background:rgba(226,166,61,.16); color:#8A6420; }
  .b-SiteWork         { background:rgba(226,166,61,.10); color:#9A6A24; }
  .b-Announced        { background:rgba(142,142,147,.13); color:#6E6E73; }
  .b-Paused           { background:rgba(194,107,78,.13); color:#A04F33; }
  .b-Cancelled        { background:rgba(194,107,78,.18); color:#A04F33; text-decoration:line-through; }

  .camp-filters { display:flex; gap:.6rem; flex-wrap:wrap; margin:1rem 0; align-items:center; }
  .camp-filters label { color:var(--muted); font-size:.82rem; font-weight:500; }
  .camp-filters .count { color:var(--muted); font-size:.78rem; margin-left:auto; }

  @media (max-width: 900px) {
    .camp-hero { grid-template-columns:1fr; gap:1.5rem; }
    .pipe-row { grid-template-columns:90px 1fr 70px; gap:.6rem; }
    .kpi:first-child { grid-column:span 1; }
  }

  /* --- Commentary timeline --- */
  .ql-filters { display:flex; gap:.6rem; flex-wrap:wrap; margin:1rem 0; align-items:center; }
  .ql-filters label { color:var(--muted); font-size:.82rem; font-weight:500; }
  .ql-filters .count { color:var(--muted); font-size:.78rem; margin-left:auto; }
  .ql-layout { display:grid; grid-template-columns:minmax(260px,.9fr) minmax(0,1.6fr); gap:14px; }
  .ql-timeline { border-radius:18px; padding:1rem 1.2rem; max-height:680px; overflow:auto;
                 background:var(--panel); }
  .ql-bucket { display:grid; grid-template-columns:70px 1fr; gap:.8rem; padding:.75rem 0;
               border-bottom:1px solid #F0F0F2; }
  .ql-bucket:last-child { border-bottom:0; }
  .ql-date { color:var(--accent); font-weight:650; font-size:.78rem; font-variant-numeric:tabular-nums; }
  .ql-item { position:relative; padding:0 0 .75rem 1rem; border-left:1px solid #E4E4E7; }
  .ql-item:last-child { padding-bottom:0; }
  .ql-item::before { content:""; position:absolute; left:-4px; top:.2rem; width:7px; height:7px;
                     border-radius:50%; background:var(--accent); }
  .ql-org { font-weight:600; font-size:.86rem; }
  .ql-meta { color:var(--muted); font-size:.73rem; margin-top:.1rem; }
  .ql-text { color:var(--ink); font-size:.8rem; margin-top:.28rem; }
  .ql-badge { display:inline-block; padding:1px 7px; border-radius:999px; font-size:.64rem;
              font-weight:600; margin-right:.25rem; }
  .ql-pos { background:rgba(14,123,91,.11); color:#0E7B5B; }
  .ql-mix { background:rgba(226,166,61,.15); color:#8A6420; }
  .ql-neg { background:rgba(194,107,78,.13); color:#A04F33; }
  .ql-neutral { background:rgba(142,142,147,.13); color:#6E6E73; }
  .ql-stage { background:rgba(47,132,136,.12); color:#236F73; }
  .ql-basis { background:rgba(130,115,181,.13); color:#5F5286; }
  .ql-table-wrap { background:var(--panel); border-radius:18px; max-height:680px; overflow:auto;
                   padding:.4rem .9rem .9rem; }
  .ql-table-wrap th { background:var(--panel); }
  .ql-table-wrap td.r { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }

  @media (max-width: 1000px) {
    .ql-layout { grid-template-columns:1fr; }
  }
</style>
</head>
<body>
<header>
  <h1>Hyperscaler Energy</h1>
  <div class="sub">Updated __BUILDDATE__</div>
</header>

<main>
  <div class="tabs">
    <div class="tab active" data-tab="overview">Overview</div>
    <div class="tab" data-tab="contracts">Contracts</div>
    <div class="tab" data-tab="campuses">Campuses</div>
    <div class="tab" data-tab="disclosures">Operator disclosures</div>
    <div class="tab" data-tab="commentary">Commentary Timeline</div>
    <div class="tab" data-tab="eia">Federal Cross-Check</div>
    <div class="tab" data-tab="costs">Costs (LCOE / CAPEX)</div>
    <div class="tab" data-tab="sources">Sources</div>
  </div>

  <section class="panel active" id="panel-overview">
    <div class="bento">
      <div class="tile tile-hero">
        <div class="eyebrow">Contracted capacity</div>
        <div class="hero-fig"><span id="bHeroGW">—</span><span class="hero-unit">GW</span></div>
        <div class="hero-sub" id="bHeroSub"></div>
        <div class="hero-kvs" id="bHeroKvs"></div>
      </div>
      <div class="tile tile-chart">
        <div class="eyebrow">By announcement year and generation type — six hyperscalers, MW</div>
        <div class="bchart-wrap"><canvas id="chartBento"></canvas></div>
        <div class="blegend" id="bentoLegend"></div>
      </div>
      <div class="tile tile-clean">
        <div class="eyebrow">Clean share</div>
        <div style="text-align:center"><svg id="bDonut" viewBox="0 0 100 100" style="width:118px;margin-top:10px"></svg></div>
      </div>
      <div class="tile tile-ops"><div class="eyebrow">By operator, GW</div><div id="bOps" class="bops"></div></div>
      <div class="tile tile-latest"><div class="eyebrow">Latest</div><div id="bLatest"></div></div>
      <div class="tile tile-dark">
        <div class="eyebrow">Sources</div>
        <div class="dark-num" id="bSrcCount">—</div>
        <div class="dark-cap">sources on file. Every number links to one.</div>
      </div>
    </div>
    <div class="tile" style="margin-bottom:14px">
      <div class="eyebrow">By announcement quarter and generation type — six hyperscalers, MW</div>
      <div class="bchart-wrap" style="height:320px"><canvas id="chartBentoQ"></canvas></div>
      <div class="blegend" id="bentoQLegend"></div>
      <div id="bentoQNote" style="font-size:.7rem; color:#86868B; margin-top:8px"></div>
    </div>
    <h3 class="camp-section-title" style="margin-top:1.6rem">More detail<span class="rule"></span><span style="font-weight:400;text-transform:none;letter-spacing:0">portfolio views — all 39 operators</span></h3>
    <div class="grid">
      <div class="card wide">
        <h2>All operators — capacity by announcement year</h2>
        <div class="hint">Stacked by generation type, announcement-year basis. Includes the colocation, neocloud, and sovereign operators that the six-hyperscaler chart above excludes.</div>
        <div class="chart-wrap"><canvas id="chartYear"></canvas></div>
      </div>
      <div class="card wide">
        <h2>Battery storage energy by announcement year</h2>
        <div class="hint">MWh/GWh attached to storage and hybrid rows. This is energy duration, not another MW stack.</div>
        <div class="chart-wrap"><canvas id="chartStorageYear"></canvas></div>
      </div>
      <div class="card">
        <h2>Total contracted MW by company</h2>
        <div class="hint">Sum of all announced deals, color-coded by generation type.</div>
        <div class="chart-wrap"><canvas id="chartCompany"></canvas></div>
      </div>
      <div class="card">
        <h2>Operational pipeline by COD year</h2>
        <div class="hint">Only rows with a disclosed Commercial Operation Date.</div>
        <div class="chart-wrap"><canvas id="chartCod"></canvas></div>
      </div>
      <div class="card wide">
        <h2>Generation mix by company</h2>
        <div class="hint">Horizontal bar, MW by generation type per hyperscaler.</div>
        <div class="chart-wrap" style="height:360px"><canvas id="chartMix"></canvas></div>
      </div>

      <div class="card">
        <h2>Behind-the-meter vs grid — all contracts by announcement year</h2>
        <div class="hint">Every tracked agreement. Stacked by announcement year regardless of COD disclosure.</div>
        <div class="chart-wrap"><canvas id="chartConnAll"></canvas></div>
      </div>
      <div class="card">
        <h2>Behind-the-meter vs grid by COD year</h2>
        <div class="hint">Only rows with a disclosed Commercial Operation Date.</div>
        <div class="chart-wrap"><canvas id="chartConnCod"></canvas></div>
      </div>
      <div class="card wide">
        <h2>Behind-the-meter vs grid by company</h2>
        <div class="hint">Total announced MW per hyperscaler, split by how the electrons reach the datacenter.</div>
        <div class="chart-wrap" style="height:360px"><canvas id="chartConnCompany"></canvas></div>
      </div>
    </div>
  </section>

  <section class="panel" id="panel-contracts">
    <!-- Operator-type breakdown ribbon -->
    <div id="opTypeRibbon" style="display:flex; gap:.6rem; margin-bottom:1rem; flex-wrap:wrap;"></div>

    <div class="filters">
      <label>Operator type
        <select id="fOpType"><option value="">All</option></select>
      </label>
      <label>Company
        <select id="fCompany"><option value="">All</option></select>
      </label>
      <label>Type
        <select id="fType"><option value="">All</option></select>
      </label>
      <label>Status
        <select id="fStatus"><option value="">All</option></select>
      </label>
      <label>Connection
        <select id="fConn"><option value="">All</option></select>
      </label>
      <label>Search <input type="text" id="fSearch" placeholder="deal name, counterparty..."></label>
      <span class="sub" id="rowCount"></span>
    </div>
    <div class="table-wrap">
      <table id="contractsTable">
        <thead><tr>
          <th data-k="operator_type">OpType</th>
          <th data-k="company">Operator</th>
          <th data-k="year">Announced</th>
          <th data-k="cod_year">COD</th>
          <th data-k="status">Status</th>
          <th data-k="generation_type">Type</th>
          <th data-k="connection_type">Conn</th>
          <th data-k="capacity_mw" style="text-align:right">MW</th>
          <th data-k="storage_power_mw" style="text-align:right">Storage MW</th>
          <th data-k="storage_energy_mwh" style="text-align:right">Storage MWh</th>
          <th data-k="deal_name">Deal</th>
          <th data-k="counterparty">Counterparty</th>
          <th>Notes</th>
          <th>Src</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </section>

  <section class="panel" id="panel-campuses">
    <div class="camp-hero">
      <div>
        <p class="lead">
          The hyperscaler power-purchase database tracks <b>contracted electrons</b>;
          this view tracks the <b>physical buildings consuming them</b>. Each row is a
          named data-center campus with disclosed or estimated <b>critical-IT load</b>
          (or facility-power where the operator only discloses that), the source citation,
          and where the deal sits between announcement and energization. Triangulating
          the two views together is the only honest way to answer <i>"how much real GW
          gets added per year?"</i> — contracts can overstate (frameworks, double counts);
          campuses can understate (private builds we don't see).
        </p>
      </div>
      <div class="camp-stats">
        <div class="camp-stat live">
          <div class="num"><span id="cs-energized">—</span><span class="gw"> MW live</span></div>
          <div class="lab">Energized today</div>
        </div>
        <div class="camp-stat">
          <div class="num"><span id="cs-phase1">—</span><span class="gw"> MW</span></div>
          <div class="lab">Phase-1 commitment</div>
        </div>
        <div class="camp-stat gap">
          <div class="num"><span id="cs-planned">—</span><span class="gw"> MW</span></div>
          <div class="lab">Full-build planned</div>
        </div>
        <div class="camp-stat">
          <div class="num"><span id="cs-count">—</span></div>
          <div class="lab">Tracked campuses</div>
        </div>
      </div>
    </div>

    <h3 class="camp-section-title">Pipeline by hyperscaler<span class="rule"></span><span style="font-weight:400;text-transform:none;letter-spacing:0">energized → phase-1 → full plan · click a bar for sources</span></h3>
    <div class="pipeline-list" id="pipelineList"></div>
    <div class="pipe-legend">
      <span><span class="sw" style="background:#0E7B5B"></span>Energized today</span>
      <span><span class="sw" style="background:#7FB5A6"></span>Phase-1 commitment</span>
      <span><span class="sw" style="background:#E2E2E5"></span>Full planned build</span>
    </div>

    <h3 class="camp-section-title" style="margin-top:2rem">All tracked campuses<span class="rule"></span></h3>
    <div class="camp-filters">
      <label>Hyperscaler
        <select id="campF1"><option value="">All</option></select>
      </label>
      <label>Status
        <select id="campF2"><option value="">All</option></select>
      </label>
      <label>Country
        <select id="campF3"><option value="">All</option></select>
      </label>
      <label>Search <input type="text" id="campSearch" placeholder="campus, city, tenant…"></label>
      <span class="count" id="campCount"></span>
    </div>
    <div class="camp-table-wrap">
      <table id="campTable">
        <thead><tr>
          <th data-k="campus_id">ID</th>
          <th data-k="campus_name">Campus</th>
          <th data-k="hyperscaler">Operator</th>
          <th data-k="primary_tenant">Tenant</th>
          <th data-k="state_or_region">Location</th>
          <th data-k="status">Status</th>
          <th data-k="capacity_definition">Def</th>
          <th data-k="it_load_mw_energized" class="r">Live MW</th>
          <th data-k="it_load_mw_planned" class="r">Plan MW</th>
          <th data-k="cod_phase1_year" class="r">P1 yr</th>
          <th data-k="cod_full_year" class="r">Full yr</th>
          <th>Power source</th>
          <th>Notes</th>
          <th>Src</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </section>

  <section class="panel" id="panel-disclosures">
    <div class="camp-hero">
      <div>
        <p class="lead">
          What each operator <b>says it is building</b>, in its own words — capacity levels
          from earnings calls, releases, and filings, split into the operator's own
          <b>planned / under construction / operational</b> stages. Self-reported figures,
          kept separate from the per-site estimates on the Campuses tab; the coverage check
          below compares the two without ever mixing them. Quarters where an operator
          discloses nothing are recorded as exactly that.
        </p>
      </div>
      <div class="camp-stats">
        <div class="camp-stat"><div class="num"><span id="dsOps">—</span></div><div class="lab">Operators tracked</div></div>
        <div class="camp-stat live"><div class="num"><span id="dsRows">—</span></div><div class="lab">Disclosure rows</div></div>
        <div class="camp-stat"><div class="num"><span id="dsQtrs">—</span></div><div class="lab">Quarters covered</div></div>
        <div class="camp-stat gap"><div class="num"><span id="dsSilent">—</span></div><div class="lab">Silent operator-quarters</div></div>
      </div>
    </div>

    <h3 class="camp-section-title">Latest disclosed capacity by stage<span class="rule"></span><span style="font-weight:400;text-transform:none;letter-spacing:0">each stage carries its own as-of quarter · click a segment for sources</span></h3>
    <div class="pipeline-list" id="dsBars"></div>
    <div class="pipe-legend">
      <span><span class="sw" style="background:#0E7B5B"></span>Operational</span>
      <span><span class="sw" style="background:#7FB5A6"></span>Under construction</span>
      <span><span class="sw" style="background:#E2E2E5"></span>Planned (shown net)</span>
    </div>
    <p class="lead" style="margin:.6rem 0 0;max-width:none;font-size:.78rem">
      Planned is shown net — disclosed contracted or secured capacity less anything already
      operational or under construction, never below zero. Stages carry their own as-of dates;
      a stage older than the operator's latest reporting quarter is marked carried.
    </p>

    <h3 class="camp-section-title" style="margin-top:2rem">Quarter by quarter — as disclosed<span class="rule"></span><span style="font-weight:400;text-transform:none;letter-spacing:0">strict per-quarter levels; gaps mean no disclosure, not zero · click a column for sources</span></h3>
    <div class="camp-filters" style="margin-bottom:.75rem">
      <label>Operator <select id="dsQoQSelect"></select></label>
      <span class="count" id="dsQoQNote"></span>
    </div>
    <div class="chart-wrap" style="height:300px;margin-bottom:1.4rem"><canvas id="dsQoQChart"></canvas></div>

    <h3 class="camp-section-title" style="margin-top:2rem">Disclosure register<span class="rule"></span><span style="font-weight:400;text-transform:none;letter-spacing:0">the operator's own words, linked to the filing</span></h3>
    <div class="camp-table-wrap" style="max-height:560px">
      <table id="dsTable">
        <thead><tr>
          <th>Operator</th><th>Quarter</th><th>Their label</th><th>Stage</th>
          <th class="r">MW</th><th>Basis</th><th>Site</th><th>Quote</th><th>Src</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>

    <h3 class="camp-section-title" style="margin-top:2rem">Coverage check — what operators say vs the campuses we track<span class="rule"></span><span style="font-weight:400;text-transform:none;letter-spacing:0">click either bar for sources</span></h3>
    <div class="pipeline-list" id="dsCoverage"></div>
    <p class="lead" style="margin:.6rem 0 0;max-width:none;font-size:.78rem" id="dsCoverageNote"></p>
  </section>

  <section class="panel" id="panel-commentary">
    <div class="camp-hero">
      <div>
        <p class="lead">
          Qualitative statements are tracked as <b>dated narrative anchors</b>, not
          energized-MW evidence. Each item keeps the speaker, source route, load-stage
          signal, and capacity basis so management commentary, utility load planning,
          grid bottlenecks, and supply-chain constraints can be compared on one chronology.
        </p>
      </div>
      <div class="camp-stats">
        <div class="camp-stat">
          <div class="num"><span id="ql-count">-</span></div>
          <div class="lab">Statement anchors</div>
        </div>
        <div class="camp-stat">
          <div class="num"><span id="ql-orgs">-</span></div>
          <div class="lab">Organizations</div>
        </div>
        <div class="camp-stat live">
          <div class="num"><span id="ql-ready">-</span></div>
          <div class="lab">Ready/live anchors</div>
        </div>
        <div class="camp-stat gap">
          <div class="num"><span id="ql-buckets">-</span></div>
          <div class="lab">Timeline buckets</div>
        </div>
      </div>
    </div>

    <h3 class="camp-section-title">Timestamped commentary<span class="rule"></span><span style="font-weight:400;text-transform:none;letter-spacing:0">statement date -> load-stage signal -> evidence basis</span></h3>
    <div class="chart-wrap" style="height:260px; margin-bottom:1rem;"><canvas id="qlBucketChart"></canvas></div>

    <div class="ql-filters">
      <label>Category
        <select id="qlFCategory"><option value="">All</option></select>
      </label>
      <label>Taxonomy
        <select id="qlFTaxonomy"><option value="">All</option></select>
      </label>
      <label>Stage
        <select id="qlFStage"><option value="">All</option></select>
      </label>
      <label>Search <input type="text" id="qlSearch" placeholder="organization, speaker, source..."></label>
      <span class="count" id="qlRowCount"></span>
    </div>

    <div class="ql-layout">
      <div class="ql-timeline" id="qlTimeline"></div>
      <div class="ql-table-wrap">
        <table id="qlTable">
          <thead><tr>
            <th>Date</th>
            <th>Bucket</th>
            <th>Organization</th>
            <th>Speaker</th>
            <th>Taxonomy</th>
            <th>Stage</th>
            <th>Basis</th>
            <th>Commentary</th>
            <th>Src</th>
          </tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
  </section>

  <section class="panel" id="panel-eia">
    <div class="camp-hero">
      <div>
        <p class="lead">
          <b>EIA Form 860M</b> publishes a monthly snapshot of every US generator
          that has filed with EIA — capacity, fuel, planned online date, and a
          <b>construction-status code</b> from "regulatory approvals not initiated"
          all the way to "construction complete." We track the full file across
          quarterly data releases spanning late 2022–2026. The signal isn't a probability estimate;
          it's the <b>empirical migration of MW between status tiers</b> over time.
        </p>
        <p class="lead" style="margin-top:.6rem">
          The big finding from this dataset: the US planned-generation pipeline
          <b>roughly doubled across the tracked history</b>, but much of the
          new MW is still stuck at the back of the funnel. The key question is
          whether each technology's pipeline is migrating toward construction
          complete or merely accumulating in planned and permitting stages.
        </p>
      </div>
      <div class="camp-stats">
        <div class="camp-stat live">
          <div class="num"><span id="es-current">—</span><span class="gw"> GW</span></div>
          <div class="lab">Latest pipeline (2026-03)</div>
        </div>
        <div class="camp-stat gap">
          <div class="num"><span id="es-growth">—</span><span class="gw">%</span></div>
          <div class="lab">Growth vs first release</div>
        </div>
        <div class="camp-stat">
          <div class="num"><span id="es-vintages">—</span></div>
          <div class="lab">Data releases tracked</div>
        </div>
        <div class="camp-stat">
          <div class="num"><span id="es-count">—</span></div>
          <div class="lab">Generator-snapshots</div>
        </div>
      </div>
    </div>

    <h3 class="camp-section-title">Pipeline evolution by status<span class="rule"></span><span style="font-weight:400;text-transform:none;letter-spacing:0">total US planned MW, stacked by EIA construction status</span></h3>
    <p class="lead" style="margin:0 0 1rem; max-width:none">
      Each bar is one EIA-860M monthly snapshot from 2024–2026. The bottom of the
      stack is shovels-in-the-ground; the top is just-announced.
      <b>If the pipeline is healthy</b>, the dark green slice (construction complete)
      grows over time and the gray slice (planned only) shrinks. If announcements
      are outpacing construction, the pile gets top-heavy.
    </p>
    <div class="chart-wrap" style="height:380px; margin-bottom:1.4rem;"><canvas id="eiaStatusVintageChart"></canvas></div>

    <h3 class="camp-section-title" style="margin-top:2rem">Pipeline evolution by technology<span class="rule"></span></h3>
    <div class="chart-wrap" style="height:340px; margin-bottom:1.4rem;"><canvas id="eiaTechVintageChart"></canvas></div>

    <h3 class="camp-section-title" style="margin-top:2rem">Construction stage by generation type over time<span class="rule"></span><span style="font-weight:400;text-transform:none;letter-spacing:0">construction complete → under construction → planned / permitting</span></h3>
    <p class="lead" style="margin:0 0 1rem; max-width:none">
      This collapses EIA's detailed status codes into three buildout stages and shows the stage mix
      for one technology across every data release. It is the time-series version of the latest
      snapshot chart below.
    </p>
    <div class="camp-filters" style="margin-bottom:.75rem;">
      <label>Generation type
        <select id="eiaStageTechSelect"></select>
      </label>
      <span class="count" id="eiaStageTechSummary"></span>
    </div>
    <div class="chart-wrap" style="height:360px; margin-bottom:.6rem;"><canvas id="eiaStageTechVintageChart"></canvas></div>
    <div id="eiaStageTechTable" style="margin-bottom:2rem;"></div>

    <h3 class="camp-section-title" style="margin-top:2rem">Completed vs announced — raw MW per window<span class="rule"></span><span style="font-weight:400;text-transform:none;letter-spacing:0">no annualization, just the actual flow during each window</span></h3>
    <p class="lead" style="margin:0 0 1rem; max-width:none">
      <b>Completed</b> = MW that began commercial operation during this window
      (read directly from EIA's Operating Year/Month field for every plant in
      the operating fleet). <b>Newly announced</b> = MW that appeared in the
      planned-additions table for the first time. <b>Cancelled</b> = MW that
      disappeared from the planned set. All values are raw MW — bar heights
      naturally reflect window length, so longer windows show bigger bars.
      Sum across all 2025 windows = ~56 GW completed, matching SEIA / Wood Mackenzie.
    </p>
    <div class="chart-wrap" style="height:380px; margin-bottom:.6rem;"><canvas id="eiaTransitionChart"></canvas></div>
    <div id="eiaTransitionTable" style="margin-bottom:2rem;"></div>

    <h3 class="camp-section-title" style="margin-top:2.4rem">Calendar-year totals — Completed vs Announced<span class="rule"></span><span style="font-weight:400;text-transform:none;letter-spacing:0">sum of all quarterly windows in each year</span></h3>
    <p class="lead" style="margin:0 0 1rem; max-width:none">
      Calendar-year roll-up. Each year sums its four quarter-end windows
      (Dec→Mar, Mar→Jun, Jun→Sep, Sep→Dec). 2026 is partial — Q1 only.
      <b>Completed totals match SEIA / Wood Mackenzie published numbers</b> (~57 GW for 2025).
    </p>
    <div class="chart-wrap" style="height:340px; margin-bottom:.6rem;"><canvas id="eiaYearChart"></canvas></div>
    <div id="eiaYearTable" style="margin-bottom:2rem;"></div>

    <h3 class="camp-section-title" style="margin-top:2.4rem">QoQ % change — Completed vs Announced<span class="rule"></span><span style="font-weight:400;text-transform:none;letter-spacing:0">raw quarter-over-quarter change in each flow</span></h3>
    <p class="lead" style="margin:0 0 1rem; max-width:none">
      For each quarter, the % change vs the prior quarter — both for plants
      that came online (Completed) and for new MW added to the planned set
      (Announced). No smoothing, no annualization. Crossover points and sign
      flips are the signals to watch.
    </p>
    <div class="chart-wrap" style="height:360px; margin-bottom:.6rem;"><canvas id="eiaQoQChart"></canvas></div>
    <div id="eiaQoQTable" style="margin-bottom:2rem;"></div>

    <h3 class="camp-section-title" style="margin-top:2.4rem">Latest snapshot — status ladder<span class="rule"></span><span style="font-weight:400;text-transform:none;letter-spacing:0">where each MW sits in the funnel today (2026-03)</span></h3>
    <div class="pipeline-list" id="eiaTierList"></div>

    <h3 class="camp-section-title" style="margin-top:2.4rem">Latest snapshot — construction status by generation type<span class="rule"></span><span style="font-weight:400;text-transform:none;letter-spacing:0">EIA-860M status tiers crossed with technology</span></h3>
    <p class="lead" style="margin:0 0 1rem; max-width:none">
      EIA-860M does disclose construction status for each planned generator. This chart crosses those
      status tiers with technology, so you can see which fuels are actually near completion versus still
      sitting in approvals or planning.
    </p>
    <div class="chart-wrap" style="height:360px; margin-bottom:.6rem;"><canvas id="eiaStatusTechChart"></canvas></div>
    <div id="eiaStatusTechTable" style="margin-bottom:2rem;"></div>

    <h3 class="camp-section-title" style="margin-top:2.4rem">Federal pipeline by generation type<span class="rule"></span><span style="font-weight:400;text-transform:none;letter-spacing:0">2,214 planned units, all sectors</span></h3>
    <p class="lead" style="margin:0 0 1.2rem; max-width:none">
      Every planned US generator that has filed with EIA, grouped by technology and planned in-service year.
      <b>Left chart</b> shows what was announced; <b>right chart</b> applies the construction-status haircut
      to show what should actually deliver. The shrinkage between the two is where the federal data
      tells you the announcement-to-energization gap is real.
    </p>
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:1.4rem; margin-bottom:1.5rem;">
      <div>
        <div style="display:flex; align-items:baseline; justify-content:space-between; margin-bottom:.5rem;">
          <span style="font-size:.78rem; text-transform:uppercase; letter-spacing:.1em; color:var(--muted); font-weight:600;">Announced</span>
          <span style="font-size:.78rem; color:var(--muted);"><span id="eiaAnnTotal" style="color:var(--ink); font-weight:600;">—</span> GW total</span>
        </div>
        <div class="chart-wrap" style="height:340px;"><canvas id="eiaAnnChart"></canvas></div>
      </div>
      <div>
        <div style="display:flex; align-items:baseline; justify-content:space-between; margin-bottom:.5rem;">
          <span style="font-size:.78rem; text-transform:uppercase; letter-spacing:.1em; color:#0E7B5B; font-weight:600;">Probability-weighted</span>
          <span style="font-size:.78rem; color:var(--muted);"><span id="eiaExpTotal" style="color:#0E7B5B; font-weight:600;">—</span> GW total</span>
        </div>
        <div class="chart-wrap" style="height:340px;"><canvas id="eiaExpChart"></canvas></div>
      </div>
    </div>

    <h3 class="camp-section-title" style="margin-top:2.4rem">By technology — total US pipeline 2026–2032<span class="rule"></span></h3>
    <div class="pipeline-list" id="eiaTechList"></div>

    <h3 class="camp-section-title" style="margin-top:2.4rem">Top 12 states by planned MW<span class="rule"></span><span style="font-weight:400;text-transform:none;letter-spacing:0">where the buildout is concentrated</span></h3>
    <div class="camp-table-wrap" style="max-height:480px">
      <table id="eiaStateTable">
        <thead><tr>
          <th>State</th>
          <th class="r">Solar</th>
          <th class="r">Storage</th>
          <th class="r">Wind</th>
          <th class="r">Gas</th>
          <th class="r">Nuclear</th>
          <th class="r">Other</th>
          <th class="r">Total announced</th>
          <th class="r">Expected</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </section>

  <section class="panel" id="panel-costs">
    <div class="grid">
      <div class="card wide">
        <h2>LCOE ($/MWh) — unsubsidized, US</h2>
        <div class="hint">Mid-point; hover a bar to see low/high range.</div>
        <div class="chart-wrap"><canvas id="chartLcoe"></canvas></div>
      </div>
      <div class="card">
        <h2>Gas plant CAPEX ($/kW)</h2>
        <div class="hint">By plant type and report year. <b>Click any dot</b> to open its source.</div>
        <div class="chart-wrap"><canvas id="chartGasCapex"></canvas></div>
      </div>
      <div class="card">
        <h2>Renewable &amp; nuclear CAPEX ($/kW)</h2>
        <div class="hint"><b>Click any dot</b> to open its source.</div>
        <div class="chart-wrap"><canvas id="chartRenCapex"></canvas></div>
      </div>
    </div>
  </section>

  <section class="panel" id="panel-sources">
    <div class="card">
      <h2>Source index</h2>
      <div class="hint">Rightmost column shows how many fact rows cite this source.</div>
      <div class="table-wrap" style="max-height:700px">
        <table id="sourcesTable">
          <thead><tr>
            <th>ID</th><th>Publisher</th><th>Title</th><th>Date</th><th>Kind</th><th>URL</th><th style="text-align:right"># facts</th>
          </tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
  </section>
</main>

<script>
const DATA = __DATA__;
const SRC = DATA.sources;

// ---- Color palette by generation type ----
const TYPE_COLORS = {
  'Gas':'#3A3A3C', 'Gas+CCS':'#54545A',
  'Fuel Cell':'#A08C72',
  'Nuclear':'#8273B5',
  'Solar':'#E2A63D', 'Solar+Storage':'#C77F35',
  'Wind':'#79A6CE',
  'Geothermal':'#C26B4E',
  'Storage':'#2F8488',
  'Renewable':'#0E7B5B',
  'Hydro':'#4A7BA6',
  'Other':'#B9B9C0'
};
const CONN_COLORS = { 'BTM':'#C26B4E', 'Grid':'#2F8488', 'Unknown':'#B9B9C0' };
const CLEAN_TYPES = new Set(['Solar','Wind','Nuclear','Fuel Cell','Storage','Geothermal','Hydro','Solar+Storage','Renewable']);
function colorFor(t){ return TYPE_COLORS[t] || '#B9B9C0'; }

// ---- Source popover: click a bar or chart column to see the rows + citations behind it ----
const srcPop = (() => {
  const el = document.createElement('div');
  el.id = 'srcPop';
  document.body.appendChild(el);
  const esc = s => String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  function srcLine(sid){
    if (!sid) return '<span>no source on file (recorded absence)</span>';
    const s = SRC[sid];
    if (!s) return esc(sid);
    const date = s.pub_date ? ` · ${esc(s.pub_date)}` : '';
    return `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || sid)}</a>` +
           ` — ${esc(s.publisher || '')}${date} · ${esc(sid)}`;
  }
  const close = () => el.classList.remove('open');
  function show(evt, title, sub, items){
    el.innerHTML = `<button class="x" aria-label="Close">✕</button><h4>${esc(title)}</h4>` +
      (sub ? `<div class="psub">${esc(sub)}</div>` : '') +
      (items.length ? items.join('') : '<div class="it" style="color:var(--muted)">no underlying rows</div>');
    el.querySelector('.x').addEventListener('click', close);
    el.classList.add('open');
    el.scrollTop = 0;
    const x = Math.max(16, Math.min(evt.clientX + 10, window.innerWidth  - el.offsetWidth  - 16));
    const y = Math.max(16, Math.min(evt.clientY + 12, window.innerHeight - el.offsetHeight - 16));
    el.style.left = x + 'px'; el.style.top = y + 'px';
    evt.stopPropagation();
  }
  document.addEventListener('click', e => {
    if (el.classList.contains('open') && !el.contains(e.target)) close();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  return { show, close, esc, srcLine };
})();

const POP_STAGE = { planned:'Planned', under_construction:'Under construction',
                    operational:'Operational', none_disclosed:'None disclosed' };
const popQ = q => q ? String(q).replace(/^20(\d\d)Q(\d)$/, '$1Q$2') : '';

function disclosureSrcItem(r){
  const e = srcPop.esc;
  const mw = r.mw_value != null ? Math.round(r.mw_value).toLocaleString() + ' MW' : '—';
  const label = r.stage_verbatim || POP_STAGE[r.stage_normalized] || r.stage_normalized;
  const site = r.component_label ? ` · ${e(r.component_label)}` : '';
  return `<div class="it">
    <div class="row1"><span><b>${e(r.fiscal_label || popQ(r.as_of_quarter))}</b> · ${e(label)}${site}</span><span class="mw">${mw}</span></div>
    ${r.verbatim_quote ? `<div class="quote">“${e(r.verbatim_quote)}”</div>` : ''}
    <div class="meta">${srcPop.srcLine(r.source_id)}</div>
  </div>`;
}

function campusSrcItem(r){
  const e = srcPop.esc;
  const lvl = r.it_load_mw_planned != null ? [r.it_load_mw_planned, 'planned']
            : r.it_load_mw_phase1  != null ? [r.it_load_mw_phase1,  'phase-1']
            : r.it_load_mw_energized != null ? [r.it_load_mw_energized, 'live'] : null;
  const mw = lvl ? `${Math.round(lvl[0]).toLocaleString()} MW ${lvl[1]}` : '—';
  const place = [r.city, r.state_or_region, r.country].filter(Boolean).join(', ');
  const status = String(r.status || '').replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase();
  return `<div class="it">
    <div class="row1"><span><b>${e(r.campus_name)}</b> · ${e(status)}</span><span class="mw">${mw}</span></div>
    ${place ? `<div class="quote" style="font-style:normal">${e(place)}${r.primary_tenant ? ' · tenant: ' + e(r.primary_tenant) : ''}</div>` : ''}
    <div class="meta">${srcPop.srcLine(r.source_id)}</div>
  </div>`;
}

Chart.defaults.color = '#6E6E73';
Chart.defaults.borderColor = '#ECECEE';
Chart.defaults.font.family = "'Inter Tight', 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif";

// ---- Freshness signal: rows announced within the last 7 days glow red ----
const FRESH_WINDOW_DAYS = 7;
const GLOW_COLOR = '#0E7B5B';    // green ring marks capacity announced in the last 7 days
function isNew(row) {
  const ad = row && row.announced_date;
  if (!ad) return false;
  // accept YYYY-MM-DD or YYYY-MM; normalize to a Date
  const d = new Date(ad.length === 7 ? ad + '-15' : ad);
  if (isNaN(d.getTime())) return false;
  const diffDays = (Date.now() - d.getTime()) / 86400000;
  return diffDays >= 0 && diffDays <= FRESH_WINDOW_DAYS;
}
// For each bucket on the x-axis (or y-axis for horizontal charts), compute
// ONLY the MW contributed by fresh rows. Parallel to the dataset's `data`
// array. Used by the plugin below to glow just the top slice of each
// segment, not the whole segment.
function freshMW(rows, buckets, bucketKey) {
  return buckets.map(b =>
    rows.filter(r => r[bucketKey] === b && isNew(r))
        .reduce((s, r) => s + (r.capacity_mw || 0), 0));
}

// Chart.js plugin — draws a flat green ring around just the fresh portion of
// each stacked bar segment (no fill — base category color shows through).
// For vertical bars it's the top slice; for horizontal bars, the stack.
Chart.register({
  id: 'freshGlow',
  afterDatasetsDraw(chart) {
    const ctx = chart.ctx;
    const isHorizontal = chart.options.indexAxis === 'y';
    chart.data.datasets.forEach((ds, di) => {
      if (!ds.freshData || !ds.freshData.some(v => v > 0)) return;
      const meta = chart.getDatasetMeta(di);
      meta.data.forEach((bar, i) => {
        const total = ds.data[i];
        const fresh = ds.freshData[i];
        if (!total || !fresh) return;
        const frac = Math.min(1, fresh / total);
        const { x, y, base, width, height } = bar.getProps(
          ['x', 'y', 'base', 'width', 'height'], true);

        // Compute the fresh-slice rectangle in pixel space.
        let rx, ry, rw, rh;
        if (isHorizontal) {
          // Segment runs from base (left edge of this segment) to x (right
          // edge). Fresh slice sits on the right side.
          const segW = Math.abs(x - base);
          rw = segW * frac;
          rx = (x > base) ? (x - rw) : base;
          ry = y - height / 2;
          rh = height;
        } else {
          // Segment runs from base (bottom) down to y (top, smaller pixel-y).
          // Fresh slice sits at the top.
          const segH = Math.abs(y - base);
          rh = segH * frac;
          rx = x - width / 2;
          ry = (y < base) ? y : (base - rh);
          rw = width;
        }

        ctx.save();
        ctx.strokeStyle = GLOW_COLOR;
        ctx.lineWidth = 2;
        ctx.globalAlpha = 0.95;
        ctx.strokeRect(rx + 1, ry + 1, rw - 2, rh - 2);
        ctx.restore();
      });
    });
  }
});


// ---- Chart: storage energy by announcement year ----
(function(){
  const C = DATA.contracts.filter(r => r.storage_energy_mwh);
  const el = document.getElementById('chartStorageYear');
  if (!el || !C.length) return;
  const years = [...new Set(C.map(r=>r.year))].sort((a,b)=>a-b);
  const companies = [...new Set(C.map(r=>r.company))].sort();
  const ds = companies.map(co => {
    const rowsC = C.filter(r => r.company === co);
    return {
      label: co,
      data: years.map(y => rowsC.filter(r=>r.year===y)
                                .reduce((s,r)=>s+(r.storage_energy_mwh||0),0) / 1000),
      backgroundColor: '#2F8488',
      borderWidth: 0
    };
  }).filter(d => d.data.some(v=>v>0));
  new Chart(el, {
    type:'bar',
    data:{ labels:years, datasets: ds },
    options:{ maintainAspectRatio:false, responsive:true,
      scales:{ x:{stacked:true}, y:{stacked:true, ticks:{callback:v=>v.toLocaleString()+' GWh'}} },
      plugins:{ legend:{position:'bottom', labels:{boxWidth:10, boxHeight:10}},
                tooltip:{callbacks:{label:c=>`${c.dataset.label}: ${c.parsed.y.toLocaleString()} GWh storage energy`}} }
    }
  });
})();

// ---- Overview bento tiles ----
Chart.register({
  id: 'bentoTotals',
  afterDatasetsDraw(chart) {
    if (chart.canvas.id !== 'chartBento' && chart.canvas.id !== 'chartBentoQ') return;
    const { ctx } = chart;
    const totals = chart.data.labels.map((_, i) =>
      chart.data.datasets.reduce((s, d) => s + (d.data[i] || 0), 0));
    ctx.save();
    ctx.font = "600 11px 'Inter Tight', 'Inter', sans-serif";
    ctx.fillStyle = '#1D1D1F';
    ctx.textAlign = 'center';
    totals.forEach((tot, i) => {
      if (!tot) return;
      const x = chart.scales.x.getPixelForValue(i);
      const y = chart.scales.y.getPixelForValue(tot);
      ctx.fillText(Math.round(tot).toLocaleString(), x, y - 6);
    });
    ctx.restore();
  }
});
(function(){
  const C = DATA.contracts;
  const HS = C.filter(r => r.operator_type === 'Hyperscaler');
  const totalMW = C.reduce((s,r)=>s+(r.capacity_mw||0),0);
  const hsMW = HS.reduce((s,r)=>s+(r.capacity_mw||0),0);
  const gasMW = C.filter(r=>r.generation_type==='Gas'||r.generation_type==='Gas+CCS')
                 .reduce((s,r)=>s+(r.capacity_mw||0),0);
  const stoPow = C.reduce((s,r)=>s+(r.storage_power_mw||0),0);
  const stoEgy = C.reduce((s,r)=>s+(r.storage_energy_mwh||0),0);
  const cutoff = Date.now() - 45*86400000;
  const addMW = C.filter(r=>{
    const ad = r.announced_date; if (!ad) return false;
    const d = new Date(ad.length===7 ? ad+'-15' : ad);
    return !isNaN(d.getTime()) && d.getTime() >= cutoff;
  }).reduce((s,r)=>s+(r.capacity_mw||0),0);

  document.getElementById('bHeroGW').textContent =
    (totalMW/1000).toLocaleString(undefined,{minimumFractionDigits:1,maximumFractionDigits:1});
  document.getElementById('bHeroSub').textContent =
    `${C.length} agreements · ${new Set(C.map(r=>r.company)).size} operators`;
  document.getElementById('bHeroKvs').innerHTML =
    `<div class="kv"><span style="color:#6E6E73">Six hyperscalers</span><b>${(hsMW/1000).toFixed(1)} GW</b></div>` +
    `<div class="kv"><span style="color:#6E6E73">Battery storage</span><b>${(stoPow/1000).toFixed(1)} GW · ${Math.round(stoEgy/1000)} GWh</b></div>` +
    `<div class="kv"><span style="color:#6E6E73">Added past 45 days</span><b style="color:#0E7B5B">+${Math.round(addMW).toLocaleString()} MW</b></div>`;
  document.getElementById('bSrcCount').textContent = Object.keys(SRC).length;

  const pct = 100*(totalMW-gasMW)/totalMW;
  const circ = 2*Math.PI*40, arc = circ*pct/100;
  document.getElementById('bDonut').innerHTML =
    `<circle cx="50" cy="50" r="40" fill="none" stroke="#E9E9EC" stroke-width="11"/>` +
    `<circle cx="50" cy="50" r="40" fill="none" stroke="#0E7B5B" stroke-width="11" stroke-dasharray="${arc.toFixed(1)} ${circ.toFixed(1)}" stroke-linecap="round" transform="rotate(-90 50 50)"/>` +
    `<text x="50" y="48" text-anchor="middle" font-size="17" font-weight="600" fill="#1D1D1F">${pct.toFixed(1)}%</text>` +
    `<text x="50" y="63" text-anchor="middle" font-size="8" fill="#6E6E73">clean + storage</text>`;

  const byOp = {};
  HS.forEach(r => { byOp[r.company] = (byOp[r.company]||0) + (r.capacity_mw||0); });
  const ops = Object.entries(byOp).sort((a,b)=>b[1]-a[1]);
  const maxOp = ops.length ? ops[0][1] : 1;
  document.getElementById('bOps').innerHTML = ops.map(([co,mw]) =>
    `<span style="font-weight:500">${co}</span>` +
    `<div class="bbar" style="width:${Math.max(2,(100*mw/maxOp)).toFixed(0)}%"></div>` +
    `<span style="text-align:right;color:#6E6E73;font-variant-numeric:tabular-nums">${(mw/1000).toFixed(1)}</span>`
  ).join('');

  const latest = C.filter(r=>r.announced_date)
                  .sort((a,b)=>String(b.announced_date).localeCompare(String(a.announced_date)))
                  .slice(0,5);
  document.getElementById('bLatest').innerHTML = latest.map(r => {
    const nm = (r.deal_name||'').length > 30 ? r.deal_name.slice(0,29)+'…' : r.deal_name;
    const mw = r.capacity_mw != null ? '+'+Math.round(r.capacity_mw).toLocaleString() : '—';
    return `<div class="blat"><span><span class="bdot" style="background:${colorFor(r.generation_type)}"></span><b>${nm}</b></span>` +
           `<span style="color:#6E6E73;font-variant-numeric:tabular-nums">${mw}</span></div>`;
  }).join('');

  const pre = HS.filter(r => r.year <= 2023);
  const post = HS.filter(r => r.year >= 2024);
  const years = [...new Set(post.map(r=>r.year))].sort((a,b)=>a-b);
  const labels = ['21–23', ...years.map(String)];
  const preMW = pre.reduce((s,r)=>s+(r.capacity_mw||0),0);
  const types = [...new Set(post.map(r=>r.generation_type))]
    .filter(ty => post.some(r => r.generation_type===ty && (r.capacity_mw||0) > 0))
    .sort((a,b) =>
      post.filter(r=>r.generation_type===b).reduce((s,r)=>s+(r.capacity_mw||0),0) -
      post.filter(r=>r.generation_type===a).reduce((s,r)=>s+(r.capacity_mw||0),0));
  const ds = [{ label:'2021–23 (all types)', data:[preMW, ...years.map(()=>0)],
                backgroundColor:'#B9B9C0', borderWidth:0 }]
    .concat(types.map(ty => ({
      label: ty,
      data: [0, ...years.map(y => post.filter(r=>r.year===y && r.generation_type===ty)
                                      .reduce((s,r)=>s+(r.capacity_mw||0),0))],
      backgroundColor: colorFor(ty), borderWidth: 0 })));
  new Chart(document.getElementById('chartBento'), {
    type:'bar',
    data:{ labels, datasets: ds },
    options:{ maintainAspectRatio:false, responsive:true,
      layout:{ padding:{ top:18 } },
      scales:{ x:{ stacked:true, grid:{ display:false } },
               y:{ stacked:true, ticks:{ callback:v=>v.toLocaleString() } } },
      plugins:{ legend:{ display:false },
                tooltip:{ callbacks:{ label:c=>`${c.dataset.label}: ${c.parsed.y.toLocaleString()} MW` } } }
    }
  });
  document.getElementById('bentoLegend').innerHTML =
    types.map(ty => `<span><span class="bdot" style="background:${colorFor(ty)}"></span>${ty}</span>`).join('') +
    `<span><span class="bdot" style="background:#B9B9C0"></span>2021–23 (all types)</span>`;
})();

// ---- Overview bento: same six hyperscalers, by announcement quarter ----
(function(){
  const C = DATA.contracts;
  const HS = C.filter(r => r.operator_type === 'Hyperscaler');
  const pre = HS.filter(r => r.year <= 2023);
  const post = HS.filter(r => r.year >= 2024);
  // post-2024 rows with a resolvable announcement month (YYYY-MM or YYYY-MM-DD)
  const dated = post.filter(r => r.announced_date && String(r.announced_date).length >= 7);
  const undated = post.filter(r => !(r.announced_date && String(r.announced_date).length >= 7));
  // bucket by canonical year (matches the annual chart) + quarter from the disclosed month
  const qkey = r => {
    const m = parseInt(String(r.announced_date).slice(5,7), 10);
    return r.year * 10 + (Math.floor((m-1)/3) + 1);   // e.g. 20241 → 2024 Q1, sortable
  };
  const qlabel = k => `'${String(Math.floor(k/10)).slice(2)} Q${k%10}`;
  const qkeys = [...new Set(dated.map(qkey))].sort((a,b)=>a-b);
  const labels = ['21–23', ...qkeys.map(qlabel)];
  const preMW = pre.reduce((s,r)=>s+(r.capacity_mw||0),0);
  const types = [...new Set(dated.map(r=>r.generation_type))]
    .filter(ty => dated.some(r => r.generation_type===ty && (r.capacity_mw||0) > 0))
    .sort((a,b) =>
      dated.filter(r=>r.generation_type===b).reduce((s,r)=>s+(r.capacity_mw||0),0) -
      dated.filter(r=>r.generation_type===a).reduce((s,r)=>s+(r.capacity_mw||0),0));
  const ds = [{ label:'2021–23 (all types)', data:[preMW, ...qkeys.map(()=>0)],
                backgroundColor:'#B9B9C0', borderWidth:0 }]
    .concat(types.map(ty => ({
      label: ty,
      data: [0, ...qkeys.map(k => dated.filter(r=>qkey(r)===k && r.generation_type===ty)
                                       .reduce((s,r)=>s+(r.capacity_mw||0),0))],
      backgroundColor: colorFor(ty), borderWidth: 0 })));
  new Chart(document.getElementById('chartBentoQ'), {
    type:'bar',
    data:{ labels, datasets: ds },
    options:{ maintainAspectRatio:false, responsive:true,
      layout:{ padding:{ top:18 } },
      scales:{ x:{ stacked:true, grid:{ display:false } },
               y:{ stacked:true, ticks:{ callback:v=>v.toLocaleString() } } },
      plugins:{ legend:{ display:false },
                tooltip:{ callbacks:{ label:c=>`${c.dataset.label}: ${c.parsed.y.toLocaleString()} MW` } } }
    }
  });
  document.getElementById('bentoQLegend').innerHTML =
    types.map(ty => `<span><span class="bdot" style="background:${colorFor(ty)}"></span>${ty}</span>`).join('') +
    `<span><span class="bdot" style="background:#B9B9C0"></span>2021–23 (all types)</span>`;
  const undMW = undated.reduce((s,r)=>s+(r.capacity_mw||0),0);
  document.getElementById('bentoQNote').textContent = undMW > 0
    ? `+ ${Math.round(undMW).toLocaleString()} MW announced in 2024 onward without a disclosed month — not shown above.`
    : '';
})();

// ---- Operator disclosures panel ----
(function(){
  const D = DATA.disclosures || [];
  const CELLS = DATA.disclosure_cells || [];
  if (!D.length && !CELLS.length) return;
  const STAGE_LBL = { planned:'Planned', under_construction:'Under construction', operational:'Operational', none_disclosed:'None disclosed' };
  const STAGE_BADGE = { planned:'b-announced', under_construction:'b-pending', operational:'b-op', none_disclosed:'b-unknown' };

  document.getElementById('dsOps').textContent = new Set(D.map(r=>r.operator)).size;
  document.getElementById('dsRows').textContent = D.filter(r=>r.stage_normalized!=='none_disclosed').length;
  document.getElementById('dsQtrs').textContent = new Set(D.map(r=>r.as_of_quarter)).size;
  document.getElementById('dsSilent').textContent = D.filter(r=>r.stage_normalized==='none_disclosed').length;

  const qLbl = q => q ? q.replace(/^20(\d\d)Q(\d)$/, "$1Q$2") : '';
  const withCells = CELLS.filter(c => c.stages.operational || c.stages.under_construction || c.stages.planned);
  const totalOf = c => ['operational','under_construction'].reduce((s,k)=>s+((c.stages[k]&&c.stages[k].mw)||0),0) + (c.planned_shown||0);
  const maxTot = Math.max(...withCells.map(totalOf), 1);
  document.getElementById('dsBars').innerHTML = withCells
    .sort((a,b)=>totalOf(b)-totalOf(a))
    .map(c => {
      const segs = [];
      const op = c.stages.operational, uc = c.stages.under_construction;
      let acc = 0;
      const seg = (mw, cls, stage) => { const w = 100*mw/maxTot, l = 100*acc/maxTot; acc += mw;
        return `<div class="seg ${cls}" data-stage="${stage}" style="left:${l}%;width:${w}%"></div>`; };
      if (op) segs.push(seg(op.mw, 'energized', 'operational'));
      if (uc) segs.push(seg(uc.mw, 'phase1', 'under_construction'));
      if (c.planned_shown) segs.push(seg(c.planned_shown, 'planned', 'planned'));
      const tags = [];
      ['operational','under_construction'].forEach(k => { const s = c.stages[k];
        if (s && (s.carried || s.as_of_quarter !== c.latest_quarter)) tags.push(`${STAGE_LBL[k]} as of ${qLbl(s.as_of_quarter)}`); });
      const planned = c.stages.planned;
      if (planned && (planned.carried || planned.as_of_quarter !== c.latest_quarter)) tags.push(`Planned as of ${qLbl(planned.as_of_quarter)}`);
      const basis = planned ? planned.basis : (uc ? uc.basis : (op ? op.basis : ''));
      return `<div class="pipe-row" data-op="${c.operator}">
        <div class="name">${c.operator}<span class="cnt">${qLbl(c.latest_quarter)}${tags.length ? ' · '+tags.join(' · ') : ''}</span></div>
        <div class="pipe-bar click" title="Click for the disclosures and sources behind this bar">${segs.join('')}</div>
        <div class="total"><b>${Math.round(totalOf(c)).toLocaleString()}</b> MW<br><span style="font-size:.64rem">${basis}</span></div>
      </div>`;
    }).join('');

  // Click a stage segment → that operator+stage's disclosure rows; the bar
  // background → all stages. Planned segments note the net-of presentation.
  document.getElementById('dsBars').addEventListener('click', e => {
    const bar = e.target.closest('.pipe-bar');
    const row = e.target.closest('.pipe-row');
    if (!bar || !row || !row.dataset.op) return;
    const op = row.dataset.op;
    const stage = e.target.closest('.seg') ? e.target.closest('.seg').dataset.stage : null;
    const rows = D.filter(r => r.operator === op && r.stage_normalized !== 'none_disclosed' &&
                               (!stage || r.stage_normalized === stage))
      .sort((a,b) => String(b.as_of_date).localeCompare(String(a.as_of_date)));
    const sub = stage === 'planned'
      ? 'disclosed planned figures are gross; the bar shows them net of operational + under construction'
      : 'disclosure rows behind this bar, latest first · the charted figure uses the newest quarter per stage';
    srcPop.show(e, stage ? `${op} — ${POP_STAGE[stage].toLowerCase()}` : `${op} — all disclosed stages`,
      sub, rows.map(disclosureSrcItem));
  });

  const tbody = document.querySelector('#dsTable tbody');
  function cite(sid){ const s = SRC[sid]; if (!sid) return '<span style="color:var(--muted)">—</span>';
    if (!s) return sid;
    return `<a class="cite" href="${s.url}" target="_blank" title="${(s.title||'').replace(/"/g,'&quot;')}">${sid}</a>`; }
  tbody.innerHTML = D.map(r => `<tr>
    <td style="font-weight:500">${r.operator}${r.tenant_operator ? `<span class="cnt" style="font-size:.68rem;color:var(--muted)"> for ${r.tenant_operator}</span>` : ''}</td>
    <td>${r.fiscal_label || qLbl(r.as_of_quarter)}</td>
    <td style="color:var(--muted)">${r.stage_verbatim || '—'}</td>
    <td><span class="badge ${STAGE_BADGE[r.stage_normalized]||'b-unknown'}">${STAGE_LBL[r.stage_normalized]||r.stage_normalized}</span></td>
    <td class="r" style="text-align:right;font-variant-numeric:tabular-nums">${r.mw_value!=null ? Math.round(r.mw_value).toLocaleString() : '—'}</td>
    <td><span class="badge b-unknown">${r.capacity_basis==='None' ? '—' : r.capacity_basis}</span></td>
    <td style="color:var(--muted)">${r.component_label || ''}</td>
    <td style="color:var(--muted);font-size:.74rem;max-width:340px">${(r.verbatim_quote || (r.notes||'').slice(0,160)) }</td>
    <td>${cite(r.source_id)}</td></tr>`).join('');

  const cov = DATA.disclosure_coverage || [];
  const covByOp = Object.fromEntries(cov.map(r=>[r.operator, r.campus_mw]));
  const covCells = CELLS.filter(c => covByOp[c.operator] != null && totalOf(c) > 0);
  const covMax = Math.max(...covCells.flatMap(c=>[totalOf(c), covByOp[c.operator]||0]), 1);
  document.getElementById('dsCoverage').innerHTML = covCells.map(c => {
    const self = totalOf(c), camp = covByOp[c.operator];
    return `<div class="pipe-row" data-op="${c.operator}">
      <div class="name">${c.operator}</div>
      <div>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
          <div class="pipe-bar click" data-kind="self" title="Click for the operator's own disclosures" style="flex:1;height:12px"><div class="seg energized" style="left:0;width:${100*self/covMax}%"></div></div>
          <span style="font-size:.7rem;color:var(--muted);min-width:150px">they say · <b style="color:var(--ink)">${Math.round(self).toLocaleString()}</b> MW</span>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <div class="pipe-bar click" data-kind="camp" title="Click for the campuses we track for this tenant" style="flex:1;height:12px"><div class="seg phase1" style="left:0;width:${100*camp/covMax}%"></div></div>
          <span style="font-size:.7rem;color:var(--muted);min-width:150px">we track · <b style="color:var(--ink)">${Math.round(camp).toLocaleString()}</b> MW</span>
        </div>
      </div>
      <div class="total"></div>
    </div>`;
  }).join('');

  // Coverage bars: "they say" → disclosure rows; "we track" → tenant campuses.
  document.getElementById('dsCoverage').addEventListener('click', e => {
    const bar = e.target.closest('.pipe-bar');
    const row = e.target.closest('.pipe-row');
    if (!bar || !row || !row.dataset.op) return;
    const op = row.dataset.op;
    if (bar.dataset.kind === 'camp') {
      const rows = (DATA.campuses || []).filter(r => r.hyperscaler === op)
        .sort((a,b) => (b.it_load_mw_planned||b.it_load_mw_phase1||b.it_load_mw_energized||0) -
                       (a.it_load_mw_planned||a.it_load_mw_phase1||a.it_load_mw_energized||0));
      srcPop.show(e, `${op} — campuses we track`,
        `${rows.length} campuses · fullest disclosed load level per campus (planned, else phase-1, else live)`,
        rows.map(campusSrcItem));
    } else {
      const rows = D.filter(r => r.operator === op && r.stage_normalized !== 'none_disclosed')
        .sort((a,b) => String(b.as_of_date).localeCompare(String(a.as_of_date)));
      srcPop.show(e, `${op} — their disclosures`,
        'disclosure rows behind the self-reported side, latest first', rows.map(disclosureSrcItem));
    }
  });
  const SERIES = DATA.disclosure_series || {};
  const sel = document.getElementById('dsQoQSelect');
  Object.keys(SERIES).sort().forEach(op => sel.insertAdjacentHTML('beforeend', `<option>${op}</option>`));
  let qoqChart = null;
  function renderQoQ(){
    const op = sel.value; const series = SERIES[op] || [];
    const labels = series.map(q => q.quarter ? q.quarter.replace(/^20(\d\d)Q(\d)/, "$1Q$2") : '');
    const stageData = k => series.map(q => (q.stages && q.stages[k] && q.stages[k].mw) || null);
    if (qoqChart) qoqChart.destroy();
    qoqChart = new Chart(document.getElementById('dsQoQChart'), {
      type: 'bar',
      data: { labels, datasets: [
        { label:'Operational', data: stageData('operational'), backgroundColor:'#0E7B5B', borderWidth:0 },
        { label:'Under construction', data: stageData('under_construction'), backgroundColor:'#7FB5A6', borderWidth:0 },
        { label:'Planned (net)', data: series.map(q => q.planned_shown || null), backgroundColor:'#E2E2E5', borderWidth:0 }
      ]},
      options: { maintainAspectRatio:false, responsive:true,
        scales:{ x:{ stacked:true, grid:{display:false} }, y:{ stacked:true, ticks:{ callback:v=>v.toLocaleString()+' MW' } } },
        plugins:{ legend:{ position:'bottom', labels:{ boxWidth:10, boxHeight:10 } },
          tooltip:{ callbacks:{ label:c=>`${c.dataset.label}: ${(c.parsed.y||0).toLocaleString()} MW` } } },
        onHover: (evt, els) => { evt.chart.canvas.style.cursor = els.length ? 'pointer' : 'default'; },
        onClick: (evt, els) => {
          if (!els.length) return;
          const quarter = series[els[0].index].quarter;
          const stage = ['operational','under_construction','planned'][els[0].datasetIndex];
          const rows = D.filter(r => r.operator === op && r.as_of_quarter === quarter &&
                                     r.stage_normalized === stage)
            .sort((a,b) => String(b.as_of_date).localeCompare(String(a.as_of_date)));
          const sub = stage === 'planned'
            ? 'disclosed planned figures are gross; the column shows them net of operational + under construction'
            : 'as disclosed that quarter';
          srcPop.show(evt.native, `${op} — ${POP_STAGE[stage].toLowerCase()}, ${popQ(quarter)}`,
            sub, rows.map(disclosureSrcItem));
        }
      }
    });
    const n = series.filter(q => q.stages && Object.values(q.stages).some(Boolean)).length;
    document.getElementById('dsQoQNote').textContent = `${n} disclosed quarter(s) — values strictly as stated each quarter`;
  }
  if (Object.keys(SERIES).length) { sel.addEventListener('change', renderQoQ); renderQoQ(); }

  // The chart is first built while this panel is display:none, and Chart.js
  // wedges at 0x0 (even explicit resize() no-ops). Rebuild it once the tab is
  // actually shown; setTimeout lets the panel-activation handler run first.
  const dsTab = document.querySelector('.tab[data-tab="disclosures"]');
  if (dsTab) dsTab.addEventListener('click', () => setTimeout(() => {
    const ch = Chart.getChart(document.getElementById('dsQoQChart'));
    if (Object.keys(SERIES).length && (!ch || !ch.width)) renderQoQ();
  }, 0));

  document.getElementById('dsCoverageNote').textContent =
    'A credibility check, never an accounting identity. Operator side: latest self-reported operational + under construction + net planned. ' +
    'Campus side: the sum over campuses operated by that tenant of the fullest disclosed load level per campus — planned if known, else phase-1, else live (COALESCE(planned, phase1, energized)). ' +
    'Only operators whose disclosures cover their own tenant footprint are compared; landlords whose capacity is tracked under their tenants are excluded to avoid double-attribution. Bases may differ between the two sides.';
})();

// ---- Tabs ----
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById('panel-'+t.dataset.tab).classList.add('active');
  });
});

// ---- Chart: MW by announcement year, stacked by type ----
(function(){
  const C = DATA.contracts;
  const years = [...new Set(C.map(r=>r.year))].sort((a,b)=>a-b);
  const types = [...new Set(C.map(r=>r.generation_type))];
  const ds = types.map(t => {
    const rowsT = C.filter(r => r.generation_type === t);
    return {
      label: t,
      data: years.map(y => rowsT.filter(r=>r.year===y)
                                .reduce((s,r)=>s+(r.capacity_mw||0),0)),
      freshData: freshMW(rowsT, years, 'year'),
      backgroundColor: colorFor(t), borderWidth:0
    };
  }).filter(d => d.data.some(v=>v>0));
  new Chart(document.getElementById('chartYear'), {
    type:'bar',
    data:{ labels:years, datasets: ds },
    options:{ maintainAspectRatio:false, responsive:true,
      scales:{ x:{stacked:true}, y:{stacked:true, ticks:{callback:v=>v.toLocaleString()+' MW'}} },
      plugins:{ legend:{position:'bottom', labels:{boxWidth:10, boxHeight:10}},
                tooltip:{callbacks:{label:c=>`${c.dataset.label}: ${c.parsed.y.toLocaleString()} MW`}} }
    }
  });
})();

// ---- Chart: MW by company (stacked by type) ----
(function(){
  const C = DATA.contracts;
  const companies = [...new Set(C.map(r=>r.company))].sort();
  const types = [...new Set(C.map(r=>r.generation_type))];
  const ds = types.map(t => {
    const rowsT = C.filter(r => r.generation_type === t);
    return {
      label: t,
      data: companies.map(co => rowsT.filter(r=>r.company===co)
                                     .reduce((s,r)=>s+(r.capacity_mw||0),0)),
      freshData: freshMW(rowsT, companies, 'company'),
      backgroundColor: colorFor(t), borderWidth:0
    };
  }).filter(d=>d.data.some(v=>v>0));
  new Chart(document.getElementById('chartCompany'), {
    type:'bar',
    data:{ labels:companies, datasets: ds },
    options:{ maintainAspectRatio:false, responsive:true,
      scales:{ x:{stacked:true}, y:{stacked:true, ticks:{callback:v=>v.toLocaleString()}} },
      plugins:{ legend:{display:false},
                tooltip:{callbacks:{label:c=>`${c.dataset.label}: ${c.parsed.y.toLocaleString()} MW`}} }
    }
  });
})();

// ---- Chart: operational pipeline by COD year ----
(function(){
  const C = DATA.contracts.filter(r=>r.cod_year);
  const years = [...new Set(C.map(r=>r.cod_year))].sort((a,b)=>a-b);
  const types = [...new Set(C.map(r=>r.generation_type))].sort();
  const ds = types.map(t => {
    const rowsT = C.filter(r => r.generation_type === t);
    return {
      label: t,
      data: years.map(y => rowsT.filter(r=>r.cod_year===y)
                                .reduce((s,r)=>s+(r.capacity_mw||0),0)),
      freshData: freshMW(rowsT, years, 'cod_year'),
      backgroundColor: colorFor(t), borderWidth:0
    };
  }).filter(d => d.data.some(v=>v>0));
  new Chart(document.getElementById('chartCod'), {
    type:'bar',
    data:{ labels:years, datasets: ds },
    options:{ maintainAspectRatio:false, responsive:true,
      scales:{ x:{stacked:true}, y:{stacked:true, ticks:{callback:v=>v.toLocaleString()+' MW'}} },
      plugins:{ legend:{position:'bottom', labels:{boxWidth:10, boxHeight:10}},
                tooltip:{callbacks:{label:c=>`${c.dataset.label}: ${c.parsed.y.toLocaleString()} MW`}} }
    }
  });
})();

// ---- Chart: BTM vs Grid by announcement year (all contracts) ----
(function(){
  const C = DATA.contracts;
  const years = [...new Set(C.map(r=>r.year))].sort((a,b)=>a-b);
  const order = ['BTM','Grid','Unknown'];
  const ds = order.map(ct => {
    const rowsCT = C.filter(r => r.connection_type === ct);
    return {
      label: ct,
      data: years.map(y => rowsCT.filter(r=>r.year===y)
                                 .reduce((s,r)=>s+(r.capacity_mw||0),0)),
      freshData: freshMW(rowsCT, years, 'year'),
      backgroundColor: CONN_COLORS[ct], borderWidth:0
    };
  }).filter(d => d.data.some(v=>v>0));
  new Chart(document.getElementById('chartConnAll'), {
    type:'bar',
    data:{ labels:years, datasets: ds },
    options:{ maintainAspectRatio:false, responsive:true,
      scales:{ x:{stacked:true}, y:{stacked:true, ticks:{callback:v=>v.toLocaleString()+' MW'}} },
      plugins:{ legend:{position:'bottom', labels:{boxWidth:10, boxHeight:10}},
                tooltip:{callbacks:{label:c=>`${c.dataset.label}: ${c.parsed.y.toLocaleString()} MW`}} }
    }
  });
})();

// ---- Chart: BTM vs Grid by COD year ----
(function(){
  const C = DATA.contracts.filter(r=>r.cod_year);
  const years = [...new Set(C.map(r=>r.cod_year))].sort((a,b)=>a-b);
  const order = ['BTM','Grid','Unknown'];
  const ds = order.map(ct => {
    const rowsCT = C.filter(r => r.connection_type === ct);
    return {
      label: ct,
      data: years.map(y => rowsCT.filter(r=>r.cod_year===y)
                                 .reduce((s,r)=>s+(r.capacity_mw||0),0)),
      freshData: freshMW(rowsCT, years, 'cod_year'),
      backgroundColor: CONN_COLORS[ct], borderWidth:0
    };
  }).filter(d => d.data.some(v=>v>0));
  new Chart(document.getElementById('chartConnCod'), {
    type:'bar',
    data:{ labels:years, datasets: ds },
    options:{ maintainAspectRatio:false, responsive:true,
      scales:{ x:{stacked:true}, y:{stacked:true, ticks:{callback:v=>v.toLocaleString()+' MW'}} },
      plugins:{ legend:{position:'bottom', labels:{boxWidth:10, boxHeight:10}},
                tooltip:{callbacks:{label:c=>`${c.dataset.label}: ${c.parsed.y.toLocaleString()} MW`}} }
    }
  });
})();

// ---- Chart: BTM vs Grid horizontal by company ----
(function(){
  const C = DATA.contracts;
  const companies = [...new Set(C.map(r=>r.company))].sort();
  const order = ['BTM','Grid','Unknown'];
  const ds = order.map(ct => {
    const rowsCT = C.filter(r => r.connection_type === ct);
    return {
      label: ct,
      data: companies.map(co => rowsCT.filter(r=>r.company===co)
                                      .reduce((s,r)=>s+(r.capacity_mw||0),0)),
      freshData: freshMW(rowsCT, companies, 'company'),
      backgroundColor: CONN_COLORS[ct], borderWidth:0
    };
  }).filter(d => d.data.some(v=>v>0));
  new Chart(document.getElementById('chartConnCompany'), {
    type:'bar',
    data:{ labels:companies, datasets: ds },
    options:{ indexAxis:'y', maintainAspectRatio:false, responsive:true,
      scales:{ x:{stacked:true, ticks:{callback:v=>v.toLocaleString()+' MW'}},
               y:{stacked:true} },
      plugins:{ legend:{position:'bottom', labels:{boxWidth:10, boxHeight:10}},
                tooltip:{callbacks:{label:c=>`${c.dataset.label}: ${c.parsed.x.toLocaleString()} MW`}} }
    }
  });
})();

// ---- Chart: MW horizontal by company/type ----
(function(){
  const C = DATA.contracts;
  const companies = [...new Set(C.map(r=>r.company))].sort();
  const types = [...new Set(C.map(r=>r.generation_type))];
  const ds = types.map(t => {
    const rowsT = C.filter(r => r.generation_type === t);
    return {
      label:t,
      data: companies.map(co => rowsT.filter(r=>r.company===co)
                                     .reduce((s,r)=>s+(r.capacity_mw||0),0)),
      freshData: freshMW(rowsT, companies, 'company'),
      backgroundColor: colorFor(t), borderWidth:0
    };
  }).filter(d=>d.data.some(v=>v>0));
  new Chart(document.getElementById('chartMix'), {
    type:'bar',
    data:{ labels:companies, datasets: ds },
    options:{ indexAxis:'y', maintainAspectRatio:false, responsive:true,
      scales:{ x:{stacked:true, ticks:{callback:v=>v.toLocaleString()+' MW'}},
               y:{stacked:true} },
      plugins:{ legend:{position:'bottom', labels:{boxWidth:10, boxHeight:10}},
                tooltip:{callbacks:{label:c=>`${c.dataset.label}: ${c.parsed.x.toLocaleString()} MW`}} }
    }
  });
})();

// ---- Chart: LCOE ----
(function(){
  const L = DATA.lcoe.filter(r=>r.geography==='US' && r.subsidized===0);
  // Group by technology, show mid of most recent vintage
  const techs = [...new Set(L.map(r=>r.technology))].sort();
  const latest = techs.map(t => {
    const rows = L.filter(r=>r.technology===t).sort((a,b)=>b.year_vintage-a.year_vintage);
    return rows[0];
  });
  new Chart(document.getElementById('chartLcoe'), {
    type:'bar',
    data:{ labels:techs, datasets:[{
      label:'LCOE mid ($/MWh)',
      data:latest.map(r=>r? r.lcoe_mid:null),
      backgroundColor:techs.map(t=>colorFor(t.split('-')[0])),
      borderWidth:0
    }]},
    options:{ maintainAspectRatio:false, responsive:true,
      scales:{ y:{ticks:{callback:v=>'$'+v}} },
      plugins:{ legend:{display:false},
                tooltip:{callbacks:{label:c=>{
                  const r = latest[c.dataIndex]; if(!r) return '';
                  return `${r.report_name} ${r.year_vintage}: $${r.lcoe_low||'-'} / $${r.lcoe_mid||'-'} / $${r.lcoe_high||'-'} /MWh`;
                }}} }
    }
  });
})();

// ---- Chart: Gas CAPEX ----
(function(){
  const G = DATA.gas_capex.sort((a,b)=>a.year-b.year);
  const el = document.getElementById('chartGasCapex');
  const gasChart = new Chart(el, {
    type:'scatter',
    data:{ datasets: [...new Set(G.map(r=>r.plant_type))].map(pt => ({
      label: pt,
      data: G.filter(r=>r.plant_type===pt).map(r => {
        const src = SRC[r.source_id];
        return {x:r.year, y:r.cost_mid_kw||r.cost_low_kw||r.cost_high_kw, label:r.label, url: src ? src.url : null, publisher: src ? src.publisher : ''};
      }),
      backgroundColor:'#C26B4E', borderColor:'#C26B4E', pointRadius:5, pointHoverRadius:8
    }))},
    options:{ maintainAspectRatio:false, responsive:true,
      scales:{ x:{type:'linear', ticks:{stepSize:1, callback:v=>v}},
               y:{ticks:{callback:v=>'$'+v.toLocaleString()+'/kW'}} },
      plugins:{ legend:{position:'bottom'},
                tooltip:{callbacks:{label:c=>`${c.raw.label}: $${c.raw.y.toLocaleString()}/kW (${c.raw.x})${c.raw.url ? ' ↗ '+c.raw.publisher : ''}`}} },
      onClick(evt){
        const pts = gasChart.getElementsAtEventForMode(evt,'nearest',{intersect:true},false);
        if (!pts.length) return;
        const url = gasChart.data.datasets[pts[0].datasetIndex].data[pts[0].index].url;
        if (url) window.open(url,'_blank');
      },
      onHover(evt, items){ el.style.cursor = items.length ? 'pointer' : 'default'; }
    }
  });
})();

// ---- Chart: Renewable CAPEX ----
(function(){
  const R = DATA.renewable_capex.sort((a,b)=>a.year-b.year);
  const techs = [...new Set(R.map(r=>r.technology))];
  const el = document.getElementById('chartRenCapex');
  const renChart = new Chart(el, {
    type:'scatter',
    data:{ datasets: techs.map(t => ({
      label:t,
      data: R.filter(r=>r.technology===t).map(r => {
        const src = SRC[r.source_id];
        return {x:r.year, y:r.cost_mid_kw||r.cost_low_kw||r.cost_high_kw, label:r.label, url: src ? src.url : null, publisher: src ? src.publisher : ''};
      }),
      backgroundColor: colorFor(t.split('-')[0]),
      borderColor: colorFor(t.split('-')[0]), pointRadius:5, pointHoverRadius:8
    }))},
    options:{ maintainAspectRatio:false, responsive:true,
      scales:{ x:{type:'linear', ticks:{stepSize:1, callback:v=>v}},
               y:{ticks:{callback:v=>'$'+v.toLocaleString()+'/kW'}} },
      plugins:{ legend:{position:'bottom', labels:{boxWidth:10, boxHeight:10}},
                tooltip:{callbacks:{label:c=>`${c.raw.label}: $${c.raw.y.toLocaleString()}/kW (${c.raw.x})${c.raw.url ? ' ↗ '+c.raw.publisher : ''}`}} },
      onClick(evt){
        const pts = renChart.getElementsAtEventForMode(evt,'nearest',{intersect:true},false);
        if (!pts.length) return;
        const url = renChart.data.datasets[pts[0].datasetIndex].data[pts[0].index].url;
        if (url) window.open(url,'_blank');
      },
      onHover(evt, items){ el.style.cursor = items.length ? 'pointer' : 'default'; }
    }
  });
})();

// ---- Contracts table ----
(function(){
  const C = DATA.contracts;
  const tbody = document.querySelector('#contractsTable tbody');
  const fOpType  = document.getElementById('fOpType');
  const fCompany = document.getElementById('fCompany');
  const fType    = document.getElementById('fType');
  const fStatus  = document.getElementById('fStatus');
  const fConn    = document.getElementById('fConn');
  const fSearch  = document.getElementById('fSearch');
  const rowCount = document.getElementById('rowCount');
  let sortKey = 'year', sortDir = -1;

  // Operator-type breakdown ribbon (count + total MW per operator type)
  const OP_COLORS = {
    'Hyperscaler': '#1D1D1F',
    'AI-Cloud':    '#2F8488',
    'Colocation':  '#E2A63D',
    'Sovereign':   '#8273B5',
    'Other':       '#8E8E93',
  };
  const opAgg = {};
  for (const r of C) {
    const t = r.operator_type || 'Other';
    if (!opAgg[t]) opAgg[t] = { deals: 0, mw: 0 };
    opAgg[t].deals += 1;
    opAgg[t].mw += (r.capacity_mw || 0);
  }
  const ribbon = document.getElementById('opTypeRibbon');
  ribbon.innerHTML = Object.entries(opAgg)
    .sort((a,b) => b[1].mw - a[1].mw)
    .map(([t, a]) => `
      <div style="background:var(--panel);
                  border-radius:16px; padding:.75rem 1rem; min-width:132px;">
        <div style="font-size:.7rem; font-weight:600; color:var(--muted);"><span style="display:inline-block;width:8px;height:8px;border-radius:4px;background:${OP_COLORS[t]||'#8E8E93'};margin-right:6px;"></span>${t}</div>
        <div style="font-size:1.4rem; font-weight:700; line-height:1.1; margin-top:.2rem;
                    font-variant-numeric:tabular-nums;">${(a.mw/1000).toFixed(1)} <span style="font-size:.7rem;color:var(--muted);font-weight:500;">GW</span></div>
        <div style="font-size:.72rem; color:var(--muted); margin-top:.2rem;">${a.deals} deals</div>
      </div>
    `).join('');

  [...new Set(C.map(r=>r.operator_type))].sort().forEach(v =>
    fOpType.insertAdjacentHTML('beforeend', `<option>${v}</option>`));
  [...new Set(C.map(r=>r.company))].sort().forEach(v =>
    fCompany.insertAdjacentHTML('beforeend', `<option>${v}</option>`));
  [...new Set(C.map(r=>r.generation_type))].sort().forEach(v =>
    fType.insertAdjacentHTML('beforeend', `<option>${v}</option>`));
  [...new Set(C.map(r=>r.status))].sort().forEach(v =>
    fStatus.insertAdjacentHTML('beforeend', `<option>${v}</option>`));
  [...new Set(C.map(r=>r.connection_type))].sort().forEach(v =>
    fConn.insertAdjacentHTML('beforeend', `<option>${v}</option>`));

  function typeBadge(t){
    if (t==='Gas' || t==='Gas+CCS') return `<span class="badge b-gas">${t}</span>`;
    if (t==='Fuel Cell') return `<span class="badge b-fuelcell">Fuel Cell</span>`;
    if (t==='Nuclear') return `<span class="badge b-nuclear">${t}</span>`;
    if (t==='Geothermal') return `<span class="badge b-geo">${t}</span>`;
    return `<span class="badge b-clean">${t}</span>`;
  }
  function statusBadge(s){
    if (s==='Operational') return `<span class="badge b-op">${s}</span>`;
    if (s==='Announced'||s==='MOU') return `<span class="badge b-announced">${s}</span>`;
    return `<span class="badge b-pending">${s||''}</span>`;
  }
  function connBadge(c, reason){
    const tip = reason ? ` title="${reason.replace(/"/g,'&quot;')}"` : '';
    if (c==='BTM') return `<span class="badge b-btm"${tip}>BTM</span>`;
    if (c==='Grid') return `<span class="badge b-grid"${tip}>Grid</span>`;
    return `<span class="badge b-unknown"${tip}>${c||'—'}</span>`;
  }
  function cite(sid){
    const s = SRC[sid];
    if (!s) return sid;
    return `<a class="cite" href="${s.url}" target="_blank" title="${(s.title||'').replace(/"/g,'&quot;')}">${sid}</a>`;
  }

  function render(){
    const op = fOpType.value, co = fCompany.value, ty = fType.value, st = fStatus.value, cn = fConn.value;
    const q = fSearch.value.toLowerCase();
    let rows = C.filter(r =>
      (!op || r.operator_type===op) &&
      (!co || r.company===co) &&
      (!ty || r.generation_type===ty) &&
      (!st || r.status===st) &&
      (!cn || r.connection_type===cn) &&
      (!q || [r.deal_name, r.counterparty, r.notes, r.cod_note].some(x => (x||'').toLowerCase().includes(q)))
    );
    rows.sort((a,b) => {
      // Fresh rows always float to the top, regardless of current sort.
      const an = isNew(a), bn = isNew(b);
      if (an !== bn) return an ? -1 : 1;
      const av=a[sortKey], bv=b[sortKey];
      if (av==null && bv==null) return 0;
      if (av==null) return 1;
      if (bv==null) return -1;
      if (typeof av==='number' && typeof bv==='number') return (av-bv)*sortDir;
      return String(av).localeCompare(String(bv))*sortDir;
    });
    tbody.innerHTML = rows.map(r => {
      const notes = [r.cod_note, r.notes].filter(Boolean).join(' — ');
      const src = SRC[r.source_id];
      const dealHtml = src
        ? `<a class="deal-link" href="${src.url}" target="_blank" title="Source: ${(src.publisher||'').replace(/"/g,'&quot;')} — ${(src.title||'').replace(/"/g,'&quot;')}">${r.deal_name}</a>`
        : r.deal_name;
      const srcBadge = src
        ? `<a class="cite" href="${src.url}" target="_blank" title="${(src.title||'').replace(/"/g,'&quot;')}">${r.source_id}<br><span style="font-size:.65rem;opacity:.7">${(src.publisher||'').slice(0,18)}</span></a>`
        : r.source_id;
      const newPill = isNew(r) ? ` <span class="new-badge" title="Announced within the last ${FRESH_WINDOW_DAYS} days">New</span>` : '';
      const opType = r.operator_type || 'Hyperscaler';
      const opColor = OP_COLORS[opType] || '#8E8E93';
      return `<tr>
        <td><span style="display:inline-block;padding:1px 6px;border-radius:3px;font-size:.65rem;font-weight:600;background:${opColor}33;color:${opColor};border:1px solid ${opColor}55;">${opType}</span></td>
        <td>${r.company}${newPill}</td>
        <td>${r.announced_date || r.year}</td>
        <td>${r.cod_year || '—'}</td>
        <td>${statusBadge(r.status)}</td>
        <td>${typeBadge(r.generation_type)}</td>
        <td>${connBadge(r.connection_type, r.connection_reason)}</td>
        <td style="text-align:right;white-space:nowrap">${r.capacity_mw!=null ? r.capacity_mw.toLocaleString()+' MW' : '—'} <span class="${r.confidence==='Sourced'?'conf-s':'conf-e'}">${r.confidence==='Sourced'?'✓':'~'}</span></td>
        <td style="text-align:right;white-space:nowrap">${r.storage_power_mw!=null ? r.storage_power_mw.toLocaleString()+' MW' : '—'}</td>
        <td style="text-align:right;white-space:nowrap">${r.storage_energy_mwh!=null ? r.storage_energy_mwh.toLocaleString()+' MWh' : '—'}</td>
        <td>${dealHtml}</td>
        <td style="color:var(--muted)">${r.counterparty||''}</td>
        <td style="color:var(--muted);font-size:.78rem;max-width:260px">${notes}</td>
        <td style="white-space:nowrap">${srcBadge}</td>
      </tr>`;
    }).join('');
    rowCount.textContent = `${rows.length} / ${C.length} rows`;
  }

  document.querySelectorAll('#contractsTable th[data-k]').forEach(th => {
    th.addEventListener('click', () => {
      const k = th.dataset.k;
      if (sortKey===k) sortDir *= -1; else { sortKey=k; sortDir=1; }
      render();
    });
  });
  [fOpType,fCompany,fType,fStatus,fConn,fSearch].forEach(el => el.addEventListener('input', render));
  render();
})();

// ---- Campuses panel ----
(function(){
  const CAMP = DATA.campuses || [];
  const PIPE = DATA.campus_pipeline || [];
  if (!CAMP.length) return;

  // ---- Hero stats ----
  const sum = (arr, k) => arr.reduce((s,r)=>s+(r[k]||0), 0);
  const fmt = n => Math.round(n).toLocaleString();
  document.getElementById('cs-energized').textContent = fmt(sum(CAMP, 'it_load_mw_energized'));
  document.getElementById('cs-phase1').textContent    = fmt(sum(CAMP, 'it_load_mw_phase1'));
  document.getElementById('cs-planned').textContent   = fmt(sum(CAMP, 'it_load_mw_planned'));
  document.getElementById('cs-count').textContent     = CAMP.length;

  // ---- Pipeline-by-hyperscaler horizontal bars ----
  // Scale all bars off the largest planned_mw so widths are comparable.
  const maxPlan = Math.max(...PIPE.map(p => p.planned_mw || 0), 1);
  const list = document.getElementById('pipelineList');
  list.innerHTML = PIPE.map(p => {
    const plan = p.planned_mw || 0;
    const ph1  = p.phase1_mw || 0;
    const en   = p.energized_mw || 0;
    const wPlan = (plan / maxPlan) * 100;
    const wPh1  = (ph1  / maxPlan) * 100;
    const wEn   = (en   / maxPlan) * 100;
    // Phase-1 is overlaid on top of planned (lighter), energized on top of phase-1 (solid).
    // Layered z-order: planned bar in back, phase-1 over it, energized over both.
    const enLabel = en > 0 ? `${fmt(en)}` : '';
    const showInside = wEn > 8;
    return `
      <div class="pipe-row" data-op="${p.hyperscaler}">
        <div class="name">${p.hyperscaler}<span class="cnt">${p.campus_count} sites</span></div>
        <div class="pipe-bar click" title="Click for the campuses and sources behind this bar">
          <div class="seg planned"   style="width:${wPlan.toFixed(2)}%"></div>
          <div class="seg phase1"    style="width:${wPh1.toFixed(2)}%"></div>
          <div class="seg energized" style="width:${wEn.toFixed(2)}%"></div>
          ${enLabel ? `<div class="seg-label ${showInside ? '' : 'outside'}"
              style="left:${showInside ? '.4rem' : (wEn.toFixed(2)+'%')}">${enLabel} live</div>` : ''}
        </div>
        <div class="total"><b>${fmt(plan)}</b> MW</div>
      </div>`;
  }).join('');

  // Click a pipeline bar → the campuses (and their citations) it aggregates.
  list.addEventListener('click', e => {
    const bar = e.target.closest('.pipe-bar');
    const row = e.target.closest('.pipe-row');
    if (!bar || !row || !row.dataset.op) return;
    const op = row.dataset.op;
    const rows = CAMP.filter(r => r.hyperscaler === op)
      .sort((a,b) => (b.it_load_mw_planned||b.it_load_mw_phase1||b.it_load_mw_energized||0) -
                     (a.it_load_mw_planned||a.it_load_mw_phase1||a.it_load_mw_energized||0));
    srcPop.show(e, `${op} — tracked campuses`,
      `${rows.length} campuses behind this bar · each links to its citation`,
      rows.map(campusSrcItem));
  });

  // ---- Campus table ----
  const tbody = document.querySelector('#campTable tbody');
  const f1 = document.getElementById('campF1');
  const f2 = document.getElementById('campF2');
  const f3 = document.getElementById('campF3');
  const fs = document.getElementById('campSearch');
  const ct = document.getElementById('campCount');
  let sortKey = 'it_load_mw_planned', sortDir = -1;

  [...new Set(CAMP.map(r=>r.hyperscaler))].sort().forEach(v =>
    f1.insertAdjacentHTML('beforeend', `<option>${v}</option>`));
  [...new Set(CAMP.map(r=>r.status))].sort().forEach(v =>
    f2.insertAdjacentHTML('beforeend', `<option>${v}</option>`));
  [...new Set(CAMP.map(r=>r.country))].sort().forEach(v =>
    f3.insertAdjacentHTML('beforeend', `<option>${v}</option>`));

  function statBadge(s){ return `<span class="b-stat b-${s}">${s}</span>`; }
  function srcCite(sid){
    const s = SRC[sid]; if (!s) return sid;
    return `<a class="cite" href="${s.url}" target="_blank" title="${(s.title||'').replace(/"/g,'&quot;')}">${sid}</a>`;
  }
  function loc(r){
    return [r.city, r.state_or_region, r.country].filter(Boolean).join(', ');
  }

  function render(){
    const v1 = f1.value, v2 = f2.value, v3 = f3.value;
    const q = fs.value.toLowerCase();
    let rows = CAMP.filter(r =>
      (!v1 || r.hyperscaler === v1) &&
      (!v2 || r.status === v2) &&
      (!v3 || r.country === v3) &&
      (!q || [r.campus_name, r.city, r.primary_tenant, r.notes, r.power_source_summary]
                .some(x => (x||'').toLowerCase().includes(q)))
    );
    rows.sort((a,b) => {
      const av=a[sortKey], bv=b[sortKey];
      if (av==null && bv==null) return 0;
      if (av==null) return 1;
      if (bv==null) return -1;
      if (typeof av==='number' && typeof bv==='number') return (av-bv)*sortDir;
      return String(av).localeCompare(String(bv))*sortDir;
    });

    tbody.innerHTML = rows.map(r => `
      <tr>
        <td><code>${r.campus_id}</code></td>
        <td><b>${r.campus_name}</b></td>
        <td>${r.hyperscaler}</td>
        <td style="color:var(--muted)">${r.primary_tenant || ''}</td>
        <td style="color:var(--muted)">${loc(r)}</td>
        <td>${statBadge(r.status)}</td>
        <td style="color:var(--muted);font-size:.74rem">${r.capacity_definition}</td>
        <td class="r">${r.it_load_mw_energized != null ? fmt(r.it_load_mw_energized) : '—'}</td>
        <td class="r"><b>${r.it_load_mw_planned != null ? fmt(r.it_load_mw_planned) : '—'}</b></td>
        <td class="r" style="color:var(--muted)">${r.cod_phase1_year || '—'}</td>
        <td class="r" style="color:var(--muted)">${r.cod_full_year || '—'}</td>
        <td style="color:var(--muted);font-size:.78rem;max-width:220px">${r.power_source_summary || ''}</td>
        <td style="color:var(--muted);font-size:.76rem;max-width:280px">${r.notes || ''}</td>
        <td>${srcCite(r.source_id)}</td>
      </tr>`).join('');
    ct.textContent = `${rows.length} / ${CAMP.length} campuses`;
  }

  document.querySelectorAll('#campTable th[data-k]').forEach(th => {
    th.addEventListener('click', () => {
      const k = th.dataset.k;
      if (sortKey === k) sortDir *= -1; else { sortKey = k; sortDir = -1; }
      render();
    });
  });
  [f1, f2, f3, fs].forEach(el => el.addEventListener('input', render));
  render();
})();

// ---- Qualitative commentary timeline ----
(function(){
  const QLC = DATA.commentary || [];
  const timeline = document.getElementById('qlTimeline');
  if (!timeline) return;

  const fCategory = document.getElementById('qlFCategory');
  const fTaxonomy = document.getElementById('qlFTaxonomy');
  const fStage = document.getElementById('qlFStage');
  const fSearch = document.getElementById('qlSearch');
  const rowCount = document.getElementById('qlRowCount');
  const tbody = document.querySelector('#qlTable tbody');
  let bucketChart = null;

  const fmt = n => Number(n || 0).toLocaleString();
  const label = s => String(s || '').replace(/_/g, ' ');
  const esc = s => String(s ?? '').replace(/[&<>"']/g, ch => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
  }[ch]));
  const sourceLink = sid => {
    const s = SRC[sid];
    if (!s) return esc(sid);
    return `<a class="cite" href="${esc(s.url)}" target="_blank" title="${esc(s.publisher || '')}: ${esc(s.title || '')}">${esc(sid)}</a>`;
  };
  const polarityClass = p => {
    if (p === 'positive_acceleration') return 'ql-pos';
    if (p === 'negative_delay' || p === 'negative_not_observed') return 'ql-neg';
    if (p === 'mixed_or_uncertain') return 'ql-mix';
    return 'ql-neutral';
  };
  const metric = r => {
    if (r.numeric_value == null) return '';
    return `${fmt(r.numeric_value)} ${label(r.numeric_unit)}`;
  };

  document.getElementById('ql-count').textContent = QLC.length;
  document.getElementById('ql-orgs').textContent = new Set(QLC.map(r => r.organization)).size;
  document.getElementById('ql-ready').textContent = QLC.filter(r =>
    ['ready_for_service','energized_or_metered'].includes(r.load_stage)
  ).length;
  document.getElementById('ql-buckets').textContent = new Set(QLC.map(r => r.timeline_bucket)).size;

  [...new Set(QLC.map(r => r.organization_bucket))].sort().forEach(v =>
    fCategory.insertAdjacentHTML('beforeend', `<option value="${esc(v)}">${esc(label(v))}</option>`));
  [...new Set(QLC.map(r => r.statement_taxonomy))].sort().forEach(v =>
    fTaxonomy.insertAdjacentHTML('beforeend', `<option value="${esc(v)}">${esc(label(v))}</option>`));
  [...new Set(QLC.map(r => r.load_stage))].sort().forEach(v =>
    fStage.insertAdjacentHTML('beforeend', `<option value="${esc(v)}">${esc(label(v))}</option>`));

  function filteredRows(){
    const cat = fCategory.value, tax = fTaxonomy.value, stage = fStage.value;
    const q = fSearch.value.toLowerCase();
    return QLC.filter(r =>
      (!cat || r.organization_bucket === cat) &&
      (!tax || r.statement_taxonomy === tax) &&
      (!stage || r.load_stage === stage) &&
      (!q || [
        r.organization, r.speaker_name, r.event_name, r.statement_taxonomy,
        r.load_stage, r.capacity_basis, r.geography, r.short_quote, r.paraphrase,
        r.notes, r.related_company
      ].some(x => String(x || '').toLowerCase().includes(q)))
    ).sort((a,b) =>
      String(a.statement_date).localeCompare(String(b.statement_date)) ||
      String(a.statement_id).localeCompare(String(b.statement_id))
    );
  }

  function renderChart(rows){
    const buckets = [...new Set(rows.map(r => r.timeline_bucket))].sort();
    const stages = [...new Set(rows.map(r => r.load_stage))].sort();
    const colors = {
      narrative_demand: '#B9B9C0',
      announced_pipeline: '#79A6CE',
      contracted_service: '#2F8488',
      under_construction: '#E2A63D',
      ready_for_service: '#7FB5A6',
      energized_or_metered: '#0E7B5B',
      bottleneck_constraint: '#C26B4E',
      interconnection_queue: '#8273B5'
    };
    if (bucketChart) bucketChart.destroy();
    bucketChart = new Chart(document.getElementById('qlBucketChart'), {
      type: 'bar',
      data: {
        labels: buckets,
        datasets: stages.map(stage => ({
          label: label(stage),
          data: buckets.map(b => rows.filter(r => r.timeline_bucket === b && r.load_stage === stage).length),
          backgroundColor: colors[stage] || '#8E8E93',
          borderWidth: 0
        }))
      },
      options: {
        responsive:true,
        maintainAspectRatio:false,
        plugins:{ legend:{ position:'bottom' } },
        scales:{
          x:{ stacked:true, grid:{ display:false } },
          y:{ stacked:true, ticks:{ precision:0 }, title:{ display:true, text:'statement count' } }
        }
      }
    });
  }

  function render(){
    const rows = filteredRows();
    const byBucket = {};
    for (const r of rows) {
      if (!byBucket[r.timeline_bucket]) byBucket[r.timeline_bucket] = [];
      byBucket[r.timeline_bucket].push(r);
    }
    timeline.innerHTML = Object.entries(byBucket).map(([bucket, items]) => `
      <div class="ql-bucket">
        <div class="ql-date">${esc(bucket)}</div>
        <div>
          ${items.map(r => `
            <div class="ql-item">
              <div class="ql-org">${esc(r.organization)} <span class="ql-meta">${esc(r.statement_date)} · ${esc(r.speaker_name)}</span></div>
              <div class="ql-meta">
                <span class="ql-badge ${polarityClass(r.polarity)}">${esc(label(r.polarity))}</span>
                <span class="ql-badge ql-stage">${esc(label(r.load_stage))}</span>
                <span class="ql-badge ql-basis">${esc(label(r.capacity_basis))}</span>
                ${metric(r) ? `<span class="ql-meta">${esc(metric(r))}</span>` : ''}
                ${sourceLink(r.source_id)}
              </div>
              <div class="ql-text">${esc(r.paraphrase)}</div>
              ${r.short_quote ? `<div class="ql-meta">"${esc(r.short_quote)}"</div>` : ''}
            </div>
          `).join('')}
        </div>
      </div>
    `).join('') || `<div class="ql-meta">No commentary rows match the current filters.</div>`;

    tbody.innerHTML = rows.map(r => `
      <tr>
        <td>${esc(r.statement_date)}<br><span class="ql-meta">${esc(r.date_precision)}</span></td>
        <td><code>${esc(r.timeline_bucket)}</code></td>
        <td><b>${esc(r.organization)}</b><br><span class="ql-meta">${esc(label(r.organization_bucket))}</span></td>
        <td>${esc(r.speaker_name)}<br><span class="ql-meta">${esc(r.speaker_title || '')}</span></td>
        <td>${esc(label(r.statement_taxonomy))}</td>
        <td><span class="ql-badge ql-stage">${esc(label(r.load_stage))}</span></td>
        <td><span class="ql-badge ql-basis">${esc(label(r.capacity_basis))}</span>${metric(r) ? `<br><span class="ql-meta">${esc(metric(r))}</span>` : ''}</td>
        <td>${esc(r.paraphrase)}${r.short_quote ? `<br><span class="ql-meta">"${esc(r.short_quote)}"</span>` : ''}</td>
        <td>${sourceLink(r.source_id)}</td>
      </tr>`).join('');
    rowCount.textContent = `${rows.length} / ${QLC.length} statements`;
    renderChart(rows);
  }

  [fCategory, fTaxonomy, fStage, fSearch].forEach(el => el.addEventListener('input', render));
  render();
})();

// ---- EIA Federal Cross-Check panel ----
(function(){
  const T = DATA.eia_tier || [];
  const O = DATA.eia_overlap || [];
  if (!T.length) return;
  const fmt = n => Math.round(n).toLocaleString();

  // ---------- Time-series datasets ----------
  const SV = DATA.eia_status_by_vintage || [];
  const TV = DATA.eia_tech_by_vintage || [];
  const STV = DATA.eia_stage_tech_by_vintage || [];
  const vintages = [...new Set(SV.map(r => r.vintage))].sort();
  const tiersOrder = ['ConstructionComplete','MajorityComplete','MinorityComplete',
                      'ApprovalsReceived','ApprovalsPending','PlannedOnly','Other'];
  const tiersPresent = tiersOrder.filter(t => SV.some(r => r.status_tier === t));

  // Earliest + latest vintage totals for hero stats
  const totalForVintage = v => SV.filter(r => r.vintage === v).reduce((s,r)=>s+(r.total_mw||0),0);
  const earliest = vintages[0], latest = vintages[vintages.length-1];
  const totalEarly = totalForVintage(earliest);
  const totalLatest = totalForVintage(latest);
  const totalCount = SV.reduce((s,r)=>s+(r.gen_count||0),0);

  document.getElementById('es-current').textContent  = (totalLatest/1000).toFixed(0);
  document.getElementById('es-growth').textContent   = '+' + Math.round(100*(totalLatest-totalEarly)/totalEarly);
  document.getElementById('es-vintages').textContent = vintages.length;
  document.getElementById('es-count').textContent    = totalCount.toLocaleString();

  // Tier ladder — sorted from highest probability to lowest, labelled with friendly text
  const TIER_LABELS = {
    'ConstructionComplete': 'Construction complete',
    'MajorityComplete':     'Under construction (>50%)',
    'MinorityComplete':     'Under construction (≤50%)',
    'ApprovalsReceived':    'Approvals received, not started',
    'ApprovalsPending':     'Approvals pending',
    'PlannedOnly':          'Planned only (no approvals filed)',
    'Other':                'Other',
  };
  // Sort highest probability first (descending)
  const tiers = T.slice().sort((a,b) => b.prob - a.prob);
  const maxAnn = Math.max(...tiers.map(t => t.announced_mw || 0), 1);

  const list = document.getElementById('eiaTierList');
  list.innerHTML = tiers.map(t => {
    const ann = t.announced_mw || 0;
    const exp = t.expected_mw || 0;
    const wAnn = (ann / maxAnn) * 100;
    const wExp = (exp / maxAnn) * 100;
    const probPct = Math.round(t.prob * 100);
    return `
      <div class="pipe-row">
        <div class="name">${TIER_LABELS[t.status_tier] || t.status_tier}
          <span class="cnt">${t.gens} gens · ${probPct}%</span></div>
        <div class="pipe-bar">
          <div class="seg planned"   style="width:${wAnn.toFixed(2)}%"></div>
          <div class="seg energized" style="width:${wExp.toFixed(2)}%"></div>
          <div class="seg-label ${wExp > 8 ? '' : 'outside'}"
            style="left:${wExp > 8 ? '.4rem' : (wExp.toFixed(2)+'%')}">${fmt(exp)} expected</div>
        </div>
        <div class="total"><b>${fmt(ann)}</b> MW</div>
      </div>`;
  }).join('');

  // ---------- Time-series chart: status by vintage ----------
  // Color scheme — green at the bottom (close to delivery), gray at the top (just announced)
  const STATUS_COLOR = {
    'ConstructionComplete': '#0E7B5B',
    'MajorityComplete':     '#4D9678',
    'MinorityComplete':     '#7FB5A6',
    'ApprovalsReceived':    '#E2A63D',
    'ApprovalsPending':     '#C26B4E',
    'PlannedOnly':          '#B9B9C0',
    'Other':                '#E4E4E7',
  };
  const STATUS_LABEL = {
    'ConstructionComplete': 'Construction complete',
    'MajorityComplete':     'Under construction (>50%)',
    'MinorityComplete':     'Under construction (≤50%)',
    'ApprovalsReceived':    'Approvals received',
    'ApprovalsPending':     'Approvals pending',
    'PlannedOnly':          'Planned only',
    'Other':                'Other',
  };

  // Order tiers bottom→top: most-built at bottom, least-built at top
  const stackOrder = ['ConstructionComplete','MajorityComplete','MinorityComplete',
                      'ApprovalsReceived','ApprovalsPending','PlannedOnly','Other']
                     .filter(t => tiersPresent.includes(t));

  function vintageLabel(v) {
    // '2024-01' → 'Jan 2024'
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const [y, m] = v.split('-');
    return `${months[parseInt(m)-1]} ${y}`;
  }

  const statusDatasets = stackOrder.map(t => ({
    label: STATUS_LABEL[t],
    data: vintages.map(v => {
      const r = SV.find(r => r.vintage === v && r.status_tier === t);
      return r ? r.total_mw : 0;
    }),
    backgroundColor: STATUS_COLOR[t],
    borderWidth: 0,
    stack: 'main'
  }));

  new Chart(document.getElementById('eiaStatusVintageChart'), {
    type: 'bar',
    data: { labels: vintages.map(vintageLabel), datasets: statusDatasets },
    options: {
      maintainAspectRatio: false, responsive: true,
      scales: {
        x: { stacked: true },
        y: { stacked: true,
             ticks: { callback: v => (v/1000).toFixed(0) + ' GW' } }
      },
      plugins: {
        legend: { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, padding: 12 } },
        tooltip: {
          callbacks: {
            label: c => `${c.dataset.label}: ${fmt(c.parsed.y)} MW`,
            footer: items => 'Total: ' + fmt(items.reduce((s,i)=>s+i.parsed.y,0)) + ' MW'
          }
        }
      }
    }
  });

  // ---------- Time-series chart: tech by vintage ----------
  const TECH_ORDER  = ['Solar','Wind','Storage','Gas','Nuclear','Geothermal','Hydro','Other'];
  const TECH_COLOR  = {
    Solar:'#E2A63D', Wind:'#79A6CE', Storage:'#2F8488', Gas:'#C26B4E',
    Nuclear:'#8273B5', Geothermal:'#C26B4E', Hydro:'#4A7BA6', Other:'#8E8E93'
  };
  const techsInTV = TECH_ORDER.filter(t => TV.some(r => r.tech_group === t));
  const techDatasets = techsInTV.map(t => ({
    label: t,
    data: vintages.map(v => {
      const r = TV.find(r => r.vintage === v && r.tech_group === t);
      return r ? r.total_mw : 0;
    }),
    backgroundColor: TECH_COLOR[t],
    borderWidth: 0,
    stack: 'main'
  }));

  new Chart(document.getElementById('eiaTechVintageChart'), {
    type: 'bar',
    data: { labels: vintages.map(vintageLabel), datasets: techDatasets },
    options: {
      maintainAspectRatio: false, responsive: true,
      scales: {
        x: { stacked: true },
        y: { stacked: true,
             ticks: { callback: v => (v/1000).toFixed(0) + ' GW' } }
      },
      plugins: {
        legend: { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, padding: 12 } },
        tooltip: {
          callbacks: {
            label: c => `${c.dataset.label}: ${fmt(c.parsed.y)} MW`
          }
        }
      }
    }
  });

  // ---------- Time-series chart: construction stage by selected technology ----------
  const STAGE_ORDER = ['ConstructionComplete','UnderConstruction','PlannedPermitting'];
  const STAGE_LABEL = {
    ConstructionComplete: 'Construction complete',
    UnderConstruction: 'Under construction',
    PlannedPermitting: 'Planned / permitting',
  };
  const STAGE_COLOR = {
    ConstructionComplete: '#0E7B5B',
    UnderConstruction: '#E2A63D',
    PlannedPermitting: '#B9B9C0',
  };
  const stageSelect = document.getElementById('eiaStageTechSelect');
  const stageSummary = document.getElementById('eiaStageTechSummary');
  const stageTable = document.getElementById('eiaStageTechTable');
  let stageTechChart = null;

  function stageValue(tech, vintage, stage) {
    const r = STV.find(r => r.tech_group === tech && r.vintage === vintage && r.stage_group === stage);
    return r ? r.total_mw : 0;
  }

  function stageTotal(tech, vintage) {
    return STAGE_ORDER.reduce((sum, stage) => sum + stageValue(tech, vintage, stage), 0);
  }

  if (STV.length && stageSelect) {
    const techsInStage = TECH_ORDER.filter(t => STV.some(r => r.tech_group === t));
    const defaultTech = techsInStage
      .map(tech => ({ tech, total: stageTotal(tech, latest) }))
      .sort((a,b) => b.total - a.total)[0]?.tech || techsInStage[0];

    stageSelect.innerHTML = techsInStage
      .map(tech => `<option value="${tech}" ${tech === defaultTech ? 'selected' : ''}>${tech}</option>`)
      .join('');

    function renderStageTech() {
      const tech = stageSelect.value || defaultTech;
      const latestTotal = stageTotal(tech, latest);
      const earliestTotal = stageTotal(tech, earliest);
      const latestComplete = stageValue(tech, latest, 'ConstructionComplete');
      const latestUnder = stageValue(tech, latest, 'UnderConstruction');
      const latestPlanned = stageValue(tech, latest, 'PlannedPermitting');
      const delta = latestTotal - earliestTotal;

      stageSummary.textContent =
        `${fmt(latestTotal)} MW latest | ${fmt(latestComplete)} complete | ` +
        `${fmt(latestUnder)} under construction | ${fmt(latestPlanned)} planned/permitting`;

      const datasets = STAGE_ORDER.map(stage => ({
        label: STAGE_LABEL[stage],
        data: vintages.map(v => stageValue(tech, v, stage)),
        backgroundColor: STAGE_COLOR[stage],
        borderWidth: 0,
        stack: 'main',
      }));

      if (stageTechChart) stageTechChart.destroy();
      stageTechChart = new Chart(document.getElementById('eiaStageTechVintageChart'), {
        type: 'bar',
        data: { labels: vintages.map(vintageLabel), datasets },
        options: {
          maintainAspectRatio: false, responsive: true,
          scales: {
            x: { stacked: true },
            y: { stacked: true, ticks: { callback: v => (v/1000).toFixed(0) + ' GW' } }
          },
          plugins: {
            title: { display: true, text: `${tech} planned-generator pipeline by construction stage` },
            legend: { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, padding: 12 } },
            tooltip: {
              callbacks: {
                label: c => `${c.dataset.label}: ${fmt(c.parsed.y)} MW`,
                footer: items => 'Total: ' + fmt(items.reduce((s,i)=>s+i.parsed.y,0)) + ' MW'
              }
            }
          }
        }
      });

      const rows = techsInStage.map(rowTech => {
        const early = stageTotal(rowTech, earliest);
        const rowLatest = stageTotal(rowTech, latest);
        const rowDelta = rowLatest - early;
        const marker = rowTech === tech ? `<b>${rowTech}</b>` : rowTech;
        return `<tr>
          <td>${marker}</td>
          <td class="r">${fmt(stageValue(rowTech, latest, 'ConstructionComplete'))}</td>
          <td class="r">${fmt(stageValue(rowTech, latest, 'UnderConstruction'))}</td>
          <td class="r">${fmt(stageValue(rowTech, latest, 'PlannedPermitting'))}</td>
          <td class="r"><b>${fmt(rowLatest)}</b></td>
          <td class="r">${rowDelta >= 0 ? '+' : ''}${fmt(rowDelta)}</td>
        </tr>`;
      }).join('');

      stageTable.innerHTML = `
        <div class="camp-table-wrap" style="max-height:360px">
          <table style="width:100%; border-collapse:collapse; font-size:.82rem;">
            <thead><tr>
              <th>Technology</th>
              <th class="r">Latest complete MW</th>
              <th class="r">Latest under-construction MW</th>
              <th class="r">Latest planned/permitting MW</th>
              <th class="r">Latest total MW</th>
              <th class="r">Change vs earliest</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>`;
    }

    stageSelect.addEventListener('change', renderStageTech);
    renderStageTech();
  }

  // ---------- Latest snapshot: construction status by technology ----------
  const ST = DATA.eia_status_tech || [];
  if (ST.length) {
    const techsInST = TECH_ORDER.filter(t => ST.some(r => r.tech_group === t));
    const statusByTechDatasets = stackOrder.map(status => ({
      label: STATUS_LABEL[status],
      data: techsInST.map(tech => {
        const r = ST.find(r => r.tech_group === tech && r.status_tier === status);
        return r ? r.announced_mw : 0;
      }),
      backgroundColor: STATUS_COLOR[status],
      borderWidth: 0,
      stack: 'main'
    }));
    new Chart(document.getElementById('eiaStatusTechChart'), {
      type: 'bar',
      data: { labels: techsInST, datasets: statusByTechDatasets },
      options: {
        maintainAspectRatio: false, responsive: true,
        scales: {
          x: { stacked: true },
          y: { stacked: true, ticks: { callback: v => (v/1000).toFixed(0) + ' GW' } }
        },
        plugins: {
          legend: { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, padding: 12 } },
          tooltip: { callbacks: { label: c => `${c.dataset.label}: ${fmt(c.parsed.y)} MW` } }
        }
      }
    });

    const tableRows = techsInST.map(tech => {
      const total = ST.filter(r => r.tech_group === tech).reduce((s,r)=>s+(r.announced_mw||0),0);
      const cells = stackOrder.map(status => {
        const r = ST.find(r => r.tech_group === tech && r.status_tier === status);
        return `<td class="r">${r ? fmt(r.announced_mw) : '—'}</td>`;
      }).join('');
      return `<tr>
        <td><b>${tech}</b></td>
        ${cells}
        <td class="r"><b>${fmt(total)}</b></td>
      </tr>`;
    }).join('');
    document.getElementById('eiaStatusTechTable').innerHTML = `
      <div class="camp-table-wrap" style="max-height:420px">
        <table style="width:100%; border-collapse:collapse; font-size:.82rem;">
          <thead><tr>
            <th>Technology</th>
            ${stackOrder.map(status => `<th class="r">${STATUS_LABEL[status]}</th>`).join('')}
            <th class="r">Total</th>
          </tr></thead>
          <tbody>${tableRows}</tbody>
        </table>
      </div>`;
  }

  // ---------- Completion rate vs announcement rate (clustered bar) ----------
  const TR = DATA.eia_transitions || [];
  if (TR.length) {
    const labels = TR.map(t => `${vintageLabel(t.v_from)}\n→ ${vintageLabel(t.v_to)} (${t.months}mo)`);
    new Chart(document.getElementById('eiaTransitionChart'), {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [
          {
            label: 'Completed (directly observed from EIA Operating Year/Month)',
            data: TR.map(t => t.operated_mw),
            backgroundColor: '#0E7B5B',
            borderWidth: 0
          },
          {
            label: 'Newly announced',
            data: TR.map(t => t.new_mw),
            backgroundColor: '#8E8E93',
            borderWidth: 0
          },
          {
            label: 'Cancelled / withdrawn',
            data: TR.map(t => t.cancelled_mw),
            backgroundColor: '#C26B4E',
            borderWidth: 0
          },
        ]
      },
      options: {
        maintainAspectRatio: false, responsive: true,
        scales: {
          x: { ticks: { maxRotation: 0, minRotation: 0, font: {size: 10} } },
          y: { ticks: { callback: v => (v/1000).toFixed(0) + ' GW' } }
        },
        plugins: {
          legend: { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, padding: 12 } },
          tooltip: {
            callbacks: {
              label: c => `${c.dataset.label}: ${fmt(c.parsed.y)} MW (over ${TR[c.dataIndex].months} mo)`
            }
          }
        }
      }
    });

    // Compact summary table beneath the chart
    const t = document.getElementById('eiaTransitionTable');
    t.innerHTML = `
      <div class="camp-table-wrap" style="max-height:none">
        <table style="width:100%; border-collapse:collapse; font-size:.82rem;">
          <thead><tr>
            <th>Window</th>
            <th class="r">Months</th>
            <th class="r" style="color:#0E7B5B;">Completed</th>
            <th class="r" style="color:#8E8E93;">Newly announced</th>
            <th class="r" style="color:#C26B4E;">Cancelled</th>
            <th class="r">Announced ÷ Completed</th>
          </tr></thead>
          <tbody>
            ${TR.map(t => `
              <tr>
                <td><b>${vintageLabel(t.v_from)} → ${vintageLabel(t.v_to)}</b></td>
                <td class="r">${t.months}</td>
                <td class="r" style="color:#0E7B5B;"><b>${fmt(t.operated_mw)}</b> MW</td>
                <td class="r" style="color:#8E8E93;"><b>${fmt(t.new_mw)}</b> MW</td>
                <td class="r" style="color:#C26B4E;">${fmt(t.cancelled_mw)} MW</td>
                <td class="r"><b>${(t.new_mw / Math.max(t.operated_mw, 1)).toFixed(1)}×</b></td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  }

  // ---------- Calendar-year totals chart ----------
  if (TR.length > 0) {
    const yearAgg = {};
    TR.forEach(t => {
      const yr = t.v_to.split('-')[0];
      if (!yearAgg[yr]) yearAgg[yr] = { completed: 0, announced: 0, cancelled: 0, n: 0 };
      yearAgg[yr].completed += (t.operated_mw  || 0);
      yearAgg[yr].announced += (t.new_mw       || 0);
      yearAgg[yr].cancelled += (t.cancelled_mw || 0);
      yearAgg[yr].n += 1;
    });
    const years = Object.keys(yearAgg).sort();

    new Chart(document.getElementById('eiaYearChart'), {
      type: 'bar',
      data: {
        labels: years.map(y => yearAgg[y].n < 4 ? `${y} (Q1 only)` : y),
        datasets: [
          {
            label: 'Completed',
            data: years.map(y => yearAgg[y].completed / 1000),
            backgroundColor: '#0E7B5B', borderWidth: 0
          },
          {
            label: 'Newly announced',
            data: years.map(y => yearAgg[y].announced / 1000),
            backgroundColor: '#8E8E93', borderWidth: 0
          },
        ]
      },
      options: {
        maintainAspectRatio: false, responsive: true,
        scales: {
          x: { ticks: { font: { size: 12 } } },
          y: { ticks: { callback: v => v.toFixed(0) + ' GW' }, beginAtZero: true }
        },
        plugins: {
          legend: { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, padding: 12 } },
          tooltip: {
            callbacks: {
              label: c => `${c.dataset.label}: ${c.parsed.y.toFixed(1)} GW (${fmt(c.parsed.y * 1000)} MW)`
            }
          }
        }
      }
    });

    document.getElementById('eiaYearTable').innerHTML = `
      <div class="camp-table-wrap" style="max-height:none">
        <table style="width:100%; border-collapse:collapse; font-size:.82rem;">
          <thead><tr>
            <th>Year</th>
            <th class="r" style="color:#0E7B5B;">Completed</th>
            <th class="r" style="color:#0E7B5B;">YoY %</th>
            <th class="r" style="color:#8E8E93;">Announced</th>
            <th class="r" style="color:#8E8E93;">YoY %</th>
            <th class="r" style="color:#C26B4E;">Cancelled</th>
            <th class="r">Announced ÷ Completed</th>
          </tr></thead>
          <tbody>
            ${years.map((y, i) => {
              const a = yearAgg[y];
              const partial = a.n < 4;
              const prev = i > 0 ? yearAgg[years[i-1]] : null;
              const yoyDone = (prev && !partial && prev.n === 4 && prev.completed > 0)
                ? ((a.completed/prev.completed - 1) * 100) : null;
              const yoyAnn = (prev && !partial && prev.n === 4 && prev.announced > 0)
                ? ((a.announced/prev.announced - 1) * 100) : null;
              const f = n => n == null ? '—' : ((n > 0 ? '+' : '') + n.toFixed(0) + '%');
              return `<tr>
                <td><b>${y}</b>${partial ? ' <span style="color:var(--muted);font-size:.75rem">(Q1 only)</span>' : ''}</td>
                <td class="r" style="color:#0E7B5B;"><b>${(a.completed/1000).toFixed(1)}</b> GW</td>
                <td class="r" style="color:${(yoyDone||0) >= 0 ? '#0E7B5B' : '#C26B4E'}">${f(yoyDone)}</td>
                <td class="r" style="color:#8E8E93;"><b>${(a.announced/1000).toFixed(1)}</b> GW</td>
                <td class="r" style="color:${(yoyAnn||0) >= 0 ? '#8E8E93' : '#C26B4E'}">${f(yoyAnn)}</td>
                <td class="r" style="color:#C26B4E;">${(a.cancelled/1000).toFixed(1)} GW</td>
                <td class="r"><b>${a.completed > 0 ? (a.announced/a.completed).toFixed(1) : '—'}×</b></td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>`;
  }

  // ---------- QoQ % change chart — Completed vs Announced ----------
  if (TR.length >= 2) {
    const qoq = TR.map((t, i) => {
      if (i === 0) return null;
      const prev = TR[i - 1];
      const pctDone = prev.operated_mw > 0 ? (t.operated_mw / prev.operated_mw - 1) * 100 : null;
      const pctAnn  = prev.new_mw      > 0 ? (t.new_mw      / prev.new_mw      - 1) * 100 : null;
      return { v_to: t.v_to, pctDone, pctAnn,
               q_done: t.operated_mw, q_ann: t.new_mw,
               p_done: prev.operated_mw, p_ann: prev.new_mw };
    }).filter(x => x !== null);

    new Chart(document.getElementById('eiaQoQChart'), {
      type: 'bar',
      data: {
        labels: qoq.map(t => vintageLabel(t.v_to)),
        datasets: [
          {
            label: 'Completed QoQ %',
            data: qoq.map(t => t.pctDone),
            backgroundColor: qoq.map(t => t.pctDone >= 0 ? '#0E7B5B' : 'rgba(14,123,91,.35)'),
            borderColor: '#0E7B5B', borderWidth: 1
          },
          {
            label: 'Announced QoQ %',
            data: qoq.map(t => t.pctAnn),
            backgroundColor: qoq.map(t => t.pctAnn >= 0 ? '#8E8E93' : 'rgba(142,142,147,.35)'),
            borderColor: '#8E8E93', borderWidth: 1
          },
        ]
      },
      options: {
        maintainAspectRatio: false, responsive: true,
        scales: {
          x: { ticks: { maxRotation: 0, font: { size: 10 } } },
          y: {
            ticks: { callback: v => (v > 0 ? '+' : '') + v.toFixed(0) + '%' },
            grid: {
              color: ctx => ctx.tick.value === 0 ? 'rgba(29,29,31,.35)' : 'rgba(29,29,31,.04)',
              lineWidth: ctx => ctx.tick.value === 0 ? 1.5 : 1
            }
          }
        },
        plugins: {
          legend: { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, padding: 12 } },
          tooltip: {
            callbacks: {
              label: c => {
                const t = qoq[c.dataIndex];
                const isDone = c.dataset.label.startsWith('Completed');
                const cur  = isDone ? t.q_done : t.q_ann;
                const prev = isDone ? t.p_done : t.p_ann;
                const pct  = c.parsed.y;
                return `${c.dataset.label}: ${pct == null ? '—' : ((pct > 0 ? '+' : '') + pct.toFixed(0) + '%')} (${fmt(prev)} → ${fmt(cur)} MW)`;
              }
            }
          }
        }
      }
    });

    document.getElementById('eiaQoQTable').innerHTML = `
      <div class="camp-table-wrap" style="max-height:none">
        <table style="width:100%; border-collapse:collapse; font-size:.82rem;">
          <thead><tr>
            <th>Quarter</th>
            <th class="r" style="color:#0E7B5B;">Completed MW</th>
            <th class="r" style="color:#0E7B5B;">Completed QoQ</th>
            <th class="r" style="color:#8E8E93;">Announced MW</th>
            <th class="r" style="color:#8E8E93;">Announced QoQ</th>
          </tr></thead>
          <tbody>
            ${qoq.map(t => {
              const f = n => n == null ? '—' : ((n > 0 ? '+' : '') + n.toFixed(0) + '%');
              return `<tr>
                <td><b>${vintageLabel(t.v_to)}</b></td>
                <td class="r"><b>${fmt(t.q_done)}</b></td>
                <td class="r" style="color:${(t.pctDone||0) >= 0 ? '#0E7B5B' : '#C26B4E'}">${f(t.pctDone)}</td>
                <td class="r"><b>${fmt(t.q_ann)}</b></td>
                <td class="r" style="color:${(t.pctAnn||0) >= 0 ? '#8E8E93' : '#C26B4E'}">${f(t.pctAnn)}</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>`;
  }

  // ---------- Federal pipeline by generation type ----------
  const Y = DATA.eia_year_tech || [];
  const S = DATA.eia_state_tech || [];

  // (TECH_ORDER and TECH_COLOR already defined above for the time-series charts)
  // Restrict to 2026–2032 — anything beyond is statistical noise (handful of nuclear)
  const years = [...new Set(Y.map(r => r.planned_year))].filter(y => y >= 2026 && y <= 2032).sort();
  const techsPresent = TECH_ORDER.filter(t => Y.some(r => r.tech_group === t));

  function buildDatasets(field) {
    return techsPresent.map(t => ({
      label: t,
      data: years.map(y => {
        const r = Y.find(r => r.planned_year === y && r.tech_group === t);
        return r ? r[field] : 0;
      }),
      backgroundColor: TECH_COLOR[t],
      borderWidth: 0,
      stack: 'main'
    }));
  }

  const annTotal = Y.reduce((s,r) => s + (r.announced_mw || 0), 0);
  const expTotal = Y.reduce((s,r) => s + (r.expected_mw || 0), 0);
  document.getElementById('eiaAnnTotal').textContent = (annTotal/1000).toFixed(0);
  document.getElementById('eiaExpTotal').textContent = (expTotal/1000).toFixed(0);

  // Find the larger of the two totals for shared y-axis ceiling so the visual shrinkage is honest
  const yAxisMax = Math.max(
    ...years.map(y => Y.filter(r => r.planned_year === y).reduce((s,r) => s + (r.announced_mw||0), 0))
  ) * 1.05;

  function chartOpts(title) {
    return {
      maintainAspectRatio: false, responsive: true,
      scales: {
        x: { stacked: true },
        y: { stacked: true, max: yAxisMax,
             ticks: { callback: v => (v/1000).toFixed(0) + ' GW' } }
      },
      plugins: {
        legend: { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, padding: 12 } },
        tooltip: {
          callbacks: {
            label: c => `${c.dataset.label}: ${fmt(c.parsed.y)} MW`,
            footer: items => {
              const tot = items.reduce((s,i) => s + i.parsed.y, 0);
              return 'Total: ' + fmt(tot) + ' MW';
            }
          }
        }
      }
    };
  }

  new Chart(document.getElementById('eiaAnnChart'), {
    type: 'bar',
    data: { labels: years, datasets: buildDatasets('announced_mw') },
    options: chartOpts('Announced')
  });
  new Chart(document.getElementById('eiaExpChart'), {
    type: 'bar',
    data: { labels: years, datasets: buildDatasets('expected_mw') },
    options: chartOpts('Probability-weighted')
  });

  // ---------- Tech-level summary horizontal bars ----------
  // Aggregate across all years for the technology total view
  const techTotals = techsPresent.map(t => ({
    tech: t,
    announced: Y.filter(r => r.tech_group === t).reduce((s,r) => s + (r.announced_mw||0), 0),
    expected:  Y.filter(r => r.tech_group === t).reduce((s,r) => s + (r.expected_mw ||0), 0),
    gens:      Y.filter(r => r.tech_group === t).reduce((s,r) => s + (r.gen_count   ||0), 0),
  })).sort((a,b) => b.announced - a.announced);

  const maxTech = techTotals[0].announced;
  document.getElementById('eiaTechList').innerHTML = techTotals.map(t => {
    const wAnn = (t.announced / maxTech) * 100;
    const wExp = (t.expected  / maxTech) * 100;
    const hitRate = Math.round(100 * t.expected / t.announced);
    return `
      <div class="pipe-row" style="grid-template-columns:120px 1fr 110px;">
        <div class="name" style="color:${TECH_COLOR[t.tech]};">${t.tech}
          <span class="cnt" style="color:var(--muted);">${t.gens}</span></div>
        <div class="pipe-bar">
          <div class="seg" style="position:absolute;left:0;top:0;bottom:0;background:${TECH_COLOR[t.tech]};opacity:.30;width:${wAnn.toFixed(2)}%"></div>
          <div class="seg" style="position:absolute;left:0;top:0;bottom:0;background:${TECH_COLOR[t.tech]};width:${wExp.toFixed(2)}%"></div>
          <div class="seg-label ${wExp > 12 ? '' : 'outside'}"
               style="left:${wExp > 12 ? '.4rem' : (wExp.toFixed(2)+'%')};">${fmt(t.expected)} expected · ${hitRate}%</div>
        </div>
        <div class="total"><b>${fmt(t.announced)}</b> MW</div>
      </div>`;
  }).join('');

  // ---------- Top 12 states table ----------
  const stateTotals = {};
  for (const r of S) {
    if (!stateTotals[r.plant_state]) stateTotals[r.plant_state] = { total:0, expected:0, byTech:{} };
    stateTotals[r.plant_state].total    += r.announced_mw || 0;
    stateTotals[r.plant_state].expected += r.expected_mw  || 0;
    stateTotals[r.plant_state].byTech[r.tech_group] = r.announced_mw || 0;
  }
  const topStates = Object.entries(stateTotals)
    .sort((a,b) => b[1].total - a[1].total).slice(0, 12);
  const stateTbody = document.querySelector('#eiaStateTable tbody');
  stateTbody.innerHTML = topStates.map(([st, d]) => {
    const cell = t => d.byTech[t] ? fmt(d.byTech[t]) : '—';
    const otherMw = (d.byTech.Geothermal||0) + (d.byTech.Hydro||0) + (d.byTech.Other||0);
    return `<tr>
      <td><b>${st}</b></td>
      <td class="r">${cell('Solar')}</td>
      <td class="r">${cell('Storage')}</td>
      <td class="r">${cell('Wind')}</td>
      <td class="r">${cell('Gas')}</td>
      <td class="r">${cell('Nuclear')}</td>
      <td class="r" style="color:var(--muted);">${otherMw ? fmt(otherMw) : '—'}</td>
      <td class="r"><b>${fmt(d.total)}</b></td>
      <td class="r" style="color:#0E7B5B;">${fmt(d.expected)}</td>
    </tr>`;
  }).join('');
})();

// ---- Sources table ----
(function(){
  const tbody = document.querySelector('#sourcesTable tbody');
  const C = DATA.contracts, L = DATA.lcoe, G = DATA.gas_capex,
        R = DATA.renewable_capex, D = DATA.demand, P = DATA.grid_plan,
        H = DATA.cumulative, T = DATA.turbine;
  function countCites(sid){
    return [C,L,G,R,D,P,H,T].reduce((s,arr)=>s+arr.filter(r=>r.source_id===sid).length, 0);
  }
  const rows = Object.values(SRC).sort((a,b)=>
    parseInt(a.id.slice(1))-parseInt(b.id.slice(1)));
  tbody.innerHTML = rows.map(r => `<tr>
    <td><b>${r.id}</b></td>
    <td>${r.publisher||''}</td>
    <td>${r.title||''}</td>
    <td>${r.pub_date||''}</td>
    <td>${r.kind||''}</td>
    <td style="max-width:380px;word-break:break-all"><a class="cite" href="${r.url}" target="_blank">${r.url}</a></td>
    <td style="text-align:right">${countCites(r.id)}</td>
  </tr>`).join('');
})();
</script>
</body></html>
"""


def main():
    if not DB.exists():
        raise SystemExit("data.db missing — run scripts/load.py first.")
    conn = sqlite3.connect(DB)
    try:
        OUT.write_text(build(conn))
    finally:
        conn.close()
    print(f"dashboard: {OUT}")


if __name__ == "__main__":
    main()
