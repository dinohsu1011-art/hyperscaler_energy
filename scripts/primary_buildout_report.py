"""Read-only report for primary-source AI buildout disclosures.

The goal is to show what is actually online, delivered, billable, connected,
contracted, or still under construction without summing unlike bases. A MW of
active power, a MW of critical IT load, a MW of gross utility capacity, and a
backlog dollar are different evidence types.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data.db"


def fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value):,}"
        return f"{value:,.1f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def print_table(title: str, rows: list[sqlite3.Row], columns: list[str]) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    if not rows:
        print("(none)")
        return
    widths = {
        col: max(len(col), *(len(fmt(row[col])) for row in rows))
        for col in columns
    }
    print("  ".join(col.ljust(widths[col]) for col in columns))
    print("  ".join("-" * widths[col] for col in columns))
    for row in rows:
        print("  ".join(fmt(row[col]).ljust(widths[col]) for col in columns))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=25, help="Rows to show in detail sections")
    args = parser.parse_args()

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    summary = conn.execute(
        """SELECT COUNT(*) AS rows,
                  COUNT(DISTINCT reporting_company) AS companies,
                  COUNT(DISTINCT source_id) AS sources,
                  COUNT(campus_id) AS linked_rows,
                  COUNT(DISTINCT campus_id) AS linked_campuses
           FROM primary_buildout_signals"""
    ).fetchone()
    print(
        "Primary Buildout Report\n"
        f"Rows: {summary['rows']:,} | Companies: {summary['companies']:,} | Sources: {summary['sources']:,}\n"
        f"Linked campus rows: {summary['linked_rows']:,} | Linked campuses: {summary['linked_campuses']:,}\n"
        "Guardrail: MW rows are grouped by capacity_basis and should not be added across bases."
    )

    print_table(
        "Evidence Ladder: Capacity Rows By Delivery Stage And Basis",
        conn.execute(
            """SELECT
                 CASE
                   WHEN status_stage IN ('active','billable','connected','delivered','ready_for_service') THEN 'online_or_delivered'
                   WHEN status_stage = 'under_construction' THEN 'under_construction'
                   WHEN status_stage IN ('contracted','secured') THEN 'contracted_or_secured'
                   WHEN status_stage IN ('announced','planned','future_target') THEN 'planned_or_future'
                   ELSE status_stage
                 END AS evidence_stage,
                 capacity_basis,
                 ROUND(SUM(CASE WHEN metric_unit = 'GW' THEN metric_value * 1000 ELSE metric_value END), 1) AS mw_equiv,
                 COUNT(*) AS rows
               FROM primary_buildout_signals
               WHERE metric_unit IN ('MW','GW')
               GROUP BY evidence_stage, capacity_basis
               ORDER BY
                 CASE evidence_stage
                   WHEN 'online_or_delivered' THEN 1
                   WHEN 'under_construction' THEN 2
                   WHEN 'contracted_or_secured' THEN 3
                   WHEN 'planned_or_future' THEN 4
                   ELSE 9
                 END,
                 capacity_basis"""
        ).fetchall(),
        ["evidence_stage", "capacity_basis", "mw_equiv", "rows"],
    )

    print_table(
        "Online Or Delivered MW Detail",
        conn.execute(
            """SELECT reporting_company,
                      COALESCE(campus_id, '') AS campus_id,
                      status_stage,
                      capacity_basis,
                      metric_value,
                      metric_unit,
                      COALESCE(project_name, '') AS project_name,
                      COALESCE(counterparty, '') AS counterparty,
                      as_of_date
               FROM primary_buildout_signals
               WHERE status_stage IN ('active','billable','connected','delivered','ready_for_service')
                 AND metric_unit IN ('MW','GW')
               ORDER BY
                 CASE WHEN metric_unit = 'GW' THEN metric_value * 1000 ELSE COALESCE(metric_value, 0) END DESC,
                 reporting_company
               LIMIT ?""",
            (args.top,),
        ).fetchall(),
        ["reporting_company", "campus_id", "status_stage", "capacity_basis", "metric_value", "metric_unit", "project_name", "counterparty", "as_of_date"],
    )

    print_table(
        "Buildout Pipeline Detail",
        conn.execute(
            """SELECT reporting_company,
                      COALESCE(campus_id, '') AS campus_id,
                      status_stage,
                      capacity_basis,
                      metric_value,
                      metric_unit,
                      COALESCE(project_name, '') AS project_name,
                      COALESCE(counterparty, '') AS counterparty,
                      COALESCE(expected_online_date, '') AS expected_online_date
               FROM primary_buildout_signals
               WHERE status_stage IN ('under_construction','contracted','secured','planned','future_target','announced')
                 AND metric_unit IN ('MW','GW')
               ORDER BY
                 CASE status_stage
                   WHEN 'under_construction' THEN 1
                   WHEN 'contracted' THEN 2
                   WHEN 'secured' THEN 3
                   WHEN 'future_target' THEN 4
                   WHEN 'planned' THEN 5
                   ELSE 9
                 END,
                 CASE WHEN metric_unit = 'GW' THEN metric_value * 1000 ELSE metric_value END DESC
               LIMIT ?""",
            (args.top,),
        ).fetchall(),
        ["reporting_company", "campus_id", "status_stage", "capacity_basis", "metric_value", "metric_unit", "project_name", "counterparty", "expected_online_date"],
    )

    print_table(
        "Non-MW Utilization, Backlog, And Chip Signals",
        conn.execute(
            """SELECT reporting_company,
                      COALESCE(campus_id, '') AS campus_id,
                      claim_type,
                      status_stage,
                      capacity_basis,
                      metric_value,
                      metric_unit,
                      COALESCE(project_name, '') AS project_name,
                      as_of_date
               FROM primary_buildout_signals
               WHERE metric_unit NOT IN ('MW','GW') OR metric_unit IS NULL
               ORDER BY
                 CASE claim_type
                   WHEN 'utilization_or_sold_out' THEN 1
                   WHEN 'gpu_or_chip_count' THEN 2
                   WHEN 'rpo_or_revenue_backlog' THEN 3
                   WHEN 'contract_value' THEN 4
                   ELSE 9
                 END,
                 CASE WHEN metric_value IS NULL THEN 0 ELSE metric_value END DESC
               LIMIT ?""",
            (args.top,),
        ).fetchall(),
        ["reporting_company", "campus_id", "claim_type", "status_stage", "capacity_basis", "metric_value", "metric_unit", "project_name", "as_of_date"],
    )

    print_table(
        "Linked Campus Coverage",
        conn.execute(
            """SELECT p.campus_id,
                      c.campus_name,
                      COUNT(*) AS rows,
                      GROUP_CONCAT(DISTINCT p.status_stage) AS status_stages,
                      ROUND(SUM(CASE WHEN p.metric_unit = 'GW' THEN p.metric_value * 1000
                                     WHEN p.metric_unit = 'MW' THEN p.metric_value
                                     ELSE 0 END), 1) AS mw_signal
               FROM primary_buildout_signals p
               JOIN data_center_campuses c ON c.campus_id = p.campus_id
               WHERE p.campus_id IS NOT NULL
               GROUP BY p.campus_id, c.campus_name
               ORDER BY mw_signal DESC, rows DESC, p.campus_id"""
        ).fetchall(),
        ["campus_id", "campus_name", "rows", "status_stages", "mw_signal"],
    )

    print_table(
        "Source Coverage",
        conn.execute(
            """SELECT p.reporting_company,
                      COUNT(*) AS rows,
                      COUNT(DISTINCT p.source_id) AS sources,
                      COUNT(DISTINCT p.campus_id) AS linked_campuses,
                      GROUP_CONCAT(DISTINCT s.publisher) AS publishers
               FROM primary_buildout_signals p
               JOIN sources s ON s.id = p.source_id
               GROUP BY p.reporting_company
               ORDER BY rows DESC, p.reporting_company"""
        ).fetchall(),
        ["reporting_company", "rows", "sources", "linked_campuses", "publishers"],
    )

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
