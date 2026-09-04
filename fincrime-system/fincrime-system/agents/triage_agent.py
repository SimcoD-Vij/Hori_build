"""
Triage agent. Decides FRAUD_BRANCH (safe to call the customer) vs
AML_BRANCH (calling would be illegal tipping-off) and sets priority.
Defaults to AML_BRANCH under any ambiguity -- this is a hard safety
rule, not a preference, and is never bypassable by a downstream agent.
"""
import json
from agents import llm_client

FRAUD_BRANCH_RULES = {"zscore_self_history", "supervised_ml", "velocity", "evolvegcn_temporal"}
AML_ONLY_RULES = {"structuring", "graph_fan_hub", "graph_round_trip", "synthetic_identity_ring",
                   "peer_group_deviation", "unsupervised_novel_pattern", "bust_out"}

# In-memory stats counters (reset on restart)
_stats = {"fraud_branch_count": 0, "aml_branch_count": 0, "ambiguous_defaults": 0, "total_cases": 0}


def triage(flags: list) -> dict:
    rules_hit = {f["rule"] for f in flags}
    max_severity = max((f.get("severity_hint", 0) for f in flags), default=0)

    if rules_hit & AML_ONLY_RULES:
        branch = "AML_BRANCH"
        reasoning = f"Matched AML-typology rule(s): {sorted(rules_hit & AML_ONLY_RULES)} -- customer contact " \
                    f"would risk illegal tipping-off. Routing to silent investigation."
        _stats["aml_branch_count"] += 1
    elif rules_hit & FRAUD_BRANCH_RULES and not (rules_hit - FRAUD_BRANCH_RULES):
        branch = "FRAUD_BRANCH"
        reasoning = f"Only fraud-verification-type rule(s) matched: {sorted(rules_hit)} -- safe to contact " \
                    f"the account holder directly for confirmation."
        _stats["fraud_branch_count"] += 1
    else:
        branch = "AML_BRANCH"
        reasoning = "Ambiguous or mixed signal -- defaulting to AML_BRANCH per hard safety rule (never contact under uncertainty)."
        _stats["ambiguous_defaults"] += 1
        _stats["aml_branch_count"] += 1

    _stats["total_cases"] += 1
    priority = min(5, max(1, round(max_severity / 2)))

    if llm_client.llm_available():
        prompt = f"Given these transaction rules: {sorted(rules_hit)} for this case, explain why it was routed to {branch} with priority {priority}. Provide a concise 2-sentence explanation."
        try:
            llm_reasoning = llm_client.call_llm("You are a financial crime triage expert.", prompt)
            if llm_reasoning:
                reasoning = llm_reasoning.strip()
        except Exception:
            pass

    return {"branch": branch, "priority": priority, "reasoning": reasoning, "matched_rules": sorted(rules_hit)}


def get_stats() -> dict:
    """Return triage routing statistics for the visual interface."""
    return dict(_stats)

