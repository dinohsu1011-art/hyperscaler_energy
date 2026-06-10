"""Display math for operator self-disclosed capacity by stage.

Pure module: reads operator_capacity_disclosures + operator_term_registry from an
open sqlite3 connection and returns plain dicts. No dashboard import — the tab
emit and the validator both consume these functions, so the display semantics
live in exactly one place and are testable without touching the dashboard.

Contract implemented (design block in
docs/plans/2026-06-10-bento-rebuild-operator-disclosures.md, step 9):
  1. Quarter snapshot — within (operator, stage, basis, quarter), ONLY rows bearing
     the latest as_of_date form the snapshot, so two same-quarter statements never
     silently sum. figure = max(sum of component rows, max of total rows). A newer
     quarter's snapshot wholly supersedes older quarters for that stage x basis.
  2. Headline carry — per stage x basis, the latest snapshot within
     CARRY_WINDOW_QUARTERS of the operator's latest recorded quarter (absence rows
     included in the anchor) carries forward, labeled with its own as-of quarter
     and flagged stale when older than that anchor quarter.
  3. Basis pick — per operator x stage, chart the registry preferred_basis when it
     has a live-or-carried snapshot; else chart whatever is live (most recent),
     with the basis named so the tab can badge it.
  4. planned_shown = max(planned - operational - under_construction, 0).
     Cross-basis subtraction is allowed (each stage contributes its own charted
     basis). The registry's cumulative_planned_flag is recorded but does not
     branch here yet — the residual subtracts for every operator.
  5. quarterly_series — strict as-disclosed per quarter: no carry, and quarters or
     stages without a disclosure are absent (gaps), never zeros.

Stored rows are verbatim levels only; every residual and carry here is computed
at call time, never written back.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional, TypedDict

DISCLOSURE_STAGES = ("operational", "under_construction", "planned")
CARRY_WINDOW_QUARTERS = 4


class StageCell(TypedDict):
    """One charted stage figure with its as-of and basis context."""
    mw: float
    basis: str
    as_of_quarter: str
    as_of_date: str
    carried: bool            # snapshot quarter older than the operator's anchor quarter
    stale: bool              # same condition; kept separate for badge wording
    preferred_basis: Optional[str]
    live_bases: list[str]    # bases with a live-or-carried snapshot for this stage


class HeadlineCell(TypedDict):
    """One operator's headline stack (carry policy applied)."""
    operator: str
    operator_bucket: Optional[str]
    latest_quarter: str               # latest recorded quarter, absence rows included
    none_disclosed_latest: bool       # latest recorded quarter is an absence row
    stages: dict[str, Optional[StageCell]]
    planned_shown: Optional[float]


class QuarterCell(TypedDict):
    """One operator-quarter in the strict as-disclosed series."""
    operator: str
    quarter: str
    stages: dict[str, Optional[dict[str, Any]]]
    planned_shown: Optional[float]


def _q_index(quarter: str) -> int:
    """'2026Q1' → monotonically sortable quarter index."""
    year, qn = quarter.split("Q")
    return int(year) * 4 + int(qn) - 1


def _rows(conn: sqlite3.Connection, operator: Optional[str] = None) -> list[dict[str, Any]]:
    sql = ("SELECT operator, operator_bucket, as_of_date, as_of_quarter, "
           "stage_normalized, row_kind, component_label, mw_value, capacity_basis "
           "FROM operator_capacity_disclosures")
    args: tuple = ()
    if operator is not None:
        sql += " WHERE operator = ?"
        args = (operator,)
    cur = conn.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _snapshots(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, dict[str, Any]]]:
    """(operator, stage, basis) → quarter → {'mw', 'as_of_date'}.

    Applies the latest-as_of invariant: within one cell-quarter, only rows bearing
    the latest as_of_date count, then figure = max(sum components, max totals).
    """
    cells: dict[tuple[str, str, str], dict[str, list[dict[str, Any]]]] = {}
    for r in rows:
        if r["stage_normalized"] == "none_disclosed":
            continue
        key = (r["operator"], r["stage_normalized"], r["capacity_basis"])
        cells.setdefault(key, {}).setdefault(r["as_of_quarter"], []).append(r)
    out: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
    for key, by_quarter in cells.items():
        out[key] = {}
        for quarter, cell_rows in by_quarter.items():
            latest = max(r["as_of_date"] for r in cell_rows)
            live = [r for r in cell_rows if r["as_of_date"] == latest]
            candidates: list[float] = []
            components = [r["mw_value"] for r in live if r["row_kind"] == "component"]
            totals = [r["mw_value"] for r in live if r["row_kind"] == "total"]
            if components:
                candidates.append(sum(components))
            if totals:
                candidates.append(max(totals))
            out[key][quarter] = {"mw": max(candidates), "as_of_date": latest}
    return out


def _registry(conn: sqlite3.Connection) -> tuple[list[str], dict[tuple[str, str], str]]:
    """Roster in YAML order + (operator, stage) → preferred basis."""
    roster = [r[0] for r in conn.execute(
        "SELECT operator FROM operator_term_registry WHERE entry_kind='operator' ORDER BY id")]
    preferred = {(r[0], r[1]): r[2] for r in conn.execute(
        "SELECT operator, stage_normalized, preferred_basis "
        "FROM operator_term_registry WHERE entry_kind='preferred_basis'")}
    return roster, preferred


def _pick_basis(candidates: dict[str, dict[str, Any]], preferred: Optional[str]) -> str:
    """Preferred basis when it has a snapshot among the candidates, else the most
    recent live one (latest quarter, then latest as_of_date, then larger figure,
    then name — fully deterministic)."""
    if preferred is not None and preferred in candidates:
        return preferred
    return max(
        candidates,
        key=lambda b: (
            _q_index(candidates[b]["quarter"]) if "quarter" in candidates[b] else 0,
            candidates[b]["as_of_date"],
            candidates[b]["mw"],
            b,
        ),
    )


def _planned_shown(stages: dict[str, Optional[dict[str, Any]]]) -> Optional[float]:
    """max(planned - operational - under_construction, 0); stages absent from the
    view subtract nothing. None when no planned figure is on view (gap, not zero)."""
    planned = stages.get("planned")
    if planned is None:
        return None
    op = stages.get("operational")
    uc = stages.get("under_construction")
    residual = planned["mw"] - (op["mw"] if op else 0.0) - (uc["mw"] if uc else 0.0)
    return max(residual, 0.0)


def headline_cells(conn: sqlite3.Connection) -> list[HeadlineCell]:
    """Per-operator headline stage stack under the carry policy, roster order."""
    rows = _rows(conn)
    if not rows:
        return []
    snaps = _snapshots(rows)
    roster, preferred = _registry(conn)
    seen = {r["operator"] for r in rows}
    operators = [o for o in roster if o in seen] + sorted(seen - set(roster))

    out: list[HeadlineCell] = []
    for op in operators:
        op_rows = [r for r in rows if r["operator"] == op]
        anchor_q = max((r["as_of_quarter"] for r in op_rows), key=_q_index)
        anchor_idx = _q_index(anchor_q)
        bucket = next((r["operator_bucket"] for r in op_rows if r["operator_bucket"]), None)

        stages: dict[str, Optional[StageCell]] = {}
        for stage in DISCLOSURE_STAGES:
            candidates: dict[str, dict[str, Any]] = {}
            for (o, s, basis), by_quarter in snaps.items():
                if o != op or s != stage:
                    continue
                latest_q = max(by_quarter, key=_q_index)   # supersession: newest quarter wins
                if anchor_idx - _q_index(latest_q) > CARRY_WINDOW_QUARTERS:
                    continue                               # too old to carry
                candidates[basis] = {"quarter": latest_q, **by_quarter[latest_q]}
            if not candidates:
                stages[stage] = None
                continue
            pref = preferred.get((op, stage))
            basis = _pick_basis(candidates, pref)
            chosen = candidates[basis]
            stages[stage] = StageCell(
                mw=chosen["mw"],
                basis=basis,
                as_of_quarter=chosen["quarter"],
                as_of_date=chosen["as_of_date"],
                carried=chosen["quarter"] != anchor_q,
                stale=_q_index(chosen["quarter"]) < anchor_idx,
                preferred_basis=pref,
                live_bases=sorted(candidates),
            )

        out.append(HeadlineCell(
            operator=op,
            operator_bucket=bucket,
            latest_quarter=anchor_q,
            none_disclosed_latest=any(
                r["stage_normalized"] == "none_disclosed" and r["as_of_quarter"] == anchor_q
                for r in op_rows
            ),
            stages=stages,
            planned_shown=_planned_shown(stages),
        ))
    return out


def quarterly_series(conn: sqlite3.Connection, operator: str) -> list[QuarterCell]:
    """Strict as-disclosed series for one operator: one entry per quarter that has
    at least one disclosure row; no carry; missing stages stay None."""
    rows = [r for r in _rows(conn, operator) if r["stage_normalized"] != "none_disclosed"]
    if not rows:
        return []
    snaps = _snapshots(rows)
    _, preferred = _registry(conn)
    quarters = sorted({r["as_of_quarter"] for r in rows}, key=_q_index)

    out: list[QuarterCell] = []
    for quarter in quarters:
        stages: dict[str, Optional[dict[str, Any]]] = {}
        for stage in DISCLOSURE_STAGES:
            candidates = {
                basis: by_quarter[quarter]
                for (o, s, basis), by_quarter in snaps.items()
                if o == operator and s == stage and quarter in by_quarter
            }
            if not candidates:
                stages[stage] = None
                continue
            pref = preferred.get((operator, stage))
            basis = _pick_basis(candidates, pref)
            stages[stage] = {
                "mw": candidates[basis]["mw"],
                "basis": basis,
                "as_of_date": candidates[basis]["as_of_date"],
                "preferred_basis": pref,
                "live_bases": sorted(candidates),
            }
        out.append(QuarterCell(
            operator=operator,
            quarter=quarter,
            stages=stages,
            planned_shown=_planned_shown(stages),
        ))
    return out
