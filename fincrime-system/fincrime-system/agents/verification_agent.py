"""Verification agent -- an independent second pass. Its FAIL always overrides earlier agents' conclusions."""


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
    return {"result": result, "issues": issues,
            "note": "FAIL routes to human review regardless of what earlier agents concluded." if issues else "No inconsistencies found."}
