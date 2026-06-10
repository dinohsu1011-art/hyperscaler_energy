"""Collect short official SEC filing-text snippets for AI capacity proxy signals."""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "data" / "sec_filing_text_signals.yaml"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
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
    {"ticker": "CRWV", "company_name": "CoreWeave", "company_group": "neocloud"},
]

SIGNAL_TERMS = {
    "ai_infrastructure": [
        "artificial intelligence",
        "AI infrastructure",
        "AI server",
        "AI servers",
        "AI-optimized",
        "accelerated computing",
        "GPU",
        "data center",
        "data centers",
    ],
    "backlog_rpo": [
        "backlog",
        "remaining performance obligations",
        "remaining performance obligation",
        "RPO",
        "contracted revenue",
        "unsatisfied performance obligations",
    ],
    "customer_concentration": [
        "customer concentration",
        "concentration of revenue",
        "significant customer",
        "major customer",
        "customers accounted for",
        "customer accounted for",
        "concentrated",
    ],
    "capex_guidance": [
        "capital expenditures",
        "capital expenditure",
        "property and equipment",
        "infrastructure",
        "data center capacity",
        "technical infrastructure",
        "cloud infrastructure",
    ],
    "purchase_commitments": [
        "purchase obligations",
        "purchase commitments",
        "supply agreement",
        "supplier commitments",
        "inventory purchase",
        "long-term supply",
    ],
    "inventory_supply": [
        "inventory",
        "supply constraints",
        "supply chain",
        "lead times",
        "capacity constraints",
        "component availability",
        "supply availability",
    ],
    "revenue_mix": [
        "data center revenue",
        "Data Center",
        "compute revenue",
        "networking revenue",
        "custom silicon",
        "inference",
        "training",
        "AI revenue",
    ],
}


def cik10(cik: int | str) -> str:
    return str(cik).zfill(10)


def fetch_bytes(url: str, user_agent: str, retries: int = 3) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json,text/html,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
            "Connection": "close",
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=60, context=SSL_CONTEXT) as response:
                payload = response.read()
            time.sleep(0.12)
            return payload
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last_error}") from last_error


def fetch_json(url: str, user_agent: str) -> Any:
    return json.loads(fetch_bytes(url, user_agent))


def resolve_tickers(user_agent: str) -> dict[str, dict[str, Any]]:
    payload = fetch_json(SEC_TICKERS_URL, user_agent)
    return {entry["ticker"].upper(): entry for entry in payload.values()}


def clean_document(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="ignore")
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def short_snippet(text: str, start: int, end: int, window: int = 120) -> str:
    left = max(0, start - window)
    right = min(len(text), end + window)
    snippet = text[left:right].strip()
    if left > 0:
        snippet = "... " + snippet
    if right < len(text):
        snippet = snippet + " ..."
    return snippet[:420]


def latest_filings(submissions: dict[str, Any], forms: tuple[str, ...] = ("10-K", "10-Q")) -> list[dict[str, Any]]:
    recent = submissions.get("filings", {}).get("recent", {})
    filings: list[dict[str, Any]] = []
    for i, form in enumerate(recent.get("form", [])):
        if form not in forms:
            continue
        accession = recent["accessionNumber"][i]
        primary_doc = recent["primaryDocument"][i]
        filings.append({
            "form": form,
            "filed_date": recent["filingDate"][i],
            "report_date": recent.get("reportDate", [""])[i],
            "accession": accession,
            "primary_document": primary_doc,
        })

    selected: list[dict[str, Any]] = []
    for form in forms:
        match = next((f for f in filings if f["form"] == form), None)
        if match:
            selected.append(match)
    return selected


def extract_signals(
    text: str,
    company: dict[str, Any],
    cik: int,
    filing: dict[str, Any],
    document_url: str,
    source_id: str,
    retrieved_on: str,
    max_per_signal: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_spans: set[tuple[str, str, str]] = set()
    for signal_type, terms in SIGNAL_TERMS.items():
        hits = 0
        for term in terms:
            pattern = re.compile(r"\b" + re.escape(term) + r"\b", re.I)
            for match in pattern.finditer(text):
                snippet = short_snippet(text, match.start(), match.end())
                key = (signal_type, term.lower(), snippet)
                if key in seen_spans:
                    continue
                seen_spans.add(key)
                rows.append({
                    "ticker": company["ticker"],
                    "cik": cik,
                    "company_name": company["company_name"],
                    "company_group": company["company_group"],
                    "form": filing["form"],
                    "filed_date": filing["filed_date"],
                    "report_date": filing.get("report_date") or None,
                    "accession": filing["accession"],
                    "document_url": document_url,
                    "signal_type": signal_type,
                    "matched_term": term,
                    "snippet": snippet,
                    "source_id": source_id,
                    "retrieved_on": retrieved_on,
                })
                hits += 1
                if hits >= max_per_signal:
                    break
            if hits >= max_per_signal:
                break
    return rows


def collect_rows(user_agent: str, source_id: str, max_per_signal: int) -> tuple[list[dict[str, Any]], list[str]]:
    tickers = resolve_tickers(user_agent)
    retrieved_on = date.today().isoformat()
    all_rows: list[dict[str, Any]] = []
    skipped: list[str] = []

    for company in COMPANIES:
        ticker = company["ticker"]
        entry = tickers.get(ticker)
        if not entry:
            skipped.append(f"{ticker}: ticker not found in SEC company_tickers.json")
            continue
        cik = int(entry["cik_str"])
        cik_padded = cik10(cik)
        try:
            submissions = fetch_json(SEC_SUBMISSIONS_URL.format(cik10=cik_padded), user_agent)
        except RuntimeError as exc:
            skipped.append(f"{ticker}: {exc}")
            print(f"[warn] {skipped[-1]}", file=sys.stderr)
            continue

        company_rows: list[dict[str, Any]] = []
        for filing in latest_filings(submissions):
            archive_cik = str(cik)
            archive_accession = filing["accession"].replace("-", "")
            document_url = ARCHIVE_URL.format(
                cik=archive_cik,
                accession=archive_accession,
                document=filing["primary_document"],
            )
            try:
                text = clean_document(fetch_bytes(document_url, user_agent))
            except RuntimeError as exc:
                skipped.append(f"{ticker} {filing['form']}: {exc}")
                print(f"[warn] {skipped[-1]}", file=sys.stderr)
                continue
            company_rows.extend(
                extract_signals(
                    text=text,
                    company=company,
                    cik=cik,
                    filing=filing,
                    document_url=document_url,
                    source_id=source_id,
                    retrieved_on=retrieved_on,
                    max_per_signal=max_per_signal,
                )
            )
        all_rows.extend(company_rows)
        print(f"[ok] {ticker}: {len(company_rows)} text signal rows")

    return all_rows, skipped


def write_output(path: Path, rows: list[dict[str, Any]], skipped: list[str], source_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "metadata": {
            "generated_on": date.today().isoformat(),
            "source_id": source_id,
            "source_url": "https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
            "row_count": len(rows),
            "skipped": skipped,
        },
        "rows": rows,
    }
    with path.open("w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, width=110)


def print_summary(rows: list[dict[str, Any]], skipped: list[str]) -> None:
    print("\nSEC filing text signals collected")
    print(f"rows: {len(rows)}")
    by_type: dict[str, int] = {}
    by_ticker: dict[str, int] = {}
    for row in rows:
        by_type[row["signal_type"]] = by_type.get(row["signal_type"], 0) + 1
        by_ticker[row["ticker"]] = by_ticker.get(row["ticker"], 0) + 1
    print("by signal:")
    for key, count in sorted(by_type.items(), key=lambda item: (-item[1], item[0])):
        print(f"  {key}: {count}")
    print("by ticker:")
    for key, count in sorted(by_ticker.items()):
        print(f"  {key}: {count}")
    if skipped:
        print("skipped:")
        for item in skipped:
            print(f"  {item}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-id", default=DEFAULT_SOURCE_ID)
    parser.add_argument("--max-per-signal", type=int, default=3)
    parser.add_argument(
        "--user-agent",
        default=os.environ.get("SEC_USER_AGENT", "DinoHsu Research dino@example.com"),
        help="SEC-compliant User-Agent string. Can also be set with SEC_USER_AGENT.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        rows, skipped = collect_rows(args.user_agent, args.source_id, args.max_per_signal)
    except RuntimeError as exc:
        print(f"[failed] {exc}", file=sys.stderr)
        return 1
    if not rows:
        print("[failed] no SEC filing text signal rows collected", file=sys.stderr)
        return 1
    write_output(args.output, rows, skipped, args.source_id)
    print_summary(rows, skipped)
    print(f"wrote: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
