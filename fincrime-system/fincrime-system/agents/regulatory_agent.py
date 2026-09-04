"""
Regulatory Agent — system-level compliance gate.

This agent does NOT decide anything about fraud or AML cases.
Its job is to verify that the *system itself* is operating within its
documented, audited, human-overseen bounds under applicable law.

Frameworks checked:
  - EU AI Act Annex III / Articles 8-15 (high-risk AI classification)
  - BSA/AML (US): SAR filing within 30 days of detection
  - SR 11-7 (Federal Reserve): model risk management
  - GDPR Article 22 / CCPA: right to human review

Separation of concerns vs. Verification Agent:
  - Verification Agent = case-level logic checker ("does this case's evidence
    support its conclusion?")
  - Regulatory Agent = system-level compliance checker ("is the system itself
    legally operating right now?")
"""
import json
import os
from datetime import datetime, date

SYSTEM_CARD_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "system_card.json")
REDTEAM_HISTORY_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "redteam_history.jsonl")
SAR_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "sar_log.jsonl")

# In-memory stats (reset on restart — use append logs for persistence)
_compliance_checks = {"compliant": 0, "non_compliant": 0}

def _load_system_card() -> dict:
    try:
        with open(SYSTEM_CARD_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def check_compliance(case_state: dict, jurisdiction: str = "global") -> dict:
    """
    Per-case compliance check. Returns a compliance result with any
    issues found. Issues do NOT block the case from closing — they
    create a compliance record that a human compliance officer must review.
    """
    issues = []
    warnings = []
    card = _load_system_card()

    # 1. Was a human meaningfully involved? (EU AI Act Art. 14, GDPR Art. 22)
    report = case_state.get("report") or {}
    call_result = case_state.get("call_result") or {}
    triage = case_state.get("triage") or {}

    hitl_present = (
        call_result.get("status") == "awaiting_human_decision"
        or report.get("recommended_action") == "ESCALATE_TO_HUMAN_REVIEW"
        or report.get("recommended_action") == "BLOCK_PENDING_REVIEW"
        or report.get("recommended_action") == "AUTO_CLOSED_CUSTOMER_CONFIRMED"
    )
    if not hitl_present and triage.get("branch") == "FRAUD_BRANCH":
        issues.append("FRAUD_BRANCH case closed without a documented human oversight step (HITL gate, escalation, or block)")

    # 2. Is the audit record complete? (SR 11-7, EU AI Act Art. 12)
    required_report_keys = ["case_id", "generated_at", "account_id", "matched_typology",
                             "evidence_sources", "recommended_action", "model_rule_versions"]
    missing_keys = [k for k in required_report_keys if not report.get(k)]
    if missing_keys:
        issues.append(f"Audit record incomplete — missing fields: {missing_keys}")

    # 3. Verification agent must have run (SR 11-7 independent validation requirement)
    verification = case_state.get("verification", {})
    if not verification:
        issues.append("Verification agent did not run on this case — SR 11-7 requires independent validation of each decision")

    # 4. Evidence sources cited in report must trace to actual data (no invented evidence)
    evidence = case_state.get("evidence", {})
    for src in report.get("evidence_sources", []):
        if src not in evidence.get("sources", []):
            issues.append(f"Report cites evidence source '{src}' not present in the evidence packet")

    # 5. DRAFT_NOT_FILED status check
    if report.get("status") != "DRAFT_NOT_FILED" and report.get("recommended_action") not in ("AUTO_CLOSED_CUSTOMER_CONFIRMED",):
        warnings.append("Report does not carry DRAFT_NOT_FILED status — ensure human filing before regulatory submission")

    # 6. System card currency check (EU AI Act Art. 9)
    last_review = card.get("last_documentation_review")
    if last_review:
        days_since = (date.today() - date.fromisoformat(last_review)).days
        if days_since > 90:
            warnings.append(f"Technical documentation last reviewed {days_since} days ago — EU AI Act recommends current documentation")

    result = "COMPLIANT" if not issues else "NON_COMPLIANT"
    _compliance_checks[result.lower()] = _compliance_checks.get(result.lower(), 0) + 1

    return {
        "case_id": case_state.get("case_id"),
        "compliance_result": result,
        "issues": issues,
        "warnings": warnings,
        "jurisdiction": jurisdiction,
        "checked_at": datetime.now().isoformat(),
        "frameworks_checked": ["EU AI Act Annex III", "SR 11-7", "GDPR Art. 22"],
        "note": "NON_COMPLIANT flags require compliance officer review — they do not reopen the fraud case itself.",
    }


def track_sar_deadline(case_id: str, detection_date: str) -> dict:
    """
    Track SAR (Suspicious Activity Report) filing deadline.
    BSA/AML (US): SAR must be filed within 30 days of detection.
    Returns days remaining and a RED/AMBER/GREEN status.
    """
    try:
        detected = date.fromisoformat(detection_date[:10])
        deadline = detected.replace(day=detected.day)  # same day as detection
        days_elapsed = (date.today() - detected).days
        days_remaining = 30 - days_elapsed

        if days_remaining < 0:
            status = "OVERDUE"
            color = "RED"
        elif days_remaining <= 5:
            status = "CRITICAL"
            color = "RED"
        elif days_remaining <= 10:
            status = "AT_RISK"
            color = "AMBER"
        else:
            status = "ON_TRACK"
            color = "GREEN"

        entry = {
            "case_id": case_id,
            "detection_date": detection_date,
            "days_elapsed": days_elapsed,
            "days_remaining": days_remaining,
            "status": status,
            "color": color,
            "deadline_framework": "BSA/AML 30-day SAR filing requirement",
        }
        # Append to SAR log for persistence
        try:
            with open(SAR_LOG_PATH, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass
        return entry
    except Exception as e:
        return {"case_id": case_id, "error": str(e), "status": "UNKNOWN"}


def get_system_posture() -> dict:
    """
    Systemwide compliance posture check.
    Reads system card and redteam history to assess if the system
    is currently operating within its documented bounds.
    """
    card = _load_system_card()
    posture_issues = []
    posture_warnings = []

    # Check redteam cadence
    last_redteam = card.get("last_redteam_date")
    redteam_days_ago = None
    if last_redteam:
        redteam_days_ago = (date.today() - date.fromisoformat(last_redteam)).days
        cadence = card.get("post_market_monitoring", {}).get("redteam_cadence_days", 7)
        if redteam_days_ago > cadence:
            posture_warnings.append(f"Red-team probe last ran {redteam_days_ago} days ago — cadence requires every {cadence} days")
    else:
        posture_warnings.append("No red-team probe has been run yet — required for EU AI Act continuous risk management")

    # Check documentation review currency
    last_review = card.get("last_documentation_review")
    if last_review:
        days_since_review = (date.today() - date.fromisoformat(last_review)).days
        if days_since_review > 90:
            posture_issues.append(f"Technical documentation ({days_since_review} days old) needs review")
    else:
        posture_issues.append("Technical documentation has never been reviewed")

    # Check conformity assessment
    conformity = card.get("conformity_assessment", {})
    if conformity.get("status") != "COMPLETE":
        posture_warnings.append(f"EU AI Act conformity assessment status: {conformity.get('status', 'UNKNOWN')} — required before EU deployment")

    # Count SAR entries at risk
    sar_at_risk = 0
    try:
        with open(SAR_LOG_PATH) as f:
            for line in f:
                entry = json.loads(line)
                if entry.get("color") in ("RED", "AMBER"):
                    sar_at_risk += 1
    except FileNotFoundError:
        pass

    overall = "GREEN"
    if posture_issues:
        overall = "RED"
    elif posture_warnings:
        overall = "AMBER"

    return {
        "overall_posture": overall,
        "issues": posture_issues,
        "warnings": posture_warnings,
        "system_name": card.get("system_name", "Unknown"),
        "version": card.get("version", "Unknown"),
        "last_redteam_days_ago": redteam_days_ago,
        "last_documentation_review": last_review,
        "conformity_assessment_status": conformity.get("status", "UNKNOWN"),
        "sar_at_risk_count": sar_at_risk,
        "frameworks": ["EU AI Act Annex III", "BSA/AML (US)", "SR 11-7", "GDPR Art. 22 / CCPA"],
        "checked_at": datetime.now().isoformat(),
    }


def get_stats() -> dict:
    """Return aggregate compliance stats for the visual interface."""
    total = _compliance_checks.get("compliant", 0) + _compliance_checks.get("non_compliant", 0)
    sar_at_risk = 0
    try:
        with open(SAR_LOG_PATH) as f:
            for line in f:
                entry = json.loads(line)
                if entry.get("color") in ("RED", "AMBER"):
                    sar_at_risk += 1
    except FileNotFoundError:
        pass

    card = _load_system_card()
    last_redteam = card.get("last_redteam_date")
    redteam_days_ago = None
    if last_redteam:
        redteam_days_ago = (date.today() - date.fromisoformat(last_redteam)).days

    return {
        "cases_checked": total,
        "compliant": _compliance_checks.get("compliant", 0),
        "non_compliant": _compliance_checks.get("non_compliant", 0),
        "compliance_rate": round(_compliance_checks.get("compliant", 0) / total, 3) if total else 1.0,
        "sar_at_risk_count": sar_at_risk,
        "last_redteam_days_ago": redteam_days_ago,
    }
