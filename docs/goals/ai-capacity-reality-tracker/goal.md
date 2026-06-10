# AI Capacity Reality Tracker

## Objective

Build an evidence-saturation workflow to determine, with approximately 90% confidence, how much AI/data-center capacity is actually coming online, how fast it is being built, and how that differs from public announcements.

## Original Request

Figure out how much capacity is actually coming online, how fast it is building, and how that differs from announcements; gather literally all the data needed until the assessment reaches 90% confidence.

## Intake Summary

- Input shape: `specific`
- Audience: Investor/operator using the hyperscaler energy dashboard to evaluate AI infrastructure reality vs announcement narrative.
- Authority: `requested`
- Proof type: `artifact` + `source_backed_answer` + `metric`
- Completion proof: A normalized evidence inventory and confidence-gated matrix where each major capacity claim is supported by at least two independent evidence types or receives an explicit not-publicly-knowable verdict, enabling a 90% confidence assessment.
- Likely misfire: Collecting broad AI/semi trivia or building a polished dashboard before proving which data actually answers real capacity ramp vs announcements.
- Blind spots considered: capacity definition ambiguity; announcement vs energized MW; tenant/operator double-counting; hidden utilization; proprietary/non-public evidence; noisy rumor channels; endless research without a stopping rule.
- Existing plan facts: Use direct capacity evidence plus supporting market proxies; exclude low-confidence rumor channels from core proof unless explicitly labeled. Next phase should prioritize primary-source disclosures that directly mention current active capacity, live/energized clusters, buildout pace, data-center capacity delivery, backlog conversion, or deployed infrastructure from hyperscalers, neoclouds, and major data-center companies. Good source classes include earnings-call transcripts, investor-day transcripts, SEC filing text, CEO/CFO interviews, company blogs, press releases, customer letters, and operator presentations.

## Goal Kind

`specific`

## Current Tranche

Discover, classify, and normalize the evidence sources needed for a 90% confidence assessment of real AI/data-center capacity ramp vs announcements. The current tranche should prioritize primary-source buildout-capacity disclosures over UI polish: earnings-call transcripts, investor days, SEC filing text, CEO/CFO interviews, operator presentations, company blogs, and press releases where operators explicitly discuss active capacity, live clusters, capacity delivered, capacity under construction, deployment timing, utilization, backlog conversion, or buildout constraints.

## Non-Negotiable Constraints

- Keep data provenance explicit with source IDs or source-backed notes.
- Separate capacity types: announced MW, utility-approved MW, planned campus MW, energized MW, critical IT load, facility power, GPU/compute proxy, and market absorption proxy.
- Avoid double-counting tenant demand, campus capacity, and energy contracts.
- Treat not publicly knowable as a valid conclusion only after strongest evidence routes are checked.
- Low-confidence HBM/CoWoS/channel-check rumors are not core proof unless explicitly tagged as low confidence.
- Do not treat generic AI enthusiasm, total capex, or broad demand commentary as active capacity unless the source directly ties it to deployed/energized capacity, delivered data-center capacity, live cluster capacity, or clear buildout cadence.
- Keep transcript/interview snippets short, source-attributed, and mapped to a capacity claim type such as active_capacity, capacity_delivered, under_construction, backlog_conversion, utilization, capex_buildout, or constraint.
- Do not treat planning, discovery, or one dataset slice as completion if the confidence matrix remains incomplete.

## Stop Rule

Stop only when a final audit proves the full original outcome is complete: each major claim in the assessment has at least two independent evidence types or an explicit not-publicly-knowable verdict, the confidence matrix supports an approximately 90% confidence answer, and the board receipts identify any residual uncertainty.

## Canonical Board

Machine truth lives at:

`docs/goals/ai-capacity-reality-tracker/state.yaml`

If this charter and `state.yaml` disagree, `state.yaml` wins for task status, active task, receipts, verification freshness, and completion truth.

## Run Command

```text
/goal Follow docs/goals/ai-capacity-reality-tracker/goal.md.
```

## PM Loop

On every `/goal` continuation:

1. Read this charter.
2. Read `state.yaml`.
3. Run the bundled GoalBuddy update checker when available and mention a newer version without blocking.
4. Work only on the active board task.
5. Assign Scout, Judge, Worker, or PM according to the task.
6. Write a compact task receipt and update the board.
7. Continue through safe Worker slices until the final audit proves the full outcome.
