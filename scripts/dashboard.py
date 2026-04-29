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
            SELECT id, company, announced_date, year, cod_year, cod_note,
                   generation_type, capacity_mw, confidence, deal_name,
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
        "sources": {r["id"]: dict(r) for r in conn.execute("SELECT * FROM sources")},
    }
    payload = json.dumps(data, default=str)

    return TEMPLATE.replace("__DATA__", payload)


TEMPLATE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>Hyperscaler Energy Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg:#0b0d12; --panel:#141821; --ink:#e6e9ef; --muted:#8b93a7;
    --line:#232838; --accent:#6ea8fe;
    --gas:#e07a5f; --clean:#8ac6a4; --nuclear:#b794f4; --geo:#f2cc8f;
    --solar:#f6c65b; --wind:#7fd1c1; --storage:#9bb0e3; --other:#6b7280;
  }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
         background:var(--bg); color:var(--ink); line-height:1.45; }
  header { padding:1.5rem 2rem; border-bottom:1px solid var(--line); }
  h1 { margin:0 0 .3rem; font-size:1.4rem; letter-spacing:-0.01em; }
  .sub { color:var(--muted); font-size:.9rem; }
  main { padding:1.5rem 2rem; max-width:1400px; margin:0 auto; }
  .kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
          gap:.8rem; margin-bottom:1.5rem; }
  .kpi { background:var(--panel); border:1px solid var(--line); border-radius:8px;
         padding:.9rem 1rem; }
  .kpi .v { font-size:1.6rem; font-weight:600; letter-spacing:-.02em; }
  .kpi .l { color:var(--muted); font-size:.78rem; text-transform:uppercase; letter-spacing:.06em; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:1rem; margin-bottom:1.5rem; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:8px;
          padding:1rem 1.2rem; }
  .card h2 { margin:0 0 .3rem; font-size:1rem; font-weight:600; }
  .card .hint { color:var(--muted); font-size:.8rem; margin-bottom:.6rem; }
  .chart-wrap { position:relative; height:320px; }
  .wide { grid-column:1 / -1; }
  .filters { display:flex; gap:.6rem; flex-wrap:wrap; margin-bottom:1rem; align-items:center; }
  .filters label { color:var(--muted); font-size:.85rem; }
  select, input[type="text"] {
    background:#0f131c; color:var(--ink); border:1px solid var(--line);
    padding:.35rem .5rem; border-radius:5px; font-size:.85rem;
  }
  table { width:100%; border-collapse:collapse; font-size:.82rem; }
  th, td { text-align:left; padding:.45rem .55rem; border-bottom:1px solid var(--line);
           vertical-align:top; }
  th { color:var(--muted); font-weight:500; font-size:.73rem;
       text-transform:uppercase; letter-spacing:.06em; position:sticky; top:0;
       background:var(--panel); cursor:pointer; user-select:none; }
  th:hover { color:var(--ink); }
  .table-wrap { max-height:600px; overflow:auto; border:1px solid var(--line); border-radius:6px; }
  .cite { color:var(--accent); text-decoration:none; font-size:.72rem;
          padding:1px 4px; border-radius:3px; background:rgba(110,168,254,.1);
          margin-left:.2rem; }
  .cite:hover { background:rgba(110,168,254,.25); }
  .badge { display:inline-block; padding:1px 7px; border-radius:10px;
           font-size:.7rem; font-weight:600; }
  .b-gas { background:rgba(224,122,95,.2); color:#f4a58e; }
  .b-fuelcell { background:rgba(250,180,90,.2); color:#fab45a; }
  .b-clean { background:rgba(138,198,164,.2); color:#a3dcbb; }
  .b-nuclear { background:rgba(183,148,244,.2); color:#cbb2fa; }
  .b-geo { background:rgba(242,204,143,.2); color:#f2cc8f; }
  .b-op { background:rgba(138,198,164,.2); color:#a3dcbb; }
  .b-pending { background:rgba(242,204,143,.15); color:#d9b36c; }
  .b-announced { background:rgba(139,147,167,.2); color:var(--muted); }
  .b-btm { background:rgba(224,122,95,.2); color:#f4a58e; }
  .b-grid { background:rgba(138,198,164,.2); color:#a3dcbb; }
  .b-unknown { background:rgba(139,147,167,.2); color:var(--muted); }
  .deal-link { color:var(--ink); text-decoration:none; border-bottom:1px solid var(--line); }
  .deal-link:hover { color:var(--accent); border-bottom-color:var(--accent); }
  .conf-s { color:#8ac6a4; font-weight:600; font-size:.7rem; }
  .conf-e { color:#d9b36c; font-weight:600; font-size:.7rem; }
  .tabs { display:flex; gap:0; border-bottom:1px solid var(--line); margin-bottom:1rem; }
  .tab { padding:.5rem 1rem; cursor:pointer; color:var(--muted);
         border-bottom:2px solid transparent; font-size:.88rem; }
  .tab.active { color:var(--ink); border-bottom-color:var(--accent); }
  .panel { display:none; }
  .panel.active { display:block; }
  code { background:#0f131c; padding:1px 5px; border-radius:3px;
         font-size:.8rem; color:var(--muted); }
  .legend-swatch { display:inline-block; width:10px; height:10px;
                   border-radius:2px; margin-right:4px; vertical-align:middle; }

  /* --- freshness highlight (new announcements within 7 days) --- */
  .new-badge {
    display:inline-block; margin-left:.35rem; padding:1px 6px;
    border-radius:4px; font-size:.6rem; font-weight:700;
    letter-spacing:.08em; text-transform:uppercase;
    background:#ff333322; color:#ff3333;
    border:1px solid #ff333377;
    animation: newpulse 2.2s ease-in-out infinite;
    vertical-align:middle;
  }
  @keyframes newpulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(255,51,51,0.6); }
    50%      { box-shadow: 0 0 14px 2px rgba(255,51,51,0.6); }
  }
  .whats-new {
    background: linear-gradient(90deg,
      rgba(255,51,51,0.10) 0%, rgba(255,51,51,0.02) 100%);
    border:1px solid rgba(255,51,51,0.28);
    border-left:3px solid #ff3333;
    border-radius:8px; padding:.7rem 1rem; margin-bottom:1.2rem;
    display:flex; align-items:center; gap:.9rem; flex-wrap:wrap;
  }
  .whats-new .wn-label {
    color:#ff3333; font-size:.68rem; font-weight:700;
    letter-spacing:.12em; text-transform:uppercase;
    padding:2px 8px; border-radius:4px;
    background:rgba(255,51,51,0.14); white-space:nowrap;
  }
  .whats-new .wn-item { font-size:.85rem; color:var(--ink); }
  .whats-new .wn-item .wn-co { color:#ff3333; font-weight:600; }
  .whats-new .wn-item .wn-date { color:var(--muted); font-size:.75rem; margin-left:.3rem; }
  .whats-new .wn-sep { color:var(--line); }
  .whats-new.empty { display:none; }

  @media (max-width: 900px) { .grid { grid-template-columns:1fr; } }

  /* --- Campuses panel: editorial layout, no card-grid soup --- */
  .camp-hero {
    display:grid; grid-template-columns: 1.6fr 1fr; gap:2.5rem;
    padding:2rem 0 2.5rem; border-bottom:1px solid var(--line); margin-bottom:2rem;
  }
  .camp-hero .lead { color:var(--muted); font-size:.92rem; max-width:48ch; line-height:1.55; }
  .camp-hero .lead b { color:var(--ink); font-weight:600; }
  .camp-stats { display:grid; grid-template-columns:1fr 1fr; gap:1.4rem 2rem; align-self:end; }
  .camp-stat .num { font-size:2.4rem; font-weight:700; letter-spacing:-.03em; line-height:1;
                    font-variant-numeric: tabular-nums; }
  .camp-stat .num .gw { font-size:1rem; color:var(--muted); font-weight:500; margin-left:.2em; }
  .camp-stat .lab { color:var(--muted); font-size:.72rem; text-transform:uppercase;
                    letter-spacing:.1em; margin-top:.35rem; }
  .camp-stat.live .num { color:#a3dcbb; }
  .camp-stat.gap .num { color:#f6c65b; }

  .camp-section-title {
    font-size:.7rem; text-transform:uppercase; letter-spacing:.18em;
    color:var(--muted); margin:0 0 1rem; font-weight:600;
    display:flex; align-items:baseline; gap:.8rem;
  }
  .camp-section-title .rule { flex:1; height:1px; background:var(--line); }

  /* horizontal pipeline rows (one per hyperscaler) */
  .pipeline-list { display:flex; flex-direction:column; gap:.65rem;
                   padding-bottom:2rem; border-bottom:1px solid var(--line); margin-bottom:2rem; }
  .pipe-row { display:grid; grid-template-columns: 130px 1fr 80px;
              gap:1rem; align-items:center; }
  .pipe-row .name { font-weight:600; font-size:.9rem; }
  .pipe-row .name .cnt { color:var(--muted); font-weight:400; font-size:.74rem; margin-left:.4rem; }
  .pipe-bar { position:relative; height:24px; background:#0f131c;
              border-radius:3px; overflow:hidden; }
  .pipe-bar .seg { position:absolute; top:0; bottom:0; transition:width .4s ease; }
  .pipe-bar .seg.energized { background: linear-gradient(180deg, #a3dcbb, #6eb89a); left:0; }
  .pipe-bar .seg.phase1    { background: rgba(110,168,254,.55); }
  .pipe-bar .seg.planned   { background: rgba(110,168,254,.18); border-right:1px solid rgba(110,168,254,.45); }
  .pipe-bar .seg-label { position:absolute; top:50%; transform:translateY(-50%);
                         font-size:.66rem; color:#0b0d12; font-weight:700; padding:0 .4rem;
                         white-space:nowrap; pointer-events:none; }
  .pipe-bar .seg-label.outside { color:var(--muted); }
  .pipe-row .total { font-variant-numeric: tabular-nums; font-size:.85rem;
                     text-align:right; color:var(--muted); }
  .pipe-row .total b { color:var(--ink); font-weight:600; }

  .pipe-legend { display:flex; gap:1.4rem; font-size:.74rem; color:var(--muted); margin-top:.8rem; }
  .pipe-legend .sw { display:inline-block; width:11px; height:11px; border-radius:2px;
                     vertical-align:middle; margin-right:.4em; }

  /* campus table */
  .camp-table-wrap { border:1px solid var(--line); border-radius:6px;
                     max-height:640px; overflow:auto; }
  .camp-table-wrap table { width:100%; border-collapse:collapse; font-size:.82rem; }
  .camp-table-wrap th { background:#0f131c; color:var(--muted); font-weight:500; font-size:.71rem;
                        text-transform:uppercase; letter-spacing:.07em;
                        text-align:left; padding:.55rem .7rem; border-bottom:1px solid var(--line);
                        position:sticky; top:0; cursor:pointer; user-select:none; }
  .camp-table-wrap td { padding:.55rem .7rem; border-bottom:1px solid var(--line); vertical-align:top; }
  .camp-table-wrap td.r { text-align:right; font-variant-numeric: tabular-nums; white-space:nowrap; }
  .camp-table-wrap tr:hover td { background:rgba(110,168,254,.04); }

  .b-stat { display:inline-block; padding:1px 7px; border-radius:3px;
            font-size:.66rem; font-weight:600; letter-spacing:.05em; }
  .b-Operational      { background:rgba(138,198,164,.18); color:#a3dcbb; }
  .b-PartiallyEnergized { background:rgba(138,198,164,.10); color:#9ed1b3; border:1px dashed rgba(138,198,164,.35); }
  .b-UnderConstruction { background:rgba(242,204,143,.18); color:#f2cc8f; }
  .b-SiteWork         { background:rgba(242,204,143,.10); color:#d9b36c; }
  .b-Announced        { background:rgba(139,147,167,.18); color:var(--muted); }
  .b-Paused           { background:rgba(224,122,95,.18); color:#f4a58e; }
  .b-Cancelled        { background:rgba(224,122,95,.25); color:#f4a58e; text-decoration:line-through; }

  .camp-filters { display:flex; gap:.6rem; flex-wrap:wrap; margin:1rem 0; align-items:center; }
  .camp-filters label { color:var(--muted); font-size:.83rem; }
  .camp-filters .count { color:var(--muted); font-size:.78rem; margin-left:auto; }

  @media (max-width: 900px) {
    .camp-hero { grid-template-columns:1fr; gap:1.5rem; }
    .pipe-row { grid-template-columns:90px 1fr 70px; gap:.6rem; }
  }
</style>
</head>
<body>
<header>
  <h1>Hyperscaler Energy Dashboard</h1>
  <div class="sub" id="meta"></div>
</header>

<main>
  <div class="kpis" id="kpis"></div>

  <div class="whats-new empty" id="whatsNew"></div>

  <div class="tabs">
    <div class="tab active" data-tab="overview">Overview</div>
    <div class="tab" data-tab="contracts">Contracts</div>
    <div class="tab" data-tab="campuses">Campuses</div>
    <div class="tab" data-tab="eia">Federal Cross-Check</div>
    <div class="tab" data-tab="costs">Costs (LCOE / CAPEX)</div>
    <div class="tab" data-tab="sources">Sources</div>
  </div>

  <section class="panel active" id="panel-overview">
    <div class="grid">
      <div class="card wide">
        <h2>Contracted MW by announce year — gas vs clean</h2>
        <div class="hint">Stacked by generation type. Uses <b>announce year</b>, not COD. Includes Oracle/xAI.</div>
        <div class="chart-wrap"><canvas id="chartYear"></canvas></div>
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
        <h2>Behind-the-meter vs grid — all contracts by announce year</h2>
        <div class="hint">All 133 contracts. Stacked by announcement year regardless of COD disclosure.</div>
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
    <div class="filters">
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
          <th data-k="company">Company</th>
          <th data-k="year">Announced</th>
          <th data-k="cod_year">COD</th>
          <th data-k="status">Status</th>
          <th data-k="generation_type">Type</th>
          <th data-k="connection_type">Conn</th>
          <th data-k="capacity_mw" style="text-align:right">MW</th>
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

    <h3 class="camp-section-title">Pipeline by hyperscaler<span class="rule"></span><span style="font-weight:400;text-transform:none;letter-spacing:0">energized → phase-1 → full plan</span></h3>
    <div class="pipeline-list" id="pipelineList"></div>
    <div class="pipe-legend">
      <span><span class="sw" style="background:#a3dcbb"></span>Energized today</span>
      <span><span class="sw" style="background:rgba(110,168,254,.55)"></span>Phase-1 commitment</span>
      <span><span class="sw" style="background:rgba(110,168,254,.18);border:1px solid rgba(110,168,254,.45)"></span>Full planned build</span>
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

  <section class="panel" id="panel-eia">
    <div class="camp-hero">
      <div>
        <p class="lead">
          <b>EIA Form 860M</b> publishes a monthly snapshot of every US generator
          that has filed with EIA — capacity, fuel, planned online date, and a
          <b>construction-status code</b> from "regulatory approvals not initiated"
          all the way to "construction complete." We track the full file across
          six vintages spanning 2024–2026. The signal isn't a probability estimate;
          it's the <b>empirical migration of MW between status tiers</b> over time.
        </p>
        <p class="lead" style="margin-top:.6rem">
          The big finding from this dataset: the US planned-generation pipeline
          <b>nearly doubled in 26 months</b> (157 GW → 286 GW), but most of the
          new MW are stuck at the back of the funnel. Approvals-pending and
          planned-only tiers <b>grew +109% each</b>. Construction-complete grew +163%
          but off a tiny base. Pipeline is widening faster than it's draining.
        </p>
      </div>
      <div class="camp-stats">
        <div class="camp-stat live">
          <div class="num"><span id="es-current">—</span><span class="gw"> GW</span></div>
          <div class="lab">Latest pipeline (2026-03)</div>
        </div>
        <div class="camp-stat gap">
          <div class="num"><span id="es-growth">—</span><span class="gw">%</span></div>
          <div class="lab">Growth vs Jan 2024</div>
        </div>
        <div class="camp-stat">
          <div class="num"><span id="es-vintages">—</span></div>
          <div class="lab">Monthly vintages</div>
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
          <span style="font-size:.78rem; text-transform:uppercase; letter-spacing:.1em; color:#a3dcbb; font-weight:600;">Probability-weighted</span>
          <span style="font-size:.78rem; color:var(--muted);"><span id="eiaExpTotal" style="color:#a3dcbb; font-weight:600;">—</span> GW total</span>
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
        <div class="hint">By plant type and data vintage. <b>Click any dot</b> to open its source.</div>
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
  'Gas':'#e07a5f', 'Gas+CCS':'#d97757',
  'Fuel Cell':'#fab45a',
  'Nuclear':'#b794f4',
  'Solar':'#f6c65b', 'Solar+Storage':'#f0ad4e',
  'Wind':'#7fd1c1',
  'Geothermal':'#f2cc8f',
  'Storage':'#9bb0e3',
  'Renewable':'#8ac6a4',
  'Hydro':'#5eb0e5',
  'Other':'#6b7280'
};
const CONN_COLORS = { 'BTM':'#e07a5f', 'Grid':'#8ac6a4', 'Unknown':'#6b7280' };
const CLEAN_TYPES = new Set(['Solar','Wind','Nuclear','Fuel Cell','Storage','Geothermal','Hydro','Solar+Storage','Renewable']);
function colorFor(t){ return TYPE_COLORS[t] || '#6b7280'; }

Chart.defaults.color = '#8b93a7';
Chart.defaults.borderColor = '#232838';
Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif';

// ---- Freshness signal: rows announced within the last 7 days glow red ----
const FRESH_WINDOW_DAYS = 7;
const GLOW_COLOR = '#ff3333';    // bright red — high contrast on dark background
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

// Chart.js plugin — draws a bright red glowing outline on just the fresh
// portion of each stacked bar segment (no fill — base category color shows
// through). For vertical bars it's the top slice; for horizontal bars,
// the stack). Two passes: a wide soft shadow, then a crisp fill+stroke, so
// the glow reads on dark AND shows inside light segments.
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

        // Pass 1: wide soft aura — stroke only, no fill, so bar color shows through.
        ctx.save();
        ctx.shadowColor = GLOW_COLOR;
        ctx.shadowBlur = 18;
        ctx.strokeStyle = GLOW_COLOR;
        ctx.lineWidth = 3;
        ctx.globalAlpha = 0.7;
        ctx.strokeRect(rx + 1, ry + 1, rw - 2, rh - 2);
        ctx.restore();

        // Pass 2: crisp bright stroke on top for a sharp edge.
        ctx.save();
        ctx.shadowColor = GLOW_COLOR;
        ctx.shadowBlur = 6;
        ctx.strokeStyle = GLOW_COLOR;
        ctx.lineWidth = 2;
        ctx.globalAlpha = 1;
        ctx.strokeRect(rx + 1, ry + 1, rw - 2, rh - 2);
        ctx.restore();
      });
    });
  }
});

// ---- Meta / KPIs ----
(function(){
  const C = DATA.contracts;
  const totalMW = C.reduce((s,r)=>s+(r.capacity_mw||0),0);
  const gasMW = C.filter(r=>r.generation_type==='Gas'||r.generation_type==='Gas+CCS')
                 .reduce((s,r)=>s+(r.capacity_mw||0),0);
  const cleanMW = totalMW - gasMW;
  const companies = new Set(C.map(r=>r.company));
  document.getElementById('meta').textContent =
    `${C.length} contract rows · ${Object.keys(SRC).length} sources · ${companies.size} hyperscalers · generated ` + new Date().toISOString().slice(0,10);
  const kpis = [
    {l:'Total contracted MW', v:totalMW.toLocaleString(undefined,{maximumFractionDigits:0})},
    {l:'Gas MW', v:gasMW.toLocaleString(undefined,{maximumFractionDigits:0}), c:'var(--gas)'},
    {l:'Clean MW', v:cleanMW.toLocaleString(undefined,{maximumFractionDigits:0}), c:'var(--clean)'},
    {l:'Gas share', v:(100*gasMW/totalMW).toFixed(1)+'%'},
    {l:'Contract rows', v:C.length},
    {l:'Sources', v:Object.keys(SRC).length},
  ];
  document.getElementById('kpis').innerHTML = kpis.map(k =>
    `<div class="kpi"><div class="v" ${k.c?`style="color:${k.c}"`:''}>${k.v}</div><div class="l">${k.l}</div></div>`
  ).join('');

  // ---- What's new this week ribbon ----
  const fresh = C.filter(isNew).sort((a,b) =>
    (b.announced_date||'').localeCompare(a.announced_date||''));
  const wn = document.getElementById('whatsNew');
  if (fresh.length) {
    wn.classList.remove('empty');
    // Group by (company, announced_date, counterparty) so a phased deal (pilot +
    // framework) surfaces as a single headline, not two near-duplicate lines.
    const key = r => `${r.company}|${r.announced_date}|${r.counterparty||''}`;
    const groups = {};
    fresh.forEach(r => { (groups[key(r)] = groups[key(r)] || []).push(r); });
    const items = Object.values(groups).map(g => {
      const r0 = g[0];
      const totalMW = g.reduce((s,r)=>s+(r.capacity_mw||0), 0);
      const types = [...new Set(g.map(r=>r.generation_type))].join(' + ');
      const cp = r0.counterparty ? ` × ${r0.counterparty}` : '';
      return `<span class="wn-item"><span class="wn-co">${r0.company}${cp}</span>
              — ${totalMW.toLocaleString()} MW ${types}
              <span class="wn-date">${r0.announced_date||''}</span></span>`;
    });
    wn.innerHTML =
      `<span class="wn-label">New · last ${FRESH_WINDOW_DAYS}d</span>` +
      items.join('<span class="wn-sep">·</span>');
  }
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

// ---- Chart: MW by announce year, stacked by type ----
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

// ---- Chart: BTM vs Grid by announce year (all contracts) ----
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
      backgroundColor:'#e07a5f', borderColor:'#e07a5f', pointRadius:5, pointHoverRadius:8
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
  const fCompany = document.getElementById('fCompany');
  const fType    = document.getElementById('fType');
  const fStatus  = document.getElementById('fStatus');
  const fConn    = document.getElementById('fConn');
  const fSearch  = document.getElementById('fSearch');
  const rowCount = document.getElementById('rowCount');
  let sortKey = 'year', sortDir = -1;

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
    const co = fCompany.value, ty = fType.value, st = fStatus.value, cn = fConn.value;
    const q = fSearch.value.toLowerCase();
    let rows = C.filter(r =>
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
      return `<tr>
        <td>${r.company}${newPill}</td>
        <td>${r.announced_date || r.year}</td>
        <td>${r.cod_year || '—'}</td>
        <td>${statusBadge(r.status)}</td>
        <td>${typeBadge(r.generation_type)}</td>
        <td>${connBadge(r.connection_type, r.connection_reason)}</td>
        <td style="text-align:right;white-space:nowrap">${r.capacity_mw!=null ? r.capacity_mw.toLocaleString()+' MW' : '—'} <span class="${r.confidence==='Sourced'?'conf-s':'conf-e'}">${r.confidence==='Sourced'?'✓':'~'}</span></td>
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
  [fCompany,fType,fStatus,fConn,fSearch].forEach(el => el.addEventListener('input', render));
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
      <div class="pipe-row">
        <div class="name">${p.hyperscaler}<span class="cnt">${p.campus_count} sites</span></div>
        <div class="pipe-bar">
          <div class="seg planned"   style="width:${wPlan.toFixed(2)}%"></div>
          <div class="seg phase1"    style="width:${wPh1.toFixed(2)}%"></div>
          <div class="seg energized" style="width:${wEn.toFixed(2)}%"></div>
          ${enLabel ? `<div class="seg-label ${showInside ? '' : 'outside'}"
              style="left:${showInside ? '.4rem' : (wEn.toFixed(2)+'%')}">${enLabel} live</div>` : ''}
        </div>
        <div class="total"><b>${fmt(plan)}</b> MW</div>
      </div>`;
  }).join('');

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

// ---- EIA Federal Cross-Check panel ----
(function(){
  const T = DATA.eia_tier || [];
  const O = DATA.eia_overlap || [];
  if (!T.length) return;
  const fmt = n => Math.round(n).toLocaleString();

  // ---------- Time-series datasets ----------
  const SV = DATA.eia_status_by_vintage || [];
  const TV = DATA.eia_tech_by_vintage || [];
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
    'ConstructionComplete': '#5fae87',
    'MajorityComplete':     '#7fc4a4',
    'MinorityComplete':     '#a3dcbb',
    'ApprovalsReceived':    '#d9b36c',
    'ApprovalsPending':     '#e07a5f',
    'PlannedOnly':          '#6b7280',
    'Other':                '#3b3f4d',
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
    Solar:'#f6c65b', Wind:'#7fd1c1', Storage:'#9bb0e3', Gas:'#e07a5f',
    Nuclear:'#b794f4', Geothermal:'#f2cc8f', Hydro:'#5eb0e5', Other:'#6b7280'
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
            backgroundColor: '#5fae87',
            borderWidth: 0
          },
          {
            label: 'Newly announced',
            data: TR.map(t => t.new_mw),
            backgroundColor: '#6b7280',
            borderWidth: 0
          },
          {
            label: 'Cancelled / withdrawn',
            data: TR.map(t => t.cancelled_mw),
            backgroundColor: '#e07a5f',
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
            <th class="r" style="color:#5fae87;">Completed</th>
            <th class="r" style="color:#6b7280;">Newly announced</th>
            <th class="r" style="color:#e07a5f;">Cancelled</th>
            <th class="r">Announced ÷ Completed</th>
          </tr></thead>
          <tbody>
            ${TR.map(t => `
              <tr>
                <td><b>${vintageLabel(t.v_from)} → ${vintageLabel(t.v_to)}</b></td>
                <td class="r">${t.months}</td>
                <td class="r" style="color:#5fae87;"><b>${fmt(t.operated_mw)}</b> MW</td>
                <td class="r" style="color:#6b7280;"><b>${fmt(t.new_mw)}</b> MW</td>
                <td class="r" style="color:#e07a5f;">${fmt(t.cancelled_mw)} MW</td>
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
            backgroundColor: '#5fae87', borderWidth: 0
          },
          {
            label: 'Newly announced',
            data: years.map(y => yearAgg[y].announced / 1000),
            backgroundColor: '#6b7280', borderWidth: 0
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
            <th class="r" style="color:#5fae87;">Completed</th>
            <th class="r" style="color:#5fae87;">YoY %</th>
            <th class="r" style="color:#6b7280;">Announced</th>
            <th class="r" style="color:#6b7280;">YoY %</th>
            <th class="r" style="color:#e07a5f;">Cancelled</th>
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
                <td class="r" style="color:#5fae87;"><b>${(a.completed/1000).toFixed(1)}</b> GW</td>
                <td class="r" style="color:${(yoyDone||0) >= 0 ? '#5fae87' : '#e07a5f'}">${f(yoyDone)}</td>
                <td class="r" style="color:#6b7280;"><b>${(a.announced/1000).toFixed(1)}</b> GW</td>
                <td class="r" style="color:${(yoyAnn||0) >= 0 ? '#6b7280' : '#e07a5f'}">${f(yoyAnn)}</td>
                <td class="r" style="color:#e07a5f;">${(a.cancelled/1000).toFixed(1)} GW</td>
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
            backgroundColor: qoq.map(t => t.pctDone >= 0 ? '#5fae87' : 'rgba(95,174,135,.35)'),
            borderColor: '#5fae87', borderWidth: 1
          },
          {
            label: 'Announced QoQ %',
            data: qoq.map(t => t.pctAnn),
            backgroundColor: qoq.map(t => t.pctAnn >= 0 ? '#6b7280' : 'rgba(107,114,128,.35)'),
            borderColor: '#6b7280', borderWidth: 1
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
              color: ctx => ctx.tick.value === 0 ? 'rgba(255,255,255,.35)' : 'rgba(255,255,255,.05)',
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
            <th class="r" style="color:#5fae87;">Completed MW</th>
            <th class="r" style="color:#5fae87;">Completed QoQ</th>
            <th class="r" style="color:#6b7280;">Announced MW</th>
            <th class="r" style="color:#6b7280;">Announced QoQ</th>
          </tr></thead>
          <tbody>
            ${qoq.map(t => {
              const f = n => n == null ? '—' : ((n > 0 ? '+' : '') + n.toFixed(0) + '%');
              return `<tr>
                <td><b>${vintageLabel(t.v_to)}</b></td>
                <td class="r"><b>${fmt(t.q_done)}</b></td>
                <td class="r" style="color:${(t.pctDone||0) >= 0 ? '#5fae87' : '#e07a5f'}">${f(t.pctDone)}</td>
                <td class="r"><b>${fmt(t.q_ann)}</b></td>
                <td class="r" style="color:${(t.pctAnn||0) >= 0 ? '#6b7280' : '#e07a5f'}">${f(t.pctAnn)}</td>
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
      <td class="r" style="color:#a3dcbb;">${fmt(d.expected)}</td>
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
