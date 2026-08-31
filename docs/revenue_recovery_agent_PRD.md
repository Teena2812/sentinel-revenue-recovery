# PRD: AI Revenue Recovery Agent

**Product:** AI Revenue Recovery Agent — Razorpay Buildathon, Track 3
**Status:** Approved for build
**Version:** 1.0
**Owner:** [Your name]
**Last updated:** August 2026

---

## 1. Overview

An agentic AI system that identifies revenue at risk of being lost — failed payments
and overdue B2B receivables — diagnoses the root cause, decides a compliant recovery
action, independently verifies that action against hard business and regulatory rules
before executing it, and improves its strategy selection over time based on measured
outcomes.

The system is built to satisfy Razorpay Track 3's stated bar: don't just identify the
problem — show measured money recovered across a batch, with compliant escalation,
stopping rules, and an audit trail.

---

## 2. Problem Statement

Revenue leaks out of a payments/lending business at multiple, disconnected points:
a card payment fails and is never retried correctly, a checkout is abandoned, or a
B2B invoice goes unpaid past its due date. Today, these are typically handled by
fixed, one-size-fits-all rules (blanket retry schedules, generic reminder emails)
that don't account for *why* a specific case is failing, don't adapt when a strategy
stops working, and don't reliably enforce the compliance boundaries that money-related
outreach requires.

This is not a hypothetical problem. As of the 2025–26 Economic Survey, an estimated
₹8.1 trillion is currently locked in delayed payments to India's MSME sector, and
industry data puts the average small business's overdue receivables at roughly
₹3.83 crore, with invoice cycles routinely breaching the legally mandated 45-day
payment window. MSMEs account for roughly 30% of India's GDP — this is a
structural cash-flow problem at national economic scale, not a narrow edge case.

---

## 3. Goals

### Product goals
- Correctly diagnose the root cause of a revenue-at-risk case, not just detect that
  one exists.
- Select a recovery action from a bounded menu, matched to the specific case, rather
  than applying one fixed response to everyone.
- Guarantee that hard compliance and safety limits are never violated, regardless of
  what the reasoning layer proposes.
- Demonstrate measurable improvement in recovery rate against a naive baseline.
- Produce a fully explainable audit trail for every decision made.

### Business goals (for this submission)
- Meet or exceed every criterion Razorpay states it evaluates: Problem Taste, Build
  Quality, AI Judgment, Failure Recovery.
- Present a system whose safety properties (idempotency, deterministic compliance
  gating) would be credible in an actual fintech environment, not just a demo.

### Non-goals
- This is not a production payment gateway integration.
- This is not a general-purpose collections CRM.
- This is not a model-training or fine-tuning project — no underlying model is
  retrained; "learning" here refers to outcome-weighted strategy selection, stated
  explicitly and without overclaiming.

---

## 4. Target Users

| User | Context in this PRD |
|---|---|
| **Collections / recovery operations team** (primary intended end user) | Would configure policy limits, review escalated/human-required cases, and monitor the audit trail and recovery reporting |
| **Finance/business stakeholder** | Would consume the batch recovery report — ₹ recovered, recovery rate vs. baseline, compliance violation count |
| **Debtor / customer** (indirect user) | Receives only compliant, bounded, rate-limited contact as a result of the system's decisions |
| **Razorpay evaluation panel** (submission context) | Assesses the system against the track's stated bar during review |

---

## 5. User Stories

- As an operations lead, I want overdue cases automatically diagnosed by likely cause,
  so I don't have to manually triage every case before deciding how to act.
- As an operations lead, I want the system to propose a recovery action but never
  execute anything outside pre-set compliance boundaries, so I can trust it to run
  with minimal supervision.
- As a finance stakeholder, I want a batch-level report of money recovered versus
  money at risk, compared against doing nothing, so I can judge whether the system
  is actually adding value.
- As an operations lead, I want any case involving a dispute, a fraud flag, or
  conflicting signals to be escalated to a human automatically, so the system never
  makes a judgment call it isn't equipped to make safely.
- As a debtor, I should never be contacted outside reasonable hours or more frequently
  than policy allows, and any dispute I raise should immediately halt further
  automated contact.

---

## 6. Functional Requirements

### FR-1 — Detection
**FR-1.1** The system shall ingest a batch of cases (failed payments and/or overdue
receivables) with associated metadata (amount, timestamp/age, failure code or invoice
details, prior attempt history).

### FR-2 — Diagnosis
**FR-2.1** For each case, the system shall generate a plain-language root-cause
diagnosis using available case context, not a fixed category lookup alone.
**FR-2.2** Diagnosis output shall be structured (schema-enforced), not free text, to
ensure it can be reliably consumed by downstream components.

### FR-3 — Strategy
**FR-3.1** The system shall propose exactly one recovery action per decision cycle,
selected from a predefined, bounded action menu (e.g. retry now, retry later, suggest
alternate method, compliant reminder, payment-plan offer, escalate, stop).
**FR-3.2** The proposal shall include a confidence score.
**FR-3.3** The action choice shall account for case-specific factors including prior
attempts, a relationship-sensitivity flag (for B2B cases), and current strategy
performance data.

### FR-4 — Compliance & Safety Gate (deterministic, non-LLM)
**FR-4.1** No proposed action shall execute without passing a deterministic rule check
covering: maximum attempt count, permitted contact-hour window, dispute-flag hard stop,
fraud-flag hard stop, and an idempotency check confirming the action has not already
been executed for this case and attempt.
**FR-4.2** A rejected action shall be returned to the Strategy component with a stated
reason, and a revised proposal shall be required before re-submission.
**FR-4.3** Compliance rules for B2B cases shall be grounded in RBI Fair Practices Code
principles (permitted contact hours, no coercive or intimidating language, no public
shaming, written notice before recovery action, honored dispute rights).

### FR-5 — Execution
**FR-5.1** Approved actions shall be executed via a defined tool interface (simulated
retry call, simulated notification send).
**FR-5.2** Every execution shall be tagged with a unique idempotency key
(case ID + attempt number) and shall check for prior successful execution before
firing.
**FR-5.3** Execution failures (tool timeout, unavailable dependency) shall trigger one
automatic retry of the tool call; continued failure shall be logged and trigger a
fallback path rather than a silent drop.

### FR-6 — Memory & Outcome Tracking
**FR-6.1** The system shall persist full case history: every diagnosis, proposal, gate
decision, execution outcome, and timestamp.
**FR-6.2** The system shall maintain a recency-weighted (windowed) success rate per
strategy, per case-type, used to influence future proposals for similar cases.

### FR-7 — Stopping & Escalation
**FR-7.1** A case shall stop or escalate to a human queue when: the maximum attempt
count is reached, a dispute or fraud flag is present, conflicting data signals are
detected, the Strategy component's confidence falls below a defined threshold, or the
case value falls below a cost-effectiveness threshold (in which case only a minimal,
low-cost action path is used).

### FR-8 — Audit & Reporting
**FR-8.1** Every decision in the pipeline shall be traceable end-to-end for any given
case, in human-readable form.
**FR-8.2** The system shall produce a batch-level report including: recovery rate (%
and ₹), comparison against a naive fixed-rule baseline, average days-to-resolution,
count of cases correctly hard-stopped, and count of compliance violations (target:
zero).

---

## 7. Non-Functional Requirements

| Requirement | Detail |
|---|---|
| **Consistency** | Identical hard-rule inputs must always produce identical gate decisions — enforced by keeping these checks in deterministic code, not LLM reasoning |
| **Auditability** | Any single case's full decision path must be reconstructable after the fact |
| **Safety** | No action may execute twice for the same case/attempt (idempotency); no action may bypass the compliance gate under any condition |
| **Transparency** | All data used is simulated and must be clearly labeled as such in documentation and any demonstration |
| **Extensibility** | New failure types, case types, or compliance rules should be addable via configuration, not architectural rework |
| **Cost-awareness** | Low-value cases should not consume full-pipeline (LLM-diagnosis-level) cost; a cheap path exists below a configurable value threshold |

---

## 8. System Design Summary

```
EVENT → DETECTION → DIAGNOSIS (LLM) → STRATEGY (LLM, + confidence)
      → DETERMINISTIC GATE (code) → EXECUTION (idempotency-checked)
      → RESULT → MEMORY/ANALYTICS (windowed) → ADAPTATION
      → STOPPING-RULE CHECK → resolved / escalate / stop
```

Full architecture rationale, case-by-case handling reference, and build sequencing are
maintained separately in the project's master plan document — this PRD defines *what*
the system must do; the master plan defines *how and in what order* it gets built.

---

## 9. Data Requirements

- **Failed payment cases:** amount, failure code (insufficient funds, bank timeout,
  auth failure, gateway error, fraud rejection), timestamp, prior attempt count.
- **B2B receivable cases:** invoice amount, invoice age relative to the appointed
  payment day, debtor payment-history pattern, dispute flag, relationship-value flag.
- All data is synthetically generated and explicitly labeled as simulated; compliance
  constraints for B2B cases are grounded in RBI Fair Practices Code principles, not
  invented rules.

---

## 10. Success Metrics

| Metric | Target for submission |
|---|---|
| Recovery rate vs. naive baseline | Meaningfully higher, with the comparison shown explicitly |
| Compliance violations across batch | Zero |
| Cases correctly hard-stopped (fraud/dispute) | 100% of injected test cases caught |
| Audit trail completeness | Every case in the batch fully traceable |
| Staged failure recovery | At least one demonstrated tool failure, caught and gracefully resolved |
| Strategy adaptation | Demonstrable shift in strategy weighting between an early and later batch run |

---

## 11. Assumptions & Constraints

- Solo build, approximately 13 days, alongside other commitments.
- No access to real Razorpay production data or live payment gateway.
- No real outbound communication (calls/SMS/email) is actually sent — all execution
  is simulated.
- "Adaptation" is a windowed, weighted-scoring mechanism, not model retraining, and
  is described as such throughout.

---

## 12. Explicit Out of Scope

- Real payment gateway or messaging integration.
- Ledger reconciliation after a successful retry (this is Track 4's problem space).
- Systemic, batch-level pattern detection (e.g. recognizing and pausing for a bank-wide
  outage across many cases simultaneously) — named as a future extension.
- True preventive intervention (acting before a failure/default occurs) — named as a
  future extension, supported only by a lightweight heuristic risk score in this
  version.
- Any actual fine-tuning or retraining of the underlying language model.

---

## 13. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Core agent loop not stable before deeper features are added | Blocks everything downstream | Build and validate the end-to-end loop on the simpler scenario first, before extending |
| Compliance rules perceived as arbitrary | Undermines credibility of the B2B scenario | Cite RBI Fair Practices Code and MSMED Act explicitly in documentation |
| "Adaptation" claim challenged as overstated | Credibility risk under technical questioning | State the mechanism precisely (windowed weighting) and its limits, unprompted |
| Live demo dependency on external LLM API | Demo failure risk | Pre-run and cache the demo batch rather than calling live APIs during presentation |
| Data realism questioned | Credibility risk | Label all data as simulated from the first slide, without being asked |

---

## 14. Milestones

| Phase | Deliverable |
|---|---|
| Foundation | Case schema, synthetic data generator, naive baseline |
| Core loop | Diagnosis → Strategy → Gate → Execution → Memory, working end-to-end on one scenario |
| Depth | Second scenario added, gate rejection path demonstrated |
| Adaptation & resilience | Outcome-weighted strategy shift, staged failure-recovery case |
| Proof | Full batch run, metrics report vs. baseline |
| Submission | Repo, 5-minute video, architecture documentation |

Detailed day-by-day sequencing lives in the project master plan.

---

## 15. Open Questions

- Does the official application form specify a team-size limit or a submission
  process distinct from the general application? (Verify directly before finalizing
  team composition.)
- Should the relationship-value flag be a simple tiered label (low/medium/high) or a
  continuous score? (Default: tiered label, for interpretability in the audit trail.)
- What confidence threshold should trigger auto-escalation on conflicting signals?
  (To be tuned empirically once synthetic data is generated — start conservative.)
