#!/usr/bin/env python3
"""
SheerID Automation via browser-use cloud browser CDP
Uses proper CDP Input events for React-controlled forms
"""

import asyncio
import json
import os
import websockets
import httpx
import uuid
from typing import Dict, Any, Optional


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
    
    async def get_element_position(self, session_id: str, selector: str) -> Dict:
        """Get element center position"""
        js = f"""
        (() => {{
            const el = document.querySelector('{selector}');
            if (!el) return {{error: 'not found'}};
            const rect = el.getBoundingClientRect();
            return {{x: rect.left + rect.width/2, y: rect.top + rect.height/2}};
        }})()
        """
        return await self.evaluate(session_id, js)
    
    async def click(self, session_id: str, selector: str):
        pos = await self.get_element_position(session_id, selector)
        if "error" in pos:
            raise Exception(f"Element not found: {selector}")
        
        # Mouse move
        await self.send_session(session_id, "Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": pos["x"], "y": pos["y"]
        })
        await asyncio.sleep(0.1)
        # Mouse down
        await self.send_session(session_id, "Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1
        })
        await asyncio.sleep(0.1)
        # Mouse up
        await self.send_session(session_id, "Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1
        })
        await asyncio.sleep(0.3)
    
    async def fill_field(self, session_id: str, selector: str, value: str):
        """Fill field using JavaScript with proper React events"""
        # Use a simpler approach that works with React controlled components
        script = f"""
        (() => {{
            const el = document.querySelector('{selector}');
            if (!el) return {{error: 'not found', selector: '{selector}'}};
            
            // Focus first
            el.focus();
            
            // For React controlled components, we need to simulate user input
            // Set value directly
            el.value = '{value}';
            
            // Create and dispatch events that React listens to
            const inputEvent = new Event('input', {{bubbles: true}});
            const changeEvent = new Event('change', {{bubbles: true}});
            const blurEvent = new Event('blur', {{bubbles: true}});
            
            // React 17+ uses native event system
            el.dispatchEvent(inputEvent);
            el.dispatchEvent(changeEvent);
            
            // Also try the native setter
            try {{
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(el, '{value}');
                el.dispatchEvent(changeEvent);
            }} catch(e) {{}}
            
            // Blur to trigger validation
            el.dispatchEvent(blurEvent);
            
            return {{value: el.value, success: true}};
        }})()
        """
        return await self.evaluate(session_id, script)
    
    async def select_dropdown_option(self, session_id: str, dropdown_selector: str, option_index: int):
        """Click dropdown and select option by index"""
        await self.click(session_id, dropdown_selector)
        await asyncio.sleep(0.5)
        
        # Click option
        option_selector = f'[role="option"]:nth-child({option_index + 1})'
        await self.click(session_id, option_selector)
        await asyncio.sleep(0.3)
    
    async def search_and_select_org(self, session_id: str, search_input_selector: str, query: str):
        """Type in search and select first result"""
        await self.click(session_id, search_input_selector)
        await asyncio.sleep(0.3)
        
        # Type query
        for char in query:
            await self.send_session(session_id, "Input.insertText", {"text": char})
            await asyncio.sleep(0.05)
        
        # Wait for results
        await asyncio.sleep(3)
        
        # Try to click first result
        try:
            await self.click(session_id, '#sid-college-name-menu [role="option"]:first-child')
            await asyncio.sleep(0.5)
            return True
        except:
            return False
    
    async def get_page_info(self, session_id: str):
        return await self.evaluate(session_id, """
            (() => {
                const stepEl = document.querySelector('[data-testid^="sid-step-"]');
                const errors = document.querySelectorAll('.sid-field-error, [class*="error"]');
                return {
                    step: stepEl ? stepEl.getAttribute('data-testid') : 'unknown',
                    url: window.location.href,
                    errors: Array.from(errors).map(e => e.textContent).filter(t => t),
                    title: document.title
                };
            })()
        """)
    
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


async def main():
    API_KEY = os.environ.get('BROWSER_USE_API_KEY')
    if not API_KEY:
        print("BROWSER_USE_API_KEY not set")
        return
    
    # Student data
    student_data = {
        "first_name": "Mohamed",
        "last_name": "Benali",
        "email": "mohamed.benali@example.com",
        "country_code": "DZ",
        "country_name": "Algeria",
        "organization_name": "University of Science and Technology Houari Boumediene",
        "birth_month": 6,  # June (1-indexed)
        "birth_day": 15,
        "birth_year": 2000
    }
    
    verification_id = "<TEST_VID_REDACTED>"
    
    print("Creating cloud browser...")
    browser = await create_cloud_browser(API_KEY)
    browser_id = browser["id"]
    cdp_url = browser["cdpUrl"]
    live_url = browser["liveUrl"]
    
    print(f"Browser ID: {browser_id}")
    print(f"CDP URL: {cdp_url}")
    print(f"Live URL: {live_url}")
    print(f"Watch live: {live_url}")
    
    ws_url = cdp_url.replace("https://", "wss://")
    
    cdp = CDPClient(ws_url)
    
    try:
        await cdp.connect()
        
        targets = await cdp.get_targets()
        print(f"Found {len(targets)} targets")
        
        page_target = next((t for t in targets if t["type"] == "page" and not t["url"].startswith("chrome://")), None)
        if not page_target:
            result = await cdp.send("Target.createTarget", {"url": "about:blank"})
            page_target_id = result.get("targetId")
        else:
            page_target_id = page_target["targetId"]
        
        session_id = await cdp.attach_to_target(page_target_id)
        print(f"Attached to target: {page_target_id}, session: {session_id}")
        
        # Navigate to SheerID
        url = f"https://services.sheerid.com/verify/67c8c14f5f17a83b745e3f82/?verificationId={verification_id}&redirectUrl=https%3A%2F%2Fone.google.com%2Fai-student&authuser=2"
        print(f"Navigating to {url}")
        await cdp.navigate(session_id, url)
        
        # Wait for page to fully load
        await asyncio.sleep(5)
        
        # Check for CAPTCHA and wait for it
        print("Waiting for CAPTCHA...")
        for attempt in range(30):
            await asyncio.sleep(2)
            captcha_status = await cdp.evaluate(session_id, """
                (() => {
                    const hiddenInput = document.querySelector('input[name="cf-turnstile-response"]');
                    const errorEl = document.querySelector('.sid-captcha-notification');
                    if (hiddenInput && hiddenInput.value) {
                        return {solved: true, length: hiddenInput.value.length};
                    }
                    if (errorEl && errorEl.offsetParent !== null) {
                        const retryBtn = document.querySelector('.sid-captcha-notification__retry-button');
                        if (retryBtn) retryBtn.click();
                        return {error: true};
                    }
                    return {pending: true, hiddenValue: hiddenInput ? hiddenInput.value : ''};
                })()
            """)
            print(f"  CAPTCHA: {captcha_status}")
            if captcha_status.get('solved'):
                print("CAPTCHA solved!")
                break
        
        print("Filling form with JavaScript for React...")
        
        # Fill text fields using JavaScript with proper React events
        await cdp.fill_field(session_id, "#sid-first-name", student_data["first_name"])
        print("First name filled")
        
        await cdp.fill_field(session_id, "#sid-last-name", student_data["last_name"])
        print("Last name filled")
        
        await cdp.fill_field(session_id, "#sid-email", student_data["email"])
        print("Email filled")
        
        # Debug: check actual email value
        email_check = await cdp.evaluate(session_id, """
            (() => {
                const el = document.getElementById('sid-email');
                return {
                    value: el.value,
                    ariaInvalid: el.getAttribute('aria-invalid'),
                    className: el.className
                };
            })()
        """)
        print(f"Email field state: {email_check}")
        
        # Select country - use JS
        country_script = """
        (() => {
            const btn = document.querySelector('.sid-field.sid-country .sid-input-select-button');
            if (btn) btn.click();
            return 'clicked country btn';
        })()
        """
        await cdp.evaluate(session_id, country_script)
        await asyncio.sleep(0.5)
        
        # Find Algeria option
        country_result = await cdp.evaluate(session_id, """
            (() => {
                const options = document.querySelectorAll('[role="option"]');
                for (let i = 0; i < options.length; i++) {
                    if (options[i].textContent.includes('Algeria')) {
                        options[i].click();
                        return {index: i, text: options[i].textContent};
                    }
                }
                options[1].click(); // fallback to index 1
                return {index: 1, text: options[1] ? options[1].textContent : 'Algeria (default)'};
            })()
        """)
        print(f"Country selected: {country_result['text']}")
        
        # Select birth month (June = index 5) - use JS since dropdown options are in overlay
        month_script = """
        (() => {
            const btn = document.querySelector('.sid-birthdate__month .sid-input-select-button');
            if (btn) btn.click();
            return 'clicked month btn';
        })()
        """
        await cdp.evaluate(session_id, month_script)
        await asyncio.sleep(0.5)
        
        # Select June via JS
        select_month = """
        (() => {
            const options = document.querySelectorAll('[role="option"]');
            if (options.length > 5) {
                options[5].click();
                return options[5].textContent;
            }
            return 'not found';
        })()
        """
        month_result = await cdp.evaluate(session_id, select_month)
        print(f"Month selected: {month_result}")
        
        # Fill day and year
        await cdp.fill_field(session_id, "#sid-birthdate-day", str(student_data["birth_day"]))
        await cdp.fill_field(session_id, "#sid-birthdate-year", str(student_data["birth_year"]))
        print("Birth date filled")
        
        # Search and select organization
        print("Searching organization...")
        org = await get_org_id(student_data["country_code"], student_data["organization_name"])
        if org:
            print(f"Found org via API: {org['name']} (ID: {org['idExtended']})")
            
            # Type in search box
            await cdp.fill_field(session_id, "#sid-college-name", org["name"])
            await asyncio.sleep(3)
            
            # Try to select first result
            try:
                await cdp.click(session_id, '#sid-college-name-menu [role="option"]:first-child')
                print("Organization selected from autocomplete")
            except:
                print("Could not select from autocomplete, but search was typed")
        
        # Wait a bit for any async validation
        await asyncio.sleep(2)
        
        # Submit form
        print("Submitting form...")
        await cdp.click(session_id, "#sid-submit-wrapper__collect-info")
        
        # Wait for submission result
        await asyncio.sleep(10)
        
        # Check result
        result = await cdp.get_page_info(session_id)
        print(f"\n=== SUBMISSION RESULT ===")
        print(json.dumps(result, indent=2))
        
        # If still on personal info step, check for validation errors
        if result.get('step') == 'sid-step-student-personal-info':
            print("Still on personal info step - checking for errors...")
            # Check for field validation errors
            field_errors = await cdp.evaluate(session_id, """
                (() => {
                    const errors = {};
                    ['sid-first-name', 'sid-last-name', 'sid-email', 'sid-college-name', 'sid-country', 'sid-birthdate-day', 'sid-birthdate-year'].forEach(id => {
                        const el = document.getElementById(id);
                        if (el && el.getAttribute('aria-invalid') === 'true') {
                            const errorEl = document.getElementById(id + '-error');
                            errors[id] = errorEl ? errorEl.textContent : 'invalid';
                        }
                    });
                    return errors;
                })()
            """)
            print(f"Field errors: {json.dumps(field_errors, indent=2)}")
        
        # Wait a bit more to see if redirect happens
        await asyncio.sleep(5)
        final_result = await cdp.get_page_info(session_id)
        print(f"\n=== FINAL RESULT ===")
        print(json.dumps(final_result, indent=2))
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await cdp.close()
        print("Stopping cloud browser...")
        await stop_cloud_browser(API_KEY, browser_id)


if __name__ == "__main__":
    asyncio.run(main())