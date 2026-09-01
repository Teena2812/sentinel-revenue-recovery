# Sentinel — Software Quality Self-Audit

> All findings are grounded in actual code. File and line references were verified
> against the live codebase at the time of this audit (2026-08-31).

---

## Summary Scores

| Dimension | Verdict | Primary Finding |
|-----------|---------|-----------------|
| Functional Correctness | ✅ Pass | Unknown `failure_code` silently gets `RETRY_NOW` in mock |
| Robustness | ⚠️ Partial | No per-case exception isolation; unguarded JSON load |
| Generalization | ⚠️ Partial | `MockLLMClient` is pattern-matched; cache hits look like live calls |
| Feasibility | ✅ Pass | Runs on any Python 3.10+ machine with free Gemini tier |
| Scalability | ⚠️ Known | Single-threaded; documented in ARCHITECTURE.md §8 |
| Reliability | ✅ Pass | Deterministic seed; contact-hours fallback is latent risk |
| Performance | ✅ Pass | Pre-pipeline skip; LLM caching; O(n≤20) memory scoring |
| Security | ⚠️ 1 finding | API key redaction in error logs (fixed: Phase 1) |
| Usability | ✅ Pass | `ModuleNotFoundError` from wrong directory for scripts/ |
| Maintainability | ✅ Pass | `MockLLMClient` has no single change point for new categories |
| Portability | ✅ Pass | Pure Python; `os.path.join` throughout; no OS dependencies |
| Testability | ✅ Pass | Zero coverage on deserialization with malformed input |

---

## 1. Functional Correctness ✅

- 116/116 tests passing (compliance, relationship tier, baseline, core loop, B2B loop).
- Benchmark numbers reproducible byte-for-byte across independent runs.
- **Weakness**: `dict_to_failed_payment` calls `FailureCode(d["failure_code"])` with no
  try/except (`core/schemas.py` L300). An unrecognized failure code raises `ValueError`
  and aborts the entire batch rather than isolating that one case.

---

## 2. Robustness & Error Handling ⚠️

### Handled correctly
- LLM API failures: both `diagnose()` and `propose_strategy()` retry once then fall back
  to safe defaults (`UNKNOWN` / `ESCALATE_HUMAN`).
- Gemini 429: parses retry delay from error string, sleeps, retries.
- Memory corruption: `Memory.load()` wraps `json.load()` in try/except; corrupt file →
  silent reset to `{}`.

### Not handled
1. **No per-case exception isolation** in `process_payment_batch` /
   `process_b2b_batch`. One bad case aborts the full batch.
2. **Unguarded enum construction** on JSON load (`FailureCode(d["failure_code"])`).
3. **Unguarded `json.load()`** in runner scripts — corrupt data file → bare traceback.
4. **`AuditLog` write failures** silently swallowed; causes audit data loss on
   read-only volumes.
5. **`calibration_check.py`** CSV read unguarded — wrong headers → opaque `KeyError`.

**Production fix for item 1:**
```python
try:
    outcome = process_case(case, ...)
except Exception as e:
    logger.error("Unhandled exception on case %s: %s", case.case_id, e)
    outcome = CaseOutcome(case_id=case.case_id, status="FAILED", ...)
```

---

## 3. Generalization ⚠️

- Compliance gate and LLM prompt builders are fully case-agnostic.
- **`MockLLMClient`** is pattern-matched to the known failure code / category space.
  New failure codes silently receive `RETRY_NOW`.
- **LLM cache**: `data/llm_cache.json` is tracked in git. If a live prompt ever
  contains PII (customer name, real account), it would be cached and committed.
  Not a problem with synthetic data.

---

## 4. Security ⚠️ (Fixed)

- No hardcoded secrets confirmed by full-tree grep (0 matches for key prefix).
- **Fixed in Phase 1**: `GeminiLLMClient.call()` now sanitizes `self.api_key` out of
  any exception string before logging or re-raising, using:
  ```python
  err_str = str(e).replace(_key, "[REDACTED]") if _key else str(e)
  ```

---

## 5. Testability ✅

- 116 tests across 5 dedicated test files.
- **Gap**: Zero tests for `dict_to_failed_payment` / `dict_to_b2b_receivable` with
  malformed input (missing fields, wrong types, unknown enum values). This is the
  most likely real-world failure path and has no coverage.
- `GeminiLLMClient` has no tests — the 429 backoff logic is entirely untested.

---

## Priority Fix List (for a production hardening pass)

| # | Issue | File | Fix Size |
|---|-------|------|----------|
| 1 | Per-case exception isolation | `core/orchestrator.py` | ~10 lines |
| 2 | Unguarded enum on JSON load | `core/schemas.py` L300 | ~5 lines |
| 3 | Unguarded `json.load()` in runners | `run_phase2.py`, `demo.py` | ~5 lines each |
| 4 | Tests for malformed deserialization | `tests/` | ~20 lines |
| 5 | `calibration_check.py` CSV guard | `calibration_check.py` | ~3 lines |
