"""
Real-time pre-transaction screening.

Everything else in this system is retrospective: a transaction happens,
lands in the dataset, then gets flagged on the next detection run. This
module is different -- it evaluates a CANDIDATE transaction BEFORE it is
committed, using the account's existing history, and returns a decision:

  ALLOW                -- let it through immediately
  HOLD_FOR_VERIFICATION -- pause it, contact the customer (FRAUD_BRANCH only)
  BLOCK                -- refuse it, escalate silently (AML_BRANCH or high severity)

This is prediction, not just detection: it answers "should this transaction
be allowed to happen" rather than "did something suspicious already happen."

Methodology: reuses the same rule/statistical/graph primitives as the
batch detection layer (no new unproven math -- this is the honest,
defensible approach: apply already-validated detectors to a
one-transaction-ahead simulation, rather than inventing a separate
"prediction model" with no track record). Severity aggregation follows
the same severity_hint scale used everywhere else in the system, so a
human reading a screening decision and a case report can reason about
both with the same mental model.
"""
import pandas as pd
import numpy as np

AML_ONLY_PATTERNS = {"structuring", "graph_fan_hub", "graph_round_trip",
                      "synthetic_identity_ring", "peer_group_deviation"}


def _check_structuring_risk(candidate: dict, history: pd.DataFrame, account_id: str, threshold=10000, window_days=30):
    """Would adding this transaction complete or extend a near-threshold clustering pattern?
    Deposits are recorded with this account as RECEIVER (e.g. sender_account="CASH") -- so this
    check must look at receiver-side history, not the full mixed history."""
    if candidate.get("method") != "cash_deposit":
        return None
    amt = candidate["amount"]
    if not (threshold * 0.9 <= amt < threshold):
        return None
    deposit_history = history[history.get("receiver_account", pd.Series(dtype=str)) == account_id] \
        if "receiver_account" in history.columns else history
    recent_similar = deposit_history[
        (deposit_history["method"] == "cash_deposit") &
        (deposit_history["amount"] >= threshold * 0.9) & (deposit_history["amount"] < threshold)
    ]
    count_with_candidate = len(recent_similar) + 1
    if count_with_candidate >= 3:
        return {
            "rule": "structuring", "severity_hint": min(10, 5 + count_with_candidate),
            "evidence": f"This transaction would be the {count_with_candidate}th near-threshold "
                        f"deposit (${amt:,.2f}) for this account in the observed window.",
        }
    return None


def _check_self_history_deviation(candidate: dict, history: pd.DataFrame, account_id: str, z_threshold=3.0):
    """Would this transaction be a statistical outlier against the account's own OUTGOING pattern?
    Filtered to sender-side history only -- mixing in incoming deposits would compare two
    different distributions (spending vs. receiving) and distort the baseline. This filter was
    added after testing surfaced exactly this distortion on real account data."""
    outgoing_history = history[history.get("sender_account", pd.Series(dtype=str)) == account_id] \
        if "sender_account" in history.columns else history
    if len(outgoing_history) < 3:
        if candidate["amount"] >= 30000:
            return {
                "rule": "zscore_self_history", "severity_hint": 5,
                "evidence": f"${candidate['amount']:,.2f} transaction with insufficient outgoing "
                            f"history to establish a baseline -- treated as high-risk by default.",
            }
        return None
    mean, std = outgoing_history["amount"].mean(), outgoing_history["amount"].std()
    if std == 0 or np.isnan(std):
        return None
    z = (candidate["amount"] - mean) / std
    if abs(z) >= z_threshold:
        return {
            "rule": "zscore_self_history", "severity_hint": min(10, int(abs(z))),
            "evidence": f"${candidate['amount']:,.2f} is {z:.1f} std devs from this account's own "
                        f"outgoing average (${mean:,.2f}).",
        }
    return None


def _check_velocity_risk(candidate: dict, history: pd.DataFrame, account_id: str, max_txns=8, window_hours=24):
    """Would this transaction push the account's OUTGOING activity over a velocity threshold?"""
    outgoing_history = history[history.get("sender_account", pd.Series(dtype=str)) == account_id] \
        if "sender_account" in history.columns else history
    recent = outgoing_history[pd.to_datetime(outgoing_history["timestamp"]) >=
                               (pd.Timestamp.now() - pd.Timedelta(hours=window_hours))]
    count_with_candidate = len(recent) + 1
    if count_with_candidate >= max_txns:
        return {
            "rule": "velocity", "severity_hint": min(10, 4 + count_with_candidate // 2),
            "evidence": f"This would be transaction #{count_with_candidate} for this account "
                        f"in the last {window_hours}h.",
        }
    return None


def screen_transaction(candidate: dict, account_history: pd.DataFrame, account_profile: dict = None) -> dict:
    """
    Main entry point. candidate: dict with sender_account (the account being screened --
    used as the identity key regardless of whether this specific transaction is technically
    incoming or outgoing for that account), amount, method, timestamp (proposed).
    account_history: this account's PAST transactions only, BOTH directions (never include
    the candidate itself in history -- that would be leaking the answer). Each check below
    filters to the direction that's actually meaningful for what it's testing.
    """
    account_id = candidate.get("sender_account") or candidate.get("account_id")
    if account_history.empty:
        account_history = pd.DataFrame(columns=["amount", "method", "timestamp", "sender_account", "receiver_account"])

    checks = [
        _check_structuring_risk(candidate, account_history, account_id),
        _check_self_history_deviation(candidate, account_history, account_id),
        _check_velocity_risk(candidate, account_history, account_id),
    ]
    hits = [c for c in checks if c is not None]

    if not hits:
        return {
            "decision": "ALLOW", "severity": 0, "matched_patterns": [],
            "reasoning": "No rule, statistical, or velocity risk detected against this account's history.",
        }

    max_severity = max(h["severity_hint"] for h in hits)
    patterns = {h["rule"] for h in hits}
    is_aml_pattern = bool(patterns & AML_ONLY_PATTERNS)

    if is_aml_pattern or max_severity >= 8:
        return {
            "decision": "BLOCK", "severity": max_severity, "matched_patterns": sorted(patterns),
            "reasoning": "High-severity or AML-typology pattern detected -- blocked and escalated "
                         "silently. Customer contact is never used to resolve a BLOCK decision, "
                         "since an AML-pattern match means contact could constitute tipping-off.",
            "evidence": [h["evidence"] for h in hits],
        }

    return {
        "decision": "HOLD_FOR_VERIFICATION", "severity": max_severity, "matched_patterns": sorted(patterns),
        "reasoning": "Moderate-severity fraud-type pattern detected. Held pending customer "
                     "verification via the calling agent -- not blocked outright, since this "
                     "pattern type is safe to confirm directly with the account holder.",
        "evidence": [h["evidence"] for h in hits],
    }


def resolve_hold(screening_result: dict, call_classification: str) -> dict:
    """
    Takes a HOLD_FOR_VERIFICATION result and the calling agent's classification, returns
    the final decision. This is the "agent in call decides whether to allow the transaction"
    logic -- the call's outcome, not a second guess by another model, resolves the hold.
    """
    if screening_result["decision"] != "HOLD_FOR_VERIFICATION":
        raise ValueError("resolve_hold() only applies to HOLD_FOR_VERIFICATION decisions -- "
                          "a BLOCK decision is never reopened by a phone call.")

    if call_classification == "satisfactory":
        return {
            "decision": "ALLOW", "resolved_by": "calling_agent_confirmation",
            "reasoning": "Customer confirmed the transaction with specific, plausible detail. "
                         "Original hold lifted.",
        }
    return {
        "decision": "BLOCK", "resolved_by": "calling_agent_unsatisfactory_or_no_response",
        "reasoning": f"Customer response was classified '{call_classification}' -- insufficient "
                     f"to lift the hold. Blocked and escalated to human review rather than "
                     f"auto-allowed, per the conservative default (ambiguous or negative "
                     f"responses never auto-clear a transaction).",
    }
