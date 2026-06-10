"""Validate data.db integrity beyond what the schema already enforces.

FK + NOT NULL on source_id already guarantee every row is cited. This script adds:
  1. Orphan sources — defined in sources.yaml but never referenced by any fact row.
  2. Duplicate-fact smell tests — numeric rows that share a value + year + entity
     but have DIFFERENT source_ids (possible double-counting via parallel reports).
  3. URL sanity — every source has a non-empty http(s) URL.
  4. Parent-child aggregate check for Microsoft Brookfield (solar+wind ≈ 10.5 GW parent).
  5. Meta 2025 nuclear (6.6 + 1.1) and Hyperion gas (2.26 + 5.24 = 7.5 GW) totals.
  6. Campus evidence hygiene — capacity rows must carry an explicit MW basis and
     duplicate evidence records are warned before they can pollute confidence scoring.
  7. SEC proxy hygiene — official XBRL facts must keep tag/period/source detail
     distinct so market proxies do not become undocumented derived claims.
  8. SEC filing-text signal hygiene — short snippets must be source-linked and
     deduped so qualitative evidence can support, not swamp, the confidence model.

Exits 0 if all OK, 1 on any failure, 2 on warnings-only.
"""
from __future__ import annotations

import datetime as dt
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data.db"


def timeline_bucket(statement_date: object, date_precision: str) -> str:
    if isinstance(statement_date, (dt.date, dt.datetime)):
        statement_date = statement_date.isoformat()[:10]
    else:
        statement_date = str(statement_date)
    if date_precision == "day":
        m = re.fullmatch(r"(\d{4})-(\d{2})-\d{2}", statement_date)
        if not m:
            raise ValueError(f"day precision requires YYYY-MM-DD, got {statement_date!r}")
        month = int(m.group(2))
        if not 1 <= month <= 12:
            raise ValueError(f"invalid month in {statement_date!r}")
        return f"{m.group(1)}Q{((month - 1) // 3) + 1}"
    if date_precision == "month":
        m = re.fullmatch(r"(\d{4})-(\d{2})", statement_date)
        if not m:
            raise ValueError(f"month precision requires YYYY-MM, got {statement_date!r}")
        month = int(m.group(2))
        if not 1 <= month <= 12:
            raise ValueError(f"invalid month in {statement_date!r}")
        return f"{m.group(1)}Q{((month - 1) // 3) + 1}"
    if date_precision == "quarter":
        if not re.fullmatch(r"\d{4}Q[1-4]", statement_date):
            raise ValueError(f"quarter precision requires YYYYQn, got {statement_date!r}")
        return statement_date
    if date_precision == "year":
        if not re.fullmatch(r"\d{4}", statement_date):
            raise ValueError(f"year precision requires YYYY, got {statement_date!r}")
        return statement_date
    if date_precision == "inferred":
        for precision in ("day", "month", "quarter", "year"):
            try:
                return timeline_bucket(statement_date, precision)
            except ValueError:
                continue
        raise ValueError(f"inferred precision still needs a parseable date, got {statement_date!r}")
    raise ValueError(f"unknown date_precision {date_precision!r}")


def main() -> int:
    if not DB.exists():
        print("data.db missing — run load.py first.", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Orphan sources — a source cited in row prose (notes/cod_note, e.g. "see S49")
    #    counts as referenced even without a source_id FK pointing at it.
    prose = " ".join(
        f"{r['notes'] or ''} {r['cod_note'] or ''}"
        for r in conn.execute("SELECT notes, cod_note FROM hyperscaler_contracts")
    )
    prose_cited = set(re.findall(r"\bS\d{1,3}\b", prose))
    orphans = conn.execute(
        """SELECT s.id, s.publisher FROM sources s
           WHERE NOT EXISTS (SELECT 1 FROM v_fact_provenance v WHERE v.source_id = s.id)"""
    ).fetchall()
    for o in orphans:
        if o["id"] in prose_cited:
            continue
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

    # 4. Duplicate-value warnings — keyed on counterparty too (same MW from two
    #    different counterparties is a coincidence, not a double-count). Pairs that
    #    share a counterparty but were manually verified as distinct deals
    #    (2026-06-10 audit) are whitelisted by deal_name below.
    KNOWN_DISTINCT = [
        # Two separate Williams BTM gas plants on the same New Albany OH campus
        {"Socrates South (Prometheus / New Albany OH) — Phase 1", "Socrates North (New Albany OH)"},
        # Two separate self-build Vantage campuses that happen to share 192 MW
        {"OH1 New Albany Ohio campus", "VA4 — Stafford County Northern Virginia"},
    ]
    dup = conn.execute(
        """SELECT company, year, generation_type, capacity_mw, counterparty,
                  COUNT(*) AS n, GROUP_CONCAT(deal_name, ' ||| ') AS deals
           FROM hyperscaler_contracts
           GROUP BY company, year, generation_type, capacity_mw, counterparty
           HAVING n > 1"""
    ).fetchall()
    for d in dup:
        names = set((d["deals"] or "").split(" ||| "))
        if any(names <= allowed for allowed in KNOWN_DISTINCT):
            continue
        warnings.append(
            f"possible double-count: {d['company']} {d['year']} {d['generation_type']} "
            f"{d['capacity_mw']} MW ({d['counterparty']}) appears {d['n']} times"
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

    # 6. connection_type tagging — warn on any row still tagged 'Unknown'.
    # Unknown rows are allowed (edge cases, speculative nuclear, real-estate deals)
    # but each should be a deliberate decision, not an oversight on new additions.
    unk = conn.execute(
        """SELECT id, company, deal_name FROM hyperscaler_contracts
           WHERE connection_type='Unknown' ORDER BY id"""
    ).fetchall()
    for u in unk:
        warnings.append(
            f"connection_type=Unknown: #{u['id']} {u['company']} — {u['deal_name']}"
        )

    # 7. Campus evidence hygiene. SQLite checks already enforce enums and FKs; these
    # warnings focus on confidence-matrix semantics.
    unbased = conn.execute(
        """SELECT evidence_id, campus_id FROM campus_evidence
           WHERE capacity_mw IS NOT NULL AND capacity_definition='Unknown'
           ORDER BY evidence_id"""
    ).fetchall()
    for r in unbased:
        warnings.append(
            f"campus_evidence {r['evidence_id']} ({r['campus_id']}) has MW but capacity_definition=Unknown"
        )

    energized_without_mw = conn.execute(
        """SELECT evidence_id, campus_id, claim_status FROM campus_evidence
           WHERE claim_status IN ('energized_partial','energized_full')
             AND capacity_mw IS NULL
           ORDER BY evidence_id"""
    ).fetchall()
    for r in energized_without_mw:
        warnings.append(
            f"campus_evidence {r['evidence_id']} ({r['campus_id']}) claims {r['claim_status']} without capacity_mw"
        )

    evidence_dups = conn.execute(
        """SELECT campus_id, evidence_type, claim_status, capacity_definition,
                  COALESCE(capacity_mw, -1) AS capacity_key, source_id, COUNT(*) AS n
           FROM campus_evidence
           GROUP BY campus_id, evidence_type, claim_status, capacity_definition,
                    COALESCE(capacity_mw, -1), source_id
           HAVING n > 1"""
    ).fetchall()
    for d in evidence_dups:
        warnings.append(
            f"possible duplicate campus_evidence: {d['campus_id']} {d['evidence_type']} "
            f"{d['claim_status']} {d['capacity_definition']} {d['capacity_key']} MW "
            f"from {d['source_id']} appears {d['n']} times"
        )

    # 8. SEC proxy signal hygiene.
    bad_sec_urls = conn.execute(
        """SELECT ticker, metric_key, source_url FROM sec_proxy_metrics
           WHERE source_url IS NULL OR source_url NOT LIKE 'http%'"""
    ).fetchall()
    for r in bad_sec_urls:
        errors.append(
            f"sec_proxy_metrics {r['ticker']} {r['metric_key']} has invalid source_url: {r['source_url']!r}"
        )

    sec_dups = conn.execute(
        """SELECT ticker, metric_key, xbrl_tag, unit, period_type, period_year,
                  fiscal_period, end_date, COUNT(*) AS n
           FROM sec_proxy_metrics
           GROUP BY ticker, metric_key, xbrl_tag, unit, period_type, period_year,
                    fiscal_period, end_date
           HAVING n > 1"""
    ).fetchall()
    for d in sec_dups:
        warnings.append(
            f"possible duplicate sec_proxy_metrics: {d['ticker']} {d['metric_key']} "
            f"{d['xbrl_tag']} {d['period_year']} {d['fiscal_period']} "
            f"{d['period_type']} ending {d['end_date']} appears {d['n']} times"
        )

    definition_count = conn.execute(
        "SELECT COUNT(*) AS n FROM proxy_signal_definitions"
    ).fetchone()["n"]
    sec_metric_count = conn.execute(
        "SELECT COUNT(*) AS n FROM sec_proxy_metrics"
    ).fetchone()["n"]
    if definition_count and not sec_metric_count:
        warnings.append("proxy_signal_definitions loaded but sec_proxy_metrics has no rows")

    mapped_metrics: set[str] = set()
    for row in conn.execute("SELECT metric_keys FROM proxy_signal_definitions"):
        mapped_metrics.update(k.strip() for k in row["metric_keys"].split(",") if k.strip())
    observed_metrics = {
        row["metric_key"]
        for row in conn.execute("SELECT DISTINCT metric_key FROM sec_proxy_metrics")
    }
    for metric_key in sorted(observed_metrics - mapped_metrics):
        warnings.append(f"sec_proxy_metrics metric_key has no proxy_signal_definition mapping: {metric_key}")

    # 9. SEC filing-text signal hygiene.
    bad_text_urls = conn.execute(
        """SELECT ticker, form, document_url FROM sec_filing_text_signals
           WHERE document_url IS NULL OR document_url NOT LIKE 'http%'"""
    ).fetchall()
    for r in bad_text_urls:
        errors.append(
            f"sec_filing_text_signals {r['ticker']} {r['form']} has invalid document_url: {r['document_url']!r}"
        )

    long_snippets = conn.execute(
        """SELECT ticker, signal_type, LENGTH(snippet) AS n
           FROM sec_filing_text_signals
           WHERE LENGTH(snippet) > 420"""
    ).fetchall()
    for r in long_snippets:
        warnings.append(
            f"sec_filing_text_signals snippet is long: {r['ticker']} {r['signal_type']} {r['n']} chars"
        )

    text_dups = conn.execute(
        """SELECT ticker, accession, signal_type, matched_term, snippet, COUNT(*) AS n
           FROM sec_filing_text_signals
           GROUP BY ticker, accession, signal_type, matched_term, snippet
           HAVING n > 1"""
    ).fetchall()
    for r in text_dups:
        warnings.append(
            f"possible duplicate sec_filing_text_signals: {r['ticker']} {r['accession']} "
            f"{r['signal_type']} {r['matched_term']} appears {r['n']} times"
        )

    # 10. Primary buildout signal hygiene. These rows can be numeric capacity,
    # contract, or timing claims, so keep units and status semantics explicit.
    value_without_unit = conn.execute(
        """SELECT signal_id, reporting_company, claim_type FROM primary_buildout_signals
           WHERE metric_value IS NOT NULL AND metric_unit IS NULL
           ORDER BY signal_id"""
    ).fetchall()
    for r in value_without_unit:
        errors.append(
            f"primary_buildout_signals {r['signal_id']} ({r['reporting_company']} {r['claim_type']}) has value but no unit"
        )

    unit_without_value = conn.execute(
        """SELECT signal_id, reporting_company, claim_type FROM primary_buildout_signals
           WHERE metric_value IS NULL AND metric_unit IS NOT NULL
           ORDER BY signal_id"""
    ).fetchall()
    for r in unit_without_value:
        warnings.append(
            f"primary_buildout_signals {r['signal_id']} ({r['reporting_company']} {r['claim_type']}) has unit but no numeric value"
        )

    long_buildout_quotes = conn.execute(
        """SELECT signal_id, reporting_company, LENGTH(source_quote) AS n
           FROM primary_buildout_signals
           WHERE source_quote IS NOT NULL AND LENGTH(source_quote) > 240"""
    ).fetchall()
    for r in long_buildout_quotes:
        warnings.append(
            f"primary_buildout_signals quote is long: {r['signal_id']} {r['reporting_company']} {r['n']} chars"
        )

    capacity_unit_mismatch = conn.execute(
        """SELECT signal_id, reporting_company, claim_type, metric_unit
           FROM primary_buildout_signals
           WHERE claim_type IN (
             'current_active_capacity','delivered_capacity','under_construction_capacity',
             'announced_future_capacity','secured_capacity','connected_capacity',
             'contracted_capacity','billable_capacity'
           )
             AND metric_value IS NOT NULL
             AND metric_unit NOT IN ('MW','GW','chips','GPUs','cluster','sites','data_halls')
           ORDER BY signal_id"""
    ).fetchall()
    for r in capacity_unit_mismatch:
        errors.append(
            f"primary_buildout_signals {r['signal_id']} ({r['reporting_company']} {r['claim_type']}) has non-capacity unit {r['metric_unit']}"
        )

    active_without_date = conn.execute(
        """SELECT signal_id, reporting_company, claim_type FROM primary_buildout_signals
           WHERE status_stage IN ('active','delivered','ready_for_service','billable','connected')
             AND as_of_date IS NULL
           ORDER BY signal_id"""
    ).fetchall()
    for r in active_without_date:
        warnings.append(
            f"primary_buildout_signals {r['signal_id']} ({r['reporting_company']} {r['claim_type']}) has active/delivered status without as_of_date"
        )

    invalid_buildout_campus = conn.execute(
        """SELECT signal_id, campus_id FROM primary_buildout_signals
           WHERE campus_id IS NOT NULL
             AND campus_id NOT IN (SELECT campus_id FROM data_center_campuses)
           ORDER BY signal_id"""
    ).fetchall()
    for r in invalid_buildout_campus:
        errors.append(
            f"primary_buildout_signals {r['signal_id']} references unknown campus_id {r['campus_id']}"
        )

    buildout_dups = conn.execute(
        """SELECT COALESCE(campus_id, '') AS campus_key,
                  reporting_company, COALESCE(project_name, '') AS project_name,
                  claim_type, status_stage, capacity_basis,
                  COALESCE(metric_value, -1) AS metric_key,
                  COALESCE(metric_unit, '') AS unit_key,
                  COALESCE(as_of_date, '') AS as_of_key,
                  source_id, COUNT(*) AS n
           FROM primary_buildout_signals
           GROUP BY COALESCE(campus_id, ''), reporting_company, COALESCE(project_name, ''), claim_type,
                    status_stage, capacity_basis, COALESCE(metric_value, -1),
                    COALESCE(metric_unit, ''), COALESCE(as_of_date, ''), source_id
           HAVING n > 1"""
    ).fetchall()
    for r in buildout_dups:
        warnings.append(
            f"possible duplicate primary_buildout_signals: {r['reporting_company']} "
            f"{r['project_name']} {r['claim_type']} {r['metric_key']} {r['unit_key']} "
            f"from {r['source_id']} appears {r['n']} times"
        )

    # 11. Qualitative commentary hygiene. Commentary rows are narrative anchors,
    # not direct capacity facts, so the timeline and evidence basis must be explicit.
    commentary_rows = conn.execute(
        """SELECT statement_id, statement_date, date_precision, timeline_bucket,
                  confidence, short_quote, numeric_value, numeric_unit,
                  capacity_basis, load_stage
           FROM qualitative_load_commentary
           ORDER BY statement_id"""
    ).fetchall()
    for r in commentary_rows:
        try:
            expected_bucket = timeline_bucket(r["statement_date"], r["date_precision"])
        except ValueError as exc:
            errors.append(f"qualitative_load_commentary {r['statement_id']} invalid date: {exc}")
            continue
        if r["timeline_bucket"] != expected_bucket:
            errors.append(
                f"qualitative_load_commentary {r['statement_id']} timeline_bucket "
                f"{r['timeline_bucket']} != derived {expected_bucket}"
            )
        if r["date_precision"] == "inferred" and r["confidence"] == "High":
            errors.append(
                f"qualitative_load_commentary {r['statement_id']} has inferred date with High confidence"
            )
        if r["numeric_value"] is not None and r["numeric_unit"] is None:
            errors.append(
                f"qualitative_load_commentary {r['statement_id']} has numeric_value but no numeric_unit"
            )
        if r["numeric_value"] is not None and r["capacity_basis"] == "not_capacity_evidence":
            errors.append(
                f"qualitative_load_commentary {r['statement_id']} has numeric_value but not_capacity_evidence basis"
            )
        if r["short_quote"] is not None and len(r["short_quote"]) > 180:
            warnings.append(
                f"qualitative_load_commentary quote is long: {r['statement_id']} {len(r['short_quote'])} chars"
            )
        if r["load_stage"] == "energized_or_metered" and r["capacity_basis"] not in {
            "metered_load", "live_cluster", "aggregate_demand_context"
        }:
            warnings.append(
                f"qualitative_load_commentary {r['statement_id']} says energized_or_metered "
                f"with {r['capacity_basis']} basis"
            )

    commentary_dups = conn.execute(
        """SELECT organization, speaker_name, event_name, statement_taxonomy,
                  load_stage, source_id, COUNT(*) AS n
           FROM qualitative_load_commentary
           GROUP BY organization, speaker_name, event_name, statement_taxonomy,
                    load_stage, source_id
           HAVING n > 1"""
    ).fetchall()
    for r in commentary_dups:
        warnings.append(
            f"possible duplicate qualitative_load_commentary: {r['organization']} "
            f"{r['event_name']} {r['statement_taxonomy']} {r['load_stage']} "
            f"from {r['source_id']} appears {r['n']} times"
        )

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
