"""
Orchestrator. Dependency-free state machine (no LangGraph install
required, so the Docker build stays fast/reliable) but structured in
the same node-and-state shape LangGraph uses -- porting to a real
StateGraph later is a mechanical change, not a redesign. Each node
writes to the audit log unconditionally before passing state forward.
"""
import uuid
from agents.triage_agent import triage
from agents.evidence_agent import gather_evidence
from agents.risk_assessment_agent import assess_risk
from agents.calling_agent import run_calling_agent
from agents.explanation_agent import generate_report
from agents.verification_agent import verify
import audit_log


def investigate_account(account_id: str, all_flags: list, accounts_by_id: dict,
                         identity_links=None, customer_response: str = None) -> dict:
    case_id = str(uuid.uuid4())[:8]
    state = {"case_id": case_id, "account_id": account_id}

    # 1. Triage
    acct_flags = [f for f in all_flags if f["account_id"] == account_id]
    triage_result = triage(acct_flags)
    state["triage"] = triage_result
    audit_log.log(case_id, "triage_agent", "classify_branch", triage_result)

    # 2. Evidence gathering
    evidence = gather_evidence(account_id, all_flags, accounts_by_id, identity_links)
    state["evidence"] = evidence
    audit_log.log(case_id, "evidence_agent", "compile_evidence", {"sources": evidence["sources"]})

    # 3. Risk assessment
    risk = assess_risk(evidence)
    state["risk"] = risk
    audit_log.log(case_id, "risk_assessment_agent", "assess_risk", risk)

    # 4. Calling agent -- only on FRAUD_BRANCH, only if evidence is ambiguous enough to need confirmation
    call_result = None
    if triage_result["branch"] == "FRAUD_BRANCH":
        try:
            call_result = run_calling_agent(risk, triage_result["branch"], customer_response)
            audit_log.log(case_id, "calling_agent", "contact_customer",
                           {"status": call_result.get("status"), "classification": call_result.get("classification")})
        except PermissionError as e:
            audit_log.log(case_id, "calling_agent", "refused", {"reason": str(e)})
    state["call_result"] = call_result

    # 5. Explanation / report drafting
    report = generate_report(case_id, evidence, risk, call_result)
    state["report"] = report
    audit_log.log(case_id, "explanation_agent", "generate_report", {"recommended_action": report["recommended_action"]})

    # 6. Verification -- independent pass, FAIL overrides everything above
    verification = verify(report, evidence, risk)
    state["verification"] = verification
    audit_log.log(case_id, "verification_agent", "verify", verification)
    if verification["result"] == "FAIL":
        report["recommended_action"] = "ESCALATE_TO_HUMAN_REVIEW"
        report["verification_override"] = verification["issues"]

    return state
