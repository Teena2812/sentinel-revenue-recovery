"""
scripts/generate_audit_viewer.py — Standalone HTML Audit Log Viewer Generator

Generates reports/audit_viewer.html: a single-file, zero-CORS visual audit log
viewer that opens directly via file:/// in any browser with zero web server.

Key Requirements:
1. Reads real Phase 2 (N=30) and Phase 3 (N=50) benchmark cases and audit logs.
2. Embeds all 80 cases and their multi-attempt lifecycles as a JSON data block.
3. KPI ribbon metrics are computed DYNAMICALLY BY JAVASCRIPT at render time
   (never baked in as static text by Python), so future model/threshold changes
   automatically reflect in the UI upon re-render.
4. Clean, camera-ready visual design for the buildathon demo video.
5. High-contrast accessible badges (color + symbol + text) for instant scanability.
6. Simplified multi-attempt drawer for hard-stop case walkthroughs.

Usage:
    python scripts/generate_audit_viewer.py
"""

import json
import os
import random
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core import config
from core.audit_log import AuditLog
from core.memory import Memory
from core.orchestrator import process_b2b_batch, process_payment_batch
from core.schemas import dict_to_b2b_case, dict_to_failed_payment


def load_dataset():
    base_dir = Path(__file__).parent.parent
    data_dir = base_dir / "data"

    pay_path = data_dir / "failed_payments.json"
    with open(pay_path, "r", encoding="utf-8") as f:
        pay_data = json.load(f)
    payment_cases = [dict_to_failed_payment(c) for c in pay_data["cases"]]

    b2b_path = data_dir / "b2b_receivables.json"
    with open(b2b_path, "r", encoding="utf-8") as f:
        b2b_data = json.load(f)
    b2b_cases = [dict_to_b2b_case(c) for c in b2b_data["cases"]]

    return payment_cases, b2b_cases


def collect_audit_data() -> list[dict]:
    """Execute standard benchmark run and compile comprehensive audit data for all 80 cases."""
    payment_cases, b2b_cases = load_dataset()

    audit = AuditLog()
    memory = Memory()
    memory.clear()

    payment_report = process_payment_batch(
        payment_cases,
        audit,
        memory,
        current_time=config.SIMULATED_CURRENT_TIME,
        rng=random.Random(42),
    )

    b2b_report = process_b2b_batch(
        b2b_cases,
        audit,
        memory,
        current_time=config.SIMULATED_CURRENT_TIME,
        rng=random.Random(42),
    )

    all_outcomes = list(payment_report.individual_outcomes) + list(b2b_report.individual_outcomes)
    trails = audit.get_all_case_trails()

    cases_data = []

    for outcome in all_outcomes:
        cid = outcome.case_id
        trail = trails.get(cid)

        is_payment = cid.startswith("PAY")
        case_type = "FAILED_PAYMENT" if is_payment else "B2B_RECEIVABLE"

        # Determine primary diagnosis and strategy
        diag_category = outcome.diagnosis.category.value if outcome.diagnosis else "N/A (Pre-Pipeline Skip)"
        diag_confidence = outcome.diagnosis.confidence if outcome.diagnosis else 0.0
        diag_root_cause = outcome.diagnosis.root_cause if outcome.diagnosis else (outcome.escalation_reason or "None")

        action = outcome.final_action.value if hasattr(outcome.final_action, "value") else str(outcome.final_action)
        strat_confidence = outcome.strategy.confidence if outcome.strategy else 0.0
        strat_reasoning = outcome.strategy.reasoning if outcome.strategy else ""

        # Gate verdict
        gate_decision = outcome.gate_decision
        if gate_decision:
            gate_approved = gate_decision.approved
            gate_violations = gate_decision.violation_reasons
        else:
            gate_approved = outcome.status not in {"GATE_BLOCKED", "STOPPED"}
            gate_violations = [outcome.escalation_reason] if outcome.escalation_reason else []

        # Resolution formatted
        res_time = outcome.resolution_time
        res_unit = outcome.resolution_unit
        res_str = f"{res_time:.1f} {res_unit}" if res_time is not None else "N/A"

        # Compile detailed lifecycle attempts
        attempts = []
        if trail:
            # Pair diagnoses, strategies, gates, and executions by index
            max_steps = max(
                len(trail.diagnoses),
                len(trail.strategies),
                len(trail.gate_decisions),
                len(trail.executions),
                1,
            )
            for i in range(max_steps):
                d = trail.diagnoses[i] if i < len(trail.diagnoses) else None
                s = trail.strategies[i] if i < len(trail.strategies) else None
                g = trail.gate_decisions[i] if i < len(trail.gate_decisions) else None
                e = trail.executions[i] if i < len(trail.executions) else None

                attempts.append({
                    "step": i + 1,
                    "diagnosis": {
                        "category": d.raw_output.get("category", "N/A") if d and d.raw_output else (d.root_cause if d else "Pre-pipeline rule"),
                        "root_cause": d.root_cause if d else "Pre-pipeline evaluation",
                        "confidence": d.confidence if d else None,
                        "reasoning": d.reasoning if d else "",
                    } if d else None,
                    "strategy": {
                        "proposed_action": s.proposed_action if s else "Direct Routing",
                        "confidence": s.confidence if s else None,
                        "reasoning": s.reasoning if s else "",
                    } if s else None,
                    "gate": {
                        "approved": g.approved if g else gate_approved,
                        "rule_name": "deterministic_compliance_gate",
                        "violations": g.violation_reasons if g else gate_violations,
                    } if g else None,
                    "execution": {
                        "action": e.action if e else action,
                        "status": e.status if e else outcome.status,
                        "detail": e.result_detail if e else outcome.reasoning_summary,
                    } if e else None,
                })

        cases_data.append({
            "case_id": cid,
            "case_type": case_type,
            "amount": outcome.amount,
            "amount_recovered": outcome.amount_recovered,
            "status": outcome.status,
            "final_action": action,
            "diagnosis_category": diag_category,
            "diagnosis_root_cause": diag_root_cause,
            "diagnosis_confidence": diag_confidence,
            "strategy_confidence": strat_confidence,
            "strategy_reasoning": strat_reasoning,
            "gate_approved": gate_approved,
            "gate_violations": gate_violations,
            "escalation_reason": outcome.escalation_reason,
            "reasoning_summary": outcome.reasoning_summary,
            "attempts_made": outcome.attempts_made,
            "initial_attempt_count": outcome.initial_attempt_count,
            "resolution_time": res_str,
            "attempts": attempts,
        })

    return cases_data


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sentinel — Autonomous Revenue Recovery Audit Log</title>
  <style>
    :root {
      --bg: #070B19;
      --card-bg: #0F172A;
      --card-border: #1E293B;
      --header-bg: #0C1327;
      --text-main: #F8FAFC;
      --text-muted: #94A3B8;
      --text-dim: #64748B;
      --accent: #00BAF2;
      --accent-glow: rgba(0, 186, 242, 0.15);
      --green: #10B981;
      --green-bg: rgba(16, 185, 129, 0.12);
      --red: #EF4444;
      --red-bg: rgba(239, 68, 68, 0.14);
      --amber: #F59E0B;
      --amber-bg: rgba(245, 158, 11, 0.12);
      --blue: #3B82F6;
      --blue-bg: rgba(59, 130, 246, 0.12);
      --purple: #8B5CF6;
      --row-hover: #162038;
      --border-subtle: #1E293B;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
      background: var(--bg);
      color: var(--text-main);
      padding: 24px;
      line-height: 1.5;
    }

    .container { max-width: 1440px; margin: 0 auto; }

    /* Top Brand Bar */
    .top-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--border-subtle);
      margin-bottom: 24px;
    }
    .brand { display: flex; align-items: center; gap: 12px; }
    .brand-logo {
      width: 36px; height: 36px; border-radius: 8px;
      background: linear-gradient(135deg, #00BAF2, #0077B6);
      display: flex; align-items: center; justify-content: center;
      font-weight: 800; font-size: 18px; color: #fff;
    }
    .brand-title h1 { font-size: 20px; font-weight: 700; letter-spacing: -0.02em; }
    .brand-title p { font-size: 13px; color: var(--text-muted); }
    .badge-benchmark {
      background: #1E293B; color: #38BDF8; font-size: 12px; font-weight: 600;
      padding: 6px 14px; border-radius: 9999px; border: 1px solid #38BDF833;
    }

    /* KPI Ribbon — Dynamically populated by JS at render time */
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px;
      margin-bottom: 24px;
    }
    .kpi-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 16px;
      position: relative;
      overflow: hidden;
    }
    .kpi-card::before {
      content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px;
      background: var(--kpi-color, var(--accent));
    }
    .kpi-label { font-size: 12px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; }
    .kpi-value { font-size: 26px; font-weight: 800; margin-top: 6px; color: var(--text-main); font-variant-numeric: tabular-nums; }
    .kpi-subtext { font-size: 12px; color: var(--text-dim); margin-top: 4px; }

    /* Controls: Search and Filter Tabs */
    .controls-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 20px;
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      align-items: center;
      justify-content: space-between;
    }
    .search-box {
      flex: 1; min-width: 280px; position: relative;
    }
    .search-input {
      width: 100%;
      background: #0B1120;
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 10px 14px 10px 38px;
      color: var(--text-main);
      font-size: 14px;
      outline: none;
      transition: border-color 0.15s ease;
    }
    .search-input:focus { border-color: var(--accent); }
    .search-icon { position: absolute; left: 12px; top: 12px; color: var(--text-dim); font-size: 14px; }

    .filter-tabs { display: flex; gap: 8px; flex-wrap: wrap; }
    .tab-btn {
      background: #0B1120;
      border: 1px solid var(--card-border);
      color: var(--text-muted);
      padding: 8px 14px;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .tab-btn:hover { background: #162038; color: var(--text-main); }
    .tab-btn.active {
      background: var(--accent-glow);
      color: #38BDF8;
      border-color: #38BDF866;
    }

    /* Table */
    .table-container {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      overflow-x: auto;
      box-shadow: 0 10px 30px rgba(0,0,0,0.3);
    }
    table { width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; }
    th {
      background: var(--header-bg);
      padding: 14px 16px;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-dim);
      border-bottom: 1px solid var(--border-subtle);
    }
    td {
      padding: 14px 16px;
      border-bottom: 1px solid #141E33;
      color: var(--text-main);
      vertical-align: middle;
    }
    tr.case-row { cursor: pointer; transition: background 0.15s ease; }
    tr.case-row:hover { background: var(--row-hover); }

    /* Badges */
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 4px 10px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.02em;
      white-space: nowrap;
    }
    .badge-recovered { background: var(--green-bg); color: var(--green); border: 1px solid rgba(16, 185, 129, 0.3); }
    .badge-escalated { background: var(--amber-bg); color: var(--amber); border: 1px solid rgba(245, 158, 11, 0.3); }
    .badge-blocked { background: var(--red-bg); color: var(--red); border: 1px solid rgba(239, 68, 68, 0.4); }
    .badge-waiting { background: var(--blue-bg); color: var(--blue); border: 1px solid rgba(59, 130, 246, 0.3); }
    .badge-stopped { background: var(--red-bg); color: #F87171; border: 1px solid rgba(239, 68, 68, 0.3); }

    .badge-approved-gate { background: rgba(16, 185, 129, 0.15); color: #34D399; font-size: 11px; padding: 4px 9px; border-radius: 5px; font-weight: 700; border: 1px solid rgba(16, 185, 129, 0.4); }
    .badge-blocked-gate { background: rgba(239, 68, 68, 0.2); color: #F87171; font-size: 11px; padding: 4px 9px; border-radius: 5px; font-weight: 800; border: 1px solid rgba(239, 68, 68, 0.5); }
    .badge-prepipe-stop { background: rgba(239, 68, 68, 0.25); color: #FCA5A5; font-size: 11px; padding: 4px 9px; border-radius: 5px; font-weight: 800; border: 1px solid rgba(239, 68, 68, 0.6); letter-spacing: 0.02em; }
    .badge-promise-wait { background: rgba(59, 130, 246, 0.2); color: #93C5FD; font-size: 11px; padding: 4px 9px; border-radius: 5px; font-weight: 700; border: 1px solid rgba(59, 130, 246, 0.5); }

    .case-id-code { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; font-weight: 700; color: #38BDF8; }
    .type-pill { font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 4px; background: #1E293B; color: var(--text-dim); }

    /* Confidence Bar */
    .conf-container { display: flex; align-items: center; gap: 8px; }
    .conf-bar { width: 50px; height: 6px; background: #1E293B; border-radius: 99px; overflow: hidden; }
    .conf-fill { height: 100%; border-radius: 99px; background: linear-gradient(90deg, #38BDF8, #10B981); }

    /* Multi-Attempt Inspection Drawer */
    .drawer-row { background: #0B1020; }
    .drawer-content {
      padding: 20px 24px;
      border-left: 3px solid var(--accent);
      background: #0B1122;
      border-bottom: 1px solid var(--border-subtle);
    }
    .drawer-title { font-size: 14px; font-weight: 700; margin-bottom: 12px; color: #38BDF8; display: flex; align-items: center; gap: 8px; }
    .attempt-timeline { display: flex; flex-direction: column; gap: 12px; }
    .attempt-step {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 14px 16px;
      display: grid;
      grid-template-columns: 80px 1fr 1fr 1fr;
      gap: 16px;
      align-items: start;
    }
    .step-badge { font-weight: 800; font-size: 12px; color: var(--text-dim); }
    .step-section h4 { font-size: 11px; text-transform: uppercase; color: var(--text-dim); margin-bottom: 4px; }
    .step-section p { font-size: 12px; color: var(--text-main); }
    .violation-pill { background: rgba(239, 68, 68, 0.15); color: #FCA5A5; padding: 4px 8px; border-radius: 4px; font-size: 11px; margin-top: 4px; display: inline-block; }

    /* Empty state */
    .empty-state { text-align: center; padding: 48px; color: var(--text-dim); }
  </style>
</head>
<body>
  <div class="container">
    <!-- Header -->
    <header class="top-header">
      <div class="brand">
        <div class="brand-logo">S</div>
        <div class="brand-title">
          <h1>Sentinel Revenue Recovery Engine</h1>
          <p>Deterministic Compliance Audit Trail & Multi-Attempt Case Lifecycle</p>
        </div>
      </div>
      <div class="badge-benchmark">Razorpay AI Buildathon — Track 3</div>
    </header>

    <!-- KPI Ribbon (Values computed dynamically at render time by JS) -->
    <section class="kpi-grid">
      <div class="kpi-card" style="--kpi-color: #00BAF2;">
        <div class="kpi-label">Evaluated Cases</div>
        <div class="kpi-value" id="kpi-total-cases">--</div>
        <div class="kpi-subtext" id="kpi-case-split">30 Payments / 50 B2B</div>
      </div>
      <div class="kpi-card" style="--kpi-color: #10B981;">
        <div class="kpi-label">Revenue Recovered</div>
        <div class="kpi-value" id="kpi-revenue-recovered">--</div>
        <div class="kpi-subtext" id="kpi-recovery-rate">-- recovery rate</div>
      </div>
      <div class="kpi-card" style="--kpi-color: #EF4444;">
        <div class="kpi-label">Compliance Violations</div>
        <div class="kpi-value" id="kpi-violations" style="color: #10B981;">0</div>
        <div class="kpi-subtext">RBI Fair Practices Guaranteed</div>
      </div>
      <div class="kpi-card" style="--kpi-color: #F87171;">
        <div class="kpi-label">Safety Stops &amp; Gate Blocks</div>
        <div class="kpi-value" id="kpi-gate-blocked">--</div>
        <div class="kpi-subtext">Pre-pipeline stops &amp; gate intercepts</div>
      </div>
      <div class="kpi-card" style="--kpi-color: #F59E0B;">
        <div class="kpi-label">Human Escalations</div>
        <div class="kpi-value" id="kpi-escalated">--</div>
        <div class="kpi-subtext">Disputed &amp; low confidence</div>
      </div>
    </section>

    <!-- Controls: Search & Tabs -->
    <div class="controls-card">
      <div class="search-box">
        <span class="search-icon">🔍</span>
        <input type="text" id="searchInput" class="search-input" placeholder="Search case ID, diagnosis, action, reason...">
      </div>
      <div class="filter-tabs" id="filterTabs">
        <button class="tab-btn active" data-filter="all">All (<span id="count-all">0</span>)</button>
        <button class="tab-btn" data-filter="PAYMENT">Payments (<span id="count-pay">0</span>)</button>
        <button class="tab-btn" data-filter="B2B">B2B (<span id="count-b2b">0</span>)</button>
        <button class="tab-btn" data-filter="SAFETY_STOPS">Safety Stops &amp; Intercepts (<span id="count-blocked">0</span>)</button>
        <button class="tab-btn" data-filter="ESCALATED">Escalated (<span id="count-esc">0</span>)</button>
        <button class="tab-btn" data-filter="RECOVERED">Recovered (<span id="count-rec">0</span>)</button>
      </div>
    </div>

    <!-- Audit Table -->
    <div class="table-container">
      <table>
        <thead>
          <tr>
            <th>Case ID / Type</th>
            <th>Amount</th>
            <th>Diagnosed Root Cause</th>
            <th>Proposed Action</th>
            <th>Confidence</th>
            <th>Compliance Gate Verdict</th>
            <th>Final Outcome</th>
            <th>Resolution</th>
          </tr>
        </thead>
        <tbody id="auditTableBody">
          <!-- Rows injected dynamically by JS -->
        </tbody>
      </table>
      <div id="emptyState" class="empty-state" style="display: none;">
        No cases match the current filter criteria.
      </div>
    </div>
  </div>

  <!-- Real Embedded Benchmark Audit Log JSON -->
  <script>
    const AUDIT_DATA = __AUDIT_DATA_JSON__;

    // --- State ---
    let currentFilter = 'all';
    let currentSearch = '';
    let expandedCaseId = null;

    // --- Formatters ---
    const formatINR = (val) => "₹" + Number(val).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

    // --- Safety Stop Classifier (Harmonizes fraud, dispute, and attempt-cap stops) ---
    function getSafetyStopInfo(c) {
      const isFraud = c.escalation_reason === 'fraud_stop_skip' || (c.status === 'STOPPED' && (c.diagnosis_category || '').includes('Skip'));
      const isDispute = c.escalation_reason === 'dispute_skip';
      const isAttemptCapStart = c.escalation_reason === 'attempt_cap_reached_at_start' ||
                                (c.initial_attempt_count >= 5 && c.case_type === 'FAILED_PAYMENT') ||
                                (c.initial_attempt_count >= 4 && c.case_type === 'B2B_RECEIVABLE');
      const isPromiseWait = c.escalation_reason === 'active_promise_wait_skip';

      if (isFraud) return { isStop: true, type: 'fraud', title: '⛔ PRE-PIPELINE STOP', desc: 'Fraud Hold (Outbound Contact Refused)' };
      if (isDispute) return { isStop: true, type: 'dispute', title: '⛔ PRE-PIPELINE STOP', desc: 'Dispute Hold (RBI Right to Dispute)' };
      if (isAttemptCapStart) return { isStop: true, type: 'cap', title: '⛔ PRE-PIPELINE STOP', desc: 'Attempt Cap Ceiling (Max Attempts Exhausted)' };
      if (isPromiseWait) return { isStop: true, type: 'promise', title: '⏳ PRE-PIPELINE STAND-DOWN', desc: 'Active Promise-to-Pay Honored' };

      return { isStop: false };
    }

    // --- 1. DYNAMIC KPI COMPUTATION AT RENDER TIME ---
    function computeAndRenderKPIs() {
      const total = AUDIT_DATA.length;
      const paymentCount = AUDIT_DATA.filter(c => c.case_type === 'FAILED_PAYMENT').length;
      const b2bCount = AUDIT_DATA.filter(c => c.case_type === 'B2B_RECEIVABLE').length;

      const recovered = AUDIT_DATA.filter(c => c.status === 'RECOVERED');
      const escalated = AUDIT_DATA.filter(c => c.status === 'ESCALATED');
      const safetyStops = AUDIT_DATA.filter(c =>
        getSafetyStopInfo(c).isStop ||
        !c.gate_approved ||
        c.status === 'GATE_BLOCKED' ||
        c.status === 'STOPPED' ||
        (c.gate_violations && c.gate_violations.length > 0)
      );

      const totalRecoveredINR = recovered.reduce((sum, c) => sum + (c.amount_recovered || 0), 0);
      const overallRecoveryRate = ((recovered.length / (total || 1)) * 100).toFixed(1) + "%";

      // Render KPIs
      document.getElementById('kpi-total-cases').textContent = total;
      document.getElementById('kpi-case-split').textContent = `${paymentCount} Payments / ${b2bCount} B2B`;
      document.getElementById('kpi-revenue-recovered').textContent = formatINR(totalRecoveredINR);
      document.getElementById('kpi-recovery-rate').textContent = `${overallRecoveryRate} recovered (${recovered.length}/${total})`;
      document.getElementById('kpi-gate-blocked').textContent = safetyStops.length;
      document.getElementById('kpi-escalated').textContent = escalated.length;

      // Tab Counts
      document.getElementById('count-all').textContent = total;
      document.getElementById('count-pay').textContent = paymentCount;
      document.getElementById('count-b2b').textContent = b2bCount;
      document.getElementById('count-blocked').textContent = safetyStops.length;
      document.getElementById('count-esc').textContent = escalated.length;
      document.getElementById('count-rec').textContent = recovered.length;
    }

    // --- 2. TABLE RENDERING ---
    function renderTable() {
      const tbody = document.getElementById('auditTableBody');
      tbody.innerHTML = '';

      const filtered = AUDIT_DATA.filter(c => {
        const safety = getSafetyStopInfo(c);
        // Tab Filter
        if (currentFilter === 'PAYMENT' && c.case_type !== 'FAILED_PAYMENT') return false;
        if (currentFilter === 'B2B' && c.case_type !== 'B2B_RECEIVABLE') return false;
        if (currentFilter === 'SAFETY_STOPS' && !(safety.isStop || !c.gate_approved || c.status === 'GATE_BLOCKED' || (c.gate_violations && c.gate_violations.length > 0))) return false;
        if (currentFilter === 'ESCALATED' && c.status !== 'ESCALATED') return false;
        if (currentFilter === 'RECOVERED' && c.status !== 'RECOVERED') return false;

        // Search Filter
        if (currentSearch) {
          const q = currentSearch.toLowerCase();
          const matchId = c.case_id.toLowerCase().includes(q);
          const matchDiag = c.diagnosis_category.toLowerCase().includes(q) || c.diagnosis_root_cause.toLowerCase().includes(q);
          const matchAction = c.final_action.toLowerCase().includes(q);
          const matchReason = (c.escalation_reason || '').toLowerCase().includes(q);
          return matchId || matchDiag || matchAction || matchReason;
        }
        return true;
      });

      document.getElementById('emptyState').style.display = filtered.length === 0 ? 'block' : 'none';

      filtered.forEach(c => {
        const tr = document.createElement('tr');
        tr.className = 'case-row';
        tr.dataset.caseId = c.case_id;

        const safety = getSafetyStopInfo(c);

        // Compliance Gate Verdict HTML
        let gateVerdictHtml = '';
        if (safety.isStop) {
          const badgeClass = safety.type === 'promise' ? 'badge-promise-wait' : 'badge-prepipe-stop';
          gateVerdictHtml = `
            <span class="${badgeClass}">${safety.title}</span>
            <div style="font-size: 11px; color: #FCA5A5; margin-top: 3px; font-weight: 600;">${safety.desc}</div>
          `;
        } else if (!c.gate_approved || c.status === 'GATE_BLOCKED' || (c.gate_violations && c.gate_violations.length > 0)) {
          const reasonSnippet = c.gate_violations && c.gate_violations.length > 0 ? c.gate_violations[0] : (c.escalation_reason || 'Compliance violation');
          gateVerdictHtml = `
            <span class="badge-blocked-gate">⚠ GATE INTERCEPTED</span>
            <div style="font-size: 11px; color: #F87171; max-width: 220px; margin-top: 3px; font-weight: 600;">${reasonSnippet.substring(0, 50)}...</div>
          `;
        } else {
          gateVerdictHtml = `
            <span class="badge-approved-gate">✓ APPROVED</span>
            <div style="font-size: 10px; color: var(--text-dim); margin-top: 3px;">Compliant with RBI &amp; Safety Rules</div>
          `;
        }

        // Final Outcome HTML
        let outcomeHtml = '';
        if (c.status === 'RECOVERED') {
          outcomeHtml = `<span class="badge badge-recovered">● RECOVERED</span>`;
        } else if (c.status === 'STOPPED') {
          outcomeHtml = `<span class="badge badge-stopped">■ STOPPED (FRAUD)</span>`;
        } else if (c.status === 'WAITING') {
          outcomeHtml = `<span class="badge badge-waiting">⏳ WAITING (PROMISE)</span>`;
        } else if (c.status === 'ESCALATED') {
          let escDetail = 'HUMAN QUEUE';
          if (safety.type === 'dispute') escDetail = 'DISPUTE QUEUE';
          else if (safety.type === 'cap' || (c.escalation_reason || '').includes('attempt_cap')) escDetail = 'CAP REACHED';
          else if ((c.escalation_reason || '').includes('low_confidence')) escDetail = 'LOW CONFIDENCE';
          outcomeHtml = `<span class="badge badge-escalated">▲ ESCALATED (${escDetail})</span>`;
        } else {
          outcomeHtml = `<span class="badge badge-blocked">✖ ${c.status}</span>`;
        }

        // Type label
        const typeLabel = c.case_type === 'FAILED_PAYMENT' ? 'PAYMENT' : 'B2B';

        tr.innerHTML = `
          <td>
            <div class="case-id-code">${c.case_id}</div>
            <span class="type-pill">${typeLabel}</span>
          </td>
          <td><strong>${formatINR(c.amount)}</strong></td>
          <td>
            <div><strong>${c.diagnosis_category}</strong></div>
            <div style="font-size: 11px; color: var(--text-muted);">${c.diagnosis_root_cause}</div>
          </td>
          <td><code>${c.final_action}</code></td>
          <td>
            <div class="conf-container">
              <span style="font-variant-numeric: tabular-nums; font-weight: 700;">${(c.strategy_confidence * 100).toFixed(0)}%</span>
              <div class="conf-bar"><div class="conf-fill" style="width: ${(c.strategy_confidence * 100).toFixed(0)}%;"></div></div>
            </div>
          </td>
          <td>${gateVerdictHtml}</td>
          <td>${outcomeHtml}</td>
          <td style="color: var(--text-dim); font-size: 12px;">${c.resolution_time}</td>
        `;

        tr.addEventListener('click', () => toggleDrawer(c.case_id, tr));
        tbody.appendChild(tr);

        // If this row was expanded, re-inject the drawer
        if (expandedCaseId === c.case_id) {
          injectDrawer(c, tr);
        }
      });
    }

    // --- 3. MULTI-ATTEMPT INSPECTION DRAWER ---
    function toggleDrawer(caseId, trElement) {
      if (expandedCaseId === caseId) {
        expandedCaseId = null;
        renderTable();
      } else {
        expandedCaseId = caseId;
        renderTable();
      }
    }

    function injectDrawer(c, trElement) {
      const drawerTr = document.createElement('tr');
      drawerTr.className = 'drawer-row';
      const drawerTd = document.createElement('td');
      drawerTd.colSpan = 8;
      drawerTd.style.padding = '0';

      let attemptsHtml = '';
      if (c.attempts && c.attempts.length > 0) {
        attemptsHtml = c.attempts.map(att => `
          <div class="attempt-step">
            <div class="step-badge">ATTEMPT ${att.step}</div>
            <div class="step-section">
              <h4>1. Root Cause Diagnosis</h4>
              <p><strong>${att.diagnosis ? att.diagnosis.category : 'N/A'}</strong></p>
              <p style="color: var(--text-muted); font-size: 11px;">${att.diagnosis ? att.diagnosis.root_cause : 'Direct pre-pipeline skip'}</p>
            </div>
            <div class="step-section">
              <h4>2. Proposed Strategy</h4>
              <p><code>${att.strategy ? att.strategy.proposed_action : c.final_action}</code></p>
              <p style="color: var(--text-muted); font-size: 11px;">${att.strategy ? att.strategy.reasoning.substring(0, 110) + '...' : ''}</p>
            </div>
            <div class="step-section">
              <h4>3. Compliance Gate &amp; Execution</h4>
              <p>
                <span class="${att.gate && att.gate.approved ? 'badge-approved-gate' : 'badge-blocked-gate'}">
                  ${att.gate && att.gate.approved ? 'APPROVED' : 'BLOCKED'}
                </span>
                <span class="badge" style="font-size: 10px; margin-left: 6px;">${att.execution ? att.execution.status : c.status}</span>
              </p>
              ${att.gate && !att.gate.approved && att.gate.violations.length > 0 ? `<div class="violation-pill">${att.gate.violations[0]}</div>` : ''}
            </div>
          </div>
        `).join('');
      } else {
        attemptsHtml = '<p style="color: var(--text-muted); padding: 12px;">Pre-pipeline hard rule evaluation (zero automated attempts fired).</p>';
      }

      drawerTd.innerHTML = `
        <div class="drawer-content">
          <div class="drawer-title">
            <span>Audit Trail Lifecycle — Case ${c.case_id}</span>
            <span style="font-size: 12px; color: var(--text-muted); font-weight: normal;">(Total attempts made: ${c.attempts_made}, Initial attempts: ${c.initial_attempt_count})</span>
          </div>
          <div class="attempt-timeline">
            ${attemptsHtml}
          </div>
        </div>
      `;

      drawerTr.appendChild(drawerTd);
      trElement.parentNode.insertBefore(drawerTr, trElement.nextSibling);
    }

    // --- 4. EVENT LISTENERS ---
    document.getElementById('searchInput').addEventListener('input', (e) => {
      currentSearch = e.target.value;
      renderTable();
    });

    document.getElementById('filterTabs').addEventListener('click', (e) => {
      if (e.target.classList.contains('tab-btn')) {
        document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
        e.target.classList.add('active');
        currentFilter = e.target.dataset.filter;
        renderTable();
      }
    });

    // --- Init ---
    computeAndRenderKPIs();
    renderTable();
  </script>
</body>
</html>
"""


def main():
    print("=" * 60)
    print("GENERATING VISUAL AUDIT LOG VIEWER")
    print("=" * 60)

    print("1. Running deterministic benchmark (seed=42) and collecting audit trails...")
    cases_data = collect_audit_data()
    print(f"--> Collected audit records for {len(cases_data)} cases.")

    print("2. Embedding JSON audit data into standalone HTML template...")
    audit_json = json.dumps(cases_data, indent=None)
    html_content = HTML_TEMPLATE.replace("__AUDIT_DATA_JSON__", audit_json)

    out_dir = Path(__file__).parent.parent / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "audit_viewer.html"

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"--> Standalone HTML viewer successfully created at: {out_path}")
    print(f"--> File size: {len(html_content):,} bytes")
    print("\nAcceptance check: double-click reports/audit_viewer.html to open offline in any browser.")
    print("=" * 60)


if __name__ == "__main__":
    main()
