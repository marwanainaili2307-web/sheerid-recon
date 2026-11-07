# SheerID Verification Automation - Complete Solution

## Overview
Automates the Google One AI Student verification flow:
1. **SheerID Personal Info** → 2. **SSO/Document Upload** → 3. **Success Redirect** → 4. **Google One Checkout with Offer Injection**

## Discovered APIs

### Program Configuration
```
GET https://my.sheerid.com/rest/v2-beta/programs/67c8c14f5f17a83b745e3f82/configuration
```
Returns: countries, locales, org search URL, feature flags, fingerprinting endpoint

### Organization Search
```
GET https://orgsearch.sheerid.net/rest/organization/search?tags=HEI%2Cqualifying_ps%2CTITLE4&country={CC}&type=UNIVERSITY%2CPOST_SECONDARY&accountId=67c88d2e0536336550bc3bd6&name={query}
```
Returns: Array of organizations with `id`, `idExtended`, `name`, `country`, `type`

Example (Algeria):
```json
[
  {"id": 10037983, "name": "Télé-Université Frères Mentouri Constantine1", "country": "DZ", "type": "UNIVERSITY"},
  {"id": 10070294, "name": "University of Science and Technology Houari Boumediene", "country": "DZ", "type": "UNIVERSITY"}
]
```

### Verification Status
```
GET https://my.sheerid.com/rest/v2/verification/{verificationId}
```
Returns: `currentStep`, `verificationId`, `submissionUrl`, `availableStatuses`

### Form Submission (Personal Info)
```
POST https://my.sheerid.com/rest/v2/verification/{verificationId}/step/collectStudentPersonalInfo
```
**Payload format (exact):**
```json
{
  "firstName": "Mohamed",
  "lastName": "Benali",
  "email": "mohamed@example.com",
  "country": "DZ",
  "organization": {"idExtended": "10037983"},
  "birthDate": {"month": 6, "day": 15, "year": 2000},
  "locale": "en-US",
  "captchaToken": "<turnstile_token>",
  "deviceFingerprintHash": "<fingerprint_token>",
  "metadata": {
    "verificationId": "<TEST_VID_REDACTED>",
    "redirectUrl": "https://one.google.com/ai-student"
  }
}
```

### Fingerprinting
```
POST https://worker.sheerid.com/GJUnkoPlgxsjeU7r/qI2joK0PpxwbeNSN
Body: {"verificationId": "..."}
Returns: {"token": "..."}
```

## Multi-Step Flow

| Step | data-testid | Description |
|------|-------------|-------------|
| 1 | `sid-step-student-personal-info` | Personal info + CAPTCHA |
| 2 | `sid-step-sso` | School SSO login (opens new tab) |
| 3 | `sid-step-docUpload` | Document upload (if SSO fails) |
| 4 | `sid-step-pending` | Review queue ("few minutes") |
| 5 | `sid-step-success` | Redirect to Google One |

## CAPTCHA: Cloudflare Turnstile
- **Sitekey**: Extracted from page (varies per session)
- **Solving**: 2Captcha API (`method=turnstile`)
- **Injection**: Token auto-picked by widget, also set in `input[name="cf-turnstile-response"]`

## Offer Tokens (Post-Verification Checkout)
From the Chrome extension (`inject.js`):

| Duration | Token |
|----------|-------|
| 12m | `REDACTED_OFFER_TOKEN=` |
| 6m_ss | `REDACTED_OFFER_TOKEN=` |
| 6m | `REDACTED_OFFER_TOKEN=` |
| 6m_2 | `REDACTED_OFFER_TOKEN=` |

**YouTube Premium Add-on**: `REDACTED_OFFER_TOKEN`

**Prefix**: `subs:com.google.android.apps.subscriptions.red:`

## Injection Script (for Checkout)
Intercepts `XMLHttpRequest.send` and `HTMLIFrameElement.src` to replace subscription items with offer tokens.

## Files Created

| File | Description |
|------|-------------|
| `sheerid_automation.py` | API-only client (reverse-engineered endpoints) |
| `sheerid_browser_automation.py` | Full Playwright automation with CAPTCHA solving |
| `run_automation.py` | Step generator for browser_use tool |
| `requirements_browser.txt` | Playwright + httpx |
| `extension/` | Chrome extension for offer injection |

## Requirements for Full Automation

1. **Real browser environment** (Playwright + Stealth)
   - musl/aarch64 doesn't support Playwright binaries
   - Need x86_64 Linux or macOS/Windows

2. **2Captcha API key** for Turnstile solving
   - Cost: ~$1-3 per 1000 solves

3. **Student data**:
   - Full name (matches school records)
   - Date of birth
   - Email
   - Country + university name
   - Verification ID from offer link

## Telegram Bot Integration

The automation can be wrapped in a Telegram bot:
1. User provides data via conversation
2. Bot runs browser automation
3. Returns verification status + checkout result

## Known Issues in Current Environment

- **PRoot sandbox blocks Cloudflare Turnstile** (iframe doesn't render)
- **Playwright not available on musl/aarch64**
- **Autocomplete needs real browser** (React-controlled inputs)

## Working Alternative

Use the API client + manual CAPTCHA:
```bash
# 1. Get org ID
python3 -c "
import httpx, json
r = httpx.get('https://orgsearch.sheerid.net/rest/organization/search?tags=HEI%2Cqualifying_ps%2CTITLE4&country=DZ&type=UNIVERSITY%2CPOST_SECONDARY&accountId=67c88d2e0536336550bc3bd6&name=University')
print(json.dumps(r.json(), indent=2))
"

# 2. Get fingerprint
python3 -c "
import httpx
r = httpx.post('https://worker.sheerid.com/GJUnkoPlgxsjeU7r/qI2joK0PpxwbeNSN', json={'verificationId': 'YOUR_ID'})
print(r.json())
"

# 3. Fill form in browser manually, capture network request for exact payload
# 4. Replay with API client
```

## Next Steps for Production

1. Deploy on x86_64 server with Playwright
2. Integrate 2Captcha
3. Add session persistence (cookies, localStorage)
4. Handle SSO step (requires school credentials)
5. Add document upload handling
6. Implement checkout with offer injection
7. Wrap in Telegram bot with conversation flow