"""Report campus evidence coverage and the highest unenergized capacity gaps.

This is a read-only helper for deciding where the next evidence collection pass
should go. It does not change campus status or capacity fields.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data.db"


def fmt_mw(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.0f}"


def gate_status(evidence_rows: int, independent_groups: int) -> str:
    if evidence_rows == 0:
        return "no_evidence"
    if independent_groups >= 2:
        return "two_group_gate"
    return "needs_second_group"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Show evidence coverage for data-center campus capacity claims."
    )
    parser.add_argument("--top", type=int, default=20, help="Number of gap-ranked campuses to show.")
    args = parser.parse_args()

    if not DB.exists():
        print("data.db missing; run scripts/load.py first.", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    totals = conn.execute(
        """SELECT
              COUNT(*) AS campus_count,
              ROUND(SUM(COALESCE(it_load_mw_planned, 0)), 0) AS planned_mw,
              ROUND(SUM(COALESCE(it_load_mw_energized, 0)), 0) AS energized_mw,
              ROUND(SUM(MAX(COALESCE(it_load_mw_planned, 0) - COALESCE(it_load_mw_energized, 0), 0)), 0) AS unenergized_gap_mw
           FROM data_center_campuses"""
    ).fetchone()

    evidence = conn.execute(
        """SELECT
              COUNT(*) AS evidence_rows,
              COUNT(DISTINCT campus_id) AS campuses_with_evidence
           FROM campus_evidence"""
    ).fetchone()

    gate = conn.execute(
        """SELECT COUNT(*) AS gated_campuses
           FROM (
             SELECT campus_id
             FROM campus_evidence
             GROUP BY campus_id
             HAVING COUNT(DISTINCT independence_group) >= 2
           )"""
    ).fetchone()

    rows = conn.execute(
        """SELECT
              c.campus_id,
              c.campus_name,
              c.hyperscaler,
              c.primary_tenant,
              c.city,
              c.state_or_region,
              c.country,
              c.status,
              c.capacity_definition AS campus_capacity_definition,
              c.it_load_mw_planned,
              c.it_load_mw_energized,
              ROUND(MAX(COALESCE(c.it_load_mw_planned, 0) - COALESCE(c.it_load_mw_energized, 0), 0), 0) AS unenergized_gap_mw,
              COUNT(e.id) AS evidence_rows,
              COUNT(DISTINCT e.independence_group) AS independent_groups,
              GROUP_CONCAT(DISTINCT e.independence_group) AS groups_seen,
              GROUP_CONCAT(DISTINCT e.evidence_type) AS evidence_types
           FROM data_center_campuses c
           LEFT JOIN campus_evidence e ON e.campus_id = c.campus_id
           GROUP BY c.campus_id
           HAVING unenergized_gap_mw > 0
           ORDER BY unenergized_gap_mw DESC, c.campus_id
           LIMIT ?""",
        (args.top,),
    ).fetchall()

    conn.close()

    print("# Campus Evidence Coverage")
    print()
    print(f"campuses: {totals['campus_count']}")
    print(f"planned_mw: {fmt_mw(totals['planned_mw'])}")
    print(f"energized_mw: {fmt_mw(totals['energized_mw'])}")
    print(f"unenergized_gap_mw: {fmt_mw(totals['unenergized_gap_mw'])}")
    print(f"campus_evidence_rows: {evidence['evidence_rows']}")
    print(f"campuses_with_evidence: {evidence['campuses_with_evidence']}")
    print(f"campuses_with_two_independent_groups: {gate['gated_campuses']}")
    print()
    print(f"## Top {len(rows)} Unenergized Campus Gaps")
    print()
    print(
        "rank | campus_id | hyperscaler | status | campus_basis | planned_mw | "
        "energized_mw | gap_mw | evidence_rows | groups | gate | campus"
    )
    print(
        "--- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---"
    )
    for i, row in enumerate(rows, start=1):
        print(
            f"{i} | {row['campus_id']} | {row['hyperscaler']} | {row['status']} | "
            f"{row['campus_capacity_definition']} | {fmt_mw(row['it_load_mw_planned'])} | "
            f"{fmt_mw(row['it_load_mw_energized'])} | {fmt_mw(row['unenergized_gap_mw'])} | "
            f"{row['evidence_rows']} | {row['independent_groups']} | "
            f"{gate_status(row['evidence_rows'], row['independent_groups'])} | {row['campus_name']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
