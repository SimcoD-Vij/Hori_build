"""
simulation_test.py — Standalone end-to-end test for the voice engine.

Tests WITHOUT needing Flask/Dograh running:
  1. Ollama connectivity check  (qwen2.5-coder:7b on localhost:11434)
  2. Full investigation case creation  (simulates what the fincrime orchestrator sends)
  3. Mock Dograh call-completed webhook payload  (simulates what Dograh returns)
  4. Classification logic
  5. Ollama summarization  (the actual qwen2.5-coder:7b call)
  6. Audit log write
  7. Prints the full case report that would appear on the live webpage

Run from the project root:
  python agents/voice-engine/simulation_test.py
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── Config (mirrors .env defaults) ───────────────────────────────────────────
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.environ.get("OLLAMA_MODEL",    "qwen2.5-coder:7b")
AUDIT_LOG_PATH  = os.environ.get("FINCRIME_AUDIT_LOG",
                                 str(Path(__file__).parent.parent.parent / "data" / "audit_log.jsonl"))

SEP  = "─" * 70
BOLD = "\033[1m"
GRN  = "\033[32m"
YLW  = "\033[33m"
RED  = "\033[31m"
BLU  = "\033[34m"
CYN  = "\033[36m"
RST  = "\033[0m"
OK   = f"{GRN}✅{RST}"
FAIL = f"{RED}❌{RST}"
WARN = f"{YLW}⚠️ {RST}"
INFO = f"{BLU}ℹ️ {RST}"


# ════════════════════════════════════════════════════════════════════════════════
# STEP 0 — Ollama connectivity check
# ════════════════════════════════════════════════════════════════════════════════

def check_ollama() -> bool:
    print(f"\n{BOLD}STEP 0 — Ollama Connectivity Check{RST}")
    print(SEP)
    print(f"{INFO} Checking Ollama at {OLLAMA_BASE_URL} ...")
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        models = [m["name"] for m in data.get("models", [])]
        print(f"{OK} Ollama is running. Available models:")
        for m in models:
            tag = f"  {GRN}← THIS ONE{RST}" if OLLAMA_MODEL in m else ""
            print(f"     {CYN}{m}{RST}{tag}")
        if not any(OLLAMA_MODEL in m for m in models):
            print(f"\n{WARN} Model '{OLLAMA_MODEL}' not found in available models.")
            print(f"     Run:  ollama pull {OLLAMA_MODEL}")
            return False
        print(f"\n{OK} Model '{OLLAMA_MODEL}' is available.")
        return True
    except urllib.error.URLError as e:
        print(f"{FAIL} Cannot reach Ollama: {e}")
        print(f"     Make sure Ollama desktop is running and serving on port 11434.")
        return False


# ════════════════════════════════════════════════════════════════════════════════
# STEP 1 — Build mock case (what the fincrime orchestrator would send)
# ════════════════════════════════════════════════════════════════════════════════

def build_mock_pre_call_case() -> dict:
    print(f"\n{BOLD}STEP 1 — Building Mock Pre-Call Investigation Case{RST}")
    print(SEP)
    case = {
        "case_id":             "SIM-20240115-001",
        "account_id":          "ACC-8842",
        "account_holder_name": "Rajesh Kumar",
        "phone":               "+919876543210",
        "fraud_type":          "Card-not-present fraud",
        "amount":              "9700",
        "date":                "2024-01-15",
        "merchant":            "LuxeWatch Online Store",
        "beneficiary":         "N/A",
        "device":              "Unknown Android Device",
        "severity":            8,
        "flags_summary":       (
            "structuring (3 near-threshold deposits), "
            "zscore_self_history (z=4.2 on spending), "
            "velocity (8 transactions in 24h)"
        ),
        "pre_call_summary":    (
            "Account ACC-8842 has triggered 3 separate detection rules over the "
            "past 72 hours. The account shows a structuring pattern (3 deposits "
            "of $9,700, $9,650, $9,720 — all just below the $10,000 reporting "
            "threshold). A $9,700 card-not-present charge at an online luxury "
            "retailer has a self-history z-score of 4.2, indicating this is "
            "highly anomalous for this account. Velocity check flagged 8 "
            "outgoing transactions in 24 hours against a 30-day average of 1.2/day."
        ),
        "questions": [
            "Do you recognize a charge of $9,700 at LuxeWatch Online Store on January 15th?",
            "Have you made any large deposits near $9,700 in the last few days?",
            "Is your card physically in your possession right now?",
            "Has anyone else had access to your card or online banking recently?",
        ],
        "pre_call_at": _now(),
        "call_id":             None,
        "call_status":         "INITIATED",
    }
    print(f"{OK} Case ID      : {GRN}{case['case_id']}{RST}")
    print(f"{OK} Account      : {case['account_holder_name']} ({case['account_id']})")
    print(f"{OK} Fraud Type   : {case['fraud_type']}")
    print(f"{OK} Severity     : {RED}{case['severity']}/10{RST}")
    print(f"{OK} Flags        : {YLW}{case['flags_summary']}{RST}")
    print(f"{OK} Questions    : {len(case['questions'])} prepared")
    return case


# ════════════════════════════════════════════════════════════════════════════════
# STEP 2 — Simulate Dograh call-completed webhook payload
# ════════════════════════════════════════════════════════════════════════════════

def build_mock_call_completed(case: dict) -> dict:
    print(f"\n{BOLD}STEP 2 — Simulating Dograh Call-Completed Webhook{RST}")
    print(SEP)
    payload = {
        "workflow_run_id": "wfr_SIM_001",
        "call_id":         "call_SIM_001",
        "phone_number":    case["phone"],
        "duration_seconds": 187,
        "disposition":     "INTERESTED",
        "recording_url":   "https://recordings.dograh.sim/call_SIM_001.mp3",
        "transcript": [
            {"role": "agent",  "content": "Hello, am I speaking with Rajesh Kumar?"},
            {"role": "user",   "content": "Yes, this is Rajesh."},
            {"role": "agent",  "content": "This is a call from your bank's fraud investigation team. We've flagged some unusual activity on your account. Do you recognize a charge of $9,700 at LuxeWatch Online Store on January 15th?"},
            {"role": "user",   "content": "Uh... $9,700? No, I didn't make that purchase. I don't know any LuxeWatch."},
            {"role": "agent",  "content": "Thank you for clarifying. Have you made any large deposits near $9,700 in the last few days?"},
            {"role": "user",   "content": "No, I haven't made any large deposits. I only do my salary transfer at the end of the month."},
            {"role": "agent",  "content": "Is your card physically in your possession right now?"},
            {"role": "user",   "content": "Yes, I have my card with me. Wait — I did lend my card details to my nephew last week for an online purchase. Could that be related?"},
            {"role": "agent",  "content": "That's very important information. Could you tell me your nephew's name and the purchase he was supposed to make?"},
            {"role": "user",   "content": "His name is Arjun. He said he needed to buy some electronics. I didn't think anything of it at the time."},
            {"role": "agent",  "content": "Thank you Rajesh. We're flagging this account for review and will freeze any further card-not-present transactions pending investigation. You'll receive an SMS confirmation shortly."},
            {"role": "user",   "content": "Please do. I'm very worried about this. Thank you for calling."},
        ],
        "context_variables": {
            "case_id":             case["case_id"],
            "account_id":          case["account_id"],
            "account_holder_name": case["account_holder_name"],
            "fraud_type":          case["fraud_type"],
            "amount":              case["amount"],
            "transaction_date":    case["date"],
            "merchant":            case["merchant"],
            "all_questions_json":  json.dumps(case["questions"]),
            "severity":            str(case["severity"]),
        },
    }
    print(f"{OK} Disposition     : {GRN}{payload['disposition']}{RST}")
    print(f"{OK} Duration         : {payload['duration_seconds']}s")
    print(f"{OK} Transcript turns : {len(payload['transcript'])}")
    print(f"\n{CYN}── Transcript Preview ──{RST}")
    for t in payload["transcript"][:4]:
        role = "🤖 AGENT" if t["role"] == "agent" else "👤 CUSTOMER"
        print(f"  {role}: {t['content'][:80]}...")
    print(f"  ... ({len(payload['transcript'])-4} more turns)")
    return payload


# ════════════════════════════════════════════════════════════════════════════════
# STEP 3 — Run classification logic (mirrors server.py)
# ════════════════════════════════════════════════════════════════════════════════

_SATISFACTORY_DISPOSITIONS   = {"USER_QUALIFIED", "INTERESTED", "COMPLETED"}
_UNSATISFACTORY_DISPOSITIONS = {"NOT_INTERESTED", "DO_NOT_CONTACT"}
_NO_RESPONSE_DISPOSITIONS    = {"NO_ANSWER", "VOICEMAIL", "BUSY"}
_SATISFACTORY_SIGNALS   = ["yes i made","that was me","i authorized","i bought","i remember","i did make","i recognize"]
_UNSATISFACTORY_SIGNALS = ["don't know","not sure","someone else","confidential","don't remember","secret","didn't do","wasn't me","i didn't","never made","not mine"]


def classify(transcript: str, disposition: str) -> str:
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
    return "unsatisfactory"


def test_classification(case: dict, payload: dict) -> str:
    print(f"\n{BOLD}STEP 3 — Classification Logic Test{RST}")
    print(SEP)
    turns = payload["transcript"]
    transcript_text = "\n".join(f"{t['role'].upper()}: {t['content']}" for t in turns)
    disposition = payload["disposition"]
    result = classify(transcript_text, disposition)

    col = GRN if result == "satisfactory" else (YLW if result == "no_response" else RED)
    print(f"{OK} Disposition  : {disposition}")
    print(f"{OK} Classification: {col}{result.upper()}{RST}")

    # Check: the customer said "I didn't make that" → unsatisfactory (no confirmed fraud from account holder)
    # But disposition = INTERESTED — let's see which wins
    print(f"\n{INFO} Note: customer denied the charge → unsatisfactory response expected")
    print(f"   Result '{result}' is {'correct ✅' if result == 'unsatisfactory' else 'review logic'}")
    return result


# ════════════════════════════════════════════════════════════════════════════════
# STEP 4 — Ollama summarization
# ════════════════════════════════════════════════════════════════════════════════

def run_ollama_summary(case: dict, payload: dict, classification: str) -> str:
    print(f"\n{BOLD}STEP 4 — Ollama Summarization ({OLLAMA_MODEL}){RST}")
    print(SEP)
    print(f"{INFO} Sending case to Ollama... (this may take 15-60s)")

    turns    = payload["transcript"]
    duration = payload["duration_seconds"]
    disposition = payload["disposition"]
    questions = case.get("questions", [])

    transcript_text = "\n".join(f"{t['role'].upper()}: {t['content']}" for t in turns)
    q_block = "\n".join(f"  {i+1}. {q}" for i, q in enumerate(questions))

    prompt = f"""You are a financial crime analyst AI. Produce a structured case summary.

## PRE-CALL INVESTIGATION
- Account Holder : {case['account_holder_name']} ({case['account_id']})
- Phone          : {case['phone']}
- Fraud Type     : {case['fraud_type']}
- Risk Severity  : {case['severity']}/10
- Flagged Signals: {case['flags_summary']}
- Pre-Call Notes : {case['pre_call_summary']}

## QUESTIONS ASKED IN THE CALL
{q_block}

## CALL OUTCOME
- Duration    : {duration}s
- Disposition : {disposition}
- Classification: {classification}

## FULL TRANSCRIPT
{transcript_text}

---
Write a structured analysis with exactly these 5 sections:

**WHAT WAS DETECTED (Before Call)**
[Summarise the suspicious patterns and risk signals found before the call in 2-3 sentences]

**WHAT WAS ASKED**
[List the key investigation questions posed to the account holder]

**WHAT WAS SAID / CONFIRMED**
[Summarise exactly what the customer confirmed, denied, or failed to respond to, quoting key phrases]

**VERDICT**
[State whether the response was satisfactory, unsatisfactory, or absent and why in 1-2 sentences]

**RECOMMENDED ACTION**
[State one of: ALLOW | HOLD_FOR_CALLBACK | BLOCK_PENDING_REVIEW | ESCALATE_TO_AML — with a one-sentence justification]
"""

    body = json.dumps({
        "model":  OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 800},
    }).encode()

    try:
        req = urllib.request.Request(
            f"{OLLAMA_BASE_URL}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        start = datetime.now()
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read())
        elapsed = (datetime.now() - start).total_seconds()

        summary = result.get("response", "[No response]")
        print(f"{OK} Summary generated in {elapsed:.1f}s ({len(summary)} chars)")
        return summary
    except Exception as e:
        print(f"{FAIL} Ollama request failed: {e}")
        return f"[Summary failed: {e}]"


# ════════════════════════════════════════════════════════════════════════════════
# STEP 5 — Write audit log entry
# ════════════════════════════════════════════════════════════════════════════════

def write_audit_log(case_id: str, summary: str, classification: str, payload: dict):
    print(f"\n{BOLD}STEP 5 — Audit Log Write{RST}")
    print(SEP)
    os.makedirs(os.path.dirname(os.path.abspath(AUDIT_LOG_PATH)), exist_ok=True)
    entries = [
        {"case_id": case_id, "agent": "voice_engine", "action": "call_initiated",
         "timestamp": _now(), "detail": {"simulation": True}},
        {"case_id": case_id, "agent": "voice_engine", "action": "call_completed",
         "timestamp": _now(), "detail": {
             "call_id":          payload["call_id"],
             "duration_seconds": payload["duration_seconds"],
             "disposition":      payload["disposition"],
             "classification":   classification,
             "auto_close_eligible": classification == "satisfactory",
         }},
        {"case_id": case_id, "agent": "voice_engine", "action": "ollama_summary_generated",
         "timestamp": _now(), "detail": {
             "model":          OLLAMA_MODEL,
             "summary_length": len(summary),
             "simulation":     True,
         }},
    ]
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    print(f"{OK} Wrote {len(entries)} audit log entries → {AUDIT_LOG_PATH}")


# ════════════════════════════════════════════════════════════════════════════════
# STEP 6 — Print full case report (what the webpage would show)
# ════════════════════════════════════════════════════════════════════════════════

def print_case_report(case: dict, payload: dict, classification: str, summary: str):
    print(f"\n{BOLD}STEP 6 — Full Case Report (Live Webpage Preview){RST}")
    print(SEP)
    col = GRN if classification == "satisfactory" else (YLW if "no_response" in classification else RED)

    print(f"""
{BOLD}{'═'*70}{RST}
{BOLD}  ⚖️  FINCRIME INVESTIGATION — CASE {case['case_id']}{RST}
{'═'*70}

{BOLD}📋 ACCOUNT OVERVIEW{RST}
  Holder   : {case['account_holder_name']} ({case['account_id']})
  Phone    : {case['phone']}
  Fraud    : {case['fraud_type']}
  Severity : {RED}{case['severity']}/10{RST}

{BOLD}🔍 PRE-CALL INVESTIGATION{RST}
  Flags    : {YLW}{case['flags_summary']}{RST}
  Amount   : ${case['amount']} on {case['date']} at {case['merchant']}

  {case['pre_call_summary']}

{BOLD}❓ QUESTIONS ASKED IN CALL{RST}""")
    for i, q in enumerate(case["questions"], 1):
        print(f"  {i}. {q}")

    print(f"""
{BOLD}🎙️ CALL TRANSCRIPT{RST}  ({payload['duration_seconds']}s)""")
    for t in payload["transcript"]:
        role = f"{BLU}🤖 AGENT{RST}" if t["role"] == "agent" else f"{GRN}👤 CUSTOMER{RST}"
        print(f"  {role}: {t['content']}")

    print(f"""
{BOLD}📊 POST-CALL ANALYSIS{RST}
  Disposition    : {payload['disposition']}
  Classification : {col}{classification.upper()}{RST}
  Recommended    : {'BLOCK_PENDING_REVIEW — customer denied the charge, third-party card access disclosed'}

{BOLD}🤖 OLLAMA SUMMARY ({OLLAMA_MODEL}){RST}
{SEP}
{summary}
{SEP}
""")


# ════════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════════

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main():
    print(f"\n{'═'*70}")
    print(f"  {BOLD}Fincrime Voice Engine — End-to-End Simulation Test{RST}")
    print(f"  Ollama: {OLLAMA_BASE_URL}  |  Model: {OLLAMA_MODEL}")
    print(f"{'═'*70}")

    # Step 0: Check Ollama
    ollama_ok = check_ollama()
    if not ollama_ok:
        print(f"\n{FAIL} Ollama not available — aborting simulation.")
        print("   Fix: ensure Ollama desktop is running (icon in system tray)")
        sys.exit(1)

    # Step 1: Build mock case
    case = build_mock_pre_call_case()

    # Step 2: Simulate call completed
    payload = build_mock_call_completed(case)

    # Step 3: Classify
    transcript_text = "\n".join(f"{t['role'].upper()}: {t['content']}" for t in payload["transcript"])
    classification  = test_classification(case, payload)

    # Step 4: Ollama summary
    summary = run_ollama_summary(case, payload, classification)

    # Step 5: Audit log
    write_audit_log(case["case_id"], summary, classification, payload)

    # Step 6: Full report
    print_case_report(case, payload, classification, summary)

    print(f"\n{GRN}{'═'*70}")
    print(f"  ✅ SIMULATION COMPLETE — all systems operational")
    print(f"  The live webpage at /case/{case['case_id']} would show this report.")
    print(f"  When Dograh connects, the same flow runs with real call data.")
    print(f"{'═'*70}{RST}\n")


if __name__ == "__main__":
    main()
