# Qualitative Load Commentary Anchors

## Objective

Build a source-backed qualitative statement layer that tracks how utilities, hyperscalers, neoclouds, data-center contractors, grid operators, supply-chain companies, CEOs/CFOs, and industry experts have described data-center load growth, construction pace, bottlenecks, and timing over the last 2-3 years.

The output should become a reference section for the AI capacity reality tracker: a timestamped time-series of anchor statements that shows whether the narrative has shifted from "future demand" to "contracted demand" to "metered load actually showing up."

## Original Request

Set up another GoalBuddy board using agents to gather qualitative statements from companies, CEOs, utilities, and industry experts, and plan which categories each agent should look into.

## Intake Summary

- Input shape: `specific`
- Audience: Investor/operator using the hyperscaler energy dashboard and capacity reality tracker
- Authority: `requested`
- Proof type: `source_backed_answer`
- Completion proof: A normalized, source-backed statement-anchor dataset and report/dashboard section exists, grouped by speaker category and timestamped chronology, with at least one completed Scout tranche for each major category or an explicit no-public-source verdict.
- Likely misfire: Collecting colorful AI/data-center quotes without tying each statement to timing, source type, speaker authority, load-stage signal, and whether it supports or contradicts the campus MW reality matrix.
- Blind spots considered: Utility statements may refer to system load rather than named data-center campuses; management commentary can be promotional; earnings-call transcripts may be copyrighted and should be summarized with short compliant quotes only; statements are evidence about narrative and timing, not direct proof of energized MW; repeated comments from the same executive should be tracked as an evolution, not double-counted as independent evidence.
- Existing plan facts: Use qualitative statements as anchor points; track statement evolution from the same people/entities over the last 2-3 years; include utilities like Vistra as reality-check signals for whether data-center load is actually hitting the meter; use agents to split source collection by category.

## Goal Kind

`specific`

## Current Tranche

Create a disciplined evidence-gathering workflow for qualitative load commentary. The current tranche should:

- define a normalized statement taxonomy;
- gather public, stable, source-backed statements by category;
- classify each statement by speaker, source date, source type, load-stage signal, geography, and relevance to data-center construction/energization rates;
- timestamp every commentary item and compare how repeated speakers/entities have changed their language over time;
- prepare a Worker slice to add the dataset, loader/validation support, and a report/dashboard section only after Judge approves the taxonomy and first data slice.

## Non-Negotiable Constraints

- Prefer primary sources: earnings-call transcripts, investor presentations, shareholder letters, utility filings, IR releases, RTO/ISO reports, regulator filings, and official company blogs.
- Secondary industry commentary may be included only when it identifies the speaker, event, date, and source route clearly.
- Do not treat qualitative commentary as direct energized MW proof.
- Keep capacity bases separate: actual metered load, contracted service, interconnection queue, announced campus MW, capex guidance, GPU delivery, and executive demand commentary are different evidence types.
- Track same-speaker evolution over time rather than using repeated statements as independent confirmation.
- Every captured commentary item must carry a statement date or event timestamp, plus an explicit precision flag when only month/quarter/year is public.
- Avoid paid, rumor, non-public, or unstable sources.
- Respect copyright limits: use short quotes only and rely on paraphrase/summaries for transcripts and articles.
- Preserve the existing AI capacity reality tracker; this is an additional qualitative layer, not a replacement for direct campus evidence.

## Stop Rule

Stop only when a final audit proves the full current tranche is complete: all major Scout categories have receipts or documented no-public-source limits, Judge has approved a normalized taxonomy and first data slice, and Worker output has created or updated the repo-native dataset/report/dashboard artifacts with validation.

Do not stop after planning or Scout findings if a safe Worker task exists.

## Canonical Board

Machine truth lives at:

`docs/goals/qualitative-load-commentary-anchors/state.yaml`

If this charter and `state.yaml` disagree, `state.yaml` wins for task status, active task, receipts, verification freshness, and completion truth.

## Run Command

```text
/goal Follow docs/goals/qualitative-load-commentary-anchors/goal.md.
```

## PM Loop

On every `/goal` continuation:

1. Read this charter.
2. Read `state.yaml`.
3. Run the bundled GoalBuddy update checker when available and mention a newer version without blocking.
4. Re-check the intake, especially likely misfire and evidence boundaries.
5. Work only on the active board task.
6. Assign Scout, Judge, Worker, or PM according to the task.
7. Write a compact task receipt.
8. Update the board and activate the next safe task.
9. Finish only with a Judge/PM audit receipt that maps receipts and verification back to the original user outcome and records `full_outcome_complete: true`.
