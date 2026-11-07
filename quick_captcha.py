#!/usr/bin/env python3
import asyncio
import json
import os
import httpx
import websockets

API_KEY = os.environ.get('BROWSER_USE_API_KEY')

async def create_browser():
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.browser-use.com/api/v4/browsers",
            headers={"X-Browser-Use-API-Key": API_KEY, "Content-Type": "application/json"},
            json={"timeout": 5}
        )
        return resp.json()

async def main():
    browser = await create_browser()
    cdp_url = browser["cdpUrl"].replace("https://", "wss://")
    print(f"Live: {browser['liveUrl']}")
    
    async with websockets.connect(cdp_url) as ws:
        async def send(method, params=None):
            id = 1
            msg = {"id": id, "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
            while True:
                resp = json.loads(await ws.recv())
                if resp.get("id") == id:
                    return resp.get("result", {})
        
        # Get target
        targets = await send("Target.getTargets")
        target_id = targets["targetInfos"][0]["targetId"]
        attached = await send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        session_id = attached["sessionId"]
        
        # Navigate
        await send("Page.navigate", {"url": "https://services.sheerid.com/verify/67c8c14f5f17a83b745e3f82/?verificationId=<TEST_VID_REDACTED>&redirectUrl=https%3A%2F%2Fone.google.com%2Fai-student&authuser=2"}, session_id=session_id)
        
        # Wait for CAPTCHA
        for i in range(60):
            await asyncio.sleep(2)
            result = await send("Runtime.evaluate", {
                "expression": """
                    (() => {
                        const input = document.querySelector('input[name="cf-turnstile-response"]');
                        if (input && input.value) return {solved: true, token: input.value};
                        return {solved: false};
                    })()
                """,
                "returnByValue": True
            }, session_id=session_id)
            
            value = result.get("result", {}).get("value", {})
            print(f"Check {i+1}: {value}")
            if value.get("solved"):
                print(f"\nTOKEN: {value['token']}")
                break
        
        # Stop browser
        async with httpx.AsyncClient() as client:
            await client.patch(
                f"https://api.browser-use.com/api/v4/browsers/{browser['id']}",
                headers={"X-Browser-Use-API-Key": API_KEY, "Content-Type": "application/json"},
                json={"action": "stop"}
            )

asyncio.run(main())
