#!/usr/bin/env python3
"""
SheerID end-to-end: cloud browser CAPTCHA + API submission
"""
import asyncio, json, os, subprocess, sys, httpx, websockets

BU_KEY = os.environ.get('BROWSER_USE_API_KEY') or os.environ.get('BROWSER_USE_API_KEY_BACKUP')
VERIFICATION_ID = "<TEST_VID_REDACTED>"
PROGRAM_ID = "67c8c14f5f17a83b745e3f82"
ACCOUNT_ID = "67c88d2e0536336550bc3bd6"

STUDENT = {
    "first_name": "Mohamed",
    "last_name": "Benali",
    "email": "mohamed.benali@example.com",
    "country_code": "DZ",
    "country_name": "Algeria",
    "organization_name": "University of Science and Technology Houari Boumediene",
    "birth_month": 6, "birth_day": 15, "birth_year": 2004,
}

def curl_json(url, data=None, method="GET", key=BU_KEY):
    cmd = ["curl", "-s", "-m", "30", url, "-H", f"X-Browser-Use-API-Key: {key}", "-H", "Content-Type: application/json"]
    if data: cmd += ["-X", method, "-d", json.dumps(data)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else {}

async def get_captcha_token():
    """Create cloud browser, navigate, wait for Turnstile auto-solve, extract token"""
    browser = curl_json("https://api.browser-use.com/api/v4/browsers", {"timeout": 10}, "POST")
    bid, cdp_url = browser["id"], browser["cdpUrl"]
    ws_url = cdp_url.replace("https://", "wss://")
    print(f"Browser {bid} created. Live: {browser['liveUrl']}")

    token = None
    async with websockets.connect(ws_url) as ws:
        rid = 0
        async def send(method, params=None, sid=None):
            nonlocal rid
            rid += 1
            msg = {"id": rid, "method": method, "params": params or {}}
            if sid: msg["sessionId"] = sid
            await ws.send(json.dumps(msg))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == rid:
                    return r.get("result", {})

        targets = await send("Target.getTargets")
        tid = next(t["targetId"] for t in targets["targetInfos"] if t["type"] == "page")
        sid = (await send("Target.attachToTarget", {"targetId": tid, "flatten": True}))["sessionId"]

        url = f"https://services.sheerid.com/verify/{PROGRAM_ID}/?verificationId={VERIFICATION_ID}&redirectUrl=https%3A%2F%2Fone.google.com%2Fai-student&authuser=2"
        await send("Page.navigate", {"url": url}, sid)
        print("Navigated, waiting for Turnstile...")

        for i in range(45):
            await asyncio.sleep(2)
            r = await send("Runtime.evaluate", {
                "expression": """(() => {
                    const i = document.querySelector('input[name="cf-turnstile-response"]');
                    if (i && i.value) return {solved:true, token:i.value};
                    const err = document.querySelector('.sid-captcha-notification');
                    if (err && err.offsetParent) {
                        const rb = document.querySelector('.sid-captcha-notification__retry-button');
                        if (rb) rb.click();
                        return {error:true};
                    }
                    return {solved:false};
                })()""",
                "returnByValue": True
            }, sid)
            v = r.get("result", {}).get("value", {})
            if i % 5 == 0: print(f"  check {i+1}: {v}")
            if v.get("solved"):
                token = v["token"]
                print(f"CAPTCHA SOLVED, token len={len(token)}")
                break
            if v.get("error"):
                print("  captcha error, retrying...")

    curl_json(f"https://api.browser-use.com/api/v4/browsers/{bid}", {"action": "stop"}, "PATCH")
    return token

async def get_org():
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get("https://orgsearch.sheerid.net/rest/organization/search", params={
            "tags": "HEI,qualifying_ps,TITLE4",
            "country": STUDENT["country_code"],
            "type": "UNIVERSITY,POST_SECONDARY",
            "accountId": ACCOUNT_ID,
            "name": STUDENT["organization_name"],
        })
        orgs = r.json()
        return orgs[0] if orgs else None

async def get_fingerprint():
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post("https://worker.sheerid.com/GJUnkoPlgxsjeU7r/qI2joK0PpxwbeNSN",
                         json={"verificationId": VERIFICATION_ID})
        return r.json().get("token", "fp-" + str(os.urandom(8).hex()))

async def submit(captcha, org_id, fingerprint, birth_fmt=None):
    # CORRECTED: services.sheerid.com (not my.sheerid.com)
    url = f"https://services.sheerid.com/rest/v2/verification/{VERIFICATION_ID}/step/collectStudentPersonalInfo"
    if birth_fmt is None:
        birth_fmt = f"{STUDENT['birth_year']}-{STUDENT['birth_month']:02d}-{STUDENT['birth_day']:02d}"
    payload = {
        "firstName": STUDENT["first_name"],
        "lastName": STUDENT["last_name"],
        "email": STUDENT["email"],
        "country": STUDENT["country_code"],
        "organization": {"id": int(org_id)},  # numeric ID
        "birthDate": birth_fmt,
        "locale": "en-US",
        "captchaToken": captcha,
        "deviceFingerprintHash": fingerprint,
        "metadata": {"verificationId": VERIFICATION_ID, "redirectUrl": "https://one.google.com/ai-student"},
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url, json=payload)
        return r.json()

async def check_status():
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"https://my.sheerid.com/rest/v2/verification/{VERIFICATION_ID}")
        return r.json()

async def main():
    print("=== 1. CAPTCHA ===")
    token = await get_captcha_token()
    if not token:
        print("FAILED to get CAPTCHA token"); return

    print("=== 2. Org + fingerprint ===")
    org = await get_org()
    fp = await get_fingerprint()
    print(f"Org: {org['name']} ({org['idExtended']})")
    print(f"Fingerprint: {fp[:30]}...")

    print("=== 3. Submit ===")
    fmt = sys.argv[1] if len(sys.argv) > 1 else None
    result = await submit(token, org["idExtended"], fp, fmt)
    print(json.dumps(result, indent=2))

    print("=== 4. Status ===")
    await asyncio.sleep(3)
    status = await check_status()
    print(f"currentStep: {status.get('currentStep')}, errors: {status.get('errorIds')}")
    if status.get("currentStep") != "collectStudentPersonalInfo":
        print(f"ADVANCED -> {status.get('currentStep')}")
        if status.get("redirectUrl"): print(f"redirect: {status['redirectUrl']}")

asyncio.run(main())
