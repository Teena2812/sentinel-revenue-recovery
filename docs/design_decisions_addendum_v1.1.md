# Design Decisions Addendum v1.1

This document refines three specific parts of the system per locked decisions. It does
not reopen the core idea or architecture — those remain as converged in the Master Plan
and PRD. Treat this as a supplement to both.

---

## 1. Solo Feasibility Audit

**Application form, verified directly:** the first page of the official Google Form
asks for Email, Full Name, College Name, Graduation Year, and internship availability
— all individual fields, no team-name or team-member field visible. This supports
solo submission as the default. Caveat: Google Forms load subsequent pages
dynamically, so only page 1 was confirmed — worth a quick manual check when you
actually fill it out, in case a later page introduces a team option.

**Audit result: nothing in the current architecture secretly requires more than one
person.** The system has multiple *logical components* (Diagnosis, Strategy,
Deterministic Gate, Execution, Memory), but all of them are code within a single
codebase, built and run sequentially by one developer — this is a design decomposition,
not a staffing requirement. Nothing requires simultaneous parallel work by different
people (no separate frontend team, no separate data-science team, no separate infra
team).

**The real solo risk was never headcount — it's total scope against 13 days.** This
was already addressed by the MVP/critical-path discipline in the Master Plan (core
loop validated on the simpler scenario first, second scenario demoted to "should
build," full frontend explicitly excluded). No changes needed here; this section
exists to confirm, on request, that the audit was actually performed rather than
assumed.

*(Note: earlier in this project's discussion, a team-split scenario was floated as a
hypothetical fallback. That framing is now superseded — the design below assumes
strict solo execution throughout, not a team option held in reserve.)*

---

## 2. Relationship-Value Tier — Derivation Logic

**Output shown to the agent and in the audit trail:** `LOW` / `MEDIUM` / `HIGH` only.
**Internally:** computed deterministically (plain code, not an LLM guess) — this keeps
it reproducible, debuggable, and consistent with the project's core principle that
anything structured and checkable belongs in deterministic code, not free-form
reasoning.

### The formula

A composite **Relationship Score (0–100)** is computed from three weighted, normalized
signals:

| Signal | Weight | What it captures | Why it's weighted this way |
|---|---|---|---|
| **Value percentile (V)** | 40% | This case's amount, ranked against the portfolio's amount distribution (not raw ₹, so it's comparable across very different case sizes) | Larger cases carry more business weight to protect |
| **Historical reliability (H)** | 35% | Proportion of this customer/debtor's past cases resolved cleanly, without escalation or dispute | Rewards a track record worth preserving — this is the actual "relationship" signal |
| **Engagement tenure (T)** | 25% | Number of prior interactions/transactions on record | More established relationships warrant more care before an aggressive action |

```
Relationship Score = 0.40×V + 0.35×H + 0.25×T   (each term normalized 0–1, score scaled to 0–100)
```

### Tier cutoffs
```
Score ≥ 70        → HIGH
40 ≤ Score < 70    → MEDIUM
Score < 40         → LOW
```

### Fatigue override (important — this is what makes the tier defensible, not just a value score)

If this specific case's **retry/contact count so far** exceeds a set cap (e.g. >3 for
B2B, >2 for payments), the tier is **downgraded by one level regardless of score**
(HIGH→MEDIUM, MEDIUM→LOW). Rationale, for judges: a case that's already been contacted
repeatedly needs *more* caution going forward, not less — a high raw value score
should never be read as license to keep pushing on an already-fatigued case. This
directly prevents the system from treating "important" as "keep escalating."

### Cold-start handling
If a customer/debtor has no prior history (H is undefined), default H to a neutral
0.5 rather than 0 or 1 — a new relationship isn't assumed good or bad. In practice
this means a brand-new, high-value case can still land in MEDIUM or even HIGH purely
on value percentile, while a brand-new, low-value case defaults toward LOW. State this
explicitly in the audit log entry ("H defaulted — no prior history") so it's never a
silent assumption.

### Why relationship-value and recovery-likelihood are kept separate
"Recovery potential" (how likely a given intervention is to succeed) is deliberately
**not** folded into this score. It's computed and used separately, inside Strategy's
action selection. Merging "how much do we value this relationship" with "how likely
is recovery" would make the tier harder to explain with one clean formula — and a
tier a judge can't get a straight answer about undermines the whole audit-trail
story. Keep the two questions separable.

**Defense summary for judging:** *"The tier is a weighted score of value, track
record, and relationship tenure, with a hard downgrade rule if we've already
contacted this case too many times — so 'important' never overrides 'we need to back
off.' It's fully deterministic and logged, not an LLM guess."*

---

## 3. Confidence & Conflicting-Signal Policy

### The auto-execute rule (as specified)
An action auto-executes only when **all four** hold:
1. Strategy's confidence score ≥ **0.85**
2. No critical conflicting signal is present (defined below)
3. The action passes the Deterministic Gate (FR-4 in the PRD)
4. The action is reversible or low-risk where a lower-risk alternative isn't required

**0.85 is an explicitly configurable starting parameter, not a validated number.**
State this plainly if asked — see calibration plan below.

### What counts as a "critical conflicting signal"
Defined explicitly, so this isn't left ambiguous in the code or the audit log:
- A fraud/risk flag contradicts a "safe to proceed" diagnosis
- A dispute flag is present while the proposed action still involves contact
- Two available signals disagree in a way that changes the recommended action (e.g.
  one source says "likely to pay soon," another shows a new broken promise)

### Fallback ladder — when the auto-execute rule is NOT met

```
Confidence ≥ 0.85, no conflict, gate passes, low-risk?
      │
      ├── YES → AUTO-EXECUTE
      │
      └── NO  → work down this ladder, stop at the first fit:
             │
             ├── 1. GATHER MORE INFO
             │      Is there an additional cheap lookup (case history, dispute
             │      status) that could resolve the ambiguity? If yes, fetch it,
             │      re-run Diagnosis/Strategy.
             │
             ├── 2. WAIT
             │      Can the case tolerate a delay without materially raising risk?
             │      If yes, re-evaluate after a defined interval instead of acting now.
             │
             ├── 3. TRY A SAFER ACTION
             │      Does a strictly lower-risk option exist in the action menu
             │      (e.g. a soft reminder instead of an escalation, a delayed retry
             │      instead of an immediate one) that still passes the gate?
             │
             ├── 4. ESCALATE FOR HUMAN REVIEW
             │      If none of the above resolve it and the stakes are non-trivial.
             │
             └── 5. STOP
                    Case is stale, limits are exhausted, or none of the above apply.
```

This ladder is itself deterministic logic wrapping the LLM's confidence output — the
LLM proposes and scores, the ladder decides what happens with a low-confidence or
conflicted proposal. Consistent with keeping hard decision boundaries in code.

### Calibration plan (added to Day 11 — Proof phase)
0.85 is a starting point, to be tested, not defended as scientifically derived. Once
the synthetic batch and baseline exist:
1. Run the batch and log how many auto-executed actions *would* have needed
   escalation on closer inspection (unsafe-action rate).
2. Log how many cases were escalated or delayed that a human would judge as
   unnecessary (over-caution rate).
3. Adjust the threshold to balance these two, and **document the before/after
   numbers** — this is a genuinely strong thing to show a judge: not "we picked
   0.85," but "we picked 0.85, tested it, and here's what we found."

**Noted for later, not built now (to avoid adding complexity the decision explicitly
asked to avoid):** customer-facing actions (contact, escalation) arguably warrant a
stricter bar than purely internal actions (scheduling a retry with no outreach). A
single flat threshold is the right MVP choice; a split threshold is a reasonable
stretch-goal refinement during calibration, not a day-one requirement.

---

## 4. How This Maps Back to the PRD

- Section 2's tier logic refines **PRD FR-3.3** (Strategy shall account for a
  relationship-sensitivity flag).
- Section 3's policy refines **PRD FR-3.2** (confidence score) and **FR-7.1**
  (escalation on low confidence / conflicting signals).
- The solo audit in Section 1 confirms **PRD Section 11 (Assumptions & Constraints)**
  — solo build — was correctly scoped, not just assumed.

Nothing here changes the architecture, the case universe, the build plan, or the
overall confidence estimate already established. This addendum exists to make three
previously-flagged design gaps concrete and defensible.
