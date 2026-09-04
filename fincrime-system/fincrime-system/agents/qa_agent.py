"""
Case QA Agent -- reviews ALREADY-CLOSED investigations, not live cases.

This is the "QA of Completed Cases" pattern -- widely regarded as the
lowest-risk AI use case in financial-crime investigation practice,
because it augments a human auditor's review rather than gating or
replacing any live decision. Deliberately distinct from
agents/verification_agent.py: verification runs BEFORE a case closes and
can override the outcome; this agent runs AFTER, on a sample of closed
cases, and only ever produces a recommendation for a human auditor to
look at -- it never reopens or changes a case itself.
"""

REQUIRED_REPORT_FIELDS = ["case_id", "account_id", "matched_typology", "confidence",
                          "severity", "evidence_sources", "recommended_action", "model_rule_versions"]

# In-memory stats counters (reset on restart)
from collections import Counter
from agents import llm_client
import json

_stats = {"total_reviewed": 0, "flagged_count": 0, "issue_counter": Counter()}


def qa_review_case(case_state: dict) -> dict:
    """case_state: the full dict returned by orchestrator.investigate_account()."""
    issues = []
    report = case_state.get("report", {})
    evidence = case_state.get("evidence", {})
    risk = case_state.get("risk", {})
    verification = case_state.get("verification", {})

    # 1. Documentation completeness
    missing_fields = [f for f in REQUIRED_REPORT_FIELDS if not report.get(f) and report.get(f) != 0]
    if missing_fields:
        issues.append(f"Documentation gap: report missing or empty fields {missing_fields}")

    # 2. A matched typology with no supporting evidence on record
    if risk.get("matched_typology") not in (None, "NOVEL_PATTERN") and not evidence.get("flags"):
        issues.append("Case matched a typology but the evidence packet has no supporting flags on record")

    # 3. Weak conclusion: high severity that didn't escalate or block
    if risk.get("severity", 0) >= 8 and report.get("recommended_action") not in \
            ("ESCALATE_TO_HUMAN_REVIEW", "BLOCK_PENDING_REVIEW"):
        issues.append(f"Severity {risk.get('severity')} case did not escalate or block -- "
                       f"weak conclusion for this risk level")

    # 4. Verification agent found a problem that isn't reflected in the final report
    if verification.get("result") == "FAIL" and "verification_override" not in report:
        issues.append("Verification agent found issues but the report shows no record of the override")

    # 5. Auto-closed as customer-confirmed with no completed call on record
    if report.get("recommended_action") == "AUTO_CLOSED_CUSTOMER_CONFIRMED":
        call_result = case_state.get("call_result")
        if not call_result or call_result.get("status") != "complete":
            issues.append("Case was auto-closed as customer-confirmed but no completed call "
                           "transcript is on record -- documentation problem")

    if llm_client.llm_available():
        prompt = f"Review this case report for financial crime investigation: {json.dumps(report)}. Verification result: {verification.get('result')}. Identify any missing logic, contradictory claims, or poor documentation in 1-2 short bullet points. If everything is well documented and consistent, reply 'NONE'."
        try:
            llm_issues = llm_client.call_llm("You are a QA auditor.", prompt)
            if llm_issues and "NONE" not in llm_issues:
                issues.append(f"LLM QA flagged: {llm_issues.strip()}")
        except Exception:
            pass

    qa_result = "FLAGGED_FOR_HUMAN_AUDIT" if issues else "PASS"

    # Track stats
    _stats["total_reviewed"] += 1
    if qa_result == "FLAGGED_FOR_HUMAN_AUDIT":
        _stats["flagged_count"] += 1
        for issue in issues:
            _stats["issue_counter"][issue[:60]] += 1

    return {
        "case_id": case_state.get("case_id"),
        "account_id": case_state.get("account_id"),
        "qa_result": qa_result,
        "issues": issues,
        "note": "Post-hoc quality check on an already-closed case -- flagged cases go to a human "
                "auditor for review; this agent never reopens or changes a case on its own.",
    }



def qa_review_batch(case_states: list) -> dict:
    """Run QA review across a sample of closed cases -- this is the periodic sampling function,
    same operating pattern as the red-team agent (runs on a schedule, not per-live-case)."""
    results = [qa_review_case(c) for c in case_states]
    flagged = [r for r in results if r["qa_result"] == "FLAGGED_FOR_HUMAN_AUDIT"]
    return {
        "total_reviewed": len(results),
        "flagged_count": len(flagged),
        "flagged_rate": round(len(flagged) / len(results), 3) if results else 0.0,
        "flagged_cases": flagged,
    }


def get_stats() -> dict:
    """Return QA review statistics for the visual interface."""
    total = _stats["total_reviewed"]
    top_issues = [{"issue": k, "count": v} for k, v in _stats["issue_counter"].most_common(5)]
    return {
        "total_reviewed": total,
        "flagged_count": _stats["flagged_count"],
        "flagged_rate": round(_stats["flagged_count"] / total, 3) if total else 0.0,
        "top_issues": top_issues,
    }
