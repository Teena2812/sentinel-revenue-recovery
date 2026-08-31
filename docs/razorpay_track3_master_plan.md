# Razorpay AI Buildathon — Track 3 (AI Revenue Recovery)
## Master Plan — Final, Consolidated

This is the single source of truth for the project. Everything below has already been
stress-tested and converged on — build from this document, don't re-debate the direction.

---

## 0. One-Line Summary

An AI agent system that finds revenue Razorpay is losing (failed payments and overdue
B2B receivables), figures out *why*, decides a compliant recovery action, independently
verifies that action against hard rules before acting, executes it safely, and gets
measurably better at choosing strategies as it sees more cases.

---

## 1. The Opportunity, in Brief

- **What it is:** Razorpay's Buildathon replaces resume screening with a build-and-defend
  filter. Strong submissions go straight to a panel interview for a paid internship
  (₹75,000/month, Bengaluru, 6 or 12 months).
- **Format:** No aptitude test. Submit a public repo + a 5-minute pitch video + an
  architecture explanation.
- **Deadline:** September 5, 2026 — treat this as both the application and the
  submission-readiness date. Confirm on the actual application form if a separate
  build window is revealed there.
- **Track:** AI Revenue Recovery (Track 3) — *"Find revenue that's slipping away and win
  it back."*
- **Their own bar, verbatim in spirit:** *"Don't just identify the problem. Show measured
  money recovered across a batch, with compliant escalation, stopping rules, and an
  audit trail."*
- **The proof they explicitly ask for:** a repo that actually runs, a 5-minute video of
  it working, and **what broke at 2 AM, and how you got out.** That last one is a real,
  named requirement — your demo must show a designed failure and recovery, not just a
  happy path.

---

## 2. What Track 3 Actually Requires (the real checklist)

1. **Root cause diagnosis** — not just detecting a problem, explaining why it's happening.
2. **A genuine decision between multiple actions** — not one fixed response every time.
3. **Bounded, gated actions** — explicit limits on what the system is allowed to do.
4. **Compliant escalation** — real-world rules of conduct, not invented ones.
5. **Stopping rules** — the system must know when to give up or hand off to a human.
6. **Measured results on a batch** — a real recovery-rate number, not one lucky demo case.
7. **An audit trail** — every decision explainable after the fact.
8. **Graceful failure handling** — at least one case where something breaks and the
   system recovers instead of crashing silently.

Every track description on their page repeats some version of "measured, honest,
auditable" — that phrase, repeated five different ways, is their real evaluation
philosophy. This document is built around satisfying it directly, not around looking
impressive.

---

## 3. The Idea — Final

**Domain:** B2B Receivables / Promise-to-Pay as the primary, deeply built scenario,
demoed through a **generalized reasoning core** that also runs Failed Payment cases —
proving the architecture isn't hardcoded to one situation.

**Why B2B receivables, briefly:** it's the richest genuine-reasoning space of the
available directions (debtor history, promise-keeping track record, relationship value,
and compliance risk have to be weighed together — not looked up), it's grounded in a
real, current, national-scale problem, and it's less crowded than the more "obvious"
payment-retry reading of the track.

**The real-world stakes, for your pitch opener:** an estimated ₹8.1 trillion is
currently locked in delayed payments to India's MSME sector (Economic Survey 2025-26),
and the average small business carries roughly ₹3.83 crore in overdue receivables,
well past the legally mandated payment window. This isn't a hypothetical hackathon
prompt — it's a live problem serious enough that Parliament is currently working an
MSME Amendment Bill through, and there's a standing government grievance system
(MSME Samadhaan) with over a quarter million unresolved cases.

---

## 4. System Architecture — Final

This is the system as it stands after a full adversarial stress test (case universe,
10 traced scenarios, consistency testing, red team / blue team convergence). The
safety mechanisms below are not extras — they are core, load-bearing parts of the
design.

```
EVENT (payment fails / invoice overdue)
      ↓
DETECTION
      ↓
DIAGNOSIS AGENT (LLM)
Reasons out the likely root cause from case context
      ↓
STRATEGY AGENT (LLM)
Proposes one bounded action from a fixed menu, with a confidence score
      ↓
DETERMINISTIC GATE (plain code — NOT an LLM)
Checks: attempt cap? contact-hour window? dispute/fraud hard-stop?
idempotency (has this exact action already fired)?
      ↓
EXECUTION AGENT
Calls the tool (simulated retry / simulated notification), idempotency-checked
      ↓
RESULT
      ↓
MEMORY + ANALYTICS
Per-strategy, per-case-type success rate, recency-weighted (not lifetime average)
      ↓
ADAPTATION
Future proposals for similar cases are re-ranked by recent outcomes
      ↓
STOPPING-RULE CHECK
Resolved / genuinely give up / escalate to human
```

### Why the Deterministic Gate matters (don't skip this in the pitch)

A consistency test on the earlier all-LLM design showed a real problem: an LLM asked
"should we retry a 5th time?" or "is this dispute serious enough to stop?" can answer
inconsistently across runs on identical input. That's unacceptable for actions that
touch money or a business relationship. The fix: hard boundaries (attempt caps,
contact-hour windows, fraud/dispute stop, idempotency) live in **plain deterministic
code**, checked independently of the LLM. The LLM's job is reasoning about the
genuinely ambiguous middle — root cause, tone, which soft intervention to offer — not
enforcing hard limits. This is a *stronger* architecture, not a smaller one: it's the
difference between a system that reliably respects boundaries and one that merely
promises to.

### Agents and their real jobs

| Agent | Job | Notes |
|---|---|---|
| Diagnosis | Explain *why* this case is happening, in plain language, not just a category tag | LLM, structured JSON output |
| Strategy | Propose *one* bounded action + a confidence score | LLM, structured JSON output |
| Deterministic Gate | Approve or reject the proposed action against hard rules | Plain code, not LLM — this is what makes the system safe and consistent |
| Execution | Carry out the approved action via a tool, idempotency-checked | Simple, mechanical, reports success/failure |
| Memory/Analytics | Track full case history + per-strategy outcome rates | Shared state store, not a "personality" agent |

### Compliance grounding (for B2B)

Rules encoded should reflect RBI's Fair Practices Code principles: contact only
between reasonable hours (e.g. 8 a.m.–7 p.m.), no intimidating or coercive language,
no public shaming, written notice before recovery action, and the debtor's right to
dispute. Cite this explicitly in your README — it's what turns "we made up business
rules" into "we grounded this in a real regulatory framework."

---

## 5. Case Handling Reference

Legend: 🟢 fully handled 🟡 partially handled ⚪ intentionally out of scope

| Situation | Status | Handling |
|---|---|---|
| Insufficient funds / bank timeout / auth failure | 🟢 | Diagnose → propose retry timing → gate → execute |
| Risk/fraud rejection | 🟢 (fixed) | Deterministic hard STOP — no retry, no contact, flag to human |
| Disputed invoice | 🟢 (fixed) | Deterministic hard STOP — route to human dispute queue |
| Duplicate retry risk | 🟢 (fixed) | Idempotency key per case+attempt in Execution |
| Broken promise-to-pay | 🟢 | Escalation ladder, tone adjusted by relationship-value field |
| High-value relationship-sensitive debtor | 🟢 (fixed) | `relationship_value` field feeds Strategy's tone/aggressiveness |
| Conflicting data signals | 🟢 (fixed) | Low confidence or source disagreement → escalate to human, don't guess |
| Tiny-value case (recovery costs more than it's worth) | 🟢 (fixed) | Cost threshold gate — below it, cheap automatic path only, skip full pipeline |
| Tool/API call fails mid-case | 🟢 | Retry once, then fallback + log — this is your "2 AM" demo moment |
| Systemic bank outage (many cases, same cause) | ⚪ | Named as future work — batch/pattern-level reasoning, not case-level |
| Payment succeeds but ledger doesn't update | ⚪ | Out of scope — Track 4's reconciliation problem |
| True predictive prevention (before failure happens) | 🟡 | Light heuristic risk score only — explicitly not real ML, say so plainly |

**Honest coverage:** with the fixes above built in from day one, this covers roughly
80–85% of the realistically important problem space for this scope. The remaining gap
is legitimate future work (systemic pattern detection, true preventive intervention),
not a hidden weakness.

---

## 6. Data & Simulation Plan

- **Failed payments:** simulate realistic failure-code distributions (insufficient
  funds, bank timeout, auth failure, gateway error, fraud rejection) with varying
  amounts, timestamps, and attempt history.
- **B2B receivables:** simulate invoices with age, amount, debtor payment-history
  pattern, dispute flags, and a `relationship_value` tag — grounded in the RBI Fair
  Practices Code constraints for contact rules.
- **Always label this clearly as simulated** in the README and the video, stated
  upfront and unprompted — don't let a judge have to ask.
- Build a **naive fixed-rule baseline** (e.g. flat retry schedule / flat reminder
  cadence) to compare your system against. "We beat a dumb baseline by X%" is far more
  convincing than a bare recovery number alone, and it's a must-have, not optional.

---

## 7. Metrics & Evaluation

- Recovery rate (% and ₹) across the batch, vs. baseline
- Average days-to-resolution
- Zero-compliance-violation count across the batch (a clean number here is a strong signal)
- Cases correctly hard-stopped (fraud/dispute) — proves the gate works, not just the happy path
- Strategy-weight shift shown before vs. after running the batch (your adaptation proof —
  described honestly as a windowed heuristic, not model learning)

---

## 8. 13-Day Build Plan

**Day 1–2 — Foundation.** Case schema (both scenarios) including the new fields
(`relationship_value`, cost threshold, confidence score, idempotency key). Encode RBI-
style compliance rules as explicit, checkable constraints. Build the synthetic data
generator and the naive baseline.
*Done when:* a believable batch of cases exists in a file/DB, and the baseline can run
against it.

**Day 3–5 — Core loop (critical path).** Build Diagnosis → Strategy → Deterministic
Gate → Execution → Memory, validated end-to-end **on Failed Payments first** (simpler,
proves the plumbing).
*Done when:* one case runs the full loop without manual intervention, and the gate
correctly blocks at least one deliberately bad proposal.

**Day 6–8 — Depth.** Extend to B2B receivables' longer case lifecycle. Confirm the gate
handles disputed-invoice and relationship-sensitive cases correctly.
*Done when:* you can trigger and show a real gate-rejection case on demand.

**Day 9–10 — Adaptation + failure story.** Add outcome logging and the windowed
strategy-weight update. Stage the deliberate tool-failure case and its recovery.
*Done when:* running the batch twice shows a visibly different (better) strategy
distribution the second time, and the staged failure resolves gracefully on demand.

**Day 11 — Proof.** Run the full batch against the baseline, generate the metrics
report.

**Day 12 — Demo + docs.** Record the video (script below), write the architecture
README, cite the RBI grounding and the MSME statistics.

**Day 13 — Buffer.** Something will break. This day exists for that.

**Critical path — cannot slip:** Days 3–5. Nothing later matters if the core loop
isn't solid.

---

## 9. MVP vs. Stretch

**MUST BUILD (this is the real MVP now, not a stretch layer):**
Diagnosis + Strategy + Deterministic Gate + Execution agents · idempotency ·
relationship-value field · cost-threshold gate · confidence-based escalation on
conflicting signals · synthetic dataset for both scenarios · RBI-grounded compliance
rules · stopping rules · audit trail · windowed outcome-based strategy weighting · one
staged failure-recovery case · naive baseline comparison · batch metrics report.

**SHOULD BUILD:** Second scenario (Failed Payments) shown running through the same
engine, to prove generality · a clean summary/report view for the demo.

**STRETCH (only if the above is stable by Day 10):** Nicer dashboard UI, a second
language/tone option for reminders, more case-type variety, the systemic bank-outage
batch-level pause behavior.

**DO NOT BUILD:** Real payment gateway integration, real outbound calling/SMS, actual
model retraining, microservices, a full frontend app, anything that doesn't serve the
core reasoning-and-verification story.

---

## 10. The 5-Minute Demo Script

1. **Open (20s):** State the real number — ₹8.1 trillion stuck in delayed MSME
   payments — before touching the screen.
2. **Batch load (20s):** Show ~50 synthetic B2B cases + a smaller failed-payment set
   loading.
3. **Case 1 — clean win (60s):** Walk one case live: diagnosis → strategy → gate
   approves → execution → resolved. Show the reasoning text, not just the outcome.
4. **Case 2 — the gate rejecting Strategy (60s):** Most important beat. Show Strategy
   proposing something out of bounds (wrong hour, or escalating a disputed invoice),
   the deterministic gate rejecting it with a stated reason, Strategy revising. This
   single moment proves genuine multi-agent value better than anything else.
5. **Case 3 — the deliberate break (45s):** A tool call fails mid-case. Show it caught,
   logged, falling back, still resolving instead of crashing.
6. **Close (45s):** Batch results — ₹ recovered vs. at risk, vs. baseline, zero
   compliance violations, and the before/after strategy-weight shift. End on the
   number.
7. **Last line, own your scope:** *"This operates at reactive and adaptive recovery
   today. The natural next step is batch-level pattern detection and true preventive
   intervention — here's specifically what that would look like."* State this
   confidently, as product thinking, not as an apology for what's missing.

---

## 11. Judge Q&A Prep

- **"Why does this need an LLM instead of a rules engine?"** → Because Diagnosis and
  Strategy reason over genuinely ambiguous, multi-factor cases (debtor history,
  relationship value, disputed status) that a lookup table can't — while the hard
  limits that *can* be enumerated in advance are enforced deterministically, not by
  the LLM.
- **"Is this real Razorpay data?"** → No — simulated, grounded in RBI's Fair Practices
  Code and public MSME receivables statistics. Say this upfront, don't wait to be asked.
- **"Show me the learning actually happening."** → Have the before/after strategy-weight
  comparison on screen. Describe it precisely: memory + analytics + windowed adaptation
  — explicitly not model retraining.
- **"What's your recovery rate vs. doing nothing / a fixed schedule?"** → This is why
  the baseline comparison is a MUST BUILD item.
- **"What happens with a disputed invoice?"** → Maps directly to a named stopping rule
  in the audit log — show it live if asked.
- **"Would this scale to millions of transactions?"** → Honestly: at hackathon scale
  this calls an LLM per case; at production scale you'd add a cheap rule/classifier
  filter first and reserve the LLM for ambiguous or high-value cases. Naming this
  tiered answer proactively is a strong signal — most students haven't thought about it.

---

## 12. Biggest Risks & Mitigations

| Risk | Mitigation |
|---|---|
| The verification/learning layer eats the whole 13 days | Build the simple end-to-end loop on the easier scenario first (Days 3–5) |
| B2B rules feel invented | Cite RBI Fair Practices Code and the MSMED 45-day rule explicitly in the README |
| "Learning" claim gets exposed as thin under questioning | Describe it accurately as windowed policy adjustment, own the limit plainly |
| LLM flakiness during a live demo | Pre-run and cache the demo batch, don't call live APIs on stage |
| Data realism scrutiny | Label all data as simulated, clearly, from the first slide |

---

## 13. Final Confidence

**Idea/direction correctness:** ~85–90%, after full adversarial testing — this part
is near its honest ceiling; further idea-switching won't meaningfully improve it.

**Overall (idea × execution) confidence, if the build plan above is followed with
discipline:** realistically **75–88%** — the range depends entirely on execution
quality and scope discipline over the next 13 days, not on finding a "better" idea.
Anything presented as higher than this would be manufactured precision, not an honest
estimate — real selection also depends on the applicant pool and panel judgment on
the day, which is genuinely outside anyone's control.

The highest-leverage thing left to do is not more strategy — it's starting Day 1.
