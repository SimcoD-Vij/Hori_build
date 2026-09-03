"""
Enforcement layer -- where a decision from realtime/pretransaction_screening.py
would translate into an actual action against a real transaction (holding
funds, cancelling a transfer, releasing it to proceed).

CURRENT STATE: DELIBERATELY INACTIVE. Every real enforcement call below is
commented out. Calling these functions right now only LOGS what the system
WOULD have done and returns enforced=False -- no real transaction is ever
touched. This is intentional: the system is in prediction-only mode until
you decide to wire it into a real core-banking/payment-rail integration.

Why this file exists separately from pretransaction_screening.py: keeping
prediction (deciding ALLOW/HOLD/BLOCK) and enforcement (actually doing
something about it) in different files means you can fully trust and
demo the prediction logic without any risk of it accidentally taking a
real action -- there is no live code path from a screening decision to a
real-world effect unless you deliberately uncomment something below.

TO ENABLE LATER: uncomment the marked blocks inside each function and
point CORE_BANKING_API at your real transaction system's endpoint.
"""
import os

CORE_BANKING_API = os.environ.get("CORE_BANKING_API", "http://localhost:9000")
ENFORCEMENT_ENABLED = os.environ.get("ENFORCEMENT_ENABLED", "false").lower() == "true"


def hold_transaction(transaction_id: str, reason: str) -> dict:
    """Would place a temporary hold on a transaction pending calling-agent verification."""
    if not ENFORCEMENT_ENABLED:
        print(f"[PREDICTION ONLY -- no action taken] Would HOLD transaction {transaction_id}: {reason}")
        return {"enforced": False, "would_have": "HOLD", "transaction_id": transaction_id, "reason": reason}

    # ------------------------------------------------------------------
    # UNCOMMENT TO ENABLE REAL ENFORCEMENT:
    #
    # import requests
    # response = requests.post(
    #     f"{CORE_BANKING_API}/transactions/{transaction_id}/hold",
    #     json={"reason": reason},
    #     timeout=10,
    # )
    # response.raise_for_status()
    # return {"enforced": True, "action": "HOLD", "transaction_id": transaction_id, "api_response": response.json()}
    # ------------------------------------------------------------------
    raise NotImplementedError("ENFORCEMENT_ENABLED is true but the real API call above is still commented out.")


def block_transaction(transaction_id: str, reason: str) -> dict:
    """Would cancel/reverse a transaction outright -- the BLOCK decision's real-world action."""
    if not ENFORCEMENT_ENABLED:
        print(f"[PREDICTION ONLY -- no action taken] Would BLOCK transaction {transaction_id}: {reason}")
        return {"enforced": False, "would_have": "BLOCK", "transaction_id": transaction_id, "reason": reason}

    # ------------------------------------------------------------------
    # UNCOMMENT TO ENABLE REAL ENFORCEMENT:
    #
    # import requests
    # response = requests.post(
    #     f"{CORE_BANKING_API}/transactions/{transaction_id}/block",
    #     json={"reason": reason},
    #     timeout=10,
    # )
    # response.raise_for_status()
    # return {"enforced": True, "action": "BLOCK", "transaction_id": transaction_id, "api_response": response.json()}
    # ------------------------------------------------------------------
    raise NotImplementedError("ENFORCEMENT_ENABLED is true but the real API call above is still commented out.")


def allow_transaction(transaction_id: str) -> dict:
    """Would explicitly release a held transaction to proceed. Included for symmetry and audit
    completeness -- most core banking systems default to ALLOW and don't need an explicit call,
    but a HOLD that gets resolved to ALLOW usually does need one to release the hold."""
    if not ENFORCEMENT_ENABLED:
        print(f"[PREDICTION ONLY -- no action taken] Would ALLOW transaction {transaction_id} to proceed")
        return {"enforced": False, "would_have": "ALLOW", "transaction_id": transaction_id}

    # ------------------------------------------------------------------
    # UNCOMMENT TO ENABLE REAL ENFORCEMENT:
    #
    # import requests
    # response = requests.post(
    #     f"{CORE_BANKING_API}/transactions/{transaction_id}/release",
    #     timeout=10,
    # )
    # response.raise_for_status()
    # return {"enforced": True, "action": "ALLOW", "transaction_id": transaction_id, "api_response": response.json()}
    # ------------------------------------------------------------------
    raise NotImplementedError("ENFORCEMENT_ENABLED is true but the real API call above is still commented out.")


def enforce_decision(transaction_id: str, decision: dict) -> dict:
    """Single entry point -- routes a screening/resolution decision to the right enforcement
    function. This is what main.py should call; it never needs to know which specific function
    handles which decision."""
    action = decision.get("decision")
    reason = decision.get("reasoning", "")
    if action == "BLOCK":
        return block_transaction(transaction_id, reason)
    if action == "HOLD_FOR_VERIFICATION":
        return hold_transaction(transaction_id, reason)
    if action == "ALLOW":
        return allow_transaction(transaction_id)
    raise ValueError(f"Unknown decision type: {action}")
