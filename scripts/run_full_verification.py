"""
scripts/run_full_verification.py — Master Backend Verification Runner.

Convenience runner that executes the three core backend verification steps in sequence:
1. Out-of-distribution robustness test suite (tests.test_out_of_distribution)
2. Full automated test suite (unittest discover tests)
3. Baseline drift assertion check (scripts.assert_baseline)

Prints clear section headers between each step and streams terminal output directly.
"""

from __future__ import annotations

import subprocess
import sys


def run_step(header: str, cmd: list[str]) -> int:
    print(f"\n{header}")
    print(f"Command: {' '.join(cmd)}\n")
    sys.stdout.flush()
    result = subprocess.run(cmd)
    return result.returncode


def main() -> None:
    print("=" * 70)
    print("SENTINEL FULL BACKEND VERIFICATION RUNNER")
    print("=" * 70)

    # Step 1: Out-of-Distribution Robustness Tests
    ret_ood = run_step(
        "=== OUT-OF-DISTRIBUTION TESTS ===",
        [sys.executable, "-m", "unittest", "tests.test_out_of_distribution", "-v"],
    )

    # Step 2: Full Test Suite
    ret_full = run_step(
        "=== FULL TEST SUITE ===",
        [sys.executable, "-m", "unittest", "discover", "tests", "-v"],
    )

    # Step 3: Baseline Drift Check
    ret_base = run_step(
        "=== BASELINE DRIFT CHECK ===",
        [sys.executable, "scripts/assert_baseline.py"],
    )

    print("\n" + "=" * 70)
    print("VERIFICATION SUMMARY")
    print("=" * 70)
    print(f"1. Out-of-Distribution Tests : {'PASS' if ret_ood == 0 else 'FINDINGS REPORTED (Exit code ' + str(ret_ood) + ')'}")
    print(f"2. Full Test Suite           : {'PASS' if ret_full == 0 else 'FAIL (Exit code ' + str(ret_full) + ')'}")
    print(f"3. Baseline Drift Check      : {'PASS' if ret_base == 0 else 'FAIL (Exit code ' + str(ret_base) + ')'}")
    print("=" * 70)

    # Exit with non-zero if any step had an issue, while ensuring all 3 ran
    if ret_ood != 0 or ret_full != 0 or ret_base != 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
