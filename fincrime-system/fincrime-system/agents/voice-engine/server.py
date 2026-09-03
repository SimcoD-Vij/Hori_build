# =============================================================
# agents/voice-engine/server.py — Fincrime Voice Investigation
#                                  Engine  (Port 8083)
# =============================================================
#
# Routes
# ──────
#   POST /trigger-call              Receives pre-call case details,
#                                   fires Dograh outbound call,
#                                   writes pre-call state to audit log
#                                   and in-memory case store.
#
#   POST /webhooks/call-completed   Dograh fires this on call end.
#                                   Parses transcript + disposition,
#                                   triggers Ollama summarization
#                                   (qwen2.5-coder:7b on Windows host),
#                                   writes full call record to audit log.
#
#   GET  /case/<case_id>            Serves a live HTML case-report page
#                                   showing pre-call investigation,
#                                   full transcript, and Ollama summary.
#                                   Auto-polls /case/<case_id>/data.
#
#   GET  /case/<case_id>/data       JSON API polled by the live page.
#                                   Returns the full case dict including
#                                   summary (once generated).
#
#   GET  /health                    Liveness check.
#
# Environment variables (set in .env or docker-compose)
# ──────────────────────────────────────────────────────
#   DOGRAH_API_URL      http://dograh-api:8000  (Dograh API container)
#   DOGRAH_API_KEY      <from Dograh dashboard → Settings → API Keys>
#   DOGRAH_WORKFLOW_ID  <workflow ID for the fraud-investigation bot>
#   OLLAMA_BASE_URL     http://host.docker.internal:11434  (Windows host)
#   OLLAMA_MODEL        qwen2.5-coder:7b
#   FINCRIME_AUDIT_LOG  /app/data/audit_log.jsonl
#   THIS_SERVICE_URL    http://localhost:8083  (for Dograh webhook URL)
# =============================================================

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from threading import Thread
from typing import Any

from flask import Flask, Response, jsonify, request

# ── Config ─────────────────────────────────────────────────────────────────────
DOGRAH_API_URL     = os.environ.get("DOGRAH_API_URL",     "http://dograh-api:8000")
DOGRAH_API_KEY     = os.environ.get("DOGRAH_API_KEY",     "")
DOGRAH_WORKFLOW_ID = os.environ.get("DOGRAH_WORKFLOW_ID", "")
OLLAMA_BASE_URL    = os.environ.get("OLLAMA_BASE_URL",    "http://host.docker.internal:11434")
OLLAMA_MODEL       = os.environ.get("OLLAMA_MODEL",       "qwen2.5-coder:7b")
OLLAMA_SUMMARIZATION_MODEL = os.environ.get("OLLAMA_SUMMARIZATION_MODEL", OLLAMA_MODEL)
FINCRIME_AUDIT_LOG = os.environ.get("FINCRIME_AUDIT_LOG", "/app/data/audit_log.jsonl")
THIS_SERVICE_URL   = os.environ.get("THIS_SERVICE_URL",   "http://localhost:8083")

app  = Flask(__name__)
PORT = 8083

# ── In-memory case store ────────────────────────────────────────────────────────
# Keyed by case_id. Each entry:
# {
#   "case_id", "account_id", "account_holder_name", "phone", "fraud_type",
#   "amount", "date", "merchant", "severity", "flags_summary",
#   "questions",         ← list[str]  from calling_agent
#   "pre_call_at",       ← ISO timestamp of call trigger
#   "call_id",           ← Dograh call_id (set after trigger)
#   "call_status",       ← "INITIATED" | "COMPLETED" | "FAILED"
#   "duration_seconds",
#   "disposition",       ← Dograh disposition string
#   "transcript_text",   ← full conversation text
#   "transcript_turns",  ← list[{role, content}]
#   "recording_url",
#   "classification",    ← "satisfactory" | "unsatisfactory" | "no_response"
#   "completed_at",
#   "summary",           ← Ollama-generated case narrative (set async)
#   "recommended_action",
# }
_CASES: dict[str, dict] = {}
_CASES_LOCK = threading.Lock()

# Idempotency guard: workflow_run_id → True
_processed_run_ids: set[str] = set()


# ══════════════════════════════════════════════════════════════════════════════
# 1. HEALTH
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health() -> Response:
    with _CASES_LOCK:
        n_cases = len(_CASES)
    return jsonify({
        "status":            "ok",
        "service":           "fincrime-voice-engine",
        "port":              PORT,
        "dograh_configured": bool(DOGRAH_API_KEY and DOGRAH_WORKFLOW_ID),
        "ollama_url":        OLLAMA_BASE_URL,
        "ollama_model":      OLLAMA_MODEL,
        "active_cases":      n_cases,
    })


# ══════════════════════════════════════════════════════════════════════════════
# 2. TRIGGER OUTBOUND INVESTIGATION CALL
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/trigger-call", methods=["POST"])
def trigger_call() -> Response:
    """
    Called by the fincrime orchestrator (or manually for testing).
    Stores pre-call investigation context, fires Dograh outbound call.

    Expected JSON body:
    {
      "account_id":          "ACC001",
      "phone":               "+919876543210",
      "account_holder_name": "John Doe",
      "case_id":             "abc123",
      "fraud_type":          "Card-not-present fraud",
      "amount":              "9700",
      "date":                "2024-01-15",
      "merchant":            "Online Store X",
      "beneficiary":         "Wire Recipient",    # optional
      "device":              "iPhone 13",         # optional
      "questions":           ["Q1...", "Q2..."],  # from calling_agent
      "severity":            8,
      "flags_summary":       "structuring, velocity",
      "summary":             "Brief pre-call case summary..."
    }
    """
    data: dict[str, Any] = request.get_json(silent=True) or {}

    required = ["account_id", "phone", "account_holder_name", "case_id"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {missing}"}), 400

    case_id            = data["case_id"]
    phone              = data["phone"]
    account_id         = data["account_id"]
    account_holder     = data["account_holder_name"]
    fraud_type         = data.get("fraud_type", "General Fraud")
    amount             = str(data.get("amount", ""))
    date               = data.get("date", "")
    merchant           = data.get("merchant", "the merchant")
    beneficiary        = data.get("beneficiary", "the recipient")
    device             = data.get("device", "an unrecognized device")
    questions          = data.get("questions", [])
    severity           = data.get("severity", 0)
    flags_summary      = data.get("flags_summary", "")
    pre_summary        = data.get("summary", "")

    # ── Store pre-call state ──────────────────────────────────────────────────
    case_record = {
        "case_id":             case_id,
        "account_id":          account_id,
        "account_holder_name": account_holder,
        "phone":               phone,
        "fraud_type":          fraud_type,
        "amount":              amount,
        "date":                date,
        "merchant":            merchant,
        "beneficiary":         beneficiary,
        "device":              device,
        "severity":            severity,
        "flags_summary":       flags_summary,
        "pre_call_summary":    pre_summary,
        "questions":           questions,
        "pre_call_at":         _now(),
        "call_id":             None,
        "call_status":         "INITIATED",
        "duration_seconds":    None,
        "disposition":         None,
        "transcript_text":     None,
        "transcript_turns":    [],
        "recording_url":       None,
        "classification":      None,
        "completed_at":        None,
        "summary":             None,
        "recommended_action":  None,
    }

    with _CASES_LOCK:
        _CASES[case_id] = case_record

    # ── Write pre-call audit log entry ────────────────────────────────────────
    _write_audit_log(case_id, "voice_engine", "call_initiated", {
        "account_id":    account_id,
        "phone":         phone,
        "fraud_type":    fraud_type,
        "severity":      severity,
        "flags_summary": flags_summary,
        "questions":     questions,
    })

    print(f"\n📞 CALL TRIGGER: case={case_id} | account={account_id} | phone={phone}")
    print(f"   Fraud   : {fraud_type} | Severity: {severity}")
    print(f"   Questions: {len(questions)}")

    # ── Build Dograh context variables ────────────────────────────────────────
    context_variables = {
        "account_holder_name": account_holder,
        "account_id":          account_id,
        "case_id":             case_id,
        "fraud_type":          fraud_type,
        "amount":              amount,
        "transaction_date":    date,
        "merchant":            merchant,
        "beneficiary":         beneficiary,
        "device":              device,
        "severity":            str(severity),
        "question_1":          questions[0] if len(questions) > 0 else "",
        "question_2":          questions[1] if len(questions) > 1 else "",
        "question_3":          questions[2] if len(questions) > 2 else "",
        "question_4":          questions[3] if len(questions) > 3 else "",
        "all_questions_json":  json.dumps(questions),
    }

    # ── Fire Dograh outbound call ─────────────────────────────────────────────
    if not DOGRAH_API_KEY or not DOGRAH_WORKFLOW_ID:
        print("   ⚠️  DOGRAH_API_KEY/WORKFLOW_ID not set — simulation mode")
        with _CASES_LOCK:
            _CASES[case_id]["call_id"]    = f"sim-{case_id}"
            _CASES[case_id]["call_status"] = "SIMULATED"
        _write_audit_log(case_id, "voice_engine", "call_simulated", {
            "reason": "DOGRAH_API_KEY or DOGRAH_WORKFLOW_ID not set"
        })
        return jsonify({
            "status":    "simulated",
            "call_id":   f"sim-{case_id}",
            "case_url":  f"{THIS_SERVICE_URL}/case/{case_id}",
            "message":   "Set DOGRAH_API_KEY + DOGRAH_WORKFLOW_ID to trigger real calls",
        }), 202

    try:
        # Dograh public trigger API expects 'phone_number' and 'initial_context'
        payload = json.dumps({
            "phone_number":      phone,
            "initial_context":   context_variables
        }).encode()

        # The UUID is pulled from DOGRAH_CALL_TRIGGER env variable (from the user's .env)
        trigger_uuid = os.environ.get("Dograh_call_trigger", DOGRAH_WORKFLOW_ID)
        
        req = urllib.request.Request(
            f"{DOGRAH_API_URL}/api/v1/public/agent/{trigger_uuid}",
            data=payload,
            headers={
                "Content-Type":  "application/json",
                "X-API-Key":     DOGRAH_API_KEY,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_body = json.loads(resp.read().decode())

        call_id = resp_body.get("workflow_run_id") or resp_body.get("call_id") or resp_body.get("id", "unknown")
        print(f"   ✅ Dograh call initiated: call_id={call_id}")

        with _CASES_LOCK:
            _CASES[case_id]["call_id"]    = call_id
            _CASES[case_id]["call_status"] = "IN_PROGRESS"

        _write_audit_log(case_id, "voice_engine", "dograh_call_initiated", {
            "call_id":     call_id,
            "trigger_id":  trigger_uuid,
        })

        return jsonify({
            "status":   "initiated",
            "call_id":  call_id,
            "case_url": f"{THIS_SERVICE_URL}/case/{case_id}",
        }), 202

    except Exception as e:
        print(f"   ❌ Dograh API error: {e}")
        with _CASES_LOCK:
            _CASES[case_id]["call_status"] = "FAILED"
        _write_audit_log(case_id, "voice_engine", "dograh_call_failed", {"error": str(e)})
        return jsonify({"error": f"Dograh call failed: {e}"}), 502


# ══════════════════════════════════════════════════════════════════════════════
# 3. DOGRAH CALL-COMPLETED WEBHOOK
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/webhooks/call-completed", methods=["POST"])
def call_completed() -> Response:
    """
    Dograh fires this when the voice call ends.
    Parses transcript + disposition, writes full record to audit log,
    then kicks off Ollama summarization in background.
    """
    payload: dict[str, Any] = request.get_json(silent=True) or {}
    run_id = payload.get("workflow_run_id", "")

    # Idempotency
    if run_id and run_id in _processed_run_ids:
        return jsonify({"status": "already_processed"})
    if run_id:
        _processed_run_ids.add(run_id)
        if len(_processed_run_ids) > 1000:
            _processed_run_ids.clear()

    phone          = payload.get("phone_number", "")
    call_id        = payload.get("call_id") or run_id
    duration       = payload.get("duration_seconds", 0)
    disposition    = payload.get("disposition", "UNKNOWN").upper()
    recording_url  = payload.get("recording_url")
    transcript_raw = payload.get("transcript", [])
    ctx            = payload.get("context_variables", {})

    case_id    = ctx.get("case_id", "unknown")
    account_id = ctx.get("account_id", "unknown")

    print(f"\n📞 CALL COMPLETED: run={run_id} | case={case_id}")
    print(f"   Duration: {duration}s | Disposition: {disposition}")

    # ── Build transcript ──────────────────────────────────────────────────────
    turns: list[dict] = []
    if isinstance(transcript_raw, list):
        turns = [{"role": t.get("role", "?"), "content": t.get("content", "")}
                 for t in transcript_raw]
        transcript_text = "\n".join(
            f"{t['role'].upper()}: {t['content']}" for t in turns
        )
    else:
        transcript_text = str(transcript_raw)

    classification = _classify_transcript(transcript_text, disposition)
    recommended    = _recommended_action(classification, disposition)

    # ── Update in-memory case record ──────────────────────────────────────────
    with _CASES_LOCK:
        case = _CASES.get(case_id)
        if case:
            case["call_status"]      = "COMPLETED"
            case["duration_seconds"] = duration
            case["disposition"]      = disposition
            case["transcript_text"]  = transcript_text
            case["transcript_turns"] = turns
            case["recording_url"]    = recording_url
            case["classification"]   = classification
            case["recommended_action"] = recommended
            case["completed_at"]     = _now()
        else:
            # Call arrived but we have no pre-call record (e.g. restart) — create one
            _CASES[case_id] = {
                "case_id":             case_id,
                "account_id":          account_id,
                "account_holder_name": ctx.get("account_holder_name", "Unknown"),
                "phone":               phone,
                "fraud_type":          ctx.get("fraud_type", ""),
                "amount":              ctx.get("amount", ""),
                "date":                ctx.get("transaction_date", ""),
                "questions":           json.loads(ctx.get("all_questions_json", "[]")),
                "severity":            ctx.get("severity", "0"),
                "flags_summary":       "",
                "pre_call_summary":    "",
                "pre_call_at":         None,
                "call_id":             call_id,
                "call_status":         "COMPLETED",
                "duration_seconds":    duration,
                "disposition":         disposition,
                "transcript_text":     transcript_text,
                "transcript_turns":    turns,
                "recording_url":       recording_url,
                "classification":      classification,
                "recommended_action":  recommended,
                "completed_at":        _now(),
                "summary":             None,
            }

    # ── Write audit log ───────────────────────────────────────────────────────
    _write_audit_log(case_id, "voice_engine", "call_completed", {
        "call_id":             call_id,
        "account_id":          account_id,
        "duration_seconds":    duration,
        "disposition":         disposition,
        "classification":      classification,
        "recommended_action":  recommended,
        "auto_close_eligible": classification == "satisfactory",
        "recording_url":       recording_url,
        "transcript":          transcript_text,
    })

    print(f"   Classification: {classification} | Action: {recommended}")
    print(f"   ✅ Audit log written for case={case_id}")

    # ── Generate Ollama summary asynchronously ────────────────────────────────
    with _CASES_LOCK:
        case_snapshot = dict(_CASES[case_id])

    Thread(
        target=_run_ollama_summary,
        args=(case_id, case_snapshot),
        daemon=True,
    ).start()

    return jsonify({"status": "accepted", "classification": classification})


# ══════════════════════════════════════════════════════════════════════════════
# 4. CASE REPORT WEBPAGE  (GET /case/<case_id>)
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/case/<case_id>", methods=["GET"])
def case_page(case_id: str) -> Response:
    """Serve the live HTML case-report page."""
    with _CASES_LOCK:
        exists = case_id in _CASES
    if not exists:
        return Response(
            f"<h3>Case '{case_id}' not found.</h3>"
            f"<p>It may not have been triggered yet.</p>",
            status=404, mimetype="text/html"
        )
    return Response(_render_case_page(case_id), mimetype="text/html")


# ══════════════════════════════════════════════════════════════════════════════
# 5. CASE DATA JSON API  (GET /case/<case_id>/data)
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/case/<case_id>/data", methods=["GET"])
def case_data(case_id: str) -> Response:
    """JSON endpoint polled by the live page every 3 s."""
    with _CASES_LOCK:
        case = _CASES.get(case_id)
    if not case:
        return jsonify({"error": "not_found"}), 404
    return jsonify(case)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

_SATISFACTORY_DISPOSITIONS  = {"USER_QUALIFIED", "INTERESTED", "COMPLETED"}
_UNSATISFACTORY_DISPOSITIONS = {"NOT_INTERESTED", "DO_NOT_CONTACT"}
_NO_RESPONSE_DISPOSITIONS   = {"NO_ANSWER", "VOICEMAIL", "BUSY"}

_SATISFACTORY_SIGNALS = [
    "yes i made", "that was me", "i authorized", "i bought",
    "i remember", "i did make", "i placed", "i transferred", "i recognize",
]
_UNSATISFACTORY_SIGNALS = [
    "don't know", "not sure", "someone else", "confidential",
    "don't remember", "secret", "didn't do", "wasn't me", "i didn't",
    "never made", "not mine",
]


def _classify_transcript(transcript: str, disposition: str) -> str:
    if disposition in _NO_RESPONSE_DISPOSITIONS:
        return "no_response"
    t = transcript.lower()
    if disposition in _SATISFACTORY_DISPOSITIONS and any(s in t for s in _SATISFACTORY_SIGNALS):
        return "satisfactory"
    if disposition in _UNSATISFACTORY_DISPOSITIONS:
        return "unsatisfactory"
    if any(s in t for s in _UNSATISFACTORY_SIGNALS):
        return "unsatisfactory"
    if any(s in t for s in _SATISFACTORY_SIGNALS):
        return "satisfactory"
    return "unsatisfactory"  # conservative default


def _recommended_action(classification: str, disposition: str) -> str:
    if classification == "satisfactory":
        return "ALLOW — customer confirmed transaction"
    if classification == "no_response":
        return "HOLD_FOR_CALLBACK — no answer"
    if disposition == "DO_NOT_CONTACT":
        return "ESCALATE_TO_AML — customer refused contact"
    return "BLOCK_PENDING_REVIEW — unsatisfactory / unresolved"


def _run_ollama_summary(case_id: str, case: dict) -> None:
    """Background thread: generate Ollama summary, update case store, write audit log."""
    print(f"   🤖 Generating Ollama summary for case={case_id} using {OLLAMA_MODEL}...")
    try:
        summary = _summarize_with_ollama(case)
        with _CASES_LOCK:
            if case_id in _CASES:
                _CASES[case_id]["summary"] = summary
        _write_audit_log(case_id, "voice_engine", "ollama_summary_generated", {
            "model": OLLAMA_MODEL,
            "summary_length": len(summary),
        })
        print(f"   ✅ Ollama summary ready for case={case_id}")
    except Exception as e:
        print(f"   ⚠️ Ollama summary failed for case={case_id}: {e}", file=sys.stderr)
        with _CASES_LOCK:
            if case_id in _CASES:
                _CASES[case_id]["summary"] = f"[Summary unavailable: {e}]"


def _summarize_with_ollama(case: dict) -> str:
    """Call local Ollama (qwen2.5-coder:7b) to generate a case narrative."""
    questions_block = "\n".join(
        f"  {i+1}. {q}" for i, q in enumerate(case.get("questions") or [])
    )
    transcript = case.get("transcript_text") or "(no transcript)"

    prompt = f"""You are a financial crime analyst AI. Produce a structured case summary.

## PRE-CALL INVESTIGATION
- Account Holder : {case.get('account_holder_name')} ({case.get('account_id')})
- Phone          : {case.get('phone')}
- Fraud Type     : {case.get('fraud_type')}
- Risk Severity  : {case.get('severity')}/10
- Flagged Signals: {case.get('flags_summary') or 'see transcript'}
- Pre-Call Notes : {case.get('pre_call_summary') or 'N/A'}

## QUESTIONS ASKED IN THE CALL
{questions_block or '  (none recorded)'}

## CALL OUTCOME
- Duration    : {case.get('duration_seconds')}s
- Disposition : {case.get('disposition')}
- Classification: {case.get('classification')}

## FULL TRANSCRIPT
{transcript}

---
Write a structured analysis with exactly these 5 sections:

**WHAT WAS DETECTED (Before Call)**
[Summarise the suspicious patterns and risk signals found before the call in 2-3 sentences]

**WHAT WAS ASKED**
[List the key investigation questions posed to the account holder]

**WHAT WAS SAID / CONFIRMED**
[Summarise exactly what the customer confirmed, denied, or failed to respond to, quoting key phrases from the transcript]

**VERDICT**
[State whether the response was satisfactory, unsatisfactory, or absent — and why in 1-2 sentences]

**RECOMMENDED ACTION**
[State one of: ALLOW | HOLD_FOR_CALLBACK | BLOCK_PENDING_REVIEW | ESCALATE_TO_AML — with a one-sentence justification]
"""

    body = json.dumps({
        "model":  OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 800},
    }).encode()

    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode())
    return result.get("response", "[No response from Ollama]")


def _write_audit_log(case_id: str, agent: str, action: str, detail: dict) -> None:
    """Append-only write to the shared fincrime audit JSONL."""
    entry = {
        "case_id":   case_id,
        "agent":     agent,
        "action":    action,
        "timestamp": _now(),
        "detail":    detail,
    }
    try:
        os.makedirs(os.path.dirname(os.path.abspath(FINCRIME_AUDIT_LOG)), exist_ok=True)
        with open(FINCRIME_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"   ⚠️ Audit log write error: {e}", file=sys.stderr)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status_color(status: str) -> str:
    return {
        "INITIATED":   "#f59e0b",
        "IN_PROGRESS": "#3b82f6",
        "COMPLETED":   "#10b981",
        "FAILED":      "#ef4444",
        "SIMULATED":   "#8b5cf6",
    }.get(status, "#6b7280")


def _verdict_color(classification: str) -> str:
    return {
        "satisfactory":   "#10b981",
        "unsatisfactory": "#ef4444",
        "no_response":    "#f59e0b",
    }.get(classification or "", "#6b7280")


@app.route("/case/<case_id>/retrigger", methods=["POST"])
def retrigger_case(case_id: str) -> Response:
    with _CASES_LOCK:
        c = _CASES.get(case_id)
    if not c:
        return jsonify({"error": "Case not found"}), 404

    data = {
        "case_id": c["case_id"],
        "account_id": c["account_id"],
        "phone": c["phone"],
        "account_holder_name": c["account_holder_name"],
        "fraud_type": c.get("fraud_type", ""),
        "amount": c.get("amount", ""),
        "date": c.get("date", ""),
        "merchant": c.get("merchant", ""),
        "severity": c.get("severity", 0),
        "questions": c.get("questions", [])
    }
    
    import urllib.request
    import json
    import threading
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/trigger-call",
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    def fire():
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            print("Retrigger error:", e)
    threading.Thread(target=fire, daemon=True).start()
    
    return jsonify({"status": "initiated"})


# ══════════════════════════════════════════════════════════════════════════════
# HTML CASE REPORT PAGE
# ══════════════════════════════════════════════════════════════════════════════

def _render_case_page(case_id: str) -> str:
    """Returns the full HTML for the live case report page."""
    with _CASES_LOCK:
        c = dict(_CASES.get(case_id, {}))

    status_col  = _status_color(c.get("call_status", ""))
    verdict_col = _verdict_color(c.get("classification"))
    questions   = c.get("questions") or []
    turns       = c.get("transcript_turns") or []

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Case {case_id} — Fincrime Investigation</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg:       #0a0e1a;
    --surface:  #111827;
    --border:   #1f2937;
    --text:     #e5e7eb;
    --muted:    #6b7280;
    --accent:   #3b82f6;
    --green:    #10b981;
    --red:      #ef4444;
    --yellow:   #f59e0b;
    --purple:   #8b5cf6;
    --radius:   12px;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', sans-serif;
    min-height: 100vh;
    padding: 0;
  }}

  /* TOP BAR */
  .topbar {{
    background: linear-gradient(135deg, #0f1729, #1a1f35);
    border-bottom: 1px solid var(--border);
    padding: 18px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    z-index: 100;
    backdrop-filter: blur(10px);
  }}
  .topbar-left {{ display: flex; align-items: center; gap: 16px; }}
  .retrigger-btn {{
    background: var(--accent);
    color: white;
    border: none;
    padding: 6px 14px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    font-family: 'Inter', sans-serif;
    transition: background 0.2s;
  }}
  .retrigger-btn:hover {{ background: #2563eb; }}
  .logo {{ font-size: 20px; font-weight: 700; color: var(--accent); letter-spacing: -0.5px; }}
  .case-badge {{
    background: rgba(59,130,246,.15);
    border: 1px solid rgba(59,130,246,.3);
    color: var(--accent);
    padding: 4px 12px;
    border-radius: 6px;
    font-size: 13px;
    font-family: 'JetBrains Mono', monospace;
  }}
  .live-dot {{
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--green);
    animation: pulse 1.5s infinite;
    display: inline-block;
    margin-right: 6px;
  }}
  @keyframes pulse {{
    0%,100% {{ opacity:1; transform:scale(1); }}
    50% {{ opacity:.5; transform:scale(1.3); }}
  }}
  .live-label {{ font-size: 12px; color: var(--green); font-weight: 500; }}
  .status-pill {{
    padding: 5px 14px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    background: rgba(255,255,255,.05);
    border: 1px solid {status_col}55;
    color: {status_col};
  }}

  /* LAYOUT */
  .container {{ max-width: 1600px; margin: 0 auto; padding: 28px 32px; }}
  .grid-3 {{ display: grid; grid-template-columns: 1fr 1.4fr 1fr; gap: 20px; }}
  @media (max-width: 1200px) {{ .grid-3 {{ grid-template-columns: 1fr; }} }}

  /* CARDS */
  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
  }}
  .card-header {{
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--muted);
  }}
  .card-header .icon {{ font-size: 16px; }}
  .card-body {{ padding: 20px; }}

  /* META ROW */
  .meta-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 4px; }}
  .meta-item {{ display: flex; flex-direction: column; gap: 3px; }}
  .meta-label {{ font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }}
  .meta-value {{ font-size: 14px; font-weight: 500; word-break: break-word; }}
  .severity-bar {{
    height: 6px;
    background: var(--border);
    border-radius: 3px;
    overflow: hidden;
    margin-top: 6px;
  }}
  .severity-fill {{
    height: 100%;
    border-radius: 3px;
    background: linear-gradient(90deg, #f59e0b, #ef4444);
    width: calc({c.get('severity', 0)} / 10 * 100%);
  }}

  /* QUESTIONS */
  .questions-list {{ list-style: none; }}
  .questions-list li {{
    padding: 10px 14px;
    margin-bottom: 8px;
    background: rgba(59,130,246,.06);
    border: 1px solid rgba(59,130,246,.15);
    border-radius: 8px;
    font-size: 13px;
    line-height: 1.5;
    color: #cbd5e1;
    counter-increment: q;
  }}
  .questions-list li::before {{
    content: counter(q) ".";
    color: var(--accent);
    font-weight: 600;
    margin-right: 8px;
  }}
  .questions-list {{ counter-reset: q; }}

  /* TRANSCRIPT */
  .transcript-box {{
    background: #0d1117;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
    max-height: 420px;
    overflow-y: auto;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12.5px;
    line-height: 1.7;
  }}
  .turn {{ margin-bottom: 14px; }}
  .turn-role {{
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-bottom: 4px;
  }}
  .turn-agent {{ color: #60a5fa; }}
  .turn-user  {{ color: #34d399; }}
  .turn-content {{
    background: rgba(255,255,255,.03);
    border-radius: 6px;
    padding: 8px 12px;
    color: #d1d5db;
  }}
  .transcript-empty {{
    color: var(--muted);
    font-style: italic;
    text-align: center;
    padding: 40px 0;
    font-family: 'Inter', sans-serif;
  }}

  /* VERDICT BADGE */
  .verdict-badge {{
    display: inline-block;
    padding: 6px 16px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    background: {verdict_col}20;
    border: 1px solid {verdict_col}55;
    color: {verdict_col};
    margin-bottom: 16px;
  }}

  /* SUMMARY */
  .summary-text {{
    font-size: 13.5px;
    line-height: 1.8;
    color: #d1d5db;
    white-space: pre-wrap;
    word-break: break-word;
  }}
  .summary-pending {{
    display: flex;
    align-items: center;
    gap: 12px;
    color: var(--muted);
    font-size: 13px;
    padding: 24px 0;
  }}
  .spinner {{
    width: 20px; height: 20px;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}

  /* ACTION BOX */
  .action-box {{
    padding: 14px 16px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 600;
    margin-top: 16px;
    border-left: 3px solid;
  }}
  .action-allow   {{ background: #10b98115; border-color: #10b981; color: #34d399; }}
  .action-block   {{ background: #ef444415; border-color: #ef4444; color: #f87171; }}
  .action-hold    {{ background: #f59e0b15; border-color: #f59e0b; color: #fbbf24; }}
  .action-escalate {{ background: #8b5cf615; border-color: #8b5cf6; color: #a78bfa; }}

  /* DIVIDER */
  .divider {{ border: none; border-top: 1px solid var(--border); margin: 16px 0; }}

  /* FOOTER */
  .footer {{ text-align: center; padding: 24px; color: var(--muted); font-size: 12px; margin-top: 20px; }}
  .refresh-info {{ font-size: 11px; color: var(--muted); text-align: right; margin-top: 8px; }}
</style>
</head>
<body>

<!-- TOP BAR -->
<div class="topbar">
  <div class="topbar-left">
    <span class="logo">⚖️ FinCrime</span>
    <span class="case-badge">CASE #{c.get('case_id','?')}</span>
    <button id="retrigger-btn" class="retrigger-btn">📞 Re-trigger Call</button>
  </div>
  <div style="display:flex;align-items:center;gap:16px;">
    <span><span class="live-dot"></span><span class="live-label">LIVE</span></span>
    <span class="status-pill" id="status-pill">{c.get('call_status','?')}</span>
  </div>
</div>

<div class="container">

  <!-- ACCOUNT META ROW -->
  <div class="card" style="margin-bottom:20px;">
    <div class="card-header"><span class="icon">🏦</span> Account & Case Overview</div>
    <div class="card-body">
      <div class="meta-grid" style="grid-template-columns: repeat(5, 1fr);">
        <div class="meta-item">
          <span class="meta-label">Account Holder</span>
          <span class="meta-value">{c.get('account_holder_name','—')}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">Account ID</span>
          <span class="meta-value" style="font-family:monospace">{c.get('account_id','—')}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">Phone</span>
          <span class="meta-value" style="font-family:monospace">{c.get('phone','—')}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">Fraud Type</span>
          <span class="meta-value">{c.get('fraud_type','—')}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">Risk Severity</span>
          <span class="meta-value">{c.get('severity','?')}/10</span>
          <div class="severity-bar"><div class="severity-fill"></div></div>
        </div>
      </div>
    </div>
  </div>

  <!-- 3 COLUMN GRID -->
  <div class="grid-3">

    <!-- COL 1: PRE-CALL INVESTIGATION -->
    <div>
      <div class="card" style="margin-bottom:20px;">
        <div class="card-header"><span class="icon">🔍</span> Pre-Call Investigation</div>
        <div class="card-body">
          <div class="meta-item" style="margin-bottom:12px;">
            <span class="meta-label">Flagged Signals</span>
            <span class="meta-value">{c.get('flags_summary') or '—'}</span>
          </div>
          <div class="meta-item" style="margin-bottom:12px;">
            <span class="meta-label">Amount</span>
            <span class="meta-value">${c.get('amount','—')} on {c.get('date','—')}</span>
          </div>
          <div class="meta-item" style="margin-bottom:12px;">
            <span class="meta-label">Merchant / Beneficiary</span>
            <span class="meta-value">{c.get('merchant','—')}</span>
          </div>
          <hr class="divider">
          <div class="meta-label" style="margin-bottom:10px;">Investigation Notes</div>
          <div style="font-size:13px;color:#cbd5e1;line-height:1.6;">{c.get('pre_call_summary') or '—'}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><span class="icon">❓</span> Questions Asked in Call</div>
        <div class="card-body">
          {'<ul class="questions-list">' + ''.join(f'<li>{q}</li>' for q in questions) + '</ul>'
           if questions else '<div style="color:var(--muted);font-size:13px;">No questions recorded.</div>'}
        </div>
      </div>
    </div>

    <!-- COL 2: LIVE TRANSCRIPT -->
    <div class="card">
      <div class="card-header">
        <span class="icon">🎙️</span> Call Transcript
        <span style="margin-left:auto;font-size:11px;color:var(--muted);" id="duration-label">
          {f'{c.get("duration_seconds")}s' if c.get("duration_seconds") else 'In progress...'}
        </span>
      </div>
      <div class="card-body" style="padding:16px;">
        <div class="transcript-box" id="transcript-box">
          {''.join(
              f'<div class="turn">'
              f'<div class="turn-role turn-{"agent" if t["role"] in ("agent","assistant") else "user"}">'
              f'{"🤖 AI AGENT" if t["role"] in ("agent","assistant") else "👤 CUSTOMER"}'
              f'</div>'
              f'<div class="turn-content">{t["content"]}</div>'
              f'</div>'
              for t in turns
          ) if turns else '<div class="transcript-empty">⏳ Waiting for call to complete…</div>'}
        </div>
        <p class="refresh-info" id="refresh-info">Auto-refreshing every 3s</p>
      </div>
    </div>

    <!-- COL 3: POST-CALL ANALYSIS -->
    <div class="card">
      <div class="card-header"><span class="icon">📊</span> Post-Call Analysis</div>
      <div class="card-body">

        <div class="meta-label" style="margin-bottom:8px;">Disposition</div>
        <div id="disposition-val" style="font-size:14px;font-weight:600;margin-bottom:16px;color:{status_col};">
          {c.get('disposition') or '—'}
        </div>

        <div class="meta-label" style="margin-bottom:8px;">Classification</div>
        <div id="classification-badge">
          {'<span class="verdict-badge">' + (c.get('classification') or 'pending') + '</span>'
           if c.get('classification') else '<span style="color:var(--muted);font-size:13px;">Pending…</span>'}
        </div>

        <hr class="divider">
        <div class="meta-label" style="margin-bottom:12px;">🤖 AI Summary ({OLLAMA_SUMMARIZATION_MODEL})</div>
        <div id="summary-area">
          {'<div class="summary-text">' + c["summary"] + '</div>' if c.get("summary")
           else '<div class="summary-pending"><div class="spinner"></div>Generating summary via Ollama…</div>'}
        </div>

        <div id="action-area">
          {_action_box_html(c.get('recommended_action'))}
        </div>

      </div>
    </div>

  </div><!-- /grid-3 -->

  <div class="footer">
    Case {c.get('case_id','?')} · Triggered {c.get('pre_call_at','?')} ·
    {'Completed ' + c.get('completed_at','') if c.get('completed_at') else 'Call in progress'}
  </div>

</div><!-- /container -->

<script>
// ── Live-polling: call /case/{case_id}/data every 3 s ──────────────
const CASE_ID = "{case_id}";
let lastStatus = "{c.get('call_status','')}";
let pollActive = true;

function classificationColor(v) {{
  return {{satisfactory:'#10b981',unsatisfactory:'#ef4444',no_response:'#f59e0b'}}[v] || '#6b7280';
}}
function statusColor(v) {{
  return {{INITIATED:'#f59e0b',IN_PROGRESS:'#3b82f6',COMPLETED:'#10b981',
           FAILED:'#ef4444',SIMULATED:'#8b5cf6'}}[v] || '#6b7280';
}}
function actionClass(a) {{
  if (!a) return '';
  const l = a.toLowerCase();
  if (l.includes('allow'))    return 'action-allow';
  if (l.includes('block'))    return 'action-block';
  if (l.includes('escalate')) return 'action-escalate';
  return 'action-hold';
}}

async function poll() {{
  if (!pollActive) return;
  try {{
    const r = await fetch(`/case/${{CASE_ID}}/data`);
    if (!r.ok) return;
    const d = await r.json();

    // Status pill
    const pill = document.getElementById('status-pill');
    if (pill) {{
      pill.textContent = d.call_status || '?';
      pill.style.color = statusColor(d.call_status);
      pill.style.borderColor = statusColor(d.call_status) + '55';
    }}

    // Duration
    const dur = document.getElementById('duration-label');
    if (dur && d.duration_seconds) dur.textContent = d.duration_seconds + 's';

    // Disposition
    const disp = document.getElementById('disposition-val');
    if (disp && d.disposition) disp.textContent = d.disposition;

    // Classification badge
    const cb = document.getElementById('classification-badge');
    if (cb && d.classification) {{
      const col = classificationColor(d.classification);
      cb.innerHTML = `<span style="display:inline-block;padding:6px 16px;border-radius:20px;
        font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;
        background:${{col}}20;border:1px solid ${{col}}55;color:${{col}};">${{d.classification}}</span>`;
    }}

    // Transcript
    const box = document.getElementById('transcript-box');
    if (box && d.transcript_turns && d.transcript_turns.length > 0) {{
      box.innerHTML = d.transcript_turns.map(t => {{
        const isAgent = ['agent','assistant'].includes(t.role);
        return `<div class="turn">
          <div class="turn-role ${{isAgent ? 'turn-agent' : 'turn-user'}}">${{isAgent ? '🤖 AI AGENT' : '👤 CUSTOMER'}}</div>
          <div class="turn-content">${{t.content}}</div>
        </div>`;
      }}).join('');
      box.scrollTop = box.scrollHeight;
    }}

    // Summary
    const sa = document.getElementById('summary-area');
    if (sa && d.summary) {{
      sa.innerHTML = `<div class="summary-text">${{d.summary}}</div>`;
    }}

    // Action
    const aa = document.getElementById('action-area');
    if (aa && d.recommended_action) {{
      const cls = actionClass(d.recommended_action);
      aa.innerHTML = `<div class="action-box ${{cls}}" style="margin-top:16px;">
        ⚡ ${{d.recommended_action}}</div>`;
    }}

    // Stop polling once call is done AND summary is ready
    if (d.call_status === 'COMPLETED' && d.summary &&
        !d.summary.startsWith('[Summary')) {{
      pollActive = false;
      const ri = document.getElementById('refresh-info');
      if (ri) ri.textContent = 'Analysis complete.';
      const dot = document.querySelector('.live-dot');
      if (dot) dot.style.animation = 'none';
      console.log('Case complete — polling stopped.');
    }}

    lastStatus = d.call_status || lastStatus;
  }} catch(e) {{
    console.error('Poll error:', e);
  }}
}}

setInterval(poll, 3000);
poll(); // immediate first poll

document.getElementById('retrigger-btn').addEventListener('click', async () => {{
    const btn = document.getElementById('retrigger-btn');
    btn.textContent = 'Triggering...';
    btn.disabled = true;
    try {{
        await fetch('/case/{case_id}/retrigger', {{ method: 'POST' }});
        // The poll loop will catch the status change and update the UI
        pollActive = true; 
        const ri = document.getElementById('refresh-info');
        if (ri) ri.textContent = 'Auto-refreshing every 3s';
        const dot = document.querySelector('.live-dot');
        if (dot) dot.style.animation = 'pulse 1.5s infinite';
        
        // Wait a bit before resetting button text
        setTimeout(() => {{
            btn.textContent = '📞 Re-trigger Call';
            btn.disabled = false;
        }}, 3000);
    }} catch(e) {{
        console.error(e);
        btn.textContent = 'Error';
    }}
}});
</script>
</body>
</html>"""


def _action_box_html(action: str | None) -> str:
    if not action:
        return ""
    a = action.lower()
    cls = ("action-allow"    if "allow"    in a else
           "action-block"    if "block"    in a else
           "action-escalate" if "escalate" in a else
           "action-hold")
    return f'<div class="action-box {cls}" style="margin-top:16px;">⚡ {action}</div>'


# ══════════════════════════════════════════════════════════════════════════════
# ENTRYPOINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"🚀 Fincrime Voice Engine starting on port {PORT}")
    print(f"   Dograh API  : {DOGRAH_API_URL}")
    print(f"   Workflow ID : {DOGRAH_WORKFLOW_ID or 'NOT SET (simulation mode)'}")
    print(f"   Ollama URL  : {OLLAMA_BASE_URL}")
    print(f"   Ollama Model: {OLLAMA_MODEL}")
    print(f"   Audit Log   : {FINCRIME_AUDIT_LOG}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
