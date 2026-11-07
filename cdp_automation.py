#!/usr/bin/env python3
"""
Connect to browser-use cloud browser via CDP WebSocket and automate SheerID
Uses raw WebSocket + Chrome DevTools Protocol
"""

import asyncio
import json
import os
import websockets
import httpx
from typing import Dict, Any, Optional
import uuid


class CDPClient:
    def __init__(self, cdp_ws_url: str):
        self.cdp_ws_url = cdp_ws_url
        self.ws = None
        self.request_id = 0
        self.pending = {}
        self.listener_task = None
        
    async def connect(self):
        """Connect to CDP WebSocket"""
        self.ws = await websockets.connect(self.cdp_ws_url)
        # Start listener first
        self.listener_task = asyncio.create_task(self._listen_loop())
        # Enable required domains
        await self.send("Target.setDiscoverTargets", {"discover": True})
        await self.send("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True})
        print("CDP connected")
    
    async def _listen_loop(self):
        """Listen for responses"""
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
        """Send CDP command"""
        self.request_id += 1
        msg = {"id": self.request_id, "method": method, "params": params or {}}
        future = asyncio.get_event_loop().create_future()
        self.pending[self.request_id] = future
        
        await self.ws.send(json.dumps(msg))
        return await asyncio.wait_for(future, timeout=30)
    
    async def listen(self):
        """Listen for responses"""
        async for message in self.ws:
            data = json.loads(message)
            if "id" in data and data["id"] in self.pending:
                future = self.pending.pop(data["id"])
                if not future.done():
                    if "error" in data:
                        future.set_exception(Exception(data["error"]))
                    else:
                        future.set_result(data.get("result", {}))
            elif "method" in data:
                # Handle events
                pass
    
    async def get_targets(self) -> list:
        result = await self.send("Target.getTargets")
        return result.get("targetInfos", [])
    
    async def attach_to_target(self, target_id: str) -> str:
        result = await self.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        return result.get("sessionId")
    
    async def send_session(self, session_id: str, method: str, params: Dict = None):
        """Send command to specific session"""
        self.request_id += 1
        msg = {"id": self.request_id, "method": method, "params": params or {}, "sessionId": session_id}
        future = asyncio.get_event_loop().create_future()
        self.pending[self.request_id] = future
        await self.ws.send(json.dumps(msg))
        return await asyncio.wait_for(future, timeout=30)
    
    async def navigate(self, session_id: str, url: str):
        await self.send_session(session_id, "Page.enable")
        await self.send_session(session_id, "Page.navigate", {"url": url})
        # Wait for load
        await asyncio.sleep(3)
    
    async def evaluate(self, session_id: str, expression: str):
        result = await self.send_session(session_id, "Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True
        })
        return result.get("result", {}).get("value")
    
    async def click(self, session_id: str, selector: str):
        # Get element position and click via Input
        js = f"""
        (() => {{
            const el = document.querySelector('{selector}');
            if (!el) return {{error: 'not found'}};
            const rect = el.getBoundingClientRect();
            return {{x: rect.left + rect.width/2, y: rect.top + rect.height/2}};
        }})()
        """
        pos = await self.evaluate(session_id, js)
        if "error" in pos:
            raise Exception(f"Element not found: {selector}")
        
        await self.send_session(session_id, "Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": pos["x"], "y": pos["y"]
        })
        await self.send_session(session_id, "Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1
        })
        await self.send_session(session_id, "Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1
        })
        await asyncio.sleep(0.5)
    
    async def type_text(self, session_id: str, selector: str, text: str):
        await self.click(session_id, selector)
        await self.send_session(session_id, "Input.insertText", {"text": text})
        await asyncio.sleep(0.3)
    
    async def get_page_content(self, session_id: str):
        return await self.evaluate(session_id, "document.documentElement.outerHTML")
    
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
    """Create browser-use cloud browser"""
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


async def main():
    API_KEY = os.environ.get('BROWSER_USE_API_KEY')
    if not API_KEY:
        print("BROWSER_USE_API_KEY not set")
        return
    
    print("Creating cloud browser...")
    browser = await create_cloud_browser(API_KEY)
    browser_id = browser["id"]
    cdp_url = browser["cdpUrl"]
    live_url = browser["liveUrl"]
    
    print(f"Browser ID: {browser_id}")
    print(f"CDP URL: {cdp_url}")
    print(f"Live URL: {live_url}")
    print(f"Watch live: {live_url}")
    
    # Convert HTTP CDP URL to WebSocket
    # cdp_url is like: https://xxx.cdp.browser-use.com
    # WS URL is: wss://xxx.cdp.browser-use.com
    ws_url = cdp_url.replace("https://", "wss://")
    
    cdp = CDPClient(ws_url)
    
    try:
        # Start listener
        listener_task = asyncio.create_task(cdp.listen())
        
        # Connect
        await cdp.connect()
        
        # Get targets
        targets = await cdp.get_targets()
        print(f"Found {len(targets)} targets")
        for t in targets:
            print(f"  {t['type']}: {t['url']} (id: {t['targetId']})")
        
        # Find page target
        page_target = next((t for t in targets if t["type"] == "page" and not t["url"].startswith("chrome://")), None)
        if not page_target:
            # Create new page
            result = await cdp.send("Target.createTarget", {"url": "about:blank"})
            page_target_id = result.get("targetId")
        else:
            page_target_id = page_target["targetId"]
        
        # Attach to page
        session_id = await cdp.attach_to_target(page_target_id)
        print(f"Attached to target: {page_target_id}, session: {session_id}")
        
        # Navigate to SheerID
        verification_id = "<TEST_VID_REDACTED>"
        url = f"https://services.sheerid.com/verify/67c8c14f5f17a83b745e3f82/?verificationId={verification_id}&redirectUrl=https%3A%2F%2Fone.google.com%2Fai-student&authuser=2"
        
        print(f"Navigating to {url}")
        await cdp.navigate(session_id, url)
        
        # Wait for page load
        await asyncio.sleep(5)
        
        # Fill form fields using JavaScript for React-controlled inputs
        print("Filling form...")
        
        fill_script = """
        (() => {
            document.getElementById('sid-first-name').value = 'Mohamed';
            document.getElementById('sid-first-name').dispatchEvent(new Event('input', {bubbles: true}));
            document.getElementById('sid-first-name').dispatchEvent(new Event('change', {bubbles: true}));
            
            document.getElementById('sid-last-name').value = 'Benali';
            document.getElementById('sid-last-name').dispatchEvent(new Event('input', {bubbles: true}));
            document.getElementById('sid-last-name').dispatchEvent(new Event('change', {bubbles: true}));
            
            document.getElementById('sid-email').value = 'mohamed.benali@example.com';
            document.getElementById('sid-email').dispatchEvent(new Event('input', {bubbles: true}));
            document.getElementById('sid-email').dispatchEvent(new Event('change', {bubbles: true}));
            return 'filled basic fields';
        })()
        """
        await cdp.evaluate(session_id, fill_script)
        print("Basic fields filled")
        
        # Country - use JavaScript
        country_script = """
        (() => {
            const btn = document.querySelector('.sid-field.sid-country .sid-input-select-button');
            if (btn) btn.click();
            return 'clicked country btn';
        })()
        """
        await cdp.evaluate(session_id, country_script)
        await asyncio.sleep(1)
        
        # Select Algeria (index 1)
        select_country = """
        (() => {
            const options = document.querySelectorAll('[role="option"]');
            for (let i = 0; i < options.length; i++) {
                if (options[i].textContent.includes('Algeria')) {
                    options[i].click();
                    return options[i].textContent;
                }
            }
            return 'Algeria not found';
        })()
        """
        result = await cdp.evaluate(session_id, select_country)
        print(f"Country selected: {result}")
        
        # Birth month - use JavaScript to find and click the correct option
        month_script = f"""
        (() => {{
            const btn = document.querySelector('.sid-birthdate__month .sid-input-select-button');
            if (btn) btn.click();
            return 'clicked month btn';
        }})()
        """
        await cdp.evaluate(session_id, month_script)
        await asyncio.sleep(1)
        
        # Find June option (index 5 = June, 0-indexed)
        june_script = """
        (() => {
            const options = document.querySelectorAll('[role="option"]');
            if (options.length > 5) {
                options[5].click();
                return options[5].textContent;
            }
            return 'not found';
        })()
        """
        result = await cdp.evaluate(session_id, june_script)
        print(f"Month selected: {result}")
        
        # Birth day/year
        await cdp.type_text(session_id, "#sid-birthdate-day", "15")
        await cdp.type_text(session_id, "#sid-birthdate-year", "2000")
        print("Birth date filled")
        
        # Get organization ID via API and set it directly
        print("Searching organization via API...")
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://orgsearch.sheerid.net/rest/organization/search",
                params={
                    "tags": "HEI,qualifying_ps,TITLE4",
                    "country": "DZ",
                    "type": "UNIVERSITY,POST_SECONDARY",
                    "accountId": "67c88d2e0536336550bc3bd6",
                    "name": "University of Science and Technology Houari Boumediene"
                }
            )
            orgs = resp.json()
            if orgs:
                org_id = orgs[0]["idExtended"]
                print(f"Found org: {orgs[0]['name']} (ID: {org_id})")
                
                # Set the organization directly in the form
                set_org_script = f"""
                (() => {{
                    // Set the hidden org field
                    const input = document.getElementById('sid-college-name');
                    input.value = '{orgs[0]["name"]}';
                    input.dispatchEvent(new Event('input', {{bubbles: true}}));
                    
                    // The form likely stores org ID in a hidden field or React state
                    // Try to find and set it
                    const hiddenFields = document.querySelectorAll('input[type="hidden"]');
                    hiddenFields.forEach(f => {{
                        if (f.name && (f.name.includes('org') || f.name.includes('college') || f.name.includes('school'))) {{
                            f.value = '{org_id}';
                        }}
                    }});
                    
                    return 'org set to {org_id}';
                }})()
                """
                result = await cdp.evaluate(session_id, set_org_script)
                print(f"Org set: {result}")
            else:
                print("No org found via API")
        
        # Check for CAPTCHA and wait for it to be solved
        print("Checking for CAPTCHA...")
        for attempt in range(30):
            await asyncio.sleep(2)
            captcha_check = """
            (() => {
                const errorEl = document.querySelector('.sid-captcha-notification');
                const retryBtn = document.querySelector('.sid-captcha-notification__retry-button');
                const hiddenInput = document.querySelector('input[name="cf-turnstile-response"]');
                const turnstileWidget = document.querySelector('[data-sitekey]');
                
                if (errorEl && errorEl.style.display !== 'none') {
                    return {captchaError: true, hiddenValue: hiddenInput ? hiddenInput.value : '', hasWidget: !!turnstileWidget};
                }
                if (hiddenInput && hiddenInput.value) {
                    return {captchaSolved: true, tokenLength: hiddenInput.value.length};
                }
                return {captchaPending: true, hiddenValue: hiddenInput ? hiddenInput.value : ''};
            })()
            """
            captcha_status = await cdp.evaluate(session_id, captcha_check)
            print(f"CAPTCHA status: {captcha_status}")
            
            if captcha_status.get('captchaSolved'):
                print("CAPTCHA solved!")
                break
            elif captcha_status.get('captchaError'):
                print("CAPTCHA error detected, clicking retry...")
                retry_script = """
                (() => {
                    const btn = document.querySelector('.sid-captcha-notification__retry-button');
                    if (btn) { btn.click(); return 'retry clicked'; }
                    return 'no retry button';
                })()
                """
                await cdp.evaluate(session_id, retry_script)
        else:
            print("CAPTCHA timeout - proceeding anyway")
        
        # Submit - use JavaScript
        submit_script = """
        (() => {
            const btn = document.getElementById('sid-submit-wrapper__collect-info');
            if (btn) {
                btn.click();
                return 'clicked';
            }
            return 'not found';
        })()
        """
        await cdp.evaluate(session_id, submit_script)
        print("Form submitted")
        
        # Wait for submission and check result
        await asyncio.sleep(8)
        
        # Check for validation errors or next step
        check_result = """
        (() => {
            const stepEl = document.querySelector('[data-testid^="sid-step-"]');
            const errors = document.querySelectorAll('.sid-field-error, [class*="error"]');
            const errorTexts = Array.from(errors).map(e => e.textContent).filter(t => t);
            
            return {
                step: stepEl ? stepEl.getAttribute('data-testid') : 'unknown',
                url: window.location.href,
                errors: errorTexts,
                pageTitle: document.title
            };
        })()
        """
        result = await cdp.evaluate(session_id, check_result)
        print(f"\n=== SUBMISSION RESULT ===")
        print(json.dumps(result, indent=2))
        
        # If still on same step with errors, report them
        if result.get('errors'):
            print(f"Validation errors: {result['errors']}")
        
        # Wait for result
        await asyncio.sleep(10)
        
        # Get result
        content = await cdp.get_page_content(session_id)
        print("\n=== FINAL PAGE ===")
        print(content[:5000])
        
        # Check URL
        final_url = await cdp.evaluate(session_id, "window.location.href")
        print(f"\nFinal URL: {final_url}")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await cdp.close()
        listener_task.cancel()
        print("Stopping cloud browser...")
        await stop_cloud_browser(API_KEY, browser_id)


if __name__ == "__main__":
    asyncio.run(main())