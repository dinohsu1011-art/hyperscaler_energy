"""Collect official SEC/XBRL proxy metrics for AI capacity triangulation.

The output is intentionally raw: one row per company / metric_key / XBRL tag /
period. Derived ratios should happen in a later report layer so tag meanings stay
visible and auditable.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "data" / "sec_proxy_metrics.yaml"

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
SEC_CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik10}/{taxonomy}/{tag}.json"
DEFAULT_SOURCE_ID = "S392"
try:
    import certifi

    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = ssl.create_default_context()

COMPANIES = [
    {"ticker": "MSFT", "company_name": "Microsoft", "company_group": "hyperscaler"},
    {"ticker": "GOOGL", "company_name": "Alphabet", "company_group": "hyperscaler"},
    {"ticker": "AMZN", "company_name": "Amazon", "company_group": "hyperscaler"},
    {"ticker": "META", "company_name": "Meta Platforms", "company_group": "hyperscaler"},
    {"ticker": "ORCL", "company_name": "Oracle", "company_group": "hyperscaler"},
    {"ticker": "DELL", "company_name": "Dell Technologies", "company_group": "server_vendor"},
    {"ticker": "SMCI", "company_name": "Super Micro Computer", "company_group": "server_vendor"},
    {"ticker": "HPE", "company_name": "Hewlett Packard Enterprise", "company_group": "server_vendor"},
    {"ticker": "NVDA", "company_name": "NVIDIA", "company_group": "chip_vendor"},
    {"ticker": "AMD", "company_name": "Advanced Micro Devices", "company_group": "chip_vendor"},
    {"ticker": "AVGO", "company_name": "Broadcom", "company_group": "chip_vendor"},
    {"ticker": "ANET", "company_name": "Arista Networks", "company_group": "networking_vendor"},
    {"ticker": "MRVL", "company_name": "Marvell Technology", "company_group": "chip_vendor"},
    {"ticker": "MU", "company_name": "Micron Technology", "company_group": "memory_vendor"},
    {"ticker": "CRWV", "company_name": "CoreWeave", "company_group": "neocloud", "optional": True},
]

METRICS = [
    {
        "metric_key": "capex_ppe",
        "metric_label": "Cash paid to acquire property, plant, and equipment",
        "period_kind": "duration",
        "units": ["USD"],
        "tags": [
            ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
            ("us-gaap", "PaymentsToAcquireProductiveAssets"),
            ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets"),
        ],
    },
    {
        "metric_key": "ppe_net",
        "metric_label": "Property, plant, and equipment / finance-lease asset base, net",
        "period_kind": "instant",
        "units": ["USD"],
        "tags": [
            ("us-gaap", "PropertyPlantAndEquipmentNet"),
            ("us-gaap", "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"),
        ],
    },
    {
        "metric_key": "depreciation_amortization",
        "metric_label": "Depreciation, depletion, and amortization",
        "period_kind": "duration",
        "units": ["USD"],
        "tags": [
            ("us-gaap", "DepreciationDepletionAndAmortization"),
            ("us-gaap", "DepreciationDepletionAndAmortizationExpense"),
            ("us-gaap", "Depreciation"),
        ],
    },
    {
        "metric_key": "operating_lease_rou_asset",
        "metric_label": "Operating lease right-of-use asset",
        "period_kind": "instant",
        "units": ["USD"],
        "tags": [("us-gaap", "OperatingLeaseRightOfUseAsset")],
    },
    {
        "metric_key": "finance_lease_rou_asset",
        "metric_label": "Finance lease right-of-use asset",
        "period_kind": "instant",
        "units": ["USD"],
        "tags": [
            ("us-gaap", "FinanceLeaseRightOfUseAsset"),
            ("us-gaap", "FinanceLeaseRightOfUseAssetNet"),
        ],
    },
    {
        "metric_key": "operating_lease_liability",
        "metric_label": "Operating lease liability",
        "period_kind": "instant",
        "units": ["USD"],
        "tags": [
            ("us-gaap", "OperatingLeaseLiability"),
            ("us-gaap", "OperatingLeaseLiabilityCurrent"),
            ("us-gaap", "OperatingLeaseLiabilityNoncurrent"),
        ],
    },
    {
        "metric_key": "finance_lease_liability",
        "metric_label": "Finance lease liability",
        "period_kind": "instant",
        "units": ["USD"],
        "tags": [
            ("us-gaap", "FinanceLeaseLiability"),
            ("us-gaap", "FinanceLeaseLiabilityCurrent"),
            ("us-gaap", "FinanceLeaseLiabilityNoncurrent"),
        ],
    },
    {
        "metric_key": "inventory_net",
        "metric_label": "Inventory, net",
        "period_kind": "instant",
        "units": ["USD"],
        "tags": [
            ("us-gaap", "InventoryNet"),
            ("us-gaap", "InventoryFinishedGoodsNetOfReserves"),
            ("us-gaap", "InventoryRawMaterialsAndPurchasedPartsNetOfReserves"),
        ],
    },
    {
        "metric_key": "accounts_receivable_net",
        "metric_label": "Accounts receivable, net",
        "period_kind": "instant",
        "units": ["USD"],
        "tags": [("us-gaap", "AccountsReceivableNetCurrent")],
    },
    {
        "metric_key": "contract_liability",
        "metric_label": "Contract liabilities and deferred revenue",
        "period_kind": "instant",
        "units": ["USD"],
        "tags": [
            ("us-gaap", "ContractWithCustomerLiability"),
            ("us-gaap", "ContractWithCustomerLiabilityCurrent"),
            ("us-gaap", "DeferredRevenueCurrent"),
            ("us-gaap", "DeferredRevenueNoncurrent"),
        ],
    },
    {
        "metric_key": "revenue",
        "metric_label": "Revenue",
        "period_kind": "duration",
        "units": ["USD"],
        "tags": [
            ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
            ("us-gaap", "Revenues"),
            ("us-gaap", "SalesRevenueNet"),
        ],
    },
    {
        "metric_key": "purchase_obligation",
        "metric_label": "Purchase obligations",
        "period_kind": "instant",
        "units": ["USD"],
        "tags": [
            ("us-gaap", "PurchaseObligation"),
            ("us-gaap", "PurchaseCommitment"),
        ],
    },
    {
        "metric_key": "remaining_performance_obligation",
        "metric_label": "Remaining performance obligations",
        "period_kind": "instant",
        "units": ["USD"],
        "tags": [
            ("us-gaap", "TransactionPriceAllocatedToRemainingPerformanceObligations"),
            ("us-gaap", "RevenueRemainingPerformanceObligation"),
        ],
    },
]


def fetch_json(url: str, user_agent: str, retries: int = 3) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
            "Connection": "close",
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=45, context=SSL_CONTEXT) as response:
                payload = response.read()
            time.sleep(0.12)
            return json.loads(payload)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last_error}") from last_error


def cik10(cik: int | str) -> str:
    return str(cik).zfill(10)


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def classify_period(fact: dict[str, Any], period_kind: str) -> str | None:
    start = parse_date(fact.get("start"))
    end = parse_date(fact.get("end"))
    if end is None:
        return None
    if period_kind == "instant":
        if start is None or start == end:
            return "instant"
        days = (end - start).days + 1
        return "instant" if days <= 2 else None

    if start is None:
        return None
    days = (end - start).days + 1
    if 70 <= days <= 110:
        return "quarter"
    if 340 <= days <= 390:
        return "annual"
    return None


def is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def resolve_tickers(user_agent: str) -> dict[str, dict[str, Any]]:
    payload = fetch_json(SEC_TICKERS_URL, user_agent)
    return {entry["ticker"].upper(): entry for entry in payload.values()}


def metric_rows_for_company(
    company: dict[str, Any],
    cik: int,
    facts_doc: dict[str, Any],
    since_fy: int,
    retrieved_on: str,
    source_id: str,
) -> list[dict[str, Any]]:
    rows_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    cik_padded = cik10(cik)
    facts = facts_doc.get("facts", {})

    for metric in METRICS:
        for taxonomy, tag in metric["tags"]:
            tag_doc = facts.get(taxonomy, {}).get(tag)
            if not tag_doc:
                continue
            units = tag_doc.get("units", {})
            for unit, fact_rows in units.items():
                if unit not in metric["units"]:
                    continue
                for fact in fact_rows:
                    fy = fact.get("fy")
                    if not isinstance(fy, int) or fy < since_fy:
                        continue
                    if fact.get("form") not in {"10-K", "10-Q", "20-F", "40-F"}:
                        continue
                    if not is_numeric(fact.get("val")):
                        continue
                    period_type = classify_period(fact, metric["period_kind"])
                    if period_type is None:
                        continue

                    fiscal_period = str(fact.get("fp") or "")
                    if period_type == "annual" and fiscal_period != "FY":
                        continue
                    if period_type == "quarter" and fiscal_period == "FY":
                        continue
                    form = str(fact.get("form") or "")
                    accession = str(fact.get("accn") or "")
                    end_date = str(fact.get("end") or "")
                    end = parse_date(end_date)
                    if end is None:
                        continue
                    if end.year < since_fy:
                        continue
                    start_date = fact.get("start")
                    filed_date = fact.get("filed")

                    row = {
                        "ticker": company["ticker"],
                        "cik": cik,
                        "company_name": company["company_name"],
                        "company_group": company["company_group"],
                        "metric_key": metric["metric_key"],
                        "metric_label": metric["metric_label"],
                        "taxonomy": taxonomy,
                        "xbrl_tag": tag,
                        "unit": unit,
                        "period_type": period_type,
                        "period_year": end.year,
                        "sec_fiscal_year": fy,
                        "fiscal_period": fiscal_period,
                        "form": form,
                        "filed_date": filed_date,
                        "start_date": start_date,
                        "end_date": end_date,
                        "frame": fact.get("frame"),
                        "accession": accession,
                        "value": fact["val"],
                        "source_url": SEC_CONCEPT_URL.format(
                            cik10=cik_padded,
                            taxonomy=taxonomy,
                            tag=tag,
                        ),
                        "source_id": source_id,
                        "retrieved_on": retrieved_on,
                    }
                    if row["period_type"] == "instant":
                        key = (
                            row["ticker"],
                            row["metric_key"],
                            row["xbrl_tag"],
                            row["unit"],
                            row["period_type"],
                            row["end_date"],
                        )
                    else:
                        key = (
                            row["ticker"],
                            row["metric_key"],
                            row["xbrl_tag"],
                            row["unit"],
                            row["period_type"],
                            row["start_date"] or "",
                            row["end_date"],
                        )
                    prev = rows_by_key.get(key)
                    if prev is None or (row["filed_date"] or "") >= (prev["filed_date"] or ""):
                        rows_by_key[key] = row
    return sorted(
        rows_by_key.values(),
        key=lambda r: (r["ticker"], r["metric_key"], r["xbrl_tag"], r["end_date"], r["period_type"]),
    )


def collect_rows(
    since_fy: int,
    user_agent: str,
    source_id: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    tickers = resolve_tickers(user_agent)
    retrieved_on = date.today().isoformat()
    all_rows: list[dict[str, Any]] = []
    skipped: list[str] = []

    for company in COMPANIES:
        ticker = company["ticker"]
        entry = tickers.get(ticker)
        if not entry:
            skipped.append(f"{ticker}: ticker not found in SEC company_tickers.json")
            if not company.get("optional"):
                print(f"[warn] {skipped[-1]}", file=sys.stderr)
            continue

        cik = int(entry["cik_str"])
        url = SEC_COMPANYFACTS_URL.format(cik10=cik10(cik))
        try:
            facts_doc = fetch_json(url, user_agent)
        except RuntimeError as exc:
            skipped.append(f"{ticker}: {exc}")
            print(f"[warn] {skipped[-1]}", file=sys.stderr)
            continue
        company_rows = metric_rows_for_company(company, cik, facts_doc, since_fy, retrieved_on, source_id)
        all_rows.extend(company_rows)
        print(f"[ok] {ticker}: {len(company_rows)} rows")

    return all_rows, skipped


def write_output(path: Path, rows: list[dict[str, Any]], skipped: list[str], since_fy: int, source_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "metadata": {
            "generated_on": date.today().isoformat(),
            "source_id": source_id,
            "source_url": "https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
            "sec_tickers_url": SEC_TICKERS_URL,
            "since_fy": since_fy,
            "row_count": len(rows),
            "skipped": skipped,
        },
        "rows": rows,
    }
    with path.open("w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, width=110)


def print_summary(rows: list[dict[str, Any]], skipped: list[str]) -> None:
    by_group = Counter(r["company_group"] for r in rows)
    by_ticker = Counter(r["ticker"] for r in rows)
    by_metric = Counter(r["metric_key"] for r in rows)

    print("\nSEC proxy metrics collected")
    print(f"rows: {len(rows)}")
    print("by group:")
    for key, count in sorted(by_group.items()):
        print(f"  {key}: {count}")
    print("by ticker:")
    for key, count in sorted(by_ticker.items()):
        print(f"  {key}: {count}")
    print("by metric:")
    for key, count in sorted(by_metric.items()):
        print(f"  {key}: {count}")
    if skipped:
        print("skipped:")
        for item in skipped:
            print(f"  {item}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--since-fy", type=int, default=2023)
    parser.add_argument("--source-id", default=DEFAULT_SOURCE_ID)
    parser.add_argument(
        "--user-agent",
        default=os.environ.get(
            "SEC_USER_AGENT",
            "DinoHsu Research dino@example.com",
        ),
        help="SEC-compliant User-Agent string. Can also be set with SEC_USER_AGENT.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        rows, skipped = collect_rows(args.since_fy, args.user_agent, args.source_id)
    except RuntimeError as exc:
        print(f"[failed] {exc}", file=sys.stderr)
        return 1
    if not rows:
        print("[failed] no SEC proxy rows collected", file=sys.stderr)
        return 1
    write_output(args.output, rows, skipped, args.since_fy, args.source_id)
    print_summary(rows, skipped)
    print(f"wrote: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
