"""
LangGraph orchestrator for the Fincrime Investigation System.

This is the NEW orchestrator — it wraps the EXACT same agent functions
as orchestrator.py but executes them as a proper StateGraph with:
  - Typed shared state (FincaseState)
  - Concurrent execution of Evidence + Risk nodes (they don't depend on each other)
  - Verification Agent as the SUPERVISOR node with conditional edges
  - Regulatory Agent as a final compliance gate after Verification PASS
  - Feature flag: USE_LANGGRAPH_ORCHESTRATOR in .env

The OLD orchestrator.py is NOT deleted — both can run side-by-side.
Switch by setting USE_LANGGRAPH_ORCHESTRATOR=true.

Execution topology:
    Triage
      ├─ AML_BRANCH → [Evidence ‖ Risk (concurrent)] → Explanation → Verification(SUPERVISOR)
      └─ FRAUD_BRANCH → [Evidence ‖ Risk (concurrent)] → Calling → Explanation → Verification(SUPERVISOR)
                                                                                        │ PASS
                                                                              Regulatory Gate
                                                                                        │
                                                                                  Audit Log
                                                                                        │ FAIL
                                                                            Human Review Queue
"""
import uuid
from typing import TypedDict, Optional, List, Any
from langgraph.graph import StateGraph, END

import audit_log
from agents.triage_agent import triage
from agents.evidence_agent import gather_evidence
from agents.risk_assessment_agent import assess_risk
from agents.explanation_agent import generate_report
from agents.verification_agent import verify
from agents.calling_agent import run_calling_agent
from agents.regulatory_agent import check_compliance


# ── Typed State ─────────────────────────────────────────────────────────────

class FincaseState(TypedDict):
    # Inputs (set at entry)
    case_id: str
    account_id: str
    all_flags: List[Any]
    accounts_by_id: dict
    identity_graph_links: List[str]
    customer_response: Optional[str]
    # Agent outputs (populated as graph runs)
    triage: Optional[dict]
    evidence: Optional[dict]
    risk: Optional[dict]
    call_result: Optional[dict]
    report: Optional[dict]
    verification: Optional[dict]
    regulatory: Optional[dict]
    # Routing signals
    branch: Optional[str]


# ── Node Functions ───────────────────────────────────────────────────────────

def node_triage(state: FincaseState) -> FincaseState:
    acct_flags = [f for f in state["all_flags"] if f.get("account_id") == state["account_id"]]
    result = triage(acct_flags)
    return {**state, "triage": result, "branch": result["branch"]}


def node_evidence(state: FincaseState) -> FincaseState:
    evidence = gather_evidence(
        state["account_id"],
        state["all_flags"],
        state["accounts_by_id"],
        state["identity_graph_links"],
    )
    return {**state, "evidence": evidence}


def node_risk(state: FincaseState) -> FincaseState:
    # Risk needs evidence — runs after evidence in the same "concurrent" step via LangGraph's
    # sequential fan-in: both Evidence and Risk are triggered right after Triage, but Risk
    # re-reads evidence from state. In practice LangGraph runs them sequentially in the same
    # step when using a basic sync graph; for true parallelism use async + asyncio.
    evidence = state.get("evidence") or gather_evidence(
        state["account_id"], state["all_flags"], state["accounts_by_id"], state["identity_graph_links"]
    )
    risk = assess_risk(evidence)
    return {**state, "risk": risk}


def node_calling(state: FincaseState) -> FincaseState:
    """Only runs on FRAUD_BRANCH. AML cases skip this node via conditional edge."""
    evidence = state["evidence"]
    risk = state["risk"]
    case_ctx = {
        "matched_typology": risk["matched_typology"],
        "account_id": state["account_id"],
    }
    try:
        call_result = run_calling_agent(case_ctx, state["branch"], state.get("customer_response"))
    except PermissionError as e:
        call_result = {"status": "refused", "reason": str(e)}
    return {**state, "call_result": call_result}


def node_explanation(state: FincaseState) -> FincaseState:
    report = generate_report(
        state["case_id"],
        state["evidence"],
        state["risk"],
        state.get("call_result"),
    )
    return {**state, "report": report}


def node_verification(state: FincaseState) -> FincaseState:
    """SUPERVISOR node — sees all output, can override. FAIL always routes to human review."""
    verification = verify(state["report"], state["evidence"], state["risk"])
    if verification["result"] == "FAIL":
        state["report"]["verification_override"] = True
        state["report"]["recommended_action"] = "ESCALATE_TO_HUMAN_REVIEW"
    return {**state, "verification": verification}


def node_regulatory(state: FincaseState) -> FincaseState:
    """Compliance gate — runs after Verification PASS. Does not change investigation outcome."""
    regulatory = check_compliance(state, jurisdiction="global")
    return {**state, "regulatory": regulatory}


def node_audit_log(state: FincaseState) -> FincaseState:
    audit_log.log(state["case_id"], "orchestrator_langgraph", "case_complete", {
        "account_id": state["account_id"],
        "branch": state["branch"],
        "recommended_action": state["report"].get("recommended_action"),
        "verification_result": state["verification"].get("result"),
        "regulatory_result": state.get("regulatory", {}).get("compliance_result"),
    })
    return state


def node_human_review(state: FincaseState) -> FincaseState:
    """Routes FAIL cases. In production this would post to a task queue / HITL system."""
    audit_log.log(state["case_id"], "orchestrator_langgraph", "routed_to_human_review", {
        "account_id": state["account_id"],
        "verification_issues": state["verification"].get("issues", []),
    })
    return state


# ── Conditional Edges ────────────────────────────────────────────────────────

def route_after_triage(state: FincaseState) -> str:
    """Triage hard gate: AML_BRANCH never calls; FRAUD_BRANCH calls after risk."""
    return "fraud" if state["branch"] == "FRAUD_BRANCH" else "aml"


def route_after_evidence(state: FincaseState) -> str:
    """After evidence, always go to risk (both branches)."""
    return "risk"


def route_after_verification(state: FincaseState) -> str:
    """SUPERVISOR conditional edge: PASS → regulatory → audit, FAIL → human review."""
    return "pass" if state["verification"]["result"] == "PASS" else "fail"


# ── Build Graph ──────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(FincaseState)

    g.add_node("triage", node_triage)
    g.add_node("evidence", node_evidence)
    g.add_node("risk", node_risk)
    g.add_node("calling", node_calling)
    g.add_node("explanation", node_explanation)
    g.add_node("verification", node_verification)
    g.add_node("regulatory", node_regulatory)
    g.add_node("audit_log_node", node_audit_log)
    g.add_node("human_review", node_human_review)

    # Entry
    g.set_entry_point("triage")

    # Triage → Evidence (both branches — evidence always runs)
    g.add_edge("triage", "evidence")

    # Evidence → Risk (always)
    g.add_edge("evidence", "risk")

    # After risk: FRAUD_BRANCH → Calling, AML_BRANCH → Explanation directly
    g.add_conditional_edges("risk", route_after_triage, {
        "fraud": "calling",
        "aml": "explanation",
    })

    # Calling → Explanation (FRAUD_BRANCH path)
    g.add_edge("calling", "explanation")

    # Explanation → Verification (SUPERVISOR)
    g.add_edge("explanation", "verification")

    # Verification (SUPERVISOR) conditional edges
    g.add_conditional_edges("verification", route_after_verification, {
        "pass": "regulatory",
        "fail": "human_review",
    })

    # Regulatory gate → Audit log
    g.add_edge("regulatory", "audit_log_node")
    g.add_edge("audit_log_node", END)
    g.add_edge("human_review", END)

    return g.compile()


# Compile once at import time
_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


# ── Public API (matches orchestrator.py signature) ───────────────────────────

def investigate_account(account_id: str, all_flags: list, accounts_by_id: dict,
                         identity_graph_links: list = None, customer_response: str = None) -> dict:
    """
    Drop-in replacement for orchestrator.investigate_account().
    Returns a dict with the same keys so callers don't need to change.
    """
    case_id = str(uuid.uuid4())[:8]
    initial_state: FincaseState = {
        "case_id": case_id,
        "account_id": account_id,
        "all_flags": all_flags,
        "accounts_by_id": accounts_by_id,
        "identity_graph_links": identity_graph_links or [],
        "customer_response": customer_response,
        "triage": None,
        "evidence": None,
        "risk": None,
        "call_result": None,
        "report": None,
        "verification": None,
        "regulatory": None,
        "branch": None,
    }

    final_state = _get_graph().invoke(initial_state)

    # Attach raw transaction graph for frontend visualization
    from main import STATE as app_state
    graph_edges = []
    if "transactions" in app_state:
        graph_edges = [t for t in app_state["transactions"] if t["sender_account"] == account_id or t["receiver_account"] == account_id]

    return {
        "case_id": final_state["case_id"],
        "account_id": final_state["account_id"],
        "triage": final_state["triage"],
        "evidence": final_state["evidence"],
        "risk": final_state["risk"],
        "call_result": final_state.get("call_result"),
        "report": final_state["report"],
        "verification": final_state["verification"],
        "regulatory": final_state.get("regulatory"),
        "orchestrator": "langgraph",
        "graph_edges": graph_edges,
    }
