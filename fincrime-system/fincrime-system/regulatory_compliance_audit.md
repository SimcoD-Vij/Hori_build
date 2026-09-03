# Fincrime System — Regulatory & Standards Compliance Audit

> Scope: full codebase scan across `detection/`, `agents/`, `realtime/`, `audit_log.py`, `main.py`, `orchestrator.py`, and the `voice-engine/server.py`. No changes are proposed here — this is a read-only audit.

---

## Crimes Being Investigated (Covered Typologies)

The system explicitly covers 8 typologies across two branches:

| Branch | Typology | Detection Signal |
|---|---|---|
| **FRAUD_BRANCH** | Card-not-present fraud | `zscore_self_history`, `supervised_ml` |
| **FRAUD_BRANCH** | Account takeover | `velocity`, `zscore_self_history` |
| **AML_BRANCH** | Structuring | Cash deposits clustered near $10k threshold |
| **AML_BRANCH** | Mule network layering | `graph_fan_hub`, `velocity` |
| **AML_BRANCH** | Round-tripping | `graph_round_trip` |
| **AML_BRANCH** | Synthetic identity ring / Bust-out | `synthetic_identity_ring`, `bust_out` |
| **AML_BRANCH** | Unusual for peer group | `peer_group_deviation` |
| **AML_BRANCH** | Novel / undetermined pattern | `unsupervised_novel_pattern` |

The voice agent (`calling_agent.py`) covers 4 of these directly with question banks:
- Card-not-present fraud
- Account takeover
- Wire fraud / BEC
- Romance / advance-fee pattern

> ⚠️ **Gap**: Wire fraud/BEC and Romance/advance-fee are in the voice agent's question bank but **NOT** in `typology_catalog.json` or the detection rule engine. These are investigated only if the voice agent is called — they cannot be triggered automatically by the detection layer.

---

## 1. BSA/AML — Bank Secrecy Act Compliance

### ✅ What is present
- The AML/FRAUD branch split in `pretransaction_screening.py` correctly prevents customer contact on AML-pattern hits to **avoid tipping-off** (this is legally required under 31 U.S.C. § 5318(g)(2)):
  ```python
  "reasoning": "...an AML-pattern match means contact could constitute tipping-off."
  ```
- Structuring detection threshold is set at **$10,000** — the correct BSA CTR (Currency Transaction Report) reporting threshold (31 CFR 1010.311).

### ❌ Gaps
- **No SAR (Suspicious Activity Report) drafting or filing workflow exists.** The `explanation_agent.py` generates case reports, but there is no SAR template, 30-day clock tracking, or FinCEN filing stub. The `INTEGRATION_AND_OPERATIONS_GUIDE.md` mentions this feature as planned but it is not implemented.
- **No CTR (Currency Transaction Report) generation** for cash transactions exceeding $10,000. The rule correctly detects these — but there is nothing downstream that generates a CTR filing.
- **No SAR deadline timer**: From the moment a case is escalated (`ESCALATE_TO_HUMAN_REVIEW`), the 30-day SAR filing clock starts. There is no timestamp tracking for this in the audit log entries.

### Recommendation
Add a `sar_deadline` field to every audit log entry where `action = ESCALATE_TO_HUMAN_REVIEW`. Even a simple `"sar_deadline": (timestamp + 30 days).isoformat()` in the log would provide the minimum traceability needed.

---

## 2. SR 11-7 — Model Risk Management

### ✅ What is present
- `explanation_agent.py` records `model_rule_versions` in every report (`"rules_engine": "v1.0"`, `"supervised_model": "RandomForest-v1"`). This is the right instinct — you need version traceability.
- The rules engine (`detection/rules.py`) is fully deterministic and documented with inline evidence strings. Every flag includes a human-readable `evidence` field — this is strong explainability.
- The `verification_agent.py` (consistency checks) aligns with SR 11-7's ongoing monitoring requirement.
- The `redteam_agent.py` is a structural approach to adversarial testing — aligns well with SR 11-7's model challenge/stress-testing requirement.

### ❌ Gaps
- **No formal model validation documentation** — SR 11-7 requires a development evidence summary, an independent validation report, and ongoing performance monitoring. The `backtest.py` computes accuracy but does not produce a structured validation report artifact.
- **The ML model is re-trained on every startup** (`train_supervised()` called in `run_full_detection()` at startup). This means there is no stable, versioned, validated model artifact — the model changes every time the app restarts, making SR 11-7's "validate before deployment" requirement impossible to satisfy.
- **No drift detection** — neither the RandomForest nor the unsupervised model has any monitoring for data or concept drift between runs.

### Recommendation
At minimum, serialize the trained model to disk with a version hash and timestamp. Don't retrain on every startup — load the persisted model and only retrain on an explicit admin action.

---

## 3. GDPR Article 22 / CCPA — Automated Decision Rights

### ✅ What is present
- The `HOLD_FOR_VERIFICATION` path explicitly routes to a human-assisted resolution via the voice agent. This is the correct structural equivalent of "right to human review."
- A `BLOCK` decision from an AML pattern (`is_aml_pattern=True`) is explicitly documented as not subject to customer contact — this is compliant handling.
- The `resolve_hold()` function correctly prevents automatic clearance: ambiguous responses always default to BLOCK, not ALLOW.

### ❌ Gaps
- **No documented right-to-explanation response path for the customer.** When a transaction is blocked, the customer receives no explanation in the current system (which is intentional for AML cases), but there is no mechanism for a customer to formally request one either. GDPR Art. 22(3) requires this.
- **No data subject access request (DSAR) handling** — the audit log is queryable by `case_id` (not customer ID), so it cannot easily respond to a customer asking "what data do you have on me."

---

## 4. PCI DSS — Cardholder Data

### ✅ What is present
- The CNP fraud detection correctly handles card-not-present transactions without ever storing or logging raw card numbers. The data schema uses `sender_account` (an internal ID), not a PAN.
- The `.env` correctly keeps credentials outside source code.

### ⚠️ Notes
- If you wire this into a real payment rail and the candidate transaction ever carries a full PAN in the `candidate` dict, that dict gets written directly to `audit_log.jsonl`. You would immediately be in-scope for PCI DSS with no masking in place.
- **Recommendation**: Add a PAN masking step in `screen_transaction()` before passing the candidate to `_write_audit_log()` when real enforcement is enabled.

---

## 5. Auditability (Immutable Audit Log)

### ✅ What is present
- `audit_log.py` is correctly **append-only** (opens with `"a"` mode, never deletes).
- Every action across `main.py` — screening, enforcement, hold resolution — writes to the audit log with actor, action, and details.
- The voice engine's `server.py` has its own audit log (`_write_audit_log`) that is also append-only.

### ❌ Gaps
- **Timestamps use `datetime.now().isoformat()` (local time, no timezone)** in `audit_log.py`. Regulators need UTC timestamps. The voice engine uses `datetime.now(timezone.utc).isoformat()` correctly — but the main `audit_log.py` does not.
- **The audit log is a flat file** (`audit_log.jsonl`). At any real scale, this is not durable or tamper-evident. A proper implementation would write to an append-only database table or a WORM (Write Once, Read Many) storage system.
- **Decision-level explainability in the audit log is incomplete**: The log records the decision (ALLOW/BLOCK/HOLD) and the pattern name, but does not capture the specific threshold values that triggered it (e.g., z-score=4.2, threshold=3.0). A regulator needs to reconstruct *exactly* what the system knew and what threshold was crossed.

---

## 6. Idempotency & Concurrency

### ✅ What is present
- The voice engine webhook (`/webhooks/call-completed`) has a correct idempotency guard using `workflow_run_id`:
  ```python
  if run_id and run_id in _processed_run_ids:
      return jsonify({"status": "already_processed"})
  ```
- The voice engine uses `threading.Lock()` (`_CASES_LOCK`) for all reads/writes to the in-memory case store.

### ❌ Gaps
- **`main.py` uses a global `STATE = {}` and `PENDING_SCREENINGS = {}` with no locking.** If two requests resolve the same `screening_id` concurrently, there is a race condition on the `del PENDING_SCREENINGS[screening_id]` call — double-processing is possible.
- **`screen_transaction()` has no idempotency key** — the same transaction submitted twice produces two independent screening decisions and two separate audit log entries.

---

## 7. Fail-Safe / Timeout Policy

### ✅ What is present
- `resolve_hold()` explicitly defaults to BLOCK (not ALLOW) when the customer response is ambiguous — the correct conservative default.
- `enforcement.py` is completely disabled by default (`ENFORCEMENT_ENABLED=false`) — safe for demo/testing.

### ❌ Gaps
- **No explicit timeout policy is documented.** If the Ollama summarization thread hangs, the voice engine silently leaves `"summary": null` forever with no timeout. This is acceptable for the summary, but in a real system the overall screening call also needs a timeout SLA.
- **No circuit breaker** on the Dograh API call in `server.py`. If Dograh is unreachable, the `trigger_call()` function raises an unhandled exception and returns a 502. There is no retry, no fallback.

---

## Summary Table

| Standard | Coverage | Key Gap |
|---|---|---|
| **BSA/AML** | 🟡 Partial | No SAR generation, no SAR 30-day deadline tracking |
| **SR 11-7** | 🟡 Partial | Model retrained every startup (no stable versioned artifact), no formal validation report |
| **GDPR Art. 22** | 🟡 Partial | No formal right-to-explanation response channel for blocked customers |
| **PCI DSS** | 🟢 Safe (demo) | PAN masking missing when real enforcement is enabled |
| **Auditability** | 🟡 Partial | Local time in main audit log (should be UTC), no threshold values logged |
| **Idempotency** | 🟡 Partial | `PENDING_SCREENINGS` has a race condition, no idempotency key on `screen_transaction()` |
| **Tipping-off prevention** | 🟢 Implemented | AML-branch BLOCK explicitly bypasses customer contact |
| **Human-in-loop path** | 🟢 Implemented | HOLD_FOR_VERIFICATION routes to voice agent correctly |
| **Explainability** | 🟢 Good | Every flag has human-readable evidence string |
| **Fail-safe default** | 🟢 Implemented | Ambiguous response → BLOCK, not ALLOW |

---

## Top 3 Fixes Before Production

1. **Fix `audit_log.py` timestamp to UTC** — one-line fix, high regulatory importance.
2. **Add SAR deadline to escalation log entries** — BSA requirement, simple to add.
3. **Serialize ML model to disk and stop retraining on every startup** — SR 11-7 requirement, needed before any real validation claim.
