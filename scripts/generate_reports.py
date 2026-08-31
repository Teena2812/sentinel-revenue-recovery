"""
Script to run batch benchmarks and generate actual CSV reports directly to disk.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from agents.llm_client import MockLLMClient
from core import config
from core.audit_log import AuditLog
from core.memory import Memory
from core.orchestrator import (
    export_breakdown_csv,
    process_b2b_batch,
    process_payment_batch,
)
from core.schemas import dict_to_b2b_case, dict_to_failed_payment


def run():
    # 1. Payment batch
    pay_path = os.path.join("data", "failed_payments.json")
    with open(pay_path, "r", encoding="utf-8") as f:
        pay_data = json.load(f)
    payment_cases = [dict_to_failed_payment(c) for c in pay_data["cases"]]

    audit_pay = AuditLog("data/agent_audit_log_run.json")
    mem_pay = Memory("data/agent_memory_run.json")
    mem_pay.clear()

    pay_report = process_payment_batch(
        payment_cases,
        audit_pay,
        mem_pay,
        llm_client=MockLLMClient(),
        current_time=config.SIMULATED_CURRENT_TIME,
    )

    pay_csv = "reports/payment_batch_breakdown.csv"
    export_breakdown_csv(pay_report, pay_csv)
    print(f"Generated {pay_csv} with {len(pay_report.individual_outcomes)} cases.")

    # 2. B2B batch
    b2b_path = os.path.join("data", "b2b_receivables.json")
    with open(b2b_path, "r", encoding="utf-8") as f:
        b2b_data = json.load(f)
    b2b_cases = [dict_to_b2b_case(c) for c in b2b_data["cases"]]

    audit_b2b = AuditLog("data/b2b_audit_log_run.json")
    mem_b2b = Memory("data/b2b_memory_run.json")
    mem_b2b.clear()

    b2b_report = process_b2b_batch(
        b2b_cases,
        audit_b2b,
        mem_b2b,
        llm_client=MockLLMClient(),
        current_time=config.SIMULATED_CURRENT_TIME,
    )

    b2b_csv = "reports/b2b_batch_breakdown.csv"
    export_breakdown_csv(b2b_report, b2b_csv)
    print(f"Generated {b2b_csv} with {len(b2b_report.individual_outcomes)} cases.")

    # Cleanup temp
    if os.path.exists("data/agent_audit_log_run.json"):
        os.remove("data/agent_audit_log_run.json")
    if os.path.exists("data/b2b_audit_log_run.json"):
        os.remove("data/b2b_audit_log_run.json")
    mem_pay.clear()
    mem_b2b.clear()


if __name__ == "__main__":
    run()
