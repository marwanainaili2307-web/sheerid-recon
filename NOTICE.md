# NOTICE — Sanitization & Framing

This repository is a **security research record** of a SheerID student-verification
flow assessment (Google One / AI Pro program, 2026-11-06..07).

## Excluded from the public version (kept in the local archive only)

| Excluded | Why |
|----------|-----|
| `student_card.jpg` (collected document) | Real identity document — never distributed |
| Live offer/subscription tokens (`AVbCH/…` strings) | Third-party functional tokens; replaced with `REDACTED_OFFER_TOKEN` |
| Verification IDs, student personal data, contact addresses | PII; replaced with `<*_REDACTED>` placeholders |
| `browser.json`, `__pycache__` | Empty artifacts / build noise |

## Framing

- No live enrollment documents, working offer tokens, or verification IDs are included.
- The `extension/` directory is a **collected third-party artifact** (Chrome MV3
  extension, unattributed), preserved for source analysis only. Tokens are redacted;
  the code is not functional as shipped here.
- Findings are scoped to: unauthenticated org enumeration, static-format validation
  gaps, and the client-trusted offer-token injection mechanism (see README).
- Escalation path: this material is suitable for a Google VRP / SheerID advisory
  writeup. Not for use against live systems operated by SheerID or Google beyond
  the scope of lawful research and disclosure.

## Repro

All scripts call environment variables for credentials:
`BROWSER_USE_API_KEY` (+ `_BACKUP`). No secrets in repo.
