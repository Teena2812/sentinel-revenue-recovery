# Sentinel — Implementation plan (prompt-by-prompt)

Each block below is a **self-contained prompt** you can paste directly into Antigravity (or any coding agent) against your existing Sentinel repo. Run them **in order** — later prompts assume earlier ones are done. Each has a goal, estimated time, the exact instruction, and an acceptance check so you know when it's actually finished, not just attempted.

**Do not skip Prompt 0.** Everything after it depends on knowing what's actually true about your current code, not what was assumed in planning conversations.

---

## Prompt 0 — Codebase audit — DONE, findings below (2026 audit)

**Verdict, confirmed with file/line references:**
1. **Shared pipeline: TRUE.** `orchestrator.process_case()` (`core/orchestrator.py:120–224`), `diagnosis.diagnose()` (`agents/diagnosis.py:131–180`), `strategy.propose_strategy()` (`agents/strategy.py:185–234`), `compliance.run_all_checks()` (`core/compliance.py:361–425`), `execution.execute()` (`agents/execution.py:108–217`) are single, shared functions for both case types. Domain differences are confined to prompt-building and action-menu selection, not duplicated core logic.
2. **Hard-rule isolation: TRUE and clean.** All checks live in `core/compliance.py` — `check_fraud_stop()` (40–65), `check_dispute_stop()` (70–98), `check_attempt_cap()` (103–145), `check_contact_hours()` (150–185), `check_idempotency()` (302–339), `should_skip_pipeline()` (240–285). The gate consumes only the LLM's `proposed_action` enum — never confidence, never generated text.
3. **Model:** `"gemini-flash-lite-latest"`, defined `core/config.py:92`, called via `model.generate_content(prompt)` at `agents/llm_client.py:484`.
4. **Batch size: already tracked.** N=30 payment-failure cases (`data/failed_payments.json`), N=50 B2B receivables (`data/b2b_receivables.json`) — 80 total. Printed in `orchestrator.py:562` and `baseline.py:312`.
5. **Reproducibility mechanism — important, read carefully:** the benchmark uses `MockLLMClient` (instantiated `run_phase2.py:81`, `run_phase3.py:86`), a seeded RNG (`random.Random(42)`), and frozen simulated time (`config.py:79`). **Execution outcomes are also simulated** — drawn from `config.PAYMENT_RETRY_SUCCESS_PROB` / `config.B2B_REMINDER_SUCCESS_PROB` via the seeded RNG, not from any model or real-world signal. Live Gemini is only invoked in `live_gemini_proof.py` and manually via `interactive.py`.
6. **External APIs: only Gemini** (`agents/llm_client.py:471–488`, endpoint `generativelanguage.googleapis.com`). Zero external calls for payment/collection rails — those outcomes are simulated locally.
7. **Tests: 116/116 passing** across `test_compliance.py` (30), `test_relationship.py` (25), `test_baseline.py` (25), `test_core_loop.py` (21), `test_b2b_loop.py` (15).

**Open question, only you can answer, decide before writing the README:** does `MockLLMClient` approximate realistic diagnosis/decision logic, or does it return fixed/canned responses per category? This determines whether the benchmark numbers say "our architecture handles realistic AI decisions correctly" or, more narrowly, "our compliance-gate and policy logic is correct given some model output" — both are legitimate claims, but the README must state the true one, not the more impressive-sounding one. Check `MockLLMClient`'s implementation before Prompt 1.

**New recommended addition (do this — high value, low cost):** `gemini-flash-lite-latest` is cheap and fast. Run the **full 80-case batch against the live API once**, labeled clearly as a non-reproducible-by-nature live validation run, separate from the seeded mock benchmark. This directly resolves the open question above with real evidence instead of a caveat, and turns your biggest honesty risk into your strongest credibility asset — you'd have both a reproducible mock benchmark and a real live-model run over the same 80 cases to compare. Add this as **Prompt 1.5** below.

---

## Prompt 1 — Separate benchmark-mode from live-API claims, precisely (~20–30 min, shorter now — the code paths already exist and just need documenting)

**Goal:** Fix Gap #1 — using the exact mechanism confirmed by the audit, not a guess.

**Paste this:**
> Add a README section titled "How the numbers were produced" that states, precisely:
> - The headline recovery-rate benchmark (N=30 payment-failure, N=50 B2B receivable cases) runs entirely through `MockLLMClient` (`run_phase2.py:81`, `run_phase3.py:86`), with a seeded RNG (`random.Random(42)`) and frozen simulated time (`config.py:79`). Execution outcomes are drawn from `config.PAYMENT_RETRY_SUCCESS_PROB` and `config.B2B_REMINDER_SUCCESS_PROB` — configured probabilities, not real gateway or collection responses. This is what makes the benchmark byte-identical across repeated runs.
> - State explicitly whether `MockLLMClient` approximates realistic model reasoning or returns fixed responses per category — describe what it actually does in one or two sentences, so a reviewer knows exactly what's being tested.
> - The live, unstaged Gemini API integration (`live_gemini_proof.py`, and manually via `interactive.py`) is a separate, non-reproducible-by-nature demonstration of resilience against the real model, including real rate-limit failures and correct escalation.
> Do not let these two numbers sit near each other in the README without this distinction stated first.

**Acceptance check:** A reviewer reading only this section can correctly state, in their own words, what produced every number in the results section, and what `MockLLMClient` actually does.

---

## Prompt 1.5 — NEW: run the full batch against the live API once (~1–2 hrs incl. cost/rate-limit handling)

**Goal:** Convert the mock-benchmark honesty risk into a strength by having real numbers to show alongside it.

**Paste this:**
> Add a new script (e.g. `run_live_validation.py`) that runs the same 80 cases (`data/failed_payments.json` + `data/b2b_receivables.json`) through the real pipeline using the live Gemini client instead of `MockLLMClient`, reusing the existing orchestrator/diagnosis/strategy/compliance/execution functions unchanged. Add basic retry/backoff for rate limits (reuse the existing backoff logic already present in `llm_client.py`, referenced near line 503). Log results in the same report format as the mock benchmark, but clearly labeled "LIVE MODEL VALIDATION RUN — not reproducible, run on [date]." Print a side-by-side comparison: mock-benchmark recovery rate vs. live-run recovery rate, and how many cases the live model diagnosed/decided differently from the mock client's response for the same case.
> Note: since execution outcomes are still simulated via probability tables (no real gateway exists to hit), this run validates that the live model's diagnosis/decision quality holds up at scale and integrates correctly — it does not create new "real" recovery numbers, and the README must say so.

**Acceptance check:** You have a second results file, clearly and differently labeled from the mock benchmark, showing the live model actually ran across all 80 cases at least once, with a stated count of how many decisions matched vs. diverged from the mock client.

---

## Prompt 2 — Surface batch size (N) in the pitch (~10 min — already tracked, just needs promoting)

**Goal:** Fix Gap #2. The audit confirms N is already tracked and printed (`orchestrator.py:562`, `baseline.py:312`) — this is now a promotion task, not a build task.

**Paste this:**
> Update the README's results section header and the one-line pitch to state explicitly: "Tested across N=30 payment-failure cases and N=50 B2B receivable cases (80 total)." Pull this from the existing `print_agent_batch_report()` / `print_baseline_report()` output — do not hardcode a separate number, reference the same source of truth so it can't drift out of sync if the datasets change.

**Acceptance check:** The number 80 (or its N=30/N=50 breakdown) appears in the first paragraph of the README, not just in a results table further down.

---

## Prompt 3 — Plain HTML audit-log viewer (~1–1.5 hrs)

**Goal:** Fix Gap #3 — give the audit trail a visual form for the video.

**Paste this:**
> Build a single static HTML page (no framework needed — plain HTML/CSS/vanilla JS is fine) that renders the case audit log as a readable table with these columns: case ID, case type, diagnosis, proposed action, confidence, hard-rule verdict (approved/blocked + reason if blocked), final outcome, timestamp. Read from the existing audit log output (JSON/CSV/whatever format it's already in) — do not change the underlying logging format, just add a read-only viewer. Include a simple filter/search box for case ID and a color indicator (e.g. green/red) for approved vs. blocked cases, using accessible colors that work without relying on color alone (add a text badge too). Keep it to one file, no build step, opens directly in a browser.

**Acceptance check:** Opening the HTML file directly (no server needed) shows real case data from your actual log file, and you can visually spot at least one blocked/escalated case within 5 seconds of scanning it.

---

## Prompt 4 — Fair Practices Code citation check (~20–30 min, mostly research not code)

**Goal:** Fix Gap #4.

**Do this yourself, not via coding agent:** Search for the actual RBI Fair Practices Code text (or the relevant NBFC/collections-conduct circular) covering permitted contact hours for collections/recovery communication. Compare it against whatever specific hours/rule your `check_rules` logic currently encodes.

**Then paste this to the coding agent:**
> Update the comment/docstring above the contact-hours rule in the hard-rule check to cite the specific source precisely: either the exact circular/section if it matches what was verified, or softened language ("modeled on RBI Fair Practices Code principles") if it's an approximation. Update the same language anywhere it's repeated in the README.

**Acceptance check:** The claim in your README matches, word for precision, what you actually verified — no more, no less.

---

## Prompt 5 — Make the "learning" boundary explicit (~20 min)

**Goal:** Fix Gap #5.

**Paste this:**
> Add a short, clearly titled section to the README called "What 'learning' means in this system" stating plainly: learning = updating a per-case-category win-rate lookup table from observed outcomes; it does not retrain the model or change any weights; introducing a genuinely new case category requires a human to add it explicitly. Add an inline comment at the exact point in the code where the win-rate table is updated, restating this in one line, so it's visible to anyone reading the code, not just the README.

**Acceptance check:** Someone reading only the code (not the README) can find, within the stats-update function, a comment stating what "learning" does and doesn't do.

---

## Prompt 6 — Self-consistency check on diagnosis (~1 hr)

**Goal:** Score-booster #1 — extend Sentinel's own stated design principle (identical questions shouldn't get inconsistent answers) into the diagnosis step.

**Paste this:**
> Modify the diagnosis step so it calls the diagnosis prompt 2–3 times per case (using either a temperature variation or light prompt paraphrasing — pick whichever fits the existing prompt structure with least disruption). Compare the resulting diagnoses for semantic agreement (exact match if outputs are already categorical, or a simple similarity check if outputs are free text). If the calls agree, proceed with the diagnosis as before. If they disagree, mark the case as low-confidence and route it through the same low-confidence path used elsewhere (see Prompt 7) rather than picking one arbitrarily. Log which cases triggered a disagreement, and how many, for reporting.

**Acceptance check:** Run the benchmark and confirm the log shows at least a few cases where self-consistency disagreed (if zero ever disagree, either the check isn't wired correctly or your synthetic data is too easy — investigate before moving on).

---

## Prompt 7 — Confidence-gated auto-escalation (~30–45 min)

**Goal:** Score-booster #2 — make the decide-agent's confidence score load-bearing, not decorative.

**Paste this:**
> In the hard-rule check layer, add a new rule: if the decide-agent's confidence score for the proposed action is below a threshold (pick a reasonable starting value, e.g. 0.6, and make it a named, documented constant, not a magic number), the action is automatically blocked and routed to escalation — regardless of what the proposed action was. Log the reason as "low confidence" distinctly from other block reasons (fraud flag, dispute flag, retry limit, etc.) so it's reportable separately in the results summary.

**Acceptance check:** The results summary can report "N cases escalated due to low confidence" as a distinct count from other escalation reasons.

---

## Prompt 8 — Document the shared adapter interface (~15–20 min — confirmed already real, this is now a documentation task, not a refactor)

**Goal:** Score-booster #3. The audit confirms this is already true: `orchestrator.process_case()`, `diagnosis.diagnose()`, `strategy.propose_strategy()`, `compliance.run_all_checks()`, `execution.execute()` are genuinely shared across both verticals. No refactor needed.

**Paste this:**
> Add a short "Architecture" section to the README naming the five shared functions explicitly (`process_case()` in `core/orchestrator.py`, `diagnose()` in `agents/diagnosis.py`, `propose_strategy()` in `agents/strategy.py`, `run_all_checks()` in `core/compliance.py`, `execute()` in `agents/execution.py`) and stating plainly that both `FAILED_PAYMENT` and `B2B_RECEIVABLE` case types run through this identical pipeline — domain differences are confined to prompt construction and action-menu selection (e.g. `strategy.py:193`), not duplicated core logic. State this is what proves the architecture generalizes across leak-point types, directly answering "could this extend to checkout abandonment or mandate retries?" before a reviewer has to ask.

**Acceptance check:** The README names the exact five functions and file locations — a reviewer can open the code and verify the claim in under a minute.

---

## Prompt 9 — Confidence calibration check (~1 hr)

**Goal:** Score-booster #4 — prove confidence is genuinely predictive.

**Paste this:**
> Write a small analysis script that, using the benchmark results, buckets all cases by their stated confidence score (e.g. 0–0.2, 0.2–0.4, ... 0.8–1.0) and computes the actual success rate of the taken action within each bucket. Output this as a simple table (and if easy, a basic bar chart image) showing bucket vs. actual success rate. Save the output to a file so it can be referenced in the README and shown in the video. Add one sentence to the README interpreting the result honestly — if higher-confidence buckets don't actually have higher success rates, say so plainly rather than omitting the finding.

**Acceptance check:** You have a concrete table/chart file you can screenshot, and the README states the actual finding, whatever it is — including if it's not flattering.

---

## Prompt 10 — Induced-failure test cases (~2–3 hrs)

**Goal:** Strengthen "failure recovery" — the buildathon's own stated evaluation criterion (what broke, what you did about it).

**Paste this:**
> Add a dedicated test file (e.g. `test_failure_modes.py`) that deliberately induces and asserts correct handling of at least these four failure scenarios, beyond the existing rate-limit case:
> 1. The decide-agent's LLM call returns an action string that is NOT in the fixed allowed-action menu — assert the system rejects it safely (does not execute an unrecognized action) and routes to escalation, logging why.
> 2. The LLM API returns a malformed/truncated/unparseable response — assert the system catches this without crashing and escalates rather than guessing.
> 3. A simulated timeout on the API call — assert correct timeout handling and escalation, not a hang or silent failure.
> 4. Two near-simultaneous processing attempts on the same case ID (simulate a race condition) — assert the idempotency mechanism prevents the action from firing twice.
> For each test, add a one-paragraph writeup to a new README section titled "Failure modes — what breaks and what we do about it," describing the scenario, what happens, and why that behavior is correct. This section should read clearly to a non-engineer reviewer, not just pass as a test.

**Acceptance check:** All four new tests pass, and the README section reads as a genuine "here's what we broke on purpose and how the system handled it" narrative — this is likely to become part of your video script.

---

## Prompt 11 — Build quality: CI, schema validation, rules-as-data (~3–4 hrs)

**Goal:** Strengthen "build quality" — does it run, is it structured, would you trust it.

**Paste this, in three parts:**
> Part A — CI: Add a GitHub Actions workflow that runs the full test suite on every push and pull request. Confirm it passes on a clean checkout, not just locally.
>
> Part B — Schema validation: Add input validation (e.g. using Pydantic or an equivalent) for every case object entering the pipeline, so a malformed case fails loudly and safely at the entry point rather than causing unclear errors deeper in the system. Add a test confirming a deliberately malformed case is rejected with a clear error, not a silent failure or crash.
>
> Part C — Rules as data: If the hard-rule logic is currently hardcoded as if/else branches, refactor the rule definitions (retry limits, contact-hour windows, confidence threshold, fraud/dispute flags) into a single external config file (JSON/YAML) that a non-engineer could read and audit, with the rule-check code reading from that config rather than embedding the values directly. Add a comment explaining this design choice — a config-driven rule set is easier to audit and change without touching logic code, which matters for a system enforcing compliance rules.
>
> Finally: update the README's setup instructions and verify, by literally following them on a clean checkout, that `git clone` to a working result takes under 2 minutes. Fix anything that makes this slower or unclear.

**Acceptance check:** CI badge passes on GitHub. A malformed case test exists and passes. Rule values are readable in a config file, not scattered through code. You've personally timed a fresh clone-to-run and it's under 2 minutes.

---

## Prompt 12 — "Where we chose not to use AI" section (~30 min, mostly writing not code)

**Goal:** Make Sentinel's strongest design decision explicit rather than only inferable.

**Paste this:**
> Add a README section titled "Where we chose not to use AI, and why" listing each place a deterministic/non-AI approach was used instead of an LLM call: the hard-rule check, the idempotency mechanism, the win-rate statistics tracker. For each, write one or two sentences on why determinism was the correct choice there (e.g. "retry-limit enforcement must behave identically every time for the same inputs — an LLM's non-determinism is a liability here, not a feature"). Cross-reference this section from the architecture section so a reviewer skimming the README can't miss it.

**Acceptance check:** This section exists as a standalone, easy-to-find block — not scattered as asides elsewhere in the README.

---

## Prompt 13 — Mandate/subscription vertical (CONDITIONAL — only if Prompts 1–12 are done with time to spare, ~2–4 hrs)

**Goal:** Score-booster — a third vertical proving the shared-interface architecture generalizes further, using regulatory research already verified.

**Paste this:**
> Implement a third case-type adapter, `mandate_failure`, using the same shared interface from Prompt 8. Encode these hard rules, sourced from the RBI Digital Payments E-Mandate Framework, 2026: a debit above ₹15,000 (or ₹1,00,000 for insurance/mutual-fund/credit-card-bill categories) requires fresh OTP authentication and cannot be silently retried without it; a pre-debit notification must have been sent at least 24 hours before any retry attempt, or the retry is blocked; if the customer used the mandate's opt-out link for that specific transaction, the case is a hard stop, no retry; if the mandate itself has been paused or cancelled, hard stop. Build a synthetic dataset covering both normal cases and every hard-stop case listed above. Add this vertical's results to the existing benchmark and results reporting, so the results table shows three verticals, not two.

**Acceptance check:** The mandate vertical uses the exact same four interface methods as the other two verticals (no new architecture invented for it), and its hard-stop cases are demonstrably present in the batch and correctly blocked.

---

## Prompt 14 — Grounded impact projection (~20 min, writing not code)

**Goal:** Strengthen "problem taste" with a concrete, honestly-labeled extrapolation.

**Do this yourself (writing, not a coding prompt):** Write one paragraph for the README's impact section that takes your actual batch recovery rate and applies it, illustratively, to a defensible slice of the ₹8.1 trillion MSME delayed-payments figure (Economic Survey 2025-26). Label it clearly as illustrative math, not a real forecast — e.g. "if applied to even 1% of that figure, a X% compliant recovery rate implies roughly ₹Y illustratively recoverable, without the compliance risk of the naive baseline's approach." Do not overstate this as a real projection.

**Acceptance check:** The paragraph contains the word "illustrative" or equivalent, and shows the actual arithmetic so a reviewer can check it themselves.

---

## Prompt 15 — Video script (not a coding prompt — do this last, ~1 hr writing + recording time)

**Goal:** Turn everything above into a 5-minute video that a reviewer remembers.

**Structure to script, in order:**
1. **0:00–0:20** — the one-liner pitch, said exactly as written.
2. **0:20–1:00** — the problem, using the verified ₹8.1 trillion stat and the specific failure mode of naive dunning/collections (illegally retrying fraud cases, contacting disputed invoices).
3. **1:00–2:30** — architecture walkthrough using the diagram: diagnose → decide → hard-rule check (say explicitly: "this box is plain code, not AI, on purpose") → execute or escalate → audit log.
4. **2:30–3:30** — the one full hard-stop case, narrated end to end, on screen, using the Prompt 3 HTML viewer: a specific case that got blocked and escalated, showing the exact reason logged.
5. **3:30–4:15** — honest results: naive baseline recovers more raw money but generates compliance violations; Sentinel recovers less raw money with zero violations — say the exact numbers, including N.
6. **4:15–4:45** — the failure-recovery moment: the real Gemini rate-limit event, and how the system handled it without crashing or guessing.
7. **4:45–5:00** — what it deliberately doesn't do (from your original writeup), said with confidence, not apology.

**Acceptance check:** Record once, watch it back, check it's under 5:00, and that every claim made on camera matches something actually verifiable in the repo — no claim in the video should be undocumented in the README.

---

## Suggested order if time is tight

If you don't get through everything, this is the priority order by points-per-hour, based on the scoring discussion:

1. Prompt 0 (mandatory, cheap)
2. Prompts 1, 2, 5 (the cheap fixes)
3. Prompt 10 (failure recovery — named evaluation criterion)
4. Prompt 11 (build quality — named evaluation criterion)
5. Prompt 3 (visual audit log — needed for the video regardless)
6. Prompt 7, then Prompt 6 (confidence gating, then self-consistency)
7. Prompt 12 (cheap, high narrative value)
8. Prompt 15 (video — do this once everything above is real)
9. Prompt 8, Prompt 9 (architecture visibility, calibration)
10. Prompt 13, Prompt 14 (only with time to spare)
