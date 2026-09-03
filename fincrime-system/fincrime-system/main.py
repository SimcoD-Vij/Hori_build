import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

import json
import pandas as pd
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# Feature flag: set USE_AMLSIM=true in .env to use real AMLSim data instead of synthetic.
# Keep both imports -- synthetic stays for regression testing (guide Step 2 requirement).
USE_AMLSIM = os.environ.get("USE_AMLSIM", "false").lower() == "true"
from data.generate_synthetic_data import generate  # kept for regression fallback
from data.ingestion.load_external import load_amlsim  # AMLSim adapter (Step 1)
from detection.rules import run_all_rules
from detection.statistics_layer import zscore_flags, benford_deviation_score
from detection.segmentation import build_peer_groups, peer_deviation_flags
from detection.graph_analysis import run_graph_detection, build_identity_graph
from detection.ml_models import train_supervised, supervised_flags, unsupervised_flags
from detection.temporal_graph import build_temporal_snapshots  # EvolveGCN prep (EVOLVEGCN_INTEGRATION.md)
from agents.redteam_agent import run_redteam_probe
from detection.backtest import compute_accuracy
from realtime.pretransaction_screening import screen_transaction, resolve_hold
from realtime.enforcement import enforce_decision  # prediction-only by default -- see realtime/enforcement.py
from agents.calling_agent import select_questions, classify_response
from agents.qa_agent import qa_review_batch
import orchestrator
import audit_log

PENDING_SCREENINGS = {}

app = FastAPI(title="Autonomous Financial Crime Investigation System")
templates = Jinja2Templates(directory="templates")

STATE = {}


def run_full_detection():
    # Step 2 (INTEGRATION_AND_OPERATIONS_GUIDE.md): swap data source based on feature flag.
    # Old synthetic generator kept commented directly above for regression testing -- do not delete.
    # accounts, transactions, ground_truth = generate()  # synthetic -- restore for regression tests
    if USE_AMLSIM:
        try:
            accounts, transactions, ground_truth = load_amlsim()
        except FileNotFoundError as e:
            print(f"[WARNING] AMLSim data not found ({e}). Falling back to synthetic data.")
            accounts, transactions, ground_truth = generate()
    else:
        accounts, transactions, ground_truth = generate()
    acc_df = pd.DataFrame(accounts)
    txn_df = pd.DataFrame(transactions)

    flags = []
    flags += run_all_rules(txn_df)
    flags += zscore_flags(txn_df)
    flags += run_graph_detection(acc_df, txn_df)

    peer_feats = build_peer_groups(acc_df, txn_df)
    flags += peer_deviation_flags(peer_feats)

    # bootstrap supervised labels from rule/graph hits so the ML layer has something to learn from
    labeled_txn_ids = {tid for f in flags for tid in f.get("transaction_ids", [])}
    labels = {tid: 1 for tid in labeled_txn_ids}
    clf = train_supervised(txn_df, labels)
    flags += supervised_flags(txn_df, clf)
    flags += unsupervised_flags(txn_df)

    id_graph = build_identity_graph(acc_df)
    identity_links = {n: list(id_graph.neighbors(n)) for n in id_graph.nodes if id_graph.degree(n) > 0}

    benford = benford_deviation_score(txn_df)

    # EvolveGCN temporal graph (EVOLVEGCN_INTEGRATION.md Step -- additive, never replaces existing detectors)
    # build_temporal_snapshots slices data into day-by-day graph snapshots ready for EvolveGCN training.
    # Inference call is commented out until the model is trained on real historical data (guide scoping note).
    temporal_snapshots = build_temporal_snapshots(txn_df)
    # from detection.evolvegcn_service import run_evolvegcn_inference  # uncomment after training
    # flags += run_evolvegcn_inference(temporal_snapshots)             # uncomment after training

    STATE["accounts"] = accounts
    STATE["transactions"] = transactions
    STATE["accounts_by_id"] = {a["account_id"]: a for a in accounts}
    STATE["flags"] = flags
    STATE["identity_links"] = identity_links
    STATE["benford"] = benford
    STATE["ground_truth"] = ground_truth
    STATE["flagged_accounts"] = sorted({f["account_id"] for f in flags})
    return STATE


@app.on_event("startup")
def startup():
    run_full_detection()


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    rows = []
    for acct_id in STATE["flagged_accounts"]:
        acct_flags = [f for f in STATE["flags"] if f["account_id"] == acct_id]
        rules_hit = sorted({f["rule"] for f in acct_flags})
        max_sev = max((f.get("severity_hint", 0) for f in acct_flags), default=0)
        rows.append({"account_id": acct_id, "rules": rules_hit, "severity": max_sev, "n_flags": len(acct_flags)})
    rows.sort(key=lambda r: -r["severity"])
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "rows": rows, "n_accounts": len(STATE["accounts"]),
        "n_transactions": len(STATE["transactions"]), "benford": STATE["benford"],
    })


@app.get("/investigate/{account_id}", response_class=HTMLResponse)
def investigate(request: Request, account_id: str):
    result = orchestrator.investigate_account(
        account_id, STATE["flags"], STATE["accounts_by_id"],
        STATE["identity_links"].get(account_id, []),
    )
    return templates.TemplateResponse("case_report.html", {"request": request, "case": result})


@app.post("/investigate/{account_id}/respond", response_class=HTMLResponse)
def investigate_with_response(request: Request, account_id: str, customer_response: str = Form(...)):
    result = orchestrator.investigate_account(
        account_id, STATE["flags"], STATE["accounts_by_id"],
        STATE["identity_links"].get(account_id, []), customer_response=customer_response,
    )
    return templates.TemplateResponse("case_report.html", {"request": request, "case": result})


@app.get("/redteam", response_class=HTMLResponse)
def redteam(request: Request):
    result = run_redteam_probe()
    return templates.TemplateResponse("redteam.html", {"request": request, "result": result})


@app.get("/audit/{case_id}")
def audit(case_id: str):
    return audit_log.read_log(case_id)


@app.get("/accuracy", response_class=HTMLResponse)
def accuracy(request: Request):
    acc = compute_accuracy(STATE["ground_truth"], STATE["flags"])
    return templates.TemplateResponse("accuracy.html", {"request": request, "acc": acc})


@app.get("/screen", response_class=HTMLResponse)
def screen_form(request: Request):
    return templates.TemplateResponse("screen.html", {"request": request, "result": None, "accounts": STATE["accounts"][:15]})


@app.post("/screen", response_class=HTMLResponse)
def screen_submit(request: Request, sender_account: str = Form(...), amount: float = Form(...),
                   method: str = Form(...)):
    import uuid
    from datetime import datetime
    candidate = {"sender_account": sender_account, "amount": amount, "method": method,
                 "timestamp": datetime.now().isoformat()}
    history = pd.DataFrame([t for t in STATE["transactions"]
                             if t["sender_account"] == sender_account or t["receiver_account"] == sender_account])

    result = screen_transaction(candidate, history)
    screening_id = str(uuid.uuid4())[:8]
    audit_log.log(screening_id, "pretransaction_screening", "screen", result)

    # Enforcement is prediction-only by default (ENFORCEMENT_ENABLED=false) -- this call
    # logs what the system WOULD do without touching any real transaction. See
    # realtime/enforcement.py to enable real enforcement when you're ready.
    enforcement_log = enforce_decision(screening_id, result)
    audit_log.log(screening_id, "enforcement_layer", "enforce_decision", enforcement_log)

    questions = None
    if result["decision"] == "HOLD_FOR_VERIFICATION":
        pattern = result["matched_patterns"][0] if result["matched_patterns"] else "default"
        type_map = {"zscore_self_history": "Card-not-present fraud", "velocity": "Account takeover"}
        case_type = type_map.get(pattern, "default")
        questions = select_questions(case_type, {"amount": amount, "date": "today"})
        PENDING_SCREENINGS[screening_id] = {"candidate": candidate, "result": result, "case_type": case_type}

    return templates.TemplateResponse("screen.html", {
        "request": request, "result": result, "screening_id": screening_id,
        "questions": questions, "accounts": STATE["accounts"][:15],
    })


@app.post("/screen/{screening_id}/resolve", response_class=HTMLResponse)
def screen_resolve(request: Request, screening_id: str, customer_response: str = Form(...)):
    pending = PENDING_SCREENINGS.get(screening_id)
    if not pending:
        return HTMLResponse("Screening not found or already resolved.", status_code=404)

    classification = classify_response(customer_response)
    final = resolve_hold(pending["result"], classification)
    audit_log.log(screening_id, "calling_agent", "resolve_hold",
                   {"classification": classification, "final_decision": final["decision"]})

    enforcement_log = enforce_decision(screening_id, final)
    audit_log.log(screening_id, "enforcement_layer", "enforce_decision", enforcement_log)
    del PENDING_SCREENINGS[screening_id]

    return templates.TemplateResponse("screen.html", {
        "request": request, "result": pending["result"], "final": final,
        "transcript": customer_response, "classification": classification,
        "accounts": STATE["accounts"][:15],
    })


@app.get("/qa", response_class=HTMLResponse)
def qa_audit(request: Request):
    cases = []
    for acct_id in STATE["flagged_accounts"]:
        result = orchestrator.investigate_account(acct_id, STATE["flags"], STATE["accounts_by_id"],
                                                    STATE["identity_links"].get(acct_id, []))
        cases.append(result)
    batch = qa_review_batch(cases)
    return templates.TemplateResponse("qa.html", {"request": request, "batch": batch})


@app.get("/api/health")
def health():
    return {"status": "ok", "accounts": len(STATE.get("accounts", [])), "flagged": len(STATE.get("flagged_accounts", []))}
