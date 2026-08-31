"""Run all Phase 1 tests and the generator + baseline."""
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("PHASE 1 VERIFICATION SCRIPT")
print("=" * 60)

# --- Run tests ---
print("\n--- Running compliance tests ---")
import unittest
loader = unittest.TestLoader()
suite = unittest.TestSuite()

from tests.test_compliance import *
from tests.test_relationship import *
from tests.test_baseline import *

suite = loader.loadTestsFromModule(sys.modules['tests.test_compliance'])
suite.addTests(loader.loadTestsFromModule(sys.modules['tests.test_relationship']))
suite.addTests(loader.loadTestsFromModule(sys.modules['tests.test_baseline']))

runner = unittest.TextTestRunner(verbosity=2)
result = runner.run(suite)

# --- Generate synthetic data ---
print("\n" + "=" * 60)
print("GENERATING SYNTHETIC DATA")
print("=" * 60)

from data.generator import generate_full_batch, save_batch
batch = generate_full_batch()
paths = save_batch(batch)

# --- Run baseline ---
print("\n" + "=" * 60)
print("RUNNING NAIVE BASELINE")
print("=" * 60)

from baseline.baseline import run_baseline_batch, print_baseline_report

pay_report = run_baseline_batch(batch["failed_payments"], "Failed Payments (Baseline)")
print_baseline_report(pay_report)

b2b_report = run_baseline_batch(batch["b2b_receivables"], "B2B Receivables (Baseline)")
print_baseline_report(b2b_report)

# --- Verify audit log ---
print("\n" + "=" * 60)
print("VERIFYING AUDIT LOG STRUCTURE")
print("=" * 60)

from core.audit_log import AuditLog, ExecutionEntry
audit = AuditLog()
audit.init_case_trail("TEST-001", "FAILED_PAYMENT")
audit.record_execution(ExecutionEntry(
    case_id="TEST-001",
    idempotency_key="TEST-001_1",
    timestamp="2026-08-23T10:00:00",
    action="RETRY_NOW",
    status="SUCCESS",
    result_detail="Simulated success",
))
trail = audit.get_case_trail("TEST-001")
assert trail is not None
assert len(trail.executions) == 1
assert audit.get_execution_log()["TEST-001_1"]["status"] == "SUCCESS"
print("✓ Audit log structure verified")
print("✓ Idempotency log working")

# Save and reload
audit.save()
audit2 = AuditLog()
audit2.load()
assert audit2.get_execution_log()["TEST-001_1"]["status"] == "SUCCESS"
print("✓ Audit log persistence (save/load) verified")

# Clean up test audit log
os.remove(audit.path)
print("✓ Test audit log cleaned up")

# --- Summary ---
print("\n" + "=" * 60)
print("PHASE 1 VERIFICATION COMPLETE")
print("=" * 60)
tests_passed = result.testsRun - len(result.failures) - len(result.errors)
print(f"Tests: {tests_passed}/{result.testsRun} passed")
if result.failures:
    print(f"FAILURES: {len(result.failures)}")
    for f in result.failures:
        print(f"  {f[0]}: {f[1][:100]}")
if result.errors:
    print(f"ERRORS: {len(result.errors)}")
    for e in result.errors:
        print(f"  {e[0]}: {e[1][:100]}")
print(f"Baseline payment recovery rate: {pay_report.recovery_rate_pct}%")
print(f"Baseline B2B recovery rate: {b2b_report.recovery_rate_pct}%")
print(f"Baseline payment avg resolution time: {pay_report.avg_resolution_time} hours")
print(f"Baseline B2B avg resolution time: {b2b_report.avg_resolution_time} days")
print(f"Baseline payment violations: {pay_report.total_compliance_violations}")
print(f"Baseline B2B violations: {b2b_report.total_compliance_violations}")
