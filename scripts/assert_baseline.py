"""
scripts/assert_baseline.py — Baseline Recovery Rate Assertion

Reads the CSV reports produced by run_phase2.py and run_phase3.py and
confirms recovery rates and compliance violations match expected constants.

Compares raw integer fractions (cases_recovered / total_cases), NOT display
strings, so the tolerance band is real slack on the actual float.

Exit code: 0 if all assertions pass, 1 if any drift is detected.
Usage: python scripts/assert_baseline.py
"""

import csv
import sys
from pathlib import Path

# Expected baseline constants (raw fractions from mock benchmark runs)
# Payment: 11/30 = 0.36666...
# B2B: 13/50 = 0.26
EXPECTED_PAYMENT_RECOVERY_RATE = 11 / 30
EXPECTED_B2B_RECOVERY_RATE = 13 / 50
EXPECTED_PAYMENT_VIOLATIONS = 0
EXPECTED_B2B_VIOLATIONS = 0
TOLERANCE = 0.001  # ±0.1% real float tolerance


def _load_csv(path: Path) -> list[dict]:
    if not path.exists():
        print(f"ERROR: Report file not found: {path}")
        print("Run run_phase2.py and run_phase3.py first to generate reports.")
        sys.exit(1)
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _count(rows: list[dict], field: str, value: str) -> int:
    return sum(1 for r in rows if r.get(field, "").strip() == value)


def assert_baseline() -> None:
    base = Path(__file__).parent.parent / "reports"

    failures = []

    # --- Payment batch ---
    payment_rows = _load_csv(base / "payment_batch_breakdown.csv")
    total_payment = len(payment_rows)
    recovered_payment = _count(payment_rows, "status", "RECOVERED")
    violations_payment = sum(int(r.get("compliance_violations", 0)) for r in payment_rows)

    if total_payment == 0:
        failures.append("Payment report has 0 rows — cannot compute recovery rate.")
    else:
        actual_payment_rate = recovered_payment / total_payment
        if abs(actual_payment_rate - EXPECTED_PAYMENT_RECOVERY_RATE) > TOLERANCE:
            failures.append(
                f"Payment recovery rate DRIFTED: got {actual_payment_rate:.4f} "
                f"({recovered_payment}/{total_payment}), "
                f"expected {EXPECTED_PAYMENT_RECOVERY_RATE:.4f} ({11}/{30}) "
                f"± {TOLERANCE}"
            )

    if violations_payment != EXPECTED_PAYMENT_VIOLATIONS:
        failures.append(
            f"Payment compliance violations DRIFTED: got {violations_payment}, "
            f"expected {EXPECTED_PAYMENT_VIOLATIONS}"
        )

    # --- B2B batch ---
    b2b_rows = _load_csv(base / "b2b_batch_breakdown.csv")
    total_b2b = len(b2b_rows)
    recovered_b2b = _count(b2b_rows, "status", "RECOVERED")
    violations_b2b = sum(int(r.get("compliance_violations", 0)) for r in b2b_rows)

    if total_b2b == 0:
        failures.append("B2B report has 0 rows — cannot compute recovery rate.")
    else:
        actual_b2b_rate = recovered_b2b / total_b2b
        if abs(actual_b2b_rate - EXPECTED_B2B_RECOVERY_RATE) > TOLERANCE:
            failures.append(
                f"B2B recovery rate DRIFTED: got {actual_b2b_rate:.4f} "
                f"({recovered_b2b}/{total_b2b}), "
                f"expected {EXPECTED_B2B_RECOVERY_RATE:.4f} ({13}/{50}) "
                f"± {TOLERANCE}"
            )

    if violations_b2b != EXPECTED_B2B_VIOLATIONS:
        failures.append(
            f"B2B compliance violations DRIFTED: got {violations_b2b}, "
            f"expected {EXPECTED_B2B_VIOLATIONS}"
        )

    # --- Report ---
    if failures:
        print("BASELINE ASSERTION FAILED — benchmark regression detected:\n")
        for f in failures:
            print(f"  ✗ {f}")
        sys.exit(1)
    else:
        print(
            f"Baseline OK: Payment {recovered_payment}/{total_payment} "
            f"({recovered_payment/total_payment:.4f}), "
            f"B2B {recovered_b2b}/{total_b2b} ({recovered_b2b/total_b2b:.4f}), "
            f"violations payment={violations_payment} b2b={violations_b2b}"
        )


if __name__ == "__main__":
    assert_baseline()
