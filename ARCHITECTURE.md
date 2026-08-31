# Sentinel — Technical Architecture & Methodology Specification

**Track 3: AI Revenue Recovery — Razorpay AI Buildathon**

> **⚠️ ALL DATA IN THIS SYSTEM IS SIMULATED.**
> All payment transactions, debtor records, and customer profiles are synthetically generated for demonstration and benchmarking purposes.

---

## 1. System Overview & Dual-Engine Scope

Sentinel is an autonomous, dual-engine AI revenue recovery system designed to tackle India's **₹8.1 Trillion MSME delayed payments crisis** (2025–26 Economic Survey). It addresses revenue leakage across two distinct temporal horizons:

1. **Failed Payments Engine (Intraday B2C/Checkout)**: Resolves high-velocity checkout failures (bank timeouts, gateway errors, auth expirations, insufficient funds) within minutes to hours.
2. **B2B Receivables Engine (Commercial Invoices)**: Manages overdue commercial invoices across weeks to months (administrative approval lag, working capital cash-flow mismatches, chronic delinquency, and disputes).

---

## 2. Core Architectural Pipeline

```
EVENT (Payment Failure / Invoice Overdue)
      │
      ▼
PRE-PIPELINE GATE (Cost Threshold, Active Promise, Fraud/Dispute Hard-Stops)
      │
      ▼
1. DIAGNOSIS AGENT (LLM Root-Cause Analysis → Category & Confidence)
      │
      ▼
2. STRATEGY AGENT (LLM Proposal from Bounded Menu + 4D Tone Calibration)
      │ (Informed by Historical Recency-Weighted Memory Context)
      ▼
3. CONFIDENCE GATE & FALLBACK LADDER (Safety Step-Down if Conf < 0.85 or Conflicting Signals)
      │
      ▼
4. DETERMINISTIC COMPLIANCE GATE (Hard-Coded RBI Rule Verification — NEVER an LLM call)
      │
      ├── [REJECTED] ──► 1-Retry Re-Proposal under Gate Constraints ──► [ESCALATE_HUMAN]
      └── [APPROVED] ──► 5. EXECUTION AGENT (Simulated Rails with Idempotency & 2 AM Tool Resilience)
                              │
                              ▼
                         6. ADAPTIVE MEMORY UPDATE (Double-Gated: Only Terminal Outcomes)
                              │
                              ▼
                         7. IMMUTABLE AUDIT TRAIL (AuditLog JSON serialization)
```

---

## 3. The Deterministic Compliance Gate

### Architectural Philosophy
Hard compliance rules, regulatory boundaries, and financial limits **must never be left to LLM discretion**. In Sentinel, compliance boundaries live in **plain, deterministic Python code** that executes downstream of the LLM.

### RBI Fair Practices Code Grounding
* **Contact Hours Window**: Outbound debtor contact (`SEND_REMINDER`, `ESCALATE_TONE`) is strictly restricted to **8:00 AM – 7:00 PM IST**. Out-of-hours actions are hard-blocked. Internal safe escalations (`ESCALATE_HUMAN`) and standby actions (`WAIT`) are permitted 24/7.
* **Attempt Caps**:
  * Failed Payments: Maximum **5 automated attempts**.
  * B2B Receivables: Maximum **4 automated touchpoints**.
  * Cases reaching attempt ceilings are blocked from further retries and routed to human queues.
* **Dispute Rights**: Active disputes immediately bypass automated collections and route to human dispute resolution teams.
* **Fraud Zero-Tolerance**: Fraud flags trigger permanent, irreversible `STOP` actions.
* **Idempotency Guarantees**: Every action is keyed by `case_id + attempt_count` to prevent duplicate payment charges or double reminder dispatch.

---

## 4. Defense-in-Depth & 2 AM Resilience

1. **Confidence Fallback Ladder**: If the Strategy Agent's confidence falls below `0.85`, the system steps down from aggressive actions to conservative interventions (`ESCALATE_HUMAN` or `SUGGEST_ALTERNATE_METHOD`), refusing to gamble on low-certainty proposals.
2. **Conflicting Signal Resolution**: When disparate data sources disagree (e.g. Risk Engine indicates low risk, but Support Ticket requests Do-Not-Contact), the Fallback Ladder halts automated retries and escalates to human review.
3. **2 AM Tool Outage Resilience**: Simulated downstream rail failures (e.g. 503 Gateway Outages) trigger an automated 1-retry dispatch. If the rail remains down, Sentinel records the incident in the audit trail and fails gracefully without pipeline crashing.

---

## 5. Adaptive Memory & Dynamic Strategy Context

* **Double-Gated Recording**: Only genuine recovery actions (`RETRY_NOW`, `RETRY_LATER`, `SUGGEST_ALTERNATE_METHOD`, `SEND_REMINDER`, `OFFER_PAYMENT_PLAN`, `ESCALATE_TONE`) that yield terminal outcomes (`SUCCESS` or `FAILED`) update historical weights. Routing actions (`STOP`, `ESCALATE_HUMAN`, `WAIT`) are excluded.
* **Recency Weighting**: Rolling 20-attempt window with exponential decay ($w_i = 1.1^i$) ensures current railway health heavily outweighs stale history.
* **Cold-Start Neutrality**: Returns a neutral `0.50` prior when zero historical samples exist, preventing premature action starvation.
* **Live LLM Integration**: Historical context is dynamically formatted into prompt text, allowing live reasoning models (Google Gemini) to adapt strategy proposals based on measured recovery performance.

---

## 6. Benchmark Methodology & Reproducibility

* **Symmetric Operational Budgets**: Both the Naive Baseline and the AI Recovery Agent receive identical 3-attempt execution budgets (`AGENT_LOOP_MAX_ATTEMPTS = 3`).
* **Permanent Simulation Anchor**: All invoice age calculations (`days_overdue`) and contact-hour evaluations are anchored to `SIMULATED_CURRENT_TIME = datetime(2026, 8, 24, 12, 0, 0)`.
* **Structural RNG Isolation**: All simulation random draws accept an explicit `rng` parameter, isolating benchmark execution from unit test suites and mock simulations.
