#!/usr/bin/env python3
"""
SheerID Verification Automation using browser-use cloud browser
Connects via CDP to a managed browser-use browser
"""

import asyncio
import json
import os
import sys
from typing import Optional, Dict, Any

from browser_use_sdk.v4 import BrowserUse
from playwright.async_api import async_playwright


class SheerIDBrowserUseAutomation:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = BrowserUse(api_key=api_key)
        self.browser_session = None
        self.browser = None
        self.page = None
        
    async def start_browser(self, timeout: int = 10) -> Dict:
        """Create a cloud browser session"""
        print("Creating cloud browser...")
        session = self.client.browsers.create(timeout=timeout)
        self.browser_session = session
        print(f"Browser created: {session.id}")
        print(f"Live URL: {session.live_url}")
        print(f"CDP URL: {session.cdp_url}")
        return {
            "id": session.id,
            "cdp_url": session.cdp_url,
            "live_url": session.live_url
        }
    
    async def connect_playwright(self):
        """Connect Playwright to the cloud browser via CDP"""
        if not self.browser_session or not self.browser_session.cdp_url:
            raise RuntimeError("No browser session or CDP URL")
        
        playwright = await async_playwright().start()
        self.browser = await playwright.chromium.connect_over_cdp(self.browser_session.cdp_url)
        
        # Get existing context or create new
        if self.browser.contexts:
            context = self.browser.contexts[0]
        else:
            context = await self.browser.new_context(
                viewport={"width": 390, "height": 844},
                user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
                locale="en-US",
                timezone_id="Africa/Algiers",
            )
        
        # Get existing page or create new
        if context.pages:
            self.page = context.pages[0]
        else:
            self.page = await context.new_page()
        
        # Anti-detection
        await self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
        """)
        
        print("Playwright connected to cloud browser")
    
    async def navigate_to_verification(self, verification_id: str):
        """Navigate to SheerID verification page"""
        url = f"https://services.sheerid.com/verify/67c8c14f5f17a83b745e3f82/?verificationId={verification_id}&redirectUrl=https%3A%2F%2Fone.google.com%2Fai-student&authuser=2"
        print(f"Navigating to: {url}")
        await self.page.goto(url, wait_until="networkidle", timeout=60000)
        await self.page.wait_for_timeout(3000)
    
    async def extract_turnstile_sitekey(self) -> Optional[str]:
        """Extract Turnstile sitekey from page"""
        sitekey = await self.page.evaluate("""
            () => {
                // Check turnstile global
                if (window.turnstile && window.turnstile._private) {
                    for (const [key, val] of Object.entries(window.turnstile._private)) {
                        if (val.params?.sitekey) return val.params.sitekey;
                    }
                }
                // Check data-sitekey attributes
                const widgets = document.querySelectorAll('[data-sitekey]');
                for (const w of widgets) {
                    if (w.dataset.sitekey) return w.dataset.sitekey;
                }
                return null;
            }
        """)
        print(f"Turnstile sitekey: {sitekey}")
        return sitekey
    
    async def solve_turnstile_2captcha(self, sitekey: str, page_url: str, api_key: str) -> str:
        """Solve Turnstile using 2Captcha"""
        print("Solving Turnstile via 2Captcha...")
        
        import httpx
        async with httpx.AsyncClient() as client:
            # Create task
            resp = await client.post("http://2captcha.com/in.php", data={
                "key": api_key,
                "method": "turnstile",
                "sitekey": sitekey,
                "pageurl": page_url,
                "json": 1
            })
            result = resp.json()
            if result.get("status") != 1:
                raise Exception(f"2Captcha error: {result}")
            
            task_id = result["request"]
            print(f"2Captcha task: {task_id}")
            
            # Poll for result
            for _ in range(60):
                await asyncio.sleep(5)
                resp = await client.get("http://2captcha.com/res.php", params={
                    "key": api_key,
                    "action": "get",
                    "id": task_id,
                    "json": 1
                })
                result = resp.json()
                if result.get("status") == 1:
                    token = result["request"]
                    print(f"Turnstile solved: {token[:20]}...")
                    return token
                if result.get("request") == "CAPCHA_NOT_READY":
                    continue
                raise Exception(f"2Captcha error: {result}")
            
            raise TimeoutError("Turnstile solving timeout")
    
    async def inject_turnstile_token(self, token: str):
        """Inject Turnstile token"""
        await self.page.evaluate(f"""
            () => {{
                if (window.turnstile && window.turnstile._private) {{
                    for (const [key, val] of Object.entries(window.turnstile._private)) {{
                        if (val.widgetId) {{
                            // Token auto-picked by widget
                        }}
                    }}
                }}
                const hiddenInput = document.querySelector('input[name="cf-turnstile-response"]');
                if (hiddenInput) hiddenInput.value = "{token}";
            }}
        """)
    
    async def select_country(self, country_name: str):
        """Select country from dropdown"""
        print(f"Selecting country: {country_name}")
        
        # Click country dropdown
        await self.page.click('.sid-field.sid-country .sid-input-select-button')
        await self.page.wait_for_selector('[role="option"]', timeout=5000)
        
        # Find and click country
        options = self.page.locator('[role="option"]')
        count = await options.count()
        for i in range(count):
            text = await options.nth(i).text_content()
            if text and country_name.lower() in text.lower():
                await options.nth(i).click()
                print(f"Selected: {text}")
                return True
        await options.first.click()
        return True
    
    async def select_birthdate(self, month: int, day: int, year: int):
        """Select birthdate"""
        print(f"Setting birthdate: {month}/{day}/{year}")
        
        # Month
        await self.page.click('.sid-birthdate__month .sid-input-select-button')
        await self.page.wait_for_selector('[role="option"]', timeout=5000)
        await self.page.locator('[role="option"]').nth(month - 1).click()
        
        # Day and year
        await self.page.fill('#sid-birthdate-day', str(day))
        await self.page.fill('#sid-birthdate-year', str(year))
    
    async def search_organization(self, org_name: str) -> bool:
        """Search and select organization"""
        print(f"Searching organization: {org_name}")
        
        await self.page.click('#sid-college-name')
        await self.page.wait_for_timeout(500)
        await self.page.fill('#sid-college-name', org_name)
        await self.page.wait_for_timeout(3000)
        
        # Check for results
        menu = self.page.locator('#sid-college-name-menu')
        if await menu.count() > 0:
            for _ in range(10):
                options = menu.locator('[role="option"]')
                if await options.count() > 0:
                    break
                await self.page.wait_for_timeout(500)
            
            if await options.count() > 0:
                first_text = await options.first.text_content()
                print(f"Selecting: {first_text}")
                await options.first.click()
                return True
        
        print("No organization results")
        return False
    
    async def fill_form(self, data: Dict):
        """Fill all form fields"""
        print("Filling form...")
        
        await self.page.fill('#sid-first-name', data['first_name'])
        await self.page.fill('#sid-last-name', data['last_name'])
        await self.page.fill('#sid-email', data['email'])
        await self.select_country(data['country_name'])
        await self.select_birthdate(data['birth_month'], data['birth_day'], data['birth_year'])
        await self.search_organization(data['organization_name'])
        
        print("Form filled")
    
    async def submit_form(self) -> Dict:
        """Submit form and check result"""
        print("Submitting form...")
        await self.page.click('#sid-submit-wrapper__collect-info')
        await self.page.wait_for_load_state("networkidle", timeout=30000)
        await self.page.wait_for_timeout(3000)
        
        result = await self.page.evaluate("""
            () => {
                const stepEl = document.querySelector('[data-testid^="sid-step-"]');
                return {
                    step: stepEl ? stepEl.getAttribute('data-testid') : 'unknown',
                    url: window.location.href
                };
            }
        """)
        print(f"Result: {result}")
        return result
    
    async def run_verification(self, verification_id: str, student_data: Dict, captcha_key: str) -> Dict:
        """Run complete verification flow"""
        await self.navigate_to_verification(verification_id)
        
        # Extract and solve CAPTCHA
        sitekey = await self.extract_turnstile_sitekey()
        if not sitekey:
            # Wait for it to appear
            for _ in range(10):
                await self.page.wait_for_timeout(2000)
                sitekey = await self.extract_turnstile_sitekey()
                if sitekey:
                    break
        
        if not sitekey:
            return {"success": False, "error": "Turnstile sitekey not found"}
        
        if captcha_key:
            token = await self.solve_turnstile_2captcha(sitekey, self.page.url, captcha_key)
            await self.inject_turnstile_token(token)
        else:
            print("No CAPTCHA key - waiting 60s for manual solve")
            await self.page.wait_for_timeout(60000)
        
        # Fill and submit
        await self.fill_form(student_data)
        result = await self.submit_form()
        
        return {"success": True, **result}
    
    async def close(self):
        """Clean up"""
        if self.browser:
            await self.browser.close()
        if self.browser_session:
            print("Stopping cloud browser...")
            self.client.browsers.stop(self.browser_session.id)


async def main():
    # Configuration
    API_KEY = os.environ.get('BROWSER_USE_API_KEY')
    CAPTCHA_KEY = os.environ.get('CAPTCHA_API_KEY')  # 2Captcha key
    
    if not API_KEY:
        print("BROWSER_USE_API_KEY not set")
        sys.exit(1)
    
    # Student data (replace with real data)
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
    
    verification_id = "<TEST_VID_REDACTED>"
    
    automation = SheerIDBrowserUseAutomation(API_KEY)
    
    try:
        # Start cloud browser
        await automation.start_browser(timeout=10)
        
        # Connect Playwright
        await automation.connect_playwright()
        
        # Run verification
        result = await automation.run_verification(
            verification_id, student_data, CAPTCHA_KEY
        )
        
        print("\n=== RESULT ===")
        print(json.dumps(result, indent=2))
        
        # Keep browser alive for inspection
        print("\nBrowser live URL:", automation.browser_session.live_url)
        print("Press Ctrl+C to stop...")
        await asyncio.sleep(300)
        
    finally:
        await automation.close()


if __name__ == "__main__":
    asyncio.run(main())