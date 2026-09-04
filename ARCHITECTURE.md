# Sentinel — Technical Architecture & Methodology Specification

**Track 3: AI Revenue Recovery — Razorpay AI Buildathon**

> **⚠️ ALL DATA IN THIS SYSTEM IS SIMULATED.**  
> All payment transactions, debtor records, and customer profiles are synthetically generated for demonstration and benchmarking purposes.

---

## 1. System Overview & Dual-Engine Scope

Sentinel is an autonomous, dual-engine AI revenue recovery architecture designed to address India's **₹8.1 Trillion MSME delayed payments crisis** (2025–26 Economic Survey). It resolves revenue leakage across two distinct temporal horizons:

1. **Failed Payments Engine (Intraday B2C/Checkout)**: Resolves high-velocity checkout failures (bank timeouts, gateway errors, auth expirations, insufficient funds) within minutes to hours.
2. **B2B Receivables Engine (Commercial Invoices)**: Manages overdue commercial invoices across weeks to months (administrative approval lag, working capital cash-flow mismatches, chronic delinquency, and disputes).

---

## 2. End-to-End Pipeline Architecture

```
EVENT (Payment Failure / Invoice Overdue)
      │
      ▼
STEP 0: PRE-PIPELINE STATUTORY CHECK (core/compliance.py)
   ├── Fraud Flag Detected ──────────────► Hard Stop (STOP)
   ├── Active Dispute Flag ──────────────► Human Dispute Queue (ESCALATE_HUMAN)
   ├── Active Promise-to-Pay (Unexpired) ─► Awaiting Promise (WAIT)
   └── Attempt Cap Exceeded (>= Ceiling) ─► Attempt Cap Escalation (ESCALATE_HUMAN)
      │
      ▼ (If case is eligible and addressable)
STEP 1: ROOT-CAUSE DIAGNOSIS AGENT (agents/diagnosis.py)
   Multi-Perspective Self-Consistency (FACTUAL, COUNTER_INDICATOR, CONSERVATIVE)
   └── Consensus Classification & Calibrated Confidence Assessment
      │
      ▼
STEP 2: STRATEGY PROPOSAL AGENT (agents/strategy.py)
   Bounded Action Menu Selection + 4D Tone Calibration (Polite, Firm, Urgent, Escalated)
   Informed by Recency-Weighted Adaptive Memory Statistics (core/memory.py)
      │
      ▼
STEP 3: SOFT FALLBACK LADDER (agents/strategy.py)
   Safety step-down if strategy confidence < 0.85 or conflicting operational signals
      │
      ▼
STEP 4: DETERMINISTIC COMPLIANCE GATE (core/compliance.py)
   Independent, Non-AI Verification Layer (Plain Python Invariants):
   ├── RBI Contact Hours Check (8:00 AM – 7:00 PM IST)
   ├── Maximum Attempt Ceiling Check (5 Payment / 4 B2B)
   ├── Minimum Value Recovery Floor Check (₹500 Payment / ₹5,000 B2B)
   ├── Thread-Safe Idempotency & Concurrency Lock (Atomic check-and-reserve)
   └── Regulatory Confidence Floor Check (>= 0.85 for active interventions)
      │
      ├── [GATE REJECTED] ──► 1-Retry Re-Proposal under Gate Constraints ──► [ESCALATE_HUMAN]
      └── [GATE APPROVED] ──► STEP 5: EXECUTION AGENT (agents/execution.py)
                                 │
                                 ▼
                              STEP 6: ATOMIC EXECUTION & 2 AM TOOL RESILIENCE
                                 │
                                 ▼
                              STEP 7: ADAPTIVE MEMORY UPDATE (core/memory.py)
                                 (Double-gated: Only terminal recovery actions recorded)
                                 │
                                 ▼
                              STEP 8: IMMUTABLE AUDIT TRAIL LOGGING (core/audit_log.py)
```

### Textual Diagram Description: The 5-Phase Flow

1. **Detect (Step 0)**: Ingests transaction failure or overdue invoice events. Before any generative AI model is called, deterministic statutory rules verify whether the case is legally actionable, halting fraud, dispute, exhausted attempt, or active promise-to-pay cases.
2. **Diagnose (Step 1)**: Evaluates root cause using 3-sample multi-perspective self-consistency (`FACTUAL`, `COUNTER_INDICATOR`, `CONSERVATIVE`), arriving at an analytical diagnosis category and calibrated confidence score.
3. **Decide (Steps 2–3)**: Consumes historical outcome memory, debtor tier, and diagnostic cause to select a single recovery action from a strictly bounded domain menu, with automatic fallback ladder step-down if confidence is low or signals conflict.
4. **Deterministic Gate (Step 4)**: The proposed action is intercepted and verified by plain Python code against RBI Fair Practices regulations and financial ceilings. If rejected, the agent re-proposes once under gate constraints before escalating to human queues.
5. **Execute & Audit (Steps 5–8)**: Dispatches recovery with atomic idempotency locks and 2 AM tool-failure retries, updates Bayesian outcome memory, and records a cryptographically auditable JSON trail.

---

## 3. Shared Dual-Engine Pipeline: The Five Functions

Both **Failed Payments** (`FAILED_PAYMENT`) and **B2B Receivables** (`B2B_RECEIVABLE`) flow through an identical state machine. Domain differences are strictly confined to schema-guided prompt construction and bounded action-menu selection; the core orchestration, verification, and execution logic is never duplicated:

1. **`process_case()` in [`core/orchestrator.py`](core/orchestrator.py)**: Coordinates the case lifecycle — evaluating pre-pipeline skip conditions (fraud, disputes, attempt ceilings), orchestrating the multi-attempt adaptive retry loop (`AGENT_LOOP_MAX_ATTEMPTS = 3`), triggering re-proposals on gate rejection, applying fallback ladders, and managing terminal outcome states.
2. **`diagnose()` in [`agents/diagnosis.py`](agents/diagnosis.py)**: Performs multi-sample self-consistency diagnostic passes across distinct analytical perspectives (`FACTUAL`, `COUNTER_INDICATOR`, `CONSERVATIVE`) to classify root cause and compute calibrated consensus confidence.
3. **`propose_strategy()` in [`agents/strategy.py`](agents/strategy.py)**: Consumes the diagnosis, debtor/customer history, relationship tier, and historical memory statistics to propose exactly one compliant action from the domain-specific bounded menu.
4. **`run_all_checks()` in [`core/compliance.py`](core/compliance.py)**: Independently evaluates proposed actions against hard regulatory rules, statutory contact windows, attempt caps, concurrency locks, and confidence thresholds before any action is dispatched.
5. **`execute()` in [`agents/execution.py`](agents/execution.py)**: Dispatches the verified recovery action under atomic idempotency protection with exception safety, returning a structured execution result.

### Domain Parameterization Matrix

| Architectural Dimension | Failed Payments Vertical | B2B Receivables Vertical |
| :--- | :--- | :--- |
| **Case Schema** | `FailedPaymentCase` (`core/schemas.py`) | `B2BInvoiceCase` (`core/schemas.py`) |
| **Temporal Granularity** | Minutes to Hours (Intraday) | Days to Weeks (Commercial cycles) |
| **Primary Root Causes** | `BANK_TIMEOUT`, `INSUFFICIENT_FUNDS`, `AUTH_EXPIRED`, `GATEWAY_ERROR` | `ADMINISTRATIVE_DELAY`, `CASH_FLOW_MISMATCH`, `CHRONIC_DELINQUENCY`, `DISPUTED_INVOICE` |
| **Bounded Action Menu** | `RETRY_NOW`, `RETRY_LATER`, `SUGGEST_ALTERNATE_METHOD`, `ESCALATE_HUMAN`, `STOP` | `SEND_REMINDER`, `OFFER_PAYMENT_PLAN`, `ESCALATE_TONE`, `WAIT`, `ESCALATE_HUMAN`, `STOP` |
| **Statutory Attempt Ceiling** | 5 automated attempts (`MAX_ATTEMPTS_PAYMENT`) | 4 automated touchpoints (`MAX_ATTEMPTS_B2B`) |
| **Regulatory Contact Hours** | Exempt on non-contact technical retries; 8 AM–7 PM on contact | Strictly enforced (8:00 AM – 7:00 PM IST) |
| **Fatigue Cap Ceiling** | 2 consecutive attempts within 24h | 3 debtor touchpoints without response |
| **Minimum Value Threshold** | ₹500 (`MIN_RECOVERY_AMOUNT_PAYMENT`) | ₹5,000 (`MIN_RECOVERY_AMOUNT_B2B`) |

---

## 4. Multi-Perspective Self-Consistency & Confidence Gating

### Multi-Sample Diagnostic Consensus (`agents/diagnosis.py`)

Single-pass LLM prompts can hallucinate or vacillate when debtor data contains ambiguous signals. Sentinel implements an architectural multi-sample consensus mechanism:
- Every eligible case runs through three distinct prompt perspectives:
  1. `FACTUAL`: Evaluates hard failure codes, payment history metrics, and timestamp differences.
  2. `COUNTER_INDICATOR`: Actively searches for disconfirming signals (e.g., whether a recent customer complaint undermines an apparent network error).
  3. `CONSERVATIVE`: Assumes adverse intent or liquidity insolvency when ambiguity arises.

### Calibrated Consensus Decision Rules

1. **Unanimous Consensus (3/3 samples agree)**:  
   High agreement indicates clear root cause. The majority category is adopted, and confidence retains full consensus weight ($\ge 0.85$, typically $0.88–0.95$).
2. **Majority Consensus (2/3 samples agree)**:  
   A dissenting sample indicates genuine underlying ambiguity. Sentinel adopts the majority category but applies **calibrated confidence decay, strictly capping confidence at 0.80**. Because $0.80 < 0.85$, this automatically triggers the Fallback Ladder and the Deterministic Confidence Gate to escalate safely rather than risk an aggressive, ungrounded action.
3. **Split Vote (1/1/1 disagreement)**:  
   Total divergence across perspectives. Sentinel classifies root cause as `UNKNOWN`, assigns default low confidence ($0.50$), logs `SELF_CONSISTENCY_DISAGREEMENT`, and immediately stands down to human operations.

### Independent Gate Backstop (`check_confidence_threshold` in `core/compliance.py`)

Even if an unvalidated caller or malformed strategy prompt bypasses the Strategy Agent's internal soft fallback ladder, the Deterministic Compliance Gate independently evaluates:
$$\text{confidence} \ge \text{config.CONFIDENCE\_THRESHOLD}\quad (0.85)$$
If sub-threshold, the gate independently rejects the action with `LOW_CONFIDENCE_BLOCKED` and routes directly to the human operations queue. Safe passive actions (`ESCALATE_HUMAN`, `STOP`, `WAIT`) remain exempt, ensuring the system can always stand down safely.

---

## 5. Where We Chose Not to Use AI

In an autonomous financial recovery engine, utilizing generative AI for deterministic constraints creates unacceptable operational, financial, and regulatory risk. Sentinel explicitly restricts generative LLMs to genuine ambiguity (identifying root causes from noisy signals and calibrating conversational/strategy tone), while enforcing all boundaries through deterministic, non-AI code:

### 1. The Hard-Rule Compliance Gate (`core/compliance.py`)
Statutory boundaries — including RBI contact-hour windows (8 AM – 7 PM IST), attempt ceilings (5 for payments, 4 for B2B), and mandatory dispute/fraud halts — are implemented in pure deterministic Python. Legal compliance must be an unbendable invariant rather than a probabilistic LLM prediction subject to prompt drift, jailbreaking, or hallucination.

### 2. The Idempotency & Concurrency Lock (`core/compliance.py` / `AuditLog`)
Atomic reservation state is protected under re-entrant threading locks (`threading.Lock`) before execution. Preventing double-debiting and duplicate debtor communications during near-simultaneous webhook retries requires strict transactional atomicity that probabilistic language models cannot guarantee.

### 3. The Win-Rate Statistics Tracker (`core/memory.py`)
Historical strategy performance and category success rates are tracked via Bayesian arithmetic and deterministic sliding windows. Injected memory context must represent mathematical empirical truth rather than model-generated summaries of past performance.

### 4. Deterministic Confidence-Threshold Enforcement (`check_confidence_threshold` in `core/compliance.py`)
The `0.85` regulatory confidence threshold (sourced directly from `config/rules_config.json`) is evaluated as an independent, deterministic gate check on the AI-produced confidence score. This ensures that the decision of whether a proposal clears the safety floor is an immutable architectural backstop rather than an AI decision left to the model itself.

---

## 6. RBI Fair Practices Code Grounding

Sentinel’s B2B compliance rules are mathematically grounded in the **Reserve Bank of India (RBI) Fair Practices Code for Lenders & Recovery Agents**:

* **Contact Hours Window (8:00 AM – 7:00 PM IST)**: Outbound debtor contact (`SEND_REMINDER`, `ESCALATE_TONE`) is strictly blocked outside reasonable business hours. Internal safe escalations (`ESCALATE_HUMAN`) and standby actions (`WAIT`) are permitted 24/7.
* **Debtor Dispute Invariant**: Active disputes immediately halt automated collections and route the invoice to human dispute resolution teams with reason code `dispute_skip`.
* **Zero-Harassment Attempt Caps**:
  * Failed Payments: Ceiling of 5 automated attempts.
  * B2B Receivables: Ceiling of 4 automated contact touchpoints.
* **No Intimidating Tone**: The Strategy Agent's tone modulation is bounded across 4 calibrated levels (`POLITE`, `FIRM`, `URGENT`, `ESCALATED`) with strict prohibitions against threatening, abusive, or coercive language.
* **Promise-to-Pay Lifecycle Tracking**: Unexpired promises-to-pay (`WAIT`) pause all recovery communications until the promised settlement date, preventing premature or harassing reminders.

---

## 7. Defense-in-Depth & 2 AM Tool Resilience

1. **Soft Fallback Ladder**: If the Strategy Agent's confidence falls below `0.85`, the system steps down from aggressive actions to conservative interventions (`ESCALATE_HUMAN` or `SUGGEST_ALTERNATE_METHOD`), refusing to gamble on low-certainty proposals.
2. **Conflicting Signal Resolution**: When disparate data sources disagree (e.g. Risk Engine indicates low risk, but Support Ticket requests Do-Not-Contact), the Fallback Ladder halts automated retries and escalates to human review.
3. **2 AM Tool Outage Resilience**: Simulated downstream rail failures (e.g. 503 Gateway Outages) trigger an automated 1-retry dispatch under exception handling. If the rail remains down, Sentinel logs the incident in the audit trail and fails gracefully without crashing worker threads.
4. **Thread-Safe Atomic Idempotency**: Pre-execution atomic reservation prevents concurrent race conditions across distributed worker processes.

---

## 8. Adaptive Memory & Bayesian Context Injection

* **Double-Gated Recording**: Only genuine recovery actions (`RETRY_NOW`, `RETRY_LATER`, `SUGGEST_ALTERNATE_METHOD`, `SEND_REMINDER`, `OFFER_PAYMENT_PLAN`, `ESCALATE_TONE`) that yield terminal outcomes (`SUCCESS` or `FAILED`) update historical weights. Routing actions (`STOP`, `ESCALATE_HUMAN`, `WAIT`) are excluded.
* **Recency Weighting**: Rolling 20-attempt window with exponential decay ($w_i = 1.1^i$) ensures current railway health heavily outweighs stale history.
* **Cold-Start Neutrality**: Returns a neutral `0.50` prior when zero historical samples exist, preventing premature action starvation.
* **Live LLM Prompt Injection**: Measured success rates per `(diagnosis_category, action)` pair are formatted and injected as dynamic context into strategy generation prompts, allowing live reasoning models (Google Gemini) to adapt strategy proposals based on measured recovery performance.

---

## 9. Benchmark Methodology & Reproducibility

* **Symmetric Operational Budgets**: Both the Naive Baseline and the AI Recovery Agent receive identical 3-attempt execution budgets (`AGENT_LOOP_MAX_ATTEMPTS = 3`).
* **Permanent Simulation Anchor**: All invoice age calculations (`days_overdue`) and contact-hour evaluations are anchored to `SIMULATED_CURRENT_TIME = datetime(2026, 8, 24, 12, 0, 0) IST`, preventing wall-clock test drift.
* **Structural RNG Isolation**: All simulation random draws accept an explicit `rng` parameter, isolating benchmark execution from unit test suites and mock simulations.
* **Shared Probability Tables**: Identical underlying success probability matrices are used for both baseline and AI agents (`config.PAYMENT_RETRY_SUCCESS_PROB` and `config.B2B_REMINDER_SUCCESS_PROB`). The AI agent wins purely through accurate root-cause diagnosis and optimal action selection, never through inflated simulation odds.

---

## 10. Production Roadmap (Buildathon Scope Boundaries)

Sentinel is architecturally complete for buildathon evaluation. The following represent production transition steps:

* **Async Worker Integration**: Replace sequential loops with distributed Celery/FastAPI workers backed by Redis queue brokers.
* **Live Gateway Webhooks**: Replace `agents/execution.py` simulation tables with live Razorpay Payments API and WhatsApp/SMS webhook integrations.
* **Distributed State Store**: Migrate JSON memory and audit logs to PostgreSQL / CockroachDB with row-level transactional locks.
