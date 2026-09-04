"""Explanation agent -- builds the client-facing, audit-ready report. Every field traces to evidence or risk_assessment; nothing invented."""
from datetime import datetime
from collections import Counter
from agents import llm_client
import json

# In-memory stats counters (reset on restart)
_stats = {"action_breakdown": Counter(), "draft_count": 0, "total_reports": 0}


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
        "status": "DRAFT_NOT_FILED",  # EU AI Act / SR 11-7: reports are drafts until filed by a human investigator
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
        "executive_summary": "Draft summary based on matching rules.", # default
        "model_rule_versions": {"rules_engine": "v1.0", "typology_catalog": "v1.0", "supervised_model": "RandomForest-v1"},
    }

    if llm_client.llm_available():
        prompt = f"Write a concise executive summary for case {case_id}. Account {evidence['account_id']} matched typology '{risk['matched_typology']}' with {risk['confidence']} confidence and {risk['severity']} severity. Recommended action is {action}. Flags: {json.dumps([f['rule'] for f in evidence['flags']])}."
        try:
            llm_summary = llm_client.call_llm("You are a financial crime investigator drafting an executive summary.", prompt)
            if llm_summary:
                report["executive_summary"] = llm_summary.strip()
        except Exception:
            pass

    # Track stats
    _stats["action_breakdown"][action] += 1
    _stats["draft_count"] += 1
    _stats["total_reports"] += 1

    return report


def get_stats() -> dict:
    """Return explanation agent statistics for the visual interface."""
    return {
        "action_breakdown": dict(_stats["action_breakdown"]),
        "draft_count": _stats["draft_count"],
        "total_reports": _stats["total_reports"],
    }

