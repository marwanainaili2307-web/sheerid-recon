#!/usr/bin/env python3
"""Capture the real form submission request from the SheerID page"""
import asyncio, json, os, subprocess, websockets

BU_KEY = os.environ.get('BROWSER_USE_API_KEY') or os.environ.get('BROWSER_USE_API_KEY_BACKUP')
VERIFICATION_ID = "<TEST_VID_REDACTED>"
PROGRAM_ID = "67c8c14f5f17a83b745e3f82"

def curl_json(url, data=None, method="GET"):
    cmd = ["curl", "-s", "-m", "30", url, "-H", f"X-Browser-Use-API-Key: {BU_KEY}", "-H", "Content-Type: application/json"]
    if data: cmd += ["-X", method, "-d", json.dumps(data)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else {}

CAPTURE_HOOK = r"""
window.__captured = [];
const origFetch = window.fetch;
window.fetch = async function(...args) {
  const url = typeof args[0] === 'string' ? args[0] : args[0].url;
  if (url.includes('sheerid.com') && !url.includes('analytics') && !url.includes('nr-data')) {
    let body = args[1] && args[1].body;
    window.__captured.push({url, method: args[1]?.method || 'GET', body: typeof body === 'string' ? body : null});
  }
  return origFetch.apply(this, args);
};
const origOpen = XMLHttpRequest.prototype.open;
const origSend = XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.open = function(m, u) { this.__url = u; this.__method = m; return origOpen.apply(this, arguments); };
XMLHttpRequest.prototype.send = function(b) {
  if (this.__url && this.__url.includes('sheerid.com') && !this.__url.includes('analytics')) {
    window.__captured.push({url: this.__url, method: this.__method, body: typeof b === 'string' ? b : null});
  }
  return origSend.apply(this, arguments);
};
"""

async def main():
    browser = curl_json("https://api.browser-use.com/api/v4/browsers", {"timeout": 10}, "POST")
    bid = browser["id"]
    ws_url = browser["cdpUrl"].replace("https://", "wss://")
    print(f"Browser {bid} | Live: {browser['liveUrl']}")

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
                if r.get("id") == rid: return r.get("result", {})

        async def ev(sid, expr):
            r = await send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True}, sid)
            return r.get("result", {}).get("value")

        targets = await send("Target.getTargets")
        tid = next(t["targetId"] for t in targets["targetInfos"] if t["type"] == "page")
        sid = (await send("Target.attachToTarget", {"targetId": tid, "flatten": True}))["sessionId"]

        await send("Page.enable", sid=sid)
        await send("Page.addScriptToEvaluateOnNewDocument", {"source": CAPTURE_HOOK}, sid=sid)

        url = f"https://services.sheerid.com/verify/{PROGRAM_ID}/?verificationId={VERIFICATION_ID}&redirectUrl=https%3A%2F%2Fone.google.com%2Fai-student&authuser=2"
        await send("Page.navigate", {"url": url}, sid)
        await asyncio.sleep(6)

        # Wait for form
        for _ in range(20):
            ok = await ev(sid, "!!document.getElementById('sid-first-name')")
            if ok: break
            await asyncio.sleep(1)
        print("Form loaded")

        async def fill(selector, text):
            # scroll into view, get center, click, then type via insertText
            await ev(sid, f"document.querySelector('{selector}').scrollIntoView({{block:'center'}})")
            await asyncio.sleep(0.3)
            pos = await ev(sid, f"(() => {{ const r = document.querySelector('{selector}').getBoundingClientRect(); return {{x: r.left + r.width/2, y: r.top + r.height/2}}; }})()")
            await send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": pos["x"], "y": pos["y"]}, sid)
            await send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1}, sid)
            await send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1}, sid)
            await asyncio.sleep(0.3)
            await send("Input.insertText", {"text": text}, sid)
            await asyncio.sleep(0.3)
            v = await ev(sid, f"document.querySelector('{selector}').value")
            print(f"  filled {selector} = {v!r}")

        await fill("#sid-first-name", "Mohamed")
        await fill("#sid-last-name", "Benali")
        await fill("#sid-email", "mohamed.benali@gmail.com")

        # Country dropdown
        await ev(sid, "document.querySelector('.sid-field.sid-country .sid-input-select-button').click()")
        await asyncio.sleep(1)
        await ev(sid, "(() => { const o = document.querySelectorAll('[role=\"option\"]'); for (const x of o) if (x.textContent.includes('Algeria')) { x.click(); return 'ok'; } return 'nf'; })()")
        await asyncio.sleep(1)

        # Month
        await ev(sid, "document.querySelector('.sid-birthdate__month .sid-input-select-button').click()")
        await asyncio.sleep(0.7)
        await ev(sid, "(() => { const o = document.querySelectorAll('[role=\"option\"]'); if (o[5]) { o[5].click(); return o[5].textContent; } return 'nf'; })()")
        await asyncio.sleep(0.5)

        await fill("#sid-birthdate-day", "15")
        await fill("#sid-birthdate-year", "2004")

        # School
        await fill("#sid-college-name", "University of Science and Technology Houari Boumediene")
        await asyncio.sleep(4)
        sel = await ev(sid, "(() => { const m = document.getElementById('sid-college-name-menu'); if (m) { const o = m.querySelectorAll('[role=\"option\"]'); if (o.length) { o[0].click(); return o[0].textContent; } } return 'no options'; })()")
        print(f"  school: {sel}")

        # Wait for captcha
        print("Waiting for Turnstile...")
        for i in range(45):
            await asyncio.sleep(2)
            v = await ev(sid, "(() => { const i = document.querySelector('input[name=\"cf-turnstile-response\"]'); return i && i.value ? {solved:true} : {solved:false}; })()")
            if v.get("solved"): print("CAPTCHA solved"); break

        # Submit
        await ev(sid, "document.getElementById('sid-submit-wrapper__collect-info').click()")
        print("Submitted, waiting...")
        await asyncio.sleep(10)

        captured = await ev(sid, "window.__captured")
        print("\n=== CAPTURED REQUESTS ===")
        for c in (captured or []):
            print(f"\n{c['method']} {c['url']}")
            if c.get('body'): print(c['body'][:3000])

        final_url = await ev(sid, "window.location.href")
        step = await ev(sid, "(() => { const e = document.querySelector('[data-testid^=\"sid-step-\"]'); return e ? e.getAttribute('data-testid') : 'unknown'; })()")
        print(f"\nFinal step: {step}")
        print(f"Final URL: {final_url}")

    curl_json(f"https://api.browser-use.com/api/v4/browsers/{bid}", {"action": "stop"}, "PATCH")

asyncio.run(main())
