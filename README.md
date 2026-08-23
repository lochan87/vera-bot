# Vera Challenge Submission

## Approach
A deterministic, context-grounded composer with trigger-specific routing. The system combines CategoryContext, MerchantContext, TriggerContext and optional CustomerContext, then produces a concise WhatsApp message with one primary CTA, correct sender attribution, a suppression key and a rationale.

The API layer persists incremental context versions and maintains lightweight conversation state. The reply handler detects stop requests, repeated/canned auto-replies, explicit commitments and deferments before choosing the next action.

## Tradeoffs
- Deterministic rules avoid latency, API cost and hallucination risk.
- Trigger-specific templates sacrifice some linguistic variety for reliable rubric alignment.
- No external data is fetched; all claims are grounded in supplied context.
- Multi-turn handling is intentionally conservative: act on explicit intent, stop quickly on spam/auto-replies, and avoid repeated qualification.

## Additional context that would help
Real historical message-level engagement labels and category-specific conversion outcomes would allow better ranking of competing triggers and stronger language personalization.
