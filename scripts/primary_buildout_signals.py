"""Summarize primary-source buildout signal rows.

This is a lightweight inspection helper for the normalized
primary_buildout_signals table. It keeps capacity basis separate so active MW,
critical IT load, gross power, utility capacity, and backlog dollars do not get
collapsed into a fake single total.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data.db"


def print_rows(title: str, rows: list[sqlite3.Row], columns: list[str]) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    if not rows:
        print("(none)")
        return
    widths = {
        col: max(len(col), *(len(str(row[col] if row[col] is not None else "")) for row in rows))
        for col in columns
    }
    print("  ".join(col.ljust(widths[col]) for col in columns))
    print("  ".join("-" * widths[col] for col in columns))
    for row in rows:
        print("  ".join(str(row[col] if row[col] is not None else "").ljust(widths[col]) for col in columns))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=20, help="Rows to show in the largest-signal table")
    args = parser.parse_args()

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) AS n FROM primary_buildout_signals").fetchone()["n"]
    sources = conn.execute("SELECT COUNT(DISTINCT source_id) AS n FROM primary_buildout_signals").fetchone()["n"]
    companies = conn.execute(
        "SELECT COUNT(DISTINCT reporting_company) AS n FROM primary_buildout_signals"
    ).fetchone()["n"]
    print(f"Primary buildout signals: {total:,} rows, {companies:,} companies, {sources:,} sources")

    print_rows(
        "Rows By Claim Type",
        conn.execute(
            """SELECT claim_type, COUNT(*) AS rows
               FROM primary_buildout_signals
               GROUP BY claim_type
               ORDER BY rows DESC, claim_type"""
        ).fetchall(),
        ["claim_type", "rows"],
    )

    print_rows(
        "Capacity MW/GW By Status And Basis",
        conn.execute(
            """SELECT status_stage,
                      capacity_basis,
                      metric_unit,
                      ROUND(SUM(CASE WHEN metric_unit = 'GW' THEN metric_value * 1000 ELSE metric_value END), 1) AS mw_equiv,
                      COUNT(*) AS rows
               FROM primary_buildout_signals
               WHERE metric_unit IN ('MW','GW')
               GROUP BY status_stage, capacity_basis, metric_unit
               ORDER BY status_stage, capacity_basis, metric_unit"""
        ).fetchall(),
        ["status_stage", "capacity_basis", "metric_unit", "mw_equiv", "rows"],
    )

    print_rows(
        "Largest Numeric Signals",
        conn.execute(
            """SELECT reporting_company,
                      claim_type,
                      status_stage,
                      capacity_basis,
                      metric_value,
                      metric_unit,
                      COALESCE(project_name, '') AS project_name
               FROM primary_buildout_signals
               WHERE metric_value IS NOT NULL
               ORDER BY
                 CASE WHEN metric_unit = 'USD_b' THEN metric_value * 1000000
                      WHEN metric_unit = 'GW' THEN metric_value * 1000
                      ELSE metric_value END DESC,
                 reporting_company
               LIMIT ?""",
            (args.top,),
        ).fetchall(),
        ["reporting_company", "claim_type", "status_stage", "capacity_basis", "metric_value", "metric_unit", "project_name"],
    )

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
