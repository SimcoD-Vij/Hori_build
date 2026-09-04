"""Verification agent -- an independent second pass. Its FAIL always overrides earlier agents' conclusions."""
from collections import Counter

# In-memory stats counters (reset on restart)
_stats = {"pass_count": 0, "fail_count": 0, "issue_counter": Counter()}


def verify(report: dict, evidence: dict, risk: dict) -> dict:
    issues = []

    # 1. Every evidence source cited in the report must actually exist in the evidence packet.
    for src in report["evidence_sources"]:
        if src not in evidence["sources"]:
            issues.append(f"Report cites source '{src}' not present in evidence packet")

    # 2. Severity vs recommended action consistency.
    if risk["severity"] >= 8 and report["recommended_action"] == "MONITOR":
        issues.append(f"Severity {risk['severity']} is high but recommended action is only MONITOR -- inconsistent")

    # 3. NOVEL_PATTERN should never auto-close.
    if risk["matched_typology"] == "NOVEL_PATTERN" and report["recommended_action"] == "AUTO_CLOSED_CUSTOMER_CONFIRMED":
        issues.append("Novel/unmatched pattern was auto-closed -- this must always route to human review instead")

    # 4. Confidence sanity check.
    if risk["confidence"] > 0 and not report["evidence_detail"]:
        issues.append("Risk assessment has nonzero confidence but no supporting evidence detail was captured")

    result = "FAIL" if issues else "PASS"

    # Track stats
    if result == "PASS":
        _stats["pass_count"] += 1
    else:
        _stats["fail_count"] += 1
        for issue in issues:
            # Bucket by the first ~40 chars as a category key
            key = issue[:60]
            _stats["issue_counter"][key] += 1

    return {"result": result, "issues": issues,
            "note": "FAIL routes to human review regardless of what earlier agents concluded." if issues else "No inconsistencies found."}


def get_stats() -> dict:
    """Return verification pass/fail statistics for the visual interface."""
    total = _stats["pass_count"] + _stats["fail_count"]
    top_reasons = [{"reason": k, "count": v} for k, v in _stats["issue_counter"].most_common(5)]
    return {
        "pass_count": _stats["pass_count"],
        "fail_count": _stats["fail_count"],
        "total_checks": total,
        "fail_rate": round(_stats["fail_count"] / total, 3) if total else 0.0,
        "top_failure_reasons": top_reasons,
    }

