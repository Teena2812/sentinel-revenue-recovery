"""
Audit Log — Execution log and full audit trail persistence.

This is the foundation for:
1. Idempotency checks (check_idempotency in compliance.py reads from this log)
2. The audit trail (FR-8.1: every decision traceable end-to-end)
3. Memory/analytics (Phase 2+: outcome tracking, strategy success rates)

The structure is defined now in Phase 1, even though it's primarily populated
starting in Phase 2 when the full pipeline executes. Building the empty
structure means Phase 2 has somewhere to write from day one.

Storage: JSON file (audit_log.json) — matches the JSON-for-now decision.
Will migrate to SQLite if querying becomes a bottleneck in later phases.

ALL DATA IS SIMULATED.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default path for the persisted audit log
DEFAULT_AUDIT_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "audit_log.json",
)


# ---------------------------------------------------------------------------
# Audit Entry schemas
# ---------------------------------------------------------------------------

@dataclass
class DiagnosisEntry:
    """Record of a diagnosis agent's output for a case."""
    case_id: str
    timestamp: str
    root_cause: str
    reasoning: str
    confidence: float
    raw_output: Optional[dict] = None


@dataclass
class StrategyEntry:
    """Record of a strategy agent's proposal for a case."""
    case_id: str
    timestamp: str
    proposed_action: str
    confidence: float
    reasoning: str
    relationship_tier: Optional[str] = None
    conflicting_signals_present: bool = False
    raw_output: Optional[dict] = None


@dataclass
class GateEntry:
    """Record of the deterministic gate's decision."""
    case_id: str
    timestamp: str
    proposed_action: str
    approved: bool
    checks_run: list[dict] = field(default_factory=list)
    violation_reasons: list[str] = field(default_factory=list)


@dataclass
class ExecutionEntry:
    """Record of an execution action — the core of the idempotency log.

    The execution_log dict used by check_idempotency maps idempotency_key
    to dicts with at minimum {"status": "SUCCESS" | "FAILED" | "PENDING"}.
    This entry provides the full record.
    """
    case_id: str
    idempotency_key: str
    timestamp: str
    action: str
    status: str                         # "SUCCESS", "FAILED", "PENDING"
    result_detail: Optional[str] = None
    retry_attempted: bool = False
    fallback_used: bool = False
    error: Optional[str] = None


@dataclass
class CaseAuditTrail:
    """Full audit trail for a single case — every decision traceable (FR-8.1).

    This is the exportable, human-readable record for any given case.
    """
    case_id: str
    case_type: str
    created_at: str
    current_status: str
    diagnoses: list[DiagnosisEntry] = field(default_factory=list)
    strategies: list[StrategyEntry] = field(default_factory=list)
    gate_decisions: list[GateEntry] = field(default_factory=list)
    executions: list[ExecutionEntry] = field(default_factory=list)
    status_changes: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Audit Log Store
# ---------------------------------------------------------------------------

class AuditLog:
    """Persistent audit log and execution history.

    Provides:
    - add_*() methods to record events (used by the pipeline in Phase 2+)
    - get_execution_log() → the dict consumed by check_idempotency()
    - get_case_trail() → full audit trail for a single case
    - save() / load() → persist to / restore from JSON
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path or DEFAULT_AUDIT_LOG_PATH
        self._lock = threading.RLock()
        # execution_log: keyed by idempotency_key → ExecutionEntry-as-dict
        self._execution_log: dict[str, dict] = {}
        # case_trails: keyed by case_id → CaseAuditTrail
        self._case_trails: dict[str, CaseAuditTrail] = {}

    @property
    def lock(self) -> threading.RLock:
        """Expose re-entrant lock for critical pipeline sections."""
        return self._lock

    # --- Execution log (for idempotency) ---

    def get_execution_log(self) -> dict[str, dict]:
        """Return the execution log dict for check_idempotency()."""
        with self._lock:
            return self._execution_log

    def check_and_reserve_idempotency(
        self, idempotency_key: str, action: str
    ) -> tuple[bool, str]:
        """Atomically check and reserve an idempotency key under a single lock acquisition.

        Prevents check-then-act race conditions (TOCTOU) across concurrent threads.
        Transitions state to IN_FLIGHT if unreserved or if prior was FAILED.
        Blocks if already SUCCESS or IN_FLIGHT/PENDING.

        Returns:
            (True, "RESERVED") if safe to proceed.
            (False, reason) if blocked by prior execution or concurrent in-flight attempt.
        """
        with self._lock:
            prior = self._execution_log.get(idempotency_key)
            if prior is not None:
                status = prior.get("status")
                if status == "SUCCESS":
                    return False, (
                        f"CONCURRENT_EXECUTION_BLOCKED: Action already successfully executed for key '{idempotency_key}'."
                    )
                elif status in {"IN_FLIGHT", "PENDING"}:
                    return False, (
                        f"CONCURRENT_EXECUTION_BLOCKED: Action is already in-flight for key '{idempotency_key}'."
                    )

            # Atomic transition to IN_FLIGHT under the same lock acquisition
            self._execution_log[idempotency_key] = {
                "status": "IN_FLIGHT",
                "action": action,
                "timestamp": datetime.now().isoformat(),
            }
            return True, "RESERVED"

    def release_reservation(
        self, idempotency_key: str, final_status: str = "FAILED"
    ) -> None:
        """Cleanly release an IN_FLIGHT reservation under the lock if an error occurs.

        Ensures keys are not permanently stuck in IN_FLIGHT if an unexpected
        exception aborts execution before record_execution() can be reached.
        """
        with self._lock:
            prior = self._execution_log.get(idempotency_key)
            if prior and prior.get("status") in {"IN_FLIGHT", "PENDING"}:
                prior["status"] = final_status
                prior["timestamp"] = datetime.now().isoformat()
                logger.info(
                    "Released IN_FLIGHT reservation for %s -> %s",
                    idempotency_key,
                    final_status,
                )

    def record_execution(self, entry: ExecutionEntry) -> None:
        """Record an execution event. Updates the idempotency-keyed log."""
        with self._lock:
            self._execution_log[entry.idempotency_key] = {
                "status": entry.status,
                "action": entry.action,
                "timestamp": entry.timestamp,
                "result_detail": entry.result_detail,
                "retry_attempted": entry.retry_attempted,
                "fallback_used": entry.fallback_used,
                "error": entry.error,
            }
            # Also append to the case trail
            trail = self._ensure_trail(entry.case_id)
            trail.executions.append(entry)
            logger.info(
                "Recorded execution: case=%s key=%s status=%s",
                entry.case_id, entry.idempotency_key, entry.status,
            )

    # --- Diagnosis ---

    def record_diagnosis(self, entry: DiagnosisEntry) -> None:
        with self._lock:
            trail = self._ensure_trail(entry.case_id)
            trail.diagnoses.append(entry)

    # --- Strategy ---

    def record_strategy(self, entry: StrategyEntry) -> None:
        with self._lock:
            trail = self._ensure_trail(entry.case_id)
            trail.strategies.append(entry)

    # --- Gate ---

    def record_gate_decision(self, entry: GateEntry) -> None:
        with self._lock:
            trail = self._ensure_trail(entry.case_id)
            trail.gate_decisions.append(entry)

    # --- Status changes ---

    def record_status_change(
        self, case_id: str, old_status: str, new_status: str, reason: str
    ) -> None:
        with self._lock:
            trail = self._ensure_trail(case_id)
            trail.current_status = new_status
            trail.status_changes.append({
                "timestamp": datetime.now().isoformat(),
                "from": old_status,
                "to": new_status,
                "reason": reason,
            })

    # --- Notes ---

    def add_note(self, case_id: str, note: str) -> None:
        with self._lock:
            trail = self._ensure_trail(case_id)
            trail.notes.append(f"[{datetime.now().isoformat()}] {note}")

    # --- Retrieval ---

    def get_case_trail(self, case_id: str) -> Optional[CaseAuditTrail]:
        """Get the full audit trail for a case."""
        with self._lock:
            return self._case_trails.get(case_id)

    def get_all_case_ids(self) -> list[str]:
        with self._lock:
            return list(self._case_trails.keys())

    def get_all_case_trails(self) -> dict[str, CaseAuditTrail]:
        """Return a copy of all case trails."""
        with self._lock:
            return dict(self._case_trails)

    # --- Trail initialization ---

    def init_case_trail(
        self, case_id: str, case_type: str, status: str = "OPEN"
    ) -> CaseAuditTrail:
        """Explicitly initialize a trail for a case. Idempotent."""
        with self._lock:
            if case_id not in self._case_trails:
                self._case_trails[case_id] = CaseAuditTrail(
                    case_id=case_id,
                    case_type=case_type,
                    created_at=datetime.now().isoformat(),
                    current_status=status,
                )
            return self._case_trails[case_id]

    def _ensure_trail(self, case_id: str) -> CaseAuditTrail:
        """Ensure a trail exists for a case. Auto-creates if needed."""
        if case_id not in self._case_trails:
            self._case_trails[case_id] = CaseAuditTrail(
                case_id=case_id,
                case_type="UNKNOWN",
                created_at=datetime.now().isoformat(),
                current_status="OPEN",
            )
        return self._case_trails[case_id]

    # --- Persistence ---

    def save(self) -> None:
        """Persist the audit log to JSON."""
        with self._lock:
            data = {
                "data_type": "SIMULATED",
                "saved_at": datetime.now().isoformat(),
                "execution_log": self._execution_log,
                "case_trails": {
                    cid: _trail_to_dict(trail)
                    for cid, trail in self._case_trails.items()
                },
            }
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            logger.info("Audit log saved to %s", self.path)

    def load(self) -> None:
        """Load the audit log from JSON. No-op if file doesn't exist."""
        with self._lock:
            if not os.path.exists(self.path):
                logger.info("No existing audit log at %s — starting fresh.", self.path)
                return
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._execution_log = data.get("execution_log", {})
            for cid, trail_dict in data.get("case_trails", {}).items():
                self._case_trails[cid] = _dict_to_trail(trail_dict)
            logger.info(
                "Audit log loaded: %d executions, %d case trails.",
                len(self._execution_log), len(self._case_trails),
            )

    def clear(self) -> None:
        """Clear all in-memory state. Does not delete the file."""
        with self._lock:
            self._execution_log.clear()
            self._case_trails.clear()


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _trail_to_dict(trail: CaseAuditTrail) -> dict[str, Any]:
    """Convert a CaseAuditTrail to a JSON-serializable dict."""
    return {
        "case_id": trail.case_id,
        "case_type": trail.case_type,
        "created_at": trail.created_at,
        "current_status": trail.current_status,
        "diagnoses": [asdict(d) for d in trail.diagnoses],
        "strategies": [asdict(s) for s in trail.strategies],
        "gate_decisions": [asdict(g) for g in trail.gate_decisions],
        "executions": [asdict(e) for e in trail.executions],
        "status_changes": trail.status_changes,
        "notes": trail.notes,
    }


def _dict_to_trail(d: dict[str, Any]) -> CaseAuditTrail:
    """Reconstruct a CaseAuditTrail from a dict."""
    trail = CaseAuditTrail(
        case_id=d["case_id"],
        case_type=d.get("case_type", "UNKNOWN"),
        created_at=d.get("created_at", ""),
        current_status=d.get("current_status", "OPEN"),
    )
    for diag in d.get("diagnoses", []):
        trail.diagnoses.append(DiagnosisEntry(**diag))
    for strat in d.get("strategies", []):
        trail.strategies.append(StrategyEntry(**strat))
    for gate in d.get("gate_decisions", []):
        trail.gate_decisions.append(GateEntry(**gate))
    for exe in d.get("executions", []):
        trail.executions.append(ExecutionEntry(**exe))
    trail.status_changes = d.get("status_changes", [])
    trail.notes = d.get("notes", [])
    return trail
