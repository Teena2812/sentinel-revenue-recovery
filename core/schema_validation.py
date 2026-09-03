"""
core/schema_validation.py — Typed LLM Output Schema Validation

Validates that a successfully-parsed LLM response dict has the correct
structure, types, and enum membership before any field is consumed by
the pipeline. Raises SchemaValidationError (subclass of Exception) so
the enclosing except Exception blocks in diagnosis.py and strategy.py
catch it with the same retry-once-then-escalate behavior as unparseable
JSON, producing LLM_RESPONSE_INVALID_SCHEMA in the reasoning.
"""

from core.schemas import ActionType


# _VALID_CATEGORIES is populated lazily on first use to avoid circular imports
# (agents.diagnosis imports core.schema_validation, so we cannot import agents.diagnosis at module level)
_VALID_CATEGORIES: set[str] = set()



# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class SchemaValidationError(Exception):
    """Base class for all LLM schema validation failures."""


class FieldMissingError(SchemaValidationError):
    """A required field is absent from the LLM response."""


class FieldTypeError(SchemaValidationError):
    """A field is present but has the wrong type."""


class InvalidEnumError(SchemaValidationError):
    """A field contains a string not in the allowed enum set."""


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

_REQUIRED_DIAGNOSIS_FIELDS = {
    "category": str,
    "confidence": (int, float),
    "reasoning": str,
}

_REQUIRED_STRATEGY_FIELDS = {
    "proposed_action": str,
    "confidence": (int, float),
    "reasoning": str,
    "risk_assessment": str,
}

_VALID_RISK_ASSESSMENTS = {"LOW", "MEDIUM", "HIGH"}


def validate_diagnosis_output(raw: dict) -> None:
    """Validate the structure, types, and enum membership of a diagnosis LLM response.

    Raises SchemaValidationError (or a subclass) with a descriptive message
    on the first violation found. Called inside the existing except Exception
    catch in agents/diagnosis.py, so failures produce LLM_RESPONSE_INVALID_SCHEMA.
    """
    if not isinstance(raw, dict):
        raise FieldTypeError(f"Expected dict, got {type(raw).__name__}")

    for field, expected_type in _REQUIRED_DIAGNOSIS_FIELDS.items():
        if field not in raw:
            raise FieldMissingError(f"Required field '{field}' missing from diagnosis response")
        if not isinstance(raw[field], expected_type):
            raise FieldTypeError(
                f"Field '{field}' expected {expected_type}, got {type(raw[field]).__name__}: {raw[field]!r}"
            )

    # Lazy import to avoid circular dependency (agents.diagnosis -> core.schema_validation)
    from agents.diagnosis import DiagnosisCategory  # noqa: PLC0415
    valid_categories = {c.value for c in DiagnosisCategory}
    cat = raw["category"]
    if cat not in valid_categories:
        raise InvalidEnumError(
            f"'category' value '{cat}' is not a valid DiagnosisCategory. "
            f"Valid values: {sorted(valid_categories)}"
        )

    confidence = raw["confidence"]
    if not (0.0 <= float(confidence) <= 1.0):
        raise FieldTypeError(
            f"'confidence' must be in [0.0, 1.0], got {confidence!r}"
        )


def validate_strategy_output(raw: dict) -> None:
    """Validate the structure and types of a strategy LLM response.

    Checks required fields, types, confidence range, and risk_assessment enum.
    Does NOT check proposed_action enum membership — out-of-menu action detection
    is handled separately in strategy.py with the distinct INVALID_ACTION_REJECTED
    reason code. This validator catches structural/type failures that happen
    when parsing succeeds but the shape is still wrong (e.g. confidence: "high").
    """
    if not isinstance(raw, dict):
        raise FieldTypeError(f"Expected dict, got {type(raw).__name__}")

    for field, expected_type in _REQUIRED_STRATEGY_FIELDS.items():
        if field not in raw:
            raise FieldMissingError(f"Required field '{field}' missing from strategy response")
        if field == "proposed_action":
            continue  # enum membership deferred to out-of-menu detection in strategy.py
        if not isinstance(raw[field], expected_type):
            raise FieldTypeError(
                f"Field '{field}' expected {expected_type}, got {type(raw[field]).__name__}: {raw[field]!r}"
            )

    risk = str(raw["risk_assessment"]).upper()
    if risk not in _VALID_RISK_ASSESSMENTS:
        raise InvalidEnumError(
            f"'risk_assessment' value '{raw['risk_assessment']!r}' is not valid. "
            f"Valid values: {_VALID_RISK_ASSESSMENTS}"
        )

    confidence = raw["confidence"]
    if not (0.0 <= float(confidence) <= 1.0):
        raise FieldTypeError(
            f"'confidence' must be in [0.0, 1.0], got {confidence!r}"
        )
