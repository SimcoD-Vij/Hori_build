# Customer Call Questions — Full Reference

## Critical rule before anything else

This script applies **only** to cases classified `FRAUD_BRANCH`. If a case
is `AML_BRANCH`, there is no call and no questions — contacting the
customer would be illegal tipping-off. This is enforced in code and is
never optional.

---

## Stage 1 — Identity Verification (always asked first, every call)

1. Can you confirm the name on the account and your date of birth?
2. Can you confirm the last four digits of your account or card number?
3. Can you confirm the registered address or phone number on file?

*Never ask for full SSN/ID number, PIN, password, or full card number —
partial identifiers only.*

---

## Stage 2 — Core Regulatory Questions (always asked second, every case)

Grounded in FATF Recommendation 10 (Customer Due Diligence): identify,
verify authorization, and understand the purpose and nature of the
transaction/relationship.

4. Did you personally authorize this transaction of {amount} on {date}?
5. Can you tell me the purpose of this transaction?
6. What is your relationship to {beneficiary}?
7. Is this a one-time transaction, or part of a regular arrangement with {beneficiary}?

---

## Stage 3 — Typology-Specific Questions (asked last, varies by case)

**Card-not-present fraud:**
8. Do you recognize a charge of {amount} at {merchant} on {date}?
9. Have you shopped with this merchant before, even under a different name or site?
10. Is your card physically in your possession right now?
11. Has anyone else had access to your card or card number recently?

**Account takeover:**
8. Did you log in from a new device or location recently?
9. Have you changed your password or received an unrequested password-reset email?
10. Do you recognize this device: {device}?

**Wire fraud / BEC:**
8. Did you receive any request to change payment details recently, by email or phone?
9. Was this transfer requested with unusual urgency or secrecy?
10. Did you independently verify this request through a known phone number or in person, separate from the message that asked for the transfer?

**Romance / advance-fee pattern:**
8. Have you met {beneficiary} in person?
9. Were you asked to keep this transfer confidential from family or the bank?
10. Has {beneficiary} asked you for money before this transaction?

**Default (no specific typology matched):**
8. Was this something you personally initiated?

---

## Never ask, on any call, for any case type

- Anything naming the internal rule, model score, or typology that triggered the call
- Full SSN/national ID, PIN, password, or full card number
- Race, religion, national origin, or immigration status
- Leading questions that presuppose guilt ("why did you commit fraud")
- Anything suggesting AML/money-laundering suspicion specifically — that suspicion means this call never happens at all

---

## How answers are classified

- **Satisfactory** (eligible for auto-close): concrete, specific detail — "Yes, I bought a laptop from that site last week."
- **Unsatisfactory** (always routes to human review): vague, evasive, or containing a red-flag phrase — "I'm not sure," "someone else might have," "I was asked to keep it confidential."
- **No response**: always routes to human review.

Ambiguous answers never auto-clear a case.