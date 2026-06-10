"""Summarize announced-vs-real campus capacity evidence.

This read-only report turns campus_evidence rows, linked primary buildout rows,
and SEC proxy rows into conservative reality tiers and confidence bands. It
intentionally does not mutate campus rollups because several evidence rows use
different MW bases: IT load, facility power, contracted load, generation
nameplate, substation capacity, and building count.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data.db"

ACTIVE_STATUSES = {"energized_partial", "energized_full"}
BUILD_STATUSES = {"site_work", "under_construction"}
APPROVED_STATUSES = {"approved"}
ACTIVE_PRIMARY_STAGES = {"active", "delivered", "ready_for_service", "billable", "connected"}
BUILD_PRIMARY_STAGES = {"under_construction"}
COMMITTED_PRIMARY_STAGES = {"contracted", "secured"}
FUTURE_PRIMARY_STAGES = {"announced", "planned", "future_target"}
COMPANY_TICKERS = {
    "Amazon": "AMZN",
    "Amazon/AWS": "AMZN",
    "CoreWeave": "CRWV",
    "Google": "GOOGL",
    "Meta": "META",
    "Microsoft": "MSFT",
    "Oracle": "ORCL",
}
CONFIDENCE_NOTES = {
    "high_active": "activation/energized evidence plus another support layer",
    "high_deliverability": "two independent campus groups plus primary or SEC support",
    "medium_active_single_layer": "activation/energized evidence but no overlay support",
    "medium_two_group_only": "two independent campus groups without overlay support",
    "medium_single_group_supported": "single campus group backed by primary or SEC support",
    "medium_primary_sec_only": "linked primary buildout and SEC support but no campus_evidence",
    "low_single_group_only": "single campus evidence group only",
    "low_primary_overlay": "linked primary buildout only",
    "low_planned_overlay": "planned/future linked primary buildout only",
    "low_announcement_only": "announcement-level campus evidence only",
    "low_not_public": "not publicly knowable with current evidence",
    "low_no_direct_evidence": "no direct campus or linked primary evidence",
}
RISK_TERMS = (
    "not disclose",
    "not disclosed",
    "not definitive",
    "not a definitive",
    "not source-backed",
    "not approved",
    "not energized",
    "not the full",
    "not the campus",
    "not critical it",
    "not critical-it",
    "option",
    "potential",
    "differs",
    "contradict",
    "replace",
    "replacing",
    "broader",
    "lower-bound",
    "permit ceiling",
)


def fmt_mw(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.0f}"


def fmt_pct(value: float) -> str:
    return f"{value:.0%}"


def gap(planned: float | None, energized: float | None) -> float:
    return max((planned or 0.0) - (energized or 0.0), 0.0)


def includes_any(values: Iterable[str], choices: set[str]) -> bool:
    return bool(set(values) & choices)


def tier_for(campus: sqlite3.Row, evidence: list[sqlite3.Row]) -> str:
    statuses = [row["claim_status"] for row in evidence]
    groups = {row["independence_group"] for row in evidence}
    has_active_evidence = includes_any(statuses, ACTIVE_STATUSES)
    has_approved = includes_any(statuses, APPROVED_STATUSES)
    has_build = includes_any(statuses, BUILD_STATUSES)
    has_not_public = "not_publicly_knowable" in statuses

    if has_active_evidence:
        return "activation_evidence"
    if (campus["it_load_mw_energized"] or 0) > 0 and len(groups) >= 2:
        return "energized_field_two_group"
    if len(groups) >= 2 and (has_approved or has_build):
        return "two_group_deliverability"
    if has_approved:
        return "single_group_approved"
    if has_build:
        return "single_group_site_work"
    if has_not_public:
        return "not_publicly_knowable"
    if evidence:
        return "announcement_only"
    return "no_evidence"


def evidence_flags(campus: sqlite3.Row, evidence: list[sqlite3.Row]) -> list[str]:
    flags: list[str] = []
    if not evidence:
        return ["no_evidence"]

    groups = {row["independence_group"] for row in evidence}
    statuses = [row["claim_status"] for row in evidence]
    if len(groups) < 2:
        flags.append("needs_second_group")
    if any(row["claim_status"] in ACTIVE_STATUSES and row["capacity_mw"] is None for row in evidence):
        flags.append("activation_no_mw")
    if "not_publicly_knowable" in statuses:
        flags.append("not_public")

    campus_basis = campus["capacity_definition"]
    campus_planned = campus["it_load_mw_planned"]
    if campus_planned is not None:
        for row in evidence:
            ev_mw = row["capacity_mw"]
            if ev_mw is None or row["capacity_definition"] != campus_basis:
                continue
            if abs(ev_mw - campus_planned) / max(campus_planned, 1.0) > 0.05:
                flags.append(f"{campus_basis}_mismatch")
                break

    notes_blob = " ".join(
        str(row["notes"] or "") + " " + str(row["quote"] or "") for row in evidence
    ).lower()
    if any(term in notes_blob for term in RISK_TERMS):
        flags.append("scope_or_basis_caveat")

    return sorted(set(flags)) or ["clean"]


def primary_stage_summary(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "-"
    counts = Counter(row["status_stage"] for row in rows)
    return ", ".join(f"{stage}:{counts[stage]}" for stage in sorted(counts))


def primary_mw_summary(rows: list[sqlite3.Row]) -> str:
    buckets: dict[str, float] = defaultdict(float)
    for row in rows:
        value = row["metric_value"]
        unit = row["metric_unit"]
        if value is None or unit not in {"MW", "GW"}:
            continue
        buckets[row["capacity_basis"]] += value * 1000 if unit == "GW" else value
    if not buckets:
        return "-"
    return "; ".join(f"{basis}:{fmt_mw(value)}" for basis, value in sorted(buckets.items()))


def primary_support_stage(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "none"
    statuses = {row["status_stage"] for row in rows}
    if statuses & ACTIVE_PRIMARY_STAGES:
        return "active_or_delivered"
    if statuses & BUILD_PRIMARY_STAGES:
        return "under_construction"
    if statuses & COMMITTED_PRIMARY_STAGES:
        return "contracted_or_secured"
    if statuses & FUTURE_PRIMARY_STAGES:
        return "planned_or_future"
    return "other_primary"


def candidate_tickers(campus: sqlite3.Row, primary_rows: list[sqlite3.Row]) -> set[str]:
    tickers: set[str] = set()
    campus_ticker = COMPANY_TICKERS.get(campus["hyperscaler"])
    if campus_ticker:
        tickers.add(campus_ticker)
    for row in primary_rows:
        if row["ticker"]:
            tickers.add(row["ticker"])
            continue
        reporting_ticker = COMPANY_TICKERS.get(row["reporting_company"])
        if reporting_ticker:
            tickers.add(reporting_ticker)
    return tickers


def confidence_for(tier: str, primary_rows: list[sqlite3.Row], has_sec_proxy: bool) -> str:
    primary_stage = primary_support_stage(primary_rows)
    has_primary = bool(primary_rows)
    has_strong_primary = primary_stage in {
        "active_or_delivered",
        "under_construction",
        "contracted_or_secured",
    }

    if tier in {"activation_evidence", "energized_field_two_group"}:
        if has_primary or has_sec_proxy:
            return "high_active"
        return "medium_active_single_layer"
    if tier == "two_group_deliverability":
        if has_primary or has_sec_proxy:
            return "high_deliverability"
        return "medium_two_group_only"
    if tier in {"single_group_site_work", "single_group_approved"}:
        if has_strong_primary or has_sec_proxy:
            return "medium_single_group_supported"
        return "low_single_group_only"
    if has_primary:
        if has_strong_primary and has_sec_proxy:
            return "medium_primary_sec_only"
        if has_strong_primary:
            return "low_primary_overlay"
        return "low_planned_overlay"
    if tier == "not_publicly_knowable":
        return "low_not_public"
    if tier == "announcement_only":
        return "low_announcement_only"
    return "low_no_direct_evidence"


def sec_proxy_summary(tickers: set[str], sec_by_ticker: dict[str, sqlite3.Row]) -> str:
    parts = []
    for ticker in sorted(tickers):
        row = sec_by_ticker.get(ticker)
        if row is None:
            continue
        parts.append(f"{ticker}:{row['metric_count']}m/{row['latest_year']}")
    return ", ".join(parts) or "-"


def evidence_layer_summary(item: dict[str, object]) -> str:
    layers = []
    evidence = item["evidence"]
    primary_rows = item["primary_rows"]
    if evidence:
        layers.append(f"campus:{item['tier']} {len(evidence)}/{len(item['groups'])}g")
    if primary_rows:
        layers.append(f"primary:{item['primary_stage']} {len(primary_rows)}")
    if item["sec_tickers"]:
        layers.append("sec_proxy")
    return "; ".join(layers) or "none"


def missing_evidence_reason(item: dict[str, object]) -> str:
    if not item["evidence"]:
        if item["primary_rows"]:
            return "needs campus_evidence"
        return "needs direct campus evidence"
    if len(item["groups"]) < 2:
        return "needs second independent group"
    if item["primary_stage"] in {"none", "planned_or_future"}:
        return "needs active/build primary support"
    if not item["sec_tickers"]:
        return "needs SEC proxy support where available"
    if "scope_or_basis_caveat" in item["flags"]:
        return "needs basis/scope reconciliation"
    return "review remaining caveats"


def load_rows(
    conn: sqlite3.Connection,
) -> tuple[
    list[sqlite3.Row],
    dict[str, list[sqlite3.Row]],
    dict[str, list[sqlite3.Row]],
    dict[str, sqlite3.Row],
]:
    campuses = conn.execute(
        """SELECT campus_id, campus_name, hyperscaler, primary_tenant, status, capacity_definition,
                  it_load_mw_planned, it_load_mw_energized, country
           FROM data_center_campuses
           ORDER BY campus_id"""
    ).fetchall()
    evidence_rows = conn.execute(
        """SELECT campus_id, evidence_id, claim_status, evidence_type, independence_group,
                  capacity_definition, capacity_mw, evidence_strength, notes, quote, source_id
           FROM campus_evidence
           ORDER BY campus_id, evidence_id"""
    ).fetchall()
    by_campus: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in evidence_rows:
        by_campus[row["campus_id"]].append(row)
    primary_rows = conn.execute(
        """SELECT campus_id, signal_id, reporting_company, ticker, claim_type, status_stage,
                  capacity_basis, metric_value, metric_unit, project_name, source_id
           FROM primary_buildout_signals
           WHERE campus_id IS NOT NULL
           ORDER BY campus_id, signal_id"""
    ).fetchall()
    primary_by_campus: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in primary_rows:
        primary_by_campus[row["campus_id"]].append(row)
    sec_by_ticker = {
        row["ticker"]: row
        for row in conn.execute(
            """SELECT ticker, company_group, COUNT(*) AS row_count,
                      COUNT(DISTINCT metric_key) AS metric_count,
                      MAX(period_year) AS latest_year
               FROM sec_proxy_metrics
               GROUP BY ticker, company_group"""
        ).fetchall()
    }
    return campuses, by_campus, primary_by_campus, sec_by_ticker


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    print(" | ".join(headers))
    print(" | ".join("---" if not h.endswith("_mw") else "---:" for h in headers))
    for row in rows:
        print(" | ".join(row))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Derive conservative reality tiers from campus_evidence."
    )
    parser.add_argument("--top", type=int, default=25, help="Top unenergized gaps to show.")
    parser.add_argument("--flags", type=int, default=12, help="Flagged campuses to show.")
    args = parser.parse_args()

    if not DB.exists():
        print("data.db missing; run scripts/load.py first.", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    campuses, evidence_by_campus, primary_by_campus, sec_by_ticker = load_rows(conn)

    campus_summaries = []
    tier_rollup: dict[str, Counter[str]] = defaultdict(Counter)
    confidence_rollup: dict[str, Counter[str]] = defaultdict(Counter)
    evidence_basis: dict[str, Counter[str]] = defaultdict(Counter)

    for campus in campuses:
        evidence = evidence_by_campus.get(campus["campus_id"], [])
        primary_rows = primary_by_campus.get(campus["campus_id"], [])
        tier = tier_for(campus, evidence)
        flags = evidence_flags(campus, evidence)
        groups = {row["independence_group"] for row in evidence}
        statuses = {row["claim_status"] for row in evidence}
        tickers = candidate_tickers(campus, primary_rows)
        sec_tickers = {ticker for ticker in tickers if ticker in sec_by_ticker}
        primary_stage = primary_support_stage(primary_rows)
        combined_confidence = confidence_for(tier, primary_rows, bool(sec_tickers))
        planned = campus["it_load_mw_planned"] or 0.0
        energized = campus["it_load_mw_energized"] or 0.0
        campus_gap = gap(campus["it_load_mw_planned"], campus["it_load_mw_energized"])

        tier_rollup[tier]["campuses"] += 1
        tier_rollup[tier]["planned_mw"] += planned
        tier_rollup[tier]["energized_mw"] += energized
        tier_rollup[tier]["gap_mw"] += campus_gap
        confidence_rollup[combined_confidence]["campuses"] += 1
        confidence_rollup[combined_confidence]["planned_mw"] += planned
        confidence_rollup[combined_confidence]["energized_mw"] += energized
        confidence_rollup[combined_confidence]["gap_mw"] += campus_gap
        if primary_rows:
            confidence_rollup[combined_confidence]["linked_primary_campuses"] += 1
        if sec_tickers:
            confidence_rollup[combined_confidence]["sec_proxy_campuses"] += 1

        for row in evidence:
            if row["capacity_mw"] is None:
                continue
            basis = row["capacity_definition"]
            evidence_basis[basis]["rows"] += 1
            evidence_basis[basis]["sum_mw"] += row["capacity_mw"]
            evidence_basis[basis]["max_mw"] = max(evidence_basis[basis]["max_mw"], row["capacity_mw"])

        campus_summaries.append(
            {
                "campus": campus,
                "evidence": evidence,
                "primary_rows": primary_rows,
                "tier": tier,
                "flags": flags,
                "groups": groups,
                "statuses": statuses,
                "gap": campus_gap,
                "primary_stage": primary_stage,
                "sec_tickers": sec_tickers,
                "sec_proxy": sec_proxy_summary(sec_tickers, sec_by_ticker),
                "combined_confidence": combined_confidence,
            }
        )

    total_planned = sum(c["it_load_mw_planned"] or 0.0 for c in campuses)
    total_energized = sum(c["it_load_mw_energized"] or 0.0 for c in campuses)
    total_gap = sum(item["gap"] for item in campus_summaries)
    evidence_rows = sum(len(v) for v in evidence_by_campus.values())
    two_group = sum(1 for item in campus_summaries if len(item["groups"]) >= 2)
    with_evidence = sum(1 for item in campus_summaries if item["evidence"])
    primary_rows_total = sum(len(item["primary_rows"]) for item in campus_summaries)
    with_primary = sum(1 for item in campus_summaries if item["primary_rows"])
    with_sec_proxy = sum(1 for item in campus_summaries if item["sec_tickers"])
    with_two_or_more_layers = sum(
        1
        for item in campus_summaries
        if sum(bool(layer) for layer in (item["evidence"], item["primary_rows"], item["sec_tickers"])) >= 2
    )
    high_confidence = sum(
        1 for item in campus_summaries if str(item["combined_confidence"]).startswith("high_")
    )
    medium_or_high = sum(
        1
        for item in campus_summaries
        if str(item["combined_confidence"]).startswith(("high_", "medium_"))
    )

    print("# Capacity Reality Report")
    print()
    print(f"campuses: {len(campuses)}")
    print(f"planned_mw: {fmt_mw(total_planned)}")
    print(f"energized_mw_field: {fmt_mw(total_energized)}")
    print(f"unenergized_gap_mw: {fmt_mw(total_gap)}")
    print(f"campus_evidence_rows: {evidence_rows}")
    print(f"campuses_with_evidence: {with_evidence} ({fmt_pct(with_evidence / len(campuses))})")
    print(f"campuses_with_two_independent_groups: {two_group} ({fmt_pct(two_group / len(campuses))})")
    print(f"primary_buildout_linked_rows: {primary_rows_total}")
    print(f"campuses_with_primary_buildout: {with_primary} ({fmt_pct(with_primary / len(campuses))})")
    print(f"campuses_with_sec_proxy_support: {with_sec_proxy} ({fmt_pct(with_sec_proxy / len(campuses))})")
    print(f"campuses_with_two_or_more_evidence_layers: {with_two_or_more_layers} ({fmt_pct(with_two_or_more_layers / len(campuses))})")
    print(f"high_confidence_campuses: {high_confidence} ({fmt_pct(high_confidence / len(campuses))})")
    print(f"medium_or_high_confidence_campuses: {medium_or_high} ({fmt_pct(medium_or_high / len(campuses))})")
    print()

    print("## Reality Tier Rollup")
    print()
    tier_rows = []
    for tier, counter in sorted(
        tier_rollup.items(), key=lambda item: (-item[1]["gap_mw"], item[0])
    ):
        tier_rows.append(
            [
                tier,
                str(int(counter["campuses"])),
                fmt_mw(counter["planned_mw"]),
                fmt_mw(counter["energized_mw"]),
                fmt_mw(counter["gap_mw"]),
            ]
        )
    print_table(["tier", "campuses", "planned_mw", "energized_mw", "gap_mw"], tier_rows)
    print()

    print("## Combined Confidence Matrix")
    print()
    confidence_rows = []
    for confidence, counter in sorted(
        confidence_rollup.items(), key=lambda item: (-item[1]["gap_mw"], item[0])
    ):
        confidence_rows.append(
            [
                confidence,
                str(int(counter["campuses"])),
                fmt_mw(counter["planned_mw"]),
                fmt_mw(counter["energized_mw"]),
                fmt_mw(counter["gap_mw"]),
                str(int(counter["linked_primary_campuses"])),
                str(int(counter["sec_proxy_campuses"])),
                CONFIDENCE_NOTES.get(confidence, "review evidence layers"),
            ]
        )
    print_table(
        [
            "confidence",
            "campuses",
            "planned_mw",
            "energized_mw",
            "gap_mw",
            "linked_primary",
            "sec_proxy",
            "note",
        ],
        confidence_rows,
    )
    print()
    print("_Confidence bands are report-only and do not change campus planned or energized MW._")
    print()

    print("## Evidence MW By Basis")
    print()
    basis_rows = []
    for basis, counter in sorted(
        evidence_basis.items(), key=lambda item: (-item[1]["sum_mw"], item[0])
    ):
        basis_rows.append(
            [basis, str(int(counter["rows"])), fmt_mw(counter["sum_mw"]), fmt_mw(counter["max_mw"])]
        )
    print_table(["basis", "rows", "sum_mw", "max_mw"], basis_rows)
    print()
    print("_Evidence MW is not additive across bases or phases._")
    print()

    ranked = sorted(campus_summaries, key=lambda item: (-item["gap"], item["campus"]["campus_id"]))
    print(f"## Top {min(args.top, len(ranked))} Unenergized Gaps")
    print()
    top_rows = []
    for idx, item in enumerate(ranked[: args.top], start=1):
        campus = item["campus"]
        top_rows.append(
            [
                str(idx),
                campus["campus_id"],
                campus["hyperscaler"],
                campus["status"],
                campus["capacity_definition"],
                fmt_mw(campus["it_load_mw_planned"]),
                fmt_mw(campus["it_load_mw_energized"]),
                fmt_mw(item["gap"]),
                item["tier"],
                item["combined_confidence"],
                f"{len(item['evidence'])}/{len(item['groups'])}",
                ", ".join(item["flags"][:3]),
                str(len(item["primary_rows"])),
                primary_stage_summary(item["primary_rows"]),
                item["sec_proxy"],
                evidence_layer_summary(item),
                campus["campus_name"],
            ]
        )
    print_table(
        [
            "rank",
            "campus_id",
            "hyperscaler",
            "status",
            "basis",
            "planned_mw",
            "energized_mw",
            "gap_mw",
            "tier",
            "confidence",
            "evidence/groups",
            "flags",
            "primary_rows",
            "primary_stages",
            "sec_proxy",
            "layers",
            "campus",
        ],
        top_rows,
    )
    print()

    linked = [item for item in ranked if item["primary_rows"]]
    print("## Primary Buildout Overlay By Campus")
    print()
    overlay_rows = []
    for item in linked[: args.top]:
        campus = item["campus"]
        overlay_rows.append(
            [
                campus["campus_id"],
                fmt_mw(item["gap"]),
                item["tier"],
                item["combined_confidence"],
                str(len(item["primary_rows"])),
                primary_stage_summary(item["primary_rows"]),
                primary_mw_summary(item["primary_rows"]),
                item["sec_proxy"],
                campus["campus_name"],
            ]
        )
    print_table(
        [
            "campus_id",
            "gap_mw",
            "tier",
            "confidence",
            "primary_rows",
            "primary_stages",
            "primary_mw_by_basis",
            "sec_proxy",
            "campus",
        ],
        overlay_rows,
    )
    print()

    remaining = [
        item
        for item in ranked
        if item["gap"] > 0 and not str(item["combined_confidence"]).startswith("high_")
    ][: args.top]
    print("## Remaining Direct-Evidence Gaps")
    print()
    remaining_rows = []
    for item in remaining:
        campus = item["campus"]
        remaining_rows.append(
            [
                campus["campus_id"],
                fmt_mw(item["gap"]),
                item["combined_confidence"],
                missing_evidence_reason(item),
                evidence_layer_summary(item),
                campus["campus_name"],
            ]
        )
    print_table(
        ["campus_id", "gap_mw", "confidence", "missing", "layers", "campus"],
        remaining_rows,
    )
    print()

    flagged = [
        item
        for item in ranked
        if any(flag != "clean" for flag in item["flags"]) and item["evidence"]
    ][: args.flags]
    print(f"## Top {len(flagged)} Flagged Campuses")
    print()
    flag_rows = []
    for item in flagged:
        campus = item["campus"]
        flag_rows.append(
            [
                campus["campus_id"],
                fmt_mw(item["gap"]),
                item["tier"],
                ", ".join(item["flags"]),
                campus["campus_name"],
            ]
        )
    print_table(["campus_id", "gap_mw", "tier", "flags", "campus"], flag_rows)

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
