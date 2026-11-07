#!/usr/bin/env python3
"""
SheerID Verification - Hybrid Approach
1. Use cloud browser to solve CAPTCHA and get fingerprint
2. Use API directly for form submission
"""

import asyncio
import json
import os
from typing import Dict, Optional
import httpx
import websockets
import uuid


class CDPClient:
    def __init__(self, cdp_ws_url: str):
        self.cdp_ws_url = cdp_ws_url
        self.ws = None
        self.request_id = 0
        self.pending = {}
        self.listener_task = None
        
    async def connect(self):
        self.ws = await websockets.connect(self.cdp_ws_url)
        self.listener_task = asyncio.create_task(self._listen_loop())
        await self.send("Target.setDiscoverTargets", {"discover": True})
        await self.send("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True})
        print("CDP connected")
    
    async def _listen_loop(self):
        try:
            async for message in self.ws:
                data = json.loads(message)
                if "id" in data and data["id"] in self.pending:
                    future = self.pending.pop(data["id"])
                    if not future.done():
                        if "error" in data:
                            future.set_exception(Exception(data["error"]))
                        else:
                            future.set_result(data.get("result", {}))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Listener error: {e}")
    
    async def send(self, method: str, params: Dict = None) -> Dict:
        self.request_id += 1
        msg = {"id": self.request_id, "method": method, "params": params or {}}
        future = asyncio.get_event_loop().create_future()
        self.pending[self.request_id] = future
        await self.ws.send(json.dumps(msg))
        return await asyncio.wait_for(future, timeout=30)
    
    async def send_session(self, session_id: str, method: str, params: Dict = None):
        self.request_id += 1
        msg = {"id": self.request_id, "method": method, "params": params or {}, "sessionId": session_id}
        future = asyncio.get_event_loop().create_future()
        self.pending[self.request_id] = future
        await self.ws.send(json.dumps(msg))
        return await asyncio.wait_for(future, timeout=30)
    
    async def get_targets(self) -> list:
        result = await self.send("Target.getTargets")
        return result.get("targetInfos", [])
    
    async def attach_to_target(self, target_id: str) -> str:
        result = await self.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        return result.get("sessionId")
    
    async def navigate(self, session_id: str, url: str):
        await self.send_session(session_id, "Page.enable")
        await self.send_session(session_id, "Page.navigate", {"url": url})
        await asyncio.sleep(3)
    
    async def evaluate(self, session_id: str, expression: str):
        result = await self.send_session(session_id, "Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True
        })
        return result.get("result", {}).get("value")
    
    async def close(self):
        if self.listener_task:
            self.listener_task.cancel()
            try:
                await self.listener_task
            except asyncio.CancelledError:
                pass
        if self.ws:
            await self.ws.close()


async def create_cloud_browser(api_key: str) -> Dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.browser-use.com/api/v4/browsers",
            headers={"X-Browser-Use-API-Key": api_key, "Content-Type": "application/json"},
            json={"timeout": 15},
            timeout=30
        )
        return resp.json()


async def stop_cloud_browser(api_key: str, browser_id: str):
    async with httpx.AsyncClient() as client:
        await client.patch(
            f"https://api.browser-use.com/api/v4/browsers/{browser_id}",
            headers={"X-Browser-Use-API-Key": api_key, "Content-Type": "application/json"},
            json={"action": "stop"}
        )


async def get_captcha_and_fingerprint(verification_id: str) -> Dict:
    """Use cloud browser to get CAPTCHA token and fingerprint"""
    API_KEY = os.environ.get('BROWSER_USE_API_KEY')
    if not API_KEY:
        raise Exception("BROWSER_USE_API_KEY not set")
    
    print("Creating cloud browser for CAPTCHA...")
    browser = await create_cloud_browser(API_KEY)
    browser_id = browser["id"]
    cdp_url = browser["cdpUrl"]
    
    ws_url = cdp_url.replace("https://", "wss://")
    cdp = CDPClient(ws_url)
    
    try:
        await cdp.connect()
        
        targets = await cdp.get_targets()
        page_target = next((t for t in targets if t["type"] == "page"), targets[0])
        session_id = await cdp.attach_to_target(page_target["targetId"])
        
        url = f"https://services.sheerid.com/verify/67c8c14f5f17a83b745e3f82/?verificationId={verification_id}&redirectUrl=https%3A%2F%2Fone.google.com%2Fai-student&authuser=2"
        await cdp.navigate(session_id, url)
        await asyncio.sleep(3)
        
        # Wait for CAPTCHA to be solved
        print("Waiting for CAPTCHA...")
        captcha_token = None
        for attempt in range(30):
            await asyncio.sleep(2)
            captcha_status = await cdp.evaluate(session_id, """
                (() => {
                    const hiddenInput = document.querySelector('input[name="cf-turnstile-response"]');
                    return hiddenInput ? hiddenInput.value : '';
                })()
            """)
            if captcha_status:
                print(f"CAPTCHA solved! Token length: {len(captcha_status)}")
                captcha_token = captcha_status
                break
            print(f"  Attempt {attempt+1}: waiting...")
        
        # Get fingerprint
        fingerprint = await cdp.evaluate(session_id, """
            (() => {
                // Try to get SheerID fingerprint
                if (window.sheerIdFingerprint) return window.sheerIdFingerprint;
                // Or try to call the fingerprint API
                return 'browser-fingerprint-' + Math.random().toString(36).substr(2, 16);
            })()
        """)
        
        # Get cookies
        cookies = await cdp.evaluate(session_id, """
            (() => document.cookie)()
        """)
        
        return {
            "captcha_token": captcha_token,
            "fingerprint": fingerprint,
            "cookies": cookies
        }
        
    finally:
        await cdp.close()
        await stop_cloud_browser(API_KEY, browser_id)


async def get_org_id(country: str, query: str) -> Optional[Dict]:
    """Get organization ID via SheerID API"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://orgsearch.sheerid.net/rest/organization/search",
            params={
                "tags": "HEI,qualifying_ps,TITLE4",
                "country": country,
                "type": "UNIVERSITY,POST_SECONDARY",
                "accountId": "67c88d2e0536336550bc3bd6",
                "name": query
            }
        )
        orgs = resp.json()
        if orgs:
            return orgs[0]
    return None


async def submit_verification(student_data: Dict, verification_id: str, captcha_data: Dict) -> Dict:
    """Submit verification via API"""
    
    # Get org
    org = await get_org_id(student_data["country_code"], student_data["organization_name"])
    if not org:
        return {"success": False, "error": "Organization not found"}
    
    # Get fingerprint from SheerID worker
    async with httpx.AsyncClient() as client:
        fp_resp = await client.post(
            "https://worker.sheerid.com/GJUnkoPlgxsjeU7r/qI2joK0PpxwbeNSN",
            json={"verificationId": verification_id},
            timeout=30
        )
        fingerprint = fp_resp.json().get("token", captcha_data["fingerprint"])
    
    # Submit form - CORRECT FORMAT:
    # - birthDate: string "MM/DD/YYYY"
    # - organization: object with "id" or "idExtended"
    submission_url = f"https://my.sheerid.com/rest/v2/verification/{verification_id}/step/collectStudentPersonalInfo"
    
    birth_date_str = f"{student_data['birth_month']:02d}/{student_data['birth_day']:02d}/{student_data['birth_year']}"
    
    payload = {
        "firstName": student_data["first_name"],
        "lastName": student_data["last_name"],
        "email": student_data["email"],
        "country": student_data["country_code"],
        "organization": {"id": org["idExtended"]},
        "birthDate": birth_date_str,
        "locale": "en-US",
        "captchaToken": captcha_data["captcha_token"],
        "deviceFingerprintHash": fingerprint,
        "metadata": {
            "verificationId": verification_id,
            "redirectUrl": "https://one.google.com/ai-student"
        }
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(submission_url, json=payload, timeout=30)
        return resp.json()


async def check_verification_status(verification_id: str) -> Dict:
    """Check verification status"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"https://my.sheerid.com/rest/v2/verification/{verification_id}")
        return resp.json()


async def main():
    verification_id = "<TEST_VID_REDACTED>"
    
    student_data = {
        "first_name": "Mohamed",
        "last_name": "Benali",
        "email": "mohamed.benali@example.com",
        "country_code": "DZ",
        "country_name": "Algeria",
        "organization_name": "University of Science and Technology Houari Boumediene",
        "birth_month": 6,
        "birth_day": 15,
        "birth_year": 2000
    }
    
    # Step 1: Get CAPTCHA token via cloud browser
    print("=== Step 1: Getting CAPTCHA token ===")
    captcha_data = await get_captcha_and_fingerprint(verification_id)
    
    if not captcha_data["captcha_token"]:
        print("Failed to get CAPTCHA token")
        return
    
    print(f"CAPTCHA token: {captcha_data['captcha_token'][:50]}...")
    print(f"Fingerprint: {captcha_data['fingerprint']}")
    
    # Step 2: Submit via API
    print("\n=== Step 2: Submitting via API ===")
    result = await submit_verification(student_data, verification_id, captcha_data)
    print(json.dumps(result, indent=2))
    
    # Step 3: Check status
    print("\n=== Step 3: Checking status ===")
    await asyncio.sleep(3)
    status = await check_verification_status(verification_id)
    print(json.dumps(status, indent=2))
    
    if status.get("currentStep") != "collectStudentPersonalInfo":
        print(f"\n✓ Advanced to step: {status.get('currentStep')}")
        if status.get("currentStep") == "success":
            print(f"Redirect URL: {status.get('redirectUrl')}")


if __name__ == "__main__":
    asyncio.run(main())