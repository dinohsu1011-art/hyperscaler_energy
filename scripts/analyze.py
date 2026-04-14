"""Analyses and charts from data.db. Writes to output/.

Every printed / plotted number traces to a source via v_fact_provenance.
Use `provenance` mode to dump that audit trail.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data.db"
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)


def df(conn, q, params=()):
    return pd.read_sql_query(q, conn, params=params)


def gas_vs_clean(conn):
    d = df(conn, "SELECT * FROM v_gas_vs_clean_by_year ORDER BY year")
    d.to_csv(OUT / "gas_vs_clean_by_year.csv", index=False)
    print("\n== Gas vs Clean by Year ==")
    print(d.to_string(index=False))

    fig, ax = plt.subplots(figsize=(8, 4.5))
    width = 0.4
    ax.bar(d["year"] - width/2, d["clean_mw"]/1000, width, label="Clean (Solar/Wind/Nuclear/Storage/Hybrid)")
    ax.bar(d["year"] + width/2, d["gas_mw"]/1000, width, label="Gas")
    ax.set_ylabel("Capacity contracted (GW)")
    ax.set_title("Hyperscaler contract mix by year")
    ax.set_xticks(d["year"])
    ax.legend()
    for _, row in d.iterrows():
        ax.annotate(f"{row['gas_pct']}% gas", (row["year"], max(row['gas_mw'], row['clean_mw'])/1000 + 1),
                    ha="center", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "gas_vs_clean_by_year.png", dpi=140)
    plt.close(fig)


def mix_by_company_year(conn):
    d = df(conn, "SELECT * FROM v_energy_mix_by_company_year ORDER BY company, year, generation_type")
    d.to_csv(OUT / "mix_by_company_year.csv", index=False)
    print("\n== Mix by Company/Year (MW) ==")
    pivot = d.pivot_table(index=["company", "year"], columns="generation_type",
                          values="total_mw", aggfunc="sum", fill_value=0)
    print(pivot.to_string())

    # Stacked bar per company
    for company in pivot.index.get_level_values(0).unique():
        sub = pivot.loc[company].copy()
        fig, ax = plt.subplots(figsize=(7, 4))
        sub.plot(kind="bar", stacked=True, ax=ax)
        ax.set_ylabel("MW contracted")
        ax.set_title(f"{company} energy mix by announcement year")
        ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left", fontsize=8)
        fig.tight_layout()
        fig.savefig(OUT / f"mix_{company.lower()}.png", dpi=140)
        plt.close(fig)


def lcoe_trend(conn):
    d = df(conn, """
        SELECT technology, year_vintage, report_name, lcoe_low, lcoe_mid, lcoe_high, source_id
        FROM lcoe_data
        WHERE subsidized = 0 AND geography='US'
        ORDER BY technology, year_vintage, report_name
    """)
    d.to_csv(OUT / "lcoe_us_unsubsidized.csv", index=False)
    print("\n== US unsubsidized LCOE (all reports) ==")
    print(d.to_string(index=False))

    # Trend chart: Lazard midpoints, select technologies
    key = ["Solar-Utility", "Wind-Onshore", "Gas-CC", "Nuclear-New",
           "Solar+Storage-4hr", "Battery-4hr-Standalone"]
    t = d[d["technology"].isin(key) & d["report_name"].str.startswith("Lazard")]
    fig, ax = plt.subplots(figsize=(9, 5))
    for tech, sub in t.groupby("technology"):
        sub = sub.sort_values("year_vintage")
        ax.plot(sub["year_vintage"], sub["lcoe_mid"], marker="o", label=tech)
    ax.set_xlabel("Vintage year")
    ax.set_ylabel("LCOE midpoint ($/MWh)")
    ax.set_title("Lazard LCOE+ midpoints — US unsubsidized")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "lcoe_trend.png", dpi=140)
    plt.close(fig)


def gas_capex_trajectory(conn):
    d = df(conn, """
        SELECT year, plant_type, cost_low_kw, cost_mid_kw, cost_high_kw, data_type, label, source_id
        FROM gas_capex ORDER BY plant_type, year, label
    """)
    d.to_csv(OUT / "gas_capex.csv", index=False)
    print("\n== Gas CAPEX trajectory ==")
    print(d.to_string(index=False))

    fig, ax = plt.subplots(figsize=(9, 5))
    ccgt = d[d["plant_type"] == "CCGT"].sort_values("year")
    ax.errorbar(ccgt["year"], ccgt["cost_mid_kw"].fillna(ccgt["cost_low_kw"]),
                yerr=[ccgt["cost_mid_kw"].fillna(ccgt["cost_low_kw"]) - ccgt["cost_low_kw"].fillna(ccgt["cost_mid_kw"]),
                      ccgt["cost_high_kw"].fillna(ccgt["cost_mid_kw"]) - ccgt["cost_mid_kw"].fillna(ccgt["cost_low_kw"])],
                fmt="o", capsize=4, label="CCGT observations")
    for _, r in ccgt.iterrows():
        y = r["cost_mid_kw"] if pd.notna(r["cost_mid_kw"]) else r["cost_low_kw"]
        ax.annotate(r["label"], (r["year"], y), fontsize=7, xytext=(5, 4), textcoords="offset points")
    ax.set_xlabel("Year")
    ax.set_ylabel("$/kW installed")
    ax.set_title("Gas CCGT capex trajectory — actual / procurement / forecast")
    fig.tight_layout()
    fig.savefig(OUT / "gas_capex.png", dpi=140)
    plt.close(fig)


def hypothesis_check(conn):
    """Core §6.3 hypothesis: does solar+storage reach gas-CC LCOE parity in the
    2026–2029 window? Compare Lazard-v18 midpoints and IRENA hybrid observed."""
    print("\n== §6.3 Hypothesis probe ==")
    gas_mid = conn.execute("""
        SELECT lcoe_mid FROM lcoe_data
        WHERE technology='Gas-CC' AND report_name='Lazard v18' AND geography='US'
    """).fetchone()
    ss_mid = conn.execute("""
        SELECT lcoe_mid FROM lcoe_data
        WHERE technology='Solar+Storage-4hr' AND report_name='Lazard v18' AND geography='US'
    """).fetchone()
    bnef_gas = conn.execute("""
        SELECT lcoe_mid FROM lcoe_data
        WHERE technology='Gas-CC' AND report_name='BNEF LCOE 2026'
    """).fetchone()
    irena_ss = conn.execute("""
        SELECT lcoe_mid FROM lcoe_data
        WHERE technology='Solar+Storage-4hr' AND report_name='IRENA hybrid projects'
    """).fetchone()

    print(f"  Lazard v18 Gas-CC mid .............. ${gas_mid[0]}/MWh")
    print(f"  Lazard v18 Solar+Storage-4hr mid ... ${ss_mid[0]}/MWh")
    print(f"  BNEF 2026 Gas-CC global mid ........ ${bnef_gas[0]}/MWh")
    print(f"  IRENA 2024 US hybrid (observed) .... ${irena_ss[0]}/MWh")

    print("\n  Gap (Gas-CC Lazard vs Solar+Storage Lazard): "
          f"${gas_mid[0] - ss_mid[0]:+}/MWh (positive = gas is more expensive)")
    print("  But Lazard v18 lags actual 2025-2026 procurement costs; use capex-implied")
    print("  gas LCOE (from gas_capex.py) for a firmer current view.")


def provenance(conn, limit: int | None):
    d = df(conn, """
        SELECT v.tbl, v.row_id, v.fact, v.source_id, s.publisher, s.url
        FROM v_fact_provenance v JOIN sources s ON s.id = v.source_id
        ORDER BY v.tbl, v.row_id
    """)
    d.to_csv(OUT / "provenance.csv", index=False)
    print("\n== Provenance audit (first rows) ==")
    if limit:
        d = d.head(limit)
    print(d.to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="all",
                    choices=["all", "mix", "lcoe", "gas", "hypothesis", "provenance"])
    ap.add_argument("--limit", type=int, default=40,
                    help="Rows to print for provenance mode.")
    args = ap.parse_args()

    if not DB.exists():
        print("data.db missing — run scripts/load.py first.", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB)
    try:
        if args.mode in ("all", "mix"):
            gas_vs_clean(conn)
            mix_by_company_year(conn)
        if args.mode in ("all", "lcoe"):
            lcoe_trend(conn)
        if args.mode in ("all", "gas"):
            gas_capex_trajectory(conn)
        if args.mode in ("all", "hypothesis"):
            hypothesis_check(conn)
        if args.mode == "provenance":
            provenance(conn, args.limit)
    finally:
        conn.close()

    print(f"\nOutputs written to {OUT}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
