"""Read-only report for SEC/XBRL market-proxy capacity signals."""
from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data.db"

HYPERSCALERS = ["MSFT", "GOOGL", "AMZN", "META", "ORCL"]
SERVER_VENDOR_TICKERS = ["DELL", "SMCI", "HPE", "CRWV"]
CHIP_VENDOR_TICKERS = ["NVDA", "AMD", "AVGO", "ANET", "MRVL", "MU"]

TAG_PRIORITY = {
    "capex_ppe": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
    ],
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "depreciation_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationDepletionAndAmortizationExpense",
        "Depreciation",
    ],
    "ppe_net": [
        "PropertyPlantAndEquipmentNet",
        "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
    ],
    "inventory_net": [
        "InventoryNet",
        "InventoryFinishedGoodsNetOfReserves",
        "InventoryRawMaterialsAndPurchasedPartsNetOfReserves",
    ],
    "contract_liability": [
        "ContractWithCustomerLiability",
        "DeferredRevenueCurrent",
        "ContractWithCustomerLiabilityCurrent",
        "DeferredRevenueNoncurrent",
    ],
    "finance_lease_liability": [
        "FinanceLeaseLiability",
        "FinanceLeaseLiabilityCurrent",
        "FinanceLeaseLiabilityNoncurrent",
    ],
    "operating_lease_liability": [
        "OperatingLeaseLiability",
        "OperatingLeaseLiabilityCurrent",
        "OperatingLeaseLiabilityNoncurrent",
    ],
}


def connect() -> sqlite3.Connection:
    if not DB.exists():
        raise SystemExit("data.db missing - run scripts/load.py first")
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def tag_rank(metric_key: str, tag: str) -> int:
    try:
        return TAG_PRIORITY.get(metric_key, []).index(tag)
    except ValueError:
        return 999


def row_sort_key(row: sqlite3.Row) -> tuple:
    return (
        row["end_date"] or "",
        row["filed_date"] or "",
        -tag_rank(row["metric_key"], row["xbrl_tag"]),
    )


def best_row(rows: Iterable[sqlite3.Row]) -> sqlite3.Row | None:
    best: sqlite3.Row | None = None
    for row in rows:
        if best is None:
            best = row
            continue
        current = (
            row["end_date"] or "",
            -tag_rank(row["metric_key"], row["xbrl_tag"]),
            row["filed_date"] or "",
        )
        previous = (
            best["end_date"] or "",
            -tag_rank(best["metric_key"], best["xbrl_tag"]),
            best["filed_date"] or "",
        )
        if current > previous:
            best = row
    return best


def selected_annual(conn: sqlite3.Connection, metric_key: str, tickers: list[str]) -> dict[tuple[str, int], sqlite3.Row]:
    placeholders = ",".join("?" for _ in tickers)
    rows = conn.execute(
        f"""SELECT * FROM sec_proxy_metrics
            WHERE metric_key=?
              AND period_type='annual'
              AND ticker IN ({placeholders})""",
        [metric_key, *tickers],
    ).fetchall()
    buckets: dict[tuple[str, int], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        buckets[(row["ticker"], row["period_year"])].append(row)
    return {key: best_row(group) for key, group in buckets.items() if best_row(group) is not None}


def latest_by_ticker(conn: sqlite3.Connection, metric_key: str, tickers: list[str]) -> dict[str, sqlite3.Row]:
    placeholders = ",".join("?" for _ in tickers)
    rows = conn.execute(
        f"""SELECT * FROM sec_proxy_metrics
            WHERE metric_key=?
              AND ticker IN ({placeholders})""",
        [metric_key, *tickers],
    ).fetchall()
    buckets: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        buckets[row["ticker"]].append(row)
    return {ticker: best_row(group) for ticker, group in buckets.items() if best_row(group) is not None}


def fmt_usd(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"${value / 1_000_000_000:,.1f}B"


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.0f}%"


def table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    out = [" | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    out.append(" | ".join("-" * w for w in widths))
    for row in rows:
        out.append(" | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(out)


def value(row: sqlite3.Row | None) -> float | None:
    return None if row is None else row["value"]


def print_coverage(conn: sqlite3.Connection) -> None:
    print("SEC Proxy Dataset")
    print("=================")
    total = conn.execute("SELECT COUNT(*) AS n FROM sec_proxy_metrics").fetchone()["n"]
    companies = conn.execute("SELECT COUNT(DISTINCT ticker) AS n FROM sec_proxy_metrics").fetchone()["n"]
    print(f"rows: {total:,}")
    print(f"companies: {companies}")
    print()
    rows = conn.execute(
        """SELECT company_group, COUNT(DISTINCT ticker) AS companies, COUNT(*) AS rows
           FROM sec_proxy_metrics
           GROUP BY company_group
           ORDER BY rows DESC"""
    ).fetchall()
    print(table(["group", "companies", "rows"], [
        [r["company_group"], str(r["companies"]), f"{r['rows']:,}"] for r in rows
    ]))
    print()


def print_hyperscaler_capex(conn: sqlite3.Connection) -> None:
    capex = selected_annual(conn, "capex_ppe", HYPERSCALERS)
    ppe = latest_by_ticker(conn, "ppe_net", HYPERSCALERS)
    dep = selected_annual(conn, "depreciation_amortization", HYPERSCALERS)
    rows: list[list[str]] = []
    for ticker in HYPERSCALERS:
        v2023 = value(capex.get((ticker, 2023)))
        v2024 = value(capex.get((ticker, 2024)))
        v2025 = value(capex.get((ticker, 2025)))
        change = None if not v2023 or not v2025 else (v2025 / v2023 - 1) * 100
        latest_ppe = ppe.get(ticker)
        latest_dep = dep.get((ticker, 2025)) or dep.get((ticker, 2024))
        rows.append([
            ticker,
            fmt_usd(v2023),
            fmt_usd(v2024),
            fmt_usd(v2025),
            fmt_pct(change),
            fmt_usd(value(latest_ppe)),
            latest_ppe["end_date"] if latest_ppe else "-",
            fmt_usd(value(latest_dep)),
        ])
    print("Hyperscaler Capex / Asset Base")
    print("------------------------------")
    print(table(
        ["ticker", "capex 2023", "capex 2024", "capex 2025", "23-25", "latest PP&E", "PP&E date", "D&A 2025"],
        rows,
    ))
    print()


def print_supply_chain(conn: sqlite3.Connection, title: str, tickers: list[str]) -> None:
    revenue = selected_annual(conn, "revenue", tickers)
    inventory = latest_by_ticker(conn, "inventory_net", tickers)
    ar = latest_by_ticker(conn, "accounts_receivable_net", tickers)
    rpo = latest_by_ticker(conn, "remaining_performance_obligation", tickers)
    purchase = latest_by_ticker(conn, "purchase_obligation", tickers)
    contract_liability = latest_by_ticker(conn, "contract_liability", tickers)

    rows: list[list[str]] = []
    for ticker in tickers:
        latest_revenue = revenue.get((ticker, 2025)) or revenue.get((ticker, 2024))
        inv = inventory.get(ticker)
        receivable = ar.get(ticker)
        rpo_row = rpo.get(ticker)
        purchase_row = purchase.get(ticker)
        contract_row = contract_liability.get(ticker)
        rows.append([
            ticker,
            fmt_usd(value(latest_revenue)),
            fmt_usd(value(inv)),
            inv["end_date"] if inv else "-",
            fmt_usd(value(receivable)),
            fmt_usd(value(contract_row)),
            fmt_usd(value(rpo_row)),
            fmt_usd(value(purchase_row)),
        ])
    print(title)
    print("-" * len(title))
    print(table(
        ["ticker", "annual revenue", "inventory", "inv date", "A/R", "contract liab.", "RPO", "purchase obl."],
        rows,
    ))
    print()


def print_top_capex_changes(conn: sqlite3.Connection, top: int) -> None:
    capex = selected_annual(
        conn,
        "capex_ppe",
        HYPERSCALERS + SERVER_VENDOR_TICKERS + CHIP_VENDOR_TICKERS,
    )
    rows: list[tuple[str, float, float, float]] = []
    for ticker in sorted({key[0] for key in capex}):
        start = value(capex.get((ticker, 2023)))
        latest_years = [year for t, year in capex if t == ticker and year <= 2025]
        if not start or not latest_years:
            continue
        latest_year = max(latest_years)
        latest = value(capex.get((ticker, latest_year)))
        if latest is None:
            continue
        rows.append((ticker, latest_year, latest - start, latest / start - 1))
    rows.sort(key=lambda r: r[2], reverse=True)

    print(f"Top Capex Step-Ups Since 2023")
    print("-----------------------------")
    print(table(
        ["ticker", "latest year", "increase", "growth"],
        [[ticker, str(int(year)), fmt_usd(delta), fmt_pct(growth * 100)] for ticker, year, delta, growth in rows[:top]],
    ))
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()

    conn = connect()
    try:
        print_coverage(conn)
        print_hyperscaler_capex(conn)
        print_supply_chain(conn, "Server / Neocloud Supply Chain", SERVER_VENDOR_TICKERS)
        print_supply_chain(conn, "Chip / Networking / Memory Supply Chain", CHIP_VENDOR_TICKERS)
        print_top_capex_changes(conn, args.top)
        print("Interpretation guardrail")
        print("------------------------")
        print(
            "These are official financial proxy signals, not direct online-MW evidence. "
            "Use them to triangulate whether announced capacity is backed by spend, asset growth, "
            "server/chip working capital, and commitments; pair them with campus_evidence before "
            "making campus-level online-capacity claims."
        )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
