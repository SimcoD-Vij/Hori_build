"""
Triage agent. Decides FRAUD_BRANCH (safe to call the customer) vs
AML_BRANCH (calling would be illegal tipping-off) and sets priority.
Defaults to AML_BRANCH under any ambiguity -- this is a hard safety
rule, not a preference, and is never bypassable by a downstream agent.
"""
import json

FRAUD_BRANCH_RULES = {"zscore_self_history", "supervised_ml", "velocity", "evolvegcn_temporal"}
AML_ONLY_RULES = {"structuring", "graph_fan_hub", "graph_round_trip", "synthetic_identity_ring",
                   "peer_group_deviation", "unsupervised_novel_pattern", "bust_out"}


def triage(flags: list) -> dict:
    rules_hit = {f["rule"] for f in flags}
    max_severity = max((f.get("severity_hint", 0) for f in flags), default=0)

    if rules_hit & AML_ONLY_RULES:
        branch = "AML_BRANCH"
        reasoning = f"Matched AML-typology rule(s): {sorted(rules_hit & AML_ONLY_RULES)} -- customer contact " \
                    f"would risk illegal tipping-off. Routing to silent investigation."
    elif rules_hit & FRAUD_BRANCH_RULES and not (rules_hit - FRAUD_BRANCH_RULES):
        branch = "FRAUD_BRANCH"
        reasoning = f"Only fraud-verification-type rule(s) matched: {sorted(rules_hit)} -- safe to contact " \
                    f"the account holder directly for confirmation."
    else:
        branch = "AML_BRANCH"
        reasoning = "Ambiguous or mixed signal -- defaulting to AML_BRANCH per hard safety rule (never contact under uncertainty)."

    priority = min(5, max(1, round(max_severity / 2)))
    return {"branch": branch, "priority": priority, "reasoning": reasoning, "matched_rules": sorted(rules_hit)}
