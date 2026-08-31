"""
Case schemas for both scenarios: Failed Payments and B2B Receivables.

Design notes:
- idempotency_key is a computed property (case_id + attempt_count), never stored.
  This prevents staleness if attempt_count changes without recomputation — a small
  bug with outsized consequences given idempotency is the core financial-safety
  guarantee. (Master prompt §4, rule 2)
- relationship_tier is computed deterministically in core/relationship.py, not here.
  The field is stored after computation so the audit trail can reference it.
- conflicting_signals captures scenario 8 from the Stress Test: when two data
  sources disagree in a way that changes the recommended action. The logic that
  reacts to it is built in Phase 4; the schema field exists now to avoid retrofitting.
- ALL DATA IS SIMULATED — never imply real Razorpay data.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Union


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class CaseType(str, enum.Enum):
    """The two scenarios this system handles."""
    FAILED_PAYMENT = "FAILED_PAYMENT"
    B2B_RECEIVABLE = "B2B_RECEIVABLE"


class CaseStatus(str, enum.Enum):
    """Lifecycle status of a case."""
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    PROMISED = "PROMISED"          # B2B: debtor made a promise-to-pay
    RESOLVED = "RESOLVED"          # Successfully recovered
    DISPUTED = "DISPUTED"          # Hard-stopped due to dispute
    ESCALATED = "ESCALATED"        # Escalated to human review
    STOPPED = "STOPPED"            # Hard-stopped (fraud, cap, or terminal)


class FailureCode(str, enum.Enum):
    """Failure codes for payment cases."""
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    BANK_TIMEOUT = "BANK_TIMEOUT"
    AUTH_FAILURE = "AUTH_FAILURE"
    GATEWAY_ERROR = "GATEWAY_ERROR"
    FRAUD_REJECTION = "FRAUD_REJECTION"


class RelationshipTier(str, enum.Enum):
    """Deterministic relationship-value tier, shown in audit trail.
    Computed by core/relationship.py, never by an LLM."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ActionType(str, enum.Enum):
    """Bounded action menu — Strategy proposes exactly one of these."""
    RETRY_NOW = "RETRY_NOW"
    RETRY_LATER = "RETRY_LATER"
    SUGGEST_ALTERNATE_METHOD = "SUGGEST_ALTERNATE_METHOD"
    SEND_REMINDER = "SEND_REMINDER"
    OFFER_PAYMENT_PLAN = "OFFER_PAYMENT_PLAN"
    ESCALATE_TONE = "ESCALATE_TONE"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    WAIT = "WAIT"
    STOP = "STOP"


# ---------------------------------------------------------------------------
# Conflicting Signal (Stress Test scenario 8, Addendum §3)
# ---------------------------------------------------------------------------

@dataclass
class ConflictingSignal:
    """Represents a conflict between two data sources that changes the
    recommended action. E.g., risk score says 'safe to retry' but a support
    ticket says 'customer asked to stop contact.'

    source_a / source_b: names of the conflicting data sources.
    signal_a / signal_b: what each source says.
    description: human-readable summary of the conflict.
    """
    source_a: str
    signal_a: str
    source_b: str
    signal_b: str
    description: str


# ---------------------------------------------------------------------------
# Promise-to-Pay (B2B only)
# ---------------------------------------------------------------------------

@dataclass
class PromiseToPay:
    """A debtor's promise to pay by a certain date."""
    promised_date: datetime
    promised_amount: float
    kept: Optional[bool] = None   # None = not yet due, True = kept, False = broken


# ---------------------------------------------------------------------------
# Customer / Debtor History
# ---------------------------------------------------------------------------

@dataclass
class CustomerHistory:
    """Historical context for relationship tier and diagnosis.
    - reliability_ratio: proportion of past cases resolved cleanly (0.0–1.0).
      Defaults to 0.5 if no history (cold-start, Addendum §2).
    - total_transactions: engagement tenure signal.
    - total_amount: for value-percentile computation.
    """
    reliability_ratio: float = 0.5      # default: neutral cold-start
    total_transactions: int = 0
    total_amount: float = 0.0
    has_history: bool = False            # explicitly track cold-start


# ---------------------------------------------------------------------------
# Case Models
# ---------------------------------------------------------------------------

@dataclass
class FailedPaymentCase:
    """A failed payment case. ALL DATA IS SIMULATED.

    idempotency_key is a computed property, never stored — it recomputes
    from case_id + attempt_count every time it is accessed, preventing
    stale keys if attempt_count is modified.
    """
    case_id: str
    amount: float
    failure_code: FailureCode
    timestamp: datetime
    attempt_count: int
    customer_id: str
    customer_history: CustomerHistory
    status: CaseStatus = CaseStatus.OPEN
    fraud_flag: bool = False
    relationship_tier: Optional[RelationshipTier] = None
    conflicting_signals: list[ConflictingSignal] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    currency: str = "INR"
    case_type: CaseType = CaseType.FAILED_PAYMENT

    @property
    def idempotency_key(self) -> str:
        """Computed every access — never stale."""
        return f"{self.case_id}_{self.attempt_count}"


@dataclass
class B2BReceivableCase:
    """A B2B overdue receivable case. ALL DATA IS SIMULATED.

    idempotency_key is a computed property, never stored.
    """
    case_id: str
    invoice_id: str
    amount: float
    invoice_date: datetime
    due_date: datetime
    debtor_id: str
    debtor_history: CustomerHistory
    attempt_count: int = 0
    contact_count: int = 0
    dispute_flag: bool = False
    fraud_flag: bool = False
    promise_to_pay: Optional[PromiseToPay] = None
    status: CaseStatus = CaseStatus.OPEN
    relationship_tier: Optional[RelationshipTier] = None
    conflicting_signals: list[ConflictingSignal] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    currency: str = "INR"
    case_type: CaseType = CaseType.B2B_RECEIVABLE

    @property
    def days_overdue(self) -> int:
        """Computed from due_date vs permanent simulation reference time."""
        from core import config
        ref_time = getattr(config, "SIMULATED_CURRENT_TIME", datetime(2026, 8, 24, 12, 0, 0))
        delta = ref_time - self.due_date
        return max(0, delta.days)

    @property
    def idempotency_key(self) -> str:
        """Computed every access — never stale."""
        return f"{self.case_id}_{self.attempt_count}"


# ---------------------------------------------------------------------------
# Serialization helpers (for JSON persistence)
# ---------------------------------------------------------------------------

def _serialize_datetime(dt: Optional[datetime]) -> Optional[str]:
    """ISO format, or None."""
    return dt.isoformat() if dt else None


def case_to_dict(case: FailedPaymentCase | B2BReceivableCase) -> dict[str, Any]:
    """Convert a case to a JSON-serializable dict.
    idempotency_key is included as a snapshot but is always recomputed on access.
    """
    if isinstance(case, FailedPaymentCase):
        return {
            "data_type": "SIMULATED",
            "case_type": case.case_type.value,
            "case_id": case.case_id,
            "amount": case.amount,
            "currency": case.currency,
            "failure_code": case.failure_code.value,
            "timestamp": _serialize_datetime(case.timestamp),
            "attempt_count": case.attempt_count,
            "customer_id": case.customer_id,
            "customer_history": {
                "reliability_ratio": case.customer_history.reliability_ratio,
                "total_transactions": case.customer_history.total_transactions,
                "total_amount": case.customer_history.total_amount,
                "has_history": case.customer_history.has_history,
            },
            "status": case.status.value,
            "fraud_flag": case.fraud_flag,
            "relationship_tier": case.relationship_tier.value if case.relationship_tier else None,
            "conflicting_signals": [
                {
                    "source_a": cs.source_a,
                    "signal_a": cs.signal_a,
                    "source_b": cs.source_b,
                    "signal_b": cs.signal_b,
                    "description": cs.description,
                }
                for cs in case.conflicting_signals
            ],
            "created_at": _serialize_datetime(case.created_at),
            "idempotency_key": case.idempotency_key,  # snapshot, always recomputed
        }
    elif isinstance(case, B2BReceivableCase):
        return {
            "data_type": "SIMULATED",
            "case_type": case.case_type.value,
            "case_id": case.case_id,
            "invoice_id": case.invoice_id,
            "amount": case.amount,
            "currency": case.currency,
            "invoice_date": _serialize_datetime(case.invoice_date),
            "due_date": _serialize_datetime(case.due_date),
            "days_overdue": case.days_overdue,
            "debtor_id": case.debtor_id,
            "debtor_history": {
                "reliability_ratio": case.debtor_history.reliability_ratio,
                "total_transactions": case.debtor_history.total_transactions,
                "total_amount": case.debtor_history.total_amount,
                "has_history": case.debtor_history.has_history,
            },
            "attempt_count": case.attempt_count,
            "contact_count": case.contact_count,
            "dispute_flag": case.dispute_flag,
            "fraud_flag": case.fraud_flag,
            "promise_to_pay": {
                "promised_date": _serialize_datetime(case.promise_to_pay.promised_date),
                "promised_amount": case.promise_to_pay.promised_amount,
                "kept": case.promise_to_pay.kept,
            } if case.promise_to_pay else None,
            "status": case.status.value,
            "relationship_tier": case.relationship_tier.value if case.relationship_tier else None,
            "conflicting_signals": [
                {
                    "source_a": cs.source_a,
                    "signal_a": cs.signal_a,
                    "source_b": cs.source_b,
                    "signal_b": cs.signal_b,
                    "description": cs.description,
                }
                for cs in case.conflicting_signals
            ],
            "created_at": _serialize_datetime(case.created_at),
            "idempotency_key": case.idempotency_key,
        }
    else:
        raise TypeError(f"Unknown case type: {type(case)}")


def dict_to_failed_payment(d: dict[str, Any]) -> FailedPaymentCase:
    """Reconstruct a FailedPaymentCase from a dict."""
    hist = d.get("customer_history", {})
    signals = [
        ConflictingSignal(**cs) for cs in d.get("conflicting_signals", [])
    ]
    return FailedPaymentCase(
        case_id=d["case_id"],
        amount=d["amount"],
        failure_code=FailureCode(d["failure_code"]),
        timestamp=datetime.fromisoformat(d["timestamp"]),
        attempt_count=d["attempt_count"],
        customer_id=d["customer_id"],
        customer_history=CustomerHistory(
            reliability_ratio=hist.get("reliability_ratio", 0.5),
            total_transactions=hist.get("total_transactions", 0),
            total_amount=hist.get("total_amount", 0.0),
            has_history=hist.get("has_history", False),
        ),
        status=CaseStatus(d.get("status", "OPEN")),
        fraud_flag=d.get("fraud_flag", False),
        relationship_tier=RelationshipTier(d["relationship_tier"]) if d.get("relationship_tier") else None,
        conflicting_signals=signals,
        created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.now(),
        currency=d.get("currency", "INR"),
    )


def dict_to_b2b_receivable(d: dict[str, Any]) -> B2BReceivableCase:
    """Reconstruct a B2BReceivableCase from a dict."""
    hist = d.get("debtor_history", {})
    signals = [
        ConflictingSignal(**cs) for cs in d.get("conflicting_signals", [])
    ]
    ptp_data = d.get("promise_to_pay")
    ptp = None
    if ptp_data:
        ptp = PromiseToPay(
            promised_date=datetime.fromisoformat(ptp_data["promised_date"]),
            promised_amount=ptp_data["promised_amount"],
            kept=ptp_data.get("kept"),
        )
    return B2BReceivableCase(
        case_id=d["case_id"],
        invoice_id=d["invoice_id"],
        amount=d["amount"],
        invoice_date=datetime.fromisoformat(d["invoice_date"]),
        due_date=datetime.fromisoformat(d["due_date"]),
        debtor_id=d["debtor_id"],
        debtor_history=CustomerHistory(
            reliability_ratio=hist.get("reliability_ratio", 0.5),
            total_transactions=hist.get("total_transactions", 0),
            total_amount=hist.get("total_amount", 0.0),
            has_history=hist.get("has_history", False),
        ),
        attempt_count=d.get("attempt_count", 0),
        contact_count=d.get("contact_count", 0),
        dispute_flag=d.get("dispute_flag", False),
        fraud_flag=d.get("fraud_flag", False),
        promise_to_pay=ptp,
        status=CaseStatus(d.get("status", "OPEN")),
        relationship_tier=RelationshipTier(d["relationship_tier"]) if d.get("relationship_tier") else None,
        conflicting_signals=signals,
        created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.now(),
        currency=d.get("currency", "INR"),
    )


# Single source of truth type alias for either case type
Case = Union[FailedPaymentCase, B2BReceivableCase]

# Backward-compatible alias
dict_to_b2b_case = dict_to_b2b_receivable

