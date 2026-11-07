#!/usr/bin/env python3
"""
SheerID Verification Automation for Google One AI Student Offer
Full browser automation with Playwright - handles CAPTCHA, form submission, checkout
"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from urllib.parse import urlencode

from playwright.async_api import async_playwright, Browser, Page, BrowserContext


@dataclass
class SheerIDConfig:
    program_id: str = "67c8c14f5f17a83b745e3f82"
    verification_id: str = ""
    services_url: str = "https://services.sheerid.com"
    redirect_url: str = "https://one.google.com/ai-student"
    locale: str = "en-US"


@dataclass
class StudentData:
    first_name: str
    last_name: str
    email: str
    country_code: str  # ISO 2-letter (e.g., "DZ")
    country_name: str  # Full name (e.g., "Algeria")
    organization_name: str  # University name to search
    organization_id: int = 0  # Will be auto-selected from search
    birth_month: int = 1  # 1-12
    birth_day: int = 1
    birth_year: int = 2000
    offer_duration: str = "12m"  # 12m, 6m, 6m_ss, 6m_2


class SheerIDAutomation:
    """Complete browser automation for SheerID verification + Google One checkout"""
    
    OFFER_TOKENS = {
        "12m": "REDACTED_OFFER_TOKEN=",
        "6m_ss": "REDACTED_OFFER_TOKEN=",
        "6m": "REDACTED_OFFER_TOKEN=",
        "6m_2": "REDACTED_OFFER_TOKEN"
    }
    YT_FREE_TOK = "REDACTED_OFFER_TOKEN"
    PREFIX = "subs:com.google.android.apps.subscriptions.red:"
    
    def __init__(self, config: SheerIDConfig, captcha_api_key: str = None, headless: bool = True):
        self.config = config
        self.captcha_api_key = captcha_api_key
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.turnstile_sitekey: Optional[str] = None
        
    async def __aenter__(self):
        await self.start()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
        
    async def start(self):
        playwright = await async_playwright().start()
        self.browser = await playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ]
        )
        self.context = await self.browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            locale="en-US",
            timezone_id="Africa/Algiers",
        )
        self.page = await self.context.new_page()
        
        # Anti-detection
        await self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        """)
        
    async def stop(self):
        if self.browser:
            await self.browser.close()
            
    async def load_verification_page(self) -> bool:
        """Load the SheerID verification page"""
        url = f"{self.config.services_url}/verify/{self.config.program_id}/?verificationId={self.config.verification_id}&redirectUrl={self.config.redirect_url}&authuser=2"
        
        print(f"Loading verification page: {url}")
        await self.page.goto(url, wait_until="networkidle", timeout=60000)
        
        # Wait for Turnstile to be ready
        await self.page.wait_for_selector('iframe[src*="challenges.cloudflare.com"]', timeout=30000)
        await self.page.wait_for_timeout(2000)
        
        # Extract Turnstile sitekey
        self.turnstile_sitekey = await self.page.evaluate("""
            () => {
                // Check turnstile widgets
                if (window.turnstile && window.turnstile._private) {
                    for (const [key, val] of Object.entries(window.turnstile._private)) {
                        if (val.params?.sitekey) return val.params.sitekey;
                    }
                }
                // Check for data-sitekey attribute
                const widgets = document.querySelectorAll('[data-sitekey]');
                for (const w of widgets) {
                    if (w.dataset.sitekey) return w.dataset.sitekey;
                }
                return null;
            }
        """)
        
        print(f"Turnstile sitekey: {self.turnstile_sitekey}")
        return self.turnstile_sitekey is not None
    
    async def solve_turnstile(self) -> str:
        """Solve Turnstile CAPTCHA using 2Captcha"""
        if not self.captcha_api_key:
            print("No 2Captcha API key - waiting for manual solve (60s)...")
            await self.page.wait_for_timeout(60000)
            # Get token from widget
            token = await self.page.evaluate("""
                () => {
                    if (window.turnstile && window.turnstile._private) {
                        for (const [key, val] of Object.entries(window.turnstile._private)) {
                            if (val.widgetId) {
                                return window.turnstile.getResponse(val.widgetId);
                            }
                        }
                    }
                    return null;
                }
            """)
            return token or ""
        
        print("Solving Turnstile via 2Captcha...")
        url = f"{self.config.services_url}/verify/{self.config.program_id}/"
        
        import httpx
        async with httpx.AsyncClient() as client:
            # Create task
            resp = await client.post("http://2captcha.com/in.php", data={
                "key": self.captcha_api_key,
                "method": "turnstile",
                "sitekey": self.turnstile_sitekey,
                "pageurl": url,
                "json": 1
            })
            result = resp.json()
            if result.get("status") != 1:
                raise Exception(f"2Captcha error: {result}")
            
            task_id = result["request"]
            print(f"Task created: {task_id}")
            
            # Poll for result
            for attempt in range(60):
                await asyncio.sleep(5)
                resp = await client.get("http://2captcha.com/res.php", params={
                    "key": self.captcha_api_key,
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
        """Inject Turnstile token into the page"""
        await self.page.evaluate(f"""
            () => {{
                // Inject into turnstile widget
                if (window.turnstile && window.turnstile._private) {{
                    for (const [key, val] of Object.entries(window.turnstile._private)) {{
                        if (val.widgetId) {{
                            // The token is automatically picked up by turnstile
                        }}
                    }}
                }}
                // Also set hidden input if exists
                const hiddenInput = document.querySelector('input[name="cf-turnstile-response"]');
                if (hiddenInput) {{
                    hiddenInput.value = "{token}";
                }}
            }}
        """)
    
    async def select_country(self, country_name: str) -> bool:
        """Select country from dropdown"""
        print(f"Selecting country: {country_name}")
        
        # Click country dropdown
        country_btn = self.page.locator('.sid-field.sid-country .sid-input-select-button')
        await country_btn.click()
        await self.page.wait_for_selector('[role="option"]', timeout=5000)
        
        # Find and click the country
        options = self.page.locator('[role="option"]')
        count = await options.count()
        
        for i in range(count):
            text = await options.nth(i).text_content()
            if text and country_name.lower() in text.lower():
                await options.nth(i).click()
                print(f"Selected: {text}")
                return True
        
        # Fallback: click first
        await options.first.click()
        text = await options.first.text_content()
        print(f"Selected first: {text}")
        return True
    
    async def select_birthdate(self, month: int, day: int, year: int):
        """Select birthdate"""
        print(f"Setting birthdate: {month}/{day}/{year}")
        
        # Month dropdown
        month_btn = self.page.locator('.sid-birthdate__month .sid-input-select-button')
        await month_btn.click()
        await self.page.wait_for_selector('[role="option"]', timeout=5000)
        
        months = self.page.locator('[role="option"]')
        await months.nth(month - 1).click()
        
        # Day and year inputs
        await self.page.fill('#sid-birthdate-day', str(day))
        await self.page.fill('#sid-birthdate-year', str(year))
    
    async def search_and_select_organization(self, org_name: str) -> int:
        """Search for and select an organization"""
        print(f"Searching for organization: {org_name}")
        
        # Click the school input to open search overlay
        school_input = self.page.locator('#sid-college-name')
        await school_input.click()
        await self.page.wait_for_timeout(500)
        
        # Type the organization name
        await school_input.fill(org_name)
        await self.page.wait_for_timeout(2000)  # Wait for search results
        
        # Check for results in the search overlay menu
        menu = self.page.locator('#sid-college-name-menu')
        if await menu.count() > 0:
            # Wait for options to appear
            for _ in range(10):
                options = menu.locator('[role="option"]')
                count = await options.count()
                if count > 0:
                    break
                await self.page.wait_for_timeout(500)
            
            count = await options.count()
            print(f"Found {count} organization options")
            
            if count > 0:
                # Get first option text and click
                first_text = await options.first.text_content()
                print(f"Selecting: {first_text}")
                await options.first.click()
                
                # Try to extract org ID from the selected option or page state
                # The ID might be in a data attribute or we need to track it
                return 0  # Will be handled by form submission
        
        print("No organization options found")
        return 0
    
    async def fill_personal_info(self, data: StudentData):
        """Fill all personal info fields"""
        print("Filling personal info...")
        
        # First name
        await self.page.fill('#sid-first-name', data.first_name)
        
        # Last name
        await self.page.fill('#sid-last-name', data.last_name)
        
        # Email
        await self.page.fill('#sid-email', data.email)
        
        # Country
        await self.select_country(data.country_name)
        
        # Birthdate
        await self.select_birthdate(data.birth_month, data.birth_day, data.birth_year)
        
        # Organization
        await self.search_and_select_organization(data.organization_name)
        
        print("Personal info filled")
    
    async def submit_form(self) -> Dict:
        """Submit the form and wait for next step"""
        print("Submitting form...")
        
        # Click submit
        submit_btn = self.page.locator('#sid-submit-wrapper__collect-info')
        await submit_btn.click()
        
        # Wait for navigation or next step
        await self.page.wait_for_load_state("networkidle", timeout=30000)
        await self.page.wait_for_timeout(3000)
        
        # Check current step
        current_step = await self.page.evaluate("""
            () => {
                const stepEl = document.querySelector('[data-testid^="sid-step-"]');
                return stepEl ? stepEl.getAttribute('data-testid') : 'unknown';
            }
        """)
        
        current_url = self.page.url
        print(f"Current step: {current_step}")
        print(f"Current URL: {current_url}")
        
        return {"step": current_step, "url": current_url}
    
    async def handle_sso_step(self) -> Dict:
        """Handle SSO step if present"""
        print("Checking for SSO step...")
        
        # Check if we're on SSO step
        sso_present = await self.page.locator('[data-testid="sid-step-sso"]').count() > 0
        
        if sso_present:
            print("SSO step detected - would need school credentials")
            # In automation, we'd need to handle SSO login
            # For now, return the SSO URL
            sso_url = await self.page.evaluate("""
                () => {
                    const loginBtn = document.querySelector('#sid-submit-btn-sso');
                    return loginBtn ? loginBtn.href : null;
                }
            """)
            return {"step": "sso", "sso_url": sso_url, "requires_manual": True}
        
        return {"step": "none"}
    
    async def wait_for_verification_complete(self, max_wait: int = 300) -> Dict:
        """Wait for verification to complete (success redirect)"""
        print("Waiting for verification result...")
        
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < max_wait:
            await self.page.wait_for_timeout(5000)
            
            current_url = self.page.url
            print(f"Current URL: {current_url}")
            
            # Check for success redirect
            if "one.google.com" in current_url and "ai-student" in current_url:
                print("Success redirect detected!")
                return {"success": True, "url": current_url, "step": "success"}
            
            # Check for pending/review step
            pending = await self.page.locator('[data-testid="sid-step-pending"]').count() > 0
            if pending:
                print("Verification pending review")
                return {"success": False, "step": "pending", "url": current_url, "message": "Under review"}
            
            # Check for document upload step
            doc_upload = await self.page.locator('[data-testid="sid-step-docUpload"]').count() > 0
            if doc_upload:
                print("Document upload required")
                return {"success": False, "step": "doc_upload", "url": current_url, "message": "Upload documents"}
        
        return {"success": False, "step": "timeout", "message": "Verification timed out"}
    
    async def run_checkout_with_offer(self, redirect_url: str) -> Dict:
        """Run Google One checkout with offer injection"""
        print(f"Starting checkout with offer: {self.config.redirect_url}")
        
        # Inject offer token script
        await self.inject_offer_script()
        
        # Navigate to checkout
        await self.page.goto(redirect_url, wait_until="networkidle", timeout=60000)
        await self.page.wait_for_timeout(3000)
        
        # Try to complete checkout
        try:
            # Wait for subscribe/continue button
            await self.page.wait_for_selector(
                'button:has-text("Subscribe"), button:has-text("Continue"), button:has-text("Get started"), button:has-text("Confirm")',
                timeout=15000
            )
            
            # Click through checkout flow
            for _ in range(5):
                buttons = await self.page.query_selector_all('button')
                clicked = False
                for btn in buttons:
                    text = await btn.text_content()
                    if text and any(kw in text.lower() for kw in ["subscribe", "continue", "get started", "confirm", "next", "agree"]):
                        if await btn.is_visible() and await btn.is_enabled():
                            await btn.click()
                            await self.page.wait_for_timeout(2000)
                            clicked = True
                            break
                if not clicked:
                    break
            
            final_url = self.page.url
            print(f"Checkout completed, final URL: {final_url}")
            return {"success": True, "url": final_url}
            
        except Exception as e:
            print(f"Checkout error: {e}")
            return {"success": False, "error": str(e)}
    
    async def inject_offer_script(self):
        """Inject the offer token injection script"""
        token = self.OFFER_TOKENS.get(self.config.redirect_url, self.OFFER_TOKENS["12m"])
        full_items = json.dumps([
            [[self.PREFIX + "g1.2tb.ai.subs_variant", 21], None, None, None, 1, [token]],
            [[self.PREFIX + "g1.youtube_premium_individual_add_on", 21], None, None, None, 1, [self.YT_FREE_TOK]]
        ])
        
        script = f"""
        (() => {{
            if (window.__GAPT_HOOKED) return;
            window.__GAPT_HOOKED = true;
            
            const currentTest = {{
                full_items: {full_items},
                layer: "{self.config.redirect_url}"
            }};
            
            function patchNode(node) {{
                if (!Array.isArray(node) || !Array.isArray(currentTest.full_items)) return false;
                let patched = false;
                if (node.length > 12 && Array.isArray(node[12])) {{
                    node[12] = currentTest.full_items;
                    patched = true;
                }}
                if (node.length > 10 && Array.isArray(node[10]) && Array.isArray(node[10][0])) {{
                    node[10] = [currentTest.full_items];
                    patched = true;
                }}
                return patched;
            }}
            
            function rewriteFreq(bodyStr) {{
                if (typeof bodyStr !== "string" || !bodyStr.includes("f.req=") || !currentTest) return bodyStr;
                try {{
                    const parts = bodyStr.split("&");
                    for (let i = 0; i < parts.length; i++) {{
                        if (parts[i].startsWith("f.req=")) {{
                            const raw = decodeURIComponent(parts[i].slice(6).replace(/\\+/g, " "));
                            const outer = JSON.parse(raw);
                            let changed = false;
                            if (Array.isArray(outer)) {{
                                for (const group of outer) {{
                                    if (!Array.isArray(group)) continue;
                                    for (const call of group) {{
                                        if (Array.isArray(call) && call[0] === "Ngbm" && typeof call[1] === "string") {{
                                            const payload = JSON.parse(call[1]);
                                            if (Array.isArray(payload)) {{
                                                if (patchNode(payload[0])) changed = true;
                                                if (patchNode(payload[1])) changed = true;
                                            }}
                                            if (changed) call[1] = JSON.stringify(payload);
                                        }}
                                    }}
                                }}
                            }}
                            if (changed) {{
                                parts[i] = "f.req=" + encodeURIComponent(JSON.stringify(outer));
                                return parts.join("&");
                            }}
                        }}
                    }}
                }} catch (e) {{}}
                return bodyStr;
            }}
            
            const origSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.send = function (body) {{
                try {{
                    if (typeof body === "string" && body.includes("Ngbm")) body = rewriteFreq(body);
                }} catch (e) {{}}
                return origSend.call(this, body);
            }};
            
            function rewriteUrl(val) {{
                const s = String(val || "");
                if (!s.includes("/eacquire/multiline") || !currentTest) return val;
                try {{
                    const u = new URL(s, location.origin);
                    const earStr = u.searchParams.get("ear");
                    if (!earStr) return val;
                    const ear = JSON.parse(earStr);
                    if (Array.isArray(ear[13]) && Array.isArray(currentTest.full_items)) ear[13][0] = currentTest.full_items;
                    if (Array.isArray(ear[4]) && typeof ear[4][0] === "string" && Array.isArray(currentTest.full_items)) {{
                        try {{
                            const inner4 = JSON.parse(atob(ear[4][0]));
                            if (Array.isArray(inner4) && inner4.length > 8) {{
                                inner4[8] = [currentTest.full_items];
                                ear[4][0] = btoa(JSON.stringify(inner4));
                            }}
                        }} catch (e4) {{}}
                    }}
                    u.searchParams.set("ear", JSON.stringify(ear));
                    return u.toString();
                }} catch (e) {{
                    return val;
                }}
            }}
            
            const srcDesc = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, "src");
            if (srcDesc && srcDesc.set) {{
                Object.defineProperty(HTMLIFrameElement.prototype, "src", {{
                    ...srcDesc,
                    set(v) {{
                        if (typeof v === "string") v = rewriteUrl(v);
                        return srcDesc.set.call(this, v);
                    }}
                }});
            }}
        }})();
        """
        
        await self.page.add_init_script(script)
        print("Offer injection script installed")


async def run_full_verification(
    student_data: StudentData,
    verification_id: str,
    captcha_api_key: str = None,
    headless: bool = True
) -> Dict:
    """Run the complete verification flow"""
    
    config = SheerIDConfig(
        verification_id=verification_id,
        locale="en-US"
    )
    
    async with SheerIDAutomation(config, captcha_api_key, headless) as bot:
        # Step 1: Load verification page
        await bot.load_verification_page()
        
        # Step 2: Solve CAPTCHA
        turnstile_token = await bot.solve_turnstile()
        await bot.inject_turnstile_token(turnstile_token)
        
        # Step 3: Fill form
        await bot.fill_personal_info(student_data)
        
        # Step 4: Submit
        result = await bot.submit_form()
        
        if not result.get("success", True):
            return result
        
        # Step 5: Handle next steps
        step = result.get("step", "")
        
        if "sso" in step:
            sso_result = await bot.handle_sso_step()
            return {**result, **sso_result}
        
        # Wait for completion
        final_result = await bot.wait_for_verification_complete()
        
        if final_result.get("success"):
            # Step 6: Checkout with offer
            checkout_result = await bot.run_checkout_with_offer(final_result["url"])
            return {"verification": final_result, "checkout": checkout_result}
        
        return final_result


# CLI
async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="SheerID Verification Automation")
    parser.add_argument("--verification-id", required=True)
    parser.add_argument("--first-name", required=True)
    parser.add_argument("--last-name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--country-code", required=True, help="ISO 2-letter code (DZ, US, etc.)")
    parser.add_argument("--country-name", required=True, help="Full country name (Algeria, United States)")
    parser.add_argument("--university", required=True, help="University name to search")
    parser.add_argument("--dob", required=True, help="Date of birth DD/MM/YYYY")
    parser.add_argument("--captcha-key", help="2Captcha API key")
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--offer", default="12m", choices=["12m", "6m", "6m_ss", "6m_2"])
    
    args = parser.parse_args()
    
    day, month, year = map(int, args.dob.split("/"))
    
    student = StudentData(
        first_name=args.first_name,
        last_name=args.last_name,
        email=args.email,
        country_code=args.country_code,
        country_name=args.country_name,
        organization_name=args.university,
        birth_month=month,
        birth_day=day,
        birth_year=year,
        offer_duration=args.offer
    )
    
    result = await run_full_verification(
        student,
        args.verification_id,
        captcha_api_key=args.captcha_key,
        headless=args.headless
    )
    
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())