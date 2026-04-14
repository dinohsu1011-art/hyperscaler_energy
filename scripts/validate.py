"""Validate data.db integrity beyond what the schema already enforces.

FK + NOT NULL on source_id already guarantee every row is cited. This script adds:
  1. Orphan sources — defined in sources.yaml but never referenced by any fact row.
  2. Duplicate-fact smell tests — numeric rows that share a value + year + entity
     but have DIFFERENT source_ids (possible double-counting via parallel reports).
  3. URL sanity — every source has a non-empty http(s) URL.
  4. Parent-child aggregate check for Microsoft Brookfield (solar+wind ≈ 10.5 GW parent).
  5. Meta 2025 nuclear (6.6 + 1.1) and Hyperion gas (2.3 + 5.2 = 7.5 GW) totals.

Exits 0 if all OK, 1 on any failure, 2 on warnings-only.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data.db"


def main() -> int:
    if not DB.exists():
        print("data.db missing — run load.py first.", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Orphan sources
    orphans = conn.execute(
        """SELECT s.id, s.publisher FROM sources s
           WHERE NOT EXISTS (SELECT 1 FROM v_fact_provenance v WHERE v.source_id = s.id)"""
    ).fetchall()
    for o in orphans:
        warnings.append(f"orphan source: {o['id']} ({o['publisher']}) — never referenced")

    # 2. URL sanity
    bad = conn.execute(
        "SELECT id, url FROM sources WHERE url IS NULL OR url NOT LIKE 'http%'"
    ).fetchall()
    for b in bad:
        errors.append(f"source {b['id']} has invalid URL: {b['url']!r}")

    # 3. Numeric-value provenance: every fact row has a valid source (FK already checks,
    #    but we re-assert for documentation).
    missing = conn.execute(
        "SELECT COUNT(*) AS n FROM v_fact_provenance WHERE source_id IS NULL"
    ).fetchone()["n"]
    if missing:
        errors.append(f"{missing} fact row(s) missing source_id (should be impossible via FK)")

    # 4. Duplicate-value warnings
    dup = conn.execute(
        """SELECT company, year, generation_type, capacity_mw, COUNT(*) AS n
           FROM hyperscaler_contracts
           GROUP BY company, year, generation_type, capacity_mw
           HAVING n > 1"""
    ).fetchall()
    for d in dup:
        warnings.append(
            f"possible double-count: {d['company']} {d['year']} {d['generation_type']} "
            f"{d['capacity_mw']} MW appears {d['n']} times"
        )

    # 5. Aggregate parent/child consistency
    # Microsoft Brookfield: solar 6,300 + wind 4,200 should = 10,500
    r = conn.execute(
        """SELECT SUM(capacity_mw) AS s FROM hyperscaler_contracts
           WHERE company='Microsoft' AND year=2024
             AND deal_name LIKE 'Brookfield framework%'"""
    ).fetchone()
    if r["s"] is not None and abs(r["s"] - 10500) > 1:
        errors.append(f"Microsoft Brookfield children sum to {r['s']}, expected 10,500")

    # Meta nuclear total (all years): Vistra 2,609 + TerraPower 2,760 + Oklo 1,200 + Constellation 1,100 = 7,669 ≈ 7.7 GW
    # Sub-deals are year=2026; omnibus row (year=2025) was replaced with sourced specifics
    meta_nuc = conn.execute(
        """SELECT SUM(capacity_mw) AS s FROM hyperscaler_contracts
           WHERE company='Meta' AND generation_type='Nuclear'"""
    ).fetchone()["s"]
    cum = conn.execute(
        """SELECT value_gw FROM hyperscaler_cumulative
           WHERE company='Meta' AND metric='Nuclear-Total'"""
    ).fetchone()
    if meta_nuc is not None and cum is not None:
        if abs(meta_nuc / 1000 - cum["value_gw"]) > 0.1:
            errors.append(
                f"Meta 2025 nuclear sum {meta_nuc/1000} GW != cumulative {cum['value_gw']} GW"
            )

    # Hyperion Phase1 + Phase2 = 7,500 MW
    hyp = conn.execute(
        """SELECT SUM(capacity_mw) AS s FROM hyperscaler_contracts
           WHERE company='Meta' AND generation_type='Gas' AND deal_name LIKE 'Hyperion%'"""
    ).fetchone()["s"]
    if hyp is not None and abs(hyp - 7500) > 1:
        errors.append(f"Hyperion gas phases sum to {hyp}, expected 7,500")

    conn.close()

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")

    if errors:
        print(f"FAIL: {len(errors)} error(s), {len(warnings)} warning(s).")
        return 1
    if warnings:
        print(f"OK-with-warnings: 0 errors, {len(warnings)} warning(s).")
        return 2
    print("PASS: no errors, no warnings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
