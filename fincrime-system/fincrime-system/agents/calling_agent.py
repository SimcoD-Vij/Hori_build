"""
Calling agent -- and its siblings SMS/email, since a phone call isn't
always the right channel. Hard rule: this agent refuses to run on any
AML_BRANCH case, no matter what calls it. That refusal is not
configurable from outside this file.
"""

QUESTION_BANKS = {
    "Card-not-present fraud": [
        "Do you recognize a charge of {amount} at {merchant} on {date}?",
        "Have you shopped with this merchant before, even under a different name or site?",
        "Is your card physically in your possession right now?",
        "Has anyone else had access to your card or card number recently?",
    ],
    "Account takeover": [
        "Did you log in from a new device or location recently?",
        "Have you changed your password or received an unrequested password-reset email?",
        "Do you recognize this device: {device}?",
    ],
    "Wire fraud / BEC": [
        "Can you confirm you personally authorized this transfer to {beneficiary}?",
        "Did you receive any request to change payment details recently, by email or phone?",
        "Was this transfer requested with unusual urgency or secrecy?",
    ],
    "Romance / advance-fee pattern": [
        "Have you met the recipient of this transfer in person?",
        "Were you asked to keep this transfer confidential from family or the bank?",
    ],
    "NOVEL_PATTERN": [
        "We noticed some unusual structural patterns in your recent account activity. Can you confirm if you've authorized all recent incoming and outgoing transfers?",
        "Are you using your account for any new business purposes or third-party transactions recently?",
        "Has anyone else been given access to manage or route funds through your account?",
    ],
    "default": [
        "Can you tell me a bit about this transaction: {amount} on {date}?",
        "Was this something you personally initiated?",
    ],
}

# Keyword heuristics for the template-mode classifier (replaced by an LLM call when configured)
UNSATISFACTORY_SIGNALS = ["don't know", "not sure", "someone else", "confidential", "don't remember", "secret"]
SATISFACTORY_SIGNALS = ["yes i made", "that was me", "i authorized", "i bought", "i remember"]


def select_questions(case_type: str, context: dict) -> list:
    bank = QUESTION_BANKS.get(case_type, QUESTION_BANKS["default"])
    return [q.format(**{**context, "merchant": context.get("merchant", "the merchant"),
                          "amount": context.get("amount", "this amount"),
                          "date": context.get("date", "this date"),
                          "device": context.get("device", "this device"),
                          "beneficiary": context.get("beneficiary", "this recipient")})
            for q in bank]


def classify_response(transcript: str) -> str:
    """template-mode classifier -- swap for an LLM call via llm_client when a key is configured."""
    t = transcript.lower()
    if any(s in t for s in UNSATISFACTORY_SIGNALS):
        return "unsatisfactory"
    if any(s in t for s in SATISFACTORY_SIGNALS):
        return "satisfactory"
    return "unsatisfactory"  # conservative default: ambiguous responses never auto-close


def send_sms(account_id: str, message: str) -> dict:
    # Stub: wire to Twilio/SMS gateway in production. Logs the intent so the pipeline is testable end to end.
    return {"channel": "sms", "account_id": account_id, "message": message, "status": "simulated_sent"}


def send_email(account_id: str, subject: str, body: str) -> dict:
    return {"channel": "email", "account_id": account_id, "subject": subject, "body": body, "status": "simulated_sent"}


def run_calling_agent(case: dict, branch: str, customer_response: str = None) -> dict:
    if branch != "FRAUD_BRANCH":
        raise PermissionError(
            "Calling agent refused: case is not FRAUD_BRANCH. Contacting the customer on an "
            "AML-branch case would constitute illegal tipping-off. This check cannot be bypassed."
        )

    case_type = case.get("matched_typology", "default")
    questions = select_questions(case_type, case)

    # HITL (Human-In-The-Loop) Update: 
    # AI no longer calls automatically. We prepare the questions and wait for the investigator's approval.
    return {
        "status": "awaiting_human_decision", 
        "questions_prepared": questions
    }

