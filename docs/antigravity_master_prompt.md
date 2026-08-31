# MASTER CONTEXT PROMPT — paste this as your first message in Antigravity

Read this entire message and all attached documents fully before taking any action.
Do not start writing code yet — see Section 10 for what to do right now.

---

## 1. What we are building

An AI agent system for Razorpay's AI Buildathon, Track 3 (AI Revenue Recovery). It
finds revenue at risk — failed payments and overdue B2B receivables — diagnoses the
root cause of each case, decides a compliant recovery action from a bounded menu,
independently verifies that action against hard rules before it executes, carries it
out safely, and improves its strategy choices over time based on measured outcomes.

Primary demo scenario: B2B receivables / promise-to-pay. Secondary scenario (proves
generality): failed payments. Both run through the same reasoning core.

This is a solo build, by a student, in a tight ~13-day window. Every design decision
below has already been debated, stress-tested, and locked — do not propose alternative
architectures or a different problem domain. Your job is to implement this system, not
redesign it.

---

## 2. Why this problem — grounded context (do not invent numbers, use these)

- An estimated ₹8.1 trillion is currently locked in delayed payments to India's MSME
  sector (2025–26 Economic Survey).
- Average small business overdue receivables are estimated around ₹3.83 crore, with
  invoice cycles routinely breaching the legally mandated payment window.
- MSMEs contribute roughly 30% of India's GDP — this is a structural, national-scale
  cash-flow problem, not a hypothetical scenario invented for this project.
- B2B compliance rules must be grounded in RBI Fair Practices Code principles:
  contact only within reasonable hours (e.g. 8 a.m.–7 p.m.), no intimidating or
  coercive language, no public shaming, written notice before recovery action, and
  the debtor's right to dispute.
- Razorpay's own stated bar for this track: "Don't just identify the problem. Show
  measured money recovered across a batch, with compliant escalation, stopping rules,
  and an audit trail." Every design choice should be justifiable against this bar.
- All data used in this project is synthetic. Label it as simulated everywhere —
  code comments, README, and any generated report — never imply it is real
  Razorpay data.

---

## 3. Full architecture / pipeline

```
EVENT (payment fails / invoice overdue)
      ↓
DETECTION
      ↓
DIAGNOSIS AGENT (LLM, structured JSON output)
Reasons out the likely root cause from case context
      ↓
STRATEGY AGENT (LLM, structured JSON output)
Proposes ONE bounded action from a fixed menu + a confidence score (0–1)
      ↓
DETERMINISTIC GATE (plain code — NEVER an LLM call)
Checks: attempt cap, contact-hour window, dispute/fraud hard-stop, idempotency
      ↓
EXECUTION AGENT
Calls a simulated tool (retry / notification), idempotency-checked before firing
      ↓
RESULT
      ↓
MEMORY + ANALYTICS
Full case history persisted; recency-weighted (windowed, not lifetime-average)
per-strategy success rate
      ↓
ADAPTATION
Future proposals for similar cases re-ranked by recent outcomes
      ↓
STOPPING-RULE CHECK
Resolved / escalate to human / stop
```

**Agent responsibilities:**

| Component | Job | Implementation |
|---|---|---|
| Diagnosis | Explain *why* this case is happening, in plain language | LLM call, JSON schema output |
| Strategy | Propose one action + confidence score | LLM call, JSON schema output |
| Deterministic Gate | Approve/reject the proposed action against hard rules | Plain code — this is the single most important architectural rule in the whole project, see Section 4 |
| Execution | Carry out the approved action, idempotency-checked | Simple, mechanical, reports success/failure |
| Memory/Analytics | Track case history + strategy outcome rates | Shared state store (SQLite or structured JSON), not an agent "personality" |

---

## 4. Non-negotiable hard rules — never violate these, even if it would make code simpler

1. **The Deterministic Gate must be plain, testable code — never an LLM judgment
   call.** Hard limits (attempt caps, contact hours, dispute/fraud stop) must produce
   the identical decision on identical input, every time. If you find yourself asking
   an LLM "should we allow this action," stop — that logic belongs in the gate, not
   the prompt.
2. **Idempotency is mandatory.** Every execution carries a unique key
   (case ID + attempt number); the Execution agent must check for a prior successful
   execution before firing, to prevent duplicate actions (e.g. double-charging).
3. **Relationship-value tier** is computed deterministically (plain code), shown only
   as `LOW` / `MEDIUM` / `HIGH` in the audit trail:
   ```
   Relationship Score (0–100) = 0.40×(value percentile) + 0.35×(historical reliability)
                                + 0.25×(engagement tenure)
   Score ≥ 70 → HIGH | 40–69 → MEDIUM | <40 → LOW
   ```
   **Fatigue override:** if this case's contact/retry count exceeds a cap (e.g. >3 for
   B2B, >2 for payments), downgrade the tier by one level regardless of score. Missing
   history defaults to a neutral 0.5, not 0 or 1.
4. **Confidence & conflicting-signal policy.** Auto-execute only if ALL of: confidence
   ≥ 0.85 (configurable, not fixed — see Section 6), no critical conflicting signal
   (fraud flag vs. safe diagnosis, dispute flag vs. contact action, disagreeing data
   sources), the action passes the Deterministic Gate, and the action is reversible/
   low-risk. Otherwise, follow this exact fallback ladder in order, stopping at the
   first fit: gather more info → wait → try a safer action → escalate to a human →
   stop. Never guess when uncertain.
5. **Cost-threshold gate.** Cases below a configurable value threshold should use a
   cheap automatic path only (skip the full LLM diagnosis pipeline) — don't spend an
   LLM call chasing a trivial amount.
6. **Describe adaptation honestly.** The system does windowed, weighted strategy
   scoring — this is memory + analytics + adaptation. It is explicitly NOT model
   retraining or fine-tuning. Never describe it as "the AI learns" without this
   qualification, in code comments, README, or logs.

---

## 5. Workflow — build in exactly this phased order, with checkpoints

**Phase 1 — Foundation.** Case schema for both scenarios (including
`relationship_value`, cost threshold, confidence score, idempotency key fields).
Encode RBI-style compliance rules as explicit checkable constraints. Build the
synthetic data generator and a naive fixed-rule baseline (flat retry schedule / flat
reminder cadence) to compare against later.

**Phase 2 — Core loop (critical path).** Diagnosis → Strategy → Deterministic Gate →
Execution → Memory, working end-to-end, validated first on the simpler Failed
Payments scenario.

**Phase 3 — Depth.** Extend to B2B receivables' longer case lifecycle. Confirm the
gate correctly handles a disputed-invoice case and a relationship-sensitive case.

**Phase 4 — Adaptation + failure story.** Add outcome logging and the windowed
strategy-weight update. Stage one deliberate tool failure (e.g. notification timeout)
and its graceful fallback/recovery — this is the required "what broke and how you got
out" demo moment.

**Phase 5 — Proof.** Run the full batch against the baseline. Generate the metrics
report (recovery rate, ₹ recovered vs. baseline, days-to-resolution, compliance
violations [target: zero], cases correctly hard-stopped).

**Phase 6 — Documentation.** README with architecture explanation, RBI/MSME
grounding citations, honest scope/limitations section, and a "what's next" section.

**STOP after every phase.** Before moving to the next phase, output a plain-language
summary of what was built and why, and wait for explicit approval. Do not batch
multiple phases into one uninterrupted run.

---

## 6. Design & implementation level

- **Language:** Python.
- **Storage:** SQLite or structured JSON files — no heavier database needed.
- **LLM calls:** structured/schema-enforced JSON output (function calling or JSON
  mode) for Diagnosis and Strategy — never parse free-form text for decisions.
- **Orchestration:** plain, readable Python — no heavy agent framework. This needs to
  be code a solo student can read line-by-line and explain in an interview, not a
  black box.
- **No frontend web app.** A CLI/script-driven pipeline plus a generated report
  (markdown or simple HTML) is sufficient.
- **File structure (suggested, adjust if there's a clearly better layout):**
  `data/` (generator + synthetic batches), `agents/` (diagnosis.py, strategy.py,
  gate.py, execution.py), `core/` (orchestrator.py, memory.py), `baseline/`,
  `reports/`, `tests/`, `README.md`.
- **Do NOT build:** real payment gateway integration, real outbound calling/SMS/email,
  actual model fine-tuning, microservices, a full frontend app, or systemic
  batch-level pattern detection (e.g. detecting a bank-wide outage across many cases)
  — name that last one explicitly as future work in the README instead of building it.
- **0.85 confidence threshold is a starting parameter.** In Phase 5, log how often
  auto-executed actions would have warranted more caution, and how often escalations
  were arguably unnecessary, and report both numbers — don't just assert the
  threshold is correct.

---

## 7. Attached documents — read these for full detail before Phase 1

1. **PRD (revenue_recovery_agent_PRD.md)** — formal requirements: functional
   requirements (FR-1 through FR-8), non-functional requirements, success metrics,
   explicit out-of-scope items.
2. **Master Plan (razorpay_track3_master_plan.md)** — the opportunity context, demo
   script, judge Q&A prep, risk mitigations.
3. **Stress Test (revenue_recovery_stress_test.md)** — the full case universe (which
   situations are handled and how), 10 traced end-to-end scenarios, and the
   reasoning behind every hard rule in Section 4 above.
4. **Design Decisions Addendum v1.1 (design_decisions_addendum_v1.1.md)** — the exact
   relationship-tier formula and confidence-ladder logic, with the reasoning behind
   each, for defending these choices under questioning.

If anything in this prompt and the attached documents conflicts, the attached
documents are the source of truth for detail; this prompt is the source of truth for
build order and hard rules.

---

## 8. Final deliverable definition — what "done" looks like

- A working, runnable repo with the structure above, clean enough to read.
- A batch metrics report: recovery rate and ₹ recovered vs. the naive baseline,
  average days-to-resolution, zero compliance violations, count of correctly
  hard-stopped cases (fraud/dispute).
- A sample audit-trail export for at least 3 cases: one clean resolution, one gate
  rejection-and-revision, one staged failure-and-recovery.
- A README covering: the problem (with the grounding data), the architecture, how to
  run it, the metrics achieved, and an honest "what this doesn't do yet" section.
- Everything needed to record the 5-minute demo video following this structure: open
  on the ₹8.1 trillion stat → batch loads → one clean case → one gate-rejection case
  → one staged-failure case → batch results screen → one line owning the scope
  boundary.

---

## 9. How to behave when uncertain

Ask before assuming, specifically on: anything that would blur the deterministic
gate / LLM separation, any new compliance rule not grounded in the RBI framework
already given, or any scope addition beyond what Section 5 and the "Do NOT build"
list define. Do not silently simplify Section 4's rules for convenience — if a rule
seems to conflict with something else, flag it and ask rather than picking one
silently.

---

## 10. What to do right now

Read this prompt and all four attached documents fully. Then work on **Phase 1
only**. Before writing any code, present your Implementation Plan for Phase 1 and
wait for explicit approval. Do not proceed to Phase 2, or any later phase, without
that approval each time.
