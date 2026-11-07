#!/usr/bin/env python3
"""
Full chain runner: real student verification end-to-end
Form -> Turnstile -> fingerprint -> docUpload -> poll -> redirect URL

Usage:
  python3 verify_runner.py --vid <VERIFICATION_ID> --first "<STUDENT_FIRST_REDACTED>" \
      --last "<STUDENT_LAST_REDACTED>" --email you@gmail.com --birth <BIRTHDATE_REDACTED> \
      --doc student_card.jpg
"""
import asyncio, json, os, sys, subprocess, base64, argparse, httpx, websockets

BU_KEY = os.environ.get('BROWSER_USE_API_KEY') or os.environ.get('BROWSER_USE_API_KEY_BACKUP')
ACCOUNT_ID = "67c88d2e0536336550bc3bd6"

PROGRAM_ID_PLACEHOLDER = "67c8c14f5f17a83b745e3f82"

def curl_json(url, data=None, method="GET", key=BU_KEY):
    cmd = ["curl", "-s", "-m", "30", url, "-H", f"X-Browser-Use-API-Key: {key}", "-H", "Content-Type: application/json"]
    if data: cmd += ["-X", method, "-d", json.dumps(data)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else {}

def status(vid):
    with httpx.Client(timeout=30) as c:
        return c.get(f"https://my.sheerid.com/rest/v2/verification/{vid}").json()

def get_program(vid):
    s = status(vid)
    return s.get("programId", PROGRAM_ID_PLACEHOLDER)

async def run_browser(vid, program_id, first, last, email, birth, doc_path):
    browser = curl_json("https://api.browser-use.com/api/v4/browsers", {"timeout": 10}, "POST")
    bid, ws_url = browser["id"], browser["cdpUrl"].replace("https://", "wss://")
    print(f"[browser] {bid}\n[live] {browser.get('liveUrl','')}  <- watch progress here")

    b64 = base64.b64encode(open(doc_path, "rb").read()).decode()
    mime = "image/jpeg" if doc_path.lower().endswith((".jpg", ".jpeg")) else "image/png"

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

        # Decode birth string into month/day/year parts
        y, m, d = (int(x) for x in birth.split("-"))

        url = f"https://services.sheerid.com/verify/{program_id}/?verificationId={vid}&redirectUrl=https%3A%2F%2Fone.google.com%2Fai-student"
        await send("Page.navigate", {"url": url}, sid)
        await asyncio.sleep(6)
        for _ in range(20):
            if await ev(sid, "!!document.getElementById('sid-first-name')"): break
            await asyncio.sleep(1)

        async def fill(sel, text):
            await ev(sid, f"document.querySelector('{sel}').scrollIntoView({{block:'center'}})")
            pos = await ev(sid, f"(()=>{{const r=document.querySelector('{sel}').getBoundingClientRect();return {{x:r.left+r.width/2,y:r.top+r.height/2}}}})()")
            await send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1}, sid)
            await send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1}, sid)
            await asyncio.sleep(0.3)
            await send("Input.insertText", {"text": text}, sid)
            await asyncio.sleep(0.3)
            filled = await ev(sid, "document.querySelector('" + sel + "').value")
            print(f"[form] {sel} = {filled!r}")

        await fill("#sid-first-name", first)
        await fill("#sid-last-name", last)
        await fill("#sid-email", email)

        # country = Algeria
        await ev(sid, "document.querySelector('.sid-field.sid-country .sid-input-select-button').click()")
        await asyncio.sleep(1)
        await ev(sid, "(()=>{for(const x of document.querySelectorAll('[role=\"option\"]')) if(x.textContent.includes('Algeria')) {x.click(); return 1}})()")
        await asyncio.sleep(1)

        # birth month/day/year
        await ev(sid, "document.querySelector('.sid-birthdate__month .sid-input-select-button').click()")
        await asyncio.sleep(0.7)
        await ev(sid, f"(()=>{{const o=document.querySelectorAll('[role=\"option\"]'); if(o[{m-1}]) o[{m-1}].click()}})()")
        await asyncio.sleep(0.5)
        await fill("#sid-birthdate-day", str(d))
        await fill("#sid-birthdate-year", str(y))

        # school
        await fill("#sid-college-name", "University of Science and Technology Houari Boumediene")
        await asyncio.sleep(4)
        s = await ev(sid, "(()=>{const m=document.getElementById('sid-college-name-menu'); if(m){const o=m.querySelectorAll('[role=\"option\"]'); if(o.length){o[0].click(); return o[0].textContent}}} )()")
        print(f"[form] school: {s}")

        # captcha
        print("[captcha] waiting for Turnstile...")
        for i in range(45):
            await asyncio.sleep(2)
            v = await ev(sid, "(()=>{const i=document.querySelector('input[name=\"cf-turnstile-response\"]'); return i&&i.value?1:0})()")
            if v: print("[captcha] solved"); break

        await ev(sid, "document.getElementById('sid-submit-wrapper__collect-info')?.click()")
        print("[submit] clicked, waiting for docUpload step...")
        for i in range(30):
            await asyncio.sleep(2)
            if await ev(sid, "!!document.querySelector('input[type=\"file\"]')"): break
        s = status(vid)
        print(f"[status] {s.get('currentStep')}, errors={s.get('errorIds')}")
        if s.get("currentStep") != "docUpload":
            print("[abort] unexpected step:", json.dumps(s))
            curl_json(f"https://api.browser-use.com/api/v4/browsers/{bid}", {"action": "stop"}, "PATCH")
            return

        # doc upload via CDP file injection
        doc_ = await send("DOM.getDocument", {}, sid)
        doc_node = doc_["root"]["nodeId"]
        fnode = await send("DOM.querySelector", {"nodeId": doc_node, "selector": "input[type='file']"}, sid)
        await send("DOM.setFileInputFiles", {"files": ["/tmp/student_card.jpg"], "nodeId": fnode["nodeId"]}, sid)
        print("[docUpload] file attached")

        # browser-use sandboxes don't share our FS; push file via data URL fallback
        # (DOM.setFileInputFiles needs the file in the browser's FS -> use drop path)
        await asyncio.sleep(2)
        # If attach failed, fallback: DataTransfer injection
        has_file = await ev(sid, "(()=>{const i=document.querySelector('input[type=\"file\"]'); return i.files && i.files.length>0 ? 1 : 0})()")
        if not has_file:
            print("[docUpload] CDP path failed, using DataTransfer injection...")
            upload_js = """
            (async () => {
                const b64 = "%s";
                const bin = atob(b64);
                const arr = new Uint8Array(bin.length);
                for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
                const f = new File([arr], "student_card.jpg", {type: "%s"});
                const dt = new DataTransfer();
                dt.items.add(f);
                const inp = document.querySelector('input[type="file"]');
                inp.files = dt.files;
                inp.dispatchEvent(new Event('change', {bubbles: true}));
                inp.dispatchEvent(new Event('input', {bubbles: true}));
                return inp.files.length;
            })()
            """ % (b64, mime)
            n = await ev(sid, upload_js)
            print(f"[docUpload] injected {n} file(s)")

        # SheerID requires clicking the doc-upload submit button after attaching
        await asyncio.sleep(1)
        click_res = await ev(sid, """(() => {
            const ids = ['sid-submit-doc-upload','sid-submit-doc-upload_pb','sid-submit'];
            for (const id of ids) {
                const b = document.getElementById(id);
                if (b && !b.disabled) { b.click(); return 'clicked:' + id; }
            }
            const b2 = document.querySelector('[data-testid="sid-submit"]') || document.querySelector('.sid-submit');
            if (b2) { b2.click(); return 'clicked-fallback'; }
            return 'no-button';
        })()""")
        print(f"[docUpload] submit: {click_res}")

        # wait for confirmation/next step
        print("[docUpload] waiting for submission...")
        for i in range(30):
            await asyncio.sleep(4)
            s = status(vid)
            if s.get("currentStep") != "docUpload": break
            if i % 5 == 0: print(f"[poll {i}] still docUpload")

        curl_json(f"https://api.browser-use.com/api/v4/browsers/{bid}", {"action": "stop"}, "PATCH")

    print("\n=== FINAL ===")
    print(json.dumps(s, indent=2))
    if s.get("currentStep") == "ssoRedirect" or s.get("redirectUrl"):
        print(f"\n>>> REDIRECT (open on HER profile): {s.get('redirectUrl')}")
    elif s.get("currentStep") in ("review", "pendingDocUploadReview"):
        print(f"\n>>> Under review (max {s.get('maxReviewTime')}). Poll later, expect redirect within ~20 min.")

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vid", required=True)
    ap.add_argument("--first", required=True)
    ap.add_argument("--last", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--birth", required=True, help="YYYY-MM-DD")
    ap.add_argument("--doc", required=True)
    a = ap.parse_args()

    program = get_program(a.vid)
    print(f"[program] {program}")
    await run_browser(a.vid, program, a.first, a.last, a.email, a.birth, a.doc)

asyncio.run(main())
