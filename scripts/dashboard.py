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


def build(conn: sqlite3.Connection) -> str:
    conn.row_factory = sqlite3.Row

    data = {
        "contracts": q(conn, """
            SELECT id, company, announced_date, year, cod_year, cod_note,
                   generation_type, capacity_mw, confidence, deal_name,
                   counterparty, contract_years, geography, status, notes, source_id
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
  @media (max-width: 900px) { .grid { grid-template-columns:1fr; } }
</style>
</head>
<body>
<header>
  <h1>Hyperscaler Energy Dashboard</h1>
  <div class="sub" id="meta"></div>
</header>

<main>
  <div class="kpis" id="kpis"></div>

  <div class="tabs">
    <div class="tab active" data-tab="overview">Overview</div>
    <div class="tab" data-tab="contracts">Contracts</div>
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

  <section class="panel" id="panel-costs">
    <div class="grid">
      <div class="card wide">
        <h2>LCOE ($/MWh) — unsubsidized, US</h2>
        <div class="hint">Mid-point; hover a bar to see low/high range.</div>
        <div class="chart-wrap"><canvas id="chartLcoe"></canvas></div>
      </div>
      <div class="card">
        <h2>Gas plant CAPEX ($/kW)</h2>
        <div class="hint">By plant type and data vintage.</div>
        <div class="chart-wrap"><canvas id="chartGasCapex"></canvas></div>
      </div>
      <div class="card">
        <h2>Renewable &amp; nuclear CAPEX ($/kW)</h2>
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
const CLEAN_TYPES = new Set(['Solar','Wind','Nuclear','Fuel Cell','Storage','Geothermal','Hydro','Solar+Storage','Renewable']);
function colorFor(t){ return TYPE_COLORS[t] || '#6b7280'; }

Chart.defaults.color = '#8b93a7';
Chart.defaults.borderColor = '#232838';
Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif';

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
  const ds = types.map(t => ({
    label: t,
    data: years.map(y => C.filter(r=>r.year===y && r.generation_type===t)
                         .reduce((s,r)=>s+(r.capacity_mw||0),0)),
    backgroundColor: colorFor(t), borderWidth:0
  })).filter(d => d.data.some(v=>v>0));
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
  const ds = types.map(t => ({
    label: t,
    data: companies.map(co => C.filter(r=>r.company===co && r.generation_type===t)
                                .reduce((s,r)=>s+(r.capacity_mw||0),0)),
    backgroundColor: colorFor(t), borderWidth:0
  })).filter(d=>d.data.some(v=>v>0));
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
  const gas  = years.map(y=>C.filter(r=>r.cod_year===y && (r.generation_type==='Gas'||r.generation_type==='Gas+CCS'))
                             .reduce((s,r)=>s+(r.capacity_mw||0),0));
  const clean= years.map(y=>C.filter(r=>r.cod_year===y && CLEAN_TYPES.has(r.generation_type))
                             .reduce((s,r)=>s+(r.capacity_mw||0),0));
  new Chart(document.getElementById('chartCod'), {
    type:'bar',
    data:{ labels:years, datasets:[
      {label:'Gas', data:gas, backgroundColor:'#e07a5f'},
      {label:'Clean', data:clean, backgroundColor:'#8ac6a4'}
    ]},
    options:{ maintainAspectRatio:false, responsive:true,
      scales:{ x:{stacked:true}, y:{stacked:true, ticks:{callback:v=>v.toLocaleString()+' MW'}} },
      plugins:{ legend:{position:'bottom'} }
    }
  });
})();

// ---- Chart: MW horizontal by company/type ----
(function(){
  const C = DATA.contracts;
  const companies = [...new Set(C.map(r=>r.company))].sort();
  const types = [...new Set(C.map(r=>r.generation_type))];
  const ds = types.map(t => ({
    label:t,
    data: companies.map(co => C.filter(r=>r.company===co && r.generation_type===t)
                                .reduce((s,r)=>s+(r.capacity_mw||0),0)),
    backgroundColor: colorFor(t), borderWidth:0
  })).filter(d=>d.data.some(v=>v>0));
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
  new Chart(document.getElementById('chartGasCapex'), {
    type:'scatter',
    data:{ datasets: [...new Set(G.map(r=>r.plant_type))].map(pt => ({
      label: pt,
      data: G.filter(r=>r.plant_type===pt).map(r => ({x:r.year, y:r.cost_mid_kw||r.cost_low_kw||r.cost_high_kw, label:r.label})),
      backgroundColor:'#e07a5f', borderColor:'#e07a5f', pointRadius:5
    }))},
    options:{ maintainAspectRatio:false, responsive:true,
      scales:{ x:{type:'linear', ticks:{stepSize:1, callback:v=>v}},
               y:{ticks:{callback:v=>'$'+v.toLocaleString()+'/kW'}} },
      plugins:{ legend:{position:'bottom'},
                tooltip:{callbacks:{label:c=>`${c.raw.label}: $${c.raw.y.toLocaleString()}/kW (${c.raw.x})`}} }
    }
  });
})();

// ---- Chart: Renewable CAPEX ----
(function(){
  const R = DATA.renewable_capex.sort((a,b)=>a.year-b.year);
  const techs = [...new Set(R.map(r=>r.technology))];
  new Chart(document.getElementById('chartRenCapex'), {
    type:'scatter',
    data:{ datasets: techs.map(t => ({
      label:t,
      data: R.filter(r=>r.technology===t).map(r => ({x:r.year, y:r.cost_mid_kw||r.cost_low_kw||r.cost_high_kw, label:r.label})),
      backgroundColor: colorFor(t.split('-')[0]),
      borderColor: colorFor(t.split('-')[0]), pointRadius:5
    }))},
    options:{ maintainAspectRatio:false, responsive:true,
      scales:{ x:{type:'linear', ticks:{stepSize:1, callback:v=>v}},
               y:{ticks:{callback:v=>'$'+v.toLocaleString()+'/kW'}} },
      plugins:{ legend:{position:'bottom', labels:{boxWidth:10, boxHeight:10}},
                tooltip:{callbacks:{label:c=>`${c.raw.label}: $${c.raw.y.toLocaleString()}/kW (${c.raw.x})`}} }
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
  const fSearch  = document.getElementById('fSearch');
  const rowCount = document.getElementById('rowCount');
  let sortKey = 'year', sortDir = -1;

  [...new Set(C.map(r=>r.company))].sort().forEach(v =>
    fCompany.insertAdjacentHTML('beforeend', `<option>${v}</option>`));
  [...new Set(C.map(r=>r.generation_type))].sort().forEach(v =>
    fType.insertAdjacentHTML('beforeend', `<option>${v}</option>`));
  [...new Set(C.map(r=>r.status))].sort().forEach(v =>
    fStatus.insertAdjacentHTML('beforeend', `<option>${v}</option>`));

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
  function cite(sid){
    const s = SRC[sid];
    if (!s) return sid;
    return `<a class="cite" href="${s.url}" target="_blank" title="${(s.title||'').replace(/"/g,'&quot;')}">${sid}</a>`;
  }

  function render(){
    const co = fCompany.value, ty = fType.value, st = fStatus.value;
    const q = fSearch.value.toLowerCase();
    let rows = C.filter(r =>
      (!co || r.company===co) &&
      (!ty || r.generation_type===ty) &&
      (!st || r.status===st) &&
      (!q || [r.deal_name, r.counterparty, r.notes, r.cod_note].some(x => (x||'').toLowerCase().includes(q)))
    );
    rows.sort((a,b) => {
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
      return `<tr>
        <td>${r.company}</td>
        <td>${r.announced_date || r.year}</td>
        <td>${r.cod_year || '—'}</td>
        <td>${statusBadge(r.status)}</td>
        <td>${typeBadge(r.generation_type)}</td>
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
  [fCompany,fType,fStatus,fSearch].forEach(el => el.addEventListener('input', render));
  render();
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
