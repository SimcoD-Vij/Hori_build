"""Risk assessment agent: matches evidence against the typology catalog, only citing red flags actually present."""
import json
import os
from agents import llm_client

CATALOG_PATH = os.path.join(os.path.dirname(__file__), "..", "detection", "typology_catalog.json")
with open(CATALOG_PATH) as f:
    TYPOLOGY_CATALOG = json.load(f)

# In-memory stats counters (reset on restart)
_stats = {"typology_breakdown": {}, "total_severity": 0, "total_confidence": 0.0,
          "novel_pattern_count": 0, "total_cases": 0}


def assess_risk(evidence: dict) -> dict:
    present_rules = set(evidence["sources"])
    best_match, best_score = None, 0.0

    # Compute dimension scores: rule_score (deterministic rules), stat_score (z-score/benford), graph_score (graph rules)
    RULE_RULES = {"structuring", "velocity", "bust_out", "zscore_self_history"}
    GRAPH_RULES = {"graph_fan_hub", "graph_round_trip", "synthetic_identity_ring"}
    STAT_RULES = {"peer_group_deviation", "unsupervised_novel_pattern", "supervised_ml", "evolvegcn_temporal"}

    rule_score = round(len(present_rules & RULE_RULES) / max(len(RULE_RULES), 1), 2)
    graph_score = round(len(present_rules & GRAPH_RULES) / max(len(GRAPH_RULES), 1), 2)
    stat_score = round(len(present_rules & STAT_RULES) / max(len(STAT_RULES), 1), 2)

    for typ in TYPOLOGY_CATALOG:
        red_flags = set(typ["red_flags"])
        overlap = present_rules & red_flags
        if not red_flags:
            continue
        score = len(overlap) / len(red_flags)
        if score > best_score:
            best_score, best_match = score, typ

    if best_match is None or best_score < 0.4:
        matched_typology = "NOVEL_PATTERN"
        confidence = 0.0
        branch_hint = "AML_BRANCH"
        matched_flags = []
        _stats["novel_pattern_count"] += 1
    else:
        matched_typology = best_match["typology"]
        confidence = round(best_score, 2)
        branch_hint = best_match["branch"]
        matched_flags = sorted(present_rules & set(best_match["red_flags"]))

    severity = min(10, max((f.get("severity_hint", 0) for f in evidence["flags"]), default=0))

    # Track stats
    _stats["typology_breakdown"][matched_typology] = _stats["typology_breakdown"].get(matched_typology, 0) + 1
    _stats["total_severity"] += severity
    _stats["total_confidence"] += confidence
    _stats["total_cases"] += 1

    reasoning = (
        f"Evidence shows {len(evidence['flags'])} flag(s) from source(s) {sorted(present_rules)}. "
        f"{'No confident typology match -- routed as a novel pattern for fresh human review.' if matched_typology == 'NOVEL_PATTERN' else f'Best match: {matched_typology} (confidence {confidence}), supported by {matched_flags}.'}"
    )

    if llm_client.llm_available():
        prompt = f"Given the following evidence flags for account {evidence['account_id']}: {json.dumps(evidence['flags'])}\n\nMatched Typology: {matched_typology}\n\nProvide a 2-3 sentence risk assessment reasoning explaining why this typology matches the evidence."
        try:
            llm_reasoning = llm_client.call_llm("You are a financial crime risk assessment expert. Provide concise reasoning.", prompt)
            if llm_reasoning:
                reasoning = llm_reasoning.strip()
        except Exception as e:
            pass # fallback to template reasoning

    return {
        "account_id": evidence["account_id"],
        "matched_typology": matched_typology,
        "confidence": confidence,
        "matched_red_flags": matched_flags,
        "severity": severity,
        "branch_hint": branch_hint,
        "dimension_scores": {"rule_score": rule_score, "graph_score": graph_score, "stat_score": stat_score},
        "reasoning": reasoning,
    }


def get_stats() -> dict:
    """Return risk assessment statistics for the visual interface."""
    n = _stats["total_cases"]
    return {
        "typology_breakdown": dict(_stats["typology_breakdown"]),
        "avg_confidence": round(_stats["total_confidence"] / n, 3) if n else 0.0,
        "avg_severity": round(_stats["total_severity"] / n, 2) if n else 0.0,
        "novel_pattern_count": _stats["novel_pattern_count"],
        "total_cases": n,
    }

