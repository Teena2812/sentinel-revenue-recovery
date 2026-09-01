"""
LLM Client Abstraction Layer.

Provides a unified interface for LLM calls with two implementations:
- MockLLMClient: Deterministic, offline responses keyed by:
    - Payments: (failure_code, relationship_tier, attempt_count)
    - B2B Receivables: (diagnosis_category, relationship_tier, attempt_count, has_broken_promise)
  Enables rigorous, deterministic unit and CI testing of multi-attempt adaptation.
- GeminiLLMClient: Real Google Gemini API calls with structured JSON output and caching.
"""

from __future__ import annotations

import abc
import hashlib
import json
import logging
import os
from typing import Any, Optional

from core import config
from core.schemas import FailureCode, RelationshipTier

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Raised when an LLM call fails or returns unparseable content."""
    pass


class LLMClient(abc.ABC):
    """Abstract base class for LLM clients."""

    @abc.abstractmethod
    def call(self, prompt: str, schema: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Call the LLM with a prompt and expected schema, returning parsed JSON."""
        pass


class MockLLMClient(LLMClient):
    """Deterministic offline mock LLM client supporting Payments and B2B."""

    def __init__(self, override_responses: Optional[dict[str, Any]] = None):
        self.override_responses = override_responses or {}
        self.call_history: list[dict[str, Any]] = []

    def call(self, prompt: str, schema: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        self.call_history.append({"prompt": prompt, "schema": schema})

        # Check for explicit test override
        for key, response in self.override_responses.items():
            if key in prompt:
                if isinstance(response, Exception):
                    raise response
                if isinstance(response, str):
                    try:
                        return json.loads(response)
                    except Exception as e:
                        raise LLMError(f"Malformed mock JSON: {response}") from e
                return response

        # Check if this is a Diagnosis prompt
        if "DIAGNOSIS REQUEST" in prompt:
            return self._handle_diagnosis(prompt)

        # Check if this is a Strategy prompt
        if "STRATEGY PROPOSAL REQUEST" in prompt or "RE-PROPOSAL REQUEST" in prompt:
            return self._handle_strategy(prompt)

        # Default fallback
        return {
            "root_cause": "Unknown failure condition.",
            "category": "UNKNOWN",
            "confidence": 0.50,
            "reasoning": "Mock default response.",
            "proposed_action": "RETRY_NOW",
            "risk_assessment": "MEDIUM",
        }

    def _extract_tier(self, prompt: str) -> str:
        prompt_lower = prompt.lower()
        for t in RelationshipTier:
            # Match "Relationship Tier: HIGH" etc. (prompt is lowercased, value must also be)
            if f"relationship tier: {t.value.lower()}" in prompt_lower:
                return t.value
        return "MEDIUM"

    def _extract_attempt(self, prompt: str) -> int:
        for line in prompt.splitlines():
            if "Attempt Count:" in line or "attempt_count:" in line.lower():
                try:
                    return int(line.split(":")[-1].strip())
                except ValueError:
                    pass
        return 1

    def _handle_diagnosis(self, prompt: str) -> dict[str, Any]:
        tier = self._extract_tier(prompt)

        # Check if B2B Case
        if "B2B_RECEIVABLE" in prompt or "Invoice ID:" in prompt:
            return self._handle_b2b_diagnosis(prompt, tier)

        # Payments Diagnosis
        failure_code = "INSUFFICIENT_FUNDS"
        for fc in FailureCode:
            if fc.value in prompt:
                failure_code = fc.value
                break

        matrix = {
            "INSUFFICIENT_FUNDS": {
                "HIGH": {
                    "root_cause": "Customer account temporarily lacked required liquidity during recurring auto-debit billing cycle.",
                    "category": "FUNDS_UNAVAILABLE",
                    "confidence": 0.92,
                    "reasoning": "High-tier relationship indicates transient payday/timing liquidity mismatch.",
                },
                "MEDIUM": {
                    "root_cause": "Insufficient funds at the time of charge attempt.",
                    "category": "FUNDS_UNAVAILABLE",
                    "confidence": 0.88,
                    "reasoning": "Standard balance shortage. Recommend retrying after balance replenishment window.",
                },
                "LOW": {
                    "root_cause": "Account balance depleted; payment instrument liquidity unavailable.",
                    "category": "FUNDS_UNAVAILABLE",
                    "confidence": 0.82,
                    "reasoning": "Low reliability score suggests customer may need an alternate payment instrument.",
                },
            },
            "BANK_TIMEOUT": {
                "HIGH": {
                    "root_cause": "Transient network timeout at acquiring bank switch during peak processing window.",
                    "category": "TRANSIENT_NETWORK",
                    "confidence": 0.95,
                    "reasoning": "Standard bank gateway timeout with no user-side debit confirmation.",
                },
                "MEDIUM": {
                    "root_cause": "Issuing bank response timeout.",
                    "category": "TRANSIENT_NETWORK",
                    "confidence": 0.92,
                    "reasoning": "Temporary bank switch degradation.",
                },
                "LOW": {
                    "root_cause": "Connection drop between acquiring bank and issuing network switch.",
                    "category": "TRANSIENT_NETWORK",
                    "confidence": 0.90,
                    "reasoning": "Transient network failure.",
                },
            },
            "AUTH_FAILURE": {
                "HIGH": {
                    "root_cause": "Customer OTP session timed out or mandate token required step-up verification.",
                    "category": "AUTH_EXPIRED",
                    "confidence": 0.90,
                    "reasoning": "High-value customer mandate auth expiration; seamless alternate link will recover.",
                },
                "MEDIUM": {
                    "root_cause": "Customer failed 2-factor authentication / 3DS step-up verification.",
                    "category": "AUTH_EXPIRED",
                    "confidence": 0.86,
                    "reasoning": "Authentication token expired or OTP not submitted.",
                },
                "LOW": {
                    "root_cause": "Repeated authentication rejection or card credential mismatch.",
                    "category": "AUTH_EXPIRED",
                    "confidence": 0.80,
                    "reasoning": "Customer failed authentication challenge; escalation or manual outreach advised.",
                },
            },
            "GATEWAY_ERROR": {
                "HIGH": {
                    "root_cause": "Razorpay internal switch failover or upstream processor 502/504 error.",
                    "category": "TRANSIENT_NETWORK",
                    "confidence": 0.94,
                    "reasoning": "Internal gateway routing hiccup, immediate smart retry will succeed via secondary route.",
                },
                "MEDIUM": {
                    "root_cause": "Upstream gateway infrastructure error.",
                    "category": "TRANSIENT_NETWORK",
                    "confidence": 0.91,
                    "reasoning": "Processor switch error.",
                },
                "LOW": {
                    "root_cause": "Payment gateway processing failure.",
                    "category": "TRANSIENT_NETWORK",
                    "confidence": 0.87,
                    "reasoning": "Gateway rejected processing; retry later or switch rails.",
                },
            },
            "FRAUD_REJECTION": {
                "HIGH": {
                    "root_cause": "Risk engine flagged unusual geo-velocity or suspicious device signature.",
                    "category": "SYSTEMIC_RISK",
                    "confidence": 0.98,
                    "reasoning": "Critical risk flag detected. Must halt all automated recovery actions immediately.",
                },
                "MEDIUM": {
                    "root_cause": "Risk engine rejected transaction due to card velocity anomaly.",
                    "category": "SYSTEMIC_RISK",
                    "confidence": 0.98,
                    "reasoning": "Critical risk flag detected. Must halt all automated recovery actions immediately.",
                },
                "LOW": {
                    "root_cause": "Blacklisted fingerprint / card fraud trigger.",
                    "category": "SYSTEMIC_RISK",
                    "confidence": 0.98,
                    "reasoning": "Critical risk flag detected. Must halt all automated recovery actions immediately.",
                },
            },
        }

        fc_data = matrix.get(failure_code, matrix["INSUFFICIENT_FUNDS"])
        return fc_data.get(tier, fc_data["MEDIUM"])

    def _handle_b2b_diagnosis(self, prompt: str, tier: str) -> dict[str, Any]:
        # Identify B2B scenario triggers
        if "Dispute Flag: True" in prompt:
            return {
                "root_cause": "Debtor has raised formal dispute regarding invoice deliverables.",
                "category": "DISPUTED_DELIVERABLE",
                "confidence": 0.96,
                "reasoning": "Active dispute flag present. Halting automated collections and routing to human resolution.",
            }
        if "Status: BROKEN" in prompt or "broken promise" in prompt.lower():
            return {
                "root_cause": "Debtor breached agreed payment milestone date.",
                "category": "CHRONIC_DELINQUENCY" if tier == "LOW" else "CASH_FLOW_MISMATCH",
                "confidence": 0.92,
                "reasoning": "Prior commitment unfulfilled. Requires escalated firm follow-up.",
            }
        import re
        days_match = re.search(r"Days Overdue:\s*(\d+)", prompt)
        if days_match and int(days_match.group(1)) <= 10:
            return {
                "root_cause": "Invoice pending internal accounts payable / procurement approval workflow.",
                "category": "ADMINISTRATIVE_DELAY",
                "confidence": 0.90,
                "reasoning": "Early overdue state typical of routine internal accounting cycle lag.",
            }

        return {
            "root_cause": "Debtor experiencing temporary working capital mismatch.",
            "category": "CASH_FLOW_MISMATCH",
            "confidence": 0.88,
            "reasoning": "Overdue receivable with established commercial relationship.",
        }

    def _handle_strategy(self, prompt: str) -> dict[str, Any]:
        tier = self._extract_tier(prompt)
        attempt = self._extract_attempt(prompt)

        # Check if B2B Strategy
        if "B2B" in prompt or "ADMINISTRATIVE_DELAY" in prompt or "CASH_FLOW_MISMATCH" in prompt or "DISPUTED_DELIVERABLE" in prompt or "CHRONIC_DELINQUENCY" in prompt or "COMMUNICATION_BREAKDOWN" in prompt:
            return self._handle_b2b_strategy(prompt, tier, attempt)

        # Payments Strategy
        failure_code = "INSUFFICIENT_FUNDS"
        for fc in FailureCode:
            if fc.value in prompt:
                failure_code = fc.value
                break

        # Re-proposal prompt handling
        if "RE-PROPOSAL REQUEST" in prompt:
            return {
                "proposed_action": "ESCALATE_HUMAN",
                "confidence": 0.90,
                "reasoning": "Prior proposal was rejected by compliance gate. Re-proposing safe escalation to human queue.",
                "risk_assessment": "LOW",
            }

        # Multi-attempt adaptation for Failed Payments
        if failure_code == "INSUFFICIENT_FUNDS":
            if attempt == 1:
                action = "RETRY_LATER" if tier in {"HIGH", "MEDIUM"} else "SUGGEST_ALTERNATE_METHOD"
                conf = 0.90 if tier == "HIGH" else (0.86 if tier == "MEDIUM" else 0.84)
                reason = "Delay retry by 6h for liquidity replenishment." if action == "RETRY_LATER" else "Suggest alternate payment method."
            else:
                action = "SUGGEST_ALTERNATE_METHOD" if tier in {"HIGH", "MEDIUM"} else "ESCALATE_HUMAN"
                conf = 0.88
                reason = f"Attempt {attempt}: Prior retry failed; adapting strategy to alternate payment instrument."
            return {"proposed_action": action, "confidence": conf, "reasoning": reason, "risk_assessment": "LOW"}

        elif failure_code == "BANK_TIMEOUT":
            if attempt == 1:
                return {"proposed_action": "RETRY_NOW", "confidence": 0.94, "reasoning": "Transient network timeout: retry immediately on secondary switch.", "risk_assessment": "LOW"}
            else:
                return {"proposed_action": "SUGGEST_ALTERNATE_METHOD", "confidence": 0.89, "reasoning": f"Attempt {attempt}: Bank switch continues to timeout; offering alternate payment method.", "risk_assessment": "LOW"}

        elif failure_code == "AUTH_FAILURE":
            if attempt == 1:
                action = "SUGGEST_ALTERNATE_METHOD" if tier == "HIGH" else ("RETRY_LATER" if tier == "MEDIUM" else "ESCALATE_HUMAN")
                conf = 0.91 if tier == "HIGH" else (0.85 if tier == "MEDIUM" else 0.88)
            else:
                action = "ESCALATE_HUMAN"
                conf = 0.90
            return {"proposed_action": action, "confidence": conf, "reasoning": f"Auth challenge handling for attempt {attempt}.", "risk_assessment": "LOW"}

        elif failure_code == "GATEWAY_ERROR":
            if attempt == 1:
                action = "RETRY_NOW" if tier in {"HIGH", "MEDIUM"} else "RETRY_LATER"
                conf = 0.93 if tier == "HIGH" else 0.88
            else:
                action = "ESCALATE_HUMAN"
                conf = 0.90
            return {"proposed_action": action, "confidence": conf, "reasoning": f"Gateway error handling attempt {attempt}.", "risk_assessment": "LOW"}

        elif failure_code == "FRAUD_REJECTION":
            return {"proposed_action": "STOP", "confidence": 0.99, "reasoning": "Fraud flag is non-negotiable. Hard stop all recovery actions.", "risk_assessment": "HIGH"}

        return {"proposed_action": "RETRY_NOW", "confidence": 0.85, "reasoning": "Default payment strategy.", "risk_assessment": "LOW"}

    def _handle_b2b_strategy(self, prompt: str, tier: str, attempt: int) -> dict[str, Any]:
        has_broken_promise = "broken" in prompt.lower() or "breached" in prompt.lower() or "status: broken" in prompt.lower()

        # Re-proposal prompt handling
        if "RE-PROPOSAL REQUEST" in prompt:
            return {
                "proposed_action": "ESCALATE_HUMAN",
                "confidence": 0.90,
                "reasoning": "Re-proposing safe escalation to human dispute/collections queue following compliance gate rejection.",
                "risk_assessment": "LOW",
            }

        # Category detection
        category = "CASH_FLOW_MISMATCH"
        if "DISPUTED_DELIVERABLE" in prompt:
            category = "DISPUTED_DELIVERABLE"
        elif "ADMINISTRATIVE_DELAY" in prompt:
            category = "ADMINISTRATIVE_DELAY"
        elif "CHRONIC_DELINQUENCY" in prompt:
            category = "CHRONIC_DELINQUENCY"
        elif "COMMUNICATION_BREAKDOWN" in prompt:
            category = "COMMUNICATION_BREAKDOWN"

        # 1. Disputed deliverable -> always ESCALATE_HUMAN
        if category == "DISPUTED_DELIVERABLE":
            return {
                "proposed_action": "ESCALATE_HUMAN",
                "confidence": 0.98,
                "reasoning": "Active dispute requires human dispute resolution queue routing.",
                "risk_assessment": "LOW",
            }

        # 2. Broken Promise Matrix (Stress Test Scenario 5)
        if has_broken_promise:
            if tier == "HIGH":
                action = "ESCALATE_TONE" if attempt == 1 else "OFFER_PAYMENT_PLAN"
                reason = f"High-tier debtor missed promise (touchpoint {attempt}): Firm tone — debtor missed the agreed payment date."
            elif tier == "MEDIUM":
                action = "ESCALATE_TONE" if attempt == 1 else "ESCALATE_HUMAN"
                reason = f"Medium-tier debtor missed agreed payment date (touchpoint {attempt}): Firm escalation."
            else:
                action = "ESCALATE_HUMAN"
                reason = "Low-tier debtor missed promise: Handover to collections queue."
            return {"proposed_action": action, "confidence": 0.90, "reasoning": reason, "risk_assessment": "LOW"}

        # 3. Clean Debtor Matrix
        if category == "ADMINISTRATIVE_DELAY":
            if tier in {"HIGH", "MEDIUM"}:
                action = "SEND_REMINDER" if attempt <= 2 else "ESCALATE_TONE"
                reason = f"Administrative approval lag (touchpoint {attempt}): Sending professional reminder with invoice copy."
            else:
                action = "SEND_REMINDER" if attempt == 1 else "ESCALATE_HUMAN"
                reason = f"Low-tier AP delay (touchpoint {attempt})."
            return {"proposed_action": action, "confidence": 0.92, "reasoning": reason, "risk_assessment": "LOW"}

        elif category == "CASH_FLOW_MISMATCH":
            if tier == "HIGH":
                action = "OFFER_PAYMENT_PLAN" if attempt <= 2 else "ESCALATE_TONE"
                reason = f"High-tier cash flow mismatch (touchpoint {attempt}): Offering structured installment schedule."
            elif tier == "MEDIUM":
                action = "OFFER_PAYMENT_PLAN" if attempt == 1 else "ESCALATE_TONE"
                reason = f"Medium-tier cash flow mismatch (touchpoint {attempt})."
            else:
                action = "SEND_REMINDER" if attempt == 1 else "ESCALATE_HUMAN"
                reason = f"Low-tier cash flow mismatch (touchpoint {attempt})."
            return {"proposed_action": action, "confidence": 0.89, "reasoning": reason, "risk_assessment": "LOW"}

        elif category == "CHRONIC_DELINQUENCY":
            if tier == "HIGH":
                action = "OFFER_PAYMENT_PLAN" if attempt == 1 else "ESCALATE_TONE"
            elif tier == "MEDIUM":
                action = "ESCALATE_TONE" if attempt == 1 else "ESCALATE_HUMAN"
            else:
                action = "ESCALATE_HUMAN"
            return {"proposed_action": action, "confidence": 0.88, "reasoning": f"Delinquency handling (touchpoint {attempt}).", "risk_assessment": "MEDIUM"}

        elif category == "COMMUNICATION_BREAKDOWN":
            if tier == "HIGH":
                action = "WAIT" if attempt == 1 else "SEND_REMINDER"
            elif tier == "MEDIUM":
                action = "SEND_REMINDER" if attempt == 1 else "ESCALATE_TONE"
            else:
                action = "ESCALATE_TONE" if attempt == 1 else "ESCALATE_HUMAN"
            return {"proposed_action": action, "confidence": 0.86, "reasoning": f"Unresponsive debtor handling (touchpoint {attempt}).", "risk_assessment": "LOW"}

        return {"proposed_action": "SEND_REMINDER", "confidence": 0.88, "reasoning": "Standard reminder.", "risk_assessment": "LOW"}


def _clean_gemini_schema(schema: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Sanitize OpenAPI schema dict for Google Gemini API compatibility.
    Gemini structured output supports type, properties, required, and enum,
    but rejects validation bounds (minimum, maximum, format).
    """
    if not schema:
        return None
    UNSUPPORTED = {"minimum", "maximum", "minLength", "maxLength", "pattern", "default", "format"}
    cleaned: dict[str, Any] = {}
    for k, v in schema.items():
        if k in UNSUPPORTED:
            continue
        if isinstance(v, dict):
            cleaned[k] = _clean_gemini_schema(v)
        elif isinstance(v, list):
            cleaned[k] = [_clean_gemini_schema(item) if isinstance(item, dict) else item for item in v]
        else:
            cleaned[k] = v
    return cleaned


class GeminiLLMClient(LLMClient):
    """Live Google Gemini API client with caching and structured output."""

    def __init__(self, model_name: Optional[str] = None, api_key: Optional[str] = None):
        self.model_name = model_name or config.GEMINI_MODEL
        self.api_key = api_key or os.environ.get(config.GEMINI_API_KEY_ENV)
        self.cache_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
            "llm_cache.json",
        )
        self._cache: dict[str, Any] = self._load_cache()

    def _load_cache(self) -> dict[str, Any]:
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("Failed to load LLM cache: %s", e)
        return {}

    def _save_cache(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2)
        except Exception as e:
            logger.warning("Failed to save LLM cache: %s", e)

    def _get_cache_key(self, prompt: str) -> str:
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def call(self, prompt: str, schema: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        cache_key = self._get_cache_key(prompt)
        if cache_key in self._cache:
            logger.info("Serving LLM response from cache.")
            return self._cache[cache_key]

        if not self.api_key:
            raise LLMError(
                f"Gemini API key missing. Set {config.GEMINI_API_KEY_ENV} env variable "
                "or switch config.LLM_MODE = 'mock'."
            )

        try:
            import google.generativeai as genai
            genai.configure(api_key=self.api_key)

            gen_config: dict[str, Any] = {"response_mime_type": "application/json"}
            cleaned_schema = _clean_gemini_schema(schema)
            if cleaned_schema:
                gen_config["response_schema"] = cleaned_schema

            model = genai.GenerativeModel(
                model_name=self.model_name,
                generation_config=gen_config,
            )

            response = model.generate_content(prompt)
            result = json.loads(response.text)
            self._cache[cache_key] = result
            self._save_cache()
            return result
        except Exception as e:
            # Sanitize API key from error string before logging or re-raising.
            # Some API error responses include the request key in their body;
            # strip it to avoid accidental key exposure in logs.
            _key = self.api_key or ""
            err_str = str(e).replace(_key, "[REDACTED]") if _key else str(e)
            if "429" in err_str or "quota" in err_str.lower():
                import re
                import time
                delay_match = re.search(r"retry in ([\d\.]+)s", err_str) or re.search(r"seconds:\s*(\d+)", err_str)
                delay = float(delay_match.group(1)) + 2.0 if delay_match else 40.0
                logger.warning("Gemini 429 Rate Limit encountered. Waiting %.1fs for quota window reset...", delay)
                time.sleep(delay)
                try:
                    response = model.generate_content(prompt)
                    result = json.loads(response.text)
                    self._cache[cache_key] = result
                    self._save_cache()
                    return result
                except Exception as retry_e:
                    safe_retry_err = str(retry_e).replace(_key, "[REDACTED]") if _key else str(retry_e)
                    raise LLMError(f"Gemini API call failed after rate-limit backoff: {safe_retry_err}") from retry_e
            raise LLMError(f"Gemini API call failed: {err_str}") from e


def get_llm_client(mode: Optional[str] = None) -> LLMClient:
    """Factory to get the appropriate LLM client based on configuration."""
    active_mode = mode or config.LLM_MODE
    if active_mode.lower() == "live":
        return GeminiLLMClient()
    return MockLLMClient()
