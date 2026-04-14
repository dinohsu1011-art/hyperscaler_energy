"""Generate a single self-contained HTML report. Every number is a clickable link
to its source URL. Groups facts by section so you can verify by clicking."""
from __future__ import annotations

import html
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data.db"
OUT = ROOT / "output" / "report.html"


def q(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def cite(sid: str, url: str) -> str:
    # superscript link to source
    return f'<sup><a href="{html.escape(url)}" target="_blank" title="{html.escape(sid)}">[{html.escape(sid)}]</a></sup>'


def num(v, fmt="{:,.0f}") -> str:
    if v is None:
        return "—"
    try:
        return fmt.format(float(v))
    except Exception:
        return html.escape(str(v))


def conf_badge(c: str) -> str:
    color = {"Sourced": "#1f8a3b", "Estimated": "#b47100"}.get(c, "#666")
    return f'<span style="color:{color};font-size:0.8em;font-weight:600;">{html.escape(c)}</span>'


def build(conn: sqlite3.Connection) -> str:
    conn.row_factory = sqlite3.Row
    src = {r["id"]: r for r in q(conn, "SELECT * FROM sources")}

    def url_of(sid):
        return src[sid]["url"] if sid in src else "#"

    parts = []
    parts.append("""<!doctype html><html><head><meta charset="utf-8">
<title>Hyperscaler Energy Database — Verification Report</title>
<style>
 body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
      margin:2rem auto;max-width:1100px;padding:0 1rem;color:#1d1d1f;line-height:1.45}
 h1{border-bottom:2px solid #1d1d1f;padding-bottom:.3rem}
 h2{margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.2rem}
 h3{margin-top:1.5rem;color:#333}
 table{border-collapse:collapse;width:100%;margin:.7rem 0;font-size:.92em}
 th,td{border:1px solid #e5e5e5;padding:.35rem .5rem;text-align:left;vertical-align:top}
 th{background:#f6f6f6;font-weight:600}
 tr:nth-child(even) td{background:#fafafa}
 sup a{text-decoration:none;color:#0366d6;font-weight:600;padding:0 2px}
 sup a:hover{text-decoration:underline;background:#eef5ff}
 .legend{background:#f6f8fa;padding:.6rem .9rem;border-radius:6px;font-size:.9em;margin:.7rem 0}
 .meta{color:#666;font-size:.9em}
 .note{color:#555;font-size:.85em;font-style:italic}
 .sources th,.sources td{font-size:.85em}
 .sources td.url{word-break:break-all;max-width:380px}
 nav a{margin-right:.8rem}
 .kpi{display:inline-block;background:#eef5ff;padding:.3rem .6rem;border-radius:4px;margin:.2rem;font-size:.9em}
</style></head><body>""")

    parts.append("<h1>Hyperscaler Energy — Verification Report</h1>")
    counts = q(conn, """
        SELECT (SELECT COUNT(*) FROM sources) AS s,
               (SELECT COUNT(*) FROM v_fact_provenance) AS f""")[0]
    parts.append(f'<p class="meta">Auto-generated from <code>data.db</code>. '
                 f'{counts["f"]} fact rows, {counts["s"]} sources. Click any <sup>[S##]</sup> marker to open the source URL in a new tab.</p>')
    parts.append("""<div class="legend"><b>Confidence:</b> """
                 f'{conf_badge("Sourced")} = exact number in the cited source; '
                 f'{conf_badge("Estimated")} = derived (split, residual, interpolated) — see row notes for method.'
                 "</div>")
    parts.append('<nav>'
                 '<a href="#contracts">Contracts</a>'
                 '<a href="#lcoe">LCOE</a>'
                 '<a href="#gas-capex">Gas CAPEX</a>'
                 '<a href="#ren-capex">Renewable CAPEX</a>'
                 '<a href="#turbine">Turbine Supply</a>'
                 '<a href="#demand">Demand</a>'
                 '<a href="#grid">Grid Plan</a>'
                 '<a href="#cum">Cumulative</a>'
                 '<a href="#sources">Sources</a>'
                 '</nav>')

    # --- Contracts ---
    parts.append('<h2 id="contracts">1. Hyperscaler energy contracts</h2>')
    parts.append('<p class="meta"><b>Announced</b> = year the deal was announced. '
                 '<b>COD</b> = commercial operation date (blank = not disclosed / range). '
                 'None of these deals are producing electrons today unless <b>Status=Operational</b>.</p>')
    for (company,) in q(conn, "SELECT DISTINCT company FROM hyperscaler_contracts ORDER BY company"):
        parts.append(f"<h3>{html.escape(company)}</h3>")
        rows = q(conn, """SELECT year, cod_year, cod_note, status, generation_type, capacity_mw,
                                 confidence, deal_name, counterparty, contract_years, source_id, notes
                          FROM hyperscaler_contracts WHERE company=?
                          ORDER BY year, generation_type, deal_name""", (company,))
        parts.append("<table><thead><tr>"
                     "<th>Announced</th><th>COD</th><th>Status</th><th>Type</th><th>MW</th>"
                     "<th>Conf.</th><th>Deal</th><th>Counterparty</th>"
                     "<th>COD note / deal notes</th></tr></thead><tbody>")
        for r in rows:
            c = cite(r["source_id"], url_of(r["source_id"]))
            notes_parts = []
            if r["cod_note"]:
                notes_parts.append(html.escape(r["cod_note"]))
            if r["notes"]:
                notes_parts.append(html.escape(r["notes"]).replace("\n", " "))
            notes_html = " — ".join(notes_parts)
            parts.append(
                f'<tr><td>{r["year"]}</td>'
                f'<td>{r["cod_year"] if r["cod_year"] else "—"}</td>'
                f'<td>{html.escape(r["status"] or "")}</td>'
                f'<td>{html.escape(r["generation_type"])}</td>'
                f'<td>{num(r["capacity_mw"])}{c}</td>'
                f'<td>{conf_badge(r["confidence"])}</td>'
                f'<td>{html.escape(r["deal_name"])}</td>'
                f'<td>{html.escape(r["counterparty"] or "")}</td>'
                f'<td class="note">{notes_html}</td></tr>'
            )
        parts.append("</tbody></table>")

    # --- LCOE ---
    parts.append('<h2 id="lcoe">2. LCOE ($/MWh)</h2>')
    rows = q(conn, """SELECT technology, year_vintage, report_name, geography, subsidized,
                             lcoe_low, lcoe_mid, lcoe_high, currency_year, source_id, notes
                      FROM lcoe_data ORDER BY technology, year_vintage, report_name""")
    parts.append("<table><thead><tr>"
                 "<th>Technology</th><th>Vintage</th><th>Report</th><th>Geo</th>"
                 "<th>Low</th><th>Mid</th><th>High</th><th>Subsidy</th><th>Notes</th></tr></thead><tbody>")
    for r in rows:
        c = cite(r["source_id"], url_of(r["source_id"]))
        sub = "w/ credit" if r["subsidized"] else "unsub."
        notes = html.escape(r["notes"] or "").replace("\n", " ")
        parts.append(
            f'<tr><td>{html.escape(r["technology"])}</td>'
            f'<td>{r["year_vintage"]}</td>'
            f'<td>{html.escape(r["report_name"])}{c}</td>'
            f'<td>{html.escape(r["geography"])}</td>'
            f'<td>{num(r["lcoe_low"])}</td>'
            f'<td>{num(r["lcoe_mid"])}</td>'
            f'<td>{num(r["lcoe_high"])}</td>'
            f'<td>{sub}</td>'
            f'<td class="note">{notes}</td></tr>'
        )
    parts.append("</tbody></table>")

    # --- Gas capex ---
    parts.append('<h2 id="gas-capex">3. Gas CAPEX ($/kW installed)</h2>')
    rows = q(conn, """SELECT year, plant_type, cost_low_kw, cost_mid_kw, cost_high_kw,
                             data_type, label, source_id, notes FROM gas_capex
                      ORDER BY plant_type, year, label""")
    parts.append("<table><thead><tr>"
                 "<th>Year</th><th>Plant</th><th>Low</th><th>Mid</th><th>High</th>"
                 "<th>Type</th><th>Label</th><th>Notes</th></tr></thead><tbody>")
    for r in rows:
        c = cite(r["source_id"], url_of(r["source_id"]))
        notes = html.escape(r["notes"] or "").replace("\n", " ")
        parts.append(
            f'<tr><td>{r["year"]}</td><td>{html.escape(r["plant_type"])}</td>'
            f'<td>{num(r["cost_low_kw"])}</td>'
            f'<td>{num(r["cost_mid_kw"])}{c}</td>'
            f'<td>{num(r["cost_high_kw"])}</td>'
            f'<td>{html.escape(r["data_type"])}</td>'
            f'<td>{html.escape(r["label"])}</td>'
            f'<td class="note">{notes}</td></tr>'
        )
    parts.append("</tbody></table>")

    # --- Renewable capex ---
    parts.append('<h2 id="ren-capex">4. Renewable & Nuclear CAPEX ($/kW installed)</h2>')
    rows = q(conn, """SELECT technology, year, cost_low_kw, cost_mid_kw, cost_high_kw,
                             data_type, label, source_id, notes FROM renewable_capex
                      ORDER BY technology, year, label""")
    parts.append("<table><thead><tr>"
                 "<th>Technology</th><th>Year</th><th>Low</th><th>Mid</th><th>High</th>"
                 "<th>Type</th><th>Label</th><th>Notes</th></tr></thead><tbody>")
    for r in rows:
        c = cite(r["source_id"], url_of(r["source_id"]))
        notes = html.escape(r["notes"] or "").replace("\n", " ")
        parts.append(
            f'<tr><td>{html.escape(r["technology"])}</td><td>{r["year"]}</td>'
            f'<td>{num(r["cost_low_kw"])}</td>'
            f'<td>{num(r["cost_mid_kw"])}{c}</td>'
            f'<td>{num(r["cost_high_kw"])}</td>'
            f'<td>{html.escape(r["data_type"])}</td>'
            f'<td>{html.escape(r["label"])}</td>'
            f'<td class="note">{notes}</td></tr>'
        )
    parts.append("</tbody></table>")

    # --- Turbine supply ---
    parts.append('<h2 id="turbine">5. Turbine supply</h2>')
    rows = q(conn, """SELECT manufacturer, as_of, backlog_note, notes, source_id FROM turbine_supply
                      ORDER BY manufacturer, as_of""")
    parts.append("<table><thead><tr><th>Manufacturer</th><th>As of</th><th>Backlog / lead time</th>"
                 "<th>Notes</th></tr></thead><tbody>")
    for r in rows:
        c = cite(r["source_id"], url_of(r["source_id"]))
        notes = html.escape(r["notes"] or "")
        parts.append(
            f'<tr><td>{html.escape(r["manufacturer"])}</td><td>{html.escape(r["as_of"])}</td>'
            f'<td>{html.escape(r["backlog_note"])}{c}</td>'
            f'<td class="note">{notes}</td></tr>'
        )
    parts.append("</tbody></table>")

    # --- Demand ---
    parts.append('<h2 id="demand">6. Demand metrics</h2>')
    rows = q(conn, """SELECT metric, value_num, value_text, unit, year, geography, source_id, notes
                      FROM demand_metrics ORDER BY metric, year""")
    parts.append("<table><thead><tr><th>Metric</th><th>Value</th><th>Year</th><th>Geo</th>"
                 "<th>Notes</th></tr></thead><tbody>")
    for r in rows:
        c = cite(r["source_id"], url_of(r["source_id"]))
        val = f'{num(r["value_num"])} {html.escape(r["unit"] or "")}' if r["value_num"] is not None else html.escape(r["value_text"] or "")
        notes = html.escape(r["notes"] or "")
        parts.append(
            f'<tr><td>{html.escape(r["metric"])}</td>'
            f'<td>{val}{c}</td>'
            f'<td>{r["year"] or ""}</td>'
            f'<td>{html.escape(r["geography"] or "")}</td>'
            f'<td class="note">{notes}</td></tr>'
        )
    parts.append("</tbody></table>")

    # --- Grid plan ---
    parts.append('<h2 id="grid">7. US grid capacity plan</h2>')
    rows = q(conn, """SELECT technology, window_start, window_end, gw_planned, geography, source_id, notes
                      FROM grid_capacity_plan ORDER BY technology""")
    parts.append("<table><thead><tr><th>Technology</th><th>Window</th><th>GW planned</th>"
                 "<th>Geo</th><th>Notes</th></tr></thead><tbody>")
    for r in rows:
        c = cite(r["source_id"], url_of(r["source_id"]))
        notes = html.escape(r["notes"] or "")
        parts.append(
            f'<tr><td>{html.escape(r["technology"])}</td>'
            f'<td>{r["window_start"]}–{r["window_end"]}</td>'
            f'<td>{num(r["gw_planned"], "{:.1f}")}{c}</td>'
            f'<td>{html.escape(r["geography"])}</td>'
            f'<td class="note">{notes}</td></tr>'
        )
    parts.append("</tbody></table>")

    # --- Cumulative ---
    parts.append('<h2 id="cum">8. Cumulative hyperscaler totals</h2>')
    rows = q(conn, """SELECT company, as_of, metric, value_gw, source_id, notes
                      FROM hyperscaler_cumulative ORDER BY company, as_of""")
    parts.append("<table><thead><tr><th>Company</th><th>As of</th><th>Metric</th><th>GW</th>"
                 "<th>Notes</th></tr></thead><tbody>")
    for r in rows:
        c = cite(r["source_id"], url_of(r["source_id"]))
        notes = html.escape(r["notes"] or "")
        parts.append(
            f'<tr><td>{html.escape(r["company"])}</td>'
            f'<td>{html.escape(r["as_of"])}</td>'
            f'<td>{html.escape(r["metric"])}</td>'
            f'<td>{num(r["value_gw"], "{:.1f}")}{c}</td>'
            f'<td class="note">{notes}</td></tr>'
        )
    parts.append("</tbody></table>")

    # --- Sources ---
    parts.append('<h2 id="sources">9. Source index</h2>')
    parts.append('<p class="meta">Rightmost column shows how many fact rows cite this source.</p>')
    parts.append('<table class="sources"><thead><tr><th>ID</th><th>Publisher</th><th>Title</th>'
                 '<th>Date</th><th>Kind</th><th>URL</th><th># facts</th></tr></thead><tbody>')
    rows = q(conn, """SELECT s.id, s.publisher, s.title, s.pub_date, s.kind, s.url,
                             (SELECT COUNT(*) FROM v_fact_provenance v WHERE v.source_id=s.id) AS n
                      FROM sources s ORDER BY CAST(SUBSTR(s.id,2) AS INTEGER)""")
    for r in rows:
        parts.append(
            f'<tr id="{html.escape(r["id"])}"><td><b>{html.escape(r["id"])}</b></td>'
            f'<td>{html.escape(r["publisher"])}</td>'
            f'<td>{html.escape(r["title"])}</td>'
            f'<td>{html.escape(r["pub_date"] or "")}</td>'
            f'<td>{html.escape(r["kind"] or "")}</td>'
            f'<td class="url"><a href="{html.escape(r["url"])}" target="_blank">{html.escape(r["url"])}</a></td>'
            f'<td>{r["n"]}</td></tr>'
        )
    parts.append("</tbody></table>")

    parts.append(f'<p class="meta">Generated from <code>{html.escape(str(DB))}</code>.</p>')
    parts.append("</body></html>")
    return "".join(parts)


def main():
    if not DB.exists():
        raise SystemExit("data.db missing — run scripts/load.py first.")
    conn = sqlite3.connect(DB)
    try:
        OUT.write_text(build(conn))
    finally:
        conn.close()
    print(f"report: {OUT}")


if __name__ == "__main__":
    main()
