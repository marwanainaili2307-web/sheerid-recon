#!/usr/bin/env python3
"""Continue at docUpload stage: inject 1+ files (PDF/PNG/JPG), click submit, poll."""
import asyncio, json, mimetypes, os, sys, subprocess, base64, httpx, websockets

BU_KEY = os.environ.get("BROWSER_USE_API_KEY") or os.environ.get("BROWSER_USE_API_KEY_BACKUP")
VID = sys.argv[1]
DOCS = sys.argv[2:] or ["student_card.jpg"]
PROGRAM = "67c8c14f5f17a83b745e3f82"

def status(vid):
    with httpx.Client(timeout=30) as c:
        return c.get(f"https://my.sheerid.com/rest/v2/verification/{vid}").json()

def curl_json(url, data=None, method="GET"):
    cmd = ["curl", "-s", "-m", "30", url, "-H", f"X-Browser-Use-API-Key: {BU_KEY}", "-H", "Content-Type: application/json"]
    if data: cmd += ["-X", method, "-d", json.dumps(data)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else {}

async def main():
    # Validate files locally BEFORE spawning a paid browser
    payload_files = []
    for path in DOCS:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        payload_files.append({"name": os.path.basename(path), "mime": mime, "b64": b64})

    s = status(VID)
    print(f"[status] {s.get('currentStep')} errors={s.get('errorIds')}")
    assert s.get("currentStep") == "docUpload", f"not at docUpload: {s.get('currentStep')}"

    browser = curl_json("https://api.browser-use.com/api/v4/browsers", {"timeout": 10}, "POST")
    bid = browser["id"]
    ws_url = browser["cdpUrl"].replace("https://", "wss://")
    print(f"[browser] {bid}\n[live] {browser.get('liveUrl','')}")

    final = s
    try:
        async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
            rid = 0
            async def send(method, params=None, sid=None):
                nonlocal rid; rid += 1
                msg = {"id": rid, "method": method, "params": params or {}}
                if sid: msg["sessionId"] = sid
                await ws.send(json.dumps(msg))
                while True:
                    r = json.loads(await ws.recv())
                    if r.get("id") == rid: return r.get("result", {})

            async def ev(sid, expr, timeout_ms=15000):
                r = await send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True, "timeout": timeout_ms}, sid)
                return r.get("result", {}).get("value")

            targets = await send("Target.getTargets")
            tid = next(t["targetId"] for t in targets["targetInfos"] if t["type"] == "page")
            sid = (await send("Target.attachToTarget", {"targetId": tid, "flatten": True}))["sessionId"]

            url = (f"https://services.sheerid.com/verify/{PROGRAM}/?verificationId={VID}"
                   "&redirectUrl=https%3A%2F%2Fone.google.com%2Fu%2F3%2Fai-student&authuser=3")
            await send("Page.navigate", {"url": url}, sid)

            for _ in range(30):
                await asyncio.sleep(2)
                if await ev(sid, "!!document.querySelector('input[type=\"file\"]')"):
                    break
            else:
                print("[abort] no file input appeared")
                return

            upload_js = f"""
            (() => {{
                const files = {json.dumps(payload_files)};
                const dt = new DataTransfer();
                for (const item of files) {{
                    const bin = atob(item.b64);
                    const arr = new Uint8Array(bin.length);
                    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
                    dt.items.add(new File([arr], item.name, {{type: item.mime}}));
                }}
                const inp = document.querySelector('input[type="file"]');
                inp.files = dt.files;
                inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                return {{injected: inp.files.length, multipleAllowed: inp.multiple}};
            }})()
            """
            res = await ev(sid, upload_js, timeout_ms=30000)
            print(f"[docUpload] {res}")
            await asyncio.sleep(2)

            click_res = await ev(sid, """(() => {
                const ids = ['sid-submit-doc-upload','sid-submit-doc-upload_pb'];
                for (const id of ids) {
                    const b = document.getElementById(id);
                    if (b && !b.disabled) { b.click(); return 'clicked:' + id; }
                }
                const b2 = document.querySelector('[data-testid="sid-submit"]') || document.querySelector('.sid-submit');
                if (b2) { b2.click(); return 'clicked-fallback'; }
                return 'no-button';
            })()""")
            print(f"[docUpload] submit: {click_res}")

            for i in range(45):
                await asyncio.sleep(4)
                final = status(VID)
                if final.get("currentStep") != "docUpload":
                    break
                if i % 5 == 0:
                    print(f"[poll {i}] still docUpload, docs={len(final.get('documents') or [])}")
    finally:
        curl_json(f"https://api.browser-use.com/api/v4/browsers/{bid}", {"action": "stop"}, "PATCH")
        print(f"[browser] {bid} stopped")

    print("\n=== FINAL ===")
    print(json.dumps(final, indent=2))

asyncio.run(main())