"""Explanation agent -- builds the client-facing, audit-ready report. Every field traces to evidence or risk_assessment; nothing invented."""
from datetime import datetime


def generate_report(case_id: str, evidence: dict, risk: dict, call_result: dict = None) -> dict:
    action = "MONITOR"
    if risk["severity"] >= 8 or risk["matched_typology"] == "NOVEL_PATTERN":
        action = "ESCALATE_TO_HUMAN_REVIEW"
    elif risk["severity"] >= 6:
        action = "BLOCK_PENDING_REVIEW"

    if call_result and call_result.get("auto_close_eligible"):
        action = "AUTO_CLOSED_CUSTOMER_CONFIRMED"

    report = {
        "case_id": case_id,
        "generated_at": datetime.now().isoformat(),
        "account_id": evidence["account_id"],
        "flagged_pattern": ", ".join(f["rule"] for f in evidence["flags"]) or "none",
        "matched_typology": risk["matched_typology"],
        "confidence": risk["confidence"],
        "severity": risk["severity"],
        "evidence_sources": evidence["sources"],
        "evidence_detail": [f["evidence"] for f in evidence["flags"]],
        "call_summary": call_result["classification"] if call_result and call_result.get("status") == "complete" else "not applicable / not yet conducted",
        "recommended_action": action,
        "model_rule_versions": {"rules_engine": "v1.0", "typology_catalog": "v1.0", "supervised_model": "RandomForest-v1"},
    }
    return report
